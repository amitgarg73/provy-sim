"""Domain packs. Each implements the DomainPack interface on the shared engine."""
from .support.pack import SupportPack
from .stripe_support.pack import StripeSupportPack
from .claims.pack import ClaimsPack
from .crm.pack import CRMPack
from .travel.pack import TravelPack
from .revops.pack import RevOpsPack
from .claims_payout.pack import ClaimsPayoutPack
from .legal.pack import LegalPack

PACKS = {
    "support": SupportPack,
    "stripe_support": StripeSupportPack,
    "claims": ClaimsPack,
    "crm": CRMPack,
    "travel": TravelPack,
    "revops": RevOpsPack,
    "claims_payout": ClaimsPayoutPack,
    "legal": LegalPack,
}


def get_pack(name: str):
    if name not in PACKS:
        raise KeyError(f"unknown pack '{name}'. Known: {', '.join(PACKS)}")
    return PACKS[name]()


__all__ = ["SupportPack", "StripeSupportPack", "ClaimsPack", "CRMPack", "TravelPack",
           "RevOpsPack", "ClaimsPayoutPack", "LegalPack", "PACKS", "get_pack"]
