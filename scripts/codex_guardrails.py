#!/usr/bin/env python3
"""Adapt Codex patches to existing venue guards; scan final or staged files.

Exact-context patches only. Ambiguous/fuzzy hunks fail closed. This is a
secondary editing guard, not a sandbox, Java parser or secret scanner.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
try:
    spec = importlib.util.spec_from_file_location(
        "vst_guardrail_check", ROOT / ".claude/hooks/vst_guardrail_check.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("venue guard unavailable")
    venue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(venue)
except (ImportError, OSError, SyntaxError):
    print("BLOCKED: existing venue guard could not be loaded.", file=sys.stderr)
    raise SystemExit(2)
PRODUCTION_HOST = "open-api" + ".bingx.com"


def violations(path: str, content: str) -> list[str]:
    """Return venue-policy violations for a complete candidate file."""
    errors = []
    if path.lower().endswith((".java", ".py")) and PRODUCTION_HOST in content.lower():
        errors.append("production venue hostname in source")
    if venue.check_workflow_guardrail(path, content):
        errors.append("VST credentials/execution referenced by a CI workflow")
    if venue.check_java_guardrail(path, content):
        errors.append("VST host obtained from external configuration")
    return errors


def repo_path(raw: str, cwd: Path, root: Path) -> Path:
    """Resolve a patch path, rejecting escapes, Git internals and env secrets."""
    if not raw:
        raise ValueError("empty patch path")
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    # Resolve existing symlinks and parents before checking the boundary.
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("patch path is outside this repository")
    rel = path.relative_to(root.resolve())
    name = path.name.casefold()
    if ".git" in {part.casefold() for part in rel.parts} or name == ".env" or (
        name.startswith(".env.") and name not in {".env.example", ".env.sample"}
    ):
        raise ValueError("patch targets Git internals or a credential file")
    return path


def apply_hunks(current: str, lines: list[str]) -> str:
    """Reconstruct exact unique contexts; do not guess Codex's fuzzy matching."""
    source = current.splitlines()
    cursor = 0
    i = 0
    while i < len(lines):
        header = lines[i]
        if header != "@@" and not header.startswith("@@ "):
            raise ValueError("unsupported patch hunk header")
        i += 1
        if header.startswith("@@ "):
            anchor = header[3:]
            matches = [n for n in range(cursor, len(source)) if source[n] == anchor]
            if len(matches) != 1:
                raise ValueError("patch anchor is absent or ambiguous")
            cursor = matches[0] + 1
        before, after = [], []
        eof = False
        while i < len(lines) and not lines[i].startswith("@@"):
            line = lines[i]
            i += 1
            if line == "*** End of File":
                eof = True
                if i != len(lines):
                    raise ValueError("content follows end-of-file marker")
                break
            if not line or line[0] not in " +-":
                raise ValueError("unsupported patch hunk line")
            if line[0] in " -":
                before.append(line[1:])
            if line[0] in " +":
                after.append(line[1:])
        if not before:
            if source:
                raise ValueError("context-free insertion is unsupported; include exact context")
            start = len(source)
        else:
            matches = [n for n in range(cursor, len(source) - len(before) + 1)
                       if source[n:n + len(before)] == before
                       and (not eof or n + len(before) == len(source))]
            if len(matches) != 1:
                raise ValueError("patch context is absent or ambiguous; use more exact context")
            start = matches[0]
        source[start:start + len(before)] = after
        cursor = start + len(after)
    return "\n".join(source) + ("\n" if source else "")


def patch_candidates(patch: str, cwd: Path, root: Path = ROOT) -> dict[Path, str | None]:
    """Rebuild a strict patch without writes; map deleted paths to None."""
    lines = patch.splitlines()
    if len(lines) < 2 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("unrecognized apply_patch envelope")
    candidates: dict[Path, str | None] = {}
    i = 1
    while i < len(lines) - 1:
        header = lines[i]
        operations = [op for op in ("Add", "Update", "Delete")
                      if header.startswith(f"*** {op} File: ")]
        if len(operations) != 1:
            raise ValueError("unrecognized file operation")
        op = operations[0]
        path = repo_path(header[len(f"*** {op} File: "):], cwd, root)
        if path in candidates:
            raise ValueError("multiple operations on one path are unsupported")
        i += 1
        destination = path
        if op == "Update" and lines[i].startswith("*** Move to: "):
            destination = repo_path(lines[i][len("*** Move to: "):], cwd, root)
            if destination in candidates or destination.exists():
                raise ValueError("move destination already exists")
            i += 1
        body = []
        while i < len(lines) - 1 and not any(
            lines[i].startswith(f"*** {kind} File: ") for kind in ("Add", "Update", "Delete")
        ):
            body.append(lines[i])
            i += 1
        if op == "Delete":
            if body or not path.is_file():
                raise ValueError("invalid file deletion")
            candidates[path] = None
        elif op == "Add":
            if path.exists() or any(not line.startswith("+") for line in body):
                raise ValueError("invalid file addition")
            candidates[path] = "\n".join(line[1:] for line in body) + "\n"
        else:
            if not body:
                raise ValueError("empty update")
            candidates[destination] = apply_hunks(path.read_text(encoding="utf-8"), body)
            if destination != path:
                candidates[path] = None
    return candidates


def check_patch(payload: dict, root: Path = ROOT) -> list[str]:
    """Validate a Codex apply_patch event and return candidate violations."""
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be an object")
    if payload.get("hook_event_name") != "PreToolUse" or payload.get("tool_name") != "apply_patch":
        raise ValueError("unsupported hook event or tool")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        raise ValueError("apply_patch.command must be a string")
    cwd = Path(payload["cwd"]).resolve()
    if not cwd.is_relative_to(root.resolve()):
        raise ValueError("hook cwd is outside the repository")
    errors = []
    for path, content in patch_candidates(tool_input["command"], cwd, root).items():
        if content is not None:
            errors.extend(f"{path.relative_to(root)}: {error}" for error in violations(str(path), content))
    return errors


def scan(staged: bool = False, *, hook_index: bool = False) -> list[str]:
    """Scan the checkout/index; only the commit hook may supply a verified index."""
    env = os.environ.copy()
    commit_index = env.get("GIT_INDEX_FILE") if hook_index else None
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX"):
        env.pop(key, None)
    # Standalone scans also support worktrees created by Windows Git.
    pointer = ROOT / ".git"
    if sys.platform != "win32" and pointer.is_file():
        gitdir = pointer.read_text(encoding="utf-8").strip().removeprefix("gitdir: ")
        if len(gitdir) > 2 and gitdir[1:3] in {":/", ":\\"}:
            env["GIT_DIR"] = subprocess.check_output(["wslpath", "-u", gitdir], text=True).strip()
            env["GIT_WORK_TREE"] = str(ROOT)
    if hook_index:
        if not staged or not commit_index:
            raise ValueError("the commit hook must provide its candidate index")
        git_dir = Path(subprocess.check_output(
            ["git", "rev-parse", "--absolute-git-dir"], cwd=ROOT, env=env, text=True,
        ).strip()).resolve()
        index = Path(commit_index)
        if not index.is_absolute():
            index = ROOT / index
        index = index.resolve(strict=True)
        # Git's partial-commit index lives beside this worktree's regular index.
        # Resolving both paths also rejects symlinks into another repository.
        if index.parent != git_dir or not index.is_file():
            raise ValueError("commit index is outside this worktree's Git directory")
        env["GIT_INDEX_FILE"] = str(index)
    args = (["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"] if staged
            else ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    paths = subprocess.check_output(["git", *args], cwd=ROOT, env=env).decode("utf-8").split("\0")
    errors = []
    for name in sorted(set(paths) - {""}):
        if not ((name.startswith(("java/", "python/")) and name.endswith((".java", ".py")))
                or (name.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")))):
            continue
        if staged:
            content = subprocess.check_output(["git", "show", f":{name}"], cwd=ROOT, env=env).decode("utf-8")
        else:
            path = ROOT / name
            if not path.exists():  # A tracked file deleted in the worktree.
                continue
            content = path.read_text(encoding="utf-8")
        errors.extend(f"{name}: {error}" for error in violations(name, content))
    return errors


def main() -> int:
    """Return zero for validated input or the hook's blocking status on failure."""
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
        if mode == "hook":
            errors = check_patch(json.load(sys.stdin))
        elif mode in {"scan", "staged"}:
            errors = scan(staged=mode == "staged")
        elif mode == "pre-commit":
            errors = scan(staged=True, hook_index=True)
        else:
            raise ValueError("expected hook, scan, staged or pre-commit")
        if errors:
            print("BLOCKED: " + "; ".join(errors), file=sys.stderr)
            return 2
        if mode != "hook":
            print("Trading-engine guardrails: OK")
        return 0
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        # Never echo payload/content: hook input may itself contain a secret.
        print(f"BLOCKED: guardrail could not validate input ({type(exc).__name__}).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
