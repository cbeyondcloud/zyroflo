"""T163: the CLI refusal branches, SPAWNED.

`build.py`'s argument-combination refusals and `mcp_server.py`'s startup refusals each
`print(...)` and `return 2`, but the suite only ever spawned `build.py` once (asserting
`code == 0`) and never spawned `mcp_server.py` at all - it was only imported. Item 117's
lesson (`policy.py --validate` handing a traceback for the one document class it exists
to refuse, while every in-process test passed) is what an unspawned refusal costs when
it is finally paid: calling `main()` in-process cannot tell a clean `return 2` from a
traceback, because pytest renders both as a failing test. The contract a person meets is
only proven by a process that reads its exit code AND its first printed line.

These are coverage tests: every branch below behaves correctly today. They assert the
FIRST LINE of each refusal, not merely that something was refused, so a message moved to
the wrong branch (a "!! ..." line that refuses the wrong thing) is caught rather than
passed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _spawn(module: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, *args],
                          cwd=str(ROOT), capture_output=True, text=True)


def _first_line(proc: subprocess.CompletedProcess) -> str:
    lines = proc.stdout.splitlines()
    return lines[0] if lines else ""


# (argv, distinctive substring of the refusal's first line). Each pair names exactly ONE
# pre-flight branch - all fire before the workspace is resolved, so no fixture is needed.
BUILD_REFUSALS = [
    (["--publish-snapshot", "--from-snapshot"], "cannot be combined: publishing, loading"),
    (["--from-snapshot", "--only", "x"], "--from-snapshot runs no extractors"),
    (["--incremental", "--from-snapshot"], "--incremental is a way of RUNNING the build"),
    (["--incremental", "--only", "x"], "--incremental decides what to run from the stamps"),
    (["--propose-canonical", "--check-fresh"], "--propose-canonical drafts from the build"),
    (["--check-fresh", "--only", "x"], "--check-fresh compares the whole committed snapshot"),
    (["--target", "a", "--workspace", "b"], "--target and --workspace cannot be combined"),
    # Batch 7. Each names a workspace that does not exist, so without its refusal the
    # command fails later, on a different first line, rather than building anything.
    # T521: one narrowed build replaced every committed shard with a partial one.
    (["--publish-snapshot", "--only", "x", "--workspace", "absent.toml"],
     "--publish-snapshot writes each component's committed shard"),
    # T524 and T525: a narrowed check called a drifting tree clean, or a clean one
    # drifted, and the second's remedy was T521's data loss.
    (["--check-snapshot", "--components", "x", "--workspace", "absent.toml"],
     "--check-snapshot compares every committed shard"),
    (["--check-snapshot", "--only", "x", "--workspace", "absent.toml"],
     "--check-snapshot compares every committed shard"),
]


@pytest.mark.parametrize("argv, first_line", BUILD_REFUSALS)
def test_build_refuses_argument_combinations_by_exit_and_first_line(argv, first_line):
    proc = _spawn("tools.graph.build", argv)
    assert proc.returncode == 2, f"exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert first_line in _first_line(proc), proc.stdout or proc.stderr


@pytest.mark.parametrize("kind, first_line", [
    ("no-graph-and-no-target", "one of --graph-dir and --target"),
    ("graph-jsonl-absent", "does not exist"),
])
def test_mcp_server_refuses_startup_by_exit_and_first_line(kind, first_line, tmp_path):
    # The graph-dir path is built under tmp_path at runtime; no machine path is written
    # into the test, and the directory genuinely has no graph.jsonl.
    argv = [] if kind == "no-graph-and-no-target" else ["--graph-dir", str(tmp_path / "nope")]
    proc = _spawn("tools.graph.mcp_server", argv)
    assert proc.returncode == 2, f"exit {proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    assert first_line in _first_line(proc), proc.stdout or proc.stderr


# ── batch 7: the doors that still took a bad input as a traceback, or wrote where
# nothing should ─────────────────────────────────────────────────────────────────

#: A header and one repository node: enough for every door below to get past its
#: load, so each test reaches the check it is about.
MINIMAL = (json.dumps({"kind": "header", "schema_version": 1}) + "\n"
           + json.dumps({"kind": "node", "id": "repo:svc", "type": "repo",
                         "props": {"component": "svc"}}) + "\n")


def _graph_dir(where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    (where / "graph.jsonl").write_text(MINIMAL, encoding="utf-8")
    (where / "unresolved.jsonl").write_text("", encoding="utf-8")
    (where / "build_report.json").write_text(
        json.dumps({"status": "ok", "run_id": "t"}), encoding="utf-8")
    return where


def _git_init(where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(where)], check=True, capture_output=True,
                   timeout=120)
    return where


def _said(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout + proc.stderr


def test_mcp_server_refuses_a_graph_with_no_nodes_at_startup(tmp_path):
    """T520: a zero-byte `graph.jsonl` passed the startup check, which only asked
    whether the file existed, and the server then served 0 nodes beside the last
    build's `ok`. Every completed build writes one repo node per component, so a
    graph holding none is not one. stdin is closed, so a server that does start
    reads end-of-input and stops rather than waiting."""
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "graph.jsonl").write_text("", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "tools.graph.mcp_server",
                           "--graph-dir", str(gdir)], cwd=str(ROOT), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert proc.returncode == 2, _said(proc)
    assert "holds no nodes" in _first_line(proc), _said(proc)


@pytest.mark.parametrize("module", ["tools.graph.impact", "tools.graph.pack"])
def test_a_diff_that_is_not_a_readable_file_is_refused_by_name(module, tmp_path):
    """T523: `exists()` is true of a directory, so its `read_text` raised - a
    traceback at exit 1 where every other damaged input to these CLIs is exit 2."""
    gdir = _graph_dir(tmp_path / "g")
    not_a_file = tmp_path / "a-directory"
    not_a_file.mkdir()
    argv = ["--graph-dir", str(gdir), "--diff", str(not_a_file), "--component", "svc"]
    if module == "tools.graph.pack":
        argv += ["--out", str(tmp_path / "pack"), "--no-svg"]
    proc = _spawn(module, argv)
    assert proc.returncode == 2 and "Traceback" not in _said(proc), _said(proc)
    assert "is not a file" in _first_line(proc), _said(proc)


def test_a_negative_sample_is_refused_by_name(tmp_path):
    """T523: `random.sample` refused -5 with a ValueError traceback at exit 1."""
    proc = _spawn("tools.graph.validate", ["--graph-dir", str(_graph_dir(tmp_path / "g")),
                                           "--sample", "-5"])
    assert proc.returncode == 2 and "Traceback" not in _said(proc), _said(proc)
    assert "--sample" in _first_line(proc), _said(proc)


def test_impact_refuses_an_absolute_seed_by_name_as_pack_does(tmp_path):
    """T527: item 128's guard was added to `pack.py` only, so `impact.py` still said
    "component 'D:' is not in this graph" - true, and about the wrong thing."""
    seed = (tmp_path / "src" / "app.py").as_posix()          # absolute, forward slashes
    proc = _spawn("tools.graph.impact", ["--graph-dir", str(_graph_dir(tmp_path / "g")),
                                         "--seed", seed])
    assert proc.returncode == 2, _said(proc)
    assert "is an absolute path" in _first_line(proc), _said(proc)


@pytest.mark.parametrize("module", ["tools.graph.pack", "tools.graph.export_okf"])
def test_a_derived_artifact_is_refused_in_a_repository_it_is_not_about(module, tmp_path):
    """T528: `build.py` and `workspace_report.py` go through `outdir.check_output_dir`;
    a context pack or an OKF bundle - every component's names, paths and lines - was
    written into an unrelated repository at exit 0."""
    gdir = _graph_dir(tmp_path / "g")
    foreign = _git_init(tmp_path / "someone-else") / "dump"
    argv = (["--graph-dir", str(gdir), "--seed", "svc/app.py", "--out", str(foreign),
             "--no-svg"] if module == "tools.graph.pack"
            else ["--graph", str(gdir), "--out", str(foreign)])
    proc = _spawn(module, argv)
    assert proc.returncode == 2, _said(proc)
    assert "refusing to write to" in _first_line(proc), _said(proc)
    assert not foreign.exists(), "nothing may be written before the refusal"


@pytest.mark.parametrize("module", ["tools.graph.pack", "tools.graph.export_okf"])
def test_the_graphs_own_repository_still_takes_its_derived_artifacts(module, tmp_path):
    """The control, and what the pipeline depends on: a run's pack goes to
    `<repo>/.sdlc/.tmp/<run>/pack` (stage.py), beside the graph it came from."""
    own = _git_init(tmp_path / "repo")
    gdir = _graph_dir(own / ".sdlc" / "graph")
    dest = own / ".sdlc" / ".tmp" / "run-1" / "out"
    argv = (["--graph-dir", str(gdir), "--seed", "svc/app.py", "--out", str(dest),
             "--no-svg"] if module == "tools.graph.pack"
            else ["--graph", str(gdir), "--out", str(dest)])
    proc = _spawn(module, argv)
    assert "refusing to write" not in _said(proc), _said(proc)
    assert proc.returncode == 0, _said(proc)
    assert dest.is_dir() and any(dest.iterdir())


def test_a_pack_is_refused_in_its_own_repositorys_committed_tree(tmp_path):
    """A pack is scratch: `pack.py --out`'s own help says derived artifacts belong
    there and never in git, and stage.py sends a run's to `.sdlc/.tmp/`. So its own
    repository takes it only there - not in a committed run folder - and the refusal
    names that place, not the committed graph directory a build would use."""
    own = _git_init(tmp_path / "repo")
    gdir = _graph_dir(own / ".sdlc" / "graph")
    dest = own / ".sdlc" / "runs" / "r1" / "pack"
    proc = _spawn("tools.graph.pack", ["--graph-dir", str(gdir), "--seed", "svc/app.py",
                                       "--out", str(dest), "--no-svg"])
    assert proc.returncode == 2, _said(proc)
    assert "refusing to write to" in _first_line(proc), _said(proc)
    assert f"{Path('.sdlc') / '.tmp'}" in proc.stdout, proc.stdout
    assert ".sdlc/graph" not in proc.stdout.replace("\\", "/"), proc.stdout
    assert not dest.exists()
