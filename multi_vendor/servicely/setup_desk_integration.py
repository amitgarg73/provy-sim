#!/usr/bin/env python3
"""Servicely side of the IAM POC, part 4: the service desk raises the access change. Idempotent. Dry run by default; --revert restores the desk's prompt.

THE FLOW: an employee or the HR feed raises a plain Request at the service desk. The Service Desk Agent (the existing front door, with its helpers) recognises it as an access
change, raises a SUBTASK under that Request for the Access orchestrator, and stops. The orchestrator works the subtask (Intake, Entitlements, Access agent, Reviewer), the
Access agent queues the Action, and the outside worker applies it. The subtask's real parent field (`ParentITSMRequest`) is the link Provy reads.

WHAT THIS CHANGES ON A LIVE WORKFLOW (so it is small, additive and reversible):
  1. one NEW tool, "Provy IAM POC: Raise access change", with formal parameters (without them a model cannot pass a value);
  2. that tool added to the Service Desk Agent's tools;
  3. ONE marked block added to the end of the desk's system prompt. Nothing else in the prompt is edited. The original prompt is saved in backup/ and `--revert` restores it.
The tool refuses anything that is not a `provy.iam.*` person, so it cannot raise access work for a real employee.

HARD LIMITS (Amit's rule): never sets a role, never sets the high-risk acknowledgement, never creates a user. The tool is HIGH RISK, so the desk persona needs its
acknowledgement ticked again by hand after this runs (editing a persona's tools clears it).

    ../argus/scripts/with-secrets SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- python3 setup_desk_integration.py [--apply | --revert]
"""
import json, os, sys, urllib.error, urllib.request

BASE = os.environ['SERVICELY_INSTANCE_URL'].strip().rstrip('/')
H = {'Authorization': 'Bearer ' + os.environ['SERVICELY_API_TOKEN'].strip(), 'Accept': 'application/json', 'Content-Type': 'application/json'}
APPLY, REVERT = '--apply' in sys.argv, '--revert' in sys.argv
HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP = os.path.join(HERE, 'backup', 'service_desk_persona_before_access_change.json')
DESK = 'Service Desk Agent'
TOOL_KEY, TOOL_NAME = 'tool_provy_iam_poc_raise_access_change', 'Provy IAM POC: Raise access change'
BEGIN, END = '<!-- provy-iam-poc access change: begin -->', '<!-- provy-iam-poc access change: end -->'

PROMPT_BLOCK = f'''{BEGIN}

## Access changes (do this INSTEAD of the process above)
If the ticket asks you to offboard, onboard or change the access of a named person (a leaver, a joiner or a mover: for example "disable the account", "remove access", "give access"), and the person is a user name that starts with provy.iam., it is an ACCESS CHANGE. For an access change:
1. Do NOT call the Triage, Routing or Knowledge helpers and do not draft a reply to the customer.
2. Call the tool "Provy IAM POC: Raise access change" exactly once, with requestNumber = this ticket's number (it looks like REQ0000123), person = the user name, kind = leaver, joiner or mover, and systems = the systems the ticket names (leave it out if none are named).
3. Write one work journal note: "ACCESS CHANGE: raised <the subtask number the tool returned> for <person>". Then use the wait transition tool to move this ticket to the waiting state, and stop. The Access team's agent works the subtask from here.
Any other ticket follows the process above, unchanged.
{END}'''

TOOL_SCRIPT = r'''// Raises an access change for the Access orchestrator as a SUBTASK of the ticket the service desk is working. Parameters: requestNumber, person, kind, systems.
// The subtask's ParentITSMRequest is the real link back to the original ticket. Refuses anyone who is not a provy.iam.* person, so it cannot raise access work for a real employee.
let number = String(parameters.requestNumber || "").trim();
let person = String(parameters.person || "").trim();
let kind = String(parameters.kind || "leaver").trim().toLowerCase();
let systems = String(parameters.systems || "").trim();
let parent = number ? Table("ITSMRequest").EQUAL("Number", number).firstOrNull() : null;
let persona = Table("SystemAgentPersona").EQUAL("Name", "Provy IAM POC: Access orchestrator").firstOrNull();
let agent = persona ? Table("User").EQUAL("AgentPersona", persona.ID()).firstOrNull() : null;
if (!parent) {
    answer = { success: false, error: "No request found for " + number };
} else if (person.indexOf("provy.iam.") !== 0) {
    answer = { success: false, error: "Only provy.iam.* people may be used with this tool" };
} else if (["leaver", "joiner", "mover"].indexOf(kind) < 0) {
    answer = { success: false, error: "kind must be leaver, joiner or mover" };
} else if (!agent) {
    answer = { success: false, error: "The Access orchestrator has no agent user" };
} else {
    let today = new Date().toISOString().substring(0, 10);
    let body = "Kind: " + kind + "\nPerson: " + person + "\nEffective: " + today + "\nSystems: " + (systems || "directory, sso, vpn, saas_token") + "\nRaised from: " + number;
    let sub = Table("ITSMRequestTask").newRecord()
        .ParentITSMRequest(parent.ID())
        .ShortDescription("[provy-iam-poc] " + kind.charAt(0).toUpperCase() + kind.slice(1) + ": access change for " + person)
        .Description(body)
        .Assignee(agent.ID())
        .create();
    answer = { success: true, subtask: String(sub.Number()), parent: number, person: person, kind: kind };
}'''

PARAMETERS = [
    {'Name': 'requestNumber', 'Type': 'string', 'Required': True, 'Order': 1, 'Active': True, 'Description': "This ticket's number, for example REQ0000123. The subtask is raised under it."},
    {'Name': 'person', 'Type': 'string', 'Required': True, 'Order': 2, 'Active': True, 'Description': 'The user name of the person whose access changes. Must start with provy.iam.'},
    {'Name': 'kind', 'Type': 'string', 'Required': False, 'Order': 3, 'Active': True, 'Description': 'leaver, joiner or mover. Defaults to leaver.'},
    {'Name': 'systems', 'Type': 'string', 'Required': False, 'Order': 4, 'Active': True, 'Description': 'Comma separated systems the ticket names, for example directory, sso, vpn, saas_token. Leave out when none are named.'},
]
TOOL = {'Name': TOOL_NAME, 'Key': TOOL_KEY, 'Active': True, 'HighRisk': True, 'MutatesTicket': True, 'ProductionRestricted': False, 'SelectAutomatically': True,
        'ModelSelectionType': 'general', 'ExecutionScript': TOOL_SCRIPT,
        'Description': 'Raises an access change (leaver, joiner or mover) for the Access orchestrator as a subtask of the current ticket.',
        'SelectionPrompt': ('Use this when a ticket asks to offboard, onboard or change the access of a named provy.iam.* person. It raises a subtask for the Access team\'s agent; it does not '
                            'change any access itself. Params: requestNumber (this ticket), person, kind, systems (optional).')}


def call(m, p, b=None):
    req = urllib.request.Request(BASE + p, data=json.dumps(b).encode() if b is not None else None, headers=H, method=m)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode(); return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def rows(t):
    st, d = call('GET', f'/v1/{t}?page_size=200'); return d.get('data', []) if isinstance(d, dict) else []


def main():
    desk = next((p for p in rows('SystemAgentPersona') if p['Name'] == DESK), None)
    if not desk:
        sys.exit(f'persona {DESK!r} not found')
    prompt = desk.get('SystemPrompt') or ''
    print('REVERTING' if REVERT else ('APPLYING' if APPLY else 'DRY RUN (add --apply)'), 'on', BASE.split('//')[1].split('.')[0])
    if REVERT:
        orig = json.load(open(BACKUP))
        print('would restore the desk prompt and tools from', os.path.basename(BACKUP))
        if APPLY or '--apply' in sys.argv:
            print('  ->', call('PATCH', f'/v1/SystemAgentPersona/{desk["id"]}', {'SystemPrompt': orig['SystemPrompt'], 'Tools': orig['Tools']})[0])
        return
    # 1. the tool and its parameters
    have = next((t for t in rows('SystemAITool') if t.get('Key') == TOOL_KEY), None); tid = have['id'] if have else None
    print('tool', 'exists (script refreshed)' if have else 'would be created')
    if APPLY:
        if have: print('  ->', call('PATCH', f'/v1/SystemAITool/{tid}', {k: TOOL[k] for k in ('ExecutionScript', 'SelectionPrompt', 'Description', 'HighRisk', 'MutatesTicket')})[0])
        else:
            st, r = call('POST', '/v1/SystemAITool', TOOL); print('  ->', st)
            tid = next((t['id'] for t in rows('SystemAITool') if t.get('Key') == TOOL_KEY), None)
    have_p = {r['Name'] for r in rows('SystemAIToolParameter') if r.get('Parent') == tid} if tid else set()
    for prm in PARAMETERS:
        print('parameter', prm['Name'], 'exists' if prm['Name'] in have_p else 'would be created')
        if APPLY and tid and prm['Name'] not in have_p: print('  ->', call('POST', '/v1/SystemAIToolParameter', {**prm, 'Parent': tid})[0])
    # 2. the tool on the desk
    tools = list(desk.get('Tools') or [])
    print('tool on the desk:', 'yes' if tid and tid in tools else 'would be attached')
    if APPLY and tid and tid not in tools: print('  ->', call('PATCH', f'/v1/SystemAgentPersona/{desk["id"]}', {'Tools': tools + [tid]})[0])
    # 3. the marked prompt block
    if BEGIN in prompt:
        base_prompt = prompt[:prompt.index(BEGIN)].rstrip()
    else:
        base_prompt = prompt.rstrip()
    new_prompt = base_prompt + '\n\n' + PROMPT_BLOCK
    print('prompt block:', 'current' if new_prompt.strip() == prompt.strip() else 'would be added or refreshed (nothing above it is edited)')
    if APPLY and new_prompt.strip() != prompt.strip(): print('  ->', call('PATCH', f'/v1/SystemAgentPersona/{desk["id"]}', {'SystemPrompt': new_prompt})[0])
    if APPLY:
        d2 = next(p for p in rows('SystemAgentPersona') if p['Name'] == DESK)
        print('desk acknowledgement still recorded:', bool(d2.get('HighRiskAcknowledgedScope')), '(if False, tick it by hand)')


main()
