// Provy demo: apply the agent's resolution (Servicely Trigger, before update on Incident)
//
// The record API refuses to set WorkflowStatus ("System managed field"), so a simulated agent cannot
// resolve a ticket by PATCHing the state. It does what a person does instead: it writes the closure
// code and notes, and this trigger moves the ticket the way the platform's own transition would.
// Servicely is still the system that settles the ticket; the sim only supplies the agent's decision.
//
// Acts ONLY on tickets carrying the demo marker in ShortDescription. Everything else is untouched.
//
// Proven on the demo instance 15 Oct 2026 (probe INC0000004 to INC0000006):
//   Workflow.forceToActivity('Resolved', current, false, false) from a BEFORE trigger moves the state.
//   With explicitUpdate=true the move does not happen (the record stayed 'new'), so do not use it here.
//   The platform's own "Resolution Count" trigger does NOT fire for a forced move (ResolutionCount stayed 0),
//   so this trigger increments it, exactly as that trigger does for a normal resolve.
try {
    var marker = ApplicationPropertyManager.getProperty('provy.marker') || '[provy-servicely]';
    if (operation == 'update' && String(current.ShortDescription()).indexOf(marker) === 0) {
        var ws = String(current.WorkflowStatus());
        if (current.ClosureCode.hasChanged() && current.ClosureCode() && ws != 'resolved' && ws != 'closed') {
            Workflow.forceToActivity('Resolved', current, false, false);
            current.ResolutionCount(Number(current.ResolutionCount() || 0) + 1);
            // The platform's "Set resolution fields" trigger does this for a normal transition.
            current.CompletedAt(DateTime());
            current.CompletedBy(user.getID());
        }
    }
} catch (e) {
    log.error('provy resolve trigger: ' + e);
}
