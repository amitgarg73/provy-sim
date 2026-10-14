// Provy demo: outcome push (Servicely Trigger, before update on Incident)
//
// The system of record tells Provy how the work turned out. Servicely pushes; Provy is never given
// credentials to read this instance and never polls it.
//
// Fires when a demo ticket reaches Closed. Sends facts the platform maintains itself:
//   ResolutionCount (a count above 1 means resolved, came back, resolved again),
//   ClosureCode, ReassignmentCountGroup, Priority.
//
// SLA verdicts are NOT pushed: this instance has no SLA targets configured for the demo, and sending a
// value the instance cannot settle would be inventing the outcome. Provy shows those conditions as
// uncovered, which is the truthful reading.
//
// Properties (Administration > Configuration, ApplicationProperty), none client accessible:
//   provy.ingest.url      e.g. https://dev.provy.ai/api/ingest/outcome
//   provy.ingest.key      the fleet's ingest key (never in git; set by scripts/wire_servicely_fleet.py)
//   provy.vercel.bypass   optional, pre-prod deployment protection
//   provy.push.mode       'live' (default) or 'capture': capture writes the payload into ClosureNotes and
//                         sends nothing, which is how the payload is checked without a receiver.
try {
    var marker = ApplicationPropertyManager.getProperty('provy.marker') || '[provy-servicely]';
    if (operation == 'update' && String(current.ShortDescription()).indexOf(marker) === 0 &&
        current.WorkflowStatus.hasChanged() && String(current.WorkflowStatus()) == 'closed') {

        var GENUINE = ['Solution provided', 'Resolved by change', 'Resolved by problem', 'Workaround provided',
                       'solution_provided', 'resolved_by_change', 'resolved_by_problem', 'workaround_provided'];
        var code = String(current.ClosureCode() || '');
        var genuineCode = GENUINE.indexOf(code) >= 0;
        var resolutions = Number(current.ResolutionCount() || 0);
        var reopens = resolutions > 1 ? resolutions - 1 : 0;
        var reassign = Number(current.ReassignmentCountGroup() || 0);

        var created = current.CreatedOn();
        var createdIso = (created && created.toISOString) ? created.toISOString() : String(created);
        var businessDate = createdIso.substring(0, 10);   // the day the work was done, not the day of the push

        var resolutionGenuine = genuineCode && reopens === 0;
        var persists = reopens === 0;
        var selfResolved = reassign <= 1;

        var payload = {
            entity_id: String(current.Number()),
            business_date: businessDate,
            label: (resolutionGenuine && persists && selfResolved) ? 'success' : 'fail',
            source: 'confirmed',
            occurred_at: new Date().toISOString(),
            signals: {
                resolution_genuine: resolutionGenuine,
                resolution_persists: persists,
                self_resolved: selfResolved,
                resolution_count: resolutions,
                reopen_count: reopens,
                close_code: code,
                reassignment_count: reassign,
                priority: String(current.Priority() || '')
            }
        };

        var mode = ApplicationPropertyManager.getProperty('provy.push.mode') || 'live';
        var url = ApplicationPropertyManager.getProperty('provy.ingest.url');
        var key = ApplicationPropertyManager.getProperty('provy.ingest.key');

        if (mode == 'capture') {
            current.ClosureNotes(JSON.stringify(payload));
        } else if (!url || !key) {
            log.info('provy outcome push disabled (provy.ingest.url or provy.ingest.key is blank): ' + current.Number());
        } else {
            var bypass = ApplicationPropertyManager.getProperty('provy.vercel.bypass');
            var req = HTTP.post(url).header('Content-Type', 'application/json').header('x-provy-key', key);
            if (bypass) req = req.header('x-vercel-protection-bypass', bypass);
            var resp = req.body(JSON.stringify(payload)).execute();
            log.info('provy outcome push ' + current.Number() + ' -> HTTP ' + resp.getStatus());
        }
    }
} catch (e) {
    log.error('provy outcome push: ' + e);
}
