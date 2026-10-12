"""Batch 16 (T628, T630; the owner's ruling 29): `--check-fresh` prints the one verdict.

STALE for a stale or missing shard, dependents included; REFUSED for a shard it cannot
trust, with the reason and a remedy that is accepted when followed; NOTE, at exit 0, for a
current or stale shard whose deployment reader filed `grammar-unavailable` and still has no
parser (D16-21, D16-53). Its dict says the same: `fresh` is the current shards only.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph.core.lib import props  # noqa: E402
from tools.graph.tests._estate import build, compose, producer, svc  # noqa: E402

FEED = {"a": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
        "b": {"app.py": producer("TOPIC")}}
NOTE = ("freshness: NOTE `compose-services` could not parse its files when its shard was "
        "published - no parser installed: a; install the parser each `grammar-unavailable` "
        "row names, then re-publish the shards it names")


def check_fresh(where: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace",
         str(where / "ws" / "workspace.toml"), "--check-fresh"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


def publish(where: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.graph.build", "--workspace",
         str(where / "ws" / "workspace.toml"), "--out", str(where / "out"),
         "--publish-snapshot"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


def lines(proc: subprocess.CompletedProcess, kind: str) -> list[str]:
    return [ln for ln in proc.stdout.splitlines() if ln.startswith(f"freshness: {kind} ")]


def verdict(where: Path) -> dict:
    return B.check_freshness(where / "ws" / "workspace.toml")


def test_a_refused_shard_is_named_with_its_remedy_and_following_it_is_accepted(tmp_path):
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    manifest = tmp_path / "ws" / "a" / ".sdlc" / "graph" / "snapshot.json"
    manifest.write_text("{not json", encoding="utf-8")
    proc = check_fresh(tmp_path)
    assert (proc.returncode, proc.stderr) == (2, ""), proc.stdout + proc.stderr
    assert lines(proc, "REFUSED") == [
        "freshness: REFUSED a - its committed snapshot.json exists but could not be read "
        "(not JSON); re-publish (build --publish-snapshot)"]
    assert "component(s) current" not in proc.stdout
    report = verdict(tmp_path)
    assert report["status"] == "stale"
    assert report["freshness"]["refused"] == {
        "a": "its committed snapshot.json exists but could not be read (not JSON)"}
    assert "a" not in report["freshness"]["fresh"] and "a" not in report["freshness"]["stale"]
    assert publish(tmp_path).returncode == 0  # followed
    assert check_fresh(tmp_path).returncode == 0


def test_a_refusal_alone_is_not_current(tmp_path):
    """`b`'s bytes fail their digest and nothing depends on what `b` read: nothing is stale,
    and still the exit is 2, the status `stale`, and no line calls the snapshot the tree."""
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    shard = tmp_path / "ws" / "b" / ".sdlc" / "graph" / "shard.jsonl"
    shard.write_bytes(shard.read_bytes() + b"\n")
    proc = check_fresh(tmp_path)
    assert (proc.returncode, proc.stderr) == (2, ""), proc.stdout + proc.stderr
    assert lines(proc, "STALE") == [] and "component(s) current" not in proc.stdout
    assert lines(proc, "REFUSED") == [
        "freshness: REFUSED b - its shard bytes no longer match their recorded digest; "
        "re-publish (build --publish-snapshot)"]
    report = verdict(tmp_path)
    assert (report["status"], report["freshness"]["fresh"], report["freshness"]["stale"]) == (
        "stale", ["a"], {})


def test_a_non_file_manifest_says_to_remove_it_and_doing_so_is_accepted(tmp_path):
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    manifest = tmp_path / "ws" / "a" / ".sdlc" / "graph" / "snapshot.json"
    manifest.unlink()
    manifest.mkdir()
    proc = check_fresh(tmp_path)
    assert proc.returncode == 2 and proc.stderr == "", proc.stdout + proc.stderr
    assert lines(proc, "REFUSED") == [
        "freshness: REFUSED a - its committed snapshot.json exists but could not be read "
        "(not a regular file); remove it, then re-publish (build --publish-snapshot)"]
    manifest.rmdir()  # followed
    assert publish(tmp_path).returncode == 0
    assert check_fresh(tmp_path).returncode == 0


def test_a_dependent_is_listed_beside_the_component_whose_inputs_moved(tmp_path):
    """`openapi-specs` claims the compose file by `*.yml` too, so both readers' inputs moved;
    only `compose-services` feeds `b` (T630: listed, as the load and the plan list it)."""
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    (tmp_path / "ws" / "a" / "docker-compose.yml").write_text(
        compose(svc("b", "../b", "web-value-2")), encoding="utf-8")
    proc = check_fresh(tmp_path)
    assert proc.returncode == 2 and proc.stderr == "", proc.stdout + proc.stderr
    assert lines(proc, "STALE") == [
        "freshness: STALE a - files read by compose-services, openapi-specs changed",
        "freshness: STALE b - depends on compose-services, whose inputs changed in another "
        "component"]
    assert verdict(tmp_path)["freshness"] == {
        "fresh": [], "refused": {}, "unparsed": {},
        "stale": {"a": "files read by compose-services, openapi-specs changed",
                  "b": "depends on compose-services, whose inputs changed in another "
                       "component"}}


def test_a_current_shard_whose_parser_is_still_missing_is_noted_at_exit_0(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    capsys.readouterr()
    assert B.main(["--workspace", str(tmp_path / "ws" / "workspace.toml"),
                   "--check-fresh"]) == 0
    out = capsys.readouterr().out
    assert [ln for ln in out.splitlines() if ln.startswith("freshness: NOTE ")] == [NOTE]
    assert "freshness: 2 component(s) current" in out
    assert verdict(tmp_path)["freshness"] == {
        "fresh": ["a", "b"], "stale": {}, "refused": {},
        "unparsed": {"compose-services": ["a"]}}


def test_once_the_parser_is_installed_the_shard_is_stale_and_not_noted(tmp_path, monkeypatch):
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    monkeypatch.undo()
    proc = check_fresh(tmp_path)
    assert proc.returncode == 2 and lines(proc, "NOTE") == [], proc.stdout
    assert lines(proc, "STALE") == [
        "freshness: STALE a - its shard was published while `compose-services` had no parser "
        "installed",
        "freshness: STALE b - depends on compose-services, which had no parser installed when "
        "its shard was published"]
    assert publish(tmp_path).returncode == 0  # the remedy
    assert check_fresh(tmp_path).returncode == 0


def test_a_stale_shard_whose_parser_is_still_missing_is_stale_and_noted(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(props, "yaml_available", lambda: False)
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    (tmp_path / "ws" / "a" / "docker-compose.yml").write_text(
        compose(svc("b", "../b", "web-value-2")), encoding="utf-8")
    capsys.readouterr()
    assert B.main(["--workspace", str(tmp_path / "ws" / "workspace.toml"),
                   "--check-fresh"]) == 2
    out = capsys.readouterr().out.splitlines()
    assert "freshness: STALE a - files read by compose-services, openapi-specs changed" in out
    assert [ln for ln in out if ln.startswith("freshness: NOTE ")] == [NOTE]


def test_the_dict_is_what_the_mcp_tool_serves(tmp_path):
    """`fresh`, `stale`, `refused`, `unparsed` and `status` - JSON all the way down."""
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    report = verdict(tmp_path)
    assert json.loads(json.dumps(report)) == report
    assert report["status"] == "fresh"
    assert report["freshness"] == {"fresh": ["a", "b"], "stale": {}, "refused": {},
                                   "unparsed": {}}
