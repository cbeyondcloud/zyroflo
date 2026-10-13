"""Impact prototype: one seed file in, a ranked blast radius out — pinned to the chain.

Item 18. `EXPECTED.md`'s chain is a hand-written blast radius: a change at hop 3's file
(the producer) really does implicate the consumer, the write mapping, and the migration,
across three components. That makes the existing answer key double as this tool's answer
key — every ranking assertion below is derived from the chain by hand, not from running
the tool. The traversal-math tests use tiny synthetic edges so the damping constants are
pinned as numbers, not as "whatever the fixture happens to produce".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph import impact as I  # noqa: E402
from tools.graph.tests.chain import INERT_COMPONENT  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"

#: The chain's files, transcribed from EXPECTED.md — component, then component-relative
#: path exactly as extractors cite it. The producer file is the seed in most tests below.
PRODUCER = ("orders-api", "src/main/java/com/example/orderflow/api/OrderPublisher.java")
CONSUMER = ("orders-worker", "src/main/java/com/example/orderflow/worker/OrderConsumer.java")
MAPPING = ("orders-worker", "config/write-mapping.yml")
MIGRATION = ("orders-worker", "src/main/resources/db/migration/V1__create_order_events.sql")
CONTROLLER = ("orders-api", "src/main/java/com/example/orderflow/api/OrderController.java")
UI_URLS = ("web-console", "src/config/urls.js")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("impact")
    B.build(FIXTURE / "workspace.toml", out, run_id="impact01")
    return out


@pytest.fixture(scope="module")
def graph(built):
    return I.load_graph(built)


def files_of(result: dict) -> dict[tuple[str, str], float]:
    return {(f["component"], f["path"]): f["score"] for f in result["files"]}


# -- the chain, as a blast radius ---------------------------------------------------------


def test_seed_by_provenance_reaches_the_chain_files(graph):
    """Seeding the producer must surface the consumer, the mapping and the migration —
    three files in another component, none of which shares a token with the seed path."""
    nodes, edges = graph
    result = I.impact(nodes, edges, *PRODUCER)
    assert result["seed"]["mode"] == "provenance"
    got = files_of(result)
    for expected in (CONSUMER, MAPPING, MIGRATION):
        assert expected in got, f"chain file missing from the blast radius: {expected}"


def test_the_blast_radius_crosses_into_the_ui(graph):
    """Hop 1 of the chain is the UI calling the producer's endpoint; the ranking must
    reach it, because 'what else does this change touch' includes the front end."""
    nodes, edges = graph
    got = files_of(I.impact(nodes, edges, *PRODUCER))
    assert UI_URLS in got and CONTROLLER in got


def test_producer_seed_ranks_consumer_higher_than_the_reverse(graph):
    """The direction doctrine as a number: a producer change hits its consumers harder
    than a consumer change hits its producer (PRODUCES against-damping is punitive)."""
    nodes, edges = graph
    from_producer = files_of(I.impact(nodes, edges, *PRODUCER))[CONSUMER]
    from_consumer = files_of(I.impact(nodes, edges, *CONSUMER))[PRODUCER]
    assert from_producer > from_consumer


def test_the_inert_component_is_outranked_by_every_chain_file(graph):
    """EXPECTED.md P5: legacy-reports participates in nothing. Any file of it that sneaks
    into the ranking must score strictly below every chain file, or the ranking is noise."""
    nodes, edges = graph
    got = files_of(I.impact(nodes, edges, *PRODUCER))
    chain_floor = min(got[f] for f in (CONSUMER, MAPPING, MIGRATION, CONTROLLER, UI_URLS))
    inert = [s for (comp, _), s in got.items() if comp == INERT_COMPONENT]
    assert all(s < chain_floor for s in inert)


def test_the_seed_file_is_never_its_own_impact(graph):
    nodes, edges = graph
    assert PRODUCER not in files_of(I.impact(nodes, edges, *PRODUCER))


def test_a_file_the_graph_never_saw_falls_back_by_directory_and_says_so(graph):
    """A brand-new file has no provenance. The fallback seeds from the nearest directory
    the component's facts live in — and the result names the mode, because a fallback
    ranking silently presented as a provenance ranking overstates the evidence."""
    nodes, edges = graph
    result = I.impact(nodes, edges, "orders-api",
                      "src/main/java/com/example/orderflow/api/BrandNew.java")
    assert result["seed"]["mode"] == "directory"
    assert result["files"], "the fallback still ranks the component's coupled files"


def test_an_unknown_component_refuses_loudly(built, capsys):
    rc = I.main(["--graph-dir", str(built), "--seed", "no-such-component/some/file.java"])
    assert rc == 2
    assert "no-such-component" in capsys.readouterr().out


def test_services_ranking_names_the_reached_owner(graph):
    nodes, edges = graph
    services = {s["id"] for s in I.impact(nodes, edges, *PRODUCER)["services"]}
    assert "svc:orders-worker@orders-worker" in services


# -- traversal math, pinned on synthetic edges --------------------------------------------


def edge(src, dst, type_, confidence=1.0, canonical=True):
    return {"src": src, "dst": dst, "type": type_, "confidence": confidence,
            "canonical": canonical, "source_file": "x", "line": 1, "props": {}}


def test_damping_is_direction_aware():
    """svc-a produces topic t; svc-b consumes it. From a the consumer is one strong
    against-CONSUMES hop (0.9); from b the producer is two weak hops (0.2 * 0.2)."""
    edges = [edge("a", "t", "DG_PRODUCES"), edge("b", "t", "DG_CONSUMES")]
    assert I.traverse({"a": 1.0}, edges)["b"] == pytest.approx(0.9)
    assert I.traverse({"b": 1.0}, edges)["a"] == pytest.approx(0.2 * 0.2)


def test_confidence_and_noncanonical_both_damp():
    edges = [edge("a", "b", "DG_CALLS", confidence=0.8, canonical=False)]
    assert I.traverse({"a": 1.0}, edges)["b"] == pytest.approx(0.3 * 0.8 * 0.5)


def test_the_hop_cap_is_a_cap():
    chain = [edge("a", "b", "DG_UNSEEN"), edge("b", "c", "DG_UNSEEN"),
             edge("c", "d", "DG_UNSEEN")]
    assert "d" not in I.traverse({"a": 1.0}, chain, hops=2)
    assert I.traverse({"a": 1.0}, chain, hops=3)["d"] == pytest.approx(0.5 ** 3)


# -- the CLI contract ---------------------------------------------------------------------


def test_json_output_is_deterministic_and_shaped(built, capsys):
    argv = ["--graph-dir", str(built), "--seed", f"{PRODUCER[0]}/{PRODUCER[1]}", "--json"]
    assert I.main(argv) == 0
    first = capsys.readouterr().out
    assert I.main(argv) == 0
    assert first == capsys.readouterr().out
    payload = json.loads(first)
    assert payload["schema_version"] == I.SCHEMA_VERSION
    assert set(payload) >= {"schema_version", "seed", "services", "files"}
    base = {"component", "path", "score", "hops"}
    # Item 78: a row for a file under a declared [[generated]] tree carries the flag
    # (and its regenerate_from, when declared); every other row is exactly the base —
    # the flag is added only when true, so pre-78 consumers see pre-78 rows. Item 21
    # added `hops` to the base: every row names how far from the change its reason sits.
    assert all(base <= set(f) <= base | {"generated", "regenerate_from"}
               for f in payload["files"])
    assert any(set(f) == base for f in payload["files"])


def test_top_limits_the_files_list(built, capsys):
    assert I.main(["--graph-dir", str(built), "--seed", f"{PRODUCER[0]}/{PRODUCER[1]}",
                   "--json", "--top", "3"]) == 0
    assert len(json.loads(capsys.readouterr().out)["files"]) == 3


def test_a_diff_the_os_will_not_read_is_named_in_words_no_locale_changes(tmp_path,
                                                                         monkeypatch):
    """T638: the reason named an `OSError` by its `strerror`, the OS's sentence in the OS's
    language - here a German machine's - so one fault read differently by machine."""
    diff = tmp_path / "change.diff"
    diff.write_text("diff --git a/x b/x\n", encoding="utf-8")
    real = Path.read_text

    def refuse(self, *args, **kwargs):
        if self == diff:
            raise PermissionError(13, "Zugriff verweigert", str(self))
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", refuse)
    text, error = I.read_diff(diff)
    assert text == "" and error == f"{diff} could not be read (PermissionError (EACCES))"
