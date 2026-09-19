"""T425: `--check-snapshot` must emit COULD-NOT-LOOK when a component's freshly extracted
facts cannot be serialized — never an uncaught TypeError (exit 1), never a raw-pool count,
never a false "DRIFT" or "match".

The drift gate turns each component's fresh facts INTO shard bytes (through the same
emitter the committed shard was written with) and byte-compares. A node or edge whose
`props` carry a non-JSON value — a `set`, `bytes`, a circular structure — is valid in
memory but cannot pass `json.dumps`, so the serializer raises. That raise used to escape
`snapshot.check` uncaught, propagate through `build.py`'s `--check-snapshot` path, and reach
the person running the door as a traceback at exit 1: a question answered with NO answer,
where a clean tree exits 0 and a real drift exits 2 ("it replaced a wrong number with no
answer").

A fact this build cannot serialize is NOT evidence the committed bytes drifted (reporting
DRIFT would be a confident wrong answer in one direction) and is NOT a clean match (exit 0
would be the same wrong answer in the other direction). The honest verdict is
could-not-look: this build cannot produce comparable bytes for that component, so it refuses
and says why — the same shape T436 already gave a manifest that is present but unreadable,
one flag along in this same command.

SPAWNED, not `B.main(...)` in-process: an in-process call cannot tell a controlled refusal
from a propagating traceback (item 96 / item 117 — both fail the calling test), and this
asks what a PERSON running the door sees: its exit code and both streams. The unserializable
prop is injected through a DRIVER run as a real `sys.executable` process (the same technique
`test_a_broken_build_refuses_as_a_real_process` uses for the broken-build refusal), so a
refusal and a crash cannot be the same green test. Two positive controls are spawned the
same way, so the could-not-look case sits provably BETWEEN a clean match (exit 0) and a real
drift (exit 2, "DRIFT").
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

# A driver that poisons ONE component's freshly extracted facts with a node whose `props`
# hold a set — valid in memory, not JSON-serializable — then runs the real CLI. It wraps
# `component_facts`, the exact point whose result `--check-snapshot` serializes through
# `snapshot.check` -> `write_shard` -> `emit.write_jsonl` -> `Node.to_jsonl` -> `json.dumps`.
_DRIVER = (
    "import sys\n"
    f"sys.path.insert(0, {str(ROOT)!r})\n"
    "import tools.graph.build as B\n"
    "from tools.graph.core.model import Node\n"
    "_real = B.component_facts\n"
    "def poisoned(components, regs, owned):\n"
    "    facts = _real(components, regs, owned)\n"
    "    if 'orders-worker' in facts:\n"
    "        facts['orders-worker'].nodes.append(\n"
    "            Node(id='svc:poison', type='svc', props={'tags': {'a', 'b'}}))\n"
    "    return facts\n"
    "B.component_facts = poisoned\n"
    "raise SystemExit(B.main(sys.argv[1:]))\n"
)


def ws(tmp_path: Path) -> Path:
    work = tmp_path / "ws"
    shutil.copytree(FIXTURE, work)
    return work


def publish(work: Path, out: str = "out") -> int:
    return B.main(["--workspace", str(work / "workspace.toml"),
                   "--out", str(work.parent / out), "--publish-snapshot"])


def spawn_check_poisoned(tmp_path: Path, work: Path,
                         out: Path) -> subprocess.CompletedProcess:
    driver = tmp_path / "poison_driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(driver), "--workspace", str(work / "workspace.toml"),
         "--out", str(out), "--check-snapshot"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT))


def spawn_check(work: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GRAPH / "build.py"), "--workspace",
         str(work / "workspace.toml"), "--out", str(out), "--check-snapshot"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT))


def test_unserializable_fresh_facts_are_could_not_look_not_a_crash(tmp_path):
    """THE defect. A committed intact snapshot, then a check whose fresh facts carry a
    non-JSON prop: the gate must refuse with a could-not-look verdict (exit 2, printed),
    not crash with an uncaught TypeError (exit 1, traceback) and not fall back to a count
    or a false drift/match."""
    work = ws(tmp_path)
    assert publish(work) == 0

    proc = spawn_check_poisoned(tmp_path, work, tmp_path / "check")

    assert proc.returncode == 2, (
        f"fresh facts that cannot be serialized are could-not-look, not a crash — the "
        f"person running this door must see a REFUSAL (exit 2), not an uncaught "
        f"TypeError (exit 1):\nreturncode={proc.returncode}\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}")
    assert proc.stderr == "", (
        f"a refusal reaches a person as a printed line, never a traceback: "
        f"{proc.stderr!r}")
    assert "Traceback" not in proc.stdout, (
        f"the crash must not merely be relocated to stdout: {proc.stdout!r}")
    assert "REFUSING orders-worker" in proc.stdout and "serialize" in proc.stdout, (
        f"the verdict must name could-not-look for the offending component, distinct "
        f"from match and from drift: {proc.stdout!r}")
    assert "shard(s) match their committed bytes" not in proc.stdout, (
        f"an uncomparable component was reported as a clean match: {proc.stdout!r}")
    assert "DRIFT" not in proc.stdout, (
        f"a fact this build cannot serialize is NOT evidence the bytes drifted — "
        f"reporting DRIFT is a confident wrong answer in the other direction: "
        f"{proc.stdout!r}")
    assert "as written to disk" not in proc.stdout and "wrote " not in proc.stdout, (
        f"a check writes nothing and must not claim it did: {proc.stdout!r}")


def test_control_intact_snapshot_still_matches(tmp_path):
    """Positive control (a): an untouched, matching snapshot still reports match, exit 0.
    Proves the could-not-look refusal is not merely "the gate now refuses everything"."""
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
    assert "serialize" not in proc.stdout, (
        f"a real drift is not could-not-look — the two verdicts must stay distinct: "
        f"{proc.stdout!r}")
