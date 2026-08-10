#!/usr/bin/env python3
"""Regression tests for vst_guardrail_check.py -- stdlib unittest only, no
extra dependency needed (run via `python3 .claude/hooks/
test_vst_guardrail_check.py` from the repo root, matching vst-guardrail.sh's
own documented invocation form -- NOT `python3 -m unittest
.claude/hooks/test_vst_guardrail_check.py`, a real, correctly-identified
CodeRabbit review finding on this PR: `-m unittest` treats its argument as
a dotted module/test name, not a file path, and raises `ValueError: Empty
module name` for a path shaped like this one; confirmed by actually running
it, not just reasoned about). Covers the real, correctly-identified
CodeRabbit review findings this file's own functions were rewritten to
close (comment-stripping bypass via `//` inside a string literal;
Guardrail A only checking the diff fragment, not the resulting file;
System.getProperty as an undetected getenv-equivalent bypass; a payload
shape reconstruct_candidate can't understand silently passing as OK) plus
the pre-existing false-positive checks against this project's own real
code.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vst_guardrail_check import (  # noqa: E402
    check_java_guardrail,
    check_workflow_guardrail,
    normalize_path,
    reconstruct_candidate,
    strip_java_comments,
)


class StripJavaCommentsTest(unittest.TestCase):
    def test_removes_line_comment(self):
        self.assertEqual(strip_java_comments("int x = 1; // comment\n"), "int x = 1; \n")

    def test_removes_block_comment(self):
        self.assertEqual(strip_java_comments("int x /* comment */ = 1;"), "int x  = 1;")

    def test_removes_javadoc_block_comment_spanning_lines(self):
        text = "/**\n * getenv and BINGX_VST_BASE_URL discussed here\n */\nint x = 1;"
        self.assertEqual(strip_java_comments(text), "\n\n\nint x = 1;")

    def test_does_not_strip_double_slash_inside_a_string_literal(self):
        # The exact real bypass CodeRabbit found: a naive `//`-based
        # comment stripper erases "https://" and everything after it on
        # the line, including the real getenv(...) call that follows.
        text = 'String x = "https://" + System.getenv("BINGX_VST_BASE_URL");'
        self.assertEqual(strip_java_comments(text), text, "a // inside a string literal must not start a comment")

    def test_does_not_strip_double_slash_inside_a_char_literal_context(self):
        text = "char a = '/'; char b = '/';"
        self.assertEqual(strip_java_comments(text), text)

    def test_handles_escaped_quote_inside_string_without_ending_it_early(self):
        text = 'String x = "a \\" // still inside the string" + getenv("y");'
        # The escaped quote must not end the string early -- if it did,
        # the following `//` would be seen as CODE-state and start a
        # real (incorrect) comment.
        self.assertEqual(strip_java_comments(text), text)

    def test_real_comment_after_a_string_with_slashes_is_still_stripped(self):
        text = 'String x = "https://example.com"; // real comment\nint y = 2;'
        expected = 'String x = "https://example.com"; \nint y = 2;'
        self.assertEqual(strip_java_comments(text), expected)


class CheckJavaGuardrailTest(unittest.TestCase):
    def test_blocks_getenv_and_constant_in_same_statement(self):
        candidate = 'private static final String BINGX_VST_BASE_URL = System.getenv("X");'
        self.assertTrue(check_java_guardrail("Foo.java", candidate))

    def test_the_real_bypass_payload_from_the_coderabbit_finding_is_now_blocked(self):
        candidate = (
            "package engine.runtime;\n"
            "public class Foo {\n"
            '    private static final String BINGX_VST_BASE_URL =\n'
            '        "https://" + System.getenv("BINGX_VST_BASE_URL");\n'
            "}\n"
        )
        self.assertTrue(check_java_guardrail("Foo.java", candidate))

    def test_does_not_block_unrelated_statements_even_when_close_together(self):
        # Mirrors the real, legitimate PaperTradingApp.forBingXVst shape:
        # a getenv call for a different constant sits right next to the
        # real BINGX_VST_BASE_URL usage, in separate statements.
        candidate = (
            'String apiKey = requireNonBlank(System.getenv(ENV_BINGX_API_KEY), ENV_BINGX_API_KEY);\n'
            'BingXAdapter adapter = new BingXAdapter(apiKey, apiSecret, BINGX_VST_BASE_URL);\n'
        )
        self.assertFalse(check_java_guardrail("Foo.java", candidate))

    def test_does_not_block_javadoc_prose_mentioning_both_terms(self):
        candidate = (
            "/**\n"
            " * Discusses getenv and BINGX_VST_BASE_URL together in prose,\n"
            " * with no semicolon anywhere in this comment.\n"
            " */\n"
            "public final class Foo {}\n"
        )
        self.assertFalse(check_java_guardrail("Foo.java", candidate))

    def test_non_java_file_is_never_checked(self):
        candidate = 'BINGX_VST_BASE_URL = System.getenv("x");'
        self.assertFalse(check_java_guardrail("Foo.py", candidate))

    def test_blocks_system_getproperty_as_an_undetected_getenv_equivalent_bypass(self):
        # Real, correctly-identified CodeRabbit review finding on this PR:
        # the pre-fix check only looked for the literal substring "getenv",
        # so System.getProperty(...) -- a real, different JVM configuration
        # surface (settable via a -D flag) that CLAUDE.md's own "no
        # environment variable, argument, or other configuration surface"
        # wording already covers -- was not textually caught at all.
        candidate = 'private static final String BINGX_VST_BASE_URL = System.getProperty("x");'
        self.assertTrue(check_java_guardrail("Foo.java", candidate))

    def test_known_disclosed_limitation_variable_aliased_bypass_across_statements_is_not_currently_blocked(self):
        # Real, correctly-identified CodeRabbit review finding on this PR
        # (round 4), deliberately NOT fixed -- see vst_guardrail_check.py's
        # own module docstring, "Known, disclosed, deliberately-NOT-fixed
        # limitation", for the full reasoning (a real "Heavy lift" per the
        # reviewer's own label: closing this needs genuine cross-statement
        # variable/constant taint tracking, not a quick fix). This test
        # pins the CURRENT, accepted behavior deliberately: if this ever
        # starts passing (i.e. the bypass gets blocked), that is a real,
        # deliberate fix that should update this test and the docstring
        # together -- not silent, undocumented drift either direction.
        candidate = (
            'private static final String VST_HOST_PROPERTY = "BINGX_VST_BASE_URL";\n'
            "private static final String CONFIGURED_HOST = System.getProperty(VST_HOST_PROPERTY);\n"
            "private static final String BINGX_VST_BASE_URL = CONFIGURED_HOST;\n"
        )
        self.assertFalse(
            check_java_guardrail("Foo.java", candidate),
            "this is the known, disclosed, deliberately-unfixed variable-aliasing bypass -- if this assertion"
            " now fails, the bypass has been closed for real and this test (and the docstring) should be"
            " updated to reflect that, not silently left stale",
        )

    def test_blocks_system_getproperties_plural_map_style_form(self):
        # A real, independently-verified gap (found and fixed alongside a
        # separate, inaccurate claim about this same file that did NOT hold
        # up under direct testing -- see .planning/paper-trading-h-vst-
        # integration.md's "round 5" section for the full account of both).
        # System.getProperties() (plural, no arguments) returns a
        # Map<Object,Object>-shaped Properties snapshot; .get("KEY") reads
        # a single property out of it -- a different real JVM configuration
        # accessor than System.getProperty(String) (singular), and the
        # pre-fix GETENV_EQUIVALENT_TOKENS list only contained "getproperty",
        # not "getproperties", so this form was not textually caught.
        candidate = 'String x = (String) System.getProperties().get("BINGX_VST_BASE_URL");'
        self.assertTrue(check_java_guardrail("Foo.java", candidate))

    def test_still_blocks_the_chained_system_getenv_get_form(self):
        # Not a new bug -- the existing same-statement substring
        # co-occurrence check already catches this shape (both "getenv"
        # and "BINGX_VST_BASE_URL" appear in the one statement), verified
        # here as a regression test rather than left unconfirmed.
        candidate = 'String x = System.getenv().get("BINGX_VST_BASE_URL");'
        self.assertTrue(check_java_guardrail("Foo.java", candidate))


class CheckWorkflowGuardrailTest(unittest.TestCase):
    def test_blocks_forbidden_token_in_workflow_file(self):
        self.assertTrue(check_workflow_guardrail(".github/workflows/x.yml", "env:\n  BINGX_API_KEY: secret\n"))

    def test_blocks_bingx_vst_mode_mention(self):
        self.assertTrue(
            check_workflow_guardrail(".github/workflows/x.yml", "env:\n  PAPER_TRADING_EXECUTION_MODE: bingx-vst\n")
        )

    def test_normalizes_windows_style_backslash_paths(self):
        self.assertTrue(check_workflow_guardrail("C:\\repo\\.github\\workflows\\x.yml", "BINGX_API_KEY: x"))

    def test_does_not_block_a_normal_workflow_file(self):
        self.assertFalse(check_workflow_guardrail(".github/workflows/x.yml", "run: ./gradlew build\n"))

    def test_non_workflow_file_is_never_checked(self):
        self.assertTrue(check_workflow_guardrail is not None)  # sanity
        self.assertFalse(check_workflow_guardrail("java/Foo.java", "BINGX_API_KEY"))


class ReconstructCandidateSplitEditTest(unittest.TestCase):
    """The real, correctly-identified CodeRabbit review finding: Guardrail A
    (and, before an earlier fix, Guardrail B too) only ever checked the raw
    new_string fragment of a single Edit call -- a forbidden token split
    across two separate Edit calls into a workflow file would pass both
    individually. This proves the fix: reconstruct_candidate reads the
    real current file and applies the edit, so a second call sees the
    *completed* forbidden pattern.
    """

    def test_a_forbidden_token_split_across_two_edits_is_detected_on_the_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            workflow_dir = os.path.join(tmp, ".github", "workflows")
            os.makedirs(workflow_dir, exist_ok=True)
            workflow_path = os.path.join(workflow_dir, "check.yml")
            # Step 1 (already applied to disk): introduces the YAML key
            # with no value yet -- innocuous on its own.
            with open(workflow_path, "w", encoding="utf-8") as fh:
                fh.write("env:\n  SOME_KEY: placeholder\n")

            # Step 2 (the call under evaluation): a separate Edit that
            # appends the real forbidden key, completing the pattern only
            # once spliced into the real current file.
            tool_input = {
                "file_path": workflow_path,
                "old_string": "  SOME_KEY: placeholder\n",
                "new_string": "  SOME_KEY: placeholder\n  BINGX_API_KEY: ${{ secrets.BINGX_API_KEY }}\n",
            }
            candidate = reconstruct_candidate(tool_input)
            self.assertTrue(check_workflow_guardrail(workflow_path, candidate))

    def test_a_split_bingx_vst_base_url_getenv_statement_is_detected_on_the_second_java_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            java_path = os.path.join(tmp, "Split.java")
            with open(java_path, "w", encoding="utf-8") as fh:
                fh.write(
                    "package engine.runtime;\n"
                    "public class Split {\n"
                    "    private static final String BINGX_VST_BASE_URL = System.getenv(\n"
                )
            tool_input = {
                "file_path": java_path,
                "old_string": "System.getenv(\n",
                "new_string": 'System.getenv(\n        "BINGX_VST_BASE_URL");\n}\n',
            }
            candidate = reconstruct_candidate(tool_input)
            self.assertTrue(check_java_guardrail(java_path, candidate))

    def test_write_tool_content_is_used_directly_not_reconstructed(self):
        tool_input = {"file_path": "x.java", "content": 'String x = "https://" + System.getenv("BINGX_VST_BASE_URL");'}
        candidate = reconstruct_candidate(tool_input)
        self.assertTrue(check_java_guardrail("x.java", candidate))


class ReconstructCandidateUnrecognizedPayloadTest(unittest.TestCase):
    """Real, correctly-identified CodeRabbit review finding on this PR:
    before this fix, a payload shape reconstruct_candidate doesn't
    understand (e.g. a hypothetical multi-edit "edits" array, or a truly
    empty tool_input) fell through to `new_string or ""` -- silently
    returning an empty string that both guardrails then check and always
    pass (nothing to match against), i.e. the guardrail silently no-ops
    for a payload shape it can't actually analyze. reconstruct_candidate
    must instead return None for anything it cannot confidently
    reconstruct, and main() must fail closed (block) rather than treat
    None as OK -- matching this project's own established fail-closed
    convention (e.g. SubmissionMarkerStore.load()) rather than silently
    degrading a security-relevant check.
    """

    def test_an_edits_array_payload_is_not_silently_treated_as_ok(self):
        tool_input = {
            "file_path": "Foo.java",
            "edits": [{"old_string": "a", "new_string": "b"}],
        }
        self.assertIsNone(reconstruct_candidate(tool_input))

    def test_a_truly_empty_tool_input_is_not_silently_treated_as_ok(self):
        self.assertIsNone(reconstruct_candidate({"file_path": "Foo.java"}))

    def test_main_blocks_on_an_unrecognized_payload_shape_rather_than_printing_ok(self):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vst_guardrail_check.py")
        payload = json.dumps({
            "tool_input": {
                "file_path": "Foo.java",
                "edits": [{"old_string": "a", "new_string": "b"}],
            }
        })
        result = subprocess.run([sys.executable, script], input=payload, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.stdout.strip(), "OK", "an unrecognized payload shape must never print OK")
        self.assertEqual(result.stdout.strip(), "BLOCK_UNRECOGNIZED_PAYLOAD")


class NormalizePathTest(unittest.TestCase):
    def test_backslashes_become_forward_slashes(self):
        self.assertEqual(normalize_path("C:\\a\\b.yml"), "C:/a/b.yml")

    def test_none_becomes_empty_string(self):
        self.assertEqual(normalize_path(None), "")


if __name__ == "__main__":
    unittest.main()
