"""The simulation's declared Guardrails rows exercise the product's DEFAULT (#1505, L10).

A context check that names no step type and no tool reads the decision steps (`decision` and `agent_message`), the steps Provy's coverage counts. L5 found the old rows, which said
`step_type: decision`, missed the faults the generator plants on the resolver's `agent_message` step (62 of 104). These tests hold the tree to: no row names a step type or a tool,
every agent the generator plants on is covered, and the ground-truth files (which these rows never feed) still match their recorded SHA-256.
"""
from __future__ import annotations

import json
import os
import re

import _context_path  # noqa: F401  (puts the tree and provy-sim on sys.path)
from config import context_sets as CS
from engine import context as C
from engine import context_truth as T

TREE = _context_path.TREE
DATA = os.path.join(TREE, "data")


def test_no_declared_row_names_a_step_type_or_a_tool():
    rows = CS.DECLARATIONS["guardrails"] + [r for v in CS.CAPTURE_DECLARATIONS.values() for r in v]
    assert rows
    for r in rows:
        assert list(r["check"]["select"]) == ["agent"], r      # an agent and nothing else: the product's default decides the steps


def test_every_agent_the_generator_plants_on_has_a_freshness_row_and_an_approved_list_row():
    agents = set(C.AGENTS)
    assert set(CS.GUARDRAIL_AGENTS) == agents
    for kind in ("context_fresh", "context_sources_in"):
        assert {r["check"]["select"]["agent"] for r in CS.DECLARATIONS["guardrails"] if r["check"]["kind"] == kind} == agents


def test_the_resolver_plants_on_an_agent_message_step_so_a_decision_only_row_would_miss_it():
    # The reason the default matters: one agent's deciding step is `agent_message`, the others' is `decision`.
    assert C.STEP_TYPE["resolver"] == "agent_message" and C.STEP_TYPE["triage"] == "decision"
    assert set(C.STEP_TYPE.values()) <= {"decision", "agent_message"}                # the product's one list of decision steps


def test_the_limit_and_the_list_are_the_ones_the_generator_plants_against():
    for r in CS.DECLARATIONS["guardrails"]:
        if r["check"]["kind"] == "context_fresh":
            assert r["check"]["max_age_hours"] == CS.LIMIT_DAYS * 24
        else:
            assert r["check"]["allowed"] == CS.APPROVED_SOURCES


def test_the_ground_truth_files_and_their_hashes_are_untouched_by_the_declarations():
    hashes = json.load(open(os.path.join(DATA, "groundtruth_context_hashes.json")))
    assert set(hashes) >= {"CM-A", "CM-B", "CM-C", "CM-D", "CAP-A", "CAP-B"}
    for name in hashes:
        assert re.fullmatch(r"[0-9a-f]{64}", T.verify_ground_truth(name, DATA))     # raises when the file's SHA-256 differs from the recorded one
