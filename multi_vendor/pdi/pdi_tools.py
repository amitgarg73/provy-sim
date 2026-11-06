#!/usr/bin/env python3
"""PDI side of the IAM POC: list, export or tear down everything the POC made. One file, three commands.

  ./scripts/with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD -- python3 -I docs/specs/servicely-iam-poc/servicenow/pdi_tools.py inventory
  ... pdi_tools.py export            writes servicenow/export/*.xml (record XML straight from the instance)
  ... pdi_tools.py teardown          DRY RUN: says what it would remove
  ... pdi_tools.py teardown --apply  removes ONLY objects named provy_iam / ProvyIam / u_provy_iam_ / provy.iam / provy.iam.* test people and the spike group

Never touches anything else on the instance (the existing 'Provy demo - verification sweep' job and every sample record are left alone).
⛔ THE EXPORT NEVER INCLUDES system properties: provy.iam.read_token and provy.iam.servicely.token hold secrets. Their NAMES are listed in export/README.md; a rebuild recreates them with setup_pdi_estate.py.
All of it is GLOBAL scope with a prefix, not a scoped application (checked 5 Nov 2026). The export is how a rebuild avoids rerunning scripts; a scoped app would need Studio.
"""
import base64, json, os, sys, urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
inst = os.environ['SERVICENOW_INSTANCE'].strip()
inst = (inst if inst.startswith('http') else 'https://' + inst).rstrip('/')
AUTH = 'Basic ' + base64.b64encode(f"{os.environ['SERVICENOW_USER']}:{os.environ['SERVICENOW_PASSWORD']}".encode()).decode()

def call(method, path, body=None, accept='application/json'):
    h = {'Authorization': AUTH, 'Accept': accept, 'Content-Type': 'application/json'}
    req = urllib.request.Request(inst + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=90) as r: return r.status, r.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:200]

def rows(table, query, fields='sys_id,name'):
    st, b = call('GET', f"/api/now/table/{table}?sysparm_limit=200&sysparm_fields={fields}&sysparm_query={urllib.parse.quote(query)}")
    try: return json.loads(b).get('result', [])
    except Exception: return []

# (table, query that matches ONLY what the POC made)
OURS = [
  ('sys_ws_operation', None),   # resolved through the definition below
  ('sys_ws_definition', 'service_id=provy_iam'),
  ('sys_script_include', 'nameSTARTSWITHProvyIam'),
  ('sysauto_script', 'nameSTARTSWITHprovy_iam'),
  ('sys_db_object', 'nameSTARTSWITHu_provy_iam'),
  ('sys_user_grmember', None),  # resolved through the test people
  ('sys_user', 'user_nameSTARTSWITHprovy.iam.'),
  ('sys_user_group', 'name=Provy IAM Spike Group'),
  ('sys_properties', 'nameSTARTSWITHprovy.iam'),
]
EXPORTABLE = [('sys_script_include', 'nameSTARTSWITHProvyIam'), ('sys_ws_definition', 'service_id=provy_iam'), ('sysauto_script', 'nameSTARTSWITHprovy_iam'), ('sys_db_object', 'nameSTARTSWITHu_provy_iam')]

def inventory():
    out = []
    ws = rows('sys_ws_definition', 'service_id=provy_iam')
    for w in ws: out += [('sys_ws_operation', r['sys_id'], r.get('name')) for r in rows('sys_ws_operation', f"web_service_definition={w['sys_id']}")]
    for t, q in OURS:
        if q: out += [(t, r['sys_id'], r.get('name') or r.get('user_name')) for r in rows(t, q, 'sys_id,name,user_name' if t == 'sys_user' else 'sys_id,name')]
    users = rows('sys_user', 'user_nameSTARTSWITHprovy.iam.', 'sys_id,user_name')
    for u in users: out += [('sys_user_grmember', r['sys_id'], u['user_name']) for r in rows('sys_user_grmember', f"user={u['sys_id']}", 'sys_id')]
    for t in ('u_provy_iam_access', 'u_provy_iam_hr_event'):
        st, b = call('GET', f"/api/now/stats/{t}?sysparm_count=true")
        try: out.append((t + ' (rows)', '-', json.loads(b)['result']['stats']['count']))
        except Exception: pass
    return out

cmd = sys.argv[1] if len(sys.argv) > 1 else 'inventory'
if cmd == 'inventory':
    for t, i, n in inventory(): print(f'{t:26} {i:34} {n}')
elif cmd == 'export':
    d = os.path.join(HERE, 'export'); os.makedirs(d, exist_ok=True)
    for t, q in EXPORTABLE:
        st, x = call('GET', f"/{t}.do?XML&sysparm_query={urllib.parse.quote(q)}", accept='text/xml')
        if st == 200 and x.startswith('<?xml'):
            open(os.path.join(d, f'{t}.xml'), 'w').write(x); print('exported', t, len(x), 'bytes')
        else: print('could not export', t, st)
    for w in rows('sys_ws_definition', 'service_id=provy_iam'):
        st, x = call('GET', f"/sys_ws_operation.do?XML&sysparm_query={urllib.parse.quote('web_service_definition=' + w['sys_id'])}", accept='text/xml')
        if st == 200 and x.startswith('<?xml'): open(os.path.join(d, 'sys_ws_operation.xml'), 'w').write(x); print('exported sys_ws_operation', len(x), 'bytes')
    names = [r['name'] for r in rows('sys_properties', 'nameSTARTSWITHprovy.iam')]
    open(os.path.join(d, 'README.md'), 'w').write("# PDI export (record XML)\n\nTaken straight from the instance. Global scope, prefix provy_iam. NOT included: system properties, because two of them hold secrets. Names to recreate with setup_pdi_estate.py:\n\n" + ''.join(f'- {n}\n' for n in sorted(names)) + "\nTo reload without the script: import each XML through System Definition, Load XML (or Studio), then run setup_pdi_estate.py to recreate the properties.\n")
    print('export README written (property names only)')
elif cmd == 'teardown':
    apply = '--apply' in sys.argv
    inv = [x for x in inventory() if '(rows)' not in x[0]]
    # children before parents: memberships, operations, then the rest; tables last
    order = ['sys_user_grmember', 'sys_ws_operation', 'sys_ws_definition', 'sysauto_script', 'sys_script_include', 'sys_user', 'sys_user_group', 'sys_properties', 'sys_db_object']
    for t in order:
        for tt, i, n in [x for x in inv if x[0] == t]:
            if not apply: print('would delete', tt, n); continue
            st, _ = call('DELETE', f'/api/now/table/{tt}/{i}'); print('deleted' if st in (200, 204) else f'FAILED http {st}', tt, n)
    if not apply: print('dry run: nothing deleted. Re-run with --apply.')
else:
    raise SystemExit('usage: pdi_tools.py inventory | export | teardown [--apply]')
