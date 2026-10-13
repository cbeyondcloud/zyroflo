"""Item 24 — the three graph-tier workflows, and the guard that keeps them true.

`workflows/*.md` are runner-facing SOPs: an agent executes them from the markdown
plus the CLIs alone (the M3 verification runs impact-review from a second provider
that has no MCP). A stale SOP naming a dead flag or a renamed tool is a wrong
instruction delivered with full confidence, so the load-bearing tests here are
currency guards: every `--flag` a workflow cites must exist in a shipped graph
CLI's argparse source, and the MCP tool names must be exactly the server's — read
live from `TOOL_NAMES`, not transcribed.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.graph.mcp_server import TOOL_NAMES  # noqa: E402
from tools.tests.test_dependencies import _tracked_python  # noqa: E402

WORKFLOWS = ("graph-build.md", "impact-review.md", "feature-impact.md")
#: Every CLI the workflows may cite flags from — grown with the surface: the
#: graph tier, and item 25's review pre-pass.
CLI_SOURCES = ("tools/graph/build.py", "tools/graph/impact.py",
               "tools/graph/pack.py", "tools/graph/validate.py",
               "tools/graph/mcp_server.py", "tools/review/prepass.py",
               "tools/review/hitl.py")

#: Flags that appear inside the documented command PREFIX rather than a graph
#: CLI's own parser. `--project` is uv's: it pins the harness environment when a
#: command is pasted from any other directory.
PREFIX_FLAGS = {"--project"}


def text_of(name: str) -> str:
    return (ROOT / "workflows" / name).read_text(encoding="utf-8")


def cli_flag_union() -> set[str]:
    flags: set[str] = set()
    for cli in CLI_SOURCES:
        src = (ROOT / cli).read_text(encoding="utf-8")
        flags.update(re.findall(r'add_argument\("(--[a-z][a-z0-9-]*)"', src))
    return flags


def tracked_python_by_name() -> dict[str, list[str]]:
    """Bare script name → every file in THIS repository that answers to it.

    Membership comes from `_tracked_python()` — `git ls-files` — and not from a
    walk of the working tree behind a hand-written skip list. The skip list was
    `{.git, __pycache__, .venv, third_party, tests}`, and what it could not
    name is the point: **gitignored is invisible to git, not to a filesystem
    walk.** A `git worktree` checked out inside the repository is gitignored and
    full of `.py` files, so `build.py` resolved to one file per live worktree
    and this guard reported an ambiguity the repository does not have. Four
    integrators met it and each spent real time proving their batch innocent,
    which is how a check gets switched off (`prepass.py`'s own recorded rule).

    The replacement asks git, which is the same argument
    `test_no_private_coordinates` makes in its docstring and the same defect
    `_tracked_python` was written for — `.sdlc-worktrees/`, one directory over.
    Reusing that helper rather than writing a fifth `git ls-files` is
    deliberate: a second implementation of membership is item 115's class.

    No exclusions survive, because measurement said none are needed: `third_party`
    has zero tracked `.py`, and with the whole tracked set every bare name the
    three SOPs cite resolves to exactly one file.
    """
    by_name: dict[str, list[str]] = {}
    for path in _tracked_python():
        by_name.setdefault(path.name, []).append(path.relative_to(ROOT).as_posix())
    return by_name


def ambiguous_citations(text: str, by_name: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
    """The bare `*.py` citations in one SOP that answer to more than one file."""
    found = []
    for cited in sorted(set(re.findall(r"`([A-Za-z0-9_./-]*\.py)\b", text))):
        if "/" in cited:
            continue
        homes = by_name.get(cited, [])
        if len(homes) > 1:
            found.append((cited, homes))
    return found


def test_a_bare_script_name_in_a_sop_resolves_to_exactly_one_file():
    """A runner reads `validate.py` and has to find it. Item 44 added a second
    file by that name (`tools/sdlc/validate.py`) and instantly made one SOP line
    ambiguous — the citation now carries enough path to be unique. This guards
    the next collision, which nothing else would notice."""
    by_name = tracked_python_by_name()
    # The actionable finding runs FIRST (item 128 / T290). When a bare name a SOP
    # cites resolves to two tracked files - the day a vendored `third_party/build.py`
    # lands beside `tools/graph/build.py` is the row's own example - a runner cannot
    # tell which file is meant, and the message naming that SOP is the one an
    # operator can act on. It has to be reported ahead of the non-vacuity anchor
    # below; put the other way round, the anchor pre-empts a real collision with a
    # sentence that is correct in form and unusable in fact.
    for name in WORKFLOWS:
        bad = ambiguous_citations(text_of(name), by_name)
        assert not bad, "; ".join(
            f"{name} cites a bare `{cited}`, which is {len(homes)} files "
            f"({', '.join(homes)}) - a runner cannot tell which one is meant"
            for cited, homes in bad)
    # Non-vacuity anchor, and the reason it stays: an external tool returning
    # nothing at exit 0 is not evidence of absence. An empty index makes the loop
    # above pass while looking at nothing at all, so the guard would report green
    # on a repository it never actually read; this exactly-one assertion fails
    # instead. It runs LAST so a genuine ambiguity is reported with its own message
    # first - the anchor now speaks only when the index itself is wrong.
    assert by_name.get("build.py") == ["tools/graph/build.py"], \
        f"the tracked index does not look like this repository: {by_name.get('build.py')}"


def test_a_real_collision_is_reported_ahead_of_the_non_vacuity_anchor(monkeypatch):
    """Item 128 / row T290. The day a second tracked `build.py` appears (the row's
    example is a vendored `third_party/build.py`), the guard is RIGHT to go red:
    `graph-build.md` cites a bare `build.py`, which a runner can no longer resolve
    to one file. What the operator must be told is THAT - which SOP is now
    ambiguous and why - not the non-vacuity anchor's `the tracked index does not
    look like this repository`, a sentence that is correct in form and unusable in
    fact at the one moment the check earns its keep.

    So the actionable per-SOP finding has to be reported AHEAD of the anchor. This
    drives the collision through the REAL guard against the REAL workflow text by
    replacing only membership; the reorder was also proven the literal way, by
    staging a real second `build.py` and spawning the guard as a process to read
    exactly what a person sees. Red before the reorder: the anchor fired first and
    this assertion saw its generic message instead of the finding that names the
    SOP."""
    monkeypatch.setattr(
        sys.modules[__name__], "tracked_python_by_name",
        lambda: {"build.py": ["third_party/build.py", "tools/graph/build.py"]})
    with pytest.raises(AssertionError) as exc:
        test_a_bare_script_name_in_a_sop_resolves_to_exactly_one_file()
    message = str(exc.value)
    assert "a runner cannot tell which one is meant" in message, \
        f"the actionable finding was not what surfaced; a person saw: {message!r}"
    assert "graph-build.md" in message and "third_party/build.py" in message, \
        f"the finding names neither the ambiguous SOP nor its collision: {message!r}"
    assert "does not look like this repository" not in message, \
        f"the non-vacuity anchor fired ahead of the real finding: {message!r}"


def test_a_name_that_really_is_two_tracked_files_is_still_caught():
    """The positive control. Narrowing the guard's scope to what git tracks
    must not have narrowed away the property it exists for, and a guard nobody
    can watch fail is a guard nobody has checked (item 115).

    The fixture is real and is the collision the guard was written for: item 44
    made `validate.py` two tracked files, and a SOP citing it bare must still be
    refused. The workflows themselves cite it with enough path to be unique,
    which is exactly why the control has to supply the offending sentence."""
    by_name = tracked_python_by_name()
    homes = by_name.get("validate.py", [])
    assert len(homes) >= 2, \
        f"the control's own fixture is gone: `validate.py` is now {homes}"
    assert ambiguous_citations("run `validate.py` against the repo", by_name) \
        == [("validate.py", homes)]
    # and the guard is not simply flagging everything
    assert ambiguous_citations("run `build.py` against the repo", by_name) == []


def test_membership_refuses_rather_than_reporting_an_empty_repository(tmp_path, monkeypatch):
    """Could-not-look is not clean — this repository's headline rule.

    A guard that silently passes when it cannot ask is worse than the walk it
    replaced, because it still prints a number. `_tracked_python` runs git with
    `check=True`, so a directory that is not a repository (or a git that is not
    there at all) raises and the guard ERRORS; it never comes back as an empty
    tree in which nothing is ambiguous. Asserted against the real binary in a
    real non-repository, because the exit code is the contract — and the
    fixture fails LOUDLY rather than vacuously if it is ever constructed
    somewhere that turns out to be a repository after all.

    The last block is the weld, and it is the point. The first two assert git's
    behaviour and `subprocess.run`'s; neither touches the helper, so they hold
    if somebody edits `_tracked_python` for `test_dependencies`' own needs and
    this module silently inherits the edit. Dropping its `check=True` leaves
    every other assertion here green while the guard starts reporting an empty
    repository as a clean one. So the refusal is asserted through the REAL
    membership function, pointed at the same non-repository."""
    argv = ["git", "ls-files", "-z", "*.py"]
    proc = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode != 0, \
        f"git claims to have listed a non-repository: {proc.stdout!r}"
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True, check=True)
    # Function-local, as `test_schema` imports from `test_policy`: the name
    # needed is the helper's module, not the helper, and only here.
    from tools.tests import test_dependencies as deps
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        tracked_python_by_name()


def test_the_three_workflows_exist_with_the_sop_skeleton():
    """The house SOP shape, including the section the self-improvement loop
    writes into (CLAUDE.md: learned constraints land in Edge Cases / Notes)."""
    for name in WORKFLOWS:
        text = text_of(name)
        for section in ("## Purpose", "## Steps", "## Verify",
                        "## Edge Cases / Notes", "## Access tiers"):
            assert section in text, f"{name} is missing {section}"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_workflow_names_both_access_tiers(name):
    """MCP when the server is registered, CLIs always: the tier doctrine is
    structural per document, because a workflow only one provider can execute
    is a provider lock-in written as prose."""
    text = text_of(name)
    assert "MCP" in text, f"{name} never mentions the MCP tier"
    assert "uv run --project <harness>" in text, \
        f"{name} lacks the runner-agnostic CLI prefix"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_cited_flag_exists_in_a_shipped_graph_cli(name):
    """The currency guard. A flag documented here and absent from every graph
    CLI is an instruction that fails for whoever follows it — the exact rot
    mode of SOPs kept apart from the code they describe."""
    known = cli_flag_union() | PREFIX_FLAGS
    cited = set(re.findall(r"--[a-z][a-z0-9-]*", text_of(name)))
    dead = sorted(cited - known)
    assert not dead, f"{name} cites flags no shipped graph CLI accepts: {dead}"


def test_the_flag_union_is_not_vacuous():
    """The guard above passes trivially if the extraction regex rots and the
    union comes back empty; the flags every reader knows must be in it."""
    flags = cli_flag_union()
    assert {"--graph-dir", "--check-fresh", "--diff", "--budget"} <= flags


def test_all_eight_mcp_tools_are_documented():
    """The three workflows are the eight tools' user manual — read live from the
    server so a renamed tool breaks this test, not a reader's session."""
    union = "".join(text_of(n) for n in WORKFLOWS)
    missing = [t for t in TOOL_NAMES if f"`{t}`" not in union]
    assert not missing, f"undocumented server tools: {missing}"


def test_every_cited_graph_cli_ships():
    union = "".join(text_of(n) for n in WORKFLOWS)
    cited = set(re.findall(r"([\w-]+\.py)", union))
    assert "build.py" in cited, "the extraction regex found nothing it should"
    for cli in cited:
        candidates = [ROOT / "tools" / "graph" / cli,
                      ROOT / "tools" / "review" / cli, ROOT / cli]
        assert any(c.exists() for c in candidates), \
            f"the workflows cite {cli}, which ships nowhere"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_no_machine_specific_paths(name):
    """Genericity: SOPs carry placeholders (`<harness>`, `<graph-dir>`), never a
    developer's drive or home directory."""
    text = text_of(name)
    assert not re.search(r"[A-Za-z]:\\", text), f"{name} carries a drive path"
    assert not re.search(r"(/home/|/Users/)", text), f"{name} carries a home path"
    assert "<harness>" in text


def test_impact_review_carries_the_seed_and_generated_doctrines():
    """The two doctrines a reviewer must not lose: a `none`-mode file
    contributed no graph evidence, and a generated row redirects the edit."""
    text = text_of("impact-review.md")
    for mode in ("`hunk`", "`provenance`", "`none`"):
        assert mode in text
    assert "regenerate, don't edit" in text
    assert "regenerate_from" in text
    assert "pack.json" in text, "the never-trimmed machine copy must be named"
    # Item 27: the clock starts at step one and closes at the end — review time
    # unmeasured at M2 makes M4's <15-min gate unreadable when it arrives.
    assert "--begin" in text and "--end" in text and "--abandon" in text
    assert "no reviewer identity" in text, \
        "the anti-Goodhart posture is part of the SOP, not an implementation note"


def test_feature_impact_states_the_semantic_layer_limit():
    """UC-04 before item 79 lands: entry points and domain aliases are not yet
    facts, and search is lexical. A workflow that oversells the graph makes
    every proposal built on it overconfident."""
    text = text_of("feature-impact.md")
    assert "item 79" in text
    assert "lexical" in text
    assert "entry points" in text


def test_graph_build_routes_freshness_validation_and_the_baseline():
    text = text_of("graph-build.md")
    assert "`check_fresh`" in text and "--check-fresh" in text, \
        "freshness must be named in both tiers"
    assert "--publish-snapshot" in text
    assert "--targets" in text, \
        "recalibrated floors reach the tool only explicitly - the workflow " \
        "must carry that doctrine, not just the default verdict"
    assert "zero-silent-drops" in text


def test_graph_build_gives_both_ways_on_its_parser_note_can_end_with():
    """Batch 17 (review B17-2; the owner's go, 2026-10-13). Step 3's NOTE ended with one way
    on, and this SOP said it: install the parser, then re-publish. The NOTE now ends with one of
    two - a row naming no parser its reader declares has none to install - and an SOP giving the
    first alone sends a runner to an install that cannot lift that row. Each way is read live
    from `build.py`, as the tool names are from `TOOL_NAMES`, and the step quotes it whole: a
    way reworded there fails here, not as a stale instruction a runner follows."""
    import tools.graph.build as B

    step = _freshness_step()
    for way in (B.PARSER_WAY, B.UNASKABLE_WAY):
        assert way in step, f"step 3 does not give the NOTE's way on: {way!r}"


def test_graph_build_names_each_key_check_fresh_serves_the_note_under(tmp_path):
    """Review B17-10: the step's MCP clause names the result keys a runner reads the NOTE's
    content from, and nothing held them - a key renamed in code would leave the SOP naming one
    the result no longer carries, as the way on once went stale. Held against a real
    `check_freshness` result: every `unparsed` key it carries is named, and every one named is
    carried."""
    import tools.graph.build as B
    from tools.tests.test_provider_files import _fresh_single_repo

    result = B.check_freshness(None, target=_fresh_single_repo(tmp_path))
    carried = {key for key in result["freshness"] if key.startswith("unparsed")}
    named = set(re.findall(r"`(unparsed\w*)`", _freshness_step()))
    assert carried and named == carried, \
        f"step 3 names {sorted(named)}; check_fresh's result carries {sorted(carried)}"


def test_graph_build_s_first_step_names_the_clean_build_verdict(tmp_path):
    """T639 (this batch's approved scope asked for it, ground rule 3): step 1 sends a runner to
    `graph_status` to learn what the graph is, and listed what that answers - but not whether
    the build was clean, which it carries now, a crashed link pass among its problems. Held
    against a real status result, as step 3's keys are."""
    import tools.graph.build as B
    from tools.graph import mcp_server as M
    from tools.tests.test_provider_files import _fresh_single_repo

    repo = _fresh_single_repo(tmp_path)
    out = tmp_path / "graph"
    assert B.main(["--target", str(repo), "--out", str(out), "--run-id", "fixed"]) == 0
    assert "clean_build" in M.GraphService(out).status()
    (step,) = [s for s in _steps(text_of("graph-build.md"))
               if s.startswith("**Ask what the graph is.**")]
    assert "`clean_build`" in step, step


def _freshness_step() -> str:
    """Step 3 of `graph-build.md`, whitespace flattened."""
    (step,) = [s for s in _steps(text_of("graph-build.md"))
               if s.startswith("**Check freshness.**")]
    return step


def _steps(text: str) -> list[str]:
    """Each numbered step under `## Steps`, in the SOP's order, with whitespace
    flattened so a command the markdown wraps across two lines reads as one."""
    section = text.split("\n## Steps\n", 1)[1].split("\n## ", 1)[0]
    return [" ".join(s.split()) for s in re.split(r"^\d+\. ", section, flags=re.M)[1:]]


def _taught_builds(step: str, repo: Path) -> list[list[str]]:
    """The one-repo `build.py` commands a step teaches, as argv - taken OUT OF THE
    DOCUMENT with only `<repo>` filled in, never assembled here. A workspace form
    names a `<toml>` a one-repo fixture has no use for, so it is left out."""
    found = []
    for span in re.findall(r"`([^`]*\bbuild\.py\b[^`]*)`", step):
        toks = span.split()
        if "--target" not in toks or any(t.startswith("<") and t != "<repo>" for t in toks):
            continue
        i = next(k for k, t in enumerate(toks) if t.endswith("build.py"))
        found.append([sys.executable, str(ROOT / "tools" / "graph" / "build.py"),
                      *(str(repo) if t == "<repo>" else t for t in toks[i + 1:])])
    return found


def test_the_sop_run_in_its_own_order_answers_on_a_fresh_install(tmp_path):
    """T388 (owner ruling EDIT, 2026-09-24). T226 measured that a fresh install has
    no freshness stamps until `--publish-snapshot` runs, and fixed the skill; this
    SOP still checked freshness at step 2 and published at step 6, "optional" - so
    an adopter following it in order met `STALE ... no freshness stamps`, exit 2,
    at the freshness step. Measured again before the edit, and wider: after a
    change, the stale step's rebuild without `--publish-snapshot` - full or
    `--incremental` - left the check STALE as well, because only a publish moves
    the stamps.

    Held as a process, in the SOP's own order: every one-repo `build.py` command
    the steps teach, spawned against a fresh committed tree with no baseline. (The
    installer's chained graph is not rebuilt first: the publish builds for itself
    and the check reads only stamps.) Whatever precedes the first freshness check
    must run clean and that check must answer `current`; then, after an edit the
    check sees, the step that follows it must make the check answer `current`
    again. The `uv run --project <harness>` prefix is held by
    `test_every_workflow_names_both_access_tiers`, so each command runs on this
    environment's interpreter - the one that prefix selects."""
    from tools.tests.test_provider_files import _fresh_single_repo

    repo = _fresh_single_repo(tmp_path)
    steps = [_taught_builds(s, repo) for s in _steps(text_of("graph-build.md"))]
    checks = [k for k, cmds in enumerate(steps) if any("--check-fresh" in c for c in cmds)]
    assert checks, ("graph-build.md hands over no one-repo freshness check "
                    "(`build.py --target <repo> --check-fresh`) - a step must give the "
                    "command, not describe it (item 128)")
    at = checks[0]
    check = next(c for c in steps[at] if "--check-fresh" in c)

    def spawn(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=600)

    for argv in (a for cmds in steps[:at] for a in cmds):
        ran = spawn(argv)
        assert ran.returncode == 0, f"{argv[2:]}\n{ran.stdout}{ran.stderr}"
    verdict = spawn(check)
    assert verdict.returncode == 0 and "current" in verdict.stdout, (
        f"followed in order on a fresh install, step {at + 1}'s freshness check "
        f"cannot answer:\n{verdict.stdout}{verdict.stderr}")

    (repo / "app.py").write_text("import os\nTOKEN = os.environ['B']\n", encoding="utf-8")
    stale = spawn(check)
    assert stale.returncode == 2 and "STALE" in stale.stdout, \
        f"the control: the check did not see the edit\n{stale.stdout}{stale.stderr}"
    rebuild = steps[at + 1] if at + 1 < len(steps) else []
    assert rebuild, f"step {at + 2}, after the freshness check, teaches no one-repo build"
    for argv in rebuild:
        ran = spawn(argv)
        assert ran.returncode == 0, f"{argv[2:]}\n{ran.stdout}{ran.stderr}"
    again = spawn(check)
    assert again.returncode == 0 and "current" in again.stdout, (
        f"step {at + 2} rebuilt without moving the baseline, so the freshness check "
        f"still answers STALE:\n{again.stdout}{again.stderr}")


def test_impact_review_states_where_the_step_paths_live():
    """Item 33's class, found inside the SOP by the first live goose run
    (2026-08-30, M6-C2): the preamble taught the CLI prefix with a graph
    example and step 1 then said bare `tools/review/hitl.py` — so an agent
    took the literal reading and ran it relative to the REPOSITORY, where the
    file is not. The requirement — every step path lives under <harness> and
    runs through the prefix — was implied, never stated. Asserted as the
    SENTENCE plus the worked hitl example, not as vocabulary."""
    text = text_of("impact-review.md")
    assert ("lives under `<harness>` — never\nunder the repository" in text
            or "lives under `<harness>` — never under the repository" in text)
    assert 'python <harness>/tools/review/hitl.py --target <repo> --begin' in text


def test_membership_is_gits_answer_not_the_filesystems(tmp_path, monkeypatch):
    """T289: T277's fix moved this guard from a filesystem walk to `git ls-files`, and
    nothing stopped it moving back - reverting it passed every test here, and the kill
    once claimed for it depended on a nested checkout the mutation pass itself created
    and then deleted. So the disagreement is built here, in a tree this test owns: a
    tracked `build.py`, and a gitignored copy of it that a walk would also count.

    The helpers stay anchored at `ROOT` - `test_repo_wide_guards` derives the sweep from
    exactly that anchor, and a root PARAMETER hid four real guards from it (measured,
    batch 2's first CI run) - so the test points the modules' `ROOT` at its tree, as the
    could-not-look weld above already does."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "build.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("scratch/\n", encoding="utf-8")
    (repo / "scratch").mkdir()
    (repo / "scratch" / "build.py").write_text("x = 2\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    assert len(list(repo.rglob("build.py"))) == 2, "the control: a walk sees both"
    from tools.tests import test_dependencies as deps
    monkeypatch.setattr(deps, "ROOT", repo)
    monkeypatch.setattr(sys.modules[__name__], "ROOT", repo)
    assert tracked_python_by_name()["build.py"] == ["tools/build.py"]
