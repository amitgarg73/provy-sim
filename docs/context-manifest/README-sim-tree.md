# Context-manifest simulation: a drop-in tree for provy-sim

Lane L4 of the context manifest (#1505), 2 Oct 2026. This directory mirrors the paths of `/Users/amitgarg/Claude Projects/provy-sim` so the lead can copy it across. The simulator is a separate repository and this lane could not touch it, so everything is here, in the argus repo.

**Every file in this tree is new. Nothing replaces a file that provy-sim already has.** The copy cannot overwrite your work, and removing it is a delete of the files below.

## Copy it across

From the argus repo root, with provy-sim clean:

    rsync -a --exclude '__pycache__' --exclude 'README.md' --exclude 'PROVENANCE.md' \
        docs/evidence/context-manifest/sim/ "/Users/amitgarg/Claude Projects/provy-sim/"
    mkdir -p "/Users/amitgarg/Claude Projects/provy-sim/docs/context-manifest"
    cp docs/evidence/context-manifest/sim/PROVENANCE.md "/Users/amitgarg/Claude Projects/provy-sim/docs/context-manifest/"

Then, inside provy-sim, run the tests (they need pytest; the lane used a throwaway venv because the system Python has none) and commit the ground truth BEFORE any emission:

    python3 -m pytest tests/test_context_*.py
    git add data/groundtruth_context_* data/capture_seed_rows_CAP-A.jsonl && git commit -m "context manifest ground truth, hashes recorded"

The tests also run from the argus repo, without copying: `tests/_context_path.py` puts provy-sim on the path read-only and extends its `engine`, `config` and `packs` packages with this tree. Set `PROVY_SIM_ROOT` if provy-sim is elsewhere.

## What each file adds

| File | Adds | Purpose |
|---|---|---|
| `engine/context.py` | new | The source catalogue, the manifest builder, the route capability map, the fault and decoy planter, the plan generator, the hash function, expected coverage |
| `engine/context_truth.py` | new | Writes a ground-truth file, records its SHA-256, refuses to run on a file that does not match |
| `engine/context_emit.py` | new | One emitter per route (REST, SDK-shaped REST, OTLP native, OTLP OpenInference plus GenAI, log line, log field map), replaying a plan. Subclasses `ProvyEmitter`, so its production refusal and environment probe apply, and adds an allow-list of pre-prod hosts |
| `engine/context_score.py` | new | The scorer and the hold-out ledger |
| `config/context_sets.py` | new | Set specs, sizes, decoy counts, the declared limit and approved list, fleet labels, key variable names |
| `packs/context_support/__init__.py`, `pack.py` | new | The request-desk roster and the one-condition contract, for onboarding. Not registered in `packs/__init__.py` |
| `scripts/gen_context_set.py` | new | Generate sets and record hashes |
| `scripts/emit_context_set.py` | new | Replay a committed set against pre-prod (dry run unless `PROVY_EMIT=1`) |
| `scripts/score_context.py` | new | Score a set from an export |
| `scripts/merge_exports.py` | new | Merge the four per-fleet exports of a set into one |
| `scripts/capture_seed_rows.py` | new | Writes the rows L6's seed script needs for the capture fleet |
| `data/groundtruth_context_{CM-A,CM-B,CM-C,CM-D,CAP-A,CAP-B}.jsonl` | new | The ground truth. One line per session |
| `data/groundtruth_context_hashes.json` | new | The recorded SHA-256 of each file, with sizes and counts |
| `data/capture_seed_rows_CAP-A.jsonl` | new | 153 rows: session, span, and the manifest to seed |
| `tests/_context_path.py`, `context_fake_server.py`, `test_context_*.py` | new | 135 tests. The fake server is an independent reading of SPEC 2 |
| `PROVENANCE.md` | new, goes to `docs/context-manifest/` | Which sets are clean hold-outs and every departure from the spec |

## What this tree deliberately does not touch

| provy-sim file | What the spec's L4 row said | What was done instead, and why |
|---|---|---|
| `engine/levers.py` | gains four levers | No lever was added. The lever engine breaks a run at random from a stream while the run is being generated. The spec also asks for ground truth committed with a hash before any emission, which needs a deterministic plan fixed in advance. The planter in `engine/context.py` does what four levers would, and its output is the file the hash covers |
| `engine/emitter.py` | an OTLP attribute emitter and a log-line emitter | Not edited. `engine/context_emit.py` subclasses `ProvyEmitter` instead |
| `config/workflows.py` | per-fleet lever rates | Not edited. The rates are counts in `config/context_sets.py`, because a plan fixes the number of faults exactly |
| `packs/__init__.py` | a capture pack variant | Not edited. The pack exists and is not registered. To register it, add the import and one PACKS entry |

## Where things are checked

`SIM-RUNBOOK.md` (in `docs/audits/context-manifest/`) says how to run a set against pre-prod once L1 to L3 are deployed there. `BUILD-L4.md` (same folder) says what was built, what was measured, and what could not be verified. This lane could not run anything against pre-prod, and did not.
