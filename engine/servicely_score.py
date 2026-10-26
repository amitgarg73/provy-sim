"""Score Provy's attribution against the seeded faults of a send.

The answer key is in the scenario (ground_truth.seeded_fault), reached through the ledger's scenario_id, so the instance never holds it.
Per ticket, Provy's reading is one of: named the right step, named a wrong step, said undetermined, or has not read it yet.
A wrong name is the worst result and is counted apart from an honest "undetermined".

Input is the send ledger (JSON lines) and a JSON list of Provy's readings: [{"entity_id": "INC...", "culprit_agent": "...|null", "method": "..."}].
`python -m engine.servicely_score LEDGER READINGS` prints the table. Reading Provy's database is the caller's job (see scripts/servicely_score_fetch.sh).
"""
import json
import sys
from collections import defaultdict
from typing import Optional

from packs.servicely.scenarios import by_id


def _norm(s: Optional[str]) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def load_ledger(path: str) -> list[dict]:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    return [r for r in rows if "ticket_number" in r]


def reading_of(r: Optional[dict]) -> str:
    """named | undetermined | unread"""
    if r is None:
        return "unread"
    if r.get("culprit_agent") and r.get("method") != "undetermined":
        return "named"
    return "undetermined"


def score(tickets: list[dict], readings: list[dict]) -> dict:
    scn = by_id()
    by_entity = {r["entity_id"]: r for r in readings}
    out: dict = defaultdict(lambda: {"tickets": 0, "right": 0, "wrong": 0, "undetermined": 0, "unread": 0})
    for t in tickets:
        sf = (scn.get(t.get("scenario_id")) or {}).get("ground_truth", {}).get("seeded_fault")
        if not sf:
            continue
        b = out[sf["id"]]
        b["tickets"] += 1
        r = by_entity.get(t["ticket_number"])
        kind = reading_of(r)
        if kind == "unread":
            b["unread"] += 1
        elif kind == "undetermined":
            b["undetermined"] += 1
        elif sf["culprit"] and _norm(r["culprit_agent"]) == _norm(sf["culprit"]):
            b["right"] += 1
        else:
            b["wrong"] += 1   # includes naming an agent when the control says no step is at fault
    return dict(out)


def render(result: dict) -> str:
    lines = [f"{'fault':<16}{'tickets':>8}{'named right':>13}{'named wrong':>13}{'undetermined':>14}{'not read':>10}"]
    for fid, b in sorted(result.items()):
        lines.append(f"{fid:<16}{b['tickets']:>8}{b['right']:>13}{b['wrong']:>13}{b['undetermined']:>14}{b['unread']:>10}")
    return "\n".join(lines)


if __name__ == "__main__":
    led, rd = sys.argv[1], sys.argv[2]
    print(render(score(load_ledger(led), json.load(open(rd)))))
