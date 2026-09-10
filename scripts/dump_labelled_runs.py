"""Dry-run labelled batches and dump {tool calls, true culprit} per diverged run.

Nothing is emitted: no ingest key, so ProvyEmitter.enabled is False and every payload is only
captured in memory. The LLM runs offline. Output feeds argus/harness/score-attribution.ts.
"""
import json, os, sys, random
sys.path.insert(0, os.path.expanduser("~/Claude Projects/provy-sim"))
os.environ.pop("PROVY_KEY", None)
os.environ.pop("PROVY_EMIT", None)

from engine.emitter import ProvyEmitter
from engine.runner import BatchRunner
from engine.levers import LeverConfig
from engine.llm import LLM
from config.workflows import get_workflow
from packs import __init__ as _p  # noqa

def load_pack(name):
    from engine.pack import BasePack
    mod = __import__(f"packs.{name}.pack", fromlist=["*"])
    cands = [getattr(mod, a) for a in dir(mod)
             if isinstance(getattr(mod, a), type)
             and issubclass(getattr(mod, a), BasePack)
             and getattr(mod, a).__module__ == mod.__name__
             and "generate_work_item" in getattr(mod, a).__dict__]
    if not cands:
        cands = [getattr(mod, a) for a in dir(mod)
                 if isinstance(getattr(mod, a), type) and issubclass(getattr(mod, a), BasePack)
                 and getattr(mod, a).__module__ == mod.__name__]
    for c in cands:
        try:
            return c()
        except Exception:
            continue
    raise SystemExit(f"no usable pack class in packs.{name}.pack")

def main(names, n, seed):
    out = []
    for name in names:
        wf = get_workflow(name)
        pack = load_pack(name)
        em = ProvyEmitter(ingest_key="", base_url="http://localhost:0", capture=True)
        runner = BatchRunner(pack, LeverConfig(wf.lever_rates), emitter=em,
                             ledger=None, llm=LLM(offline=True), seed=seed)
        outs = runner.run_batch(n)
        for o in outs:
            r = o.result
            faults = [{"lever": f.lever, "agent": f.agent, "dimension": f.dimension} for f in r.faults]
            calls = []
            for s in r.traces:
                if s.step_type != "tool_call" or not s.tool_name:
                    continue
                calls.append({
                    "agent": s.agent, "tool_name": s.tool_name, "outcome": s.outcome,
                    "error": s.error, "latency_ms": s.latency_ms,
                    "created_at": None,
                    "payload": {"tool_output": s.tool_output, "entity_id": s.entity_id},
                })
            out.append({
                "diverged": bool(r.diverged()),
                "workflow": name, "session_id": r.session_id, "entity_id": r.entity_id,
                "predicted_label": "success" if r.metadata.get("estimated_success") else "fail",
                "actual_label": r.outcome_label,
                "real_signals": r.real_signals, "estimated_signals": r.estimated_signals,
                "true_faults": faults,
                "agents": [a if isinstance(a, str) else getattr(a, "name", str(a)) for a in (pack.agents() or [])],
                "tool_calls": calls,
            })
        print(f"  {name}: {len(outs)} runs, {sum(1 for o in outs if o.result.diverged())} diverged", file=sys.stderr)
    json.dump(out, open("/tmp/labelled_runs.json", "w"), default=str)
    print(f"\nwrote {len(out)} diverged runs to /tmp/labelled_runs.json", file=sys.stderr)

if __name__ == "__main__":
    packs = sys.argv[1].split(",") if len(sys.argv) > 1 else ["support"]
    main(packs, int(sys.argv[2]) if len(sys.argv) > 2 else 60, int(sys.argv[3]) if len(sys.argv) > 3 else 7)
