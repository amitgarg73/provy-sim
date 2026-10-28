// Provy demo — connection check (ServiceNow scheduled job)
//
// An outbound call from the system of record to Provy on a timer, to prove the link is alive
// without waiting for a ticket to close. It calls Provy's test-connection door, which only
// confirms the ingest key still resolves to this fleet and WRITES NOTHING on the Provy side
// (argus#974). It never touches an incident.
//
// Result is logged ("[provy] connection check ...") and kept in the property provy.check.last
// as "<UTC time> | HTTP <code> | <short note>", so the last answer can be read without the log.
//
// The URL is derived from provy.ingest.url (the outcome door) so it always follows the same
// Provy the push targets; this job has no URL of its own to drift.
//
// ES5 only: Rhino engine.

(function runConnectionCheck() {
    var url = gs.getProperty('provy.ingest.url', '');
    var key = gs.getProperty('provy.ingest.key', '');
    var bypass = gs.getProperty('provy.vercel.bypass', '');
    var LAST_PROP = 'provy.check.last';

    function remember(text) {
        var line = new GlideDateTime().getValue() + ' UTC | ' + text;
        gs.setProperty(LAST_PROP, line);
        gs.info('[provy] connection check ' + line);
    }

    if (!key || !url) {
        remember('skipped | provy.ingest.key or provy.ingest.url is blank');
        return;
    }

    var target = url.replace(/\/outcome\/?$/, '/test-connection');
    if (target === url) {
        remember('skipped | provy.ingest.url does not end in /outcome, so the check URL is unknown: ' + url);
        return;
    }

    try {
        var req = new sn_ws.RESTMessageV2();
        req.setEndpoint(target);
        req.setHttpMethod('POST');
        req.setRequestHeader('Content-Type', 'application/json');
        if (bypass) req.setRequestHeader('x-vercel-protection-bypass', bypass);
        req.setRequestBody(JSON.stringify({ingest_key: key}));
        var resp = req.execute();
        var code = resp.getStatusCode();
        var body = (resp.getBody() + '');
        var note = '';
        try { note = (JSON.parse(body).message || '') + ''; } catch (e1) { note = body.substring(0, 120); }
        if (code === 0) {
            var err = '';
            try { err = (resp.getErrorMessage() || '') + ''; } catch (e2) { err = ''; }
            note = 'no HTTP answer: ' + err;
        }
        remember('HTTP ' + code + ' | ' + note.substring(0, 160));
    } catch (e) {
        remember('error | ' + e);
    }
})();
