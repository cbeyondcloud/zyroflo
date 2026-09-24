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


def tracked_python_by_name(root: Path = ROOT) -> dict[str, list[str]]:
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
    for path in _tracked_python(root):
        by_name.setdefault(path.name, []).append(path.relative_to(root).as_posix())
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


def test_membership_refuses_rather_than_reporting_an_empty_repository(tmp_path):
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
    # T289 gave the membership function a root, so it is POINTED at the
    # non-repository rather than steered there by patching a module constant.
    with pytest.raises(subprocess.CalledProcessError):
        tracked_python_by_name(tmp_path)


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


def test_membership_is_gits_answer_not_the_filesystems(tmp_path):
    """T289: T277's fix moved this guard from a filesystem walk to `git ls-files`, and
    nothing stopped it moving back - reverting it passed every test here, and the kill
    once claimed for it depended on a nested checkout the mutation pass itself created
    and then deleted. So the disagreement is built here, in a tree this test owns: a
    tracked `build.py`, and a gitignored copy of it that a walk would also count."""
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
    assert tracked_python_by_name(repo)["build.py"] == ["tools/build.py"]
