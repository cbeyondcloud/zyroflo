"""Item 36 — the self-review loop's exit condition, computed rather than described.

The loop was correct prose and no code, and three things were wrong the moment
anything evaluated it. The pre-pass speaks rdjson (`ERROR`) and `block_on`
speaks `critical, high`, so the categorical exit could not see a single machine
finding — a leaked credential exited clean. A finding could be *deferred* past
the gate. And the cap could stop the loop silently, dropping what was still
open.

The first of those is the one worth the most tests, because it fails in the
direction that looks like success.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.review import hitl as H  # noqa: E402
from tools.review import loop as LP  # noqa: E402
from tools.sdlc import state as L  # noqa: E402


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "gate_profile: solo\nreview:\n  block_on: [critical, high]\n"
        "  max_iterations: 3\n", encoding="utf-8")
    return r


@pytest.fixture
def run(repo: Path) -> str:
    return L.create(repo, "requirement", "a change worth reviewing")["run_id"]


def prepass_report(tool: str, severity: str, rule: str = "generic-api-key") -> dict:
    return {"schema_version": 1, "scope": {"mode": "diff", "files": ["a.py"]},
            "tools": [{"name": tool, "status": "ok", "detail": "", "findings": 1}],
            "sources": [{"source": {"name": tool}, "diagnostics": [{
                "severity": severity, "message": "a finding",
                "code": {"value": rule},
                "location": {"path": "a.py", "range": {"start": {"line": 4}}}}]}],
            "summary": {"total": 1, "by_severity": {severity: 1}}}


def agent_finding(**over) -> dict:
    row = {"id": "F1", "source": "agent", "disposition": "accepted",
           "severity": "high", "path": "a.py", "line": 1}
    row.update(over)
    return row


def session(target: Path, *rows: dict) -> None:
    _, pending = H.paths(target)
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({"schema_version": 1, "started_at": "now",
                                   "findings": list(rows)}), encoding="utf-8")


# ── the bridge that did not exist ────────────────────────────────────────────


def test_a_leaked_credential_is_seen_by_a_block_on_written_in_severities():
    """The headline defect. `block_on: [critical, high]` compared against a set
    of rdjson `ERROR`s matches nothing, so the loop would have exited clean over
    a secret. This is that comparison, and it must now hold the loop open."""
    findings = LP.from_prepass(prepass_report("gitleaks", "ERROR"))
    verdict = LP.assess(findings, ["critical", "high"])
    assert not LP.may_exit(verdict)
    assert findings[0]["severity"] == "critical"


def test_a_tool_that_labels_everything_error_does_not_make_everything_critical():
    """Only the tool whose every finding IS a credential gets the floor. A lint
    rule firing at ERROR is a high, and calling it critical would make the
    severity meaningless in the other direction."""
    assert LP.from_prepass(prepass_report("ast-grep", "ERROR"))[0]["severity"] == "high"
    assert LP.from_prepass(prepass_report("ast-grep", "WARNING"))[0]["severity"] == "medium"
    assert LP.from_prepass(prepass_report("ast-grep", "INFO"))[0]["severity"] == "low"


def test_a_severity_no_rule_maps_is_a_refusal_and_never_a_pass():
    with pytest.raises(LP.LoopError, match="cannot say whether it is a blocker"):
        LP.from_prepass(prepass_report("ast-grep", "NOTE"))


def test_a_blank_severity_from_a_tool_is_refused_too():
    with pytest.raises(LP.LoopError, match="blank"):
        LP.from_prepass(prepass_report("ast-grep", ""))


def test_the_map_covers_every_severity_the_prepass_can_emit():
    """Read off the pre-pass rather than remembered: gitleaks hardcodes one and
    ast-grep uppercases its rule's own, and both must land in the table."""
    src = (ROOT / "tools" / "review" / "prepass.py").read_text(encoding="utf-8")
    assert '"severity": "ERROR"' in src
    for level in ("ERROR", "WARNING", "INFO"):
        assert level in LP.RDJSON_SEVERITY


def test_every_mapped_severity_is_one_block_on_can_name():
    assert set(LP.RDJSON_SEVERITY.values()) <= set(LP.SEVERITIES)
    assert set(LP.TOOL_SEVERITY.values()) <= set(LP.SEVERITIES)


# ── what holds the loop open ─────────────────────────────────────────────────


def test_a_finding_below_the_line_does_not_hold_the_loop():
    verdict = LP.assess([agent_finding(severity="low")], ["critical", "high"])
    assert LP.may_exit(verdict)


def test_a_rejected_finding_closes_because_somebody_judged_it_not_real():
    verdict = LP.assess([agent_finding(disposition="rejected")], ["critical", "high"])
    assert LP.may_exit(verdict)


def test_a_block_on_severity_cannot_be_deferred_out_of_the_gate():
    """Deferral is the shape of a hole at critical: agree it is real, agree not
    to fix it, exit. Honest below the line, refused at or above it."""
    verdict = LP.assess([agent_finding(severity="critical", disposition="deferred")],
                        ["critical", "high"])
    assert not LP.may_exit(verdict)
    assert "cannot be deferred" in " ".join(LP.reasons(verdict))
    assert LP.may_exit(LP.assess([agent_finding(severity="low",
                                                disposition="deferred")],
                                 ["critical", "high"]))


def test_an_ungraded_finding_holds_the_loop_and_says_why():
    """`hitl.py` accepts a blank severity to keep recording cheap, and
    cheap-to-record is how an ungraded critical gets past a severity gate."""
    verdict = LP.assess([agent_finding(severity="")], ["critical", "high"])
    assert not LP.may_exit(verdict)
    assert "carries no severity" in " ".join(LP.reasons(verdict))


def test_an_empty_block_on_is_refused_here_too():
    """Item 35 refuses it in a profile. It is refused here in case it arrived
    some other way — a control with no way to fire is not a faster review."""
    with pytest.raises(LP.LoopError, match="not a faster review"):
        LP.assess([], [])


def test_a_block_on_naming_something_that_is_not_a_severity_is_refused():
    with pytest.raises(LP.LoopError, match="not a severity"):
        LP.assess([], ["critical", "blocker"])


def test_both_sources_land_in_one_verdict(repo, run):
    session(repo, agent_finding(severity="medium"))
    findings = LP.from_prepass(prepass_report("gitleaks", "ERROR")) + \
        LP.from_session(H.load_pending(repo))
    verdict = LP.assess(findings, ["critical", "high"])
    assert verdict["counts"] == {"critical": 1, "medium": 1}
    assert len(verdict["open"]) == 1 and verdict["open"][0]["source"] == "prepass"


# ── the score is reported and never read ─────────────────────────────────────


def test_the_gate_cannot_see_a_score_at_all():
    """Enforced by shape, not by intention: a function that does not take the
    score cannot be influenced by it, however the caller is written."""
    import inspect
    assert "score" not in inspect.signature(LP.assess).parameters
    assert "score" not in inspect.signature(LP.may_exit).parameters


@pytest.mark.parametrize("score", [0.0, 5.0, 9.5, 10.0])
def test_the_score_is_recorded_and_changes_no_verdict(repo, run, score):
    session(repo, agent_finding(severity="critical"))
    result = LP.iterate(repo, run, LP.from_session(H.load_pending(repo)), score)
    assert result["outcome"] == "open"
    assert result["record"]["iterations"][-1]["score_reported"] == score


def test_a_perfect_score_with_a_critical_open_still_does_not_exit(repo, run):
    session(repo, agent_finding(severity="critical"))
    result = LP.iterate(repo, run, LP.from_session(H.load_pending(repo)), 10.0)
    assert LP.exit_code(result["outcome"]) == 1


# ── the regime decides what blocks, not only the profile ─────────────────────


def test_a_regulated_run_blocks_on_medium_the_profile_never_listed(repo):
    (repo / ".sdlc" / "profile.yml").write_text(
        "gate_profile: regulated\nreview:\n  block_on: [critical, high]\n",
        encoding="utf-8")
    assert "medium" in LP.block_on_of(repo)
    assert not LP.may_exit(LP.assess([agent_finding(severity="medium")],
                                     LP.block_on_of(repo)))


def test_a_solo_run_does_not_block_on_medium(repo):
    assert LP.block_on_of(repo) == ["critical", "high"]


# ── the cap ──────────────────────────────────────────────────────────────────


def test_the_iteration_number_is_counted_from_the_record_not_the_caller(repo, run):
    session(repo, agent_finding(severity="critical"))
    findings = LP.from_session(H.load_pending(repo))
    for expected in (1, 2, 3):
        assert LP.iterate(repo, run, findings)["iteration"] == expected


def test_the_cap_stops_the_loop_and_names_what_survived(repo, run):
    session(repo, agent_finding(severity="critical"))
    findings = LP.from_session(H.load_pending(repo))
    outcomes = [LP.iterate(repo, run, findings)["outcome"] for _ in range(3)]
    assert outcomes == ["open", "open", "capped"]
    record = LP.load_record(repo, run)
    assert record["capped"] is True
    assert record["iterations"][-1]["open"][0]["id"] == "F1"


def test_capped_is_its_own_exit_code_and_not_a_worse_one(repo, run):
    """3 is not "keep going" and it is not "clean". A caller that reads it as
    the first loops forever; as the second, it drops the findings."""
    assert (LP.exit_code("clean"), LP.exit_code("open"), LP.exit_code("capped")) \
        == (0, 1, 3)


def test_a_loop_that_clears_before_the_cap_is_clean(repo, run):
    session(repo, agent_finding(severity="critical"))
    LP.iterate(repo, run, LP.from_session(H.load_pending(repo)))
    session(repo, agent_finding(severity="critical", disposition="rejected"))
    result = LP.iterate(repo, run, LP.from_session(H.load_pending(repo)))
    assert result["outcome"] == "clean" and result["record"]["capped"] is False


def test_a_cap_below_one_iteration_is_refused(repo):
    (repo / ".sdlc" / "profile.yml").write_text(
        "review:\n  max_iterations: 0\n", encoding="utf-8")
    with pytest.raises(LP.LoopError, match="never reviews anything"):
        LP.cap_of(repo)


def test_an_absent_cap_is_the_specs_five(repo):
    (repo / ".sdlc" / "profile.yml").write_text("gate_profile: solo\n",
                                                encoding="utf-8")
    assert LP.cap_of(repo) == LP.DEFAULT_CAP == 5
    spec = (ROOT / "sdlc" / "LIFECYCLE.md").read_text(encoding="utf-8")
    assert "max_iterations" in spec


def test_a_corrupt_loop_record_is_a_refusal_not_a_fresh_start(repo, run):
    """A loop whose record cannot be read has no iteration count, and starting
    over at one is how a cap is escaped."""
    LP.record_path(repo, run).write_text("{not json", encoding="utf-8")
    with pytest.raises(LP.LoopError, match="not readable JSON"):
        LP.load_record(repo, run)


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_exit_codes_are_the_loops_answer(repo, run, tmp_path, capsys):
    report = tmp_path / "prepass.json"
    report.write_text(json.dumps(prepass_report("gitleaks", "ERROR")),
                      encoding="utf-8")
    assert LP.main(["--target", str(repo), "--run", run, "--prepass", str(report),
                    "--iterate"]) == 1
    out = capsys.readouterr().out
    assert "generic-api-key" in out and "critical" in out

    report.write_text(json.dumps({"schema_version": 1, "scope": {}, "tools": [],
                                  "sources": [], "summary": {"total": 0}}),
                      encoding="utf-8")
    assert LP.main(["--target", str(repo), "--run", run, "--prepass", str(report),
                    "--iterate"]) == 0
    assert "may exit" in capsys.readouterr().out


def test_cli_refuses_a_missing_prepass_rather_than_reviewing_without_it(repo, run,
                                                                        capsys):
    assert LP.main(["--target", str(repo), "--run", run,
                    "--prepass", str(repo / "nope.json"), "--iterate"]) == 2
    assert "not a reviewed change" in capsys.readouterr().out


def test_cli_show_reports_the_record_without_advancing_it(repo, run, capsys):
    session(repo, agent_finding(severity="critical"))
    LP.main(["--target", str(repo), "--run", run, "--iterate"])
    capsys.readouterr()
    assert LP.main(["--target", str(repo), "--run", run, "--show"]) == 0
    assert "1 iteration(s)" in capsys.readouterr().out
    assert len(LP.load_record(repo, run)["iterations"]) == 1


def test_a_rejected_finding_closes_even_when_nobody_graded_it():
    """Found reviewing this module before pushing it: severity was judged before
    disposition, so a finding somebody declared NOT REAL but recorded without a
    severity held the loop open — and it could never be cleared, because you
    cannot grade a non-finding."""
    verdict = LP.assess([agent_finding(severity="", disposition="rejected")],
                        ["critical", "high"])
    assert LP.may_exit(verdict) and verdict["ungraded"] == []


def test_every_open_row_says_why_it_is_open():
    findings = [agent_finding(id="A", severity=""),
                agent_finding(id="B", severity="critical", disposition="deferred"),
                agent_finding(id="C", severity="high")]
    verdict = LP.assess(findings, ["critical", "high"])
    assert [f["why"] for f in verdict["open"]] == \
        ["ungraded", "deferred-blocker", "open"]


def test_two_identical_findings_are_each_explained_on_their_own_terms():
    """`why` is carried on the row rather than recovered by matching, so a
    duplicate cannot be described by its twin's category."""
    twin = agent_finding(id="DUP", severity="high")
    verdict = LP.assess([twin, dict(twin)], ["critical", "high"])
    assert len(LP.reasons(verdict)) == 2
    assert all("is open" in r for r in LP.reasons(verdict))


def test_the_read_only_view_says_the_loop_is_clear_when_it_is(repo, run, capsys):
    """The clean message was unreachable: it sat behind an `or` on a list that
    always had the block_on line in it, so the branch could never be taken."""
    assert LP.main(["--target", str(repo), "--run", run]) == 0
    out = capsys.readouterr().out
    assert "no block_on severity is open" in out
    session(repo, agent_finding(severity="critical"))
    assert LP.main(["--target", str(repo), "--run", run]) == 1
    assert "F1 [critical]" in capsys.readouterr().out
    assert len(LP.load_record(repo, run)["iterations"]) == 0, \
        "the read-only view advanced the loop"
