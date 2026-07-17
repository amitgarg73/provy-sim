"""In-process mock system of record (a fake Stripe) for the commitment-integrity packs.

The point of a commitment-integrity pack: an agent calls refund() and gets an
immediate OK receipt, so the agent, and its trace, believe the promise
succeeded. The store's SETTLED state, read later via settlement(), may silently
disagree. The refund sits pending, the bank returns it, the amount is wrong, or
it posts twice. Which of those happens EMERGES from behavioral injectors seeded
off the run's RNG, so even the harness must read the settled state to know the
truth. That is what makes the failure a genuine promise-vs-settlement
divergence, not a label set by hand.

At most one injector fires per order, so a broken run has one clear cause the
scoreboard can grade Provy's attribution against. Everything else settles clean.

Injectors (rate-driven):
  unsettled_insufficient  receipt OK, settled=False (balance cannot cover it)
  unsettled_bank_return   receipt OK, settled=False (bank returned the transfer)
  wrong_amount            settled True, but a different amount than requested
  duplicate               a second charge posts; the customer is double-refunded
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Rolled in this order; the first injector that hits wins (one cause per order).
INJECTORS = ["unsettled_insufficient", "unsettled_bank_return", "wrong_amount", "duplicate"]

_REASON = {
    "unsettled_insufficient": "pending_insufficient_balance",
    "unsettled_bank_return": "bank_returned",
    "wrong_amount": "wrong_amount",
    "duplicate": "duplicate_charge",
}


@dataclass
class Receipt:
    """What the agent gets back at call time. Always looks like success."""
    ok: bool
    refund_id: str
    amount: float


@dataclass
class Settlement:
    """What actually settled, read later by the settlement feed. The truth."""
    settled: bool
    amount_settled: float
    duplicate: bool
    reason: str                 # 'cleared' | one of _REASON values | 'not_found'
    injector: Optional[str]     # the injector that fired, or None on a clean settle


class MockStripe:
    """A tiny stateful fake payment system. Deterministic given the RNG."""

    def __init__(self, rng, rates: dict[str, float]):
        self.rng = rng
        self.rates = rates                      # injector name -> per-order rate
        self._refunds: dict[str, Settlement] = {}

    def refund(self, order_id: str, amount: float) -> Receipt:
        """Issue a refund. The receipt is always OK; the settled fate is decided
        now (behaviorally) but only visible later via settlement()."""
        injector = self._roll()
        if injector is None:
            s = Settlement(True, amount, False, "cleared", None)
        elif injector in ("unsettled_insufficient", "unsettled_bank_return"):
            s = Settlement(False, 0.0, False, _REASON[injector], injector)
        elif injector == "wrong_amount":
            bad = round(amount * self.rng.choice([0.5, 0.9, 1.1, 1.25]), 2)
            s = Settlement(True, bad, False, _REASON[injector], injector)
        else:  # duplicate
            s = Settlement(True, amount, True, _REASON[injector], injector)
        self._refunds[order_id] = s
        return Receipt(ok=True, refund_id=f"re_{order_id}", amount=amount)

    def settlement(self, order_id: str) -> Settlement:
        """Read what actually settled. This is the settlement feed's ground truth."""
        return self._refunds.get(order_id, Settlement(False, 0.0, False, "not_found", None))

    def _roll(self) -> Optional[str]:
        for name in INJECTORS:
            rate = self.rates.get(name, 0.0)
            if rate > 0 and self.rng.random() < rate:
                return name
        return None
