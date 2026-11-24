"""The Chief of Staff demo sends to https://dev.provy.ai and to nothing else.

Every production host, and every trick that makes one look like dev (or dev look like one), must be
refused before anything is sent.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from cos.guard import ALLOWED_URL, Refused, check_target

ROOT = Path(__file__).resolve().parent.parent

PRODUCTION = [
    "https://provy.ai", "https://www.provy.ai", "https://provyai.vercel.app", "https://support.provy.ai",
    "https://provy-amit-garg-s-projects.vercel.app", "https://provy-git-main-amit-garg-s-projects.vercel.app",
]
TRICKS = [
    "", "   ", "provy.ai", "dev.provy.ai", "//dev.provy.ai",
    "http://dev.provy.ai", "ftp://dev.provy.ai",
    "https://dev.provy.ai@provy.ai", "https://provy.ai@dev.provy.ai", "https://user:pw@dev.provy.ai",
    "https://dev.provy.ai.evil.com", "https://evil-dev.provy.ai", "https://xdev.provy.ai", "https://sub.dev.provy.ai",
    "https://evil.com/dev.provy.ai", "https://evil.com?x=dev.provy.ai", "https://evil.com#dev.provy.ai",
    "https://DEV.PROVY.AI", "https://Dev.provy.ai", "https://dev.provy.ai.", "https://dev.provy.ai.:443",
    "https://dev.provy.ai:443", "https://dev.provy.ai:8443", "https://dev.provy.ai:", "https://dev.provy.ai:abc",
    "https://dev.provy.ai/path", "https://dev.provy.ai/?a=b", "https://dev.provy.ai#x",
    "https://dev.provy.ai\\@provy.ai", "https://dev.provy.ai\n", " https://dev.provy.ai", "https://dev.provy.ai ",
    "https://provydev.vercel.app", "https://localhost", "https://127.0.0.1", "https://dev.provy.ai%2eevil.com",
    "https:dev.provy.ai", "https:///dev.provy.ai", None, 5,
]


@pytest.mark.parametrize("url", PRODUCTION)
def test_every_production_host_is_refused(url):
    with pytest.raises(Refused):
        check_target(url)


@pytest.mark.parametrize("url", [u + suffix for u in PRODUCTION for suffix in ("/", "/api/ingest", ":443", ".", "@dev.provy.ai")])
def test_production_with_decoration_is_refused(url):
    with pytest.raises(Refused):
        check_target(url)


@pytest.mark.parametrize("url", TRICKS)
def test_every_trick_is_refused(url):
    with pytest.raises(Refused):
        check_target(url)


def test_the_one_allowed_address_passes():
    assert check_target("https://dev.provy.ai") == ALLOWED_URL
    assert check_target("https://dev.provy.ai/") == ALLOWED_URL


def test_production_message_says_production():
    with pytest.raises(Refused, match="production"):
        check_target("https://provy.ai")


def _cli(url):
    return subprocess.run([sys.executable, str(ROOT / "cos" / "guard.py"), url], capture_output=True, text=True)


def test_cli_refuses_production_and_prints_refused():
    r = _cli("https://provy.ai")
    assert r.returncode == 1 and "Refused" in r.stdout


def test_cli_accepts_dev():
    r = _cli("https://dev.provy.ai")
    assert r.returncode == 0 and r.stdout.strip() == "OK https://dev.provy.ai"


def test_cli_with_no_argument_refuses():
    r = subprocess.run([sys.executable, str(ROOT / "cos" / "guard.py")], capture_output=True, text=True)
    assert r.returncode == 1 and "Refused" in r.stdout
