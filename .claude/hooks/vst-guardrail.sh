#!/usr/bin/env bash
# PreToolUse guardrail for Edit/Write, added Paper Trading Bridge Task H
# (credentials now exist in .env -- see CLAUDE.md's Tooling Stack table,
# this hook was the row marked "not built yet").
#
# What this covers (see .planning/paper-trading-h-vst-integration.md for
# the full "what this does and doesn't cover" writeup):
#   A. Blocks a CI workflow file (.github/workflows/*.yml|*.yaml) from ever
#      referencing BINGX_API_KEY/BINGX_API_SECRET or the bingx-vst
#      execution mode -- the real VST order-execution path must stay
#      local-only, run by a human, never wired into CI.
#   B. Blocks a .java edit whose resulting file would read
#      BINGX_VST_BASE_URL (the hardcoded VST-host constant on
#      PaperTradingApp) from an environment variable in the same
#      statement -- reintroducing the one configuration surface the
#      overall safety design deliberately eliminated.
#
# Deliberately a separate script file, not another giant inline one-liner
# like the existing bingx-hostname-guard PreToolUse hook -- this one
# embeds a real Python analysis step (comment-stripping + statement-level
# parsing) that would be impractical to correctly triple-escape into a
# single JSON string value; a plain file avoids that class of bug
# entirely and is easier to review/test/lint on its own.
#
# Both guardrails' real logic (candidate-file reconstruction, the
# comment-aware Java lexer, the statement-level check) lives in
# vst_guardrail_check.py, with its own regression suite in
# test_vst_guardrail_check.py (run: `python3 .claude/hooks/
# test_vst_guardrail_check.py`) -- this shell script is now just a thin
# wrapper translating that script's OK/BLOCK_WORKFLOW/BLOCK_JAVA decision
# into the hook's own exit-code/stderr-message contract.
set -uo pipefail

INPUT=$(cat)

echo "$INPUT" | jq -e '.tool_input.file_path // null | type == "string" and length > 0' >/dev/null
if [ $? -ne 0 ]; then
  echo "BLOCKED: tool_input.file_path missing, empty, not a string, or hook payload JSON unparseable." >&2
  exit 2
fi
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

echo "$INPUT" | jq -e '(.tool_input.new_string // .tool_input.content // null) | . == null or type == "string"' >/dev/null
if [ $? -ne 0 ]; then
  echo "BLOCKED: tool_input.new_string/content present but not a string." >&2
  exit 2
fi

# Both guardrails now live in one Python script (vst_guardrail_check.py),
# which reconstructs the actual RESULTING file content (old_string
# applied to the real current file, or Write's own content) and checks
# THAT -- not just this one call's own diff fragment. This is a real fix,
# not a style choice: an earlier version only gave this treatment to
# Guardrail B; a real, correctly-identified CodeRabbit review finding on
# this PR caught that Guardrail A (the CI-workflow check) still only
# checked the raw fragment, so a forbidden token split across two
# separate Edit calls into a workflow file would have passed both
# individually. Sharing one candidate-reconstruction path for both closes
# that gap for good, not just for the one case that was found.
DECISION=$(echo "$INPUT" | python3 "$(dirname "$0")/vst_guardrail_check.py")
case "$DECISION" in
  BLOCK_WORKFLOW)
    echo "BLOCKED: CI workflow $FILE references BINGX_API_KEY/BINGX_API_SECRET or the bingx-vst execution mode (checked against the resulting file content, not just this one edit's own fragment). The real VST order-execution path must stay local-only, run by a human, and never be wired into CI -- see CLAUDE.md Non-negotiable Rules (never add live exchange write-access in CI) and .planning/paper-trading-h-vst-integration.md." >&2
    exit 2
    ;;
  BLOCK_JAVA)
    echo "BLOCKED: $FILE appears to read BINGX_VST_BASE_URL from an environment variable (getenv found in the same statement, checked against the resulting file content after this edit, not just this one edit's own fragment). The VST order-execution host must remain a hardcoded Java constant with no environment-variable or argument override -- see the PaperTradingApp class Javadoc and CLAUDE.md Non-negotiable Rules." >&2
    exit 2
    ;;
  OK)
    exit 0
    ;;
  *)
    echo "BLOCKED: vst_guardrail_check.py produced an unexpected result ('$DECISION') -- failing closed rather than silently allowing the edit." >&2
    exit 2
    ;;
esac
