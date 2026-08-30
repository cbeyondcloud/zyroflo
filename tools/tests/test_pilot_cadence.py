"""The pilot plan and the weekly snapshot cadence. Item 58, M6 condition C7.

C7 asks for pilot KPIs **defined** and the maintainer cadence **documented**. A
plan is prose, so most of it cannot be tested — what CAN be is that the document
names a target rather than an aspiration, that every command it tells a maintainer
to run actually exists with the flags it cites, and that the one executable part
behaves like item 101's job rather than being a second answer to the same question.

**The currency guard is the point.** `test_workflows.py` exists because a runbook
citing a flag no CLI has is worse than no runbook: it reads as authoritative and
sends somebody to a command that errors. This file applies that rule to the pilot
plan.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "pilot-plan.md"
TEMPLATE = ROOT / "sdlc_templates" / "snapshot-publish.yml"
INSTALLER = ROOT / "sdlc-init.py"


def installer():
    spec = importlib.util.spec_from_file_location("sdlc_init_pilot", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SI = installer()
BODY = PLAN.read_text(encoding="utf-8")


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True,
                   capture_output=True, timeout=120)


# ── the KPI is defined, which means it has a number ─────────────────────────


def test_the_headline_kpi_names_a_target_and_a_window():
    """**A KPI with no target is not defined**, which is what C7 asks for. The
    same shape as a gate with no ruling: a number nobody can miss."""
    assert re.search(r"\bfive runs reach close-out in eight weeks\b", BODY, re.I), \
        "the plan states no target and window"


def test_the_kpi_is_computable_from_what_metrics_actually_holds():
    """A KPI nobody can compute is worse than none: it looks like a commitment and
    is an aspiration. `runs.closed` is a real key of the real document."""
    from tools.sdlc import metrics as M

    doc = M.compute(ROOT)                       # this repo has no runs; shape only
    assert "closed" in doc["runs"], "the KPI names a key metrics.json does not have"
    assert "runs.closed" in BODY or "`runs.closed`" in BODY


def test_the_kpi_excludes_runs_the_harness_opened_itself():
    """`debt.py --file` and P10's auto-intake open runs. Counting them as adoption
    would let the tool inflate its own score.

    Asserted on the RULE, not on the words `person`, `debt` and `probe` appearing
    somewhere — the first cut checked exactly that and a mutation retitling the
    section walked straight through it, because all three words occur elsewhere in
    the document.
    """
    # Whitespace-tolerant because the phrase wraps: the document is prose and a
    # regex that assumed one line would fail on a reflow rather than on a change
    # of meaning.
    assert re.search(
        r"source\s+kind\s+is\s+not\s+`?debt`?\s+or\s+`?probe`?", BODY, re.I), \
        "the plan does not state which runs the KPI excludes, or how to tell"
    # And the two source kinds it names must be the ones the tools actually write.
    from tools.sdlc import debt, verify

    assert debt.file_one.__doc__ and verify.file_defect.__doc__
    assert '"debt"' in (ROOT / "tools" / "sdlc" / "debt.py").read_text(
        encoding="utf-8"), "debt.py no longer files with src_kind debt"
    assert '"probe"' in (ROOT / "tools" / "sdlc" / "verify.py").read_text(
        encoding="utf-8"), "verify.py no longer files with src_kind probe"


def test_a_missed_kpi_has_a_stated_consequence():
    """A number with no consequence is one nobody acts on."""
    assert "If the KPI is missed" in BODY
    assert "recalibration" in BODY, \
        "extending the window must cost what every other recalibration costs"


def test_review_duration_is_reported_and_never_the_gate():
    """**M4 ruled on this.** A time cap on human review has the incentive
    backwards: G3 exists so every agent change is looked at properly, and gating
    on speed rewards the rubber stamp the gate exists to prevent. A pilot plan
    that graded it would overturn a ruling already on the record."""
    assert "median_duration_s" in BODY
    assert "incentive backwards" in BODY


# ── every command the runbook cites exists, with the flags it names ─────────


CITED = re.findall(r"^\s*((?:build|third_party|corpus_replay|metrics)\.py[^\n]*)",
                   BODY, re.M)


def test_the_runbook_cites_at_least_one_command():
    """The guard's own guard: a regex that matched nothing would make every
    assertion below vacuous."""
    assert len(CITED) >= 4, f"only found {CITED}"


@pytest.mark.parametrize("line", CITED)
def test_every_flag_the_runbook_cites_exists_in_the_shipped_cli(line):
    """**A runbook citing a flag no CLI has is worse than no runbook**: it reads
    as authoritative and sends somebody to a command that errors. `test_workflows`
    makes this rule for the SOPs; the pilot plan is held to it too."""
    script = line.split()[0]
    where = {"build.py": ROOT / "tools" / "graph" / "build.py",
             "third_party.py": ROOT / "tools" / "third_party.py",
             "corpus_replay.py": ROOT / "tools" / "corpus_replay.py",
             "metrics.py": ROOT / "tools" / "sdlc" / "metrics.py"}[script]
    source = where.read_text(encoding="utf-8")
    for flag in re.findall(r"(--[a-z][a-z-]+)", line):
        assert f'"{flag}"' in source, f"{script} has no {flag}"


def test_the_drift_step_names_both_causes_and_how_to_tell_them_apart():
    """`--check-snapshot` cannot say WHY bytes moved, and the two causes have
    opposite remedies. Re-publishing a drift nobody diagnosed overwrites the
    evidence of an extractor regression with the remedy for a stale baseline."""
    assert "stale_repos" in BODY
    assert "extractor regression" in BODY
    assert "--check-snapshot" in BODY


# ── the one executable part ─────────────────────────────────────────────────


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    h = tmp_path / "harness"
    h.mkdir()
    _git(h.parent, "init", "-q", str(h))
    _git(h, "remote", "add", "origin", "https://github.com/an-owner/a-harness.git")
    return h


@pytest.fixture
def target(tmp_path: Path) -> Path:
    t = tmp_path / "target"
    (t / ".github").mkdir(parents=True)
    return t


@pytest.mark.parametrize("kind,fragment", [
    ("no-github", "has no .github/"),
    ("no-remote", "no repository to check the harness out FROM"),
    ("no-workspace", "needs a workspace file"),
])
def test_it_refuses_with_the_reason_rather_than_a_job_that_fails_weekly(
        tmp_path, harness, target, kind, fragment):
    ws = tmp_path / "workspace.toml"
    ws.write_text("root = '.'\n", encoding="utf-8")
    bad = tmp_path / "nogh"
    bad.mkdir(exist_ok=True)
    blank = tmp_path / "blank"
    blank.mkdir(exist_ok=True)

    args = {"no-github": (bad, ws, harness),
            "no-remote": (target, ws, blank),
            "no-workspace": (target, None, harness)}[kind]
    reason = SI.no_snapshot_job_reason(*args)
    assert reason and fragment in reason
    ops = SI.snapshot_job_ops(*args)
    assert [o["status"] for o in ops] == ["blocked"] and ops[0]["content"] is None


def test_everything_present_writes_the_workflow_with_no_placeholder_left(
        tmp_path, harness, target):
    ws = tmp_path / "workspace.toml"
    ws.write_text("root = '.'\n", encoding="utf-8")
    assert SI.no_snapshot_job_reason(target, ws, harness) is None
    ops = SI.snapshot_job_ops(target, ws, harness)
    assert len(ops) == 1 and ops[0]["rel"] == SI.SNAPSHOT_WORKFLOW_REL
    body = ops[0]["content"]
    assert "an-owner/a-harness" in body
    assert "@@" not in body, "a placeholder survived into the written workflow"


def test_nothing_is_written_unless_the_flag_is_given():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "if snapshot_cadence:" in text
    assert '"--snapshot-cadence"' in text


def test_the_job_publishes_and_does_NOT_check_drift():
    """The two are different questions and the second has two opposite causes.
    A job that did both would re-publish a drift nobody diagnosed."""
    tpl = TEMPLATE.read_text(encoding="utf-8")
    assert "--publish-snapshot" in tpl
    runs = [ln for ln in tpl.splitlines()
            if "--check-snapshot" in ln and not ln.strip().startswith("#")]
    assert not runs, "the scheduled job runs the drift check it must leave to a human"


def test_the_job_commits_only_when_the_shards_moved():
    """`--publish-snapshot` is deterministic, so an unchanged week must produce no
    commit - a weekly commit that says nothing is how a log stops being read."""
    tpl = TEMPLATE.read_text(encoding="utf-8")
    assert "git diff --cached --quiet" in tpl


def test_the_job_passes_untrusted_values_through_env_not_a_shell_line():
    """Item 43(f)'s rule: a value interpolated into a command is how
    attacker-chosen text becomes an executed string."""
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        assert not (s.startswith("run:") and "${{" in s), line


def test_the_two_scheduled_jobs_do_not_share_a_concurrency_group():
    """They publish to the same repository and would serialise behind each other
    for no reason - and worse, a cancelled one would look like a job that ran."""
    replay = (ROOT / "sdlc_templates" / "corpus-replay.yml").read_text(encoding="utf-8")
    snap = TEMPLATE.read_text(encoding="utf-8")

    def group(text: str) -> str:
        m = re.search(r"^\s*group:\s*(\S+)", text, re.M)
        return m.group(1) if m else ""

    assert group(replay) and group(snap) and group(replay) != group(snap)
