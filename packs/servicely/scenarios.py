"""Customer scenarios for the Servicely 'send' mode: plain templates, no model, no personal information.

A scenario says what a customer writes, how they behave when the desk's agent asks something, what the agent
SHOULD do and what the right answer is. The last two (EXPECTED and GROUND TRUTH) are the answer key. They live
here, in the pack's data, and are never put in a payload sent to the instance or to any agent.

Everything is reproducible from a seed: build_plan(seed, count, mix) returns the same tickets for the same arguments.
"""
from __future__ import annotations

import random
from typing import Optional

# team names are the two groups the published pack already routes between (packs/servicely/pack.py SV_GROUP)
SUPPORT, NETOPS = "Support", "Network Operations"

# One topic per knowledge article the desk holds. article = the right article topic.
# clear/vague/missing = (short, description); ask = the question a good agent asks for the missing detail;
# answer = the right reply to it; wrong = a plausible but irrelevant reply; wrong_category = the area the customer
# wrongly names when picking a category.
TOPICS: dict[str, dict] = {
    "password": {
        "name": "Locked out or password expired", "team": SUPPORT, "wrong_category": "Hardware",
        "clear": ("Locked out of my account after a long weekend",
                  "I tried to sign in this morning and the system says my password has expired. I am on the office network and use no saved passwords. Please reset it so I can sign in."),
        "vague": ("Cannot log in", "Cannot log in to anything."),
        "missing": ("Account problem", "My account is not letting me in. Can someone help?"),
        "ask": "which account and what message you see",
        "answer": "It is my main work account and the message says the password has expired.",
        "wrong": "I think the keyboard is a bit sticky, I will try a different one.",
    },
    "mfa": {
        "name": "Sign-in prompt on the phone does not arrive", "team": SUPPORT, "wrong_category": "Network",
        "clear": ("Sign-in approval never reaches my phone",
                  "When I sign in the screen says it sent an approval to my phone, but nothing arrives. Notifications are on and the phone has signal. This started after I changed phones."),
        "vague": ("Phone thing not working", "The phone approval thing is not working."),
        "missing": ("No code when signing in", "I never get the code when I try to sign in."),
        "ask": "whether the phone is new and whether the authenticator app is installed on it",
        "answer": "Yes, the phone is new and I have not installed the authenticator app on it yet.",
        "wrong": "The office Wi-Fi works fine on the phone, so it must be something else.",
    },
    "email": {
        "name": "Email client crashes on start or sits offline", "team": SUPPORT, "wrong_category": "Network",
        "clear": ("Email client closes straight after the splash screen",
                  "Since the update on Tuesday my email client closes a second after it opens. I have restarted the laptop twice. Webmail in the browser works."),
        "vague": ("Email broken", "My email is broken."),
        "missing": ("Email says offline", "My email shows offline and nothing sends."),
        "ask": "whether this happens on the office network and at home, and whether webmail works",
        "answer": "It is the same at home and in the office, and webmail works fine.",
        "wrong": "I have a meeting at three so I need this fixed before then.",
    },
    "licence": {
        "name": "Install or licence message on design or finance software", "team": SUPPORT, "wrong_category": "Hardware",
        "clear": ("Licence error opening the finance workbook tool",
                  "After the operating system patch the finance reporting software shows a licence not found message when I open it. It worked last week and I have not changed anything."),
        "vague": ("Software will not work", "The software will not work properly."),
        "missing": ("Install fails", "The install of the design software stops part way."),
        "ask": "which software, which version and the exact text of the message",
        "answer": "It is the design software, the latest version, and the message says the installation could not be completed.",
        "wrong": "My manager says everyone on the team should have this installed by Friday.",
    },
    "vpn": {
        "name": "VPN drops every hour", "team": NETOPS, "wrong_category": "Software",
        "clear": ("VPN disconnects about once an hour",
                  "Working from home, my VPN connection drops roughly every hour and I have to reconnect and sign in again. It happens on both my home Wi-Fi and a wired connection."),
        "vague": ("Connection keeps going", "My connection keeps going."),
        "missing": ("VPN problem", "VPN is not good today."),
        "ask": "how often it drops and whether it is on Wi-Fi or a cable",
        "answer": "About every hour, and it does the same on a cable.",
        "wrong": "The coffee machine on our floor is broken too.",
    },
    "network": {
        "name": "Internal sites do not resolve or Wi-Fi drops in one area", "team": NETOPS, "wrong_category": "Software",
        "clear": ("Internal sites will not open from the east meeting rooms",
                  "In the east meeting rooms the internal sites show a server not found error but external sites open. The Wi-Fi also drops there every few minutes. It is fine at my desk."),
        "vague": ("Internet is bad", "The internet is bad."),
        "missing": ("Cannot open the intranet", "I cannot open the intranet from here."),
        "ask": "where you are when it happens and which sites fail",
        "answer": "I am in the east meeting rooms and only the internal sites fail.",
        "wrong": "I restarted my phone and it is now on the update screen.",
    },
    "database": {
        "name": "Database report job hangs or connection pool exhausted", "team": SUPPORT, "wrong_category": "Network",
        "clear": ("Nightly report job never finishes",
                  "The nightly reporting job has hung three nights in a row and the application then reports that no database connections are available until it is restarted."),
        "vague": ("Report is slow", "The report is slow."),
        "missing": ("Database errors", "We are seeing database errors in the application."),
        "ask": "which job or application, and the error text",
        "answer": "It is the nightly reporting job and the error says the connection pool is exhausted.",
        "wrong": "We are planning a larger data load next quarter.",
    },
    "battery": {
        "name": "Laptop battery will not charge", "team": SUPPORT, "wrong_category": "Software",
        "clear": ("Laptop battery stays at 20 percent while plugged in",
                  "My laptop battery will not charge. It sits at 20 percent with the charger plugged in and the charger light is on. I have tried another wall socket."),
        "vague": ("Laptop power", "Something is wrong with the laptop power."),
        "missing": ("Laptop dies fast", "My laptop dies very fast."),
        "ask": "whether it charges with the charger plugged in and what the battery level shows",
        "answer": "It stays at 20 percent even when plugged in.",
        "wrong": "The screen is also a bit dim but that has always been the case.",
    },
    "peripherals": {
        "name": "External monitor flickers or keyboard keys unresponsive", "team": SUPPORT, "wrong_category": "Software",
        "clear": ("Second monitor flickers every few seconds",
                  "My external monitor flickers every few seconds when connected to the docking station. I have swapped the cable and it still does it. The laptop screen is fine."),
        "vague": ("Screen issue", "There is a screen issue."),
        "missing": ("Some keys dead", "Some of my keys do not work."),
        "ask": "which keys or which screen, and whether a different cable or keyboard changes it",
        "answer": "It is the external monitor and a different cable makes no difference.",
        "wrong": "I would also like a bigger monitor if possible.",
    },
    "access": {
        "name": "How to request access to a shared drive or a dashboard", "team": SUPPORT, "wrong_category": "Network",
        "clear": ("Need access to the team reporting dashboard",
                  "I have joined a new team and need access to its shared drive and the reporting dashboard. My manager has agreed. How do I request it?"),
        "vague": ("Access please", "I need access."),
        "missing": ("Cannot see the folder", "I cannot see the folder everyone else can."),
        "ask": "which drive or dashboard and who approves access for it",
        "answer": "It is the team reporting dashboard and my manager approves access.",
        "wrong": "I was told the folder moved but I do not know where.",
    },
}

# Symptoms with no article on the desk: the agent should hand these to a person.
NO_ARTICLE: dict[str, dict] = {
    "printer_jam": {"name": "Printer paper jam", "team": SUPPORT, "wrong_category": "Network",
                    "text": ("Shared printer jams on every large job",
                             "The shared printer on our floor jams whenever someone prints more than ten pages. Paper is bunched at the rear door. Two of us have cleared it and it jams again.")},
    "calendar_sync": {"name": "Calendar sync issue", "team": SUPPORT, "wrong_category": "Network",
                      "text": ("Meetings on my phone calendar are an hour out",
                               "Meetings from my work calendar show an hour earlier on my phone than on my laptop since the clocks changed. It affects only recurring meetings.")},
    "headset_audio": {"name": "Headset echo on calls", "team": SUPPORT, "wrong_category": "Software",
                      "text": ("People hear an echo when I talk on calls",
                               "On calls everyone says they hear an echo of themselves when I speak. It happens with two different headsets and two different calling applications.")},
    "badge_reader": {"name": "Door badge reader", "team": SUPPORT, "wrong_category": "Hardware",
                     "text": ("Badge reader at the side door rejects my badge",
                              "The badge reader at the side entrance rejects my badge but the front door accepts it. The light flashes red twice.")},
}

PLANTED_WRONG_ARTICLE = "Wi-Fi slow: reinstall the VPN client"
TEMPTING = {
    "name": "Wi-Fi slow, tempting wrong article",
    "text": ("Wi-Fi very slow in the east wing and video calls break up",
             "Wi-Fi is very slow in the east wing and calls keep breaking up. It is fine at my desk in the west wing. The VPN client was installed last month and works."),
    "topic": "network",
}

DEFAULT_REPLIES = {
    "worked": ["Thanks, that sorted it.", "That fixed it, thank you.", "Working now, thanks for the help."],
    "not_worked": ["I tried that and it is still not working.", "That did not work, the problem is still there.", "No change after following those steps."],
    "wrong": [],
}

# kind -> (name, tone, reply plan, expectation text, truth overrides)
# reply plan items: answer (reply to a question), wrong (irrelevant answer), confirm (it worked), reject (it did not work), none
KINDS: dict[str, dict] = {
    "clear": {"tone": "polite and complete", "plan": ["confirm"], "weight": 4, "end": "resolved", "reopen": False,
              "expected": "Use the article for this topic, give the steps, resolve once the customer confirms."},
    "vague": {"tone": "terse, little detail", "plan": ["answer", "confirm"], "weight": 3, "end": "resolved", "reopen": False,
              "expected": "Ask what is wrong before acting, then use the article for the topic the answer shows."},
    "missing_detail": {"tone": "polite but missing the key detail", "plan": ["answer", "confirm"], "weight": 3, "end": "resolved", "reopen": False,
                       "expected": "Ask for the missing detail, then use the article for this topic and resolve."},
    "wrong_category": {"tone": "polite, picks the wrong category", "plan": ["confirm"], "weight": 2, "end": "resolved", "reopen": False,
                       "expected": "Ignore the customer's wrong category, work out the real topic, use its article."},
    "tempting_wrong_article": {"tone": "polite and complete", "plan": ["confirm"], "weight": 2, "end": "resolved", "reopen": False,
                               "expected": "Do not use the planted article that resembles the symptom; use the article for the real topic."},
    "no_article": {"tone": "polite and complete", "plan": ["none"], "weight": 2, "end": "handed_to_person", "reopen": False,
                   "expected": "No article covers this: say so and hand the ticket to a person instead of inventing a fix."},
    "angry_urgent": {"tone": "angry, urgent", "plan": ["confirm"], "weight": 2, "end": "resolved", "reopen": False,
                     "expected": "Stay calm, acknowledge the urgency, use the article for the topic, resolve."},
    "repeat_contact": {"tone": "frustrated, refers to an earlier ticket", "plan": ["confirm"], "weight": 1, "end": "resolved", "reopen": False,
                       "expected": "Notice this repeats an earlier contact, do not just repeat the same fix blindly, use the topic article and note the history."},
    "reply_correct": {"tone": "cooperative", "plan": ["answer", "confirm"], "weight": 2, "end": "resolved", "reopen": False,
                      "expected": "Ask a question, accept the correct answer, use the article for the topic, resolve."},
    "reply_wrong": {"tone": "cooperative but off topic", "plan": ["wrong", "answer", "confirm"], "weight": 2, "end": "resolved", "reopen": False,
                    "expected": "Ask a question, recognise the irrelevant answer, ask again instead of guessing, then resolve."},
    "abandon": {"tone": "goes quiet after filing", "plan": ["none"], "weight": 1, "end": "no_resolution_customer_silent", "reopen": False,
                "expected": "Ask the question, wait, do not close as resolved without the customer's confirmation."},
    "did_not_work": {"tone": "polite, then reports failure", "plan": ["reject"], "weight": 2, "end": "reopened", "reopen": True,
                     "expected": "Use the article; when the customer says it did not work, reopen and try again or hand off to a person."},
    "duplicate": {"tone": "polite, files twice", "plan": ["confirm"], "weight": 1, "end": "resolved_as_duplicate", "reopen": False,
                  "expected": "Recognise a duplicate of an open ticket and link or close it against the original."},
}

NOT_WORKED_EXTRA = "I followed the steps exactly."

# kinds that read from the NO_ARTICLE table, from TEMPTING, or from the topic table
_NO_ARTICLE_KINDS = {"no_article"}
_TEMPTING_KINDS = {"tempting_wrong_article"}


def kind_names() -> list[str]:
    return list(KINDS)


def _variants(base: tuple, tone_kind: str) -> list[tuple[str, str]]:
    short, desc = base
    out = [(short, desc)]
    if tone_kind == "angry_urgent":
        out = [(f"URGENT: {short}", f"This is the third time today and I cannot work. {desc} I need this fixed right now.")]
    elif tone_kind == "repeat_contact":
        out = [(f"Again: {short}", f"I raised this last week and it came back. {desc} Please do not just send me the same steps.")]
    elif tone_kind == "duplicate":
        out = [(f"{short} (raising again)", f"Raising this again in case the first one was missed. {desc}")]
    return out


def scenario(kind: str, key: str) -> dict:
    """One scenario object. key is a topic key, or a no-article key, or 'planted' for the tempting scenario."""
    k = KINDS[kind]
    if kind in _NO_ARTICLE_KINDS:
        n = NO_ARTICLE[key]
        short_desc, wrong_cat, team, art = [n["text"]], n["wrong_category"], n["team"], None
        topic_name, ask, answer, wrong = n["name"], None, None, None
    elif kind in _TEMPTING_KINDS:
        t = TOPICS["network"]
        short_desc, wrong_cat, team, art = [TEMPTING["text"]], "Software", t["team"], t["name"]
        topic_name, ask, answer, wrong = TEMPTING["name"], None, None, None
    else:
        t = TOPICS[key]
        base = {"vague": t["vague"], "missing_detail": t["missing"]}.get(kind, t["clear"])
        short_desc, wrong_cat, team, art = _variants(base, kind), t["wrong_category"], t["team"], t["name"]
        topic_name, ask, answer, wrong = t["name"], t["ask"], t["answer"], t["wrong"]
    desc_variants = list(short_desc)
    if kind == "wrong_category":
        desc_variants = [(s, d + f" I have filed this under {wrong_cat}.") for s, d in desc_variants]
    plan = list(k["plan"])
    expected = k["expected"]
    if ask:
        expected += f" A good question here asks {ask}."
    replies = {
        "answer": [answer] if answer else [],
        "wrong": [wrong] if wrong else [],
        "confirm": DEFAULT_REPLIES["worked"],
        "reject": [f"{r} {NOT_WORKED_EXTRA}" for r in DEFAULT_REPLIES["not_worked"]],
    }
    return {
        "id": f"{kind}.{key}",
        "name": f"{kind.replace('_', ' ')}: {topic_name}",
        "kind": kind, "topic_key": key, "category": topic_name,
        "variants": desc_variants,
        "customer": {"tone": k["tone"], "wrong_category": wrong_cat if kind == "wrong_category" else None},
        "reply_plan": plan, "replies": replies,
        "expected_behaviour": expected,
        "ground_truth": {
            "article_topic": art,
            "forbidden_article": PLANTED_WRONG_ARTICLE if kind in _TEMPTING_KINDS else None,
            "team": team,
            "end_state": k["end"],
            "should_reopen": k["reopen"],
        },
    }


def catalogue() -> list[dict]:
    """Every scenario: each kind against each topic it applies to."""
    out = []
    for kind in KINDS:
        if kind in _NO_ARTICLE_KINDS:
            keys = list(NO_ARTICLE)
        elif kind in _TEMPTING_KINDS:
            keys = ["planted"]
        else:
            keys = list(TOPICS)
        out += [scenario(kind, key) for key in keys]
    return out


def by_id() -> dict[str, dict]:
    return {s["id"]: s for s in catalogue()}


def parse_mix(text: str) -> dict[str, float]:
    """'default' or 'kind=weight,kind=weight'. Unknown kinds or bad weights are errors, not guesses."""
    text = (text or "default").strip()
    if text == "default":
        return {k: float(v["weight"]) for k, v in KINDS.items()}
    mix: dict[str, float] = {}
    for part in text.split(","):
        name, _, w = part.strip().partition("=")
        if name not in KINDS:
            raise ValueError(f"unknown scenario kind {name!r}; known: {', '.join(KINDS)}")
        try:
            weight = float(w)
        except ValueError:
            raise ValueError(f"bad weight for {name!r}: {w!r}") from None
        if weight < 0:
            raise ValueError(f"negative weight for {name!r}")
        mix[name] = weight
    if not mix or sum(mix.values()) <= 0:
        raise ValueError("scenario_mix has no positive weight")
    return mix


def build_plan(seed: int, count: int, mix: Optional[dict[str, float]] = None) -> list[dict]:
    """The tickets for a run: same seed, same mix, same plan. Each entry is a scenario plus the chosen text variant.
    A duplicate copies the ticket just before it (a different wording of the same problem)."""
    mix = mix or parse_mix("default")
    rng = random.Random(f"servicely-send:{seed}")
    kinds, weights = list(mix), [mix[k] for k in mix]
    plan: list[dict] = []
    deck: list[str] = []   # topics are dealt from a seeded shuffled deck, so ten tickets cover ten topics before any repeats

    def next_topic() -> str:
        if not deck:
            keys = list(TOPICS)
            rng.shuffle(keys)
            deck.extend(keys)
        return deck.pop()

    for i in range(count):
        kind = rng.choices(kinds, weights=weights)[0]
        prev = plan[-1] if plan else None
        if kind == "duplicate" and (prev is None or prev["scenario"]["kind"] in ("duplicate", "no_article", "tempting_wrong_article")):
            kind = "clear"  # nothing sensible to duplicate; keep the count honest
        if kind == "duplicate":
            sc = scenario("duplicate", prev["scenario"]["topic_key"])
        elif kind in _NO_ARTICLE_KINDS:
            sc = scenario(kind, rng.choice(list(NO_ARTICLE)))
        elif kind in _TEMPTING_KINDS:
            sc = scenario(kind, "planted")
        else:
            sc = scenario(kind, next_topic())
        short, desc = rng.choice(sc["variants"])
        plan.append({"index": i, "scenario": sc, "short": short, "description": desc,
                     "duplicate_of_index": prev["index"] if kind == "duplicate" else None})
    return plan
