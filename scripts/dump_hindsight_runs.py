"""Dry-run labelled batches and dump the FULL step list per run, for the hindsight review experiment.

Companion to dump_labelled_runs.py, which keeps tool calls only (it feeds the deterministic detector).
Hindsight review has to see what every step saw and said, so this keeps agent messages, decisions,
tool inputs and outputs, reasoning and the scalar payload fields, in order.

Nothing is emitted (no ingest key, capture-only emitter) and the LLM runs offline, so this costs
nothing and writes nothing anywhere but the two output files.

⛔ THE LABELS GO IN A SEPARATE FILE ON PURPOSE. `runs.json` is what the reviewer is allowed to read;
`labels.json` holds the injected faults and is read only by the scorer. Putting both in one record is
how an experiment grades itself.

Usage:
  python3 scripts/dump_hindsight_runs.py <out_dir> [packs] [n_per_pack] [seed]
Defaults reproduce /tmp/labelled_runs.json: the 11 non-ServiceNow packs, 80 runs each, seed 7.
Argus#873.
"""
import json, os, sys
sys.path.insert(0, os.path.expanduser("~/Claude Projects/provy-sim"))
os.environ.pop("PROVY_KEY", None)
os.environ.pop("PROVY_EMIT", None)

from engine.emitter import ProvyEmitter
from engine.runner import BatchRunner
from engine.levers import LeverConfig
from engine.llm import LLM
from config.workflows import get_workflow
from scripts.dump_labelled_runs import load_pack

DEFAULT_PACKS = ["support", "claims", "claims_payout", "claude_code", "crm", "edwin",
                 "legal", "revops", "stripe_support", "teameight", "travel"]


def step_record(i, s):
    return {
        "step": i,
        "agent": s.agent,
        "step_type": s.step_type,
        "tool_name": s.tool_name,
        "tool_input": s.tool_input,
        "tool_output": s.tool_output,
        "outcome": s.outcome,
        "error": s.error,
        "reasoning": s.agent_reasoning,
        "fields": s.payload_extra or {},
    }


def main(out_dir, names, n, seed):
    os.makedirs(out_dir, exist_ok=True)
    runs, labels = [], {}
    for name in names:
        wf = get_workflow(name)
        pack = load_pack(name)
        em = ProvyEmitter(ingest_key="", base_url="http://localhost:0", capture=True)
        runner = BatchRunner(pack, LeverConfig(wf.lever_rates), emitter=em,
                             ledger=None, llm=LLM(offline=True), seed=seed)
        outs = runner.run_batch(n)
        agents = [a.name if hasattr(a, "name") else str(a) for a in (pack.agents() or [])]
        contract = [c.to_contract_json() for c in pack.contract()]
        failed_n = 0
        for o in outs:
            r = o.result
            key = f"{name}:{r.session_id}:{r.entity_id}"
            est_ok = bool(r.metadata.get("estimated_success", r.outcome_label == "success"))
            if r.outcome_label == "fail":
                failed_n += 1
            runs.append({
                "key": key,
                "workflow": name,
                "entity_id": r.entity_id,
                "agents": agents,
                "contract": contract,
                "settled_label": r.outcome_label,
                "claimed_success": est_ok,
                "estimated_signals": r.estimated_signals,
                "real_signals": r.real_signals,
                "steps": [step_record(i, s) for i, s in enumerate(r.traces)],
            })
            labels[key] = {
                "diverged": bool(r.diverged()),
                "settled_label": r.outcome_label,
                "faults": [{"lever": f.lever, "agent": f.agent, "dimension": f.dimension} for f in r.faults],
            }
        print(f"  {name}: {len(outs)} runs, {failed_n} failed", file=sys.stderr)
    json.dump(runs, open(os.path.join(out_dir, "runs.json"), "w"), default=str)
    json.dump(labels, open(os.path.join(out_dir, "labels.json"), "w"), default=str)
    print(f"\nwrote {len(runs)} runs to {out_dir}/runs.json and labels to {out_dir}/labels.json", file=sys.stderr)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/hindsight"
    packs = sys.argv[2].split(",") if len(sys.argv) > 2 else DEFAULT_PACKS
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 80
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    main(out, packs, n, seed)
