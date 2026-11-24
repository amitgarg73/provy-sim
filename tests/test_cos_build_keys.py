"""The key-map builder finds all 11 fleets, refuses a gap, and prints no key."""
import pytest

from cos import build_keys_json as b


def _state(**over):
    d = {f: {"ingest_key": "k-" + f} for f in b.FLEETS}
    d.update(over)
    return d


def test_builds_all_eleven():
    assert b.build(_state()) == {f: "k-" + f for f in b.FLEETS}


def test_accepts_a_fleets_map_and_plain_strings():
    assert b.build({"fleets": {f: "k-" + f for f in b.FLEETS}})["twin"] == "k-twin"


def test_missing_fleet_names_it_and_no_value(capsys):
    s = _state()
    del s["pledge"]
    with pytest.raises(SystemExit) as e:
        b.build(s)
    assert "pledge" in str(e.value) and "k-" not in str(e.value)


def test_multiline_key_is_refused():
    with pytest.raises(SystemExit):
        b.build(_state(cos={"ingest_key": "a\nb"}))


def test_main_prints_no_key(monkeypatch, tmp_path, capsys):
    import json
    p = tmp_path / "t.json"
    p.write_text(json.dumps(_state()))
    monkeypatch.setattr(b, "STATE", p)
    got = {}
    monkeypatch.setattr(b.subprocess, "run", lambda *a, **k: got.update(k))
    assert b.main() == 0
    assert "k-cos" not in capsys.readouterr().out
    assert b"k-cos" in got["input"]
