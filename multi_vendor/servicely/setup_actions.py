#!/usr/bin/env python3
"""Servicely side of the IAM POC: our own queue and one Action per command. NOT RUN YET (the first spike was read-only). Idempotent.

Run:  ./scripts/with-secrets SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- python3 -I docs/specs/servicely-iam-poc/servicely/setup_actions.py [--apply]
Without --apply it only prints what it would create. Everything is named 'Provy IAM POC' and the Actions are created INACTIVE.
Creating an Action over the API was proven on 5 Nov 2026 (created and deleted). Creating the provider instance is NOT yet proven.
The queue name is the provider instance's connection string; it must equal the PDI property provy.iam.queue.
"""
import json, os, sys, urllib.request, urllib.error
base = os.environ['SERVICELY_INSTANCE_URL'].strip().rstrip('/')
H = {'Authorization': 'Bearer ' + os.environ['SERVICELY_API_TOKEN'].strip(), 'Accept': 'application/json', 'Content-Type': 'application/json'}
APPLY = '--apply' in sys.argv
QUEUE = 'provy-iam-poc'
COMMANDS = {  # subject -> what it does (the PDI worker handles each)
  'provy-iam-disable-account': 'Disable the directory account and remove its groups',
  'provy-iam-remove-groups': 'Remove group memberships',
  'provy-iam-revoke': 'Revoke access in the named systems',
  'provy-iam-apply-change': 'Apply an access change across the named systems',
  'provy-iam-grant': 'Grant time-limited access',
}

def call(m, p, b=None):
    req = urllib.request.Request(base + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode(); return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:200]

def rows(t, q=''):
    st, d = call('GET', f'/v1/{t}?limit=100{q}'); return d.get('data', []) if isinstance(d, dict) else []

inst = next((r for r in rows('ActionProviderInstance') if r.get('Name') == 'Provy IAM POC queue'), None)
if not inst:
    prov = rows('ActionProviderInstance')[0]['Provider']
    print('would create provider instance Provy IAM POC queue, type async_integration, queue', QUEUE)
    if APPLY:
        st, r = call('POST', '/v1/ActionProviderInstance', {'Name': 'Provy IAM POC queue', 'ConnectionType': 'async_integration', 'ConnectionString': QUEUE, 'Provider': prov, 'Active': True})
        print('  ->', st, (r if st >= 300 else 'created'))
        inst = next((r for r in rows('ActionProviderInstance') if r.get('Name') == 'Provy IAM POC queue'), None)
have = {r.get('Name') for r in rows('Action')}
for cmd, desc in COMMANDS.items():
    name = f'Provy IAM POC: {cmd}'
    if name in have: print('exists', name); continue
    print('would create action', name, '(inactive)')
    if APPLY and inst:
        st, r = call('POST', '/v1/Action', {'Name': name, 'Command': cmd, 'Table': 'ITSMRequest', 'ProviderInstance': inst['id'], 'SideEffects': True, 'Active': False,
                                            'ResultStatusKey': 'status', 'ResultTextKey': 'payload', 'Description': desc})
        print('  ->', st, (r if st >= 300 else 'created'))
