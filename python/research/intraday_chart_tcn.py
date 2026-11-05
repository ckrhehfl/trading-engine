"""Fixed EF two-resolution causal learner over already packed CPU tensors.

The caller owns past-only splits, source/runtime authentication and budget
enforcement. Neither construction nor fit accepts evaluation labels or reads
files. Persist numeric weights and the split/runtime binding in the runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from torch import Tensor, nn


MINUTE_SHAPE = (60, 13)
COARSE_SHAPE = (24, 6)
STATIC_WIDTH = 6
HEADS = 6
EPOCHS = 6
BATCH_SIZE = 128
SEED = 20261103


@dataclass(frozen=True)
class SequenceBatch:
    minute: Tensor  # [rows, time, channels]
    coarse: Tensor
    static: Tensor


def build_tcn():
    import torch
    from torch import nn
    from torch.nn import functional as F

    class Tower(nn.Module):
        def __init__(self, channels: int, width: int, dilations: tuple[int, ...]) -> None:
            super().__init__()
            self.input_projection = nn.Conv1d(channels, width, 1)
            self.dilations = dilations
            self.convolutions = nn.ModuleList(nn.Conv1d(width, width, 3, dilation=d) for d in dilations)

        def forward(self, values):
            state = self.input_projection(values.transpose(1, 2)).relu()
            for convolution, dilation in zip(self.convolutions, self.dilations, strict=True):
                state = (state + convolution(F.pad(state, (2 * dilation, 0)))).relu()
            return state

    class ChartTCN(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.minute_tower = Tower(MINUTE_SHAPE[1], 16, (1, 2, 4, 8, 16))
            self.coarse_tower = Tower(COARSE_SHAPE[1], 8, (1, 2, 4, 8))
            self.head = nn.Linear(16 + 8 + STATIC_WIDTH, HEADS)

        def forward(self, minute, coarse, static):
            current_minute = self.minute_tower(minute)[:, :, -1]
            current_coarse = self.coarse_tower(coarse)[:, :, -1]
            return self.head(torch.cat((current_minute, current_coarse, static), dim=1))

    return ChartTCN().to(device="cpu", dtype=torch.float32)


def _check_cpu_runtime(torch) -> None:
    if (torch.__version__ != "2.7.0+cpu" or torch.version.cuda is not None
            or torch.get_num_threads() != 1 or torch.get_num_interop_threads() != 1
            or not torch.are_deterministic_algorithms_enabled()):
        raise ValueError("pinned deterministic single-thread CPU Torch required")


def _validate_batch(torch, inputs: SequenceBatch) -> int:
    size = len(inputs.minute)
    for tensor, trailing in ((inputs.minute, MINUTE_SHAPE), (inputs.coarse, COARSE_SHAPE),
                             (inputs.static, (STATIC_WIDTH,))):
        if (tensor.shape != (size, *trailing) or tensor.device.type != "cpu"
                or tensor.dtype != torch.float32 or tensor.requires_grad or not torch.isfinite(tensor).all().item()):
            raise ValueError("invalid finite EF sequence tensor")
    if size == 0:
        raise ValueError("empty EF sequence tensor")
    return size


def fit_tcn(inputs: SequenceBatch, targets: Tensor, known: Tensor, *, fold_index: int) -> nn.Module:
    import torch
    from torch.nn import functional as F

    _check_cpu_runtime(torch)
    size = _validate_batch(torch, inputs)
    if (targets.shape != (size, HEADS) or known.shape != targets.shape
            or targets.device.type != "cpu" or known.device.type != "cpu"
            or targets.dtype != torch.float32 or known.dtype != torch.bool or targets.requires_grad
            or not torch.isfinite(targets).all().item() or not known[:, 0].all().item()
            or (targets[~known] != 0).any().item()):
        raise ValueError("invalid mature primary target or auxiliary mask")
    if type(fold_index) is not int or not 1 <= fold_index <= 5:
        raise ValueError("fixed EF fold index required")

    torch.manual_seed(SEED + fold_index)
    row_order = torch.Generator(device="cpu").manual_seed(SEED + fold_index)
    model = build_tcn()
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.01,
                                  betas=(0.9, 0.999), eps=1e-8, foreach=False, fused=False)
    for _ in range(EPOCHS):
        for indices in torch.randperm(size, generator=row_order).split(BATCH_SIZE):
            batch_target = targets[indices]
            batch_known = known[indices]
            optimizer.zero_grad(set_to_none=True)
            output = model(inputs.minute[indices], inputs.coarse[indices], inputs.static[indices])
            loss = F.mse_loss(output[:, 0], batch_target[:, 0])
            for head in range(1, HEADS):
                available = batch_known[:, head]
                if available.any().item():
                    loss = loss + 0.2 * F.mse_loss(output[available, head], batch_target[available, head])
            if not torch.isfinite(loss).item():
                raise ValueError("nonfinite EF training loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
            optimizer.step()
    if any(not torch.isfinite(parameter).all().item() for parameter in model.parameters()):
        raise ValueError("nonfinite EF model parameters")
    return model.eval()


def predict_tcn(model: nn.Module, inputs: SequenceBatch) -> Tensor:
    import torch

    _check_cpu_runtime(torch)
    size = _validate_batch(torch, inputs)
    if model.training:
        raise ValueError("EF prediction requires a frozen evaluation model")
    with torch.inference_mode():
        parts = []
        for start in range(0, size, BATCH_SIZE):
            batch = slice(start, start + BATCH_SIZE)
            parts.append(model(inputs.minute[batch], inputs.coarse[batch], inputs.static[batch]))
        values = torch.cat(parts)
    if values.shape != (size, HEADS) or not torch.isfinite(values).all().item():
        raise ValueError("invalid EF prediction")
    return values
