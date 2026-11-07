#!/usr/bin/env python3
"""Lists the IAM POC test tickets on the Servicely instance: Requests and subtasks whose short description carries [provy-iam-poc] or names a provy.iam.* person.
Dry run only: it deletes nothing. Run with --apply to delete subtasks first, then their parent requests.

    ../../argus/scripts/with-secrets SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- python3 cleanup_tickets.py [--apply]
"""
import json, os, sys, urllib.error, urllib.request
BASE = os.environ['SERVICELY_INSTANCE_URL'].strip().rstrip('/')
H = {'Authorization': 'Bearer ' + os.environ['SERVICELY_API_TOKEN'].strip(), 'Accept': 'application/json', 'Content-Type': 'application/json'}
APPLY = '--apply' in sys.argv


def call(m, p):
    req = urllib.request.Request(BASE + p, headers=H, method=m)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode(); return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def rows(t):
    out, page = [], 1
    while True:
        st, d = call('GET', f'/v1/{t}?page_size=200&page={page}')
        data = d.get('data', []) if isinstance(d, dict) else []
        out += data
        if len(data) < 200: return out
        page += 1


def ours(r):
    s = ' '.join(str(r.get(k) or '') for k in ('ShortDescription', 'Description'))
    return '[provy-iam-poc]' in s or 'provy.iam.' in s


reqs = [r for r in rows('ITSMRequest') if ours(r)]
tasks = [t for t in rows('ITSMRequestTask') if ours(t) or t.get('ParentITSMRequest') in {r['id'] for r in reqs}]
print('DRY RUN' if not APPLY else 'APPLYING', 'on', BASE.split('//')[1].split('.')[0])
print('requests:', len(reqs), [r.get('Number') for r in reqs])
print('subtasks:', len(tasks))
if APPLY:
    for t in tasks: print('task', t.get('Number'), call('DELETE', f'/v1/ITSMRequestTask/{t["id"]}')[0])
    for r in reqs: print('request', r.get('Number'), call('DELETE', f'/v1/ITSMRequest/{r["id"]}')[0])
