"""Stdlib-only source tests; never import torch or start the backend worker."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "research/intraday_chart_backend_probe.py"
probe = types.ModuleType("ef_backend_probe_test_target")
probe.__file__ = str(SOURCE)
exec(compile(SOURCE.read_bytes(), str(SOURCE), "exec"), probe.__dict__)


class BackendProbeTests(unittest.TestCase):
    def test_import_is_lazy_and_resource_partition_is_bounded(self):
        self.assertNotIn("torch", probe.__dict__)
        self.assertEqual(probe.WORKER_CPU + probe.CONTROLLER_CPU, 60)
        self.assertEqual(probe.WORKER_AS, 3072 * 1024**2)
        self.assertLessEqual(probe.WORKER_AS + probe.CONTROLLER_AS, 4096 * 1024**2)
        self.assertEqual(probe.WALL_SECONDS, 120)

    def test_hard_worker_limits_and_parent_soft_then_hard_partition(self):
        with (patch.object(probe, "restrict_common"), patch.object(probe, "require_unprivileged_worker"),
              patch.object(probe.resource, "setrlimit") as limits):
            probe.prepare_controller()
            probe.prepare_worker()
            probe.tighten_controller()
        self.assertEqual(limits.call_args_list, [
            unittest.mock.call(probe.resource.RLIMIT_AS, (probe.CONTROLLER_AS, probe.WORKER_AS)),
            unittest.mock.call(probe.resource.RLIMIT_CPU, (10, 50)),
            unittest.mock.call(probe.resource.RLIMIT_AS, (probe.WORKER_AS, probe.WORKER_AS)),
            unittest.mock.call(probe.resource.RLIMIT_CPU, (50, 50)),
            unittest.mock.call(probe.resource.RLIMIT_NPROC, (0, 0)),
            unittest.mock.call(probe.resource.RLIMIT_AS, (probe.CONTROLLER_AS, probe.CONTROLLER_AS)),
            unittest.mock.call(probe.resource.RLIMIT_CPU, (10, 10)),
        ])

    def test_worker_argv_disables_site_and_authenticates_raw_source(self):
        args = argparse.Namespace(output="/tmp/ef-backend-unit", source_sha256="source",
                                  runtime_pin="/tmp/pin.json", runtime_sha256="runtime")
        command = probe.worker_command(args, "/retained/venv/bin/python", 12)
        self.assertEqual(command[:5], ["/retained/venv/bin/python", "-I", "-S", "-B", "-c"])
        self.assertIn("compile(b,p,'exec')", command[5])
        self.assertIn("--barrier-fd", command)
        self.assertIn("runtime", command)

    def test_environment_excludes_host_credentials_and_disables_gpu(self):
        env = probe.clean_environment(Path("/tmp/ef-backend-unit"))
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("KIS_APP_KEY", env)
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "")
        self.assertTrue(all(env[name] == "1" for name in probe.THREAD_ENV))
        self.assertEqual(env["HOME"], "/tmp/ef-backend-unit")

    def test_receipt_cannot_overwrite_existing_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "failure.json"
            probe.save(path, {"original": True})
            with self.assertRaises(FileExistsError):
                probe.save(path, {"original": False})
            self.assertEqual(json.loads(path.read_text()), {"original": True})

    def test_bad_runtime_hash_stops_before_payload_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pin.json"
            path.write_text("{}")
            with patch.object(probe, "digest") as digest:
                with self.assertRaisesRegex(ValueError, "hash/size"):
                    probe.read_pin(path, "wrong")
                digest.assert_not_called()

    def run_fake_controller(self, temp: str, *, timeout: bool = False,
                            barrier_failure: bool = False, interpreter_failure: bool = False):
        root = Path(temp)
        directory = root / "ef-backend-unit"
        pin_path = root / "pin.json"
        pin_path.write_text(json.dumps({"schema": "ef-retained-torch-runtime-v1", "torch": "2.7.0+cpu",
             "prefix": "/retained", "interpreter": "/retained/venv/bin/python", "interpreter_sha256": "interpreter"}))
        args = argparse.Namespace(output=str(directory), runtime_pin=str(pin_path),
                                  runtime_sha256=hashlib.sha256(pin_path.read_bytes()).hexdigest(),
                                  source_sha256="source")
        process = Mock(pid=1234, returncode=-9, args=["fake-worker"])
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired(process.args, 120), -9] if timeout else [1, 1]
        def fake_digest(path):
            return "source" if path == SOURCE else "wrong" if interpreter_failure else "interpreter"
        with (patch.object(probe, "prepare_controller"), patch.object(probe, "tighten_controller") as tighten,
              patch.object(probe, "OUTPUT_ROOT", root),
              patch.object(probe, "readback", return_value={}), patch.object(probe, "digest", side_effect=fake_digest),
              patch.object(probe.subprocess, "Popen", return_value=process) as popen,
              patch.object(probe.os, "killpg") as killpg, patch.object(probe.os, "write") as write):
            if barrier_failure:
                tighten.side_effect = RuntimeError("parent hard cap failed")
            code = probe.controller(args)
        return code, directory, process, popen, killpg, write

    def test_timeout_kills_group_preserves_receipt_and_never_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            code, directory, process, popen, killpg, write = self.run_fake_controller(temp, timeout=True)
            self.assertEqual(code, 1)
            popen.assert_called_once()
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertIs(popen.call_args.kwargs["preexec_fn"], probe.prepare_worker)
            self.assertEqual(len(popen.call_args.kwargs["pass_fds"]), 1)
            self.assertGreater(process.wait.call_args_list[0].kwargs["timeout"], 0)
            self.assertLessEqual(process.wait.call_args_list[0].kwargs["timeout"], 120)
            killpg.assert_called_once_with(1234, probe.signal.SIGKILL)
            write.assert_called_once()
            terminal = json.loads((directory / "controller-terminal.json").read_text())
            self.assertEqual(terminal["status"], "wall_timeout")
            self.assertFalse(terminal["automatic_retry"])
            self.assertEqual(terminal["market_fits"], 0)
            self.assertTrue((directory / "worker.stderr.txt").exists())

    def test_parent_hard_cap_failure_never_releases_import_barrier(self):
        with tempfile.TemporaryDirectory() as temp:
            code, directory, _, popen, killpg, write = self.run_fake_controller(temp, barrier_failure=True)
            self.assertEqual(code, 1)
            popen.assert_called_once()
            write.assert_not_called()
            killpg.assert_called_once()
            self.assertIn("parent hard cap failed", (directory / "controller-terminal.json").read_text())

    def test_changed_interpreter_is_rejected_before_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            code, directory, _, popen, _, write = self.run_fake_controller(temp, interpreter_failure=True)
            self.assertEqual(code, 1)
            popen.assert_not_called()
            write.assert_not_called()
            self.assertIn("interpreter hash mismatch", (directory / "controller-terminal.json").read_text())

    def test_reusing_attempt_directory_does_not_launch_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "ef-backend-unit"
            directory.mkdir()
            original = directory / "failure.json"
            original.write_text("original")
            with patch.object(probe.subprocess, "Popen") as popen, patch.object(probe, "OUTPUT_ROOT", Path(temp)):
                with self.assertRaises(FileExistsError):
                    probe.controller(argparse.Namespace(output=str(directory)))
                popen.assert_not_called()
            self.assertEqual(original.read_text(), "original")

    def test_output_outside_private_workspace_is_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "ef-backend-forbidden"
            with self.assertRaisesRegex(ValueError, "workspace var"):
                probe.controller(argparse.Namespace(output=str(output)))
            self.assertFalse(output.exists())

    def test_closed_barrier_fails_without_torch_or_runtime_reads(self):
        import os
        with tempfile.TemporaryDirectory() as temp:
            read_fd, write_fd = os.pipe()
            os.close(write_fd)
            args = argparse.Namespace(output=temp, barrier_fd=read_fd)
            with (patch.object(probe.signal, "signal"), patch.object(probe.signal, "alarm") as alarm,
                  patch.object(probe, "tiny_training") as training,
                  patch.object(probe, "read_pin") as pin, patch.object(probe, "readback", return_value={})):
                self.assertEqual(probe.worker(args), 1)
                training.assert_not_called()
                pin.assert_not_called()
                alarm.assert_called_once_with(120)
            receipt = json.loads((Path(temp) / "worker-failure.json").read_text())
            self.assertEqual(receipt["stage"], "barrier")

    def test_worker_resource_mismatch_fails_before_runtime_or_torch(self):
        import os
        with tempfile.TemporaryDirectory() as temp:
            read_fd, write_fd = os.pipe()
            os.write(write_fd, b"1")
            os.close(write_fd)
            args = argparse.Namespace(output=temp, barrier_fd=read_fd)
            with (patch.object(probe.signal, "signal"), patch.object(probe.signal, "alarm"),
                  patch.object(probe, "tiny_training") as training,
                  patch.object(probe, "read_pin") as pin, patch.object(probe, "readback", return_value={}),
                  patch.object(probe, "assert_worker_limits", side_effect=RuntimeError("AS mismatch"))):
                self.assertEqual(probe.worker(args), 1)
                training.assert_not_called()
                pin.assert_not_called()

    def test_invalid_thread_or_resource_readback_is_rejected(self):
        values = {"rlimit_as": [probe.WORKER_AS] * 2, "rlimit_cpu": [50, 50],
                  "affinity": [0], "nice": 10, "as_bytes": 1000000, "cpu_seconds": 0.1,
                  "rlimit_nproc": [0, 0], "tasks": 1}
        with (patch.object(probe, "require_unprivileged_worker"),
              patch.dict(probe.os.environ, probe.clean_environment(Path("/tmp/ef-backend-unit")), clear=True)):
            probe.assert_worker_limits(values)
            for key, invalid in (("nice", 0), ("affinity", [0, 1]), ("rlimit_cpu", [50, 60]),
                                 ("rlimit_as", [probe.WORKER_AS, probe.WORKER_AS + 1]),
                                 ("rlimit_nproc", [1, 1]), ("tasks", 2)):
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    probe.assert_worker_limits({**values, key: invalid})
            probe.os.environ["OMP_NUM_THREADS"] = "2"
            with self.assertRaisesRegex(RuntimeError, "thread"):
                probe.assert_worker_limits(values)

    def test_worker_refuses_ids_and_capabilities_exempt_from_task_limit(self):
        for uids, permitted in (((0, 1000, 1000), 0), ((1000,) * 3, 1 << 21), ((1000,) * 3, 1 << 24)):
            with (self.subTest(uids=uids, permitted=permitted),
                  patch.object(probe.os, "getresuid", return_value=uids),
                  patch.object(Path, "read_text", return_value=f"CapPrm:\t{permitted:x}\n"),
                  self.assertRaisesRegex(RuntimeError, "Linux task limit")):
                probe.require_unprivileged_worker()


if __name__ == "__main__":
    unittest.main()
