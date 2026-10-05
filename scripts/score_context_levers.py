#!/usr/bin/env python3
"""Score the four existing context checks against the ground truth the simulator wrote.

    python3 scripts/score_context_levers.py --truth DIR --export DIR --out DIR

`--truth` holds `<fleet>.jsonl` (written by run_context_fleet.py, never sent to Provy) and the fleet's
`<fleet>.ledger.jsonl`. `--export` holds sessions.csv, evals.csv and steps.csv, read from the pre-prod
database by a SELECT. The scorer reads the truth to decide what was injected and the export to decide
what Provy said, and it never uses one to fill in the other.

Per fault type, over the work items that carried it:
  caught             a failing verdict of the right check, on the faulted agent's step
  caught_wrong_step  a failing verdict of the right check, but only on another agent
  missed             the check ran on that agent in that session and passed
  not_measured       the check wrote no row for that agent in that session (unknown, never a pass)

Per check, false alarms: failing verdicts of that check on work items that carried no fault of that check's kind,
counted over the work items the check measured and over all of them.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict

CHECK_OF = {"stale": "context_freshness", "unapproved": "source_not_approved",
            "empty": "retrieval_empty", "instruction": "instructions_changed"}
CHECKS = ["context_freshness", "source_not_approved", "retrieval_empty", "instructions_changed"]


def check_name(eval_name: str) -> str | None:
    if eval_name.startswith("context freshness") or eval_name == "context_freshness":
        return "context_freshness"
    if eval_name.startswith("context approved sources") or eval_name == "source_not_approved":
        return "source_not_approved"
    if eval_name in ("retrieval_empty", "instructions_changed"):
        return eval_name
    return None


def load_truth(truth_dir: str) -> list[dict]:
    rows, seen = [], set()
    for fn in sorted(os.listdir(truth_dir)):
        if fn.endswith(".jsonl") and not fn.endswith(".ledger.jsonl"):
            for line in open(os.path.join(truth_dir, fn)):
                r = json.loads(line)
                rows.append(r)
                seen.add(r["session_id"])
    # Sessions an interrupted first attempt sent before the real run: the ledger holds what the simulator
    # knows of them (no context fault was planted yet, they were in the warmup), and nothing else does.
    for fn in sorted(os.listdir(truth_dir)):
        if fn.endswith(".ledger.jsonl"):
            fleet = fn[:-len(".ledger.jsonl")]
            if fleet.startswith("smoke-"):
                continue
            role = "control" if fleet.endswith("control") else "fault"
            for line in open(os.path.join(truth_dir, fn)):
                r = json.loads(line)
                if r["session_id"] in seen:
                    continue
                ctx_faults = [f for f in r["faults"] if f["lever"].startswith("ctx_")]
                assert not ctx_faults, "a legacy session with a context fault: rebuild its truth instead"
                bg = next((f["params"]["cause"] for f in r["faults"] if f["lever"] == "background_failure"), None)
                failed = r["outcome_label"] == "fail"
                rows.append({"fleet": fleet, "role": role, "pack": r["workflow"], "entity_id": r["entity_id"],
                             "session_id": r["session_id"], "date": r["ts"][:10], "occurred_at": r["ts"], "faults": [],
                             "outcome": r["outcome_label"], "failure_cause": ("background:" + bg) if failed and bg else None,
                             "steps": [], "legacy": True})
                seen.add(r["session_id"])
    return rows


def read_csv(path: str) -> list[dict]:
    return list(csv.DictReader(open(path)))


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "n/a"


def score(truth: list[dict], evals: list[dict]) -> dict:
    by_session = {r["session_id"]: r for r in truth}
    # (session, check) -> {agent: [passed...]}
    verdicts: dict = defaultdict(lambda: defaultdict(list))
    for e in evals:
        c = check_name(e["eval_name"])
        if c and e["session_id"] in by_session:
            verdicts[(e["session_id"], c)][e["agent"]].append(e["passed"] in ("t", "true", "True"))

    cells = defaultdict(lambda: defaultdict(int))
    continuing = defaultdict(int)
    for r in truth:
        for f in r["faults"]:
            kind = f["fault"]
            if kind == "missing":
                # No manifest on that step: the honest answer from every check is silence for that agent.
                said = {c: verdicts.get((r["session_id"], c), {}).get(f["agent"], []) for c in CHECKS}
                for scope in ("all", r["pack"]):
                    cell = cells[("missing", scope)]
                    cell["n"] += 1
                    cell["silent_for_agent"] += not any(said.values())
                    cell["a_pass_was_written_for_the_missing_step"] += any(any(v) for v in said.values())
                    cell["a_failure_was_written_for_the_missing_step"] += any(not all(v) and v for v in said.values())
                continue
            if kind == "instruction" and not f.get("first_after_change"):
                continuing["n"] += 1
                v = verdicts.get((r["session_id"], "instructions_changed"), {})
                continuing["failing_again"] += any(not all(x) for x in v.values())
                continue
            check = CHECK_OF[kind]
            v = verdicts.get((r["session_id"], check), {})
            mine = v.get(f["agent"], [])
            if mine and not all(mine):
                outcome = "caught"
            elif any(not all(x) for a, x in v.items() if a != f["agent"]) and not mine:
                outcome = "caught_wrong_step"
            elif mine:
                outcome = "missed"
            else:
                outcome = "not_measured"
            spill = any(not all(x) for a, x in v.items() if a != f["agent"])
            for scope in ("all", r["pack"]):
                cell = cells[(kind, scope)]
                cell["n"] += 1
                cell[outcome] += 1
                cell["spill"] += bool(spill and outcome == "caught")
                cell[f"failed_{r['outcome']}"] += 1

    # false alarms: a failing verdict of a check on a work item with no fault of that check's kind
    fa = defaultdict(lambda: defaultdict(int))
    for r in truth:
        kinds = {f["fault"] for f in r["faults"]}
        pop = "control fleets" if r["role"] == "control" else "fault fleets, no fault on the item"
        if r["role"] != "control" and r["faults"]:
            pop = "fault fleets, item carried some other fault"
        for c in CHECKS:
            own = {k for k, cn in CHECK_OF.items() if cn == c}
            if kinds & own:
                continue
            if c == "instructions_changed" and any(f["fault"] == "instruction" for f in r["faults"]):
                continue
            v = verdicts.get((r["session_id"], c), {})
            cell = fa[(c, pop)]
            cell["items"] += 1
            cell["measured"] += bool(v)
            cell["failing"] += any(not all(x) for x in v.values())
    return {"cells": cells, "false_alarms": fa, "continuing": dict(continuing)}


def outcome_table(truth: list[dict]) -> list[list]:
    rows = []
    for role in ("fault", "control"):
        for pack in ("iam", "claims"):
            sub = [r for r in truth if r["role"] == role and r["pack"] == pack]
            faulted = [r for r in sub if r["faults"]]
            unf = [r for r in sub if not r["faults"]]
            rows.append([f"{pack}-{role}", len(sub), len(faulted), sum(r["outcome"] == "fail" for r in faulted),
                         len(unf), sum(r["outcome"] == "fail" for r in unf)])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", required=True)
    ap.add_argument("--export", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    truth = load_truth(a.truth)
    evals = read_csv(os.path.join(a.export, "evals.csv"))
    sess = {r["session_id"] for r in read_csv(os.path.join(a.export, "sessions.csv"))}
    in_truth = [r for r in truth if r["session_id"] in sess]
    missing_from_db = len(truth) - len(in_truth)
    res = score(in_truth, evals)
    os.makedirs(a.out, exist_ok=True)

    cols = ["fault_type", "check", "fleet_shape", "n_faulted", "caught", "caught_wrong_step", "missed_check_ran",
            "not_measured", "catch_rate_of_all", "catch_rate_when_measured", "failed_of_n", "also_failed_on_another_agent"]
    out = []
    for kind in ("stale", "unapproved", "empty", "instruction"):
        for scope in ("all", "iam", "claims"):
            c = res["cells"].get((kind, scope))
            if not c:
                continue
            n, ca = c["n"], c["caught"]
            meas = ca + c["missed"] + c["caught_wrong_step"]
            out.append([kind, CHECK_OF[kind], scope, n, ca, c["caught_wrong_step"], c["missed"], c["not_measured"],
                        pct(ca, n), pct(ca, meas), c["failed_fail"], c["spill"]])
    miss_rows = []
    for scope in ("all", "iam", "claims"):
        c = res["cells"].get(("missing", scope))
        if c:
            miss_rows.append(["missing", "(coverage gap, none of the four checks)", scope, c["n"], "", "", "", "", "",
                              "", "", f"silent {c['silent_for_agent']} of {c['n']}; pass written {c['a_pass_was_written_for_the_missing_step']}; failure written {c['a_failure_was_written_for_the_missing_step']}"])
    fa_cols = ["check", "population", "work_items", "check_measured_them", "failing_verdicts", "false_alarm_rate_of_all", "false_alarm_rate_when_measured"]
    fa_rows = []
    for (c, pop), v in sorted(res["false_alarms"].items()):
        fa_rows.append([c, pop, v["items"], v["measured"], v["failing"], pct(v["failing"], v["items"]), pct(v["failing"], v["measured"])])
    oc_cols = ["fleet", "work_items", "faulted_items", "faulted_that_failed", "unfaulted_items", "unfaulted_that_failed"]
    oc_rows = outcome_table(in_truth)

    with open(os.path.join(a.out, f"catch-by-fault{a.tag}.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(out + miss_rows)
    with open(os.path.join(a.out, f"false-alarms{a.tag}.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(fa_cols); w.writerows(fa_rows)
    with open(os.path.join(a.out, f"outcomes{a.tag}.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(oc_cols); w.writerows(oc_rows)

    def md(cols, rows):
        return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows])
    text = ["## Catch, miss and not measured, per fault type\n", md(cols, out + miss_rows),
            f"\nInstruction changes after the first session on a new version (not an event the check should repeat): {res['continuing']}",
            "\n## False alarms per check\n", md(fa_cols, fa_rows),
            "\n## Outcomes the simulator produced\n", md(oc_cols, oc_rows),
            f"\nWork items in the truth files: {len(truth)}; found in the pre-prod export: {len(in_truth)}; truth rows with no session in the database: {missing_from_db}."]
    open(os.path.join(a.out, f"step2-tables{a.tag}.md"), "w").write("\n".join(text) + "\n")
    print("\n".join(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
