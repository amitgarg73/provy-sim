// Provy demo: verification sweep (Servicely Job, every two minutes)
//
// What a desk does after a resolution: some tickets come back, the rest are closed. Reopen probability
// is derived from the record, not a coin flip against nothing: a weak fix (a closure code that is not a
// genuine one) comes back far more often than a proper one. The base rate is a property.
//
// Properties: provy.demo.reopen_pct (default 6), provy.demo.reopen_after_min (default 3).
// Acts only on marker tickets that are resolved and older than the wait.
//
// It reports on itself, because a Job has no run history we can read: provy.sweep.last_run holds the time
// and counts of the last pass, provy.sweep.last_error holds the last failure. Without these a sweep that
// never ran and a sweep that ran and found nothing look identical.
(function () {
    function setProp(key, value) {
        try {
            var p = Table('ApplicationProperty', EQUAL('Key', key));
            if (p) { p.Value(String(value)); p.update(); }
        } catch (e) { /* the heartbeat must never break the sweep */ }
    }
    var seen = 0, reopened = 0, closed = 0, waiting = 0, secondLine = 0, errors = '';
    try {
        var marker = ApplicationPropertyManager.getProperty('provy.marker') || '[provy-servicely]';
        var basePct = ApplicationPropertyManager.getIntegerProperty('provy.demo.reopen_pct', 6);
        var afterMin = ApplicationPropertyManager.getIntegerProperty('provy.demo.reopen_after_min', 3);
        var GENUINE = ['Solution provided', 'Resolved by change', 'Resolved by problem', 'Workaround provided',
                       'solution_provided', 'resolved_by_change', 'resolved_by_problem', 'workaround_provided'];
        var cutoff = Date.now() - afterMin * 60000;
        var table = Table('Incident')
            .AND(STARTS_WITH('ShortDescription', marker), EQUAL('WorkflowStatus', 'resolved'))
            .maxResults(25).query();
        while (table.hasNext()) {
            var rec = table.next();
            seen++;
            try {
                var done = rec.CompletedAt();
                var doneMs = (done && done.getTime) ? done.getTime() : Date.parse(String(done));
                // An unreadable stamp must not hold a ticket for ever: treat it as old enough, and say so.
                if (isNaN(doneMs)) { errors += ' unparsed CompletedAt for ' + rec.Number(); doneMs = 0; }
                if (doneMs > cutoff) { waiting++; continue; }
                var resolutions = Number(rec.ResolutionCount() || 0);
                var weak = GENUINE.indexOf(String(rec.ClosureCode() || '')) < 0;
                // Weak fix 3x, proper fix 0.3x, as the ServiceNow sweep. A second resolution is never reopened again.
                var pct = resolutions > 1 ? 0 : (weak ? basePct * 3 : basePct * 0.3);
                // Deterministic per ticket, so a re-run of the sweep decides the same way.
                var h = 0, n = String(rec.Number());
                for (var i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) % 1000;
                if (h / 10 < pct) {
                    Workflow.forceToActivity('Work in progress', rec, true, true);
                    reopened++;
                } else {
                    Workflow.forceToActivity('Closed', rec, true, true);
                    closed++;
                }
            } catch (e) {
                errors += ' ' + rec.Number() + ': ' + e;
            }
        }
        // ── second line ────────────────────────────────────────────────────────────────────────────
        // A ticket that came back belongs to a second-line engineer, not to the agent: the agent gets one
        // shot at each incident, which is the only way "the resolution held" means anything. Without this a
        // reopened ticket sits In progress for ever, is never closed, and never pushes its outcome.
        var back = Table('Incident')
            .AND(STARTS_WITH('ShortDescription', marker), EQUAL('WorkflowStatus', 'work_in_progress'),
                 GE('ResolutionCount', 1))
            .maxResults(25).query();
        while (back.hasNext()) {
            var b = back.next();
            try {
                var upd = b.UpdatedOn();
                var updMs = (upd && upd.getTime) ? upd.getTime() : Date.parse(String(upd));
                if (!isNaN(updMs) && updMs > cutoff) { waiting++; continue; }
                var before = Number(b.ResolutionCount() || 0);
                b.ClosureCode('Solution provided');
                b.ClosureNotes('[second line] fixed after the incident came back.');
                Workflow.forceToActivity('Resolved', b, true, true);
                // The platform's own counter fires on a normal transition. If this forced one did not, count it here.
                if (Number(b.ResolutionCount() || 0) === before) { b.ResolutionCount(before + 1); b.update(); }
                secondLine++;
            } catch (e2) {
                errors += ' second line ' + b.Number() + ': ' + e2;
            }
        }
    } catch (e) {
        errors += ' sweep: ' + e;
    }
    setProp('provy.sweep.last_run', new Date().toISOString() + ' seen=' + seen + ' waiting=' + waiting +
            ' closed=' + closed + ' reopened=' + reopened + ' secondline=' + secondLine);
    if (errors) setProp('provy.sweep.last_error', new Date().toISOString() + errors);
})();
