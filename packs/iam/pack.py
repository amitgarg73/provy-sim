"""Domain pack — Identity and access (commitment integrity).

An identity agent processes a joiner, mover or leaver request. It disables the account, strips the
groups, revokes the tokens, and reports "offboarding complete." The directory returns OK, every eval
passes, and the trace is clean. Later the effective-access check reads what a person can ACTUALLY do:
a SaaS token still authenticates, a live session was never terminated, a privileged group survived,
the change landed on the wrong principal, or an HR sync re-created the account overnight.

⛔ THIS PACK EXISTS FOR A DISTINCTION NO OTHER PACK MODELS: CONFIGURED STATE vs EFFECTIVE STATE.
Every other commitment pack asks whether an action settled — did the money move, did the ticket
issue. Here the action provably settled: the directory really does say `disabled`. The question is
whether that *means* anything, and the answer lives in a different system from the one the agent
wrote to. "Disabling the main account is not the same as completing offboarding" is not a bug in the
agent's work; it is a gap between two systems that both believe they are right.

That is also why the failure is invisible to a run-time supervisor. Nothing errored. The agent did
exactly what it was asked, got an OK, and was wrong about what it had achieved.

The failure EMERGES from the mock system of record; the applier made the commitment, so the applier
is the culprit.
"""
from __future__ import annotations

from engine.commitment import CommitmentPack, Injector
from engine.types import AgentSpec, Criterion, LeverManifest, RunContext, RunResult

# The systems an identity change has to reach. Each one is somewhere a credential can survive.
_TARGETS = ["directory", "sso", "vpn", "cloud_console", "code_host", "crm"]


class IamPack(CommitmentPack):
    workflow = "iam"
    session_type = "access_change"

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("intake", "Request Intake",
                      "Reads the joiner, mover or leaver request and pins down who it is about.", "📨", 0),
            AgentSpec("mapper", "Entitlement Lookup",
                      "Works out which systems and groups that person should end up with.", "🗺️", 1),
            AgentSpec("applier", "Apply Access Change",
                      "Makes the change in the identity provider and reports the request complete.", "🔑", 2),
            AgentSpec("reviewer", "Access Review",
                      "Checks the change against the request before the ticket is closed.", "✅", 3),
        ]

    def contract(self) -> list[Criterion]:
        """
        ⛔ c1 IS NOT "DID THE DIRECTORY ACCEPT THE CHANGE". It accepted it in every run, including
        every failing one. c1 asks whether the person's EFFECTIVE access matches the request, which
        is a different system's answer and the only one a customer cares about.
        """
        return [
            Criterion("c1", "The access change actually took effect everywhere it had to",
                      "both", "access_change_effective", "eq", True),
            Criterion("c2", "The person ends up with exactly the entitlements the request called for",
                      "outcome", "entitlements_correct", "eq", True),
            Criterion("c3", "The change was applied to the right person",
                      "outcome", "correct_principal", "eq", True),
            Criterion("c4", "Completed within the agreed access-change window",
                      "outcome", "sla_met", "eq", True),
            Criterion("c5", "The account was not re-created by a later sync",
                      "outcome", "no_reprovision", "eq", True),
        ]

    def failure_cost(self) -> dict:
        """A leaver who keeps access is the expensive one: it is the audit finding and the breach path.

        ⛔ THESE ARE ILLUSTRATIVE AND THE DEMO SAYS SO. No customer has given us a cost per orphaned
        credential, and inventing a precise one is the exact thing Provy sells against.
        """
        return {"commitment_unsettled": 4000.0, "commitment_wrong_amount": 1500.0,
                "commitment_wrong_target": 3000.0, "commitment_duplicate": 2500.0}

    def trace_aliases(self) -> dict[str, str]:
        """The agent knows it SENT the change. Whether access actually ended is the estate's to say.

        ⛔ THE RENAME IS THE WHOLE POINT OF THIS PACK. The applier can only ever report
        `directory_updated`, because that is the only thing it observed. Emitting it as
        `access_change_effective` would have the agent claiming knowledge it does not have, and the
        divergence this pack exists to show would be an agent contradicting itself rather than two
        systems disagreeing.
        """
        return {
            "access_change_effective": "directory_updated",
            "sla_met": "within_window",
        }

    def signal_owners(self) -> dict[str, str]:
        """Which agent's work decides each signal, and therefore who a failure is attributed to.

        Without this every condition blames the reviewer, because the shared helper stamps contract
        signals on the closing message unless a pack says otherwise.

        The applier makes the change and reports it done. WHICH entitlements were right is the
        mapper's call, and so is the principal: picking the wrong person is a lookup failure, not an
        execution one. Timeliness is left to the reviewer, because an SLA is a property of the whole
        run rather than of any single step.
        """
        return {
            "access_change_effective": "applier",
            "entitlements_correct":    "mapper",
            "correct_principal":       "mapper",
            "no_reprovision":          "applier",
        }

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="applier", retriever_agent="mapper", reviewer_agent="reviewer",
            first_agent="intake", downstream_agent="applier",
            correctness_signal="access_change_effective",
            policy_signal="entitlements_correct", sla_signal="sla_met",
            drift_agent="applier",
        )

    def injectors(self) -> list[Injector]:
        """Five ways an access change is accepted and still does not mean what it says.

        ⛔ EVERY ONE OF THESE LEAVES THE DIRECTORY CORRECT. That is what separates this pack: there
        is no run where the agent's own system disagrees with the agent. The disagreement is always
        between the system it wrote to and the systems that actually gate access.
        """
        return [
            Injector("credential_still_live", "unsettled", "orphaned_credential_active",
                     "the account is disabled and a SaaS token issued to it still authenticates"),
            Injector("session_not_terminated", "wrong_amount", "live_session_survived",
                     "the account is disabled and a session opened before the change is still valid"),
            Injector("group_left_behind", "wrong_amount", "privileged_group_retained",
                     "a privileged group membership survived the change, so the access is still granted"),
            Injector("wrong_principal", "wrong_target", "applied_to_wrong_account",
                     "the change was applied to a similarly named account, not the one requested"),
            Injector("reprovisioned_by_sync", "duplicate", "account_recreated_by_sync",
                     "an HR sync re-created the account after the change, restoring the access"),
        ]

    def settle_map(self) -> dict:
        return {"promise": "access_change_effective", "wrong_amount": "entitlements_correct",
                "wrong_target": "correct_principal", "duplicate": "no_reprovision"}

    def commit_amount(self, item) -> float:
        """The count of systems this change has to reach.

        ⛔ A COUNT, NOT MONEY, AND THE ENGINE DOES NOT CARE. `commit_amount` is what the mock system
        of record settles against; for a payout that is dollars, and here it is how many systems must
        end up in the requested state. "Settled for a different amount" then reads as "a different
        number of systems ended up correct", which is exactly what a partial revocation is.
        """
        return float(len(item["systems"]))

    def clean_narration(self, amount: float) -> str:
        return (f"Effective-access check: all {int(amount)} systems match the request, "
                f"no credential, session or group survived. Promise kept.")

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        """A joiner, mover or leaver. All three are the same shape: end state requested, end state reached.

        ⛔ THE KIND IS ON THE ITEM AND NOT IN THE CONTRACT. A leaver should end with nothing and a
        joiner with a specific set, but both are graded by `entitlements_correct` — "exactly what the
        request called for". Forking the contract per kind would mean two contracts, and a fleet that
        grades itself differently depending on the work is not one fleet.
        """
        kind = rng.choice(["leaver", "leaver", "mover", "joiner"])   # leavers are the common case
        n = rng.randint(1000, 9999)
        person = rng.choice(["a.okafor", "j.lindqvist", "r.mehta", "s.dubois", "t.nakamura", "m.oyelaran"])
        systems = rng.sample(_TARGETS, rng.randint(3, len(_TARGETS)))
        want = {
            "leaver": "revoke every entitlement",
            "mover":  "swap the old team's groups for the new team's",
            "joiner": "grant the standard set for the role",
        }[kind]
        item = {
            "id": f"AC-{n}",
            "kind": kind,
            "principal": person,
            "systems": systems,
            "text": f"{kind.title()} request for {person}: {want} across {len(systems)} systems.",
        }
        ground_truth = {"kind": kind, "systems": systems, "principal": person}
        return item, ground_truth

    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        who = item["principal"]
        systems = item["systems"]
        A = {a.name: a for a in self.agents()}

        r.traces.append(self.agent_step(
            ctx, A["intake"], item,
            decision=f"{item['kind'].title()} request for {who} across {len(systems)} systems.",
            entity_id=eid,
            payload_extra={"narration": f"Read the request: {item['kind']} for {who}.",
                           "kind": item["kind"], "principal": who}))

        r.traces.append(self.tool_step(
            ctx, A["mapper"], "entitlements.lookup",
            tool_input={"principal": who},
            tool_output={"principal": who, "systems": systems,
                         "groups": [f"grp-{s}" for s in systems], "resolved": True},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["mapper"], item,
            decision=f"{who} holds access in {', '.join(systems)}. That is the set to change.",
            entity_id=eid,
            payload_extra={"narration": f"Resolved {who} to {len(systems)} systems.",
                           "systems": systems}))

        # ⛔ THE TOOL ONLY EVER ANSWERS FOR THE DIRECTORY. It cannot see whether a token minted last
        # month still works, which is the gap the whole pack is about, so a clean run and every
        # failing run get the identical OK here.
        r.traces.append(self.tool_step(
            ctx, A["applier"], "identity.apply_change",
            tool_input={"principal": who, "kind": item["kind"], "systems": systems},
            tool_output={"ok": True, "principal": who, "applied": systems,
                         "directory_state": "disabled" if item["kind"] == "leaver" else "updated"},
            entity_id=eid))
        told = f"{item['kind'].title()} complete for {who}: {len(systems)} systems updated."
        r.traces.append(self.agent_step(
            ctx, A["applier"], item,
            decision=f"Applied the change across {len(systems)} systems; directory returned OK.",
            entity_id=eid,
            payload_extra={"action": "apply_change", "systems_changed": len(systems),
                           "system_response": "ok", "told_requester": told, "confidence": "HIGH",
                           "narration": f'Applied across {len(systems)} systems (got OK): "{told}"'}))

        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision="The change matches the request on every listed system. Closing.",
            entity_id=eid,
            payload_extra={"narration": "Compared the applied change to the request, it matches, closed."}))

        r.evals = [
            self.eval_pass("intake", "request_parsed", eid, "identified the principal and the kind of change"),
            self.eval_pass("mapper", "entitlement_resolution", eid, "resolved the full set of systems the person holds"),
            self.eval_pass("applier", "change_applied", eid, "applied the change and received an OK from the directory"),
            self.eval_pass("reviewer", "request_match", eid, "the applied change matches what the request asked for"),
        ]
        r.terminal_reason = "applied"
        return r
