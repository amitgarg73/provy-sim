"""Domain packs. Each implements the DomainPack interface on the shared engine."""
from .support.pack import SupportPack
from .stripe_support.pack import StripeSupportPack
from .claims.pack import ClaimsPack
from .crm.pack import CRMPack

PACKS = {
    "support": SupportPack,
    "stripe_support": StripeSupportPack,
    "claims": ClaimsPack,
    "crm": CRMPack,
}


def get_pack(name: str):
    if name not in PACKS:
        raise KeyError(f"unknown pack '{name}'. Known: {', '.join(PACKS)}")
    return PACKS[name]()


__all__ = ["SupportPack", "StripeSupportPack", "ClaimsPack", "CRMPack", "PACKS", "get_pack"]
