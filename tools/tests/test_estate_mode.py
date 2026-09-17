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


def test_estate_install_chains_the_real_build(tmp_path, quiet_verify, capsys):
    target = tmp_path / "target"
    target.mkdir()
    code = install(target, workspace=si.read_workspace(FIXTURE_WS), estate=True,
                   workspace_path=FIXTURE_WS)
    assert code == 0, capsys.readouterr().out
    assert (target / ".sdlc" / "graph" / "graph.jsonl").exists(), \
        "the estate graph lands in the TARGET's own .sdlc, the home item 80 lacked"


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
    code = install(target, workspace=si.read_workspace(FIXTURE_WS), estate=True,
                   workspace_path=FIXTURE_WS, from_snapshot=True)
    assert code == 3, capsys.readouterr().out
    rows = [line for line
            in (target / ".sdlc" / "graph" / "graph.jsonl").read_text("utf-8").splitlines()
            if line.strip() and '"kind": "header"' not in line]
    assert rows == [], "the fixture has published no shards; any row here means the " \
                       "load read source instead of shards"
