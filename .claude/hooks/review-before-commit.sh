#!/usr/bin/env bash
# PreToolUse hook on `git commit`: before service code is committed, make
# Claude run /code-review and /simplify (plus /security-review when the change
# touches something security-relevant).
#
# Hooks can't run slash commands themselves, so the first commit attempt for a
# given set of files is denied with a reason telling Claude to invoke the
# skills; the retry goes through. "Given set of files" is deliberate: fixing
# what the reviews find edits the same files, and shouldn't re-trigger the
# review it just did.

set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

command="$(jq -r '.tool_input.command // ""' <<<"$(cat)")"

# What the commit will contain: `-a`/`--all` sweeps in every tracked change,
# otherwise only what's already staged.
if grep -Eq '(^|[[:space:]])(--all|-[a-zA-Z]*a[a-zA-Z]*)([[:space:]]|$)' <<<"$command"; then
    diff_args=(HEAD)
else
    diff_args=(--cached)
fi

# What counts as "a feature": changes to the service itself. Tests and docs
# alone don't trigger a review pass.
paths=(krzykacz scripts systemd)

diff="$(git diff "${diff_args[@]}" -U0 -- "${paths[@]}" 2>/dev/null)"
mapfile -t changed < <(git diff "${diff_args[@]}" --name-only -- "${paths[@]}" 2>/dev/null)
[ "${#changed[@]}" -eq 0 ] && exit 0

fingerprint="$(printf '%s\n' "${changed[@]}" | sha256sum | cut -d' ' -f1)"
state=".claude/.last-review-fingerprint"
if [ -f "$state" ] && [ "$(cat "$state")" = "$fingerprint" ]; then
    exit 0
fi
echo "$fingerprint" > "$state"

# Security context: a sensitive file is in the commit, or an added line
# mentions something that widens the attack surface.
sensitive_files='(^|/)(auth|ratelimit|audit|http_server|mcp_server|procutil|config)\.py$|^systemd/|install-service\.sh$'
sensitive_terms='token|bearer|authorization|secret|password|credential|subprocess|popen|shell=|chmod|chown|sudo|0\.0\.0\.0|rate.?limit|traversal|sanitiz|untrusted|input='
security_reason=""
if printf '%s\n' "${changed[@]}" | grep -Eq "$sensitive_files"; then
    security_reason="touches security-relevant files ($(printf '%s\n' "${changed[@]}" | grep -E "$sensitive_files" | paste -sd, -))"
elif grep -E '^\+[^+]' <<<"$diff" | grep -Eiq "$sensitive_terms"; then
    security_reason="adds code mentioning auth, subprocesses, network exposure or untrusted input"
fi

reason="Not committing yet: this commit changes service code ($(printf '%s\n' "${changed[@]}" | paste -sd, -)). Run these review passes with the Skill tool first, fix what they find (git add the fixes), re-run the tests, then run the same git commit again -- it will go through:
1. /code-review
2. /simplify"
if [ -n "$security_reason" ]; then
    reason="$reason
3. /security-review -- this change $security_reason."
else
    reason="$reason
(No security context detected, so /security-review is skipped. Run it anyway if you know this change has one.)"
fi
reason="$reason
If this is a trivial fix rather than a feature, say so and just retry the commit."

jq -n --arg reason "$reason" '{
    hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: $reason
    }
}'
