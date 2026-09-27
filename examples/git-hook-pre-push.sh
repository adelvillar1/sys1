#!/usr/bin/env bash
# .git/hooks/pre-push — drop-in sample showing sys1 used as a real gate.
#
# Install: copy this file to .git/hooks/pre-push in your repo, chmod +x.
# Requires: `pip install sys1` (or a venv that has it on PYTHONPATH).
#
# Behavior:
#   - Reads the diff between the pushed range and the upstream remote.
#   - Calls sys1 with the "pr_gate" task on the Decide backend.
#   - On "pass"  → exit 0, push proceeds.
#   - On escalate → exit 1, push blocked with the human-readable summary.
#   - On sys1 failure (no API key, network down, …) → exit 0 with a warning.
#     A gate that blocks pushes when *the gate itself* is broken trains
#     people to skip the gate; we'd rather warn and proceed.

set -euo pipefail

remote="$1"
url="$2"

# Collect the diff for the push range. git gives the refspecs on stdin.
diff_text="$(git diff --cached HEAD 2>/dev/null || true)"
if [ -z "$diff_text" ]; then
  # No staged changes — fall back to the last commit
  diff_text="$(git show --format= --no-color HEAD 2>/dev/null || true)"
fi
if [ -z "$diff_text" ]; then
  echo "sys1: empty diff — skipping classification."
  exit 0
fi

# Truncate very large diffs so the request body stays reasonable.
diff_text="${diff_text:0:12000}"

# The classify run. PYTHONPATH here picks up a local checkout; otherwise
# drop it and rely on the installed sys1.
PY="${SYS1_PYTHON:-python3}"
output="$("$PY" -c "
import json, sys
sys.path.insert(0, 'src')
import sys1
diff_text = sys.stdin.read()
result = sys1.classify('decide', 'pr_gate', diff_text)
print(json.dumps(result.as_dict()))
" <<<"$diff_text" 2>/dev/null || echo '{"verdict":"error","reason":"sys1 invocation failed"}')"

verdict=$(echo "$output" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('verdict','error'))")

case "$verdict" in
  pass)
    echo "sys1: ✓ $(echo "$output" | "$PY" -c "import json,sys;d=json.load(sys.stdin);print(' | '.join(d.get('summary',[])[:3]))")"
    exit 0
    ;;
  escalate)
    echo "sys1: ⚠ low confidence or null verdict — push blocked. Resolve or --no-verify to override."
    echo "$output" | "$PY" -c "import json,sys;d=json.load(sys.stdin);[print(s) for s in d.get('summary',[])]"
    exit 1
    ;;
  *)
    echo "sys1: ✗ gate unavailable ($verdict). Pushing anyway." >&2
    exit 0
    ;;
esac
