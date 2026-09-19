#!/usr/bin/env bash
# Measure what Provy actually DOES with each evidence lever, one lever at a time.
#
# ⛔ WHY THIS EXISTS. `docs/failure-research/01-mapping.md` has a column headed "Provy today". Every
# cell in it is a claim somebody reasoned out, and the file says so: "What Provy actually DOES with
# each one is a claim in the table above and is unmeasured." This script replaces those claims with
# counts.
#
# ⛔ IT RUNS ONE LEVER AT A TIME, AT RATE 1.0. Phase-A levers are exclusive, so in a mix the first to
# fire claims the run and the others starve. A mix cannot answer "what does Provy do with THIS".
#
# ⛔ AND IT SCORES A WINDOW, NOT A FLEET. Each block records its own start time and scores only rows
# created after it, so one lever's result never absorbs the previous lever's.
#
# ⛔ n IS SIZED AGAINST MIN_GROUP (6), NOT AGAINST PATIENCE. A 10-run sample can only ever return
# base_rate_verdict=insufficient, which answers nothing about confidence. That mistake was made on
# the first run of the night.
# ⛔ NO `set -u` HERE. provy.config contains entries that reference other variables, so sourcing it
# under `-u` aborts the script on the first one and the failure reads as "unbound variable
# CERTIFY_DB_URL", which points at the wrong thing entirely. Cost: one dead overnight launch.
set -o pipefail

cd "$(dirname "$0")/.." || exit 1
OUT="${1:-docs/failure-research/lever-measurements.md}"
N="${LEVER_N:-20}"

# ⛔ `. <(grep ...)` SILENTLY LOADS NOTHING UNDER THIS MACHINE'S BASH (3.2.57, arm64). It works in
# zsh, which is why the documented one-liner looks fine when pasted into a terminal and then loads
# an empty string inside a script. It fails with no error and no exit code: the variables are simply
# absent. Use eval, which is proven here, and check the result rather than trusting it.
set -a; eval "$(grep -E '^(CERTIFY_[A-Z_]+|GROQ_API_KEY)=' "$HOME/Claude Projects/provy.config")"; set +a
export PROVY_DB_URL="${CERTIFY_DB_URL:-}"
if [ -z "$PROVY_DB_URL" ]; then
  echo "⛔ CERTIFY_DB_URL did not load from provy.config; nothing would be scored. Stopping." >&2
  exit 1
fi
export PROVY_EMIT=1
. "${SIMKEYS:-/private/tmp/claude-501/-Users-amitgarg/d946831e-7d33-4cac-b0b7-0ccb5386afa3/scratchpad/simkeys2.env}"

# lever|pack|tenant|workflow
FLEET_EDWIN="f4e78321-17a3-440f-8c67-4c3c7bc62487|4839de37-6e2c-4782-9ade-40c48be2a7d7"
FLEET_T8="98aa14ca-73ce-48f6-bd09-af7c8e1064f4|dd9d7acc-2e61-4de5-8805-52b6e1ed9a4c"
FLEET_CC="341ccb19-e5ba-4eda-b178-3dbf25664d33|f49fcb26-6689-4bee-aa5e-6aa544040803"

RUNS="
ok_but_empty|teameight|$FLEET_T8
retry_loop|teameight|$FLEET_T8
agent_paralysis|teameight|$FLEET_T8
context_truncated|teameight|$FLEET_T8
parametric_override|teameight|$FLEET_T8
escalation_refused|teameight|$FLEET_T8
fabricated_policy|teameight|$FLEET_T8
overliteral_constraint|claude_code|$FLEET_CC
reversed_on_appeal|edwin|$FLEET_EDWIN
"

{
  echo "# What Provy does with each evidence lever"
  echo
  echo "Measured $(date -u +%Y-%m-%dT%H:%M:%SZ) against PRE-PROD, one lever at a time at rate 1.0,"
  echo "n=$N per lever, each scored over its own window."
  echo
  echo "⛔ Read the confidence column, not the method. A low-confidence candidate that the base-rate"
  echo "guard already qualified is not an accusation. Counting the two together is the defect Provy"
  echo "sells against."
  echo
} > "$OUT"

for spec in $RUNS; do
  [ -z "$spec" ] && continue
  lever="${spec%%|*}"; rest="${spec#*|}"
  pack="${rest%%|*}";  rest="${rest#*|}"
  tenant="${rest%%|*}"; workflow="${rest#*|}"

  since="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
  echo "[$(date -u +%H:%M:%S)] $lever on $pack (n=$N)" >&2

  .venv/bin/python scripts/run_batch.py --pack "$pack" --count "$N" --seed 91 \
      --levers "{\"$lever\":{\"rate\":1.0}}" --reconcile --show 0 >/tmp/lever_$lever.log 2>&1
  rc=$?

  PROVY_TENANT_ID="$tenant" PROVY_WORKFLOW_ID="$workflow" PROVY_SCORE_SINCE="$since" \
  .venv/bin/python - "$lever" "$pack" "$N" "$rc" >> "$OUT" 2>&1 <<'PY'
import json, sys
from engine.scoreboard import ProvyQuery
lever, pack, n, rc = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
q = ProvyQuery()
print(f"## `{lever}` on `{pack}` (n={n})\n")
if rc != "0":
    print(f"⚠ the batch exited {rc}; see /tmp/lever_{lever}.log. Scored anyway, treat as partial.\n")
if not q.available:
    print(f"⛔ detected side unavailable: {q.error}\n"); raise SystemExit(0)
mix = q.attribution_mix() or {}
print("| measure | value |")
print("|---|---|")
print(f"| contract met-rate (Provy) | {q.contract_met_rate()} |")
print(f"| divergence rate, by work item | {q.reconciled_divergence_rate()} |")
print(f"| incidents opened | {q.incident_count()} |")
print(f"| **cause named, high/medium confidence** | **{mix.get('named_with_confidence')}** |")
print(f"| cause offered, low confidence only | {mix.get('named_low_only')} |")
print(f"| refused to name a cause | {mix.get('refused')} |")
print()
if mix.get("by_method"):
    print("method / confidence breakdown:\n")
    print("| method / confidence | n | base-rate verdict |")
    print("|---|---|---|")
    for k, d in sorted(mix["by_method"].items()):
        print(f"| `{k}` | {d['n']} | {d['base_rate_verdict'] or 'none recorded'} |")
    print()
silent = q.silent_checks()
if silent:
    print(f"⛔ {len(silent)} enabled check(s) on this fleet have never produced a result: "
          + ", ".join(f"L{c['layer']} `{c['eval_name']}`" for c in silent) + "\n")
PY
done

echo "[$(date -u +%H:%M:%S)] done -> $OUT" >&2
