"""Domain pack — Travel (commitment integrity).

A corporate-travel agent books a flight and tells the traveler "you're booked and
ticketed." The GDS returns an OK booking reference, every eval passes, and the trace
looks clean. Later the settlement feed (PNR segment status, e-ticket issuance) reads
what actually happened: the ticket never issued and the segment auto-cancelled at the
ADTK deadline, the fare that settled is different, or the trip was booked twice. That
gap is the divergence Provy catches by reconciling the claim (what the agent said)
against the settled reservation (what the airline system shows).

The failure EMERGES from the mock system of record (engine/commitment.py MockSoR), not
from a lever that sets a signal by hand, so the harness itself must reconcile to know
the truth. The booker made the commitment, so the booker is the culprit.

This pack is a superset: on top of these travel injectors it runs the full generic +
L1/L2 lever set (see CommitmentPack), so it can also demonstrate silent-wrong,
drift, tool latency, LLM cost, and everything the other packs inject.
"""
from __future__ import annotations

from engine.commitment import CommitmentPack, Injector
from engine.types import AgentSpec, Criterion, LeverManifest, RunContext, RunResult


class TravelPack(CommitmentPack):
    workflow = "travel"
    session_type = "booking"

    # ── pipeline (self-explaining roles) ─────────────────────────────────────
    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("intake", "Trip Intake",
                      "Reads the traveler's request and pins down the trip: route, dates, cabin.", "🧳", 0),
            AgentSpec("searcher", "Fare Search",
                      "Searches the GDS for a fare that fits the policy and the traveler's request.", "🔎", 1),
            AgentSpec("booker", "Booking",
                      "Books the fare in the GDS, then tells the traveler the trip is confirmed and ticketed.", "🎟️", 2),
            AgentSpec("reviewer", "Itinerary Check",
                      "Checks the confirmed itinerary against the request before it goes to the traveler.", "✅", 3),
        ]

    # ── contract: the promise is the star ('both' so claim vs settled reservation
    # are graded side by side) ──────────────────────────────────────────────────
    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "Flight was actually ticketed", "both", "ticket_issued", "eq", True),
            Criterion("c2", "Charged the fare that was quoted", "outcome", "fare_correct", "eq", True),
            Criterion("c3", "No duplicate booking", "outcome", "no_duplicate_booking", "eq", True),
            Criterion("c4", "Ticketed before the deadline", "outcome", "sla_met", "eq", True),
            Criterion("c5", "Booked the trip the traveler asked for", "both", "itinerary_correct", "eq", True),
        ]

    def failure_cost(self) -> dict:
        # A traveler stranded at the gate costs a rebook plus goodwill; a wrong fare costs the
        # difference plus rehandling; a double booking costs the second ticket.
        return {"commitment_unsettled": 450.0, "commitment_wrong_amount": 120.0, "commitment_duplicate": 400.0}

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="booker", retriever_agent="searcher", reviewer_agent="reviewer",
            first_agent="intake", downstream_agent="booker",
            correctness_signal="ticket_issued", policy_signal="ticket_issued", sla_signal="sla_met",
            drift_agent="booker",
        )

    # ── commitment-integrity injectors (the mock GDS/ticketing settlement) ───────
    def injectors(self) -> list[Injector]:
        return [
            Injector("not_ticketed", "unsettled", "ticket_never_issued",
                     "the ticket never issued and the segment auto-cancelled at the ticketing deadline, so the seat was released"),
            Injector("segment_reversed", "unsettled", "segment_status_uc",
                     "the airline flipped the segment from confirmed to unable, so the seat is gone"),
            Injector("wrong_fare", "wrong_amount", "fare_mismatch",
                     "the fare that ticketed is not the fare the traveler was quoted"),
            Injector("double_booked", "duplicate", "duplicate_pnr",
                     "the trip was booked twice, so the traveler holds two tickets"),
        ]

    def settle_map(self) -> dict:
        return {"promise": "ticket_issued", "wrong_amount": "fare_correct", "duplicate": "no_duplicate_booking"}

    def commit_amount(self, item) -> float:
        return float(item["fare"])

    def clean_narration(self, amount: float) -> str:
        return f"Ticketing check: the e-ticket issued and the segment is confirmed for ${amount:.2f}. Promise kept."

    # ── generator with ground truth ──────────────────────────────────────────
    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        route = rng.choice(["SFO-JFK", "SEA-ORD", "AUS-DEN", "BOS-LAX", "ATL-SEA"])
        fare = rng.choice([214.00, 318.50, 402.00, 561.00, 288.75, 640.00])
        item = {
            "id": f"TRIP-{n}",
            "pnr": f"PNR{rng.randint(100, 999)}",
            "route": route,
            "cabin": "economy",
            "fare": fare,
            "text": f"Book {route} in economy for ${fare:.2f}.",
        }
        ground_truth = {"route": route, "fare": fare, "policy_ok": True}
        return item, ground_truth

    # ── clean baseline: the agent books and confirms ─────────────────────────
    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        fare = item["fare"]
        A = {a.name: a for a in self.agents()}

        r.traces.append(self.agent_step(
            ctx, A["intake"], item,
            decision=f"Traveler wants {item['route']} in {item['cabin']}.",
            entity_id=eid,
            payload_extra={"narration": f"Read the request: {item['route']} in {item['cabin']}."}))

        r.traces.append(self.tool_step(
            ctx, A["searcher"], "gds.search",
            tool_input={"route": item["route"], "cabin": item["cabin"]},
            tool_output={"route": item["route"], "fare": fare, "policy_ok": True,
                         "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["searcher"], item,
            decision=f"Found a policy-compliant fare on {item['route']} at ${fare:.2f}.",
            entity_id=eid,
            payload_extra={"narration": f"Searched the GDS: ${fare:.2f} on {item['route']}, within policy."}))

        r.traces.append(self.tool_step(
            ctx, A["booker"], "gds.book",
            tool_input={"route": item["route"], "fare": fare},
            tool_output={"ok": True, "pnr": item["pnr"], "fare": fare, "segment_status": "HK"},
            entity_id=eid))
        told = f"You're confirmed on {item['route']} for ${fare:.2f}. Your ticket is issued."
        r.traces.append(self.agent_step(
            ctx, A["booker"], item,
            decision=f"Booked {item['route']} at ${fare:.2f}; received OK; told the traveler it is ticketed.",
            entity_id=eid,
            payload_extra={"action": "book", "fare": fare, "system_response": "ok",
                           "told_traveler": told, "confidence": "HIGH",
                           "narration": f'Booked {item["route"]} in the GDS (got OK), told the traveler: "{told}"'}))

        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision="Itinerary matches the request and the booking receipt. Approved to send.",
            entity_id=eid,
            payload_extra={"narration": "Checked the itinerary against the request, it matches, approved."}))

        r.evals = [
            self.eval_pass("intake", "request_accuracy", eid, "captured the route, cabin, and dates correctly"),
            self.eval_pass("searcher", "fare_relevance", eid, "found a policy-compliant fare that fits the request"),
            self.eval_pass("booker", "booking_executed", eid, "booked the fare and received an OK reference"),
            self.eval_pass("reviewer", "itinerary_quality", eid, "the itinerary matches the request and is safe to send"),
        ]
        r.terminal_reason = "booked"
        return r
