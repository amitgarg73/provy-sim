// Provy demo — verification sweep (ServiceNow scheduled job)
//
// This is the system of record deciding, on its own, whether the AI agent's work
// held up. It runs inside ServiceNow, it reads only the record the agent wrote,
// and the simulation has no say in what it concludes. That separation is the
// entire point of this demo: without it, the thing being measured is also the
// thing reporting the result.
//
// Real desks find out days later, when the caller comes back. This compresses
// that to minutes (provy.demo.reopen_after_min) so a demo can show the full
// lifecycle in one sitting.
//
// Two phases per pass:
//   A. Resolved and old enough -> either the caller reports it is back (reopen and
//      escalate to a second team) or verification passes and it closes.
//   B. Reopened and old enough -> a person finishes it and closes it.
//
// The reopen decision is derived from the record, not from a coin flip. A ticket
// closed with "No resolution provided", or fixed by a team that does not own the
// category, is far likelier to come back, which is exactly what happens on a real
// desk. provy.demo.reopen_pct sets the overall rate; the record moves each
// individual ticket around it.
//
// ES5 only: this runs on the Rhino engine inside ServiceNow.

(function runVerificationSweep() {
    var MARKER = 'provy-itsm';
    var GENUINE = ['Solution provided', 'Resolved by change', 'Resolved by problem',
                   'Workaround provided'];

    var reopenPct = parseFloat(gs.getProperty('provy.demo.reopen_pct', '30')) / 100;
    var ageMinutes = parseFloat(gs.getProperty('provy.demo.reopen_after_min', '3'));
    var ageSeconds = ageMinutes * 60;

    var reopened = 0, closed = 0, finishedByHuman = 0;

    function isGenuineFix(code) {
        for (var i = 0; i < GENUINE.length; i++) {
            if (GENUINE[i] === code) return true;
        }
        return false;
    }

    // The answer key the generator wrote at creation: what this incident really is.
    // The agent never reads it. Format: cat=<category>;grp=<group>
    function answerKey(gr) {
        var out = {cat: '', grp: ''};
        var raw = gr.correlation_display + '';
        var parts = raw.split(';');
        for (var i = 0; i < parts.length; i++) {
            var kv = parts[i].split('=');
            if (kv.length === 2) out[kv[0]] = kv[1];
        }
        return out;
    }

    function secondsSince(glideDateTime) {
        if (!glideDateTime) return 0;
        var then = new GlideDateTime(glideDateTime);
        return gs.dateDiff(then.getDisplayValue(), gs.nowDateTime(), true);
    }

    // How likely this resolution is to come back, read off the record.
    function reopenProbability(gr) {
        var key = answerKey(gr);
        var code = gr.close_code + '';
        var routedRight = key.grp && (gr.assignment_group.getDisplayValue() === key.grp);
        var categoryRight = key.cat && (gr.category + '' === key.cat);

        var multiplier = 1.0;
        if (!isGenuineFix(code)) {
            multiplier = multiplier * 3.0;          // closed without actually fixing anything
        } else if (code === 'Workaround provided') {
            multiplier = multiplier * 1.8;          // the underlying fault is still there
        }
        if (!routedRight) multiplier = multiplier * 1.6;   // wrong team, shakier fix
        if (!categoryRight) multiplier = multiplier * 1.3; // misunderstood the problem
        if (isGenuineFix(code) && code !== 'Workaround provided' && routedRight && categoryRight) {
            multiplier = 0.3;                       // done properly, rarely comes back
        }
        var p = reopenPct * multiplier;
        if (p > 0.95) p = 0.95;
        if (p < 0.01) p = 0.01;
        return p;
    }

    // A second team picking the ticket up. Deliberately not the group that had it:
    // this is the handoff the contract's "handled without being passed on"
    // condition exists to catch, and it has to be a real one in the record.
    function escalationGroup(currentName) {
        var candidates = ['Incident Management', 'Service Desk', 'Software', 'Network',
                          'Hardware', 'Database'];
        for (var i = 0; i < candidates.length; i++) {
            if (candidates[i] !== currentName) {
                var grp = new GlideRecord('sys_user_group');
                if (grp.get('name', candidates[i])) return grp.getUniqueValue();
            }
        }
        return '';
    }

    // ── Phase A: resolved tickets face verification ─────────────────────────
    var resolved = new GlideRecord('incident');
    resolved.addQuery('correlation_id', MARKER);
    resolved.addQuery('state', '6');
    resolved.query();
    while (resolved.next()) {
        if (secondsSince(resolved.resolved_at) < ageSeconds) continue;

        if (Math.random() < reopenProbability(resolved)) {
            // The caller says it is not fixed. Setting the state back is what makes
            // ServiceNow's own "Reopen Count" business rule increment reopen_count,
            // so that number is the platform's, not ours.
            var handTo = escalationGroup(resolved.assignment_group.getDisplayValue());
            resolved.state = '2';
            resolved.close_code = '';
            resolved.close_notes = '';
            resolved.assigned_to = '';
            if (handTo) resolved.assignment_group = handTo;
            resolved.work_notes = '[caller] This is not fixed. The problem came back shortly ' +
                                  'after the ticket was marked resolved. Passing to a second team.';
            resolved.update();
            reopened++;
        } else {
            resolved.state = '7';
            resolved.work_notes = '[verification] Caller confirmed the issue is resolved. Closing.';
            resolved.update();
            closed++;
        }
    }

    // ── Phase B: a person finishes what came back ───────────────────────────
    var back = new GlideRecord('incident');
    back.addQuery('correlation_id', MARKER);
    back.addQuery('state', '2');
    back.addQuery('reopen_count', '>', 0);
    back.query();
    while (back.next()) {
        if (secondsSince(back.sys_updated_on) < ageSeconds) continue;
        back.state = '7';
        back.close_code = 'Solution provided';
        back.close_notes = 'Second-line engineer identified the underlying fault and corrected it.';
        back.work_notes = '[second line] Picked up after the reopen and fixed the root cause.';
        back.update();
        finishedByHuman++;
    }

    gs.info('[provy] verification sweep: reopened=' + reopened + ' closed=' + closed +
            ' finished_by_human=' + finishedByHuman);
})();
