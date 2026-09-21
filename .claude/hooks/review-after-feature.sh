#!/usr/bin/env bash
# Stop hook: once a feature's worth of source changes is sitting in the working
# tree, make Claude run /code-review and /simplify (plus /security-review when
# the change touches something security-relevant) before it finishes.
#
# Hooks can't run slash commands themselves, so this blocks the stop with a
# reason telling Claude to invoke the skills. Two guards keep it from nagging:
#   - stop_hook_active: the stop we caused by blocking doesn't block again.
#   - a fingerprint of the change set: reviewed once, quiet until the diff
#     changes again (the tree stays dirty until you commit).

set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

input="$(cat)"
if [ "$(jq -r '.stop_hook_active // false' <<<"$input")" = "true" ]; then
    exit 0
fi

# What counts as "a feature": changes to the service itself. Tests and docs
# alone don't trigger a review pass.
paths=(krzykacz scripts systemd)

mapfile -t changed < <(
    { git diff --name-only HEAD -- "${paths[@]}"
      git ls-files --others --exclude-standard -- "${paths[@]}"; } 2>/dev/null | sort -u
)
[ "${#changed[@]}" -eq 0 ] && exit 0

fingerprint="$(
    { git diff HEAD -- "${paths[@]}"
      for f in $(git ls-files --others --exclude-standard -- "${paths[@]}"); do
          echo "$f"; cat "$f"
      done; } 2>/dev/null | sha256sum | cut -d' ' -f1
)"
state=".claude/.last-review-fingerprint"
if [ -f "$state" ] && [ "$(cat "$state")" = "$fingerprint" ]; then
    exit 0
fi
echo "$fingerprint" > "$state"

# Security context: a sensitive file was touched, or an added line mentions
# something that widens the attack surface.
sensitive_files='(^|/)(auth|ratelimit|audit|http_server|mcp_server|procutil|config)\.py$|^systemd/|install-service\.sh$'
sensitive_terms='token|bearer|authorization|secret|password|credential|subprocess|popen|shell=|chmod|chown|sudo|0\.0\.0\.0|rate.?limit|traversal|sanitiz|untrusted|input='
security_reason=""
if printf '%s\n' "${changed[@]}" | grep -Eq "$sensitive_files"; then
    security_reason="touches security-relevant files ($(printf '%s\n' "${changed[@]}" | grep -E "$sensitive_files" | paste -sd, -))"
elif git diff HEAD -U0 -- "${paths[@]}" 2>/dev/null | grep -E '^\+[^+]' | grep -Eiq "$sensitive_terms"; then
    security_reason="adds code mentioning auth, subprocesses, network exposure or untrusted input"
fi

reason="Feature changes are sitting in the working tree ($(printf '%s\n' "${changed[@]}" | paste -sd, -)). Before you finish, run these review passes with the Skill tool, fix what they find, then re-run the test suite:
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
If the change is a trivial fix rather than a feature, say so and skip the passes."

jq -n --arg reason "$reason" '{decision: "block", reason: $reason}'
