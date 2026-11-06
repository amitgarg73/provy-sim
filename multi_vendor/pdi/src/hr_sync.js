// Scheduled script body (INACTIVE until switched on): the HR sync. A due 'reprovision' event re-creates the directory account and the
// sign-on session, which is how an offboarding is quietly undone by a later feed. The leaver check must catch this after a hold window.
var h = new GlideRecord('u_provy_iam_hr_event');
h.addQuery('u_done', false);
h.addQuery('u_due', '<=', new GlideDateTime().getValue());
h.addQuery('u_action', 'reprovision');
h.query();
while (h.next()) {
  var person = h.getValue('u_person');
  var u = new GlideRecord('sys_user'); u.addQuery('user_name', person); u.query();
  if (u.next()) { u.setValue('active', true); u.update(); }
  var a = new GlideRecord('u_provy_iam_access'); a.addQuery('u_person', person); a.addQuery('u_system', 'sso'); a.query();
  if (a.next()) { a.setValue('u_state', 'active'); a.update(); }
  h.setValue('u_done', true); h.setValue('u_note', 'account re-created by sync'); h.update();
  // its own named actor in the trace (when tracing is configured): the thing that quietly undoes an offboarding. Never throws into the job.
  try {
    var tr = new global.ProvyIamTrace();
    if (tr.enabled()) {
      tr.open('pdi-hrsync-' + h.getUniqueValue(), 'access_provisioning', null, { actor: 'hr_sync', person: person });
      tr.step('hr_sync', 'tool_call', 'success', { phase: 'reprovision', person: person, effect: 'directory account active again, sso session restored' });
      tr.close('account re-created by sync', 're_created');
    }
  } catch (eTrace) { gs.warn('provy_iam hr_sync: trace failed: ' + String(eTrace).substring(0, 80)); }
}
