"""P8 — the release cut: version, changelog, notes, tag. Item 65.

`release.version_command`, `changelog_command`, `tag_command` and
`notes_from_run` have been in the shipped profile template since item 60 and
**nothing read any of them**. The P8 SOP told a runner to draft them, which is
an instruction to do it by hand — the class this project has now found five
times (`phases[]` before item 36, `gate_profile` before 63, a run's `kind`
before 45, a component's `deploy_command` before 46).

The properties worth more than the rest of this file:

**A skipped step is RECORDED, not absent.** *Nothing ran* and *nothing failed*
are identical output unless something insists they differ.

**Blank does not mean the same thing for all three keys**, because the template
does not say it does: version and changelog blank = skipped, tag blank = the
plain git default, stated in the template's own comment.

**Nothing here publishes.** A tag may be created locally; pushing it is the
merge click, which stays a human's — asserted by walking the function.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import release as R  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

BASE = ("stack: python\ngate_profile: solo\n"
        "phases: [intake, requirements, design, implementation, test, review, "
        "triage, release, deploy, closeout]\n"
        'test_command: "pytest -q"\nreview:\n  block_on: [critical, high]\n')


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(BASE, encoding="utf-8")
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    return r


def declare(repo: Path, **dials: str) -> None:
    """A `release:` block with only the keys a test cares about."""
    lines = ["release:"]
    lines += [f'  {k}: "{v}"' for k, v in dials.items()]
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8") + "\n".join(lines) + "\n",
                 encoding="utf-8")


def at_p8(repo: Path) -> str:
    run_id = ledger.create(repo, "requirement", "a change worth releasing")["run_id"]
    ledger.run_dir(repo, run_id).mkdir(parents=True, exist_ok=True)
    ledger.advance(repo, run_id, phase="P8", note="branch pushed")
    return run_id


ECHO = 'python -c \\"print(\'{}\')\\"'


# ── a skipped step is recorded, not absent ──────────────────────────────────


def test_a_blank_command_is_a_SKIPPED_step_with_its_reason(repo):
    """Not a step that passed, and not a step that is missing from the record."""
    declare(repo, tag_command="python -c \"pass\"")
    row = R.cut(repo, at_p8(repo))
    by = {s["step"]: s for s in row["steps"]}
    assert by["version"]["source"] == "skipped"
    assert "no command is declared" in by["version"]["why"]
    assert by["changelog"]["source"] == "skipped"
    assert row["skipped"] == 2


def test_every_declared_step_appears_in_the_record(repo):
    declare(repo, version_command="python -c \"print('1.4.0')\"",
            changelog_command="python -c \"print('changelog')\"",
            tag_command="python -c \"print('tagged')\"")
    row = R.cut(repo, at_p8(repo))
    assert [s["step"] for s in row["steps"]] == list(R.CUT_STEPS)
    assert row["ran"] == 3 and row["skipped"] == 0 and row["cut"] is True


def test_the_steps_run_in_the_order_a_release_needs(repo):
    """A changelog is written FOR a version and a tag names one. Any other
    order describes a release that does not exist yet."""
    assert R.CUT_STEPS == ("version", "changelog", "tag")


# ── blank means different things per key, because the template says so ──────


def test_a_blank_tag_command_uses_the_plain_git_DEFAULT(repo):
    """The template's own comment: `blank = plain git tag -a {version}`. Reading
    version's rule onto tag would silently stop tagging."""
    declare(repo, version_command="python -c \"print('2.0.0')\"")
    row = R.cut(repo, at_p8(repo))
    tag = [s for s in row["steps"] if s["step"] == "tag"][0]
    assert tag["source"] == "default"
    assert tag["command"].startswith("git tag -a 2.0.0")


def test_the_version_reaches_the_later_steps(repo):
    declare(repo, version_command="python -c \"print('3.1.4')\"",
            changelog_command="python -c \"print('for {version}')\"",
            tag_command="python -c \"print('tag {version}')\"")
    row = R.cut(repo, at_p8(repo))
    assert row["version"] == "3.1.4"
    assert all("3.1.4" in s["command"] for s in row["steps"][1:])


def test_the_level_reaches_the_version_command(repo):
    declare(repo, version_command="python -c \"print('{level}')\"")
    row = R.cut(repo, at_p8(repo), level="minor")
    assert "minor" in row["steps"][0]["command"]


def test_a_version_that_cannot_be_determined_does_not_invent_one(repo):
    """The later steps say they had nothing to name, rather than tagging the
    empty string. This module names no ecosystem, so it cannot guess harder."""
    declare(repo, version_command="python -c \"print('built ok, no version here')\"",
            changelog_command="python -c \"print('nope')\"")
    row = R.cut(repo, at_p8(repo))
    assert row["version"] == ""
    later = [s for s in row["steps"] if s["step"] != "version"]
    assert all(s["exit_code"] is None for s in later)
    assert all("nothing to name" in s["why"] for s in later)


@pytest.mark.parametrize("printed,expect", [
    ("1.2.3", "1.2.3"), ("v2.0.0", "v2.0.0"), ("1.2", "1.2"),
    ("1.2.3-rc.1", "1.2.3-rc.1"), ("no version at all", ""),
    ("noise\n9.9.9", "9.9.9"), ("", ""),
    # TWO version-shaped lines, which is the only case that says which END of
    # the output is read. A bump command routinely prints the old version and
    # then the new one, and the new one is the answer. Every other case here is
    # blind to the direction - found by mutation, which reversed the scan and
    # survived the whole file.
    ("1.0.0\n2.0.0", "2.0.0"),
    ("Updating from 4.5.6 to 4.6.0", ""),
])
def test_the_version_is_read_off_the_LAST_line_that_is_one(printed, expect):
    assert R._version_from(printed) == expect


# ── nothing here publishes ──────────────────────────────────────────────────


def test_the_cut_reaches_no_push_path(repo):
    """**The merge click stays a human's, and so does publishing a tag.**

    Walked as AST over the function body rather than searched as text, for the
    reason item 47 learned when its first `gh pr merge` ban failed on a section
    header: prose that describes a rule is not a violation of it.
    """
    tree = ast.parse((ROOT / "tools" / "sdlc" / "release.py").read_text(
        encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "cut")
    called = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(f.id if isinstance(f, ast.Name)
                       else getattr(f, "attr", ""))
    for banned in ("push", "open_pr", "_gh"):
        assert banned not in called, f"the release cut reaches {banned}()"


def test_the_default_tag_command_does_not_push(repo):
    assert "push" not in R.TAG_DEFAULT


# ── a run's title is text, never shell syntax (T501) ────────────────────────


TITLE = "Fix the \"retry\" path's 100% case"


def _committed(repo: Path) -> None:
    """A commit to tag, and an identity to tag it with: a CI container has none."""
    for args in (["config", "user.email", "t@e"], ["config", "user.name", "t"],
                 ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True, timeout=120)


def _at_p8_titled(repo: Path, title: str) -> str:
    run_id = ledger.create(repo, "requirement", title)["run_id"]
    ledger.advance(repo, run_id, phase="P8", note="branch pushed")
    return run_id


def _step(row: dict, name: str) -> dict:
    return next(s for s in row["steps"] if s["step"] == name)


def test_the_default_tag_carries_the_title_byte_for_byte(repo):
    """T501. The default tag ran `git tag -a {version} -m "{title}"` through a
    shell, and the title is the specification's own text, so a quote in it
    ended the argument and what followed was read as shell syntax. Run as an
    argument list, the tag's message is the title exactly."""
    _committed(repo)
    declare(repo, version_command="python -c \"print('1.0.0')\"")
    tag = _step(R.cut(repo, _at_p8_titled(repo, TITLE)), "tag")
    assert tag["source"] == "default" and tag["exit_code"] == 0, tag
    said = subprocess.run(["git", "-C", str(repo), "tag", "-l",
                           "--format=%(contents)", "1.0.0"],
                          capture_output=True, text=True, encoding="utf-8",
                          check=True, timeout=120).stdout
    assert said.strip() == TITLE


def test_a_declared_command_is_not_filled_with_a_title_a_shell_would_read(repo):
    """T501, for a DECLARED command. It runs through the platform's shell, so
    a value is written into it only when bash, cmd and PowerShell all read
    every character as text; otherwise the step does not run, and says which
    placeholder stopped it."""
    declare(repo, version_command="python -c \"print('1.0.0')\"",
            changelog_command="python -c \"print('{title}')\"")
    log = _step(R.cut(repo, _at_p8_titled(repo, TITLE)), "changelog")
    assert log["exit_code"] is None and "{title}" in log["why"], log


def test_a_declared_command_is_still_filled_with_a_plain_title(repo):
    """Rule-28 control for T501: a title that is only text still arrives."""
    declare(repo, version_command="python -c \"print('1.0.0')\"",
            changelog_command="python -c \"print('{title}')\"")
    title = "Add a retry to the outbound client"
    log = _step(R.cut(repo, _at_p8_titled(repo, title)), "changelog")
    assert log["exit_code"] == 0 and title in log["stdout"], log


def test_the_artifact_check_writes_only_a_plain_version_into_its_command(repo):
    """T501's rule, where the artifact is asked its version: a `--version`
    given by hand is written into the declared command only as text."""
    declare(repo, artifact_version_command="python -c \"print('{version}')\"")
    got = R.artifact_version(repo, _at_p8_titled(repo, "a change"), "1.0.0\"; x")
    assert got["checked"] is False, got
    assert "was not written into this command" in got["why"], got


# ── notes come from the run's own artifacts ─────────────────────────────────


def test_notes_are_drafted_from_what_the_run_RECORDED(repo):
    """Anything else would be a summary of what somebody remembers doing."""
    run_id = at_p8(repo)
    (ledger.run_dir(repo, run_id) / "40-impl").mkdir(parents=True, exist_ok=True)
    (ledger.run_dir(repo, run_id) / "40-impl" / "change.patch").write_text(
        "diff\n", encoding="utf-8")
    ledger.record_artifact(repo, run_id, "40-impl/")
    notes = R.notes_for(repo, run_id)
    assert "a change worth releasing" in notes
    assert "40-impl/" in notes
    assert "nothing here was published" in notes


def test_notes_are_drafted_only_when_the_profile_asks(repo):
    declare(repo, version_command="python -c \"print('1.0.0')\"")
    assert R.cut(repo, at_p8(repo))["notes"] == ""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8") + "  notes_from_run: true\n",
                 encoding="utf-8")
    assert R.cut(repo, at_p8(repo))["notes"] != ""


def test_a_run_whose_state_cannot_be_read_refuses(repo):
    with pytest.raises(R.ReleaseError):
        R.notes_for(repo, "20260101-000000-nosuch")


# ── a cut that produced nothing is not a cut ────────────────────────────────


def test_every_step_blank_and_no_notes_is_NOT_a_release_that_was_cut(repo):
    """The phase would otherwise report a clean cut for having done nothing -
    item 40's shape, which P8 already met once in item 47's `none` verdict."""
    p = repo / ".sdlc" / "profile.yml"
    p.write_text(p.read_text(encoding="utf-8")
                 + 'release:\n  tag_command: ""\n', encoding="utf-8")
    run_id = at_p8(repo)
    row = R.cut(repo, run_id)
    # tag falls back to the git default, which cannot run with no version
    assert row["version"] == ""
    assert row["cut"] is False


def test_a_failing_step_is_not_a_cut(repo):
    declare(repo, version_command="python -c \"import sys; sys.exit(2)\"")
    row = R.cut(repo, at_p8(repo))
    assert row["cut"] is False
    assert row["steps"][0]["exit_code"] == 2


def test_a_step_that_could_not_start_is_neither_pass_nor_fail(repo):
    declare(repo, version_command="this-command-does-not-exist-anywhere")
    row = R.cut(repo, at_p8(repo))
    assert row["cut"] is False


# ── wired into the phase contract ───────────────────────────────────────────


def test_p8_owes_the_cut_through_the_conditional_seam():
    assert ST.CUT_RESULT in ST.by_phase("P8").conditional
    assert ST.CI_RESULT in ST.by_phase("P8").conditional
    assert ST.CUT_RESULT == R.CUT_RESULT          # one name, asked for


def test_a_declared_release_with_no_cut_is_owed(repo):
    declare(repo, version_command="python -c \"print('1.0.0')\"")
    owed = R.cut_problems(repo, at_p8(repo))
    assert len(owed) == 1 and R.CUT_RESULT in owed[0]
    assert "release nobody can name" in owed[0]


def test_a_repository_declaring_no_release_commands_owes_nothing(repo):
    assert R.cut_problems(repo, at_p8(repo)) == []


def test_a_failed_cut_holds_the_phase(repo):
    declare(repo, version_command="python -c \"import sys; sys.exit(2)\"")
    run_id = at_p8(repo)
    R.cut(repo, run_id)
    owed = R.cut_problems(repo, run_id)
    assert len(owed) == 1 and "did not complete" in owed[0]
    assert "no version, changelog or tag behind it" in owed[0]


def test_a_completed_cut_owes_nothing(repo):
    declare(repo, version_command="python -c \"print('1.0.0')\"",
            changelog_command="python -c \"pass\"",
            tag_command="python -c \"pass\"")
    run_id = at_p8(repo)
    R.cut(repo, run_id)
    assert R.cut_problems(repo, run_id) == []


def test_an_unreadable_cut_is_refused_not_read_as_complete(repo):
    declare(repo, version_command="python -c \"print('1.0.0')\"")
    run_id = at_p8(repo)
    (ledger.run_dir(repo, run_id) / R.CUT_RESULT).write_text("{not json",
                                                              encoding="utf-8")
    owed = R.cut_problems(repo, run_id)
    assert len(owed) == 1 and "could not be read" in owed[0]


def test_p8_obligations_ask_about_BOTH_answers(repo):
    """CI is what somebody else's machine said; the cut is what this phase
    produced. A phase that owed only one of them would leave the other
    unasked, which is how `release.version_command` went unread for so long."""
    declare(repo, version_command="python -c \"print('1.0.0')\"")
    run_id = at_p8(repo)
    owed = R.obligations(repo, run_id)
    assert any(R.RESULT in o for o in owed), owed
    assert any(R.CUT_RESULT in o for o in owed), owed


# ── it cuts the RUN'S tree, not the base ────────────────────────────────────


def test_the_cut_runs_in_the_runs_own_tree(repo, tmp_path):
    """Item 46's defect, not repeated: a release cut run from the repository
    root would version and tag the BASE."""
    from tools.sdlc import worktree as W

    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@e", "-c",
                    "user.name=t", "commit", "-qm", "base"], check=True,
                   capture_output=True, timeout=120)
    run_id = at_p8(repo)
    W.create(repo, run_id)
    home = Path(next(iter(W.load_record(repo, run_id)["components"].values()))["home"])
    declare(repo, version_command="python -c \"import os; print(os.getcwd())\"")
    row = R.cut(repo, run_id)
    # T127: `where` stored RELATIVE (item 120), resolving to the live tree.
    assert not Path(row["where"]).is_absolute(), row["where"]
    assert (repo / row["where"]).resolve() == home.resolve()
    assert Path(row["steps"][0]["stdout"].strip()).resolve() == home.resolve()


def test_a_run_with_no_tree_cuts_from_the_root(repo):
    declare(repo, version_command="python -c \"print('1.0.0')\"")
    # T127: the run root itself, stored relative and resolving back to the repo.
    row = R.cut(repo, at_p8(repo))
    assert not Path(row["where"]).is_absolute(), row["where"]
    assert (repo / row["where"]).resolve() == repo.resolve()


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_cuts_and_reports(repo, capsys):
    # A tag_command as well as a version, because the DEFAULT tag is real git
    # and this test is about the CLI rather than about git.
    declare(repo, version_command="python -c \"print('1.2.3')\"",
            tag_command="python -c \"pass\"")
    run_id = at_p8(repo)
    assert R.main(["--target", str(repo), "--run", run_id, "--cut"]) == 0
    out = capsys.readouterr().out
    assert "1.2.3" in out and "skipped" in out


def test_the_cli_exits_nonzero_when_the_cut_did_not_complete(repo, capsys):
    declare(repo, version_command="python -c \"import sys; sys.exit(2)\"")
    run_id = at_p8(repo)
    assert R.main(["--target", str(repo), "--run", run_id, "--cut"]) == 1


def test_git_refusing_to_tag_is_not_a_cut(repo):
    """**Found by the CLI test failing, and kept.** The default tag command is
    real git, and git cannot tag a repository with no commits - exit 128. A
    release whose tag step failed has no tag, and calling that a cut would be
    the green this whole phase exists to refuse.
    """
    declare(repo, version_command="python -c \"print('1.2.3')\"")
    row = R.cut(repo, at_p8(repo))
    tag = [s for s in row["steps"] if s["step"] == "tag"][0]
    assert tag["source"] == "default" and tag["exit_code"] != 0
    assert row["cut"] is False


def test_p8_asks_about_the_cut_EVEN_WHEN_CI_HAS_ANSWERED(repo):
    """**The branch the other test could not reach.**

    `obligations` has two returns - one for a run with no CI verdict yet, one
    for a run that has one - and the existing test only ever exercised the
    first, because its fixture recorded no verdict. So the second could stop
    asking about the cut entirely with the suite still green, which is how
    `release.version_command` went unread for five milestones in the first
    place: a question nobody asks has no failing test.
    """
    declare(repo, version_command="python -c \"print('1.0.0')\"")
    run_id = at_p8(repo)
    (ledger.run_dir(repo, run_id) / R.RESULT).write_text(
        json.dumps({"state": R.GREEN, "why": "every check passed"}),
        encoding="utf-8")
    owed = R.obligations(repo, run_id)
    assert owed, "a green CI must not excuse an uncut release"
    assert any(R.CUT_RESULT in o for o in owed), owed


def test_a_timed_out_cut_step_still_records_what_it_printed(repo):
    """A release step killed on the clock keeps its partial streams: the step
    row carries `exit_code: None` AND what the command managed to print."""
    declare(repo, version_command="python -c \"import sys,time; "
                                  "print('partial-out'); "
                                  "print('partial-err', file=sys.stderr); "
                                  "sys.stdout.flush(); sys.stderr.flush(); "
                                  "time.sleep(5)\"")
    row = R.cut(repo, at_p8(repo), timeout=1)
    by = {s["step"]: s for s in row["steps"]}
    assert by["version"]["exit_code"] is None, by["version"]
    assert "partial-out" in by["version"]["stdout"], by["version"]["stdout"]
    assert "partial-err" in by["version"]["stderr"], by["version"]["stderr"]


def test_a_timed_out_artifact_check_keeps_what_the_command_printed(repo):
    """The artifact check's refusal quotes the command's output. On a timeout
    it had nothing to quote, so `could not look` came with no evidence."""
    declare(repo, **{R.ARTIFACT_VERSION_DIAL:
                     "python -c \"import sys,time; print('partial-out'); "
                     "print('partial-err', file=sys.stderr); sys.stdout.flush(); "
                     "sys.stderr.flush(); time.sleep(5)\""})
    said = R.artifact_version(repo, at_p8(repo), "2.9.4", timeout=1)
    assert said["checked"] is False and said["agrees"] is None
    # BOTH streams. `err or out` picked one, and the one it picked on a
    # timeout is stderr - so the version the command printed to stdout, which
    # is the whole thing this check exists to read, was the half discarded.
    assert "partial-err" in said["why"], said["why"]
    assert "partial-out" in said["why"], said["why"]


def test_a_noisy_hung_artifact_check_still_names_the_timeout(repo):
    """`why` is quoted to 200 characters, so a command that printed 400 before
    it hung decides what an operator reads. With the reason joined after that
    output, `could not look` arrived with nothing saying the CLOCK ended it
    rather than the command answering."""
    declare(repo, **{R.ARTIFACT_VERSION_DIAL:
                     "python -c \"import sys,time; "
                     "print('x' * 400, file=sys.stderr); sys.stderr.flush(); "
                     "time.sleep(5)\""})
    said = R.artifact_version(repo, at_p8(repo), "2.9.4", timeout=1)
    assert said["checked"] is False and said["agrees"] is None
    assert len(said["why"]) > 200, said["why"]
    assert "did not finish" in said["why"], said["why"]


def test_cut_where_is_recorded_relative_to_the_target(repo, tmp_path):
    """T127: 80-cut.json must carry `where` RELATIVE to the run root (item 120),
    resolving to the live tree via target/where."""
    from tools.sdlc import worktree as W
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@e", "-c",
                    "user.name=t", "commit", "-qm", "base"], check=True,
                   capture_output=True, timeout=120)
    run_id = at_p8(repo)
    W.create(repo, run_id)
    home = Path(next(iter(
        W.load_record(repo, run_id)["components"].values()))["home"])
    declare(repo, version_command="python -c \"print('1.0.0')\"")
    row = R.cut(repo, run_id)
    assert not Path(row["where"]).is_absolute(), row["where"]
    assert (repo / row["where"]).resolve() == home.resolve()
