"""Tripwire for #1648: nothing in this repo opens the credential files. Secrets come through the door.

argus has a CI check for its own tree (scripts/check-secret-door.py). It cannot see this repository, which is how
scripts/fleet_doctor.py came to open the config file directly and read the ServiceNow rows by line number
(7 Oct 2026). This is the same check for provy-sim, modelled on it.

It fails when a CODE line in a tracked python, javascript, shell or workflow file names either credential file
or the override that points at one:

    provy.config        provy.preprod.env        PROVY_CONFIG (the override, but not PROVY_CONFIG_FILE)

Comment lines and docstring bodies are ignored, because most mentions are prose about the file. The allow-list
below is the ONLY exception, and every entry says why. Adding a file to it is a decision, so it is a change to
this test, where a reviewer sees it. A stale entry (a file that no longer exists or no longer matches) also
fails, so the list cannot rot into a blanket pass.

How a script gets a secret instead: read the name from its own environment and let the caller fill it with
`cd "$HOME/Claude Projects/argus" && scripts/with-secrets NAME... -- <command>`. See scripts/fleet_doctor.py.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]

# path (relative to the repo root) -> why it may name a credential file
ALLOWED = {
    "tests/test_secret_door_tripwire.py": "this file: it holds the patterns it searches for",
    "tests/test_context_emitters.py": "asserts the context emitter does NOT name either file (the opposite of reading it)",
}

CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".yml", ".yaml"}
PATTERNS = [
    re.compile(r"provy\.config"),
    re.compile(r"provy\.preprod\.env"),
    re.compile(r"\bPROVY_CONFIG\b(?!_FILE)"),
]
COMMENT = re.compile(r"^\s*(#|//|\*|/\*|<!--)")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    # untracked files that are not ignored count too, so a new script is checked before it is committed;
    # a file deleted in the working tree but still in the index is not code that can run
    return [f for f in out.splitlines() if (ROOT / f).is_file()]


def code_lines(text: str, suffix: str):
    """(line number, line) for every line that is code: no comment lines, no docstring bodies."""
    in_doc = None
    for no, line in enumerate(text.split("\n"), 1):
        if suffix == ".py":
            stripped = line.strip()
            if in_doc:
                if in_doc in line:
                    in_doc = None
                continue
            for q in ('"""', "'''"):
                if stripped.startswith(q) or stripped.startswith("r" + q):
                    rest = stripped.split(q, 1)[1]
                    if q not in rest:
                        in_doc = q
                    break
            else:
                q = None
            if q:
                continue
        if COMMENT.match(line):
            continue
        yield no, line


def scan(files: list[str] | None = None, read=None):
    read = read or (lambda rel: (ROOT / rel).read_text(errors="replace"))
    hits = []
    for rel in files if files is not None else tracked_files():
        suffix = pathlib.Path(rel).suffix
        if suffix not in CODE_EXT:
            continue
        for no, line in code_lines(read(rel), suffix):
            if any(p.search(line) for p in PATTERNS):
                hits.append((rel, no, line.strip()[:110]))
    return hits


def test_nothing_in_the_repo_opens_a_credential_file():
    hits = [h for h in scan() if h[0] not in ALLOWED]
    assert hits == [], (
        "these files name a credential file in code. Read the name from the environment and run under "
        "scripts/with-secrets (argus) instead:\n" + "\n".join(f"  {r}:{n}  {t}" for r, n, t in hits))


def test_every_allow_list_entry_is_real_and_still_needed():
    live = {h[0] for h in scan()}
    gone = [f for f in ALLOWED if not (ROOT / f).is_file()]
    unused = [f for f in ALLOWED if (ROOT / f).is_file() and f not in live]
    assert gone == [], f"allow-listed file no longer exists: {gone}"
    assert unused == [], f"allow-listed file no longer names a credential file, remove it from the list: {unused}"
    assert all(len(why) > 15 for why in ALLOWED.values()), "every entry needs its reason"


def test_the_scanner_catches_each_way_of_opening_the_file():
    bad = {
        "a.py": 'open(os.path.expanduser("~/Claude Projects/provy.config"))\n',
        "b.sh": 'eval "$(grep -E \'^X=\' "$HOME/Claude Projects/provy.config")"\n',
        "c.mjs": "fs.readFileSync(path.join(home, 'provy.preprod.env'))\n",
        "d.js": "const p = process.env.PROVY_CONFIG;\n",
        "e.yml": "      - run: cat provy.config\n",
    }
    assert sorted(h[0] for h in scan(list(bad), read=lambda r: bad[r])) == sorted(bad)


def test_the_scanner_ignores_prose_and_the_file_override_name():
    ok = {
        "a.py": '"""Never reads provy.config; see the door."""\nx = 1\n',
        "b.py": 'def f():\n    """\n    provy.config is mentioned here\n    in a docstring body\n    """\n    return 1\n',
        "c.sh": "# the old version sourced provy.config\necho ok\n",
        "d.mjs": "// provy.preprod.env was removed\nconst a = 1\n",
        "e.py": 'f = os.environ.get("PROVY_CONFIG_FILE")\n',
        "f.md": "provy.config in docs is fine\n",
    }
    assert scan(list(ok), read=lambda r: ok[r]) == []
