// Provy demo: verification sweep (Servicely Job, every two minutes)
//
// What a desk does after a resolution: some tickets come back, the rest are closed. Reopen probability
// is derived from the record, not a coin flip against nothing: a weak fix (a closure code that is not a
// genuine one) comes back far more often than a proper one. The base rate is a property.
//
// Properties: provy.demo.reopen_pct (default 6), provy.demo.reopen_after_min (default 3).
// Acts only on marker tickets that are resolved and older than the wait. A reopened ticket is left alone
// afterwards: its second resolution is the sim's second-line work, and closing it triggers the push.
(function () {
    var marker = ApplicationPropertyManager.getProperty('provy.marker') || '[provy-servicely]';
    var basePct = ApplicationPropertyManager.getIntegerProperty('provy.demo.reopen_pct', 6);
    var afterMin = ApplicationPropertyManager.getIntegerProperty('provy.demo.reopen_after_min', 3);
    var GENUINE = ['Solution provided', 'Resolved by change', 'Resolved by problem', 'Workaround provided',
                   'solution_provided', 'resolved_by_change', 'resolved_by_problem', 'workaround_provided'];
    var cutoff = new Date(Date.now() - afterMin * 60000);
    var table = Table('Incident').STARTS_WITH('ShortDescription', marker).EQUAL('WorkflowStatus', 'resolved').maxResults(25).query();
    while (table.hasNext()) {
        var rec = table.next();
        try {
            var done = rec.CompletedAt();
            var doneMs = (done && done.getTime) ? done.getTime() : Date.parse(String(done));
            if (!doneMs || doneMs > cutoff.getTime()) continue;
            var resolutions = Number(rec.ResolutionCount() || 0);
            var weak = GENUINE.indexOf(String(rec.ClosureCode() || '')) < 0;
            // Weak fix 3x, proper fix 0.3x, as the ServiceNow sweep. A second resolution is never reopened again.
            var pct = resolutions > 1 ? 0 : (weak ? basePct * 3 : basePct * 0.3);
            // Deterministic per ticket, so a re-run of the sweep decides the same way.
            var h = 0, n = String(rec.Number());
            for (var i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) % 1000;
            if (h / 10 < pct) {
                Workflow.forceToActivity('Work in progress', rec, true, true);
            } else {
                Workflow.forceToActivity('Closed', rec, true, true);
            }
        } catch (e) {
            log.error('provy sweep ' + rec.Number() + ': ' + e);
        }
    }
})();
