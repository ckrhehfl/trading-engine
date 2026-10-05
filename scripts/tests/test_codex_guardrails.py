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
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def check(self, body, cwd=None):
        return guard.check_patch({
            "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
            "cwd": str(cwd or self.root),
            "tool_input": {"command": "*** Begin Patch\n" + body + "*** End Patch\n"},
        }, self.root)

    def test_safe_add_and_existing_file_are_not_written(self):
        p = self.put("python/a.py", "x = 1\n")
        self.assertEqual([], self.check("*** Update File: python/a.py\n@@\n-x = 1\n+x = 2\n"))
        self.assertEqual("x = 1\n", p.read_text())
        self.assertEqual([], self.check("*** Add File: python/b.py\n+x = 3\n"))
        self.assertFalse((self.root / "python/b.py").exists())

    def test_hostname_assembled_across_context_and_new_line_is_blocked(self):
        self.put("python/a.py", 'HOST = "open-api' + '.bingx.com"\nx = 1\n')
        self.assertTrue(self.check("*** Update File: python/a.py\n@@\n-x = 1\n+x = 2\n"))

    def test_add_production_host_is_blocked_case_insensitively(self):
        host = guard.PRODUCTION_HOST.upper()
        self.assertTrue(self.check(f'*** Add File: java/A.java\n+String host = "{host}";\n'))

    def test_workflow_split_edit_checks_entire_result(self):
        self.put(".github/workflows/x.yml", "env:\n  BINGX_API_KEY: placeholder\nsteps: []\n")
        self.assertTrue(self.check("*** Update File: .github/workflows/x.yml\n@@\n-steps: []\n+steps: [x]\n"))

    def test_java_alias_in_existing_context_is_blocked(self):
        self.put("java/A.java", 'String a = System.getProperty("HOST");\nString unrelated = a;\n')
        self.assertTrue(self.check("*** Update File: java/A.java\n@@\n-String unrelated = a;\n+String BINGX_VST_BASE_URL = a;\n"))

    def test_unrelated_configuration_and_vst_literal_allowed(self):
        body = ('*** Add File: java/A.java\n'
                '+String BINGX_VST_BASE_URL = "https://open-api-vst.bingx.com";\n'
                '+String other = System.getenv("OTHER");\n')
        self.assertEqual([], self.check(body))

    def test_move_is_checked_under_destination_extension(self):
        self.put("notes.txt", 'String host = "' + guard.PRODUCTION_HOST + '";\n')
        self.assertTrue(self.check('*** Update File: notes.txt\n*** Move to: java/A.java\n@@\n'
                                   '-String host = "' + guard.PRODUCTION_HOST + '";\n'
                                   '+String host = "' + guard.PRODUCTION_HOST + '";\n'))

    def test_relative_path_from_subdirectory(self):
        sub = self.root / "python"
        sub.mkdir()
        self.assertEqual([], self.check("*** Add File: a.py\n+x = 1\n", sub))

    def test_absolute_path(self):
        p = self.root / "a.py"
        self.assertEqual([], self.check(f"*** Add File: {p.as_posix()}\n+x = 1\n"))

    def test_delete_does_not_read_file_content(self):
        self.put("python/a.py", guard.PRODUCTION_HOST)
        self.assertEqual([], self.check("*** Delete File: python/a.py\n"))

    def test_external_and_sensitive_paths_rejected(self):
        for name in ("../outside.py", ".env", ".ENV", ".env.local", ".git/config", ".GIT/config"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.check(f"*** Add File: {name}\n+x\n")
        self.assertEqual([], self.check("*** Add File: .env.example\n+EXAMPLE=\n"))

    def test_symlink_outside_repo_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            link = self.root / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("host does not allow user symlinks")
            with self.assertRaises(ValueError):
                self.check("*** Add File: linked/a.py\n+x\n")

    def test_unknown_payloads_fail_closed(self):
        for payload in ({}, {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                            "cwd": str(self.root), "tool_input": {"patch": "ignored"}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                guard.check_patch(payload, self.root)

    def test_ambiguous_or_missing_context_rejected(self):
        self.put("a.py", "x\nx\n")
        for old in ("x", "missing"):
            with self.subTest(old=old), self.assertRaises(ValueError):
                self.check(f"*** Update File: a.py\n@@\n-{old}\n+y\n")

    def test_repeated_path_and_unknown_operation_rejected(self):
        for body in ("*** Add File: a.py\n+x\n*** Add File: a.py\n+y\n",
                     "*** Rename File: a.py\n"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.check(body)

    def test_multiple_hunks_anchor_and_eof(self):
        text = "start\na\nkeep\nend\nz\n"
        result = guard.apply_hunks(text, ["@@ start", "-a", "+b", "@@ end", "-z", "+q", "*** End of File"])
        self.assertEqual("start\nb\nkeep\nend\nq\n", result)

    def test_context_free_insertion_does_not_guess_position(self):
        with self.assertRaises(ValueError):
            guard.apply_hunks("existing\n", ["@@", "+new"])
        self.assertEqual("new\n", guard.apply_hunks("", ["@@", "+new"]))

    def test_malformed_input_exits_two_without_echoing_secret(self):
        for content in ('{secret-value', 'null', '[]', '"secret-value"'):
            with self.subTest(content=content):
                p = subprocess.run([sys.executable, str(SCRIPT), "hook"], input=content,
                                   text=True, capture_output=True)
                self.assertEqual(2, p.returncode)
                self.assertNotIn("secret-value", p.stderr + p.stdout)

    def test_native_hook_command_from_subdirectory(self):
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
        copied = self.put("scripts/codex_guardrails.py", SCRIPT.read_text())
        p = subprocess.run([sys.executable, str(copied), "hook"], input="{}",
                           text=True, capture_output=True)
        self.assertEqual(2, p.returncode)
        self.assertIn("could not be loaded", p.stderr)


class StagedGuardTest(unittest.TestCase):
    def test_scans_index_even_when_worktree_was_cleaned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
            (root / "python").mkdir()
            p = root / "python/a.py"
            p.write_text('host = "' + guard.PRODUCTION_HOST + '"\n')
            subprocess.run(["git", "add", "python/a.py"], cwd=root, check=True, env=env)
            p.write_text('host = "demo"\n')
            with patch.object(guard, "ROOT", root), patch.dict(os.environ, {"GIT_DIR": "/not-this-repo"}):
                self.assertTrue(guard.scan(staged=True))
                self.assertEqual([], guard.scan())


if __name__ == "__main__":
    unittest.main()
