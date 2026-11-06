// Scheduled script body (INACTIVE until switched on): one pass of the queue worker.
var worked = new global.ProvyIamWorker().run();
if (worked > 0) gs.info('provy_iam worker: worked ' + worked + ' task(s)');
