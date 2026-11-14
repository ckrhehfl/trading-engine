"""Closed WSL entry point for the preregistered EL comparison."""

import argparse
import ast
from hashlib import sha1, sha256
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import sys
import time


ROOT = Path(__file__).absolute().parents[2]
SPEC = "configs/research/discovery/intraday-event-model-v1.json"
DATASET = "configs/research/discovery/intraday-event-dataset-v1.json"
ORIGINAL = "configs/research/discovery/intraday-trader-local-input-v1.json"
ENTRY = "research.intraday_event_model_run"
MIB = 1024**2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _git(*args):
    def limits():
        resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
        resource.setrlimit(resource.RLIMIT_AS, (96 * MIB,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))

    return subprocess.check_output(
        ["/usr/bin/git", "--no-optional-locks", "-c", "core.safecrlf=false", "-c", "core.fsmonitor=false",
         "-c", "core.preloadindex=false", "-c", "index.threads=1", "-C", str(ROOT), *args],
        env={"PATH": "/usr/bin:/bin", "HOME": "/home/minju", "LC_ALL": "C"}, preexec_fn=limits, timeout=20)


def normal_merge(merge, reviewed, tree, *, current=False):
    require(all(type(value) is str and re.fullmatch("[0-9a-f]{40}", value) for value in (merge, reviewed, tree)),
            "EL exact Git identities required")
    commit, commit_tree, parents = _git("show", "-s", "--format=%H%n%T%n%P", merge).decode().splitlines()
    require(commit == merge and commit_tree == tree and len(parents.split()) == 2
            and parents.split()[1] == reviewed and parents.split()[0] != reviewed
            and _git("rev-parse", reviewed + "^{tree}").decode().strip() == tree,
            "EL normal reviewed merge required")
    _git("merge-base", "--is-ancestor", merge, "refs/remotes/origin/main")
    if current:
        require(_git("rev-parse", "HEAD").decode().strip() == merge
                and not _git("status", "--porcelain", "--untracked-files=no"), "EL reviewed checkout differs")


class ReviewedModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, modules):
        self.modules = modules

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "research" or fullname.startswith("research."):
            require(fullname in self.modules, "Unregistered EL project import")
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname == "research")
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path, raw = self.modules[module.__name__]
        module.__file__ = str(ROOT / path)
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)


def _source_bytes(path, blob):
    location = ROOT / path
    require(not any(part.is_symlink() for part in (location, *location.parents)), "EL source alias rejected")
    before = location.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size < 2 * MIB, "EL source bound")

    def stamp(value):
        return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)

    fd = os.open(location, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        require(stamp(os.fstat(fd)) == stamp(before), "EL source changed before opening")
        raw = stream.read(2 * MIB)
        require(stamp(os.fstat(fd)) == stamp(location.stat()) == stamp(before) and len(raw) == before.st_size,
                "EL source changed during admission")
    digest = sha1(f"blob {len(raw)}\0".encode() + raw, usedforsecurity=False).hexdigest()
    require(blob == "100644 blob " + digest, "EL reviewed source blob differs")
    return raw


def freeze(merge, reviewed, tree):
    normal_merge(merge, reviewed, tree, current=True)
    items = _git("ls-tree", "-rz", "--full-tree", reviewed, "--", "python/research", SPEC, DATASET, ORIGINAL)
    entries = {name: blob for blob, name in (item.decode().split("\t") for item in items.split(b"\0") if item)}

    def read(path):
        require(path in entries, "EL source missing from reviewed tree")
        return _source_bytes(path, entries[path])

    registrations = {path: read(path) for path in (SPEC, DATASET, ORIGINAL)}
    modules, pending = {}, ["research", ENTRY]
    while pending:
        name = pending.pop()
        if name in modules:
            continue
        path = "python/" + name.replace(".", "/") + ("/__init__.py" if name == "research" else ".py")
        raw = read(path)
        modules[name] = path, raw
        for node in ast.walk(ast.parse(raw)):
            if isinstance(node, ast.ImportFrom):
                require(not node.level, "EL relative project import requires review")
                if node.module == "research":
                    pending.extend("research." + alias.name for alias in node.names)
                elif node.module and node.module.startswith("research."):
                    pending.append(node.module)
            elif isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names if alias.name.startswith("research."))
    require(not any(name == "research" or name.startswith("research.") for name in sys.modules),
            "EL project import preceded source admission")
    sys.meta_path.insert(0, ReviewedModules(modules))
    pins = {path: sha256(raw).hexdigest() for path, raw in (*registrations.items(), *modules.values())}
    return {path: json.loads(raw) for path, raw in registrations.items()}, {
        "merge": merge, "reviewed": reviewed, "tree": tree, "sources": pins, "registration_sha256": pins[SPEC]}


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("invented-dataset", "invented-dataset-audit", "qualification",
                                        "qualification-audit", "cases", "actual", "actual-audit", "summary"))
    for name in ("merge", "reviewed", "tree"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--dataset-binding-sha256")
    parser.add_argument("--kind")
    parser.add_argument("--scope")
    parser.add_argument("--fold", type=int)
    parser.add_argument("--shard", type=int)
    parser.add_argument("--attempt", choices=("actual1", "actual2"))
    args = parser.parse_args()
    require(sys.platform == "linux" and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
            and sys.pycache_prefix == "/dev/null" and "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower(),
            "EL requires local WSL Python -I -S -B -X pycache_prefix=/dev/null")
    registrations, release = freeze(args.merge, args.reviewed, args.tree)
    from research import intraday_event_model_launch
    intraday_event_model_launch.execute(args, registrations, release, started, normal_merge)


if __name__ == "__main__":
    main()
