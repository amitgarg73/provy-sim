#!/usr/bin/env python3
"""Servicely side of the IAM POC, part 3: the tool that queues an Action, and the parameter scripts that build the queue payload. Idempotent.

WHY THIS EXISTS: a Servicely agent cannot call an outside system; it queues an Action, and an outside worker (the PDI) takes the task from the queue.
Nothing can queue an Action except a script, so this creates ONE tool, "Provy IAM POC: Queue access change", whose script calls
`Action.queueSubmit(actionId, request)` (read from the instance's own Action library on 6 Nov 2026).

THE PAYLOAD IS THE ACTION'S PARAMETER SCRIPT. Action.queueSubmit evaluates the Action's ParameterScript with the Request as `current` and queues the
object it returns. Until this script ran those scripts were empty, so a task would have reached the worker with no person in it. The parameter script
reads the person (provy.iam.*) and an optional "systems: a, b" line from the Request's short description and description. No systems named means a
leaver across all four.

HARD LIMITS (the harness refuses these, and they are Amit's hand steps): this script never sets a role, never sets the high-risk acknowledgement and
never creates a user. The tool is marked HIGH RISK on purpose: it changes access in another system. Attaching it to a persona therefore needs that
persona's acknowledgement, and editing a persona's tools clears it, so run this BEFORE the hand steps in PERSONAS.md.

    ../argus/scripts/with-secrets SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- python3 setup_queue_tool.py          (dry run)
    ../argus/scripts/with-secrets SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- python3 setup_queue_tool.py --apply
"""
import json, os, sys, urllib.error, urllib.request

BASE = os.environ['SERVICELY_INSTANCE_URL'].strip().rstrip('/')
H = {'Authorization': 'Bearer ' + os.environ['SERVICELY_API_TOKEN'].strip(), 'Accept': 'application/json', 'Content-Type': 'application/json'}
APPLY = '--apply' in sys.argv
MARKER = 'Provy IAM POC'
TOOL_KEY = 'tool_provy_iam_poc_queue_access_change'
TOOL_NAME = 'Provy IAM POC: Queue access change'
COMMANDS = ['provy-iam-disable-account', 'provy-iam-remove-groups', 'provy-iam-revoke', 'provy-iam-apply-change', 'provy-iam-grant']
ATTACH_TO = [('SystemAIAgent', 'Provy IAM POC: Access agent'), ('SystemAgentPersona', 'Provy IAM POC: Infrastructure agent')]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=H, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def rows(table, **eq):
    q = '&'.join(f'{k}={v}' for k, v in eq.items())
    st, d = call('GET', f'/v1/{table}?page_size=100' + (f'&{q}' if q else ''))
    return d.get('data', []) if isinstance(d, dict) else []


PARAMETER_SCRIPT = r'''// Builds the queue payload for the Provy IAM POC worker from the work item (a Request or a subtask) this action runs on. Plain text in, plain fields out.
// item: the work item's id. correlation: the root request number (see below). parent_session: the id of THIS run's top-level conversation, in the id format an observer uses for that run's session, so the outside
// system's own session can name it as its parent. Left null when the conversation cannot be found: the outside system then sends no parent, never a guess.
let text = String(current.ShortDescription() || "") + " " + String(current.Description() || "");
let who = /(provy\.iam\.[A-Za-z0-9_.\-]+)/.exec(text);
let named = /systems?\s*:\s*([a-z_, ]+)/i.exec(text);
let systems = named ? named[1].split(",").map(function (s) { return s.trim(); }).filter(function (s) { return s; }) : ["directory", "sso", "vpn", "saas_token"];
// correlation: the REQUEST that started the whole flow (a subtask's parent request, otherwise this item itself). The same number is on every hop, carried by this script and not by a model.
let correlation = String(current.Number());
try {
    let parentId = current.ParentITSMRequest.value();
    let parent = parentId ? Table("ITSMRequest").get(parentId) : null;
    if (parent) correlation = String(parent.Number());
} catch (e) { /* a Request has no parent field: it is its own root */ }
let conversationId = null;
Table("SystemAgentConversationHistory").EQUAL("RelatedRecord", current.ID() + ":" + current.getTableName()).forEach(function (c) {
    if (!c.ParentConversation.value()) conversationId = String(c.ID());
});
answer = { person: who ? who[1] : null, systems: systems, item: String(current.ID()), item_number: String(current.Number()), correlation: correlation, parent_session: conversationId ? "pull-servicely-" + conversationId : null };'''

TOOL_SCRIPT = r'''// Queues one Provy IAM POC Action on a Provy IAM POC work item, so the outside worker can apply it. Parameters: command, requestNumber (optional).
// A work item is a Request (REQ...) or a subtask of one (REQT...): the service desk raises the access change as a subtask under the original Request.
// A model often leaves the number out, so it is optional: with none, the work item is the ONE open [provy-iam-poc] item assigned to the calling agent user.
// Zero or several is refused, never guessed. Refuses anything that is not marked [provy-iam-poc] or not one of this POC's own Actions.
const TABLES = ["ITSMRequest", "ITSMRequestTask"];
let command = String(parameters.command || "");
let number = String(parameters.requestNumber || "").trim();
let item = null;
let refusal = null;
// a number the model gives is only a hint: it is used when it names a marked item, otherwise ignored (a model often passes the PARENT ticket's number)
if (number) {
    for (let i = 0; i < TABLES.length && !item; i++) item = Table(TABLES[i]).EQUAL("Number", number).firstOrNull();
    if (item && String(item.ShortDescription()).indexOf("[provy-iam-poc]") !== 0) item = null;
}
if (!item) {
    let mine = [];
    for (let i = 0; i < TABLES.length; i++) {
        Table(TABLES[i]).EQUAL("Assignee", user.getID()).limit(50).forEach(function (r) {
            if (String(r.ShortDescription()).indexOf("[provy-iam-poc]") === 0 && String(r.WorkflowStatus()) === "work_in_progress") mine.push(r);
        });
    }
    if (mine.length === 1) item = mine[0];
    else refusal = mine.length === 0 ? "No [provy-iam-poc] item is assigned to you, and the number given (" + number + ") is not one" : mine.length + " [provy-iam-poc] items are open, so it cannot tell which one: pass requestNumber";
}
let action = command ? Table("Action").EQUAL("Command", command).EQUAL("Active", true).firstOrNull() : null;
if (refusal) {
    answer = { success: false, error: refusal };
} else if (String(item.ShortDescription()).indexOf("[provy-iam-poc]") !== 0) {
    answer = { success: false, error: "Only [provy-iam-poc] requests and subtasks may be used with this tool" };
} else if (!action || String(action.Name()).indexOf("Provy IAM POC") !== 0) {
    answer = { success: false, error: "No active Provy IAM POC action has the command " + command };
} else {
    let resultId = Action.queueSubmit(action.ID(), item, {});
    answer = { success: true, queued: command, item: String(item.Number()), actionResultId: resultId };
}'''

TOOL = {
    'Name': TOOL_NAME, 'Key': TOOL_KEY, 'Active': True, 'HighRisk': True, 'MutatesTicket': False, 'ProductionRestricted': False,
    'SelectAutomatically': True, 'ModelSelectionType': 'general', 'ExecutionScript': TOOL_SCRIPT,
    'Description': 'Queues a Provy IAM POC Action (disable account, revoke, apply change, grant) on a POC Request, for the outside worker to apply.',
    'SelectionPrompt': ('Use this to make a change in the identity estate: it queues one of the POC actions for the outside worker. Params: `command` (one of '
                        + ', '.join(COMMANDS) + '), `requestNumber` (optional: the REQ number of the [provy-iam-poc] request being worked; leave it out and the tool finds the one assigned to you). It only queues the change; '
                        'it does not look at the estate, so never report the access as removed from its answer.'),
}


PARAMETERS = [
    {'Name': 'command', 'Type': 'string', 'Required': True, 'Order': 1, 'Active': True,
     'Description': 'Which POC action to queue. Use provy-iam-apply-change: it covers every system in one call. Others: ' + ', '.join(c for c in COMMANDS if c != 'provy-iam-apply-change') + '.'},
    {'Name': 'requestNumber', 'Type': 'string', 'Required': False, 'Order': 2, 'Active': True,
     'Description': 'The REQ number of the [provy-iam-poc] request being worked, for example REQ0000008. Optional: left out, the tool uses the one open request assigned to you.'},
]


AGENT_PROMPTS = {
    'Provy IAM POC: Entitlements': ("You report which systems a leaver, mover or joiner change has to reach. The work item names them on a line that starts with 'Systems:'. Return exactly those systems as a list and "
        "nothing else. Do NOT search any table, user record, group or access database: this platform holds no access data, and the systems themselves are checked elsewhere. If none are named, say so and stop."),
    'Provy IAM POC: Access agent': ("You queue the access change for the systems you were given. Call the tool 'Provy IAM POC: Queue access change' exactly ONCE, with command = provy-iam-apply-change "
        "(it covers every system in one go; do not use any other command). Do NOT pass requestNumber: leave it out, the tool finds the work item assigned to you. "
        "Report exactly what the tool answered. You have only queued a change: you cannot see the systems, so never claim the access is gone."),
}


def main():
    print(('APPLYING' if APPLY else 'DRY RUN (add --apply to change anything)'), 'on', BASE.split('//')[1].split('.')[0])
    # 1. the tool
    have = [r for r in rows('SystemAITool') if r.get('Key') == TOOL_KEY]
    tool_id = have[0]['id'] if have else None
    if have:
        print('tool exists:', tool_id, '(script refreshed)')
        if APPLY:
            st, r = call('PATCH', f'/v1/SystemAITool/{tool_id}', {k: TOOL[k] for k in ('ExecutionScript', 'SelectionPrompt', 'Description', 'HighRisk', 'MutatesTicket')}); print('  ->', st)
    else:
        print('would create tool', TOOL_NAME, '(high risk)')
        if APPLY:
            st, r = call('POST', '/v1/SystemAITool', TOOL); print('  ->', st, r if st >= 300 else 'created')
            tool_id = (r or {}).get('id') if isinstance(r, dict) else None
            if not tool_id:
                tool_id = ([x for x in rows('SystemAITool') if x.get('Key') == TOOL_KEY] or [{}])[0].get('id')
    # 1b. the tool's formal parameters. WITHOUT THESE A MODEL CANNOT PASS ANY VALUE: the selection prompt's text is not enough, and the agent calls the tool with nothing.
    have_p = {r.get('Name') for r in rows('SystemAIToolParameter') if r.get('Parent') == tool_id} if tool_id else set()
    for prm in PARAMETERS:
        if prm['Name'] in have_p:
            print('parameter exists:', prm['Name']); continue
        print('would create parameter', prm['Name'])
        if APPLY and tool_id:
            st, r = call('POST', '/v1/SystemAIToolParameter', {**prm, 'Parent': tool_id}); print('  ->', st, r if st >= 300 else 'created')
    # 1c. the sub-agents' prompts (what a model needs to be told, learned by running it)
    for a in rows('SystemAIAgent'):
        if a.get('Name') in AGENT_PROMPTS and (a.get('SystemPrompt') or '').strip() != AGENT_PROMPTS[a['Name']].strip():
            print('would set the prompt of', a['Name'])
            if APPLY: print('  ->', call('PATCH', f'/v1/SystemAIAgent/{a["id"]}', {'SystemPrompt': AGENT_PROMPTS[a['Name']]})[0])
    # 2. the parameter scripts
    for a in rows('Action'):
        if str(a.get('Name', '')).startswith(MARKER) and a.get('Command') in COMMANDS:
            same = (a.get('ParameterScript') or '').strip() == PARAMETER_SCRIPT.strip()
            print(('parameter script current:' if same else 'would set parameter script:'), a['Command'])
            if APPLY and not same:
                st, r = call('PATCH', f'/v1/Action/{a["id"]}', {'ParameterScript': PARAMETER_SCRIPT}); print('  ->', st)
    # 3. attach the tool (never touches roles, users or the acknowledgement)
    for table, name in ATTACH_TO:
        for rec in [r for r in rows(table) if r.get('Name') == name]:
            tools = list(rec.get('Tools') or [])
            if tool_id and tool_id in tools:
                print('tool already on', name); continue
            print('would attach the tool to', name)
            if APPLY and tool_id:
                st, r = call('PATCH', f'/v1/{table}/{rec["id"]}', {'Tools': tools + [tool_id]}); print('  ->', st, r if st >= 300 else 'attached')


main()
