"""Item 19's gate: a second stack's chain resolves against the UNCHANGED core.

Five hops across three components of a Python/ASGI/compose workspace, every one asserted
against `fixtures/pantry/EXPECTED.md` — hand-written before any of these extractors existed.

**The gate is not "this file is green".** Rev 7 stated the limitation the first fixture could
never escape: one committed fixture can always be accommodated by the code that reads it, so
"the core is generic" is only genuinely proven by the *second*. The claim being tested here
is therefore not that the chain resolves — it is that the chain resolves **and
`tools/graph/core/` is byte-identical**. A green chain bought with a core edit is a failure
wearing a pass's clothes: it means the SPI boundary was drawn around one stack, and every
later adopter pays for it. `test_the_core_was_not_touched` is the assertion that matters.

`test_cutting_any_seam_breaks_the_chain_pantry.py` is the other half, for the same reason it
is for the first fixture: a golden test that still passes when the fixture is wrong is not
reading the fixture.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.tests.chain_pantry import (  # noqa: E402
    CHAIN, CHAIN_OWNERS, CHAIN_PROVENANCE, INERT_COMPONENT, UNCLAIMED_COMPONENT, find)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pantry"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A clean build of the committed fixture — no cache, no prior state, no shortcuts."""
    out = tmp_path_factory.mktemp("pantry")
    report = B.build(FIXTURE / "workspace.toml", out, run_id="pantry01")
    edges = [json.loads(line)
             for line in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines()
             if '"kind": "edge"' in line]
    unresolved = [json.loads(line) for line in
                  (out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return report, edges, unresolved


# ── the gate ──────────────────────────────────────────────────────────────────


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0, f"git {' '.join(args)} failed: {out.stderr}"
    return out.stdout.strip()


def test_the_core_was_not_touched():
    """THE assertion of item 19. Everything else in this file is evidence for it.

    A second stack that forces a core edit means the extraction SPI was shaped around the
    first one — which is exactly the finding this fixture exists to produce, and it has to
    arrive as a failing test rather than as a paragraph nobody re-reads.

    **The baseline is the commit that added this fixture's answer key, found by asking git
    rather than hardcoded**, and the comparison runs to the WORKING TREE so uncommitted
    edits count. An earlier draft compared against `HEAD`, which passes trivially the moment
    anything is committed — a test that goes permanently green on the first commit is the
    "a skipping test still prints green" failure this project already paid for at item 73,
    wearing different clothes.

    **When core legitimately changes later** — the impact engine at M2, the SQLite store at
    M6 — this test goes red, and that is the design. It is moved forward in its own commit
    with the reasoning, exactly as an answer key is corrected: never silently, and never in
    the same commit as the change that made it fail.
    """
    baseline = _git("log", "--diff-filter=A", "--format=%H", "--",
                    "tools/graph/tests/fixtures/pantry/EXPECTED.md")
    assert baseline, (
        "cannot locate the commit that added the pantry answer key, so the core-untouched "
        "claim cannot be checked. This fails rather than skips: an unverifiable gate that "
        "prints green is worse than no gate")
    diff = _git("diff", "--stat", baseline.splitlines()[-1], "--", "tools/graph/core/")
    assert diff == "", (
        "item 19's gate: the second fixture must build against an UNCHANGED core, and "
        f"core has changed since the fixture landed:\n{diff}")


# ── the chain ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("hop, type_, src, dst, file, line", CHAIN,
                         ids=[f"hop{h[0]}" for h in CHAIN])
def test_every_hop_resolves_where_the_answer_key_says(built, hop, type_, src, dst, file, line):
    _, edges, _ = built
    hits = find(edges, type_, src, dst)
    assert len(hits) == 1, f"hop {hop}: expected exactly one {type_} {src} -> {dst}"
    assert hits[0]["source_file"].endswith(file)
    assert hits[0]["line"] == line, f"EXPECTED.md cites {file}:{line} for hop {hop}"


@pytest.mark.parametrize("hop", sorted(CHAIN_PROVENANCE), ids=lambda h: f"hop{h}")
def test_every_other_coordinate_the_answer_key_names_is_recorded(built, hop):
    """Four hops are assembled from more than one place and the key lists every one. An edge
    that resolves to the right node while losing the trail a human follows to CHANGE it is
    half a fact — and the half it keeps is the half nobody needs."""
    _, edges, _ = built
    row = next(r for r in CHAIN if r[0] == hop)
    props = find(edges, row[1], row[2], row[3])[0]["props"]
    for name, expected in CHAIN_PROVENANCE[hop].items():
        got = props.get(name)
        if isinstance(expected, str):
            assert isinstance(got, str) and got.endswith(expected), \
                f"hop {hop}: props[{name!r}] = {got!r}, expected to end with {expected!r}"
        else:
            assert got == expected, f"hop {hop}: props[{name!r}] = {got!r} != {expected!r}"


def test_each_hop_is_resolved_by_a_different_mechanism(built):
    """Five hops, five extractors. Sharing one would mean cutting a seam fails two hops for
    one reason, which measures the extractor rather than the chain."""
    _, edges, _ = built
    extractors = [find(edges, t, s, d)[0]["extractor"] for _, t, s, d, _, _ in CHAIN]
    assert len(set(extractors)) == 5, f"hops share an extractor: {extractors}"


def test_the_chain_crosses_component_boundaries(built):
    _, edges, _ = built
    owners = [find(edges, t, s, d)[0]["src"].split("@")[-1] for _, t, s, d, _, _ in CHAIN]
    assert owners == CHAIN_OWNERS
    assert sum(1 for a, b in zip(owners, owners[1:]) if a != b) == 2


def test_the_two_sides_of_the_queue_meet_at_the_value_and_nowhere_else(built):
    """`EXPECTED.md`'s sharpest claim. The publisher's key, file, component AND resolution
    layer all differ from the consumer's — the first arrives from the deployment layer, the
    second from a default written in code. An extractor pairing them by key name finds
    nothing and reports a topic with a producer and no consumer, which reads as a real
    architectural finding rather than as a bug."""
    _, edges, _ = built
    produced = find(edges, "DG_PRODUCES", ids.component_service_id("pantry-api"),
                    "topic:restock-events-v2")[0]
    consumed = find(edges, "DG_CONSUMES", ids.component_service_id("restock-worker"),
                    "topic:restock-events-v2")[0]
    assert produced["props"]["key"] != consumed["props"]["key"]
    assert produced["props"]["resolved_from"].endswith("docker-compose.yml:10")
    assert consumed["props"]["resolved_from"].endswith("consumer.py:13")


def test_the_caller_and_the_server_meet_at_one_endpoint(built):
    """Hops 1 and 2 are only a chain if they name the same node. The path is assembled from
    a router prefix on one side and read from a module constant on the other, and nothing
    links the two files."""
    _, edges, _ = built
    assert find(edges, "DG_CALLS", ids.component_service_id("restock-worker"),
                "ep:/pantry/v1/items")
    assert find(edges, "DG_EXPOSES", ids.component_service_id("pantry-api"),
                "ep:/pantry/v1/items")


# ── the decoys: precision, not only recall ────────────────────────────────────


def test_e1_two_spellings_of_one_path_are_one_endpoint(built):
    """`{sku}` on the server and `{item_sku}` on the caller. Two nodes would split every
    impact query on this endpoint in half."""
    _, edges, _ = built
    both = {e["type"] for e in edges if e["dst"] == "ep:/pantry/v1/items/{}"}
    assert both == {"DG_CALLS", "DG_EXPOSES"}
    assert not [e for e in edges if e["dst"].startswith("ep:") and "item_sku" in e["dst"]]


def test_e2_three_spellings_of_one_key_are_one_node(built):
    _, edges, _ = built
    keys = {e["dst"] for e in edges if e["dst"].startswith("envkey:")}
    assert ids.env_key_id("RESTOCK_QUEUE") in keys
    assert not [k for k in keys if "-" in k.split(":", 1)[1]]


def test_e3_the_compose_default_grammar_resolved(built):
    """`${QUEUE_NAME:-restock-events-v2}` is not the first fixture's `${VAR:default}`. A
    reader written for that one names the topic after the placeholder."""
    _, edges, _ = built
    assert not [e for e in edges if e["dst"].startswith("topic:")
                and ("$" in e["dst"] or "QUEUE_NAME" in e["dst"])]


def test_e4_the_contested_service_is_decided_by_the_workspace(built):
    """The same decoy as the first fixture in a different deployment technology — and this
    is where it bit. The canonical pass resolves the service NODE, but the resolved VALUE
    view is built during extraction: `legacy/` sorts last, so it won, and the component
    published to a topic one version number off."""
    _, edges, _ = built
    claims = {e["source_file"]: e for e in edges if e["type"] == "DG_DEPLOYS"
              and e["dst"].endswith("pantry-api@deploy-compose")}
    winner, loser = claims["docker-compose.yml"], claims["legacy/docker-compose.yml"]
    assert (winner["canonical"], winner["confidence"]) == (True, 1.0)
    assert (loser["canonical"], loser["confidence"]) == (False, 0.5), "damped, not dropped"
    assert not [e for e in edges if e["dst"] == "topic:restock-events-v1"]


def test_e5_a_path_named_in_a_test_never_becomes_a_call_or_a_route(built):
    _, edges, _ = built
    assert not [e for e in edges if e["type"] in {"DG_CALLS", "DG_EXPOSES"}
                and "test" in e["source_file"].rsplit("/", 1)[-1]]


def test_e8_a_commented_out_statement_does_not_own_a_table(built):
    """And the real `CREATE TABLE` above it keeps its line, which is what proves the comment
    was blanked rather than removed."""
    _, edges, _ = built
    tables = {e["dst"] for e in edges if e["type"] == "DG_OWNS_TABLE"}
    assert tables == {"table:restock_events"}


def test_no_endpoint_was_invented(built):
    _, edges, _ = built
    assert {e["dst"] for e in edges if e["dst"].startswith("ep:")} == {
        "ep:/pantry/v1/items", "ep:/pantry/v1/items/{}"}


# ── zero silent drops ─────────────────────────────────────────────────────────


def test_unresolved_holds_the_pre_registered_entries_and_says_why(built):
    """`EXPECTED.md` pre-registers E6 and E7 and says "at least 2". The third is the
    superseded declaration E4 produced once the value ranking started reporting it — a
    finding the answer key's own decoy implies and did not enumerate."""
    _, _, unresolved = built
    kinds = {r["kind"] for r in unresolved}
    assert {"compose-unparsed", "env-key-unset"} <= kinds
    assert all(r["why"] for r in unresolved), \
        "an unresolved row with no reason is a log line, not a finding"


@pytest.mark.parametrize("kind, file, line", [
    ("compose-unparsed", "docker-compose.override.yml", 0),   # E6
    ("env-key-unset", "publisher.py", 21),                    # E7
    ("superseded-declaration", "legacy/docker-compose.yml", 15),
])
def test_each_unresolved_entry_cites_where_it_came_from(built, kind, file, line):
    _, _, unresolved = built
    row = next(r for r in unresolved if r["kind"] == kind)
    assert row["source_file"].endswith(file) and row["line"] == line


def test_e6_the_malformed_file_did_not_abort_the_build(built):
    report, edges, _ = built
    assert report["status"] == "ok" and edges


# ── the build itself ──────────────────────────────────────────────────────────


def test_no_profile_was_needed(built):
    """A claim, not an omission. Every format here is one a generic tool can read; the
    profile layer exists for formats an organisation invented, and this workspace invented
    none. A second fixture that quietly needed a profile would prove the opposite of what
    item 19 exists to prove."""
    report, _, _ = built
    assert report["workspace"]["profile"] == ""


def test_the_coverage_hole_is_named_and_the_build_still_succeeds(built):
    """"Nothing matched this component" and "this component has no couplings" are different
    answers, and only the second is legitimate."""
    report, _, _ = built
    assert report["components_unclaimed"] == [UNCLAIMED_COMPONENT]
    assert report["status"] == "ok"


def test_the_build_is_deterministic(tmp_path_factory):
    a, b = tmp_path_factory.mktemp("pa"), tmp_path_factory.mktemp("pb")
    B.build(FIXTURE / "workspace.toml", a, run_id="one")
    B.build(FIXTURE / "workspace.toml", b, run_id="two")
    assert (a / "graph.jsonl").read_bytes() == (b / "graph.jsonl").read_bytes()
    assert (a / "unresolved.jsonl").read_bytes() == (b / "unresolved.jsonl").read_bytes()


def test_the_inert_component_reaches_nothing(built):
    """P5 at the graph level. Its configured value `/dev/usb/lp0` is path-shaped and its env
    read is module-level, so a careless call reader would have paired them into an edge."""
    _, edges, _ = built
    for e in edges:
        if INERT_COMPONENT in e["src"] and INERT_COMPONENT not in e["dst"]:
            pytest.fail(f"the inert component reaches {e['dst']} via {e['type']}")
