"""Estate mode in sdlc-init (item 15): one command, a verified and graph-loaded estate.

`--estate` opens the seam item 80 deliberately closed: a multi-repo workspace graph had
no repository to land in, so the workspace path refused to chain the build. Item 13's
per-component shards and the target-repo convention changed that fact — the estate graph
now has a home, the TARGET's own `.sdlc/graph/` — and this flag is the recorded successor
to that refusal, not a silent reversal of it.

What estate mode measured out to, against the plan's original six-step sketch:

* verify vendored binaries — exists (`third_party.py --verify`), spawned and reprinted;
* environment/Neo4j — REPORTED, never started: the store is optional by the parity
  doctrine (item 62), and starting containers unasked is a state change nobody approved;
* attach census — reported per component (git / declared / shallow / skipped).
  **Clone-when-missing is deferred with its reason**: the workspace schema carries no
  remote field, so there is nothing to clone FROM; the field arrives with its consumer;
* graph — the chained build, workspace-wide, into the target's `.sdlc/graph/`;
  `--from-snapshot` forwards to the builder, whose own validation rules on shard state;
* MCP registration — deferred to item 23, which owned the server it would register;
  landed there: `register_mcp` runs after the chained build for estate and
  single-repo installs alike (its own suite is `test_mcp_registration.py`).

The single-repo path is untouched; most tests here replace `run_builder`/`run_capture`,
the two seams, exactly as the item-80 suite does.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"
FIXTURE_WS = ROOT / "tools" / "graph" / "tests" / "fixtures" / "orderflow" / "workspace.toml"


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_estate", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init_estate"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()

assert si.find_uv(), (
    "uv is not on PATH; this suite drives the real chained build and needs it. "
    "`uv run pytest` guarantees it.")


class Spy:
    def __init__(self, code: int = 0):
        self.code = code
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, cmd: list[str], cwd: Path) -> int:
        self.calls.append((list(cmd), Path(cwd)))
        return self.code


@pytest.fixture()
def spy(monkeypatch) -> Spy:
    s = Spy()
    monkeypatch.setattr(si, "run_builder", s)
    return s


@pytest.fixture()
def quiet_verify(monkeypatch):
    """Preflight's verify spawn, canned."""
    monkeypatch.setattr(si, "run_capture",
                        lambda cmd, cwd: (0, "platform: test\nok rg 1.0.0\n"))


def install(root: Path, **kw) -> int:
    kw.setdefault("tools", ["claude"])
    kw.setdefault("force", False)
    kw.setdefault("dry_run", False)
    return si.install(root, **kw)


# ── flag wiring ───────────────────────────────────────────────────────────────


def test_estate_requires_a_workspace(tmp_path):
    with pytest.raises(SystemExit) as exc:
        si.main(["--target", str(tmp_path), "--estate"])
    assert "--workspace" in str(exc.value)


def test_from_snapshot_requires_estate(tmp_path):
    ws = tmp_path / "ws.toml"
    ws.write_text(f'root = "{FIXTURE_WS.parent.as_posix()}"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        si.main(["--target", str(tmp_path), "--workspace", str(ws), "--from-snapshot"])
    assert "--estate" in str(exc.value)


# ── the chain decision ────────────────────────────────────────────────────────


def test_estate_opens_the_workspace_chain(tmp_path):
    ws = si.read_workspace(FIXTURE_WS)
    assert si.no_chain_reason(tmp_path, enabled=True, workspace=ws,
                              harness=ROOT, estate=True) is None


def test_without_estate_the_refusal_now_offers_the_flag(tmp_path):
    ws = si.read_workspace(FIXTURE_WS)
    reason = si.no_chain_reason(tmp_path, enabled=True, workspace=ws, harness=ROOT)
    assert reason is not None and "--estate" in reason, \
        "the refusal must name the flag that lifts it"


def test_the_single_repo_decision_is_untouched(tmp_path):
    (tmp_path / ".git").mkdir()
    assert si.no_chain_reason(tmp_path, enabled=True, workspace=None,
                              harness=ROOT) is None


def test_the_estate_builder_command(tmp_path):
    cmd = si.builder_command_estate(FIXTURE_WS, tmp_path, ROOT, from_snapshot=False)
    assert Path(cmd[0]).is_absolute(), "a bare program name would let Windows search " \
                                       "the current directory first"
    assert "--workspace" in cmd and str(FIXTURE_WS) in cmd
    out_i = cmd.index("--out")
    assert cmd[out_i + 1] == str(tmp_path / ".sdlc" / "graph")
    # The estate graph is committed into the TARGET hub's own .sdlc/, so the command must
    # DECLARE that hub as the output's home. Without it the builder refuses the path as "a
    # repository this graph is not about" (T420) and the whole estate install exits 3 against
    # any real repository. The declaration lifts check_output_dir's refusal for exactly this
    # repo and leaves it intact for every other.
    assert "--home" in cmd, "the command must declare the target hub as the graph's home"
    assert cmd[cmd.index("--home") + 1] == str(tmp_path)
    assert "--from-snapshot" not in cmd
    assert "--from-snapshot" in si.builder_command_estate(FIXTURE_WS, tmp_path, ROOT,
                                                          from_snapshot=True)


# ── preflight rows ────────────────────────────────────────────────────────────


def test_the_attach_census_names_every_component_and_the_deferral():
    ws = si.read_workspace(FIXTURE_WS)
    rows = si.estate_attach_rows(si.discover_components(ws), ws["root"])
    text = "\n".join(rows)
    for alias in ("orders-api", "orders-worker", "deploy-charts"):
        assert alias in text
    assert "declared" in text, "fixture components carry no .git — the census says how " \
                               "each one is attached rather than pretending"
    assert "no remote" in text.lower(), \
        "clone-when-missing is deferred because the schema has nothing to clone from — " \
        "the census states that rather than silently not cloning"


def test_the_neo4j_row_reports_and_never_starts(tmp_path):
    row = si.estate_neo4j_row(ROOT, {"NEO4J_URI": "bolt://somewhere:7687"})
    assert "NEO4J_URI" in row and "somewhere" not in row, \
        "report the key's presence, never its value"
    bare = si.estate_neo4j_row(tmp_path, {})
    assert "optional" in bare.lower()


def test_binary_verification_reprints_the_verify_output(monkeypatch):
    monkeypatch.setattr(si, "run_capture",
                        lambda cmd, cwd: (1, "ok rg 1.0.0\n!! fd: pinned but MISSING\n"))
    rows = si.estate_verify_rows(ROOT)
    text = "\n".join(rows)
    assert "ok rg" in text and "MISSING" in text
    assert any("issue" in r.lower() for r in rows), \
        "a nonzero verify exit is summarized, not swallowed"


def test_binary_verification_degrades_when_uv_is_absent(monkeypatch):
    monkeypatch.setattr(si, "find_uv", lambda: None)
    rows = si.estate_verify_rows(ROOT)
    assert any("uv" in r for r in rows)


# ── the install, wired together ───────────────────────────────────────────────


def test_estate_dry_run_reports_everything_and_spawns_nothing(tmp_path, spy,
                                                              monkeypatch, capsys):
    def boom(cmd, cwd):
        raise AssertionError("dry-run must not spawn")
    monkeypatch.setattr(si, "run_capture", boom)
    target = tmp_path / "target"
    target.mkdir()
    code = install(target, dry_run=True, workspace=si.read_workspace(FIXTURE_WS),
                   estate=True, workspace_path=FIXTURE_WS)
    out = capsys.readouterr().out
    assert code == 0 and not spy.calls
    assert "would build the workspace" in out
    assert "would verify" in out


def test_estate_install_runs_preflight_then_chains(tmp_path, spy, quiet_verify, capsys):
    target = tmp_path / "target"
    target.mkdir()
    code = install(target, workspace=si.read_workspace(FIXTURE_WS), estate=True,
                   workspace_path=FIXTURE_WS)
    out = capsys.readouterr().out
    assert code == 0
    assert len(spy.calls) == 1
    cmd, cwd = spy.calls[0]
    assert "--workspace" in cmd and str(FIXTURE_WS) in cmd
    assert cwd == ROOT, "the build runs from the harness clone's locked environment"
    assert out.index("ok rg") < out.index("graph:"), "preflight prints before the build"


def test_estate_install_chains_the_real_build(tmp_path):
    """The estate chain against a target that IS a git repository — the ordinary case for a
    tool whose whole purpose is installing a lifecycle into somebody's repository (T420).

    SPAWNED, and over a real git repo, because a bare tmp_path could not exercise the defect:
    the builder refuses to write a workspace-spanning graph into a repository it is not about,
    and the estate chain handed it `--out <target>/.sdlc/graph` with nothing declaring the
    target as that graph's committing home — so the build exited 2 and the whole install
    exited 3, on every real repository and none of the bare directories the old test used.

    The graph must land in the TARGET's own .sdlc/graph (item 15's home, which both MCP
    registration and the instruction files point at); the fix is on the caller's side (it
    declares the home) and the builder's own refusal is left untouched."""
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)

    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(target),
         "--workspace", str(FIXTURE_WS), "--estate"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (
        "the estate build must succeed against a git-repository target, not refuse its own "
        "output path:\n" + proc.stdout + proc.stderr)
    graph = target / ".sdlc" / "graph" / "graph.jsonl"
    assert graph.is_file(), (
        "the estate graph must land in the TARGET's own .sdlc/graph:\n" + proc.stdout)
    rows = [ln for ln in graph.read_text("utf-8").splitlines()
            if ln.strip() and '"kind": "header"' not in ln]
    assert rows, "the built graph carries no records:\n" + proc.stdout
    # The records are the estate's OWN facts: the workspace's components appear as nodes.
    body = "\n".join(rows)
    assert "orders-api" in body and "orders-worker" in body, (
        "the built records must be the workspace's estate facts:\n" + body[:2000])


def test_estate_refuses_a_subdirectory_of_a_repository_as_the_home(tmp_path):
    """The home declaration must be a repository ROOT, or it defeats the very refusal it
    lifts (T420). Point the estate install at a SUBDIRECTORY of a repository and, with no
    constraint, that subdirectory is declared as owner — so the workspace-spanning graph,
    carrying every component's names, lands under `<subdir>/.sdlc/graph`, i.e. INSIDE the
    enclosing repository, a tree the graph is not about. Main refuses that exact command;
    the declaration must not turn a previously-refused, genuinely wrong case into exit 0.

    No hand-typed flag is needed: `--estate --target <subdir>` passes the subdir as `--home`
    on its own. The build must refuse, and nothing may be written into the enclosing repo."""
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q", str(outer)], check=True, capture_output=True)
    subdir = outer / "component"
    subdir.mkdir()

    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(subdir),
         "--workspace", str(FIXTURE_WS), "--estate"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode != 0, (
        "declaring a subdirectory as the graph's home lands a workspace-spanning graph "
        "inside the enclosing repository — it must be refused, not exit 0:\n" + proc.stdout)
    graph = subdir / ".sdlc" / "graph" / "graph.jsonl"
    assert not graph.is_file(), (
        "a spanning graph was committed into the enclosing repository's tree:\n"
        + (graph.read_text("utf-8")[:1000] if graph.is_file() else ""))


def test_estate_from_snapshot_refuses_empty_and_never_reads_source(tmp_path,
                                                                   quiet_verify, capsys):
    """A from-snapshot load where NOT ONE component has a published shard is a
    refusal, not a clean build (T235): build.py exits 2 for the empty snapshot and
    the estate chain surfaces that as a broken chained build, exit 3 (item 80).
    A PARTIAL estate — some shards present — stays legitimate degradation at exit 0;
    this fixture publishes zero shards, which is precisely the empty case T235
    stopped reporting as ok. The discriminating assertion still holds either way:
    the graph must come out EMPTY, because a from-snapshot load that quietly fell
    back to building from source would be a different command than requested."""
    target = tmp_path / "target"
    target.mkdir()
    # A real git repository: the estate graph's home must be a repository root, so it is the
    # empty snapshot (not the home constraint) that makes this exit 3.
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
    code = install(target, workspace=si.read_workspace(FIXTURE_WS), estate=True,
                   workspace_path=FIXTURE_WS, from_snapshot=True)
    assert code == 3, capsys.readouterr().out
    rows = [line for line
            in (target / ".sdlc" / "graph" / "graph.jsonl").read_text("utf-8").splitlines()
            if line.strip() and '"kind": "header"' not in line]
    assert rows == [], "the fixture has published no shards; any row here means the " \
                       "load read source instead of shards"


def test_estate_re_run_hint_names_a_command_that_actually_works(tmp_path, monkeypatch,
                                                                capsys):
    """T420 clause 3: when an estate build is attempted and fails, the printed re-run hint
    must name a command that WOULD work — not the command that just failed.

    Before the fix the hint omitted the home declaration, so pasting it hit the same refusal
    and failed identically forever: a hint that repeats the failing command is worse than no
    hint, because it costs the reader a second failure to learn the first was not transient.
    The build is forced to fail so the hint is printed, and the hint must carry
    `--home "<target>"`; a companion assertion spawns the exact command the hint mirrors
    against a git target and proves it exits 0 with a graph written."""
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)

    monkeypatch.setattr(si, "run_builder", lambda cmd, cwd: 2)   # force a failed build
    code = si.chain_graph_build(target, enabled=True,
                                workspace=si.read_workspace(FIXTURE_WS),
                                estate=True, workspace_path=FIXTURE_WS)
    out = capsys.readouterr().out
    assert code == 3

    # Pull the exact hint line a person would paste (the one naming the builder) and RUN IT
    # through the shell, verbatim — so a hint that PRINTS an un-runnable command fails this
    # test rather than passing it. (Rebuilding the command from a helper, as this test once
    # did, tests the helper and never the printed text.)
    hint = next(ln.strip() for ln in out.splitlines()
                if "build.py" in ln and "--workspace" in ln)
    ran = subprocess.run(hint, shell=True, cwd=str(ROOT),
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert ran.returncode == 0, (
        "the printed re-run hint must be a command that works when pasted:\n"
        + hint + "\n" + ran.stdout + ran.stderr)
    assert (target / ".sdlc" / "graph" / "graph.jsonl").is_file(), (
        "running the printed hint produced no graph:\n" + hint + "\n" + ran.stdout)
