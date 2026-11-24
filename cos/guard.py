"""The one address the Chief of Staff demo may send to: https://dev.provy.ai, and nothing else.

An allow-list of ONE exact host name. The URL is taken apart and every part is checked, so none of
the usual tricks get through: user-info ('https://dev.provy.ai@provy.ai'), suffix hosts
('dev.provy.ai.evil.com'), a missing scheme, plain http, an empty value, upper case, a trailing dot
and any port. Production host names are listed only so the refusal can say so in plain words; the
allow-list alone already refuses them.

Command line (the workflow uses it, and feeds it a production host on purpose to prove it refuses):
    python3 cos/guard.py https://dev.provy.ai     prints "OK https://dev.provy.ai", exit 0
    python3 cos/guard.py https://provy.ai         prints "Refused: ...", exit 1
"""
from __future__ import annotations

import sys
from urllib.parse import urlsplit

ALLOWED_HOST = "dev.provy.ai"
ALLOWED_URL = "https://dev.provy.ai"

# Named so the message can say "production". Never used to allow anything.
PRODUCTION_HOSTS = frozenset({
    "provy.ai", "www.provy.ai", "support.provy.ai", "provyai.vercel.app",
    "provy-amit-garg-s-projects.vercel.app", "provy-git-main-amit-garg-s-projects.vercel.app",
})


class Refused(RuntimeError):
    """The target is not allowed. Nothing was sent."""


def check_target(url: str) -> str:
    """Return the canonical base URL when `url` is exactly https://dev.provy.ai (optionally with one trailing slash); refuse everything else."""
    if not isinstance(url, str) or not url:
        raise Refused("Refused: the target address is empty.")
    if url != url.strip() or any(c.isspace() or ord(c) < 32 or c == "\\" for c in url):
        raise Refused("Refused: the target address has spaces, control characters or a backslash.")
    if "://" not in url:
        raise Refused("Refused: the target address has no scheme. Use https://dev.provy.ai.")
    try:
        parts = urlsplit(url)
        port = parts.port  # raises on a malformed port
    except ValueError:
        raise Refused("Refused: the target address cannot be read.")
    if parts.scheme != "https":
        raise Refused("Refused: only https is allowed.")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise Refused("Refused: the target address carries user-info ('@').")
    if port is not None or ":" in parts.netloc:
        raise Refused("Refused: the target address carries a port.")
    host = parts.netloc
    if host != ALLOWED_HOST:
        what = "production Provy" if host.lower().rstrip(".") in PRODUCTION_HOSTS else "not the dev address"
        raise Refused(f"Refused: {host or 'that address'} is {what}. The only allowed host is {ALLOWED_HOST}.")
    if parts.path not in ("", "/") or parts.query or parts.fragment or "?" in url or "#" in url:
        raise Refused("Refused: give the bare address, with no path, query or fragment.")
    return ALLOWED_URL


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Refused: give exactly one address to check.")
        return 1
    try:
        print("OK " + check_target(argv[1]))
        return 0
    except Refused as e:
        print(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
