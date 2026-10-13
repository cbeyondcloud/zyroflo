"""Batch 16 (T628; the owner's ruling 29): a `--from-snapshot` load reads every shard once and
judges it by the one verdict. Every shard it cannot trust is refused by name with its remedy,
joins `snapshot.mismatched` and narrows the load; following the remedy is accepted. A stale
shard is loaded and narrows the load (ruling 16).

Each cell is a spawned process: a refusal is only a refusal if a person gets exit 2 and the
words, never a traceback (CLAUDE.md: a CLI's contract is a process).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import snapshot as SN  # noqa: E402
from tools.graph.tests._estate import build, compose, lay_down, producer, svc  # noqa: E402

FEED = {"a": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
        "b": {"app.py": producer("TOPIC")}}
FOLLOW = " (build --publish-snapshot)"


def run(where: Path, *flags: str, out: str = "out", no_yaml: bool = False
        ) -> subprocess.CompletedProcess:
    """The build as a process; `no_yaml` starts it with PyYAML reported missing, as a tree
    without it would be read."""
    argv = ["--workspace", str(where / "ws" / "workspace.toml"), "--out", str(where / out),
            "--run-id", "fixed", *flags]
    code = (["-c", "import sys; from tools.graph.core.lib import props; "
                   "props.yaml_available = lambda: False; import tools.graph.build as B; "
                   "raise SystemExit(B.main(sys.argv[1:]))"] if no_yaml
            else ["-m", "tools.graph.build"])
    return subprocess.run([sys.executable, *code, *argv], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def report(where: Path, out: str = "out") -> dict:
    return json.loads((where / out / "build_report.json").read_text(encoding="utf-8"))


def shard_dir(where: Path, path: str = "a") -> Path:
    return where / "ws" / path / ".sdlc" / "graph"


def restamp(dir_: Path) -> None:
    """Re-record the digest over hand-edited bytes, so the refusal is the edit's own and not
    the digest's (as `test_snapshot_shards.py` does)."""
    manifest = json.loads((dir_ / SN.MANIFEST).read_text(encoding="utf-8"))
    manifest["sha256"] = SN._digest(dir_)
    (dir_ / SN.MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                    encoding="utf-8", newline="\n")


def _replace_with_dir(path: Path) -> None:
    path.unlink()
    path.mkdir()


def _schema(dir_: Path) -> None:
    lines = (dir_ / SN.SHARD).read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["schema_version"] = 99999
    (dir_ / SN.SHARD).write_text("\n".join([json.dumps(header, sort_keys=True), *lines[1:]])
                                 + "\n", encoding="utf-8", newline="\n")
    restamp(dir_)


def _junk_rows(dir_: Path) -> None:
    (dir_ / SN.SHARD_UNRESOLVED).write_text("not json\n", encoding="utf-8", newline="\n")
    restamp(dir_)


#: Each refusal class: the damage, the reason the verdict gives, its remedy, and the step the
#: remedy asks before the re-publish.
CLASSES = {
    "manifest-not-json": (
        lambda d: (d / SN.MANIFEST).write_text("{not json", encoding="utf-8"),
        "its committed snapshot.json exists but could not be read (not JSON)",
        "re-publish", None),
    "manifest-not-a-file": (
        lambda d: _replace_with_dir(d / SN.MANIFEST),
        "its committed snapshot.json exists but could not be read (not a regular file)",
        "remove it, then re-publish", lambda d: (d / SN.MANIFEST).rmdir()),
    "rows-file-not-a-file": (
        lambda d: _replace_with_dir(d / SN.SHARD_UNRESOLVED),
        "its shard file `a/.sdlc/graph/shard.unresolved.jsonl` could not be read (not a "
        "regular file)",
        "remove it, then re-publish", lambda d: (d / SN.SHARD_UNRESOLVED).rmdir()),
    "facts-file-missing": (
        lambda d: (d / SN.SHARD).unlink(),
        "its shard file `a/.sdlc/graph/shard.jsonl` is missing", "re-publish", None),
    "facts-unreadable": (
        _schema, "its shard's facts could not be read (", "re-publish", None),
    "rows-unreadable": (
        _junk_rows, "its shard's rows could not be read (line 1: not JSON)", "re-publish", None),
}


@pytest.mark.parametrize("kind", list(CLASSES))
def test_every_refusal_is_named_counted_and_followed(tmp_path, kind):
    damage, reason, remedy, step = CLASSES[kind]
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    damage(shard_dir(tmp_path))
    proc = run(tmp_path, "--from-snapshot")
    assert (proc.returncode, proc.stderr) == (2, ""), proc.stdout + proc.stderr
    (line,) = [ln for ln in proc.stdout.splitlines() if ln.startswith("snapshot: REFUSING ")]
    assert line.startswith(f"snapshot: REFUSING a - {reason}"), line
    assert line.endswith(f"; {remedy}{FOLLOW}"), line
    loaded = report(tmp_path)
    assert loaded["status"] == "snapshot-mismatch"
    assert loaded["snapshot"]["mismatched"] == ["a"] and loaded["snapshot"]["loaded"] == ["b"]
    assert "loaded 1 of 2 component shard(s), 1 refused; totals" in proc.stdout
    (note,) = loaded["notes"]
    assert f"refused - {reason}" in note
    if step:
        step(shard_dir(tmp_path))  # followed
    assert run(tmp_path, "--publish-snapshot").returncode == 0
    again = run(tmp_path, "--from-snapshot")
    assert again.returncode == 0 and report(tmp_path)["notes"] == [], again.stdout


def test_the_digest_class_keeps_its_sentence_and_its_group(tmp_path):
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    shard = shard_dir(tmp_path) / SN.SHARD
    shard.write_bytes(shard.read_bytes() + b"\n")
    proc = run(tmp_path, "--from-snapshot")
    assert (proc.returncode, proc.stderr) == (2, ""), proc.stdout + proc.stderr
    assert ("snapshot: REFUSING a - its shard does not match its own recorded digest; a hand "
            "edit or a half-written publish. Rebuild and re-publish it") in proc.stdout.splitlines()
    assert "loaded 1 of 2 component shard(s), 1 refused; totals" in proc.stdout
    (note,) = report(tmp_path)["notes"]
    assert "refused for a digest mismatch: a" in note


def test_a_stale_shard_is_loaded_and_narrows_with_its_reason(tmp_path):
    """A dependent is stale like any other (T630): `b` is loaded, and narrows the load."""
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    (tmp_path / "ws" / "a" / "docker-compose.yml").write_text(
        compose(svc("b", "../b", "web-value-2")), encoding="utf-8")
    proc = run(tmp_path, "--from-snapshot")
    assert proc.returncode == 0 and proc.stderr == "", proc.stdout + proc.stderr
    loaded = report(tmp_path)
    assert loaded["snapshot"]["loaded"] == ["a", "b"]
    (note,) = loaded["notes"]
    assert ("stale - depends on compose-services, whose inputs changed in another component: "
            "b") in note
    assert "stale - files read by compose-services, openapi-specs changed: a" in note


def test_a_path_with_a_space_and_a_non_ascii_character_is_named_intact(tmp_path):
    """Review focus 4: every REFUSING, REFUSED and NOTE line names it intact on a UTF-8
    stdout, and nothing reaches stderr."""
    estate = {"svc café": {"docker-compose.yml": compose(svc("b", "../b", "web-value"))},
              "b": {"app.py": producer("TOPIC")},
              "__components__": [("café", "svc café"), ("b", "b")]}
    lay_down(tmp_path, estate)
    assert run(tmp_path, "--publish-snapshot", no_yaml=True).returncode == 0
    noted = run(tmp_path, "--check-fresh", no_yaml=True)
    assert (noted.returncode, noted.stderr) == (0, ""), noted.stdout + noted.stderr
    assert ("freshness: NOTE `compose-services` could not parse its files when its shard was "
            "published - no parser `yaml` installed: café; install the parser each "
            "`grammar-unavailable` row names, then re-publish the shards it names"
            ) in noted.stdout.splitlines()
    _replace_with_dir(shard_dir(tmp_path, "svc café") / SN.SHARD)
    reason = ("its shard file `svc café/.sdlc/graph/shard.jsonl` could not be read (not a "
              "regular file); remove it, then re-publish" + FOLLOW)
    fresh = run(tmp_path, "--check-fresh")
    assert (fresh.returncode, fresh.stderr) == (2, ""), fresh.stdout + fresh.stderr
    assert f"freshness: REFUSED café - {reason}" in fresh.stdout.splitlines()
    load = run(tmp_path, "--from-snapshot")
    assert (load.returncode, load.stderr) == (2, ""), load.stdout + load.stderr
    assert f"snapshot: REFUSING café - {reason}" in load.stdout.splitlines()


def test_every_shard_refused(tmp_path):
    """Review focus 5: `--check-fresh` exits 2 with no "current" line, an empty `fresh` and
    status `stale`; the load exits 2, names each shard, and keeps `main`'s all-refused status
    and its summary's denominator."""
    assert build(tmp_path, FEED, "--publish-snapshot").code == 0
    for comp in ("a", "b"):
        shard = shard_dir(tmp_path, comp) / SN.SHARD
        shard.write_bytes(shard.read_bytes() + b"\n")
    fresh = run(tmp_path, "--check-fresh")
    assert (fresh.returncode, fresh.stderr) == (2, ""), fresh.stdout + fresh.stderr
    assert "component(s) current" not in fresh.stdout
    assert [ln.split(" - ")[0] for ln in fresh.stdout.splitlines()
            if ln.startswith("freshness: REFUSED ")] == ["freshness: REFUSED a",
                                                        "freshness: REFUSED b"]
    verdict = B.check_freshness(tmp_path / "ws" / "workspace.toml")
    assert (verdict["status"], verdict["freshness"]["fresh"]) == ("stale", [])
    assert sorted(verdict["freshness"]["refused"]) == ["a", "b"]
    load = run(tmp_path, "--from-snapshot")
    assert (load.returncode, load.stderr) == (2, ""), load.stdout + load.stderr
    assert [ln.split(" - ")[0] for ln in load.stdout.splitlines()
            if ln.startswith("snapshot: REFUSING ")] == ["snapshot: REFUSING a",
                                                        "snapshot: REFUSING b"]
    loaded = report(tmp_path)
    assert loaded["status"] == "snapshot-mismatch"
    assert loaded["snapshot"] == {"loaded": [], "missing": [], "mismatched": ["a", "b"]}
    assert "loaded 0 of 2 component shard(s), 2 refused; totals" in load.stdout
