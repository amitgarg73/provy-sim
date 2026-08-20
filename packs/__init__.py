"""Domain packs. Each implements the DomainPack interface on the shared engine."""
from .support.pack import SupportPack
from .stripe_support.pack import StripeSupportPack
from .claims.pack import ClaimsPack
from .crm.pack import CRMPack
from .travel.pack import TravelPack
from .revops.pack import RevOpsPack
from .claims_payout.pack import ClaimsPayoutPack
from .legal.pack import LegalPack
from .itsm.pack import ItsmPack
from .edwin.pack import EdwinPack
from .teameight.pack import TeameightPack
from .claude_code.pack import ClaudeCodePack

PACKS = {
    "support": SupportPack,
    "stripe_support": StripeSupportPack,
    "claims": ClaimsPack,
    "crm": CRMPack,
    "travel": TravelPack,
    "revops": RevOpsPack,
    "claims_payout": ClaimsPayoutPack,
    "legal": LegalPack,
    "edwin": EdwinPack,
    "teameight": TeameightPack,
    "claude_code": ClaudeCodePack,
    # The only fleet whose work items and outcomes come from a system this
    # simulation does not own.
    "itsm": ItsmPack,
}


def get_pack(name: str):
    if name not in PACKS:
        raise KeyError(f"unknown pack '{name}'. Known: {', '.join(PACKS)}")
    return PACKS[name]()


__all__ = ["SupportPack", "StripeSupportPack", "ClaimsPack", "CRMPack", "TravelPack",
           "RevOpsPack", "ClaimsPayoutPack", "LegalPack", "EdwinPack", "ItsmPack",
           "TeameightPack", "ClaudeCodePack",
           "PACKS", "get_pack"]
