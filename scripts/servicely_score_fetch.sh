#!/usr/bin/env bash
# Reads Provy's attribution for the tickets of one send ledger (pre-prod, read-only) and prints the score table.
# Usage (from the repo root, through the secret door): scripts/with-secrets CERTIFY_DB_URL -- ./scripts/servicely_score_fetch.sh LEDGER.jsonl FLEET_ID
set -euo pipefail
LEDGER="$1"; FLEET="$2"
NUMS=$(python3 - "$LEDGER" <<'PY'
import json, sys
print(",".join("'%s'" % json.loads(l)["ticket_number"] for l in open(sys.argv[1]) if l.strip() and "ticket_number" in json.loads(l)))
PY
)
TMP=$(mktemp)
psql "$CERTIFY_DB_URL" -X -A -t -c "begin read only;" -c "select coalesce(json_agg(json_build_object('entity_id', a.entity_id, 'culprit_agent', a.culprit_agent, 'method', a.method)), '[]') from ag_attributions_effective a where a.workflow_id = '$FLEET' and a.entity_id in ($NUMS)" -c "rollback;" | grep -v '^BEGIN$\|^ROLLBACK$' > "$TMP"
python3 -m engine.servicely_score "$LEDGER" "$TMP"
rm -f "$TMP"
