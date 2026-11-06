"""A fake ServiceNow estate that behaves like the real one, per person: seed = active account, one group, five access items; apply honours the fault map (global keys and a map per person); the
levers a scenario can pull (over-reach onto a bystander, the time-limited-access sweep, a model plan that breaks policy, a guard that is bypassed) have the same effect they have on the instance."""
from multi_vendor.estate import Estate
from multi_vendor.runner import SYNC_JOB, WORKER_JOB

DEFAULT = "provy.iam.spike01"


class FakeEstate:
    def __init__(self, apply_changes_on_wait=False):
        self.fault, self.person_faults, self.jobs, self.log = {}, {}, {SYNC_JOB: False, WORKER_JOB: False}, []
        self.people = {}
        self.after_poll = apply_changes_on_wait
        self._polls = 0
        self.agentic = False
        self.pending_people = set()

    bystander_of = staticmethod(Estate.bystander_of)
    pending = property(lambda s: bool(s.pending_people))                     # the older tests read this as a flag

    # the original single-person view the older tests read
    def _prop(key):
        return property(lambda s: s._p(DEFAULT)[key], lambda s, v: s._p(DEFAULT).__setitem__(key, v))
    active, groups, left = _prop("active"), _prop("groups"), _prop("left")
    del _prop

    def _p(self, person):
        return self.people.setdefault(person, {"active": True, "groups": 1, "left": 5, "grants": 0, "revoked": 0})

    def _faults(self, person):
        return {**self.fault, **self.person_faults.get(person, {})}

    def queue_resync(self, p): self.log.append(("resync_queued", p)); self.pending_people.add(p)
    def clear_hr_events(self, p): self.log.append(("events_cleared", p)); self.pending_people.discard(p)
    def set_fault(self, f): self.fault, self.person_faults = dict(f), {}; self.log.append(("fault", dict(f)))
    def set_person_faults(self, m): self.fault, self.person_faults = {}, {k: dict(v) for k, v in m.items()}; self.log.append(("person_faults", dict(m)))
    def set_agentic(self, on): self.agentic = on; self.log.append(("agentic", on))
    def seed(self, p, expired_grant=False):
        self.people[p] = {"active": True, "groups": 1, "left": 5, "grants": 1 if expired_grant else 0, "revoked": 0}
        self.log.append(("seed", p))
    def set_job_active(self, name, on):
        self.jobs[name] = on; self.log.append(("job", name, on))
        if name == SYNC_JOB and on:                                           # the sync acts only on the people with a queued event
            for who in self.pending_people:
                self._p(who)["active"] = True
    def readings(self, p):
        self._polls += 1
        if self.after_poll and self._polls >= 3:
            self._apply("provy-iam-apply-change", {"person": p})
        s, by = self._p(p), self.people.get(self.bystander_of(p))
        out = {"account_active": s["active"], "groups_held": float(s["groups"]), "access_remaining": float(s["left"]), "systems_checked": 4.0, "expired_grants_active": float(s["grants"])}
        if by is not None:
            out["others_changed"] = float(by["revoked"] + (0 if by["active"] else 1))
        return out
    def apply(self, command, params): self._apply(command, params); return {"status": "ok"}
    def agent_apply(self, command, params, queue_item=None):
        self.log.append(("agent_apply", command, queue_item))
        person, f = params["person"], self._faults(params["person"])
        target = person
        if f.get("model_plan") == "other_person" and f.get("guard") == "bypass":
            target = self.bystander_of(person)                              # the plan the guard should have refused is applied to someone else
        self._apply(command, {**params, "person": target}, faults_of=person)
        held = not any(v == "accept_not_apply" for v in f.values())
        return {"result": {"status": "ok"}, "plan_source": "stub", "trace_mode": "log", "verification": {"held": held, "summary": "x"}}
    def _apply(self, command, params, faults_of=None):
        person = params["person"]
        f = self._faults(faults_of or person)
        s = self._p(person)
        if command == "provy-iam-expire-grants":
            if f.get("expiry_sweep") != "accept_not_apply":
                s["grants"] = 0
            return
        if f.get("directory") != "accept_not_apply":
            s["active"], s["groups"] = False, 0
        s["left"] = 1 if f.get("vpn") == "accept_not_apply" else 0
        over = f.get("over_reach") or []
        if over:
            by = self._p(self.bystander_of(person))
            by["revoked"] += len([x for x in over if x != "directory"])
            if "directory" in over:
                by["active"] = False
