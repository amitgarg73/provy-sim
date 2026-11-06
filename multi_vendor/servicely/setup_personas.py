#!/usr/bin/env python3
"""Servicely side of the IAM POC, part 2: the sub-agents and the two personas, created PAUSED. Idempotent.

Run:  ./scripts/with-secrets SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- python3 -I docs/specs/servicely-iam-poc/servicely/setup_personas.py [--apply]
Without --apply it only prints what it would create.

HARD LIMITS (Amit's rule, and the harness refuses them): this script never sets a role on a user, a persona or an agent, never sets AcknowledgeHighRisk, and never creates a user.
Every record is named 'Provy IAM POC: ...', every persona is Paused and every sub-agent is inactive, so nothing picks up work until a person switches it on.
Only existing tools are attached. The tool that queues an Action is NOT created here: how an Action's payload is built is unknown until the first real queue row is read (see QUEUE-API.md, 'What is not known').
"""
import json, os, sys, urllib.request, urllib.error
base = os.environ['SERVICELY_INSTANCE_URL'].strip().rstrip('/')
H = {'Authorization': 'Bearer ' + os.environ['SERVICELY_API_TOKEN'].strip(), 'Accept': 'application/json', 'Content-Type': 'application/json'}
APPLY = '--apply' in sys.argv

def call(m, p, b=None):
    req = urllib.request.Request(base + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode(); return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:300]

def rows(t, q=''):
    st, d = call('GET', f'/v1/{t}?limit=200{q}'); return d.get('data', []) if isinstance(d, dict) else []

def first(d):
    r = d.get('data', d) if isinstance(d, dict) else d
    return r[0] if isinstance(r, list) else r

FETCH_ANY = 'f46edcde91f011f09b58b627797e1155'       # (General) Fetch any record, observed 5 Nov 2026
LOOKUP_USER = '46db2c56527711f0adf8f2f67e1667af'     # (User Management) Lookup User by Name
MARKER = '[provy-iam-poc]'
SELECT = f"Route work here only when the short description of the request carries the marker {MARKER}. These are Provy IAM POC test requests. Never route real work here."

AGENTS = [
  ('Provy IAM POC: Intake', 'provy_iam_poc_intake', 'Reads a joiner, mover or leaver request.', [LOOKUP_USER, FETCH_ANY],
   'Read the request. Return the person, the kind of change (joiner, mover, leaver), the effective date and the systems named, as plain fields.'),
  ('Provy IAM POC: Entitlements', 'provy_iam_poc_entitlements', 'Works out which systems hold access for a person.', [FETCH_ANY],
   'For the person, list the systems that hold access. Return the list and nothing else.'),
  ('Provy IAM POC: Access agent', 'provy_iam_poc_access', 'Queues the access change.', [FETCH_ANY],
   'Apply the change to every system in the list you were given. Report exactly what you queued. (The tool that queues an Action is added after the first queue row is read.)'),
  ('Provy IAM POC: Reviewer', 'provy_iam_poc_reviewer', 'Checks what was queued against the request.', [FETCH_ANY],
   'Compare what was queued with the request. If they differ, say so. Do not claim the access is gone: you have not looked at the systems.'),
]
PERSONAS = [
  ('Provy IAM POC: Access orchestrator',
   'You handle joiner, mover and leaver requests. Delegate to Intake, then Entitlements, then the Access agent, then the Reviewer. Never close a request yourself before the Reviewer has answered. If any agent fails, hand the request to a person and say which one failed.',
   True),
  ('Provy IAM POC: Infrastructure agent',
   'You take a change raised by the Access orchestrator and queue it for the named systems. Report what you queued.', False),
]

ref = first(call('GET', '/v1/SystemAgentPersona?limit=1')[1])   # the Service Desk Agent's record type and escalation group are reused, never changed
st, d = call('GET', '/v1/SystemAgentPersona?limit=20')
sd = next(r for r in d['data'] if r.get('Name') == 'Service Desk Agent')
RECORD_TYPE, ESCALATE = sd['RecordType'], sd['EscalationTargetGroup']
have_agents = {r.get('Name'): r['id'] for r in rows('SystemAIAgent')}
have_personas = {r.get('Name'): r['id'] for r in rows('SystemAgentPersona')}

ids = {}
for name, key, desc, tools, prompt in AGENTS:
    if name in have_agents: ids[name] = have_agents[name]; print('exists agent', name); continue
    print('would create agent', name, '(inactive)')
    if APPLY:
        st, r = call('POST', '/v1/SystemAIAgent', {'Name': name, 'Key': key, 'Description': desc, 'Active': False, 'Tools': tools, 'Agents': [], 'SystemPrompt': prompt,
                                                   'SelectionPrompt': SELECT, 'ModelSelectionType': 'general'})
        print('  ->', st, 'created' if st < 300 else r)
        if st < 300: ids[name] = first(r)['id']
for name, prompt, orch in PERSONAS:
    if name in have_personas: print('exists persona', name); continue
    print('would create persona', name, '(Paused)')
    if APPLY:
        body = {'Name': name, 'Status': 'paused', 'RecordType': RECORD_TYPE, 'EscalationTargetGroup': ESCALATE, 'SystemPrompt': prompt, 'SelectionPrompt': SELECT,
                'Tools': [FETCH_ANY], 'Agents': [ids[a[0]] for a in AGENTS if a[0] in ids] if orch else [], 'MaxConcurrentTickets': 5, 'ModelSelectionType': 'complex',
                'OverflowBehaviour': 'queue', 'QueuePriorityMethod': 'fifo', 'Description': 'Provy IAM POC. Paused until a person switches it on.'}
        st, r = call('POST', '/v1/SystemAgentPersona', body)
        print('  ->', st, 'created' if st < 300 else r)
