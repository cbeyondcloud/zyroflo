"""The nightly replay: scheduled, and checkable that it is still running. Item 101.

Item 51 built the replay and its own docstring calls it *the nightly replay*.
Nothing made it nightly, and M5's kill gate asks for exactly that word.

The properties worth more than the rest of this file:

**A schedule that silently stopped and a schedule that finds no regressions
produce the same output.** That is item 97's quiet-gate class arriving at the
one check this project already describes as nightly, and it is why *nightly*
only means something if somebody can tell it stopped being true.

**Four answers, not a boolean** — `never`, `stale`, `unreadable`, `fresh` — and
three of them are not fine.

**The harness URL is read off the clone's own remote, never written down.** A
hardcoded URL would name one person's fork in every repository the installer
touches.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import corpus_replay as R  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"


def installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_replay", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc" / "metrics").mkdir(parents=True)
    return r


def stamp(seconds_ago: float, now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - seconds_ago))


def history(repo: Path, *rows: dict) -> None:
    path = repo / R.HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8", newline="\n")


DAY = 86400.0
NOW = 1_800_000_000.0            # fixed: this project does not estimate times


# ── four answers, and three of them are not fine ────────────────────────────


def test_a_replay_that_never_ran_is_NEVER_and_not_fresh(repo):
    """No history at all. The floors can still say the corpus is big enough,
    which is the sentence that makes this worth distinguishing."""
    found = R.freshness(repo, now=NOW)
    assert found["state"] == R.NEVER and found["age_days"] is None
    assert str(R.HISTORY) in found["why"] or R.HISTORY.name in found["why"]
    assert "whether ranking has got worse" in found["why"]


def test_a_recent_replay_is_FRESH(repo):
    history(repo, {"at": stamp(0.5 * DAY, NOW), "replayed": 3})
    found = R.freshness(repo, now=NOW)
    assert found["state"] == R.FRESH and found["why"] == ""
    assert 0.4 < found["age_days"] < 0.6


def test_a_replay_that_STOPPED_is_stale_and_says_how_long(repo):
    """**The whole item.** A nightly job nobody is running reports no
    regressions for exactly the reason a healthy one does."""
    history(repo, {"at": stamp(9 * DAY, NOW), "replayed": 3})
    found = R.freshness(repo, now=NOW)
    assert found["state"] == R.STALE
    assert 8.9 < found["age_days"] < 9.1
    assert "9.0 day(s) ago" in found["why"] and "limit is 2" in found["why"]


@pytest.mark.parametrize("days,expect", [
    (0.0, R.FRESH), (1.9, R.FRESH), (2.0, R.FRESH), (2.01, R.STALE),
    (30.0, R.STALE),
])
def test_the_boundary_is_at_the_declared_limit(repo, days, expect):
    """Two nights, not one: a single missed run is a runner hiccup, and a check
    that reddens on one is a check somebody switches off."""
    history(repo, {"at": stamp(days * DAY, NOW), "replayed": 3})
    assert R.freshness(repo, now=NOW)["state"] == expect


def test_the_limit_is_a_parameter_not_a_constant_nobody_can_move(repo):
    history(repo, {"at": stamp(5 * DAY, NOW), "replayed": 3})
    assert R.freshness(repo, max_age_days=10, now=NOW)["state"] == R.FRESH
    assert R.freshness(repo, max_age_days=1, now=NOW)["state"] == R.STALE


# ── could-not-look is never fresh ───────────────────────────────────────────


def test_a_history_with_holes_is_UNREADABLE_not_fresh(repo):
    """`history()` already refuses a file it cannot read whole, because a
    history with holes cannot say whether anything got worse. Freshness must
    inherit that rather than answering from the rows it happened to parse."""
    path = repo / R.HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": stamp(0.0, NOW)}) + "\n{not json\n",
                    encoding="utf-8")
    found = R.freshness(repo, now=NOW)
    assert found["state"] == R.UNREADABLE
    assert "unreadable row" in found["why"]


def test_a_timestamp_this_cannot_read_is_UNREADABLE_not_recent(repo):
    """Inventing a time for it would be the invention this module refuses
    everywhere else - and guessing 'now' would make a corrupt row look like
    tonight's run."""
    history(repo, {"at": "last Tuesday", "replayed": 3})
    found = R.freshness(repo, now=NOW)
    assert found["state"] == R.UNREADABLE and found["age_days"] is None
    assert "not a time this can read" in found["why"]


def test_rows_with_no_timestamp_at_all_read_as_never(repo):
    history(repo, {"replayed": 3}, {"replayed": 2})
    assert R.freshness(repo, now=NOW)["state"] == R.NEVER


@pytest.mark.parametrize("shape", ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S+00:00"])
def test_both_spellings_the_history_has_carried_are_read(repo, shape):
    """`replay()` writes `Z`; the rest of this package writes `+00:00`. A
    reader that knew only one would call half the histories unreadable."""
    history(repo, {"at": time.strftime(shape, time.gmtime(NOW - 3600))})
    assert R.freshness(repo, now=NOW)["state"] == R.FRESH


def test_the_NEWEST_row_decides_whatever_order_they_were_written(repo):
    """Append order is not time order once anything replays out of band."""
    history(repo,
            {"at": stamp(0.5 * DAY, NOW), "replayed": 3},
            {"at": stamp(40 * DAY, NOW), "replayed": 3})
    assert R.freshness(repo, now=NOW)["state"] == R.FRESH


def test_freshness_needs_no_corpus_at_all(repo):
    """The question is about the HISTORY. A repository whose corpus has gone
    missing is exactly one where somebody needs this answer."""
    history(repo, {"at": stamp(0.1 * DAY, NOW), "replayed": 3})
    assert not (repo / R.CORPUS).exists()
    assert R.freshness(repo, now=NOW)["state"] == R.FRESH


# ── the CLI ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rows,code", [
    ([], 1),                                              # never
    ([{"at": "2001-01-01T00:00:00Z"}], 1),                # stale
    ([{"at": "last Tuesday"}], 3),                        # could-not-look
])
def test_the_cli_exit_says_which_answer_it_gave(repo, rows, code, capsys):
    if rows:
        history(repo, *rows)
    assert R.main(["--target", str(repo), "--check-fresh"]) == code
    capsys.readouterr()


def test_a_fresh_replay_exits_zero(repo, capsys):
    history(repo, {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    assert R.main(["--target", str(repo), "--check-fresh"]) == 0
    assert "FRESH" in capsys.readouterr().out


# ── the installer writes the schedule, or says why not ──────────────────────


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True,
                   capture_output=True, timeout=120)


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    """A harness clone with a github remote, because the slug is READ off one."""
    h = tmp_path / "harness"
    h.mkdir()
    _git(h.parent, "init", "-q", str(h))
    _git(h, "remote", "add", "origin",
         "https://github.com/an-owner/a-harness.git")
    return h


@pytest.fixture
def target(tmp_path: Path) -> Path:
    t = tmp_path / "target"
    (t / ".github").mkdir(parents=True)
    return t


def test_the_harness_url_is_read_off_the_clone_not_written_down(harness):
    """A hardcoded URL would name one person's fork in every repository this
    installer touches, and would be wrong the first time somebody else ran it."""
    mod = installer()
    assert mod.harness_repo_slug(harness) == "an-owner/a-harness"
    _git(harness, "remote", "set-url", "origin",
         "git@github.com:someone-else/their-fork.git")
    assert mod.harness_repo_slug(harness) == "someone-else/their-fork"


def test_a_harness_with_no_github_remote_has_no_slug(tmp_path):
    bare = tmp_path / "nowhere"
    bare.mkdir()
    assert installer().harness_repo_slug(bare) == ""


@pytest.mark.parametrize("kind,fragment", [
    ("no-github", "has no .github/"),
    ("no-remote", "no repository to check the harness out FROM"),
    ("no-workspace", "needs a workspace file"),
])
def test_it_refuses_with_the_reason_rather_than_writing_a_job_that_fails_nightly(
        tmp_path, harness, target, kind, fragment):
    mod = installer()
    ws = target / "sdlc-workspace.toml"
    ws.write_text("root = '.'\n", encoding="utf-8")
    if kind == "no-github":
        bad = tmp_path / "nogh"
        bad.mkdir()
        reason = mod.no_replay_job_reason(bad, ws, harness)
    elif kind == "no-remote":
        blank = tmp_path / "blank"
        blank.mkdir()
        reason = mod.no_replay_job_reason(target, ws, blank)
    else:
        reason = mod.no_replay_job_reason(target, None, harness)
    assert reason and fragment in reason
    ops = mod.replay_job_ops(
        target if kind != "no-github" else tmp_path / "nogh",
        ws if kind != "no-workspace" else None,
        harness if kind != "no-remote" else tmp_path / "blank")
    assert [o["status"] for o in ops] == ["blocked"]
    assert ops[0]["content"] is None


def test_everything_present_writes_the_workflow_with_no_placeholder_left(
        tmp_path, harness, target):
    mod = installer()
    ws = target / "sdlc-workspace.toml"
    ws.write_text("root = '.'\n", encoding="utf-8")
    assert mod.no_replay_job_reason(target, ws, harness) is None
    ops = mod.replay_job_ops(target, ws, harness)
    assert len(ops) == 1 and ops[0]["rel"] == mod.REPLAY_WORKFLOW_REL
    body = ops[0]["content"]
    assert "an-owner/a-harness" in body
    assert "@@" not in body, "a placeholder survived into the written workflow"


def rendered_workspace(body: str) -> str:
    """The `WORKSPACE:` value the runner will actually be handed.

    Read off the written workflow, not off the installer, so this test cannot
    agree with the code by construction.
    """
    hits = re.findall(r"^\s*WORKSPACE:\s*'(.*)'\s*$", body, flags=re.M)
    assert len(hits) == 1, f"expected one WORKSPACE: line, found {hits!r}"
    return hits[0]


@pytest.mark.parametrize("layout", [
    "beside-the-repo", "at-the-repo-root", "in-a-subdirectory",
])
def test_the_rendered_workspace_resolves_inside_the_checkout_the_job_performs(
        tmp_path, harness, target, layout):
    """Substitution having RUN is not the substituted value being usable.

    The job checks out exactly two trees — this repository at `$GITHUB_WORKSPACE`
    and the harness beneath it at `.sdlc-harness-ci`. So a `WORKSPACE:` naming
    anything above the repository root names a file no runner can open, at 03:17
    every night, forever. A recurring red is how a real check gets switched off,
    which is this item's own stated reason for the guard.

    The test above it asserted only that no `@@` placeholder survived — which its
    own fixture satisfied while rendering `../workspace.toml`.

    The contract is a disjunction and both halves are asserted: either the
    installer REFUSES with a reason, or the value it rendered resolves to a file
    under `root`. Resolution is checked with `pathlib` against the tree on disk,
    never by re-asking the installer's own predicate.
    """
    mod = installer()
    ws = {"beside-the-repo": tmp_path / "workspace.toml",
          "at-the-repo-root": target / "sdlc-workspace.toml",
          "in-a-subdirectory": target / ".sdlc" / "sdlc-workspace.toml"}[layout]
    ws.parent.mkdir(parents=True, exist_ok=True)
    ws.write_text("root = '.'\n", encoding="utf-8")

    ops = mod.replay_job_ops(target, ws, harness)
    assert len(ops) == 1
    reason = mod.no_replay_job_reason(target, ws, harness)

    if ops[0]["status"] == "blocked":
        assert ops[0]["content"] is None
        assert reason and ws.name in reason, (
            "a refusal that does not name the file it refused sends its reader "
            f"looking: {reason!r}")
        return

    assert reason is None, "an op was written while the guard reported a reason"
    rel = rendered_workspace(ops[0]["content"])
    resolved = (target / rel).resolve()
    assert resolved.is_file(), (
        f"{rel!r} names nothing the checkout contains")
    assert target.resolve() in resolved.parents, (
        f"{rel!r} resolves outside the repository the job checks out")


def test_the_workflow_is_scheduled_and_hand_startable(tmp_path, harness, target):
    ws = target / "sdlc-workspace.toml"
    ws.write_text("root = '.'\n", encoding="utf-8")
    body = installer().replay_job_ops(target, ws, harness)[0]["content"]
    assert "schedule:" in body and "cron:" in body
    assert "workflow_dispatch:" in body, "a schedule nobody can trigger by hand"
    assert "--check-fresh" in body, "the job must also report its own freshness"


def test_the_workflow_passes_untrusted_values_through_env_not_into_a_shell_line():
    """Item 43(f)'s rule, and item 47 met it again: a value interpolated into a
    command is how attacker-chosen text becomes an executed string."""
    body = (ROOT / "sdlc_templates" / "corpus-replay.yml").read_text(
        encoding="utf-8")
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not (stripped.startswith("run:") and "${{" in stripped), line
    # And what it does substitute is substituted by the INSTALLER, before the
    # file is written, so no expression reaches the runner at all.
    assert "${{" not in body.replace("${{ github", "")


def test_nothing_is_written_unless_the_flag_is_given():
    """A scheduled job on a repository with no corpus goes red every night, and
    a recurring red is how a real check gets switched off - item 44's reason for
    adding no job to a repository with nothing to check."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "if corpus_replay:" in text
    assert '"--corpus-replay"' in text


# ── freshness has a reader ──────────────────────────────────────────────────


def test_metrics_reports_the_replay_age(repo):
    """A check with a CLI and no consumer is the shape this project keeps
    finding: askable, and asked by nothing."""
    from tools.sdlc import metrics as M

    doc = M.corpus(repo)
    assert doc["replay_age_days"]["value"] is None
    assert "no replay has ever been recorded" in doc["replay_age_days"]["why"]

    history(repo, {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    assert M.corpus(repo)["replay_age_days"]["value"] is not None


def test_an_unmeasurable_replay_age_is_never_zero(repo):
    """The document's own rule, and the sharpest case of it: a stopped nightly
    replay reports no regressions for exactly the reason a healthy one does."""
    from tools.sdlc import metrics as M

    doc = M.compute(repo)
    assert doc["corpus"]["replay_age_days"]["value"] is None
    assert any(line.startswith("corpus.replay_age_days")
               for line in doc["unmeasured"])


# ── T312: the refusal's remedy must not silently cost a relative `root` ──────
#
# T302 refuses a cadence job whose workspace file sits outside the checkout and
# tells the adopter to move it under the repository root. But `root` inside the
# file is resolved RELATIVE TO THE WORKSPACE FILE (`read_workspace`:
# `root = (path.parent / raw_root).resolve()`), so a bare move silently re-points
# it. MEASURED: with the file beside the repo and `root = '.'`, `root` resolves
# to the directory that holds the repositories; moving the file under the repo
# with `root` untouched re-points it at the one repo, and the refusal then
# CLEARS — a success signal handed to an adopter whose workspace has silently
# narrowed, and an empty workspace reads exactly like a clean one. The refusal is
# correct and is NOT weakened here (its condition is untouched); the remedy it
# names is what changes.


def _root_of(mod, ws_path: Path) -> Path:
    """Where `root` resolves for this workspace file, read off the installer's
    own parser — the one thing that decides the category `root`."""
    return mod.read_workspace(ws_path)["root"]


@pytest.mark.parametrize("fn", ["no_replay_job_reason", "no_snapshot_job_reason"])
def test_the_refusal_warns_that_a_bare_move_re_bases_a_relative_root(
        tmp_path, harness, target, fn):
    """RED before the fix: the refusal names 'move the file' with no word about
    `root` being resolved relative to it, so an adopter who follows it loses
    their root. Asserted for BOTH cadence jobs, because they share one refusal
    predicate and a fix to one is not a fix to the other."""
    mod = installer()
    outside = tmp_path / "sdlc-workspace.toml"          # beside the repo
    outside.write_text("root = '.'\n", encoding="utf-8")

    reason = getattr(mod, fn)(target, outside, harness)
    assert reason, "T302 must still refuse a workspace file outside the checkout"
    assert outside.name in reason, "the refusal must still name the file (T302)"
    assert "relative to" in reason, (
        "the remedy must warn that `root` is resolved relative to the workspace "
        f"file, or an adopter who moves it silently re-bases `root`: {reason!r}")


def test_following_the_remedy_keeps_root_and_clears_the_refusal(
        tmp_path, harness, target):
    """END TO END. The adopter's `root` is the repository the job checks out (the
    layout the cadence job supports), declared from a file beside it. The refusal
    fires. Following the remedy the refusal now names — put the file at the top of
    {root} and set `root = '.'` — leaves `root` pointing at the SAME directory and
    clears the refusal. Resolution is read off disk, never by re-asking the
    installer to agree with itself."""
    mod = installer()
    beside = tmp_path / "sdlc-workspace.toml"
    beside.write_text(f"root = {target.name!r}\n", encoding="utf-8")   # -> target
    before = _root_of(mod, beside)
    assert before == target.resolve()
    assert mod.no_replay_job_reason(target, beside, harness), "refusal must fire"

    inside = target / "sdlc-workspace.toml"             # file at the top of {root}
    inside.write_text("root = '.'\n", encoding="utf-8")  # root still names {root}
    assert _root_of(mod, inside) == before, "root must still point at the same dir"
    assert mod.no_replay_job_reason(target, inside, harness) is None


def test_the_bare_move_the_old_remedy_named_silently_re_bases(
        tmp_path, harness, target):
    """The MEASURED consequence, kept as a control: doing only what the old
    remedy said — move the file under {root}, `root` untouched — re-points `root`
    and clears the refusal with no error. This is what the new wording exists to
    stop."""
    mod = installer()
    beside = tmp_path / "sdlc-workspace.toml"
    beside.write_text("root = '.'\n", encoding="utf-8")
    before = _root_of(mod, beside)                       # the dir holding the repos
    assert before == tmp_path.resolve()

    moved = target / "sdlc-workspace.toml"               # under {root}, root='.'
    moved.write_text("root = '.'\n", encoding="utf-8")
    after = _root_of(mod, moved)
    assert after == target.resolve() and after != before   # silently re-based
    assert mod.no_replay_job_reason(target, moved, harness) is None  # no signal


def test_a_person_running_the_installer_reads_the_root_caveat(tmp_path, target):
    """Item 96: the refusal's contract is a PROCESS. Spawn the real installer in
    --dry-run against a workspace file outside the checkout and assert the caveat
    reaches stdout, where a person reads it. The harness is this clone (its slug
    is read off its own origin), so the workspace-location refusal is the branch
    that fires. --dry-run writes nothing."""
    (tmp_path / "comp-a" / ".git").mkdir(parents=True)   # a discoverable component
    (target / ".git").mkdir()                            # target is a repo
    outside = tmp_path / "sdlc-workspace.toml"
    outside.write_text("root = '.'\n", encoding="utf-8")  # -> tmp_path, outside target
    proc = subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(target),
         "--workspace", str(outside), "--corpus-replay", "--no-graph", "--dry-run"],
        capture_output=True, text=True, cwd=str(ROOT))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "was not written" in out and "sdlc-corpus-replay.yml" in out, out
    assert "relative to" in out, (
        "a person following the printed remedy must be warned `root` is resolved "
        f"relative to the file:\n{out}")
