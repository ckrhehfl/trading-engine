"""Real candidate reconstruction and process exit tests, without exchange access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "codex_guardrails.py"
spec = importlib.util.spec_from_file_location("codex_guardrails", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class PatchGuardTest(unittest.TestCase):
    def setUp(self):
        """Keep patch fixtures outside the real checkout and clean them up."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def put(self, name, content):
        """Create a UTF-8 fixture beneath the isolated repository root."""
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def check(self, body, cwd=None):
        """Wrap a patch body in the native Codex event shape for validation."""
        return guard.check_patch({
            "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
            "cwd": str(cwd or self.root),
            "tool_input": {"command": "*** Begin Patch\n" + body + "*** End Patch\n"},
        }, self.root)

    def test_safe_add_and_existing_file_are_not_written(self):
        """Validation must neither create a file nor modify existing content."""
        p = self.put("python/a.py", "x = 1\n")
        self.assertEqual([], self.check("*** Update File: python/a.py\n@@\n-x = 1\n+x = 2\n"))
        self.assertEqual("x = 1\n", p.read_text())
        self.assertEqual([], self.check("*** Add File: python/b.py\n+x = 3\n"))
        self.assertFalse((self.root / "python/b.py").exists())

    def test_hostname_assembled_across_context_and_new_line_is_blocked(self):
        """An unchanged forbidden host still invalidates the complete candidate."""
        self.put("python/a.py", 'HOST = "open-api' + '.bingx.com"\nx = 1\n')
        self.assertTrue(self.check("*** Update File: python/a.py\n@@\n-x = 1\n+x = 2\n"))

    def test_add_production_host_is_blocked_case_insensitively(self):
        """Changing hostname case must not evade the source guard."""
        host = guard.PRODUCTION_HOST.upper()
        self.assertTrue(self.check(f'*** Add File: java/A.java\n+String host = "{host}";\n'))

    def test_workflow_split_edit_checks_entire_result(self):
        """Workflow credentials outside the edited hunk must remain visible."""
        self.put(".github/workflows/x.yml", "env:\n  BINGX_API_KEY: placeholder\nsteps: []\n")
        self.assertTrue(self.check("*** Update File: .github/workflows/x.yml\n@@\n-steps: []\n+steps: [x]\n"))

    def test_java_alias_in_existing_context_is_blocked(self):
        """A new host assignment must be checked against existing data flow."""
        self.put("java/A.java", 'String a = System.getProperty("HOST");\nString unrelated = a;\n')
        self.assertTrue(self.check("*** Update File: java/A.java\n@@\n-String unrelated = a;\n+String BINGX_VST_BASE_URL = a;\n"))

    def test_unrelated_configuration_and_vst_literal_allowed(self):
        """Unrelated environment reads must not taint the fixed demo host."""
        body = ('*** Add File: java/A.java\n'
                '+String BINGX_VST_BASE_URL = "https://open-api-vst.bingx.com";\n'
                '+String other = System.getenv("OTHER");\n')
        self.assertEqual([], self.check(body))

    def test_move_is_checked_under_destination_extension(self):
        """Renaming text into Java source must apply the Java destination rules."""
        self.put("notes.txt", 'String host = "' + guard.PRODUCTION_HOST + '";\n')
        self.assertTrue(self.check('*** Update File: notes.txt\n*** Move to: java/A.java\n@@\n'
                                   '-String host = "' + guard.PRODUCTION_HOST + '";\n'
                                   '+String host = "' + guard.PRODUCTION_HOST + '";\n'))

    def test_relative_path_from_subdirectory(self):
        """Resolve relative paths against the event's working directory."""
        sub = self.root / "python"
        sub.mkdir()
        self.assertEqual([], self.check("*** Add File: a.py\n+x = 1\n", sub))

    def test_absolute_path(self):
        """Permit an absolute file path that remains inside the repository."""
        p = self.root / "a.py"
        self.assertEqual([], self.check(f"*** Add File: {p.as_posix()}\n+x = 1\n"))

    def test_delete_does_not_read_file_content(self):
        """Removing an unsafe file must not validate it as retained content."""
        self.put("python/a.py", guard.PRODUCTION_HOST)
        self.assertEqual([], self.check("*** Delete File: python/a.py\n"))

    def test_external_and_sensitive_paths_rejected(self):
        """Reject path escapes and sensitive files while permitting templates."""
        for name in ("../outside.py", ".env", ".ENV", ".env.local", ".git/config", ".GIT/config"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.check(f"*** Add File: {name}\n+x\n")
        self.assertEqual([], self.check("*** Add File: .env.example\n+EXAMPLE=\n"))

    def test_symlink_outside_repo_rejected(self):
        """A link must not disguise a patch destination outside the boundary."""
        with tempfile.TemporaryDirectory() as outside:
            link = self.root / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("host does not allow user symlinks")
            with self.assertRaises(ValueError):
                self.check("*** Add File: linked/a.py\n+x\n")

    def test_unknown_payloads_fail_closed(self):
        """Unknown event or argument shapes must not silently pass validation."""
        for payload in ({}, {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                            "cwd": str(self.root), "tool_input": {"patch": "ignored"}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                guard.check_patch(payload, self.root)

    def test_ambiguous_or_missing_context_rejected(self):
        """Reject edits whose target cannot be reconstructed uniquely."""
        self.put("a.py", "x\nx\n")
        for old in ("x", "missing"):
            with self.subTest(old=old), self.assertRaises(ValueError):
                self.check(f"*** Update File: a.py\n@@\n-{old}\n+y\n")

    def test_repeated_path_and_unknown_operation_rejected(self):
        """Reject patch operations the candidate model cannot represent safely."""
        for body in ("*** Add File: a.py\n+x\n*** Add File: a.py\n+y\n",
                     "*** Rename File: a.py\n"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.check(body)

    def test_multiple_hunks_anchor_and_eof(self):
        """Apply independent anchored hunks and honor the end-of-file marker."""
        text = "start\na\nkeep\nend\nz\n"
        result = guard.apply_hunks(text, ["@@ start", "-a", "+b", "@@ end", "-z", "+q", "*** End of File"])
        self.assertEqual("start\nb\nkeep\nend\nq\n", result)

    def test_context_free_insertion_does_not_guess_position(self):
        """Require context for nonempty files instead of guessing where to add."""
        with self.assertRaises(ValueError):
            guard.apply_hunks("existing\n", ["@@", "+new"])
        self.assertEqual("new\n", guard.apply_hunks("", ["@@", "+new"]))

    def test_malformed_input_exits_two_without_echoing_secret(self):
        """Malformed JSON must block without reflecting potentially secret input."""
        for content in ('{secret-value', 'null', '[]', '"secret-value"'):
            with self.subTest(content=content):
                p = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=content,
                                   text=True, capture_output=True)
                self.assertEqual(2, p.returncode)
                self.assertNotIn("secret-value", p.stderr + p.stdout)

    def test_native_hook_command_from_subdirectory(self):
        """Exercise the configured shell command for safe and forbidden edits."""
        config = json.loads((SCRIPT.parents[1] / ".codex/hooks.json").read_text())
        definition = config["hooks"]["PreToolUse"][0]["hooks"][0]
        command = definition["commandWindows"] if sys.platform == "win32" else definition["command"]
        cwd = SCRIPT.parents[1] / "python"
        for name, content, expected in ((".codex-hook-probe.py", "x = 1", 0),
                                        (".codex-hook-probe.py", guard.PRODUCTION_HOST, 2),
                                        ("../.env", "VALUE=", 2)):
            with self.subTest(name=name, expected=expected):
                payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                           "cwd": str(cwd), "tool_input": {"command":
                           f"*** Begin Patch\n*** Add File: {name}\n+{content}\n*** End Patch\n"}}
                p = subprocess.run(command, shell=True, cwd=cwd, input=json.dumps(payload),
                                   text=True, capture_output=True)
                self.assertEqual(expected, p.returncode, p.stderr)
        self.assertFalse((cwd / ".codex-hook-probe.py").exists())

    def test_missing_existing_guard_fails_closed(self):
        """The adapter cannot allow edits when its existing venue guard is absent."""
        copied = self.put("scripts/codex_guardrails.py", SCRIPT.read_text())
        p = subprocess.run([sys.executable, str(copied), "hook"], input="{}",
                           text=True, capture_output=True)
        self.assertEqual(2, p.returncode)
        self.assertIn("could not be loaded", p.stderr)

    def test_hook_bootstrap_failures_exit_two_without_traceback(self):
        """Root and handler failures must block even before the adapter runs."""
        config = json.loads((SCRIPT.parents[1] / ".codex/hooks.json").read_text())
        definition = config["hooks"]["PreToolUse"][0]["hooks"][0]
        command = definition["commandWindows"] if sys.platform == "win32" else definition["command"]
        for failure in ("no_repository", "missing_handler", "broken_handler"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                if failure != "no_repository":
                    (root / ".git").mkdir()
                if failure == "broken_handler":
                    (root / "scripts").mkdir()
                    (root / "scripts/codex_guardrails.py").write_text(
                        "raise RuntimeError('secret-value')\n", encoding="utf-8")
                p = subprocess.run(command, shell=True, cwd=root, input="secret-value",
                                   text=True, capture_output=True)
                self.assertEqual(2, p.returncode, p.stderr)
                self.assertIn("BLOCKED:", p.stderr)
                self.assertNotIn("Traceback", p.stderr)
                self.assertNotIn("secret-value", p.stderr + p.stdout)


class StagedGuardTest(unittest.TestCase):
    def test_commit_hook_rejects_unverified_index_paths(self):
        """Only a real file inside this worktree's Git directory may be retained."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
            foreign = Path(temp) / "foreign-index"
            foreign.write_bytes(b"not an index")
            for candidate in (None, str(foreign), str(root / ".git/missing"), str(root / ".git")):
                hook_env = env.copy()
                if candidate is not None:
                    hook_env["GIT_INDEX_FILE"] = candidate
                with self.subTest(candidate=candidate), patch.object(guard, "ROOT", root), \
                        patch.dict(os.environ, hook_env, clear=True):
                    with self.assertRaises((ValueError, OSError)):
                        guard.scan(staged=True, hook_index=True)

    def test_scans_index_even_when_worktree_was_cleaned(self):
        """A safe working copy cannot hide an unsafe staged blob or foreign env."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
            (root / "python").mkdir()
            p = root / "python/a.py"
            p.write_text('host = "' + guard.PRODUCTION_HOST + '"\n')
            subprocess.run(["git", "add", "python/a.py"], cwd=root, check=True, env=env)
            p.write_text('host = "demo"\n')
            foreign_env = {key: str(root / "not-this-repo") for key in (
                "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
            )}
            with patch.object(guard, "ROOT", root), patch.dict(os.environ, foreign_env):
                self.assertTrue(guard.scan(staged=True))
                self.assertEqual([], guard.scan())


@unittest.skipIf(sys.platform == "win32", "Bash wrapper runs in WSL or Linux")
class DevWrapperTest(unittest.TestCase):
    def test_foreign_git_environment_cannot_select_another_repository(self):
        """The Bash entry point must select its own checkout despite inherited env."""
        root = SCRIPT.parents[1]
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX"):
                env[key] = str(Path(temp) / "missing")
            p = subprocess.run(
                ["bash", str(root / "scripts/dev.sh"), "git", "rev-parse", "--show-toplevel"],
                cwd=temp, env=env, text=True, capture_output=True,
            )
            self.assertEqual(0, p.returncode, p.stderr)
            self.assertEqual(root.resolve(), Path(p.stdout.strip()).resolve())


@unittest.skipIf(sys.platform == "win32", "The installed pre-commit hook runs in WSL or Linux")
class CommitHookTest(unittest.TestCase):
    def setUp(self):
        """Install the real hook in a temporary repo; stub only the secret scanner."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_AUTHOR_NAME="Guard Test", GIT_COMMITTER_NAME="Guard Test",
                        GIT_AUTHOR_EMAIL="guard@example.invalid", GIT_COMMITTER_EMAIL="guard@example.invalid")
        subprocess.run(["git", "init", "-q", str(self.root)], env=self.env, check=True)
        self.git("config", "core.hooksPath", ".githooks")
        self.git("config", "commit.gpgsign", "false")
        for name in ("scripts/codex_guardrails.py", ".claude/hooks/vst_guardrail_check.py", ".githooks/pre-commit"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((SCRIPT.parents[1] / name).read_text(), encoding="utf-8")
        (self.root / ".githooks/pre-commit").chmod(0o755)
        bindir = Path(self.temp.name) / "bin"
        bindir.mkdir()
        scanner = bindir / "gitleaks"
        scanner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        scanner.chmod(0o755)
        self.env["PATH"] = str(bindir) + os.pathsep + self.env.get("PATH", "")
        (self.root / "python").mkdir()
        for name in ("a.py", "b.py"):
            (self.root / "python" / name).write_text("x = 1\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "seed")
        self.seed = self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *args, check=True):
        """Run Git only in the isolated fixture with explicit test identity."""
        return subprocess.run(["git", *args], cwd=self.root, env=self.env,
                              text=True, capture_output=True, check=check)

    def linked_worktree(self):
        """Exercise a per-worktree Git directory as well as an ordinary clone."""
        target = Path(self.temp.name) / "linked"
        self.git("worktree", "add", "-q", "--detach", str(target), self.seed)
        self.root = target

    def test_partial_commit_blocks_unsafe_candidate_absent_from_regular_index(self):
        """A --only commit must inspect its temporary candidate, including worktrees."""
        for linked in (False, True):
            if linked:
                self.linked_worktree()
            with self.subTest(linked=linked):
                before = self.git("rev-parse", "HEAD").stdout
                (self.root / "python/a.py").write_text(guard.PRODUCTION_HOST, encoding="utf-8")
                self.assertNotIn(guard.PRODUCTION_HOST, self.git("show", ":python/a.py").stdout)
                result = self.git("commit", "--only", "-qm", "blocked", "--", "python/a.py", check=False)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("production venue hostname", result.stderr)
                self.assertEqual(before, self.git("rev-parse", "HEAD").stdout)

    def test_partial_commit_ignores_unsafe_staged_file_excluded_from_candidate(self):
        """Unselected staged changes must not be mistaken for the partial commit."""
        for linked, value in ((False, 2), (True, 3)):
            if linked:
                self.linked_worktree()
            with self.subTest(linked=linked):
                (self.root / "python/b.py").write_text(guard.PRODUCTION_HOST, encoding="utf-8")
                self.git("add", "python/b.py")
                (self.root / "python/a.py").write_text(f"x = {value}\n", encoding="utf-8")
                self.git("commit", "--only", "-qm", "safe partial commit", "--", "python/a.py")
                self.assertEqual(f"x = {value}\n", self.git("show", "HEAD:python/a.py").stdout)
                self.assertNotIn(guard.PRODUCTION_HOST, self.git("show", "HEAD:python/b.py").stdout)
                self.assertIn(guard.PRODUCTION_HOST, self.git("show", ":python/b.py").stdout)

    def test_regular_commit_still_blocks_unsafe_staged_file(self):
        """Using Git's candidate index must retain protection for ordinary commits."""
        before = self.git("rev-parse", "HEAD").stdout
        (self.root / "python/a.py").write_text(guard.PRODUCTION_HOST, encoding="utf-8")
        self.git("add", "python/a.py")
        result = self.git("commit", "-qm", "blocked", check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("production venue hostname", result.stderr)
        self.assertEqual(before, self.git("rev-parse", "HEAD").stdout)


if __name__ == "__main__":
    unittest.main()
