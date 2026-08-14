"""Which Provy is this demo pointed at?

⛔ ONE ANSWER, BECAUSE TWO SCRIPTS ACT ON IT AND THEY ACT DIFFERENTLY. `wire_itsm_fleet.py` refuses
to run; `install_servicenow_lifecycle.py` silently rewrites the instance's `provy.ingest.url`. A
disagreement between them is not a cosmetic drift: one of them changes a live ServiceNow property.

⛔ AND IT MUST COMPARE THE HOSTNAME, NOT A SUBSTRING OF THE URL. The old test was
`any(h in url for h in PROD_HOSTS)`, and the pre-prod host is a SUPERSTRING of the production one:
"provy.ai" is inside "https://dev.provy.ai/api/ingest/outcome". So every pre-prod run through the
custom domain was refused as production, and the lifecycle installer would have "corrected" a
correctly-configured instance away from it.
"""

from urllib.parse import urlparse

# Hosts that serve production Provy. A demo pointed at one of these writes real outcomes into the
# real ledger, which is what happened on 2026-07-27.
PROD_HOSTS = frozenset({"provy.ai", "provyai.vercel.app"})

# Where a demo belongs. `dev.provy.ai` is the same deployment under the custom domain.
PREPROD_INGEST = "https://provydev.vercel.app/api/ingest/outcome"


def target_host(url: str) -> str:
    """The hostname a URL actually addresses, lowercased. Empty when it cannot be parsed.

    Accepts a bare host too, so a value copied without its scheme is still judged rather than
    silently treated as safe: urlparse("provy.ai/x") puts everything in `path` and leaves `hostname`
    as None, which would read as "not production".
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "//" in raw else f"//{raw}")
    return (parsed.hostname or "").lower()


def is_production_target(url: str) -> bool:
    """True when this URL addresses production Provy.

    Exact hostname match. `dev.provy.ai` is pre-prod and is not production, however much of the
    production host appears inside its name.
    """
    return target_host(url) in PROD_HOSTS
