"""Servicely 'send' mode: raise tickets and play the customer, so the desk's OWN agents do the work.

Unlike the normal Servicely pack this runs none of our simulated agents. It only
  1. creates incidents marked with the desk marker, assigned straight to the front-door agent user,
  2. plays the customer when a ticket waits on the customer (scripted replies, seeded delays),
  3. records everything in a run ledger (JSON lines) so a clean-up is possible.

Safety, in the order it is applied:
  * dry_run defaults to true: prints what it would create, builds no client, makes no network call.
  * count is capped at 25 per dispatch (150 with confirm_large=yes, never more).
  * before creating anything it reads the spend state and the number of still-open tickets this tool raised, and
    refuses if headroom is too low, cannot be computed, or too many are open.
  * a request is retried at most twice (the client's own backoff); the first failure that survives that stops the
    run, reports what was created and exits non-zero. There is no loop around a failure.
  * the answer key (expected behaviour and ground truth) is never in a payload; it is looked up from the scenario id.

Inputs arrive as INPUT_<NAME> environment variables set by .github/workflows/run-servicely-send.yml.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from engine.servicely import MARKER, ServicelyClient, ServicelyError
from packs.servicely import scenarios as sc

# Every input the workflow declares. tests/test_servicely_send.py asserts the YAML declares exactly these.
INPUT_DEFAULTS: dict[str, str] = {
    "count": "10",
    "confirm_large": "no",
    "dry_run": "true",
    "seed": "1",
    "scenario_mix": "default",
    "concurrency": "5",
    "arrival_gap": "30",
    "settle_wait": "30",
    "max_open": "15",
    "min_headroom_usd": "100",
    "max_exchanges": "2",
    "close_delay": "45",
    "agent_user": "",
    "requested_for": "",
    "run_label": "",
    "headroom_override_usd": "",
    "usage_table": "SystemLLMUsage",
    "usage_cost_field": "ApproximateCost",
}

DEFAULT_CAP, LARGE_CAP = 25, 150
AWAITING = {"awaiting_client_action", "awaiting_client", "pending_client"}
DONE = {"resolved", "closed", "cancelled", "canceled"}
SPEND_RULE_TABLE = "SystemAISpendRule"
DEFAULT_MONTHLY_CAP = 1000.0
USAGE_PAGE = 200   # the instance returns at most 200 rows per read, whatever page_size says (checked 26 Oct); more come with offset
USAGE_MAX_PAGES = 25  # 5,000 rows; a month with more than that is refused, not guessed
POLL_SECONDS = 15.0


class Refused(RuntimeError):
    """Nothing was created: a rule said no."""


class Stopped(RuntimeError):
    """A request failed after its retries: stop, report, exit non-zero."""


# ── inputs ──────────────────────────────────────────────────────────────────
def read_inputs(env: dict) -> dict:
    optional = ("agent_user", "requested_for", "run_label", "headroom_override_usd", "usage_table")  # an empty value means none
    raw = {}
    for k, d in INPUT_DEFAULTS.items():
        v = env.get(f"INPUT_{k.upper()}")
        v = d if v is None else str(v).strip()
        raw[k] = v if (v or k in optional) else d

    def num(k: str, kind=int, lo=0):
        try:
            v = kind(raw[k])
        except ValueError:
            raise Refused(f"input {k} must be a number, got {raw[k]!r}") from None
        if v < lo:
            raise Refused(f"input {k} must be at least {lo}")
        return v

    cfg = {
        "count": num("count", int, 1), "seed": num("seed", int, -10**9),
        "concurrency": num("concurrency", int, 1), "arrival_gap": num("arrival_gap", float, 0),
        "settle_wait": num("settle_wait", float, 0), "max_open": num("max_open", int, 0),
        "min_headroom_usd": num("min_headroom_usd", float, 0), "max_exchanges": num("max_exchanges", int, 0),
        "close_delay": num("close_delay", float, 0),
        "confirm_large": raw["confirm_large"].lower() == "yes",
        "dry_run": raw["dry_run"].lower() not in ("false", "no", "0"),
        "scenario_mix": raw["scenario_mix"], "agent_user": raw["agent_user"], "requested_for": raw["requested_for"], "run_label": raw["run_label"],
        "usage_table": raw["usage_table"], "usage_cost_field": raw["usage_cost_field"],
        "headroom_override_usd": None,
    }
    if raw["headroom_override_usd"]:
        cfg["headroom_override_usd"] = num("headroom_override_usd", float, 0)
    check_count(cfg["count"], cfg["confirm_large"])
    return cfg


def check_count(count: int, confirm_large: bool) -> None:
    if count > LARGE_CAP:
        raise Refused(f"count {count} is above the absolute cap of {LARGE_CAP}")
    if count > DEFAULT_CAP and not confirm_large:
        raise Refused(f"count {count} is above {DEFAULT_CAP}: set confirm_large=yes to send up to {LARGE_CAP}")


# ── spend guard ─────────────────────────────────────────────────────────────
def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def monthly_cap(rules: list[dict]) -> tuple[float, str]:
    """The instance monthly cap from the spend rules if a numeric field names a month, else the known 1000."""
    for r in rules or []:
        for k, v in r.items():
            if "month" in k.lower() and _num(v) is not None and _num(v) > 0:
                return float(v), "rule"
    return DEFAULT_MONTHLY_CAP, "default"


def compute_headroom(client, cfg: dict, now: Optional[datetime] = None) -> dict:
    """Remaining model-spend headroom in USD. Raises Refused when it cannot be worked out, unless an explicit override is given.
    Read-only: GET on the spend rules and on the usage table named by usage_table."""
    rules = client.query(SPEND_RULE_TABLE, {}, page_size=100) or []
    cap, cap_src = monthly_cap(rules)
    if cfg.get("headroom_override_usd") is not None:
        return {"headroom_usd": cfg["headroom_override_usd"], "cap_usd": cap, "cap_source": cap_src, "basis": "override"}
    table = cfg.get("usage_table")
    if not table:
        raise Refused("headroom cannot be computed: no usage_table given. Name the usage table, or pass headroom_override_usd to proceed on your own figure")
    month = (now or datetime.now(timezone.utc)).strftime("%Y-%m")
    rows: list = []
    for page in range(USAGE_MAX_PAGES):
        got = client.query(table, {"order_desc": "CreatedOn", "offset": page * USAGE_PAGE}, page_size=USAGE_PAGE) or []
        rows.extend(got)
        # newest first: stop at a short page (no more rows) or when the page already reaches back before this month
        if len(got) < USAGE_PAGE or not str(got[-1].get("CreatedOn", "")).startswith(month):
            break
    else:
        raise Refused(f"headroom cannot be computed: more than {USAGE_MAX_PAGES * USAGE_PAGE} usage rows this month, so the total would be too low")
    field = cfg.get("usage_cost_field") or "Cost"
    spent = 0.0
    for r in rows:
        if not str(r.get("CreatedOn", "")).startswith(month):
            continue
        v = _num(r.get(field))
        if v is None:
            # an embedding call carries no tokens and no cost: that is zero, not a gap. A row that used tokens and has no cost IS a gap.
            if sum(_num(r.get(k)) or 0 for k in ("InputTokens", "OutputTokens", "ReasoningTokens", "CachedInputTokens", "CacheWriteTokens")) == 0:
                continue
            raise Refused(f"headroom cannot be computed: usage row used tokens but has no numeric {field}")
        spent += v
    return {"headroom_usd": round(cap - spent, 4), "cap_usd": cap, "cap_source": cap_src, "spent_usd": round(spent, 4), "basis": "usage_table"}


def _status(row: Any) -> str:
    if isinstance(row, list):
        row = row[0] if row else {}
    ws = (row or {}).get("WorkflowStatus")
    return str(ws.get("value") if isinstance(ws, dict) else ws or "").lower()


def count_open(client, limit: int = 500) -> int:
    """Open tickets this tool raised (marker on the short description)."""
    return sum(1 for r in client.demo_incidents(limit) if _status(r) not in DONE)


def guard(client, cfg: dict) -> dict:
    head = compute_headroom(client, cfg)
    if head["headroom_usd"] < cfg["min_headroom_usd"]:
        raise Refused(f"spend headroom {head['headroom_usd']} USD is under min_headroom_usd {cfg['min_headroom_usd']}")
    open_n = count_open(client)
    if open_n > cfg["max_open"]:
        raise Refused(f"{open_n} tickets raised by this tool are still open, more than max_open {cfg['max_open']}")
    return {**head, "open_before": open_n}


# ── ledger ──────────────────────────────────────────────────────────────────
class Ledger:
    """JSON lines. Line 1 is the run header, then one line per ticket. Rewritten whole on every change (small)."""

    def __init__(self, path: str, header: dict):
        self.path, self.header, self.tickets = path, header, []

    def add(self, rec: dict) -> dict:
        self.tickets.append(rec)
        self.write()
        return rec

    def write(self) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "w") as f:
            f.write(json.dumps({"run": self.header}, sort_keys=True) + "\n")
            for t in self.tickets:
                f.write(json.dumps(t, sort_keys=True) + "\n")


# ── the run ─────────────────────────────────────────────────────────────────
def _payload(item: dict, cfg: dict, ids: dict) -> dict:
    """The only thing that ever leaves for the instance: text, people, routing. No scenario id, no answer key."""
    # The customer is the API user itself: a reply only moves a ticket when the person replying is the ticket's RequestedFor.
    who = cfg.get("requested_for") or ids["requestor"]
    return {"short": item["short"], "description": item["description"], "requestor_id": who,
            "requested_for_id": who, "classification_id": ids["classification"],
            "assignment_group_id": ids["group"], "extra": {"Assignee": cfg["agent_user"], "Impact": "medium", "Urgency": "medium"}}


def _directory(client) -> dict:
    """Requestor, classification and group, read the way the pack does (packs/servicely/pack.py _ids)."""
    from packs.servicely.pack import ServicelyPack
    d = ServicelyPack(client)._ids()
    return {"requestor": d["requestor"], "classification": d["classification"], "group": d["groups"]["Support"]}


def _rec_of(row: Any) -> dict:
    return row[0] if isinstance(row, list) and row else (row if isinstance(row, dict) else {})


def run(cfg: dict, out_path: str, client: Optional[ServicelyClient] = None,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
        poll: float = POLL_SECONDS) -> int:
    mix = sc.parse_mix(cfg["scenario_mix"])
    plan = sc.build_plan(cfg["seed"], cfg["count"], mix)
    label = cfg["run_label"] or ("dry_run" if cfg["dry_run"] else f"send-seed{cfg['seed']}")
    header = {"label": label, "dry_run": cfg["dry_run"], "seed": cfg["seed"], "count": cfg["count"], "marker": MARKER,
              "scenario_mix": cfg["scenario_mix"], "max_exchanges": cfg["max_exchanges"],
              "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if cfg["dry_run"]:
        header["label"] = "dry_run" + (f":{cfg['run_label']}" if cfg["run_label"] else "")
    led = Ledger(out_path, header)

    if cfg["dry_run"]:
        for it in plan:
            rec = {"label": "dry_run", "would_create": True, "index": it["index"], "scenario_id": it["scenario"]["id"],
                   "seed": cfg["seed"], "short": f"{MARKER} {it['short']}", "reply_plan": it["scenario"]["reply_plan"][: cfg["max_exchanges"]]}
            led.add(rec)
            print(f"DRY RUN would create {rec['short']!r}  [{rec['scenario_id']}]")
        led.write()
        print(f"DRY RUN: {len(plan)} tickets listed, none created, no network call made.")
        return 0

    if not cfg["agent_user"] or not cfg["requested_for"]:
        print("REFUSED: agent_user and requested_for are required for a real run", file=sys.stderr)
        header["refused"] = "agent_user and requested_for are required for a real run"
        led.write()
        return 2
    client = client or ServicelyClient()
    try:
        client.require()
        header["guard"] = guard(client, cfg)
        ids = _directory(client)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        header["refused"] = str(e)
        led.write()
        return 2
    except ServicelyError as e:
        print(f"STOPPED before creating anything: {e}", file=sys.stderr)
        header["stopped"] = str(e)[:300]
        led.write()
        return 1
    led.write()
    try:
        _loop(client, cfg, plan, ids, led, sleep, clock, poll)
    except Stopped as e:
        header["stopped"] = str(e)[:300]
        led.write()
        print(f"STOPPED: {e}. Created {len(led.tickets)} of {cfg['count']}; see the ledger.", file=sys.stderr)
        return 1
    header["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    led.write()
    print(f"done: created {len(led.tickets)}, ledger {out_path}")
    return 0


def _guarded(fn, *a, **k):
    try:
        return fn(*a, **k)
    except ServicelyError as e:
        raise Stopped(str(e)) from None


def _loop(client, cfg, plan, ids, led: Ledger, sleep, clock, poll) -> None:
    rng = random.Random(f"servicely-send-replies:{cfg['seed']}")
    start = clock()
    next_create = start
    nxt = 0
    live: list[dict] = []          # ledger records still being watched
    state: dict[str, dict] = {}    # per ticket: reply bookkeeping
    last_created = None
    hard_stop = start + cfg["count"] * cfg["arrival_gap"] + cfg["settle_wait"] * 60 + 60

    while True:
        now = clock()
        in_flight = [t for t in led.tickets if not t.get("final_state")]
        if nxt < len(plan) and now >= next_create and len(in_flight) < cfg["concurrency"]:
            item = plan[nxt]
            body = _payload(item, cfg, ids)
            row = _rec_of(_guarded(client.create_incident, body["short"], body["requestor_id"], body["requested_for_id"],
                                   body["classification_id"], body["assignment_group_id"],
                                   description=body["description"], **body["extra"]))
            rec = led.add({"ticket_number": row.get("Number"), "ticket_id": row.get("id"), "scenario_id": item["scenario"]["id"],
                           "seed": cfg["seed"], "index": item["index"], "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "replies_played": [], "final_state": None, "observed_state": None})
            print(f"CREATED {rec['ticket_number']} [{rec['scenario_id']}]", flush=True)  # the console page reads these lines from the run log
            state[rec["ticket_id"]] = {"exchanges": 0, "due": None, "need_leave": False, "plan": list(item["scenario"]["reply_plan"]),
                                       "scn": item["scenario"]}
            nxt += 1
            last_created = clock()
            next_create = last_created + cfg["arrival_gap"]
            continue
        # watch tickets
        for rec in in_flight:
            _watch(client, cfg, rec, state[rec["ticket_id"]], rng, clock, led)
        all_created = nxt >= len(plan)
        settle_over = last_created is not None and all_created and clock() >= last_created + cfg["settle_wait"] * 60
        if all_created and not [t for t in led.tickets if not t.get("final_state")]:
            break
        if settle_over or clock() >= hard_stop:
            for rec in led.tickets:
                if not rec.get("final_state"):
                    st = state[rec["ticket_id"]]
                    rec["final_state"] = "abandoned" if (st["plan"] and st["plan"][0] == "none") or rec["observed_state"] in AWAITING else (rec["observed_state"] or "unknown")
                    if rec["final_state"] == "abandoned":
                        rec["abandoned"] = True
            led.write()
            break
        wait = poll
        if nxt < len(plan) and len(in_flight) < cfg["concurrency"]:
            wait = min(poll, max(0.0, next_create - clock()))
        sleep(wait)


def _watch(client, cfg, rec: dict, st: dict, rng: random.Random, clock, led: Ledger) -> None:
    row = _rec_of(_guarded(client.get_incident, rec["ticket_id"]))
    status = _status(row)
    rec["observed_state"] = status
    if st["need_leave"] and status not in AWAITING:
        st["need_leave"] = False
    if status in DONE:
        rec["final_state"] = status
        st["close_due"] = None
        led.write()
        return
    if st.get("close_due") is not None and clock() >= st["close_due"]:
        st["close_due"] = None
        _guarded(client.update, "Incident", rec["ticket_id"], {"ClosureCode": "Solution provided"})
        led.write()
        return
    # the planned reply, if the ticket is waiting on the customer and the budget allows
    if status in AWAITING and not st["need_leave"] and st["exchanges"] < cfg["max_exchanges"]:
        step = st["plan"][st["exchanges"]] if st["exchanges"] < len(st["plan"]) else "none"
        if step == "none":
            led.write()
            return
        if st["due"] is None:
            st["due"] = clock() + rng.uniform(20, 120)
        if clock() >= st["due"]:
            text = rng.choice(st["scn"]["replies"][step] or [""])
            _guarded(client.update, "Incident", rec["ticket_id"], {"ClientJournal": text})
            if step == "confirm":
                # 26 Oct: the reply and the closure code used to be written back to back. Servicely's agent runtime wakes the agent on the
                # reply and moves the ticket 'Continue work' (resolved -> in progress) one second later, so the ticket was resolved twice.
                # A person who confirms does not close the ticket in the same second: hold the closure code until the agent has reacted.
                if cfg["close_delay"] <= 0:
                    _guarded(client.update, "Incident", rec["ticket_id"], {"ClosureCode": "Solution provided"})
                else:
                    st["close_due"] = clock() + cfg["close_delay"]
            st["exchanges"] += 1
            st["due"], st["need_leave"] = None, True
            rec["replies_played"].append({"kind": step, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    led.write()


def main(argv=None, env: Optional[dict] = None, client=None, sleep=time.sleep) -> int:
    env = dict(os.environ if env is None else env)
    try:
        cfg = read_inputs(env)
    except (Refused, ValueError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    out = (argv or [None])[0] or env.get("LEDGER_PATH") or "servicely-send-ledger.jsonl"
    try:
        return run(cfg, out, client=client, sleep=sleep)
    except ValueError as e:  # bad scenario_mix
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
