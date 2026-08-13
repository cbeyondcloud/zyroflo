"""INIT chains the zero-config graph build, and says so when it does not (item 80).

The claim under test is *one command at a repository root produces every artifact*. Every
way that claim can fail is a way of producing LESS than it says while still exiting 0:

| the run                      | what must happen                                | exit |
|------------------------------|-------------------------------------------------|------|
| harness clone, repo root     | the builder runs, against THIS repo, says where |  0   |
| no graph engine beside it    | Tier 0 completes and prints why                 |  0   |
| uv absent                    | same shape, and the reason names uv             |  0   |
| `--workspace`                | not chained; prints the multi-repo command      |  0   |
| not a repository root        | not chained; a graph needs a repository         |  0   |
| `--no-graph`                 | not chained; says the flag did it               |  0   |
| `--dry-run`                  | nothing runs, but it still REPORTS the build    |  0   |
| no extractor claims the repo | the graph is empty, and that is coverage        |  0   |
| the build broke              | names the exit code, scaffold still stands      |  1   |
| the build broke, stale report| the id did not change, so it is not evidence    |  1   |

The exit column is the item. An ABSENT environment is a supported configuration, and an
EMPTY graph is a fact about extractor coverage — neither is a failure of this install. A
build that was attempted and broke is, and exiting 0 there would claim every artifact was
created. The last row is the same distinction one level down: a report is only evidence
about the run that just happened if its run id CHANGED.

Most tests replace `run_builder`, the one seam, and assert on the command that would have
run — a real build would test the graph layer, which has its own suite. Four of them run
the real thing, because the failures that survive unit tests of a subprocess call are the
wiring ones, and because the two fields read back out of `build_report.json` are a contract
nothing else would notice breaking.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repository the extractors have something to say about."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "pom.xml").write_text("<project><artifactId>demo</artifactId></project>\n",
                                      encoding="utf-8")
    res = tmp_path / "src" / "main" / "resources"
    res.mkdir(parents=True)
    (res / "application.properties").write_text("server.port=8080\n", encoding="utf-8")
    return tmp_path


class Spy:
    """Stands in for the builder subprocess and records how it was called."""

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


def install(root: Path, **kw) -> int:
    kw.setdefault("tools", ["claude"])
    kw.setdefault("force", False)
    kw.setdefault("dry_run", False)
    return si.install(root, **kw)


# ── guard on the guard ────────────────────────────────────────────────────────
def test_this_clone_can_actually_build():
    """Every 'it refuses because it cannot build' test below passes vacuously if the
    capability check is broken and always refuses. This is the assertion that makes the
    others mean something: in THIS clone, which has the engine and the environment, the
    check must return None."""
    assert si.graph_capability() is None, (
        "the harness clone reports itself incapable of building a graph, so every "
        "capability test below is asserting against a check that never says yes")


# ── the chain fires, against the right repository, from the right place ───────
def test_an_install_at_a_repository_root_builds_that_repository(repo, spy, capsys):
    assert install(repo) == 0
    assert len(spy.calls) == 1, "the graph build was not chained"
    cmd, _ = spy.calls[0]
    assert "--target" in cmd and cmd[cmd.index("--target") + 1] == str(repo)
    assert "graph: built" in capsys.readouterr().out


def test_the_build_runs_from_the_harness_clone_not_the_target(repo, spy):
    """Tier 0's promise is kept BY CONSTRUCTION: the locked environment lives in the
    harness clone, so that is where the build runs. Run it from the target and `uv` would
    resolve the target's own project — or none — and the harness would be asking a
    stranger's repository to supply its dependencies."""
    install(repo)
    cmd, cwd = spy.calls[0]
    assert cwd == si.harness_root()
    assert Path(cmd[3]).parent.parent.parent == si.harness_root()


def test_the_chained_command_names_no_output_directory(repo, spy):
    """`--out` is deliberately absent: building an inferred repository, the builder already
    defaults to that repository's own .sdlc/graph. A second copy of that decision here is
    one that drifts."""
    install(repo)
    cmd, _ = spy.calls[0]
    assert "--out" not in cmd
    assert cmd[:3] == ["uv", "run", "python"]


def test_the_graph_lands_under_the_repositorys_own_sdlc(repo, spy, capsys):
    install(repo)
    assert str(repo / ".sdlc" / "graph") in capsys.readouterr().out


# ── every refusal is reported, and none of them is silent ─────────────────────
def test_a_workspace_install_says_the_graph_was_not_built(tmp_path, spy, capsys):
    repos = tmp_path / "repos"
    (repos / "orders" / ".git").mkdir(parents=True)
    (repos / "orders" / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    ws = tmp_path / "ws.toml"
    ws.write_text('root = "repos"\n', encoding="utf-8")
    install(target, workspace=si.read_workspace(ws))
    out = capsys.readouterr().out
    assert not spy.calls
    assert "graph: not built" in out
    assert "--workspace" in out, "it must name the command that DOES build one"


def test_a_target_that_is_not_a_repository_says_so(tmp_path, spy, capsys):
    install(tmp_path)
    assert not spy.calls
    assert "graph: not built" in capsys.readouterr().out


def test_no_graph_skips_the_build_and_says_which_flag_did_it(repo, spy, capsys):
    assert install(repo, graph=False) == 0
    assert not spy.calls
    assert "--no-graph" in capsys.readouterr().out


def test_a_missing_engine_completes_tier_zero_and_exits_zero(repo, spy, capsys, tmp_path):
    """The Tier 0 case, and the one the item calls the whole design. A developer who copied
    one file has no engine; the install is still complete and must not read as broken."""
    elsewhere = tmp_path / "not-a-harness"
    elsewhere.mkdir()
    assert si.chain_graph_build(repo, enabled=True, workspace=None, harness=elsewhere) == 0
    out = capsys.readouterr().out
    assert not spy.calls
    assert "graph: not built" in out
    assert "standalone installer" in out


def test_a_missing_uv_is_named_as_the_missing_thing(repo, spy, monkeypatch, capsys):
    monkeypatch.setattr(si.shutil, "which", lambda name: None)
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 0
    out = capsys.readouterr().out
    assert not spy.calls
    assert "uv is not on PATH" in out
    assert "https://docs.astral.sh/uv/" in out


def test_every_refusal_prints_the_same_greppable_prefix(repo, tmp_path, monkeypatch, capsys):
    """One prefix for every outcome, so 'did I get a graph?' is answerable by looking rather
    than by knowing which of five sentences to expect."""
    monkeypatch.setattr(si, "run_builder", Spy())
    cases = [
        lambda: si.chain_graph_build(repo, enabled=False, workspace=None),
        lambda: si.chain_graph_build(repo, enabled=True, workspace={"root": tmp_path}),
        lambda: si.chain_graph_build(tmp_path / "nope", enabled=True, workspace=None),
        lambda: si.chain_graph_build(repo, enabled=True, workspace=None,
                                     harness=tmp_path),
    ]
    for case in cases:
        capsys.readouterr()
        case()
        assert "graph: not built - " in capsys.readouterr().out


# ── a build that was attempted and failed is not a supported configuration ────
def test_a_failed_build_makes_the_install_exit_non_zero(repo, monkeypatch, capsys):
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    assert install(repo) == 1
    out = capsys.readouterr().out
    assert "exited 2" in out
    assert "graph: not built" in out


def test_a_failed_build_still_leaves_the_scaffold_in_place(repo, monkeypatch):
    """The install did happen. Reporting the build honestly must not retract it."""
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    install(repo)
    assert (repo / ".sdlc" / "profile.yml").is_file()


def test_an_absent_environment_exits_zero_but_a_broken_build_does_not(repo, tmp_path,
                                                                     monkeypatch):
    """The distinction, as one assertion. Absent is supported; broken is not."""
    absent = si.chain_graph_build(repo, enabled=True, workspace=None, harness=tmp_path)
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    broken = si.chain_graph_build(repo, enabled=True, workspace=None)
    assert (absent, broken) == (0, 1)


# ── an empty graph is a fact about coverage, not a failed install ─────────────
def _report(repo: Path, status: str, run_id: str = "fresh") -> None:
    out = repo / si.GRAPH_OUT
    out.mkdir(parents=True, exist_ok=True)
    (out / "build_report.json").write_text(json.dumps({"status": status, "run_id": run_id}),
                                           encoding="utf-8")


class Writer(Spy):
    """A builder that fails, having written a report of its own. The ordinary case."""

    def __init__(self, repo: Path, status: str, code: int = 2):
        super().__init__(code)
        self.repo, self.status = repo, status

    def __call__(self, cmd: list[str], cwd: Path) -> int:
        _report(self.repo, self.status, run_id="this-run")
        return super().__call__(cmd, cwd)


def test_a_repository_no_extractor_understands_is_not_a_failed_install(repo, monkeypatch,
                                                                      capsys):
    """The case that found this branch: a Go repository. Every extractor ran, none matched,
    the builder exits 2 calling it a misconfiguration - which is right for a deliberate
    build and wrong here, because the harness simply has no Go extractor yet. Most stacks
    are in that position today, so exiting 1 would make the one-command install report
    failure on the majority of repositories it exists to serve."""
    monkeypatch.setattr(si, "run_builder", Writer(repo, "no-activation"))
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 0
    out = capsys.readouterr().out
    assert "EMPTY" in out
    assert "not a failure" in out


@pytest.mark.parametrize("status", ["no-extractors", "all-failed", "", "surprise"])
def test_every_other_non_zero_status_stays_a_failure(repo, monkeypatch, status):
    """`no-extractors` is a harness that shipped without extractors and `all-failed` is one
    whose extractors all crashed. Neither is a fact about the repository, and an unknown
    status is not a licence to assume the benign reading."""
    monkeypatch.setattr(si, "run_builder", Writer(repo, status))
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 1


def test_an_unreadable_report_is_a_failure_not_a_pass(repo, monkeypatch):
    """No report at all. A build whose own verdict cannot be read is not one to call fine."""
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 1
    assert si.build_verdict(repo / si.GRAPH_OUT) == ("", "")


def test_a_stale_benign_report_does_not_excuse_a_crash(repo, monkeypatch, capsys):
    """The subtle one. A build that dies before writing leaves the PREVIOUS run's report on
    disk, and this repository's previous run was benignly empty. Reading status alone, a
    crash would report itself as coverage - same file, same field, opposite conclusion. The
    run id is what tells them apart: it did not change, so no build wrote this."""
    _report(repo, "no-activation", run_id="a-previous-run")
    monkeypatch.setattr(si, "run_builder", Spy(code=2))  # writes nothing
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 1
    assert "EMPTY" not in capsys.readouterr().out


def test_the_builder_still_writes_the_verdict_this_branch_reads(tmp_path):
    """A CONTRACT test, run against the real builder. `build_verdict` reads a JSON file
    rather than importing anything, because tools/ may depend on this installer and never
    the reverse - so nothing but this test would notice the day either field is renamed.
    Rename `status` and every repository with no extractor reports a failed install;
    rename `run_id` and every one of them reports a failed install too, because an id that
    never changes reads as a report nobody wrote."""
    repo = tmp_path / "unclaimed"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "main.go").write_text("package main\n", encoding="utf-8")

    code = subprocess.run(
        si.builder_command(repo, si.harness_root()), cwd=str(si.harness_root()),
        capture_output=True, text=True, encoding="utf-8", errors="replace").returncode
    assert code != 0, "sanity: a repository nothing claims must not build a graph quietly"
    run_id, status = si.build_verdict(repo / si.GRAPH_OUT)
    assert status == si.BENIGN_BUILD_STATUS
    assert run_id, "a report with no run id cannot be told apart from a stale one"


def test_that_repository_installs_and_exits_zero(tmp_path):
    """The same repository through the whole command, which is how a Go developer meets it."""
    repo = tmp_path / "unclaimed"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "main.go").write_text("package main\n", encoding="utf-8")

    proc = subprocess.run([sys.executable, str(INSTALLER), "--target", str(repo)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "EMPTY" in proc.stdout


# ── the dry run describes the same command the real run performs ──────────────
def test_a_dry_run_runs_nothing_but_still_reports_the_build(repo, spy, capsys):
    assert install(repo, dry_run=True) == 0
    out = capsys.readouterr().out
    assert not spy.calls
    assert not (repo / ".sdlc").exists()
    assert "[dry-run] graph: would build into" in out


def test_a_dry_run_that_would_not_build_says_why(tmp_path, spy, capsys):
    install(tmp_path, dry_run=True)
    assert "[dry-run] graph: not built" in capsys.readouterr().out


# ── .sdlc/.gitignore: the commit-vs-ignore split, owned by the generator ──────
def test_the_install_writes_the_sdlc_gitignore(tmp_path):
    """Deliberately against a NON-repository target, and deliberately without the spy: no
    chain fires either way, so this test needs neither the new seam nor the new keyword
    and fails on its own assertion against the source that predates them. Every other test
    in this section is red on pre-fix source because the API it patches does not exist yet,
    which proves the fix landed rather than proving the assertion is load-bearing."""
    install(tmp_path)
    assert (tmp_path / ".sdlc" / ".gitignore").is_file()


def test_it_ignores_the_un_merged_copy_a_re_run_leaves(repo, spy):
    """`profile.yml.new` is this installer's own output and belongs to one machine mid-
    merge. Committed, it travels to the next clone as a second profile nobody chose."""
    install(repo)
    body = (repo / ".sdlc" / ".gitignore").read_text(encoding="utf-8")
    assert "*.new" in body.split("\n")


def test_it_ignores_a_credential_that_should_never_be_there_anyway(repo, spy):
    """Credentials are out of scope by construction - they live in the harness clone's
    .env. This line costs nothing and the cost of being wrong once is a public secret."""
    install(repo)
    assert ".env" in (repo / ".sdlc" / ".gitignore").read_text(encoding="utf-8").split("\n")


def test_it_says_in_the_file_what_is_meant_to_be_committed(repo, spy):
    """An ignore-only file cannot express 'keep these' as patterns without `!` re-includes,
    which silently do nothing when a parent directory is excluded. So it is said in prose,
    where it is true regardless of the host repository's own ignores."""
    install(repo)
    body = (repo / ".sdlc" / ".gitignore").read_text(encoding="utf-8")
    assert "COMMITTED" in body
    for artifact in ("profile.yml", "plans/", "graph/", "runs/"):
        assert artifact in body, f"{artifact} is not named as travelling with the repo"


def test_it_re_includes_nothing(repo, spy):
    """No `!` lines, deliberately. A re-include that cannot fire reads exactly like one
    that did, and which it is depends on the repository this lands in."""
    install(repo)
    body = (repo / ".sdlc" / ".gitignore").read_text(encoding="utf-8")
    assert not [ln for ln in body.split("\n") if ln.startswith("!")]


def test_an_edited_gitignore_is_preserved_not_clobbered(repo, spy):
    """The drop-in's headline promise, applied to the file a developer is most likely to
    add a line to. Clobbering it would be silent: they find out when something they
    excluded gets committed."""
    install(repo)
    target = repo / ".sdlc" / ".gitignore"
    target.write_text("*.new\nmy-own-rule/\n", encoding="utf-8")
    install(repo)
    assert "my-own-rule/" in target.read_text(encoding="utf-8")
    assert (repo / ".sdlc" / ".gitignore.new").is_file()


def test_force_overwrites_it_like_every_other_generated_file(repo, spy):
    install(repo)
    (repo / ".sdlc" / ".gitignore").write_text("mine\n", encoding="utf-8")
    install(repo, force=True)
    assert "mine" not in (repo / ".sdlc" / ".gitignore").read_text(encoding="utf-8")


# ── the wiring, run for real ──────────────────────────────────────────────────
def test_the_flag_exists_on_the_command_line():
    proc = subprocess.run([sys.executable, str(INSTALLER), "--help"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "--no-graph" in proc.stdout


def test_one_command_at_a_repository_root_produces_every_artifact(tmp_path):
    """The item's headline claim, end to end, as a developer runs it. The unit tests above
    all replace the subprocess; this is the one that would catch a wrong flag name, an
    exception escaping as a traceback, or an output directory the builder refuses."""
    assert shutil.which("uv"), "uv is on PATH in every environment this suite runs in"
    repo = tmp_path / "demo"
    (repo / "src" / "main" / "resources").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "pom.xml").write_text("<project><artifactId>demo</artifactId></project>\n",
                                  encoding="utf-8")
    (repo / "src" / "main" / "resources" / "application.properties").write_text(
        "server.port=8080\n", encoding="utf-8")

    proc = subprocess.run([sys.executable, str(INSTALLER), "--target", str(repo)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for artifact in (".sdlc/profile.yml", ".sdlc/.gitignore", ".sdlc/graph/graph.jsonl",
                     ".sdlc/graph/build_report.json"):
        assert (repo / artifact).is_file(), f"{artifact} is missing:\n{proc.stdout}"
