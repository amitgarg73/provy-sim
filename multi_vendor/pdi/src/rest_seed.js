// POC test door: makes a test person with access everywhere, so a leaver scenario has something to take away.
// Only names that start with provy.iam. are accepted. Optional reprovision_in_minutes queues an HR sync that re-creates the access.
(function process(request, response) {
  var E = new global.ProvyIamEstate();
  if (!E.authorized(request)) { response.setStatus(401); response.setBody({ error: 'unauthorized' }); return; }
  var b = request.body.data || {};
  var person = String(b.person || '');
  if (person.indexOf('provy.iam.') !== 0) { response.setStatus(400); response.setBody({ error: 'person must start with provy.iam.' }); return; }
  var u = new GlideRecord('sys_user'); u.addQuery('user_name', person); u.query();
  if (!u.next()) { u.initialize(); u.setValue('user_name', person); u.setValue('first_name', 'POC'); u.setValue('last_name', person.substring(10)); u.setValue('email', person + '@example.invalid'); u.insert(); }
  u.setValue('active', true); u.update();
  var grp = new GlideRecord('sys_user_group'); grp.addQuery('name', 'Provy IAM Spike Group'); grp.query();
  if (grp.next()) {
    var m = new GlideRecord('sys_user_grmember'); m.addQuery('user', u.getUniqueValue()); m.addQuery('group', grp.getUniqueValue()); m.query();
    if (!m.next()) { m.initialize(); m.setValue('user', u.getUniqueValue()); m.setValue('group', grp.getUniqueValue()); m.insert(); }
  }
  var systems = ['sso', 'vpn', 'saas_token'];
  for (var i = 0; i < systems.length; i++) {
    var a = new GlideRecord('u_provy_iam_access'); a.addQuery('u_person', person); a.addQuery('u_system', systems[i]); a.query();
    if (!a.next()) { a.initialize(); a.setValue('u_person', person); a.setValue('u_system', systems[i]); a.setValue('u_kind', systems[i] === 'sso' ? 'session' : 'token'); a.setValue('u_simulated', true); }
    a.setValue('u_state', 'active'); if (a.isNewRecord()) a.insert(); else a.update();
  }
  if (b.reprovision_in_minutes) {
    var d = new GlideDateTime(); d.addSeconds(parseInt(b.reprovision_in_minutes, 10) * 60);
    var h = new GlideRecord('u_provy_iam_hr_event'); h.initialize(); h.setValue('u_person', person); h.setValue('u_action', 'reprovision'); h.setValue('u_due', d.getValue()); h.setValue('u_done', false); h.insert();
  }
  response.setBody(E.read(person));
})(request, response);
