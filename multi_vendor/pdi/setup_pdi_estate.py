#!/usr/bin/env python3
"""Servicely IAM POC, PDI side. Creates (or updates) everything the customer's estate and queue worker need. Idempotent: safe to rerun, and the way to rebuild after a PDI is lost.

Run:  ./scripts/with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD -- python3 -I docs/specs/servicely-iam-poc/servicenow/setup_pdi_estate.py [--token-file PATH]

Makes (all prefixed provy_iam / provy.iam / u_provy_iam_, and everything it makes is simulated):
  tables        u_provy_iam_access (one row per person and simulated system), u_provy_iam_hr_event (HR feed events)
  script incs   ProvyIamEstate (the estate and its read), ProvyIamWorker (polls the Servicely queue), and the agents behind provy.iam.agentic:
                ProvyIamPlan (allow-list and verdict, pure), ProvyIamModel (provider-neutral model client, stub by default), ProvyIamTrace (reports to Provy), ProvyIamAgents (applier and verifier)
  REST          Provy IAM Readings (POC): GET /readings (what Provy pulls), POST /apply and POST /seed (test doors), POST /agent-apply (the agentic path, 409 while the flag is off),
                POST /plan-check (runs the shared test vectors against the deployed ProvyIamPlan), bearer token only
  jobs          provy_iam_worker (POC), provy_iam_hr_sync (POC). Created INACTIVE; a rerun never changes the active flag. Switch them on by hand when the Servicely properties are set.
  properties    provy.iam.read_token (private, generated once), provy.iam.fault, provy.iam.queue, provy.iam.servicely.url, provy.iam.servicely.token (private, EMPTY)

Prints names and results, never a credential. The Servicely token is NOT set here: it is a hand step (see ../servicely/PERSONAS.md).
"""
import base64, json, os, secrets, sys, urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
inst = os.environ['SERVICENOW_INSTANCE'].strip()
inst = (inst if inst.startswith('http') else 'https://' + inst).rstrip('/')
AUTH = 'Basic ' + base64.b64encode(f"{os.environ['SERVICENOW_USER']}:{os.environ['SERVICENOW_PASSWORD']}".encode()).decode()

def src(name):
    with open(os.path.join(HERE, 'src', name)) as f: return f.read()

def call(method, path, body=None, headers=None):
    h = {'Authorization': AUTH, 'Accept': 'application/json', 'Content-Type': 'application/json'}
    h.update(headers or {})
    req = urllib.request.Request(inst + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

def find(table, query, fields='sys_id'):
    st, j = call('GET', f"/api/now/table/{table}?sysparm_limit=1&sysparm_fields={fields}&sysparm_query={urllib.parse.quote(query)}")
    r = (j or {}).get('result') if isinstance(j, dict) else None
    return r[0] if r else None

def ensure(table, key_query, body, update=True):
    row = find(table, key_query)
    if row:
        if update: call('PATCH', f"/api/now/table/{table}/{row['sys_id']}", body)
        return row['sys_id'], 'updated' if update else 'kept'
    st, j = call('POST', f"/api/now/table/{table}", body)
    if st not in (200, 201):
        raise SystemExit(f'{table} create failed: http {st} {j}')
    return j['result']['sys_id'], 'created'

token_file = sys.argv[sys.argv.index('--token-file') + 1] if '--token-file' in sys.argv else None

# ---- properties
existing = find('sys_properties', 'name=provy.iam.read_token', 'sys_id,value')
tok = (existing or {}).get('value') or secrets.token_urlsafe(24)
ensure('sys_properties', 'name=provy.iam.read_token', {'name': 'provy.iam.read_token', 'value': tok, 'type': 'string', 'is_private': 'true', 'description': 'Bearer token for the Provy IAM REST doors (POC)'}, update=False)
for name, val, private, desc in [
    ('provy.iam.fault', '{}', 'false', 'POC fault injection, JSON such as {"vpn":"accept_not_apply"}: the revoke answers ok and changes nothing'),
    ('provy.iam.queue', '', 'false', 'Servicely queue name the worker polls (the provider instance connection string)'),
    ('provy.iam.servicely.url', '', 'false', 'Servicely base URL for the worker'),
    ('provy.iam.servicely.token', '', 'true', 'Servicely System API token for the worker. Set by hand, never in a script'),
    # the agents (all OFF or STUB by default; nothing here calls out until switched on by hand)
    ('provy.iam.agentic', 'false', 'false', 'true runs each queued task through the agents (plan, validate, apply, verify, trace). false is the plain worker, unchanged'),
    ('provy.iam.model.mode', 'stub', 'false', 'stub (offline, deterministic) or live. Live needs url, key and name below'),
    ('provy.iam.model.url', '', 'false', 'chat-completions URL of an OpenAI-compatible provider (live only)'),
    ('provy.iam.model.key', '', 'true', 'model provider key. Set by hand, never in a script'),
    ('provy.iam.model.name', '', 'false', 'model name (live only)'),
    ('provy.iam.trace.url', '', 'false', 'Provy base URL for the agents traces, no path'),
    ('provy.iam.trace.key', '', 'true', 'Provy ingest key of the Access provisioning workflow. Set by hand, never in a script'),
    ('provy.iam.trace.mode', '', 'false', 'live | log | off. Unset: live when url and key are set. log writes what would be sent to the system log'),
]:
    ensure('sys_properties', f'name={name}', {'name': name, 'value': val, 'type': 'string', 'is_private': private, 'description': desc}, update=False)
print('properties: ok')
if token_file:
    with open(token_file, 'w') as f: f.write(tok)
    os.chmod(token_file, 0o600)

# ---- tables and columns
TABLES = {
  'u_provy_iam_access': ('Provy IAM Access (simulated)', [
      ('u_person', 'string', 100), ('u_system', 'string', 40), ('u_kind', 'string', 40), ('u_state', 'string', 20),
      ('u_level', 'string', 40), ('u_expires', 'glide_date_time', None), ('u_simulated', 'boolean', None), ('u_note', 'string', 200)]),
  'u_provy_iam_hr_event': ('Provy IAM HR Event (simulated)', [
      ('u_person', 'string', 100), ('u_action', 'string', 40), ('u_due', 'glide_date_time', None), ('u_done', 'boolean', None), ('u_note', 'string', 200)]),
}
for tname, (label, cols) in TABLES.items():
    tid, how = ensure('sys_db_object', f'name={tname}', {'name': tname, 'label': label, 'ws_access': 'true', 'is_extendable': 'false'})
    print(f'table {tname}:', how)
    for col, typ, ln in cols:
        body = {'name': tname, 'element': col, 'column_label': col[2:].replace('_', ' ').title(), 'internal_type': typ, 'active': 'true'}
        if ln: body['max_length'] = str(ln)
        if col == 'u_state': body['default_value'] = 'active'
        if col in ('u_simulated',): body['default_value'] = 'true'
        _, h2 = ensure('sys_dictionary', f'name={tname}^element={col}', body, update=False)
        if h2 == 'created': print(f'   column {col}')

# ---- script includes
for name in ('ProvyIamEstate', 'ProvyIamPlan', 'ProvyIamModel', 'ProvyIamTrace', 'ProvyIamAgents', 'ProvyIamWorker'):
    ensure('sys_script_include', f'name={name}', {'name': name, 'api_name': f'global.{name}', 'script': src(f'{name}.js'), 'active': 'true', 'access': 'package_private' if False else 'public', 'client_callable': 'false',
                                                   'description': 'Servicely IAM POC (simulated estate)'})
    print('script include', name, 'ok')

# ---- scripted REST api and its resources
ws_id, how = ensure('sys_ws_definition', 'service_id=provy_iam', {'name': 'Provy IAM Readings (POC)', 'service_id': 'provy_iam', 'active': 'true', 'requires_authentication': 'false', 'enforce_acl': 'false'})
print('scripted REST api:', how)
for opname, method, path, fn in [('readings', 'GET', '/readings', 'rest_readings.js'), ('apply', 'POST', '/apply', 'rest_apply.js'), ('seed', 'POST', '/seed', 'rest_seed.js'),
                                 ('agent_apply', 'POST', '/agent-apply', 'rest_agent_apply.js'), ('plan_check', 'POST', '/plan-check', 'rest_plan_check.js')]:
    oid, how = ensure('sys_ws_operation', f'web_service_definition={ws_id}^name={opname}', {'web_service_definition': ws_id, 'name': opname, 'http_method': method, 'relative_path': path,
        'operation_script': src(fn), 'active': 'true', 'requires_authentication': 'false', 'enforce_acl': 'false'})
    st, j = call('GET', f"/api/now/table/sys_ws_operation/{oid}?sysparm_fields=operation_uri")
    print(f'   {method} {path}:', how, ((j or {}).get('result') or {}).get('operation_uri') if isinstance(j, dict) else j)

# ---- scheduled jobs. Created INACTIVE; a rerun updates the script and the period but NEVER the active flag, so a job that is switched on stays on.
def ensure_job(name, period, script_file):
    row = find('sysauto_script', f'name={name}')
    body = {'name': name, 'run_type': 'periodically', 'run_period': period, 'script': src(script_file)}
    if row:
        call('PATCH', f"/api/now/table/sysauto_script/{row['sys_id']}", body)
        return 'updated (active flag untouched)'
    call('POST', '/api/now/table/sysauto_script', {**body, 'active': 'false'})
    return 'created (inactive)'

print('job provy_iam_hr_sync (POC):', ensure_job('provy_iam_hr_sync (POC)', '1970-01-01 00:05:00', 'hr_sync.js'))
print('job provy_iam_worker (POC):', ensure_job('provy_iam_worker (POC)', '1970-01-01 00:01:00', 'worker_job.js'))
