# Chief of Staff demo, on demand

`.github/workflows/run-cos-demo.yml` runs the Chief of Staff demo against the dev workspace, when you start it from the pre-prod console. It can only ever send to `https://dev.provy.ai`. That address is written in the workflow. No input, secret or variable can change it, and `cos/guard.py` refuses every other address (production, look-alikes, ports, user-info tricks) before anything is sent.

This repository is public. So the workflow masks every fleet key before it uses it, prints no key and no planted answer, and uploads its results only after encrypting them.

## Files

- `cos/guard.py`: the address check. Tests: `tests/test_cos_guard.py`.
- `cos/HARNESS_PIN`: the exact 40-character commit of the argus `reference-harness/` folder to run. It holds a placeholder now, so the workflow stops with a clear message until you write a real commit id. Move the pin only by committing a new id.
- `cos/build_keys_json.py`: builds the key secret on your laptop and copies it. It prints no key.
- Static test of the workflow: `tests/test_cos_workflow_static.py`.

## Secrets (names only)

Create a GitHub Environment named `cos-dev` in the provy-sim repository and put these secrets in it. All are dev only.

| Name | What it is |
|---|---|
| `COS_FLEET_KEYS_JSON` | One JSON map of fleet to ingest key, for the 11 fleets: cos, status, meetings, cross, sched, wms, kestrel, pledge, candidate, twin, sender |
| `ARGUS_HARNESS_READ` | A token that can read the contents of the argus repository, and nothing else |
| `COS_RESULTS_PASSPHRASE` | The passphrase that encrypts the results file |
| `ANTHROPIC_API_KEY` | Live mode with the paid model. A dev key, with a spend limit at the provider if possible |
| `GROQ_API_KEY` | Live mode with the free model |

Never put a production key, a certification key or a Servicely secret in this environment.

## How you set them

Run these on your laptop, in order. The first copies the key map without showing it.

```
cd ~/Claude\ Projects/provy-sim
python3 cos/build_keys_json.py
pbpaste | gh secret set COS_FLEET_KEYS_JSON --env cos-dev --repo amitgarg73/provy-sim
printf '' | pbcopy
```

Then the others (each command asks you to paste the value, so it never lands in your shell history):

```
gh secret set ARGUS_HARNESS_READ --env cos-dev --repo amitgarg73/provy-sim
gh secret set ANTHROPIC_API_KEY --env cos-dev --repo amitgarg73/provy-sim
gh secret set GROQ_API_KEY --env cos-dev --repo amitgarg73/provy-sim
openssl rand -base64 32 | tr -d '\n' | pbcopy
pbpaste | gh secret set COS_RESULTS_PASSPHRASE --env cos-dev --repo amitgarg73/provy-sim
```

If the `cos-dev` environment does not exist yet, create it first:

```
gh api -X PUT repos/amitgarg73/provy-sim/environments/cos-dev
```

The passphrase is on your clipboard after the `openssl` line: save it in your password manager before you copy anything else, because you need it to open a results file.

## Starting a run and what it prints

The console starts the workflow. The inputs are: scenario, runs, lever_set, levers_json, mode, model, budget_usd, seed, dry_run, outcome_path, label, no_cert_running. A dry run is the default: it prints the plan and the worst-case cost and loads no key.

The harness prints these lines, and the console reads them:

- `COS plan runs <N> worst_case_usd <X>`
- `COS progress runs <i> of <N> spend $<x> of $<cap>`
- `COS done runs <N> spend $<x> truth_rows <n> refusals <k>`
- `COS stopped reason=<why>`

Results are an encrypted file attached to the run (`cos-results.tgz.enc`). To open one: `openssl enc -d -aes-256-cbc -pbkdf2 -pass pass:<passphrase> -in cos-results.tgz.enc | tar -xz`.
