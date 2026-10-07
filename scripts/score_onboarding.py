#!/usr/bin/env python3
"""Step 7 scorer: ground truth (never sent to Provy) against what Provy wrote, per door.

    python scripts/score_onboarding.py --truth RUN/sdk.jsonl --export EXPORT/sdk --label "SDK Co" --out OUT [--tag -as-emitted]

`--truth` is the fleet's ground-truth file. `--export` holds sessions.csv, evals.csv and steps.csv read from pre-prod by
SELECT (evidence/.../scripts/export_fleet.sh). Reuses the step 2 scorer's arithmetic (scripts/score_context_levers.score) so the
numbers mean the same thing as in step 2: caught, caught on another agent's step only, missed (the check ran and passed), not measured.

For the customer who missed context (`sent_mode` in the truth) it also splits by what was sent: none, partial, full.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.score_context_levers import CHECK_OF, CHECKS, check_name, pct, read_csv, score  # noqa: E402


def load(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def md(cols, rows):
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] + ["| " + " | ".join(str(x) for x in r) + " |" for r in rows])


def verdict_index(truth_ids, evals):
    v = defaultdict(lambda: defaultdict(list))
    for e in evals:
        c = check_name(e["eval_name"])
        if c and e["session_id"] in truth_ids:
            v[(e["session_id"], c)][e["agent"]].append(e["passed"] in ("t", "true", "True"))
    return v


def mode_table(truth, evals, steps):
    """Per sent_mode: what the checks wrote. A pass on a check that had nothing to read is the defect to look for."""
    ids = {r["session_id"] for r in truth}
    v = verdict_index(ids, evals)
    ctx_steps = defaultdict(int)
    dec_steps = defaultdict(int)
    for s in steps:
        if s["session_id"] in ids and s["step_type"] in ("agent_message", "decision") and s["agent"] in ("intake", "validator", "adjudicator", "reviewer", "mapper", "applier"):
            dec_steps[s["session_id"]] += 1
            ctx_steps[s["session_id"]] += s["has_context"] in ("t", "true", "True")
    rows = []
    for mode in ("none", "partial", "full"):
        sub = [r for r in truth if r.get("sent_mode") == mode]
        if not sub:
            continue
        n = len(sub)
        cell = {c: {"rows": 0, "pass_sessions": 0, "fail_sessions": 0} for c in CHECKS}
        for r in sub:
            for c in CHECKS:
                vv = v.get((r["session_id"], c), {})
                if vv:
                    cell[c]["rows"] += 1
                    allv = [x for xs in vv.values() for x in xs]
                    cell[c]["pass_sessions"] += all(allv)
                    cell[c]["fail_sessions"] += not all(allv)
        dsteps = sum(dec_steps[r["session_id"]] for r in sub)
        csteps = sum(ctx_steps[r["session_id"]] for r in sub)
        rows.append([mode, n, f"{csteps}/{dsteps}"] + [f"{cell[c]['rows']} rows ({cell[c]['pass_sessions']} all-pass, {cell[c]['fail_sessions']} with a fail)" for c in CHECKS])
    return rows


def missed_cells(truth, evals):
    """Per (fault kind, sent_mode): n planted, observable, caught / missed / not measured / caught on another agent only."""
    ids = {r["session_id"] for r in truth}
    v = verdict_index(ids, evals)
    cells = defaultdict(lambda: defaultdict(int))
    for r in truth:
        for f in r["faults"]:
            kind = f["fault"]
            if kind == "instruction" and not f.get("first_after_change"):
                continue
            check = CHECK_OF[kind]
            vv = v.get((r["session_id"], check), {})
            mine = vv.get(f["agent"], [])
            if mine and not all(mine):
                out = "caught"
            elif not mine and any(not all(x) for a, x in vv.items() if a != f["agent"]):
                out = "caught_wrong_step"
            elif mine:
                out = "missed_check_passed"
            else:
                out = "not_measured"
            c = cells[(kind, r["sent_mode"], bool(f.get("observable")))]
            c["n"] += 1
            c[out] += 1
    return cells


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", required=True)
    ap.add_argument("--export", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    truth = load(a.truth)
    # a work item id the pack drew twice makes two plan rows share one session id, so Provy holds ONE session for both: neither can be scored
    dup = {k for k, n in __import__("collections").Counter(r["session_id"] for r in truth).items() if n > 1}
    truth = [r for r in truth if r["session_id"] not in dup]
    evals = read_csv(os.path.join(a.export, "evals.csv"))
    sess = {r["session_id"] for r in read_csv(os.path.join(a.export, "sessions.csv"))}
    steps = read_csv(os.path.join(a.export, "steps.csv"))
    in_db = [r for r in truth if r["session_id"] in sess]
    res = score(in_db, evals)
    os.makedirs(a.out, exist_ok=True)
    stem = os.path.splitext(os.path.basename(a.truth))[0] + a.tag

    cols = ["fault type", "check", "n faulted", "caught", "caught on another agent's step only", "missed (check ran, passed)", "not measured", "caught of all", "caught when measured", "failed work items of n", "also failed another agent's row"]
    rows = []
    for kind in ("stale", "unapproved", "empty", "instruction"):
        c = res["cells"].get((kind, "all"))
        if not c:
            continue
        n, ca = c["n"], c["caught"]
        meas = ca + c["missed"] + c["caught_wrong_step"]
        rows.append([kind, CHECK_OF[kind], n, ca, c["caught_wrong_step"], c["missed"], c["not_measured"], pct(ca, n), pct(ca, meas), f"{c['failed_fail']}/{n}", c["spill"]])
    fa_cols = ["check", "population", "work items", "check measured them", "failing verdicts (false alarms)", "of all", "of measured"]
    fa_rows = []
    for (c, pop), v in sorted(res["false_alarms"].items()):
        fa_rows.append([c, pop, v["items"], v["measured"], v["failing"], pct(v["failing"], v["items"]), pct(v["failing"], v["measured"])])
    text = [f"## {a.label}: catch, miss and not measured, per fault type (n = work items that carried the fault)\n", md(cols, rows),
            f"\nInstruction changes after the first session on a new version (the check is not meant to fire again): {res['continuing']}",
            f"\n## {a.label}: false alarms per check (a failing verdict on a work item that carried no fault of that check's kind)\n", md(fa_cols, fa_rows),
            f"\nWork items scored: {len(truth)} (left out, same session id drawn twice by the pack: {len(dup)} ids); found in the pre-prod export: {len(in_db)}."]
    mrows = None
    if any("sent_mode" in r for r in truth):
        mrows = mode_table(in_db, evals, steps)
        mc = missed_cells(in_db, evals)
        mcols = ["fault", "sent", "observable", "n", "caught", "caught on another agent only", "missed (check passed)", "not measured"]
        mrr = [[k, m, "yes" if o else "no", c["n"], c["caught"], c["caught_wrong_step"], c["missed_check_passed"], c["not_measured"]] for (k, m, o), c in sorted(mc.items())]
        text += ["\n## What the checks wrote, by what the customer sent\n",
                 md(["sent", "sessions", "decision steps with a manifest", *CHECKS], mrows),
                 "\n## Faults by what was sent\n", md(mcols, mrr)]
        with open(os.path.join(a.out, f"{stem}-by-sent.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(mcols); w.writerows(mrr)
    with open(os.path.join(a.out, f"{stem}-catch.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)
    with open(os.path.join(a.out, f"{stem}-false-alarms.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(fa_cols); w.writerows(fa_rows)
    open(os.path.join(a.out, f"{stem}.md"), "w").write("\n".join(text) + "\n")
    print("\n".join(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
