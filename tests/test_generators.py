"""Each pack's generator must produce internally-consistent ground truth."""
import random

from packs import get_pack
from packs.support.pack import CATEGORIES, _ACTION
from packs.claims.pack import CLAIM_TYPES
from packs.crm.pack import TERRITORIES


def test_support_ground_truth_consistent():
    pack = get_pack("support")
    rng = random.Random(7)
    for _ in range(300):
        item, gt = pack.generate_work_item(rng)
        assert item["id"].startswith("TKT-")
        assert gt["category"] in CATEGORIES
        assert gt["category"] == item["category"]
        grant = _ACTION[gt["category"]][1]
        if gt["policy_allows"]:
            assert gt["correct_resolution"] == grant
        else:
            assert gt["correct_resolution"] in ("escalate_to_manager", "deny_with_reason")
        # complaints are never auto-granted by policy
        if gt["category"] == "complaint":
            assert gt["policy_allows"] is False


def test_claims_ground_truth_consistent():
    pack = get_pack("claims")
    rng = random.Random(11)
    for _ in range(300):
        item, gt = pack.generate_work_item(rng)
        assert item["id"].startswith("CLM-")
        assert item["claim_type"] in CLAIM_TYPES
        assert gt["within_limit"] == (item["amount"] <= item["policy_limit"])
        assert gt["valid"] == (gt["docs_complete"] and gt["within_limit"] and not gt["is_duplicate"])
        assert gt["correct_decision"] == ("approve" if gt["valid"] else "deny")


def test_crm_ground_truth_consistent():
    pack = get_pack("crm")
    rng = random.Random(13)
    for _ in range(300):
        item, gt = pack.generate_work_item(rng)
        assert item["id"].startswith("LEAD-")
        assert gt["qualification"] in ("MQL", "SQL", "unqualified")
        assert gt["correct_owner"] == item["territory"]
        assert gt["correct_owner"] in TERRITORIES


def test_generator_is_deterministic():
    for name in ("support", "claims", "crm"):
        a = get_pack(name).generate_work_item(random.Random(42))
        b = get_pack(name).generate_work_item(random.Random(42))
        assert a == b
