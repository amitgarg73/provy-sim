"""The Multi Vendor Scenario set: one identity change (a leaver) that has to land in systems owned by different vendors.

The agent runs in one vendor's platform (Servicely). The systems it changes sit in another vendor's (a ServiceNow PDI standing in for the
directory, single sign-on, the VPN and a SaaS token). The agent's claim is always the same, "all four done". The truth lives in the other vendor's
estate, and a scenario is one way that truth can differ from the claim.

⛔ SCENARIOS ARE DATA. Adding a case is adding a row here, never a branch in the runner. The estate injects faults through one JSON property
(`provy.iam.fault`), so every case below is a fault map plus what a correct reading looks like afterwards.
⛔ GENERIC: nothing here is specific to a customer. The two vendors are named only in the README, as the stand-ins this POC uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SYSTEMS = ["directory", "sso", "vpn", "saas_token"]


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    person: str = "provy.iam.spike01"
    command: str = "provy-iam-apply-change"
    systems: tuple[str, ...] = tuple(SYSTEMS)
    fault: dict = field(default_factory=dict)          # provy.iam.fault, e.g. {"vpn": "accept_not_apply"}
    resync: bool = False                               # the HR sync runs afterwards and re-creates the account
    should_hold: bool = True                           # does the access really end? False means Provy should NOT call this settled
    why: str = ""                                      # one sentence for the report


SCENARIOS: list[Scenario] = [
    Scenario("clean_leaver", "Clean leaver: every system changes", should_hold=True,
             why="The claim is true. The control case: a check that flags this is wrong."),
    Scenario("vpn_missed", "One system says done and does nothing (VPN)", fault={"vpn": "accept_not_apply"}, should_hold=False,
             why="The VPN answers OK and keeps the access. The agent reports all four done."),
    Scenario("directory_missed", "The directory itself says done and does nothing", fault={"directory": "accept_not_apply"}, should_hold=False,
             why="The account stays active. The most visible miss, and the one an agent's own status check would catch if it looked."),
    Scenario("recreated_by_sync", "Clean leaver, then an HR sync re-creates the account", resync=True, should_hold=False,
             why="Every command worked at the time. A later sync from the source of truth brought the account back."),
]

BY_KEY = {s.key: s for s in SCENARIOS}
