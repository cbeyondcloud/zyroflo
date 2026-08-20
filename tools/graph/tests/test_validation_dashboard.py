"""The validation dashboard: the M1 targets, verdicted with their parts shown.

Item 11. The fixture's values below are HAND-DERIVED from EXPECTED.md before the
dashboard ran — the same pre-registration discipline as the golden chain, because a
dashboard checked against its own output is a mood ring. Ratios carry numerator and
denominator; a fact class with no producer is N/A, never 0% or 100%.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
import tools.graph.validate as V  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("validation") / "out"
    assert B.main(["--workspace", str(FIXTURE / "workspace.toml"),
                   "--out", str(out)]) == 0
    return out


def metric(result: dict, name: str) -> dict:
    (m,) = [m for m in result["metrics"] if m["metric"] == name]
    return m


# ── the fixture's numbers, derived from the answer key by hand ────────────────


def test_the_fixture_metrics_match_the_answer_key(built):
    result = V.validate(built, dict(V.DEFAULT_TARGETS))

    topics = metric(result, "topics_with_producer_and_consumer")
    assert (topics["numerator"], topics["denominator"]) == (1, 5), \
        "order-events-v1 has both; D7's shipment topic is a stub with neither; " \
        "item 91's three topics are producer-only by design"

    spec = metric(result, "endpoints_spec_matched")
    assert (spec["numerator"], spec["denominator"]) == (1, 2), \
        "of the two SERVED endpoints, only /orders/v1/list is specified (9a)"

    ui = metric(result, "ui_urls_resolved")
    assert ui["value"] == 100.0 and ui["denominator"] >= 2

    write = metric(result, "write_targets_migration_owned")
    assert (write["numerator"], write["denominator"]) == (1, 2), \
        "order_events is written and owned; D7's orphan mapping writes a second table " \
        "that no migration owns — which is exactly the loss this target exists to see"

    host = metric(result, "host_keys_resolved")
    assert (host["numerator"], host["denominator"]) == (1, 5), \
        "TRANSITIONAL (item 92 answer-key state): the inventory pair resolves and the " \
        "four pre-registered item-92 rows sit unpaired until the extractor commit " \
        "converts them, when this returns to 100%"

    clean = metric(result, "zero_silent_drops")
    assert clean["met"] is True and clean["problems"] == []


def test_orphans_match_an_independent_recount(built):
    """Weak on purpose where hand-derivation is impractical: an independent five-line
    recount from the same files, not the dashboard's own code path."""
    result = V.validate(built, dict(V.DEFAULT_TARGETS))
    rows = [json.loads(line) for line
            in (built / "graph.jsonl").read_text(encoding="utf-8").splitlines()
            if '"header"' not in line]
    nodes = [r for r in rows if "src" not in r and r.get("type") != "repo"]
    edges = [r for r in rows if "src" in r]
    touched = {e["src"] for e in edges} | {e["dst"] for e in edges}
    orphans = [n for n in nodes if n["id"] not in touched]
    m = metric(result, "orphans")
    assert (m["numerator"], m["denominator"]) == (len(orphans), len(nodes))


# ── verdicts, N/A, and the clean-build gate ───────────────────────────────────


def test_missed_targets_name_themselves_and_exit_2(built, tmp_path, capsys):
    """The fixture honestly MISSES the default topic target (50% < 60%) — the dashboard
    must say so and fail, not grade on a curve."""
    rc = V.main(["--graph-dir", str(built)])
    out = capsys.readouterr().out
    assert rc == 2
    assert "FAIL topics_with_producer_and_consumer: 20.0%" in out
    assert "verdict: missed" in out
    written = json.loads((built / "metrics.json").read_text(encoding="utf-8"))
    assert written["verdict"] == "missed"
    assert (built / "report.md").exists()


def test_recalibrated_targets_reach_the_tool_only_through_the_flag(built, tmp_path,
                                                                   capsys):
    override = tmp_path / "targets.toml"
    override.write_text("topics_with_producer_and_consumer = 20.0\n"
                        "endpoints_spec_matched = 50.0\n"
                        "write_targets_migration_owned = 50.0\n", encoding="utf-8")
    rc = V.main(["--graph-dir", str(built), "--targets", str(override)])
    assert rc == 0
    assert "verdict: met" in capsys.readouterr().out


def test_a_fact_class_nobody_produced_is_na_not_a_grade(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "graph.jsonl").write_text(
        '{"id": "svc:a@a", "type": "svc"}\n'
        '{"src": "svc:a@a", "dst": "ep:/x", "type": "DG_EXPOSES", '
        '"source_file": "f", "line": 1, "extractor": "e"}\n'
        '{"src": "cfg:a/s.yaml", "dst": "ep:/x", "type": "DG_SPECIFIES", '
        '"source_file": "s.yaml", "line": 1, "extractor": "e"}\n'
        '{"id": "ep:/x", "type": "ep"}\n', encoding="utf-8")
    (out / "unresolved.jsonl").write_text("", encoding="utf-8")
    (out / "build_report.json").write_text(
        '{"run_id": "r", "status": "ok", "contract_violations": [], "extractors": []}',
        encoding="utf-8")
    result = V.validate(out, dict(V.DEFAULT_TARGETS))
    m = metric(result, "write_targets_migration_owned")
    assert m["value"] is None and m["met"] is None
    assert result["verdict"] == "met", "N/A must not fail a workspace for not having " \
                                       "a technology"


def test_a_dirty_build_fails_validation_before_any_ratio_is_believed(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "graph.jsonl").write_text('{"id": "svc:a@a", "type": "svc"}\n',
                                     encoding="utf-8")
    (out / "unresolved.jsonl").write_text("", encoding="utf-8")
    (out / "build_report.json").write_text(json.dumps({
        "run_id": "r", "status": "ok",
        "contract_violations": ["row missing promised field"],
        "extractors": [{"name": "x", "failed": 1}]}), encoding="utf-8")
    result = V.validate(out, dict(V.DEFAULT_TARGETS))
    clean = metric(result, "zero_silent_drops")
    assert clean["met"] is False
    assert any("contract violation" in p for p in clean["problems"])
    assert any("extractor failed: x" in p for p in clean["problems"])
    assert result["verdict"] == "missed"


# ── the spot-audit sample ─────────────────────────────────────────────────────


def test_the_sample_is_seeded_and_repeatable(built):
    a = V.validate(built, dict(V.DEFAULT_TARGETS), sample=5, seed="audit-1")
    b = V.validate(built, dict(V.DEFAULT_TARGETS), sample=5, seed="audit-1")
    c = V.validate(built, dict(V.DEFAULT_TARGETS), sample=5, seed="audit-2")
    assert a["sample"] == b["sample"], "same seed, same draw — re-checkable by anyone"
    assert a["sample"]["rows"] != c["sample"]["rows"]
    assert len(a["sample"]["rows"]) == 5
    assert all(":" in r["where"] for r in a["sample"]["rows"]), \
        "every sampled edge carries the provenance the audit checks"


def test_the_report_renders_the_sample_and_the_rollup(built):
    result = V.validate(built, dict(V.DEFAULT_TARGETS), sample=3, seed="s")
    md = V.render_markdown(result)
    assert "## Spot-audit sample (seed `s`)" in md
    assert "## Unresolved, by kind" in md
    assert "| topics_with_producer_and_consumer | 20.0% | 1/5 | " in md


def test_a_missing_input_is_a_loud_refusal(tmp_path, capsys):
    assert V.main(["--graph-dir", str(tmp_path)]) == 2
    assert "does not exist" in capsys.readouterr().out
