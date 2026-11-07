#!/usr/bin/env bash
# Run the prompt-optimization loop unattended: one fresh, non-interactive Claude Code session per iteration.
# ledger.txt and runs/ carry the memory between iterations, so each session starts with a small context.
#
# Usage:  ./run_loop.sh [ITERATIONS]          (default 5)
# Optional environment variables:
#   MODEL=<model>       model for the agent's Claude Code session (passed as --model)
#   EDIT_RULE=<rule>    permission rule for edits (default "Edit(prompt.txt)"; use "Edit" if the agent can't edit)
#
# Prerequisites: classes created in the labeler, `python INITIALIZE.py` run, documents labeled, `claude` on PATH.
set -euo pipefail
cd "$(dirname "$0")"   # always operate on this folder's prompt.txt, ledger and git repo

ITERATIONS="${1:-5}"
EDIT_RULE="${EDIT_RULE:-Edit(prompt.txt)}"
LEDGER="ledger.txt"

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ "$ITERATIONS" =~ ^[1-9][0-9]*$ ]] || fail "ITERATIONS must be a positive integer (got '$ITERATIONS')"
command -v claude >/dev/null || fail "the 'claude' CLI is not on PATH"
[[ -f prompt.txt ]] || fail "prompt.txt does not exist yet. Create classes with 'python labeler.py', then run 'python INITIALIZE.py'."

ITERATION_PROMPT='Read program.md and follow its rules. Do exactly ONE iteration of its loop:
1. Review ledger.txt (all lines) and the runs/ detail file named in its newest line.
2. Form one hypothesis about the mistakes and edit prompt.txt to address it.
3. Run `python evaluate.py` exactly once; it keeps or reverts prompt.txt by itself.
4. Stop, and report in 2-3 sentences what you changed and the result.'

ledger_lines() { [[ -f "$LEDGER" ]] && wc -l < "$LEDGER" | tr -d ' ' || echo 0; }

# Baseline: the loop needs a scored starting point and errors to learn from.
if [[ "$(ledger_lines)" -eq 0 ]]; then
  echo "=== Baseline: scoring the starting prompt ==="
  python evaluate.py || fail "baseline evaluation failed (see the message above)"
fi

missed=0
for ((i = 1; i <= ITERATIONS; i++)); do
  echo
  echo "=== Iteration $i of $ITERATIONS ==="
  before="$(ledger_lines)"
  claude -p "$ITERATION_PROMPT" \
    --permission-mode acceptEdits \
    --allowedTools "Read" "$EDIT_RULE" "Bash(python evaluate.py)" \
    ${MODEL:+--model "$MODEL"} \
    || fail "the claude session exited with an error; stopping"

  if [[ "$(ledger_lines)" -eq "$before" ]]; then
    echo "WARNING: this iteration did not record an evaluate.py run in $LEDGER."
    missed=$((missed + 1))
    [[ $missed -ge 2 ]] && fail "two iterations in a row did not run evaluate.py; stopping"
    continue
  fi
  missed=0

  # An empty prompt diff means the agent ran the evaluator without changing prompt.txt.
  if python -c 'import json,sys; e=json.loads(open(sys.argv[1]).readlines()[-1]); sys.exit(0 if e.get("prompt_diff_vs_best") == "" else 1)' "$LEDGER"; then
    echo "WARNING: prompt.txt was not changed in this iteration. If the agent reported it could not edit,"
    echo "         rerun with EDIT_RULE=Edit ./run_loop.sh $ITERATIONS"
  fi
done

echo
echo "=== Done: $ITERATIONS iteration(s) ==="
python - "$LEDGER" <<'EOF'
import json, sys
entries = [json.loads(l) for l in open(sys.argv[1]) if l.strip().startswith("{")]
current = [e for e in entries if e["fingerprint"] == entries[-1]["fingerprint"]]
kept = [e for e in current if e["decision"] == "kept"]
best = max(kept, key=lambda e: e["holdout"]["macro_f1"]) if kept else None
print(f"Runs with the current data/config: {len(current)} ({len(kept)} kept)")
if best:
    print(f"Best held-out macro-F1: {best['holdout']['macro_f1']:.4f} (run {best['run_id']}); prompt.txt in git holds that prompt.")
EOF
