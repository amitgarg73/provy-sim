// Provy demo — outcome push (ServiceNow business rule, after update on incident)
//
// The system of record tells Provy how the work turned out. ServiceNow pushes;
// Provy is never given credentials to read this instance and never polls it. That
// is both the honest architecture and what a real customer does.
//
// Fires when a demo incident reaches Closed. Sends the settled facts, all of them
// fields ServiceNow maintains itself, plus two derived from the answer key the
// generator wrote at creation and the agent never read.
//
// Condition (set on the business rule record, not here):
//   correlation_id = provy-itsm, state changes to 7
//
// ES5 only: Rhino engine.

(function executeRule(current, previous /*null when async*/) {
    var GENUINE = ['Solution provided', 'Resolved by change', 'Resolved by problem',
                   'Workaround provided'];

    var url = gs.getProperty('provy.ingest.url', '');
    var key = gs.getProperty('provy.ingest.key', '');
    var bypass = gs.getProperty('provy.vercel.bypass', '');

    if (!key || !url) {
        gs.info('[provy] outcome push disabled (provy.ingest.key or provy.ingest.url is blank): ' +
                current.number);
        return;
    }

    function isGenuineFix(code) {
        for (var i = 0; i < GENUINE.length; i++) {
            if (GENUINE[i] === code) return true;
        }
        return false;
    }

    function answerKey() {
        var out = {cat: '', grp: ''};
        var parts = (current.correlation_display + '').split(';');
        for (var i = 0; i < parts.length; i++) {
            var kv = parts[i].split('=');
            if (kv.length === 2) out[kv[0]] = kv[1];
        }
        return out;
    }

    var key_ = answerKey();
    var closeCode = current.close_code + '';
    var reopenCount = parseInt(current.reopen_count + '', 10) || 0;
    var reassignCount = parseInt(current.reassignment_count + '', 10) || 0;

    // DO NOT read current.made_sla. It looks like the obvious field and it is a trap:
    // nothing in this instance maintains it. Stock incidents with a genuinely breached
    // SLA still carry made_sla = true (INC0000050, INC0000060), and there is no active
    // business rule that writes it. It is seeded demo data, not a computed result.
    //
    // The SLA engine's real output is task_sla, one record per attached target,
    // carrying the has_breached the platform actually calculates. Reading it there is
    // not a workaround: it is reading the verdict from where the platform keeps it,
    // instead of from a mirror the platform never updates.
    // Counted PER KIND, which is the whole point. A ticket carries a response target and a
    // resolution target, and rolling them into one boolean meant a ticket answered in seconds but
    // fixed three days late reported that it MISSED FIRST RESPONSE. Every such ticket has been
    // lying since this script shipped. contract_sla.target is 'response' or 'resolution' and is set
    // by scripts/install_servicenow_lifecycle.py, so this reads the platform's own classification
    // rather than guessing from a name.
    var slaTotal = 0, slaBreached = 0;
    var respTotal = 0, respBreached = 0;
    var resoTotal = 0, resoBreached = 0;
    var sla = new GlideRecord('task_sla');
    sla.addQuery('task', current.sys_id);
    sla.query();
    while (sla.next()) {
        slaTotal++;
        var breached = (sla.has_breached + '') === 'true';
        if (breached) slaBreached++;
        var kind = (sla.sla.target + '').toLowerCase();
        if (kind === 'response')        { respTotal++; if (breached) respBreached++; }
        else if (kind === 'resolution') { resoTotal++; if (breached) resoBreached++; }
    }
    // Kept for traceability only: the roll-up across every attached target. NOT a contract signal.
    // Grading anything on this is the bug described above.
    var madeSla = slaBreached === 0;
    // A target that was never attached was never committed to, so there is no verdict to report.
    // Omitted rather than asserted, on the same principle that keeps procedure_grounded out of the
    // bag below: sending a value this instance cannot settle would be inventing the outcome.
    var responseMet   = respTotal > 0 ? respBreached === 0 : null;
    var resolutionMet = resoTotal > 0 ? resoBreached === 0 : null;

    // Setting the assignment group counts as a reassignment in ServiceNow, so the
    // agent's own routing always leaves 1. A handoff is anything beyond that.
    var handoffs = reassignCount > 1 ? reassignCount - 1 : 0;

    // Minutes of human effort recorded against the ticket. time_worked is a
    // duration field; empty means nobody logged any, which is the normal case for a
    // ticket the agent handled end to end.
    var worklogMinutes = 0;
    if (current.time_worked) {
        var dur = new GlideDuration(current.time_worked + '');
        worklogMinutes = Math.round(dur.getNumericValue() / 60000);
    }

    var routingCorrect = key_.grp ? (current.assignment_group.getDisplayValue() === key_.grp) : null;
    var categoryCorrect = key_.cat ? ((current.category + '') === key_.cat) : null;

    // The customer's own definition of a good outcome, which is what the contract
    // in Provy grades against. Computed here because it is the customer's call, not
    // the monitoring tool's.
    // Resolution time now counts. A ticket fixed long after its target is not a good outcome, and
    // leaving it out was only defensible while the two SLAs were indistinguishable. This makes the
    // demo's success rate lower and more honest.
    var success = (responseMet !== false) && (resolutionMet !== false) &&
                  reopenCount === 0 && isGenuineFix(closeCode) && reassignCount <= 1;

    // The contract's own signal names, one per condition (2026-07-28). Provy grades a
    // condition by looking up the signal it was authored against, so the push has to speak
    // the contract's vocabulary, not just this instance's field names. The raw fields below
    // stay in the bag: they are what makes a verdict traceable back to the records it came
    // from, and Provy reads them for the failure-anatomy comparisons.
    //
    // Condition text these satisfy, verbatim from the contract:
    //   resolution_genuine       "resolved with a genuine fix on first attempt, not reopened
    //                             or marked cannot-reproduce"
    //   first_response_time_met  "first response is delivered within the agreed response time target"
    //   resolution_time_met      "the incident is resolved within the agreed resolution time target"
    //   resolution_persists      "stays resolved and is not reopened after closure"
    //   self_resolved            "resolves the incident without escalation or handoff to another team"
    //
    // The contract's fifth condition, procedure_grounded, is deliberately NOT pushed. Whether a
    // diagnosis followed a documented procedure is a judgement about the agent's reasoning, and
    // this instance settles no such fact. Sending a value we cannot observe would be inventing
    // the outcome, so it stays unreported and Provy shows it as uncovered.
    var contractSignals = {
        resolution_genuine:      isGenuineFix(closeCode) && reopenCount === 0,
        first_response_time_met: responseMet,
        resolution_time_met:     resolutionMet,
        resolution_persists:     reopenCount === 0,
        self_resolved:           reassignCount <= 1
    };

    // THE DATE THE AGENT DID THE WORK, not the date this push fires. Those are
    // different once a ticket sits through a reopen, and pushing "today" for work
    // done earlier is what put 233 phantom rows in the ledger. opened_at is used
    // rather than resolved_at because a reopened ticket gets resolved twice and
    // only the first one was the agent's.
    var businessDate = (current.opened_at + '').substring(0, 10);

    var payload = {
        entity_id: current.number + '',
        business_date: businessDate,
        label: success ? 'success' : 'fail',
        source: 'confirmed',
        occurred_at: new GlideDateTime().getDisplayValueInternal(),
        signals: {
            // Contract vocabulary first — these are what Provy actually grades. The two
            // time-based ones are attached below, and only when a target of that kind was
            // actually committed to.
            resolution_genuine:      contractSignals.resolution_genuine,
            resolution_persists:     contractSignals.resolution_persists,
            self_resolved:           contractSignals.self_resolved,
            // Raw instance fields, kept so a verdict can be traced to its records.
            made_sla: madeSla,
            sla_response_targets:   respTotal,
            sla_response_breached:  respBreached,
            sla_resolution_targets: resoTotal,
            sla_resolution_breached: resoBreached,
            // Shown alongside so the graded verdict can be traced back to the SLA
            // records it came from, rather than being an unexplained boolean.
            sla_targets: slaTotal,
            sla_breached: slaBreached,
            reopen_count: reopenCount,
            close_code: closeCode,
            reassignment_count: reassignCount,
            handoffs: handoffs,
            worklog_minutes: worklogMinutes,
            priority: current.priority + '',
            category: current.category + ''
        }
    };
    // Attached only when a target of that kind was actually committed to. A ticket with no
    // resolution SLA has no resolution verdict, and asserting one would be inventing it; Provy shows
    // the condition as uncovered instead, which is the truthful reading.
    if (responseMet !== null) payload.signals.first_response_time_met = responseMet;
    if (resolutionMet !== null) payload.signals.resolution_time_met = resolutionMet;
    if (routingCorrect !== null) payload.signals.routing_correct = routingCorrect;
    if (categoryCorrect !== null) payload.signals.category_correct = categoryCorrect;

    try {
        var req = new sn_ws.RESTMessageV2();
        req.setEndpoint(url);
        req.setHttpMethod('POST');
        req.setRequestHeader('Content-Type', 'application/json');
        req.setRequestHeader('x-provy-key', key);
        // Provy pre-prod sits behind Vercel's deployment protection, so this header
        // is what gets the request to the route at all.
        if (bypass) req.setRequestHeader('x-vercel-protection-bypass', bypass);
        req.setRequestBody(JSON.stringify(payload));
        req.setHttpTimeout(15000);

        var resp = req.execute();
        var status = resp.getStatusCode();
        var body = resp.getBody() + '';

        if (status >= 200 && status < 300) {
            gs.info('[provy] pushed ' + current.number + ' (' + payload.label + ', ' +
                    businessDate + '): ' + body.substring(0, 200));
            return;
        }

        // A rotated bypass token fails here as a VERCEL error, not a Provy-shaped
        // one, and reads like a Provy bug unless it is called out by name.
        if (body.indexOf('Protected deployment') > -1 || body.indexOf('Authentication Required') > -1 ||
            body.indexOf('vercel') > -1) {
            gs.error('[provy] PUSH BLOCKED BY VERCEL, NOT BY PROVY, for ' + current.number +
                     '. The deployment-protection bypass token in the provy.vercel.bypass ' +
                     'property is missing or has been rotated. HTTP ' + status);
            return;
        }
        if (status === 401) {
            gs.error('[provy] push rejected for ' + current.number +
                     ': Provy did not accept the ingest key in provy.ingest.key. HTTP 401');
            return;
        }
        gs.error('[provy] push failed for ' + current.number + ': HTTP ' + status + ' ' +
                 body.substring(0, 300));
    } catch (e) {
        gs.error('[provy] push threw for ' + current.number + ': ' + e);
    }
})(current, previous);
