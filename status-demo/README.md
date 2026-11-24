# Status tracking and escalation demo, on demand

`.github/workflows/run-status-demo.yml` fires the simple status demo against the dev workspace, when you start it from the pre-prod console (Dev group, "Status demo"). Like the Chief of Staff demo it can only send to `https://dev.provy.ai` (`cos/guard.py` proves it on every run), masks the key before using it, prints no per-session planted answer, and encrypts its results before upload.

What it runs is `reference-harness/status_demo.py` from the private argus repo, one folder, at the commit named in `status-demo/HARNESS_PIN`. It holds a placeholder until the argus branch `feat/simple-status-demo` is merged: write the commit that holds `reference-harness/status_demo/` and commit it.

## Secrets, in the existing GitHub Environment `cos-dev`

| Name | What it is |
|---|---|
| `STATUS_DEMO_KEY` | The ingest key of the "Simulated demo: Status tracking and escalation" fleet on dev. It is in `~/Claude Projects/provy-sim-state-status-demo/tenant.json` (mode 600) |
| `ARGUS_HARNESS_DEPLOY_KEY` | The private half of a read-only deploy key on the argus repo (the public half is on that repo as "provy-sim status-demo (read only)"). The workflow fetches the harness with it |
| `COS_RESULTS_PASSPHRASE` | Encrypts the results before upload. The passphrase is saved in `~/Claude Projects/provy-sim-state-status-demo/results-passphrase.txt` (mode 600) |

```
python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/Claude Projects/provy-sim-state-status-demo/tenant.json')))['ingest_key'],end='')" | pbcopy
pbpaste | gh secret set STATUS_DEMO_KEY --env cos-dev --repo amitgarg73/provy-sim
printf '' | pbcopy
```

## Reading the results

A real run uploads `status-results-<run id>`: `ground-truth.jsonl`, `runs.jsonl` and `ground-truth.html`, encrypted. To read them:

```
gh run download <run id> --repo amitgarg73/provy-sim -n status-results-<run id>
openssl enc -d -aes-256-cbc -pbkdf2 -in status-results.tgz.enc -out r.tgz -pass pass:<the COS_RESULTS_PASSPHRASE>   # then: tar -xzf r.tgz
```
