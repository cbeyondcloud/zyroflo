"""Batch 16 (the owner's ruling 30): the publish stamps each shard with the parser state it was
built under, from the one producer the verdict also calls (`freshness.parser_stamp`; design
round 3, D16-31); and it checks every path it will write before writing anything, so it refuses
instead of crashing, and a write failing mid-way names what it wrote, at exit 2 (D16-22, D16-49).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import freshness, snapshot as SN  # noqa: E402
from tools.graph.core.lib import props  # noqa: E402
from tools.graph.tests._estate import build, compose, producer, spawn, svc  # noqa: E402

#: `a`'s compose file answers `b`'s producer through the shared cache.
FEED = {"a": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
        "b": {"app.py": producer("TOPIC")}}


def manifest(tmp_path: Path, comp: str) -> dict:
    return json.loads((tmp_path / "ws" / comp / ".sdlc" / "graph" / SN.MANIFEST)
                      .read_text(encoding="utf-8"))


def report(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "out" / "build_report.json").read_text(encoding="utf-8"))


def test_a_manifest_records_the_parsers_its_files_and_its_dependencies_reach(tmp_path):
    """`a` reads its compose file with PyYAML; `b`'s producer reads `a`'s compose file through
    the shared cache (`env-keys` requires `compose-services`), so its stamp records YAML too."""
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    assert manifest(tmp_path, "a").get("parsers") == {"yaml": True}
    assert manifest(tmp_path, "b").get("parsers") == {"yaml": True}


def test_a_parser_missing_at_publish_is_stamped_false(tmp_path, monkeypatch):
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    assert manifest(tmp_path, "a").get("parsers") == {"yaml": False}
    assert manifest(tmp_path, "b").get("parsers") == {"yaml": False}


def test_a_component_whose_files_reach_no_parser_stamps_an_empty_map(tmp_path):
    """Present and empty is a stamp; only an absent `parsers` is "published before parser
    stamps existed" (design round 2, D16-25)."""
    assert build(tmp_path, {"a": {"README.md": "a\n"}, "b": {"README.md": "b\n"}},
                 "--publish-snapshot").code == 0
    assert manifest(tmp_path, "a").get("parsers") == {}


def test_the_publish_writes_exactly_what_the_one_producer_computes(tmp_path, monkeypatch):
    seen: dict[str, dict] = {}
    real = freshness.parser_stamp

    def recording(component, *args, **kwargs):
        seen[component] = real(component, *args, **kwargs)
        return seen[component]
    monkeypatch.setattr(freshness, "parser_stamp", recording)
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    assert set(seen) == {"a", "b"}
    assert {c: manifest(tmp_path, c).get("parsers") for c in seen} == seen


def test_the_moved_stamp_helpers_still_resolve_through_build():
    assert B.stamp_matches is freshness.stamp_matches
    assert B._shared_of is freshness._shared_of
    assert (B.ACTIVATION, B.ACTIVATION_DEPLOY) == ("activation", "activation-deploy")


def _mtimes(dir_: Path) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in dir_.iterdir() if p.is_file()}


@pytest.mark.parametrize("name", [SN.SHARD, SN.SHARD_UNRESOLVED, SN.MANIFEST, SN.GITATTRIBUTES])
def test_a_publish_over_a_path_it_cannot_write_refuses_and_writes_nothing(tmp_path, name):
    """Each of the four files a shard directory holds, on the SECOND component, so a publish
    that wrote as it went would have rewritten the first. A directory at `.gitattributes` was
    accepted at exit 0 - its `-text` attributes could then never be written, a latent
    line-ending digest failure - and is refused deliberately now (D16-41)."""
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    target = tmp_path / "ws" / "b" / ".sdlc" / "graph" / name
    target.unlink()
    target.mkdir()
    first = tmp_path / "ws" / "a" / ".sdlc" / "graph"
    before = _mtimes(first)
    proc = spawn(tmp_path, "--publish-snapshot")
    assert proc.returncode == 2 and proc.stderr == "", proc.stdout + proc.stderr
    assert (f"snapshot: refused - cannot write `b/.sdlc/graph/{name}`: not a regular file"
            in proc.stdout), proc.stdout
    assert _mtimes(first) == before, "the pre-flight runs before any write"
    target.rmdir()  # followed
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0


def test_a_publish_whose_shard_directory_cannot_be_made_refuses(tmp_path):
    """A regular file where the shard directory's parent goes: `mkdir` could never make it."""
    estate = {**FEED, "b": {**FEED["b"], ".sdlc": "not a directory\n"}}
    proc_estate = build(tmp_path, estate)
    assert proc_estate.code == 0
    proc = spawn(tmp_path, "--publish-snapshot")
    assert proc.returncode == 2 and proc.stderr == "", proc.stdout + proc.stderr
    assert "snapshot: refused - cannot write `b/.sdlc`: not a directory" in proc.stdout
    assert not (tmp_path / "ws" / "a" / ".sdlc").exists(), "nothing was written"
    (tmp_path / "ws" / "b" / ".sdlc").unlink()  # followed
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0


def test_an_incremental_publish_checks_only_the_shards_it_writes(tmp_path):
    """D16-32: the pre-flight covers the components this publish writes; `c` is reused, so a
    directory at its `.gitattributes` is not this publish's to refuse."""
    estate = {**FEED, "c": {"README.md": "c\n"}}
    assert build(tmp_path, estate, "--publish-snapshot").code == 0
    attributes = tmp_path / "ws" / "c" / ".sdlc" / "graph" / SN.GITATTRIBUTES
    attributes.unlink()
    attributes.mkdir()
    (tmp_path / "ws" / "a" / "docker-compose.yml").write_text(
        compose(svc("b", "../b", "web-value-2")), encoding="utf-8")
    proc = spawn(tmp_path, "--incremental", "--publish-snapshot")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    inc = report(tmp_path)["incremental"]
    assert "c" in inc["reused"] and "a" in inc["rebuilt"], inc
    assert attributes.is_dir()


def test_a_write_failing_mid_publish_exits_2_naming_what_it_wrote(tmp_path, monkeypatch):
    """D16-49: the publish's exit is decided by `"published" not in snapshot`, so a mid-loop
    failure names the components it wrote in its refusal and publishes no `published` key."""
    real = SN.write_shard
    calls: list[Path] = []

    def failing(dir_, facts, stamps=None):
        calls.append(dir_)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real(dir_, facts, stamps)
    monkeypatch.setattr(SN, "write_shard", failing)
    try:
        built = build(tmp_path, FEED, "--publish-snapshot")
    except OSError as exc:
        pytest.fail(f"a write failing part-way escaped as a traceback: {exc}")
    snapshot = report(tmp_path)["snapshot"]
    assert built.code == 2
    assert "published" not in snapshot
    assert snapshot["refused"] == (
        "cannot write `b/.sdlc/graph`: No space left on device; already written: a")
