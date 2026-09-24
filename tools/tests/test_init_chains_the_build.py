"""INIT chains the zero-config graph build, and says so when it does not (item 80).

The claim under test is *one command at a repository root produces every artifact*. Every
way that claim can fail is a way of producing LESS than it says while still exiting 0:

| the run                      | what must happen                                | exit |
|------------------------------|-------------------------------------------------|------|
| harness clone, repo root     | the builder runs, against THIS repo, says where |  0   |
| no graph engine beside it    | Tier 0 completes and prints why                 |  0   |
| uv absent                    | same shape, and the reason names uv             |  0   |
| uv found but unlaunchable    | a message naming uv, never a traceback          |  3   |
| `--workspace`                | not chained; prints the multi-repo command      |  0   |
| a repo that contains repos   | not chained; the builder would only refuse      |  0   |
| not a repository root        | not chained; a graph needs a repository         |  0   |
| `--no-graph`                 | not chained; says the flag did it               |  0   |
| `--dry-run`                  | nothing runs, but it still REPORTS the build    |  0   |
| no extractor claims the repo | the graph is empty, and that is coverage        |  0   |
| the build broke              | names the exit code, scaffold still stands      |  3   |

The exit column is the item. An ABSENT environment is a supported configuration, and an
EMPTY graph is a fact about extractor coverage — neither is a failure of this install. A
build that was attempted and broke is, and exiting 0 there would claim every artifact was
created — but it exits 3, not 1, because every fatal-input path already exits 1 meaning
"nothing was installed" and this outcome is its opposite: the scaffold stands. The
empty-graph judgement itself lives in the BUILDER, behind `--empty-ok`: it rules from the
report in memory, and an exit code cannot be stale, where a report re-read from disk after
a non-zero exit can be (a build that dies before writing leaves the previous run's).

Most tests replace `run_builder`, the one seam, and assert on the command that would have
run — a real build would test the graph layer, which has its own suite. Four of them run
the real thing, because the failures that survive unit tests of a subprocess call are the
wiring ones, and because `--empty-ok` and the `status` field read back out of
`build_report.json` are a contract nothing else would notice breaking.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests import _project  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("sdlc_init", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


si = _project.filled_gate(load_installer())

# One loud guard instead of nineteen baffling failures. `find_uv` is a second
# environmental seam — the spy replaces only `run_builder` — so without uv on PATH most
# of this file would refuse before the spy and two tests would die inside subprocess with
# messages that never name uv. An IDE test runner launching the venv's python directly is
# exactly that machine. An assert rather than a skip, because this suite forbids quiet
# skips by design (SDLC_FORBID_SKIPS).
assert si.find_uv(), (
    "uv is not on PATH; this suite drives the real chained build and needs it. "
    "`uv run pytest` guarantees it; an IDE runner must have uv reachable from its "
    "environment.")


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
    assert cmd[1:3] == ["run", "python"]


def test_the_spawned_uv_is_an_absolute_path_never_a_bare_name(repo, spy):
    """A bare `uv` leaves the OS a search to perform, and on Windows that search visits
    the CURRENT DIRECTORY — the target repository — before PATH. A repo shipping its own
    `uv.exe` would be executed the moment a developer ran INIT at its root, beating even a
    properly installed uv. The command must arrive pre-resolved, from PATH alone."""
    install(repo)
    cmd, _ = spy.calls[0]
    assert Path(cmd[0]).is_absolute(), "the program name must leave the OS no search"
    assert Path(cmd[0]).name.lower().startswith("uv")


def test_uv_is_resolved_from_path_alone_never_the_current_directory(tmp_path, monkeypatch):
    """The resolver itself, against a planted binary. The current directory holds a fake
    uv and PATH holds the real one: PATH must win, and with PATH empty the planted copy
    must be invisible rather than found."""
    exe = "uv.exe" if os.name == "nt" else "uv"
    trap = tmp_path / "repo"
    trap.mkdir()
    (trap / exe).write_text("", encoding="utf-8")
    real = tmp_path / "bin"
    real.mkdir()
    (real / exe).write_text("", encoding="utf-8")
    (real / exe).chmod(0o755)
    monkeypatch.chdir(trap)
    monkeypatch.setenv("PATH", str(real))
    assert si.find_uv() == str(real / exe)
    monkeypatch.setenv("PATH", "")
    assert si.find_uv() is None, "an empty PATH means no uv, not the one in the cwd"


def test_the_chained_command_declares_the_empty_graph_acceptable(repo, spy):
    """`--empty-ok` is the caller's one bit of intent, handed to the builder so the
    empty-vs-broken verdict is decided where it cannot be fooled: from the report in
    memory, as an exit code. The installer re-deriving it from the file on disk was the
    staleness bug this flag replaced."""
    install(repo)
    cmd, _ = spy.calls[0]
    assert "--empty-ok" in cmd


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
    monkeypatch.setattr(si, "find_uv", lambda: None)
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 0
    out = capsys.readouterr().out
    assert not spy.calls
    assert "uv is not on PATH" in out
    assert "https://docs.astral.sh/uv/" in out


def test_a_repository_of_repositories_is_a_refusal_not_a_failure(repo, spy, capsys):
    """A repo vendoring an independent clone at depth 1 is the shape the builder's own
    `workspace_for_repo` refuses by design — it is a multi-repo workspace wearing one
    root. Chaining a build whose first act is to refuse would land a SUPPORTED shape in
    the broken-build exit, with a re-run hint that fails identically forever. The mirror
    check refuses first, exits 0, and points at the command that does work."""
    (repo / "lib" / ".git").mkdir(parents=True)
    assert install(repo) == 0
    out = capsys.readouterr().out
    assert not spy.calls, "the chain must not launch a build the builder only refuses"
    assert "graph: not built - " in out
    assert "--workspace" in out, "it must name the route that DOES build one"


def test_a_dry_run_predicts_the_nested_clone_refusal(repo, spy, capsys):
    """The dry run reports the same decision the real run makes — which was false for
    this shape: it said 'would build' about a build whose real run refused."""
    (repo / "lib" / ".git").mkdir(parents=True)
    install(repo, dry_run=True)
    out = capsys.readouterr().out
    assert "[dry-run] graph: not built" in out
    assert "would build into" not in out


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
def test_a_failed_build_makes_the_install_exit_three(repo, monkeypatch, capsys):
    """3, not 1: the fatal-input paths own 1 and it means "nothing was installed". This
    outcome is the opposite — the scaffold stands — and a CI step that cannot tell the two
    apart from the code would wipe a usable install to remedy a graph-only failure."""
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    assert install(repo) == 3
    out = capsys.readouterr().out
    assert "exited 2" in out
    assert "graph: build FAILED" in out


def test_a_failed_build_does_not_claim_nothing_was_built(repo, monkeypatch, capsys):
    """The builder exits non-zero even at status `ok` (contract violations, graph fully
    written), so a summary that says "not built" over a written graph is a lie the
    transcript itself contradicts — the builder's own `wrote ...` line sits above it. On
    failure the summary claims nothing about the artifacts; the report on disk may be
    stale, and the builder's own output is the only honest account."""
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    install(repo)
    assert "graph: not built" not in capsys.readouterr().out


def test_a_failed_build_still_leaves_the_scaffold_in_place(repo, monkeypatch):
    """The install did happen. Reporting the build honestly must not retract it."""
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    install(repo)
    assert (repo / ".sdlc" / "profile.yml").is_file()


def test_a_failed_build_does_not_sign_off_with_tips(repo, monkeypatch, capsys):
    """The last lines of a failed install must be the failure, not onboarding. Tips
    printed below "build FAILED" read as the end of the command, and the end of the
    command reads as success."""
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    install(repo)
    out = capsys.readouterr().out
    assert "Next: open this repo" not in out
    assert "Tip: add --claude-extras" not in out


def test_a_successful_install_still_signs_off(repo, spy, capsys):
    """The sign-off now has two steps, and the first one is why.

    This asserted the literal `Next: open this repo`, and that instruction was
    incomplete: a first field install showed the very next command refusing,
    because the files the installer had just written were untracked and P0
    requires a clean tree. Nothing in the harness or the README said to commit
    them. The sign-off names the commit first now, so this asserts BOTH halves
    rather than one string's position.
    """
    assert install(repo) == 0
    out = capsys.readouterr().out
    assert "git add -A" in out and "clean tree" in out
    assert "open this repo in an AI coding tool" in out


def test_an_unlaunchable_uv_is_a_message_not_a_traceback(repo, monkeypatch, capsys):
    """`find_uv` can say yes and the spawn still fail: uv removed in between, a shim the
    OS cannot execute, permissions. Everything else in the installer fails as a message;
    the spawn boundary was the one place a raw OSError could escape."""
    def boom(cmd, cwd):
        raise OSError("simulated: uv vanished between the check and the spawn")
    monkeypatch.setattr(si, "run_builder", boom)
    assert install(repo) == 3
    out = capsys.readouterr().out
    assert "graph: not built - uv could not be launched" in out


def test_run_builder_refuses_a_bare_program_name(tmp_path):
    """The real spawn, not the seam: handed a bare name, it must refuse rather than let
    the OS search for it — on Windows that search starts in the current directory, which
    is the untrusted target repo. The guard is what turns 'builder_command happens to
    pass an absolute path' into a guarantee."""
    with pytest.raises(OSError):
        si.run_builder(["uv", "--version"], tmp_path)


def test_the_re_run_hint_pins_the_harness_environment_and_survives_spaces(repo,
                                                                          monkeypatch,
                                                                          capsys):
    """The hint exists to be pasted after a failure, from whatever directory the
    developer is standing in. Without `--project`, uv resolves THEIR project — a degraded
    build at exit 0 on the wrong interpreter, or a stranger's repo gaining a .venv. And
    both paths on this machine can contain spaces, so they are quoted."""
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    install(repo)
    out = capsys.readouterr().out
    assert f'--project "{si.harness_root()}"' in out
    assert f'--target "{repo}"' in out


def test_an_absent_environment_exits_zero_but_a_broken_build_does_not(repo, tmp_path,
                                                                     monkeypatch):
    """The distinction, as one assertion. Absent is supported; broken is not."""
    absent = si.chain_graph_build(repo, enabled=True, workspace=None, harness=tmp_path)
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    broken = si.chain_graph_build(repo, enabled=True, workspace=None)
    assert (absent, broken) == (0, 3)


# ── an empty graph is a fact about coverage, not a failed install ─────────────
def _report(repo: Path, status, run_id: str = "fresh", raw: str | None = None) -> None:
    out = repo / si.GRAPH_OUT
    out.mkdir(parents=True, exist_ok=True)
    body = raw if raw is not None else json.dumps({"status": status, "run_id": run_id})
    (out / "build_report.json").write_text(body, encoding="utf-8")


class Writer(Spy):
    """A builder that writes a report of its own before returning. The ordinary case."""

    def __init__(self, repo: Path, status: str, code: int = 2):
        super().__init__(code)
        self.repo, self.status = repo, status

    def __call__(self, cmd: list[str], cwd: Path) -> int:
        _report(self.repo, self.status, run_id="this-run")
        return super().__call__(cmd, cwd)


def test_a_repository_no_extractor_understands_is_not_a_failed_install(repo, monkeypatch,
                                                                      capsys):
    """The case that found this outcome: a Go repository. Every extractor ran, none
    matched — coverage, not failure. The chained call passes `--empty-ok`, so the BUILDER
    exits 0 for it; the installer's only remaining job is the EMPTY wording."""
    monkeypatch.setattr(si, "run_builder", Writer(repo, "no-activation", code=0))
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 0
    out = capsys.readouterr().out
    assert "EMPTY" in out
    assert "not a failure" in out


@pytest.mark.parametrize("status", ["no-activation", "no-extractors", "all-failed", "",
                                    "surprise"])
def test_any_non_zero_exit_is_a_failure_whatever_the_report_says(repo, monkeypatch, status):
    """The verdict is the exit code, full stop. With `--empty-ok` the builder already
    exits 0 for the one benign outcome, so by the time a non-zero code reaches the
    installer there is no benign reading left — including for a `no-activation` report,
    which next to a non-zero exit means the violations gate fired or the report is stale.
    Re-deriving a friendlier verdict from the file was the staleness bug."""
    monkeypatch.setattr(si, "run_builder", Writer(repo, status))
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 3


def test_an_unreadable_report_is_worded_without_it_not_crashed_on(repo, monkeypatch):
    """No report at all: the failure branch must not need one."""
    monkeypatch.setattr(si, "run_builder", Spy(code=2))
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 3
    assert si.build_status(repo / si.GRAPH_OUT) == ""


@pytest.mark.parametrize("raw", ["null", "[]", '"x"', "42", '{"status": null}'])
def test_a_garbage_report_on_disk_cannot_crash_the_install(repo, monkeypatch, raw):
    """Valid JSON that is not an object — an interrupted foreign writer, a hand edit —
    used to escape the reader's except-clause as an AttributeError and kill the whole
    install with a traceback. Unreadable means unreadable: `""`, and the install
    completes on the builder's own verdict."""
    _report(repo, None, raw=raw)
    assert si.build_status(repo / si.GRAPH_OUT) == ""
    monkeypatch.setattr(si, "run_builder", Spy(code=0))
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 0


def test_a_stale_benign_report_does_not_excuse_a_crash(repo, monkeypatch, capsys):
    """A build that dies before writing leaves the PREVIOUS run's report on disk, and this
    repository's previous run was benignly empty. The old status-plus-run-id dance guarded
    this hole; moving the verdict into the builder's exit code closes it structurally —
    the report is never consulted for the verdict at all, so a stale benign file next to a
    non-zero exit changes nothing."""
    _report(repo, "no-activation", run_id="a-previous-run")
    monkeypatch.setattr(si, "run_builder", Spy(code=2))  # writes nothing
    assert si.chain_graph_build(repo, enabled=True, workspace=None) == 3
    assert "EMPTY" not in capsys.readouterr().out


def test_the_builder_owns_the_empty_verdict_and_this_run_reads_its_wording(tmp_path):
    """A CONTRACT test, run against the real builder, in both directions. With
    `--empty-ok` (the chained form) an unclaimed repository is a CLEAN exit 0 whose
    report still says `no-activation` — rename the status or drop the flag handling and
    every such repository reports a failed install. Without the flag (a deliberate
    build) the same repository must keep exiting non-zero: the flag widens nothing for
    anyone who did not ask."""
    repo = tmp_path / "unclaimed"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "main.go").write_text("package main\n", encoding="utf-8")

    cmd = si.builder_command(repo, si.harness_root())
    chained = subprocess.run(cmd, cwd=str(si.harness_root()),
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
    assert chained.returncode == 0, (
        "with --empty-ok an unclaimed repository is coverage, not failure:\n"
        + chained.stdout + chained.stderr)
    assert si.build_status(repo / si.GRAPH_OUT) == si.BENIGN_BUILD_STATUS

    deliberate = subprocess.run([c for c in cmd if c != "--empty-ok"],
                                cwd=str(si.harness_root()), capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
    assert deliberate.returncode != 0, (
        "without the flag, no-activation must stay the misconfiguration it usually is")


def test_that_repository_installs_and_exits_zero(tmp_path):
    """The same repository through the whole command, which is how a Go developer meets it."""
    repo = tmp_path / "unclaimed"
    repo.mkdir()
    _project.seed(repo)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "main.go").write_text("package main\n", encoding="utf-8")

    _project.seed(repo)
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
    for artifact in ("profile.yml", "plans/", "graph/"):
        assert artifact in body, f"{artifact} is not named as travelling with the repo"


def test_it_promises_no_artifact_nothing_shipped_can_write(repo, spy):
    """The header used to name `runs/` and "the ledgers" — artifacts only a pending item
    creates. A developer who goes looking for them finds nothing and concludes the
    install broke. The file may only describe the install that wrote it."""
    install(repo)
    body = (repo / ".sdlc" / ".gitignore").read_text(encoding="utf-8")
    assert "runs/" not in body
    assert "ledger" not in body


def test_a_workspace_targets_gitignore_does_not_promise_a_graph(tmp_path, spy):
    """A workspace target never receives a `graph/` — the same run says so on stdout —
    and a header promising one would leave two artifacts of one command asserting
    opposite facts about the same directory."""
    repos = tmp_path / "repos"
    (repos / "orders" / ".git").mkdir(parents=True)
    (repos / "orders" / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    ws = tmp_path / "ws.toml"
    ws.write_text('root = "repos"\n', encoding="utf-8")
    install(target, workspace=si.read_workspace(ws))
    body = (target / ".sdlc" / ".gitignore").read_text(encoding="utf-8")
    assert "COMMITTED" in body
    assert "graph/" not in body


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


def test_the_preserved_note_names_the_file_that_was_preserved(repo, spy, capsys):
    """Two files are preserve-capable, and the NOTE used to hardcode the other one: a
    developer who edited only the .gitignore was told their profile.yml was kept and
    pointed at a profile.yml.new that does not exist — while the real .gitignore.new sat
    hidden from `git status` by the generated `*.new` rule, leaving the wrong NOTE as
    their only pointer to it."""
    install(repo)
    target = repo / ".sdlc" / ".gitignore"
    target.write_text(target.read_text(encoding="utf-8") + "my-own-rule/\n",
                      encoding="utf-8")
    capsys.readouterr()
    install(repo)
    out = capsys.readouterr().out
    assert "your edited .sdlc/.gitignore was kept" in out
    assert "profile.yml.new" not in out, "the NOTE points at a file nobody wrote"


def test_force_overwrites_it_like_every_other_generated_file(repo, spy):
    install(repo)
    (repo / ".sdlc" / ".gitignore").write_text("mine\n", encoding="utf-8")
    install(repo, force=True)
    assert "mine" not in (repo / ".sdlc" / ".gitignore").read_text(encoding="utf-8")


# ── T382: the preserve NOTE must not blame the adopter for an installer change ──
def test_preserve_note_does_not_blame_adopter_for_installer_change(tmp_path):
    """When the installer itself wrote the profile and a re-run computes different
    values (different --tools list), the NOTE must NOT say 'your edited': the adopter
    never touched the file. Proven through the spawned installer (CLI contract)."""
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "setup.py").write_text("", encoding="utf-8")

    # First install: tools=claude
    proc1 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr
    assert (repo / ".sdlc" / "profile.yml").is_file()

    # Re-install with different tools: profile content changes, but
    # the adopter never edited the file.
    proc2 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude,cursor")
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    assert "your edited" not in proc2.stdout, (
        "the adopter never edited the profile; the installer changed its own "
        "values and must not blame the adopter:\n" + proc2.stdout)
    # Assert what the note DOES say: the file was not edited, the installer's
    # values changed, and --force is the remedy.
    assert "was not edited since last install" in proc2.stdout, (
        "the outdated NOTE must say the file was not edited:\n" + proc2.stdout)
    assert "re-run with --force" in proc2.stdout, (
        "the outdated NOTE must name the remedy:\n" + proc2.stdout)
    # The mark line must say "outdated" not "preserved".
    assert "outdated" in proc2.stdout, (
        "the file mark must say 'outdated':\n" + proc2.stdout)


def test_one_unreadable_run_does_not_delete_the_profiles_recorded_digest(tmp_path):
    """T428: the sidecar keeps exactly the paths whose planning outcome says they
    are preserve-capable, and the could-not-read outcome did not say so - so ONE
    run over an undecodable profile pruned its digest, and once the file read
    again the adopter was told for ever that no digest exists and nothing can
    be told about who changed what. Spawned, across the three runs."""
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "setup.py").write_text("", encoding="utf-8")
    args = ("--target", str(repo), "--no-graph", "--tools", "claude")
    first = _spawn_installer(*args)
    assert first.returncode == 0, first.stdout + first.stderr
    digests = repo / ".sdlc" / ".installed-digests"
    assert ".sdlc/profile.yml" in digests.read_text(encoding="utf-8")

    profile = repo / ".sdlc" / "profile.yml"
    good = profile.read_bytes()
    profile.write_bytes(b"\xff\xfe not utf-8 \x80\n")
    second = _spawn_installer(*args)
    assert "could not be read" in second.stdout, second.stdout + second.stderr
    assert ".sdlc/profile.yml" in digests.read_text(encoding="utf-8"), (
        "one unreadable moment deleted the record:\n" + second.stdout)

    profile.write_bytes(good)
    third = _spawn_installer(*args)
    assert third.returncode == 0, third.stdout + third.stderr
    assert "unverified" not in third.stdout, third.stdout


def test_preserve_note_does_blame_adopter_for_a_hand_edit(tmp_path):
    """When the adopter genuinely hand-edited the profile, the preserve NOTE must
    still say 'your edited'. Proven through the spawned installer (CLI contract)."""
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "setup.py").write_text("", encoding="utf-8")

    # First install
    proc1 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr

    # Hand-edit the profile
    profile = repo / ".sdlc" / "profile.yml"
    content = profile.read_text(encoding="utf-8")
    profile.write_text(content.replace("coverage_scope: changed",
                                       "coverage_scope: whole   # mine"),
                       encoding="utf-8")

    # Re-install with the SAME tools: the profile differs because the adopter edited it
    proc2 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    assert "your edited .sdlc/profile.yml was kept" in proc2.stdout, (
        "the adopter hand-edited the profile and the preserve NOTE must say so:\n"
        + proc2.stdout)


def test_digest_write_failure_prints_a_note_not_a_traceback(tmp_path):
    """If the digest sidecar cannot be WRITTEN (directory in its place), the
    installer must still exit 0, print the full report, AND print a NOTE naming
    the path with the correct consequence. Proven by spawning."""
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "setup.py").write_text("", encoding="utf-8")

    # First install
    proc1 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr

    # Place a directory where the digest file should be, so the write fails
    digest_path = repo / ".sdlc" / ".installed-digests"
    if digest_path.exists():
        digest_path.unlink()
    digest_path.mkdir()

    # Re-install: must still exit 0, print "Done", AND print the failure NOTE
    proc2 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc2.returncode == 0, (
        "a digest write failure must not crash the install:\n"
        + proc2.stdout + proc2.stderr)
    assert "Done." in proc2.stdout, (
        "the install report must still complete:\n" + proc2.stdout)
    assert "could not write" in proc2.stdout, (
        "the write failure must be announced in a NOTE:\n" + proc2.stdout)
    assert ".installed-digests" in proc2.stdout, (
        "the NOTE must name the file that could not be written:\n" + proc2.stdout)
    assert "will not be able" in proc2.stdout, (
        "the consequence must say WILL, not MAY:\n" + proc2.stdout)


def test_digest_file_is_gitignored(repo, spy):
    """The digest sidecar must be named in the generated .gitignore so it does
    not appear as untracked in the adopter's repository."""
    install(repo)
    gi = (repo / ".sdlc" / ".gitignore").read_text(encoding="utf-8")
    assert ".installed-digests" in gi, (
        "the digest sidecar must be ignored:\n" + gi)


def _simulate_pre_digest_install(repo, *, mutate_profile=False,
                                 mutate_gitignore=True):
    """Simulate a pre-digest-era install by deleting the digest file and
    optionally mutating files to differ from what the current template would
    write, as an older installer would have."""
    digest_path = repo / ".sdlc" / ".installed-digests"
    if digest_path.exists():
        digest_path.unlink()
    if mutate_gitignore:
        gi_path = repo / ".sdlc" / ".gitignore"
        old_gi = gi_path.read_text(encoding="utf-8")
        lines = [ln for ln in old_gi.splitlines(keepends=True)
                 if ".installed-digests" not in ln
                 and "Digest sidecar" not in ln]
        gi_path.write_text("".join(lines), encoding="utf-8")
    if mutate_profile:
        # Simulate an older template that produced slightly different content
        pr_path = repo / ".sdlc" / "profile.yml"
        old_pr = pr_path.read_text(encoding="utf-8")
        pr_path.write_text(old_pr + "# old-template-marker\n", encoding="utf-8")


def test_upgrade_says_unverified_not_your_edited(tmp_path):
    """On the first run after upgrade (no digest file), files that differ must
    NOT be called 'your edited' — the installer cannot tell who changed them.
    The honest message says 'first run with digest tracking' and 'cannot tell'.
    Tested for BOTH profile.yml AND .gitignore (T382 rd 4)."""
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "setup.py").write_text("", encoding="utf-8")

    proc1 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr

    # Simulate a pre-digest install with both profile and gitignore differing
    _simulate_pre_digest_install(repo, mutate_profile=True, mutate_gitignore=True)

    proc2 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    # Must NOT blame the adopter
    assert "your edited" not in proc2.stdout, (
        "upgrade must not blame the adopter:\n" + proc2.stdout)
    # Must NOT credit the installer either
    assert "was not edited since last install" not in proc2.stdout, (
        "upgrade must not credit the installer:\n" + proc2.stdout)
    # Must say the honest thing — no recorded digest, cannot tell
    assert "no recorded digest exists" in proc2.stdout, (
        "upgrade must say no digest exists:\n" + proc2.stdout)
    assert "cannot tell" in proc2.stdout, (
        "upgrade must say it cannot tell who changed the file:\n" + proc2.stdout)
    # Must warn that forcing replaces every preserved file
    assert "replaces every preserved file" in proc2.stdout, (
        "upgrade must warn that forcing replaces all preserved files:\n"
        + proc2.stdout)
    # Both profile AND gitignore must be reported as unverified
    assert ".sdlc/profile.yml differs" in proc2.stdout, (
        "profile must be named in the unverified NOTE:\n" + proc2.stdout)
    assert ".sdlc/.gitignore differs" in proc2.stdout, (
        "gitignore must be named in the unverified NOTE:\n" + proc2.stdout)
    # The mark must say "unverified"
    assert "unverified" in proc2.stdout, (
        "the file mark must say 'unverified':\n" + proc2.stdout)


def test_hand_edit_is_still_detected_after_two_re_runs(tmp_path):
    """The digest must NOT be updated when a file is preserved, or a hand-edit
    would be blessed as installer-written on the second re-run. Three runs:
    install, hand-edit, re-run (must blame), re-run again (must STILL blame).
    Proven through the spawned installer."""
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "setup.py").write_text("", encoding="utf-8")

    # Run 1: install
    proc1 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr

    # Hand-edit the profile
    profile = repo / ".sdlc" / "profile.yml"
    content = profile.read_text(encoding="utf-8")
    profile.write_text(content.replace("coverage_scope: changed",
                                       "coverage_scope: whole   # mine"),
                       encoding="utf-8")

    # Run 2: first re-install after edit -> must blame, naming the file
    proc2 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    assert "your edited .sdlc/profile.yml was kept" in proc2.stdout, (
        "first re-run after hand-edit must blame the adopter:\n" + proc2.stdout)

    # Run 3: second re-install -> must STILL blame (digest must not have changed)
    proc3 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc3.returncode == 0, proc3.stdout + proc3.stderr
    assert "your edited .sdlc/profile.yml was kept" in proc3.stdout, (
        "second re-run after hand-edit must still blame the adopter "
        "(the digest must not be updated when the file is preserved):\n"
        + proc3.stdout)


def test_forcing_does_not_destroy_the_digest_sidecar(tmp_path):
    """Forcing must update the sidecar with the forced content's digests, not
    prune it to nothing. Without this, the next non-forcing run after a force
    says cannot-tell where it should say outdated (T382 rd 6-7).

    The force must go through the UPDATE path (not unchanged), so the profile
    is hand-edited before forcing — this exercises the tail return that carries
    preserve_capable."""
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "setup.py").write_text("", encoding="utf-8")

    # Install
    proc1 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude")
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr
    digest_path = repo / ".sdlc" / ".installed-digests"
    assert digest_path.is_file(), "first install must create the sidecar"
    keys_before = [ln.split("\t")[0] for ln in
                   digest_path.read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.startswith("#")]
    assert ".sdlc/profile.yml" in keys_before, (
        f"profile must be in the sidecar before forcing: {keys_before}")

    # Hand-edit the profile so the forcing run goes through the UPDATE path
    profile = repo / ".sdlc" / "profile.yml"
    profile.write_text(profile.read_text(encoding="utf-8") + "# edited\n",
                       encoding="utf-8")

    # Force: the profile is overwritten (update), not unchanged
    proc2 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude", "--force")
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    keys_after = [ln.split("\t")[0] for ln in
                  digest_path.read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.startswith("#")]
    assert ".sdlc/profile.yml" in keys_after, (
        f"profile must survive in the sidecar after forcing: {keys_after}")

    # The run after forcing with a genuine change must say outdated, not cannot-tell
    proc3 = _spawn_installer("--target", str(repo), "--no-graph",
                             "--tools", "claude,cursor")
    assert proc3.returncode == 0, proc3.stdout + proc3.stderr
    assert "was not edited since last install" in proc3.stdout, (
        "after forcing, a genuine installer change must say outdated:\n"
        + proc3.stdout)
    assert "no recorded digest" not in proc3.stdout, (
        "after forcing, the sidecar must exist and the run must not say "
        "cannot-tell:\n" + proc3.stdout)


# ── the wiring, run for real ──────────────────────────────────────────────────
def test_the_flag_and_the_exit_contract_are_on_the_command_line():
    proc = subprocess.run([sys.executable, str(INSTALLER), "--help"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "--no-graph" in proc.stdout
    assert "exit status" in proc.stdout, (
        "a CI author's only documented basis for what 3-vs-1 means is --help and README")


def test_one_command_at_a_repository_root_produces_every_artifact(tmp_path):
    """The item's headline claim, end to end, as a developer runs it. The unit tests above
    all replace the subprocess; this is the one that would catch a wrong flag name, an
    exception escaping as a traceback, or an output directory the builder refuses."""
    repo = tmp_path / "demo"
    (repo / "src" / "main" / "resources").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "pom.xml").write_text("<project><artifactId>demo</artifactId></project>\n",
                                  encoding="utf-8")
    (repo / "src" / "main" / "resources" / "application.properties").write_text(
        "server.port=8080\n", encoding="utf-8")

    _project.seed(repo)
    proc = subprocess.run([sys.executable, str(INSTALLER), "--target", str(repo)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for artifact in (".sdlc/profile.yml", ".sdlc/.gitignore", ".sdlc/graph/graph.jsonl",
                     ".sdlc/graph/build_report.json"):
        assert (repo / artifact).is_file(), f"{artifact} is missing:\n{proc.stdout}"
    # Piped output must read in the order it happened. The parent's narration is
    # block-buffered on a pipe while the child writes the raw fd, so without a flush at
    # the spawn seam this log OPENS with the builder's whole report — thirty lines of
    # extractor output introduced by nothing, and "graph: building" printed after the
    # build it announces has finished.
    assert proc.stdout.index("graph: building") < proc.stdout.index("wrote "), (
        "the builder's output appears BEFORE the line announcing the build:\n"
        + proc.stdout)


def test_it_ignores_the_bytecode_an_adopters_own_python_leaves(repo, spy):
    """**`.sdlc/` is where a `deploy_command` and a release step LIVE**, so the
    first time either runs, Python drops a `__pycache__` beside it.

    Measured on a real repository: that cache dirtied the tree and `intake`
    refused the next run by name - a working install blocking itself for a file
    nobody wrote on purpose, and the fix is one line in the generator rather
    than an instruction every adopter has to rediscover.
    """
    install(repo)
    lines = (repo / ".sdlc" / ".gitignore").read_text(encoding="utf-8").split("\n")
    assert "__pycache__/" in lines and "*.pyc" in lines


# ── item 96: a CLI refusal is a PROCESS contract, spawned not called (T146) ──
# main() raising in-process cannot tell exit 2 from exit 1 from a traceback, and it is
# the exit code and the first stderr line an adopter's shell actually reads. These spawn
# the real interpreter and assert both. The empty-`--tools` case is the T146 half2 fix:
# "" / "   " / ",,," parsed to [] and the unconditional `claude` insert repaired the list
# BEFORE the validity check, so a caller who asked for no tools was handed one at exit 0.
# The other three pin refusals that behave correctly today but nobody spawned.


def _spawn_installer(*args: str) -> subprocess.CompletedProcess:
    # T442: the target carries its project metadata.
    if "--target" in args:
        _project.seed(Path(args[args.index("--target") + 1]))
    return subprocess.run([sys.executable, str(INSTALLER), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


@pytest.mark.parametrize("value", ["", "   ", ",,,", " , , "])
def test_an_empty_tools_list_is_refused_by_name_not_defaulted(tmp_path, value):
    """A list that names no tools must be refused BY NAME, never silently
    defaulted to claude: a caller who asked for nothing got exactly one thing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    proc = _spawn_installer("--target", str(repo), "--tools", value)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "--tools" in proc.stderr and "names no tools" in proc.stderr, proc.stderr
    assert not (repo / ".sdlc").exists(), "a refused install still wrote the scaffold"


def test_an_unknown_tool_is_refused_naming_it(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    proc = _spawn_installer("--target", str(repo), "--tools", "cursor,nope")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "unknown tool(s): nope" in proc.stderr, proc.stderr
    assert not (repo / ".sdlc").exists(), "a refused install still wrote the scaffold"


def test_estate_without_workspace_is_refused(tmp_path):
    proc = _spawn_installer("--target", str(tmp_path), "--estate")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "--estate needs --workspace" in proc.stderr, proc.stderr


def test_from_snapshot_without_estate_is_refused(tmp_path):
    proc = _spawn_installer("--target", str(tmp_path), "--from-snapshot")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "--from-snapshot only means something with --estate" in proc.stderr, proc.stderr
