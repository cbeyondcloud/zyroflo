"""T436: `--check-snapshot` must emit COULD-NOT-LOOK when a committed manifest exists but
cannot be read — never "shards match" (exit 0), and never a false "DRIFT".

The drift gate recomputes each component's shard bytes and byte-compares them against the
committed `shard.jsonl` / `shard.unresolved.jsonl`. It never opened the manifest for that
comparison — it only tested that the manifest FILE EXISTS. So a manifest corrupted to
unreadable JSON, with its shard bytes left intact, sailed straight through as "match" and
exited 0: a confident wrong answer, the exact class this repo exists to refuse ("an
external tool returning nothing at exit 0 is not evidence of absence").

A corrupt manifest is NOT evidence the bytes drifted, so reporting DRIFT would be a
confident wrong answer in the OTHER direction. The honest verdict is could-not-look: this
build cannot verify whether the shard matches, so it refuses and says why — distinct from
both "matches" and "drifted".

SPAWNED, not `B.main(...)` in-process: an in-process call cannot tell a controlled refusal
from a propagating traceback (both fail the calling test), and this asks what a PERSON
running the door sees — its exit code and both streams. The two positive controls are
spawned the same way, so the could-not-look case sits provably BETWEEN a clean match
(exit 0) and a real drift (exit 2, "DRIFT").
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import tools.graph.build as B

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"
COMPONENTS = ["orders-api", "orders-worker", "orders-shared", "web-console",
              "orders-notifier", "deploy-charts", "legacy-reports"]
GRAPH = Path(__file__).resolve().parents[1]
ROOT = GRAPH.parents[1]


def ws(tmp_path: Path) -> Path:
    work = tmp_path / "ws"
    shutil.copytree(FIXTURE, work)
    return work


def publish(work: Path, out: str = "out") -> int:
    return B.main(["--workspace", str(work / "workspace.toml"),
                   "--out", str(work.parent / out), "--publish-snapshot"])


def spawn_check(work: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GRAPH / "build.py"), "--workspace",
         str(work / "workspace.toml"), "--out", str(out), "--check-snapshot"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT))


def corrupt_all_manifests(work: Path) -> None:
    """Leave every shard's BYTES intact and make only the manifest unreadable — invalid
    JSON. The digest the check recomputes is over the shard bytes, so nothing has
    "drifted"; what changed is that the shard's integrity can no longer be verified."""
    for name in COMPONENTS:
        manifest = work / name / ".sdlc" / "graph" / "snapshot.json"
        assert manifest.is_file(), f"{name} was never published"
        manifest.write_text("this is not valid json {{{{\n", encoding="utf-8")


def test_a_corrupt_manifest_is_could_not_look_not_a_clean_match(tmp_path):
    """THE defect. All manifests corrupted, all shard bytes intact: the gate must refuse
    with a could-not-look verdict, not report a clean match and exit 0."""
    work = ws(tmp_path)
    assert publish(work) == 0
    corrupt_all_manifests(work)

    proc = spawn_check(work, tmp_path / "check")

    assert proc.returncode == 2, (
        f"a manifest that cannot be read is could-not-look, not a clean tree — the "
        f"person running this door must see a REFUSAL (exit 2), not exit 0:\n"
        f"returncode={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}")
    assert proc.stderr == "", (
        f"a refusal reaches a person as a printed line, never a traceback: "
        f"{proc.stderr!r}")
    assert "shard(s) match their committed bytes" not in proc.stdout, (
        f"a corrupt manifest was reported as a clean match: {proc.stdout!r}")
    assert "DRIFT" not in proc.stdout, (
        f"a corrupt manifest is NOT evidence the bytes drifted — reporting DRIFT is a "
        f"confident wrong answer in the other direction: {proc.stdout!r}")
    assert "REFUSING" in proc.stdout and "could not be read" in proc.stdout, (
        f"the verdict must name could-not-look, distinct from match and from drift: "
        f"{proc.stdout!r}")
    for name in COMPONENTS:
        assert name in proc.stdout, (
            f"every component whose manifest could not be read must be named "
            f"(zero silent drops): {name} missing from {proc.stdout!r}")


def test_control_intact_snapshot_still_matches(tmp_path):
    """Positive control (a): an untouched, matching snapshot still reports match, exit 0.
    Proves the could-not-look refusal above is not merely "the gate now refuses
    everything"."""
    work = ws(tmp_path)
    assert publish(work) == 0

    proc = spawn_check(work, tmp_path / "check")

    assert proc.returncode == 0, (
        f"an intact matching snapshot must still pass:\n"
        f"returncode={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}")
    assert "shard(s) match their committed bytes" in proc.stdout, proc.stdout


def test_control_a_drifted_shard_still_reports_drift(tmp_path):
    """Positive control (b): a genuinely drifted shard (a source file changed after
    publish) still reports DRIFT, exit 2. The could-not-look case must sit BETWEEN this
    and the clean match above — not collapse into either."""
    work = ws(tmp_path)
    assert publish(work) == 0
    props = (work / "orders-worker" / "src" / "main" / "resources"
             / "application.properties")
    props.write_text(props.read_text(encoding="utf-8").replace(
        "order-events-v1", "order-events-v9"), encoding="utf-8")

    proc = spawn_check(work, tmp_path / "check")

    assert proc.returncode == 2, (
        f"a genuinely drifted shard must still be caught:\n"
        f"returncode={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}")
    assert "DRIFT in orders-worker" in proc.stdout, proc.stdout
    assert "could not be read" not in proc.stdout, (
        f"a real drift is not could-not-look — the two verdicts must stay distinct: "
        f"{proc.stdout!r}")
