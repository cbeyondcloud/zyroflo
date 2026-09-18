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
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.review import hitl as H  # noqa: E402
from tools.review import loop as LP  # noqa: E402
from tools.review import prepass as PP  # noqa: E402

LOOP_CLI = Path(LP.__file__)
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
    gates = [{"name": g, "status": "ok", "detail": "",
              "findings": 1 if g == tool else 0} for g in PP.GATES]
    if tool not in PP.GATES:
        gates.append({"name": tool, "status": "ok", "detail": "", "findings": 1})
    return {"schema_version": 1, "scope": {"mode": "diff", "files": ["a.py"]},
            "tools": gates,
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

    clean = prepass_report("gitleaks", "ERROR")
    clean["tools"] = [{"name": g, "status": "ok", "detail": "", "findings": 0}
                      for g in PP.GATES]
    clean["sources"], clean["summary"] = [], {"total": 0}
    report.write_text(json.dumps(clean), encoding="utf-8")
    assert LP.main(["--target", str(repo), "--run", run, "--prepass", str(report),
                    "--iterate"]) == 0
    assert "may exit" in capsys.readouterr().out

    # ...and a report in which a gate never fired is NOT a clean exit. The
    # previous version of this test asserted the opposite with `"tools": []`.
    unfired = dict(clean)
    unfired["tools"] = [{"name": "gitleaks", "status": "not-run",
                         "detail": "vendored binary missing", "findings": 0},
                        {"name": "ast-grep", "status": "ok", "detail": "",
                         "findings": 0}]
    report.write_text(json.dumps(unfired), encoding="utf-8")
    assert LP.main(["--target", str(repo), "--run", run, "--prepass", str(report),
                    "--iterate"]) == 3
    assert "did not fire" in capsys.readouterr().out


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


# ── the class sweep ──────────────────────────────────────────────────────────
#
# `from_prepass` reads a document a RUNNER produced, and `suite.py` hands it
# the SCA result as well - so it reads two documents this module does not
# write. A present-but-null key is not a missing key, so `.get(k, [])` returns
# the null and the default never fires. Item 40 met this in the field when a
# clean osv-scanner scan emitted `"results": null`.


def test_a_sources_field_that_is_null_reads_as_nothing_found():
    """Null is the measured producer convention, not damage: item 40 met a
    clean osv-scanner scan emitting `"results": null` in the field."""
    assert LP.from_prepass({"schema_version": 1, "sources": None}) == []
    assert LP.from_prepass({"schema_version": 1}) == []


@pytest.mark.parametrize("sources", ["not a list", 7, {"a": 1}])
def test_a_sources_field_of_the_wrong_type_is_refused_not_read_as_empty(sources):
    """This test asserted `== []` and that ruling was WRONG - mine, from the
    pass that introduced `_list`.

    Coercing anything non-list to `[]` stopped the crash and put a silent one
    in its place: a report whose `sources` is a string yielded NO findings and
    the loop exited CLEAN, while a malformed ENTRY one line below refused by
    name. One function, two answers to the same damage.

    The test's original property still holds and is asserted below - a refusal
    is not a traceback. `LoopError` is caught at the CLI boundary and printed,
    and the loop exits 2 rather than dying."""
    doc = {"schema_version": 1, "sources": sources}
    with pytest.raises(LP.LoopError) as exc:
        LP.from_prepass(doc)
    assert "not a list" in str(exc.value)


@pytest.mark.parametrize("diagnostics", ["not a list", 7, {"a": 1}])
def test_a_diagnostics_field_of_the_wrong_type_is_refused_too(diagnostics):
    """The same ruling one level down, and a mutation pass is the only reason
    it is here: the `sources` check was tested and its sibling on the very
    next line was not, so coercing `diagnostics` back to `[]` reddened
    NOTHING. The container rule has to hold at both levels or a report can
    still arrive with a well-formed `sources` and an unreadable payload."""
    doc = {"schema_version": 1,
           "sources": [{"source": {"name": "gitleaks"},
                        "diagnostics": diagnostics}]}
    with pytest.raises(LP.LoopError) as exc:
        LP.from_prepass(doc)
    assert "not a list" in str(exc.value) and "gitleaks" in str(exc.value)


def test_a_refused_report_leaves_the_cli_saying_so_rather_than_crashing(
        repo, run, tmp_path, capsys):
    """The half of the old test worth keeping, at the boundary where "not a
    traceback" is actually observable: the refusal is printed and the loop
    exits 2, exactly as it does for a report that is not there at all."""
    doc = prepass_report("gitleaks", "ERROR")
    doc["sources"] = "oops"
    report = tmp_path / "prepass.json"
    report.write_text(json.dumps(doc), encoding="utf-8")
    assert LP.main(["--target", str(repo), "--run", run,
                    "--prepass", str(report), "--iterate"]) == 2
    assert "not a list" in capsys.readouterr().out


def test_a_diagnostics_field_that_is_null_is_not_a_traceback():
    doc = {"schema_version": 1,
           "sources": [{"source": {"name": "gitleaks"}, "diagnostics": None}]}
    assert LP.from_prepass(doc) == []


def test_a_malformed_entry_is_refused_by_name_not_discarded():
    """CLAUDE.md: "Zero silent drops ... it never discards it."

    The previous version of this test asserted the entry was SKIPPED, and its
    document's only well-formed source carried an empty diagnostics list - so
    `== []` held whether the bad entry was skipped, or the loop abandoned
    everything after it. A reviewer proved that by changing the `continue` to
    a `break`: one junk entry ahead of a real gitleaks source discarded every
    finding behind it and the whole file stayed green.

    A refusal makes both spellings impossible, and the two cases below pin it
    from the side that matters: a REAL finding sits after the malformed entry,
    so a reader that silently drops either one returns a list instead of
    raising."""
    real = {"severity": "ERROR", "message": "a leak", "code": {"value": "leak"},
            "location": {"path": "a.py", "range": {"start": {"line": 3}}}}

    junk_source = {"schema_version": 1, "sources": [
        "aws key at a.py:3",
        {"source": {"name": "gitleaks"}, "diagnostics": [real]}]}
    with pytest.raises(LP.LoopError, match="cannot read"):
        LP.from_prepass(junk_source)

    junk_diag = {"schema_version": 1, "sources": [
        {"source": {"name": "gitleaks"}, "diagnostics": ["a leak, as a string",
                                                         real]}]}
    with pytest.raises(LP.LoopError, match="cannot read"):
        LP.from_prepass(junk_diag)


def test_a_report_that_is_not_an_object_is_refused():
    """`_list` hardened the value and never the container: from_prepass([])
    raised AttributeError, which loop.main does not catch, so a runner's
    malformed report exited 1 - "iterate again"."""
    for shape in ([], "a report", 7):
        with pytest.raises(LP.LoopError, match="not an object"):
            LP.from_prepass(shape)


def test_a_real_finding_still_arrives(tmp_path):
    """The negative control: tolerating malformed shapes must not make the
    reader tolerant of findings."""
    doc = {"schema_version": 1,
           "sources": [{"source": {"name": "gitleaks"},
                        "diagnostics": [{"severity": "ERROR",
                                         "code": {"value": "leak"},
                                         "message": "m",
                                         "location": {"path": "a.py"}}]}]}
    rows = LP.from_prepass(doc)
    assert len(rows) == 1 and rows[0]["id"] == "leak"


# ── the other finding source, hardened the way its sibling already was ───────


def test_a_session_whose_findings_are_null_refuses_rather_than_crashing():
    """`from_prepass` was hardened for the present-null class in the previous
    pass and `from_session`, twenty lines away in the same file, was not — the
    leave-the-neighbours shape this whole review is about. `.get("findings",
    [])` returns the null, and `for row in None` raises TypeError out of the
    module that decides whether review may exit."""
    with pytest.raises(LP.LoopError) as exc:
        LP.from_session({"findings": None})
    assert "null" in str(exc.value)


@pytest.mark.parametrize("findings", ["a string", 7, {"id": "x"}])
def test_findings_that_are_not_a_list_are_refused_by_type(findings):
    """`or []` would cover null and empty and NOT a truthy value of the wrong
    type — the distinction measured one pass ago on `loop.from_prepass`'s
    `sources`, where `"sources": 7` still died."""
    with pytest.raises(LP.LoopError):
        LP.from_session({"findings": findings})


def test_a_finding_that_is_not_an_object_is_refused_not_dropped():
    """Refused rather than skipped, because `from_prepass` states that rule
    for the other source and CLAUDE.md states it for the repository: a review
    loop that quietly dropped a malformed finding would exit CLEAN on a
    session that had recorded one."""
    with pytest.raises(LP.LoopError) as exc:
        LP.from_session({"findings": [{"id": "ok"}, "oops"]})
    assert "finding 2" in str(exc.value)


@pytest.mark.parametrize("line,expected", [
    ("not-a-number", 0), (None, 0), ([], 0), (True, 0), ("", 0),
    ("42", 42), (42, 42), (" 7 ", 7),
])
def test_a_line_number_a_human_typed_never_takes_the_loop_down(line, expected):
    """`int(row.get("line", 0) or 0)` raises ValueError on a non-numeric
    string and TypeError on a list, and `hitl.py` accepts whatever a human
    types into `--line`. `_line_of` learned this for the nested rdjson case;
    this is the same rule where the value arrives bare. `True` is excluded
    because a bool IS an int and a finding on line True is nobody's intent."""
    assert LP.from_session({"findings": [{"id": "x", "line": line}]}
                           )[0]["line"] == expected


def test_a_session_that_records_nothing_is_still_an_empty_list():
    """The negative controls. An ABSENT `findings` key, an empty list and no
    session at all are all legitimately empty — folding them in with the
    damage would make the refusal fire on every review that found nothing."""
    assert LP.from_session({}) == []
    assert LP.from_session({"findings": []}) == []
    assert LP.from_session(None) == []


def test_a_well_formed_session_is_translated_unchanged():
    """The other negative control: severity is still lowered and stripped, and
    a numeric string line still becomes a number."""
    got = LP.from_session({"findings": [
        {"id": "f1", "source": "human", "severity": " High ",
         "disposition": "Open", "path": "a.py", "line": "12"}]})
    assert got == [{"id": "f1", "source": "human", "tool": "",
                    "severity": "high", "disposition": "open",
                    "path": "a.py", "line": 12, "message": ""}]


# ── T266: an unreadable session must not buy the gate ────────────────────────


def spawn_loop(target, run, *flags):
    return subprocess.run(
        [sys.executable, str(LOOP_CLI), "--target", str(target), "--run", run,
         *flags],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(target), timeout=300)


def deferred_critical(repo):
    """An intact session holding the loop open on a DEFERRED critical."""
    H.begin(repo, component="orders")
    H.finding(repo, "SEC-CRIT-1", "agent", "deferred", severity="critical")


def test_a_truncated_session_cannot_buy_the_gate(repo, run):
    """Spawned three ways, because the damage is only visible as an exit code.

    `hitl.load_pending` answered None for BOTH "no session" and "the bytes do
    not parse", so `from_session` saw the same None either way and assessed
    zero findings — the loop reported it may exit on a session whose critical
    is still literally in the file. A gate bought by damaging its own record.
    """
    deferred_critical(repo)
    intact = spawn_loop(repo, run)
    assert intact.returncode == 1, intact.stdout + intact.stderr
    assert "cannot be deferred out of the gate" in intact.stdout, intact.stdout

    _, pending = H.paths(repo)
    text = pending.read_text(encoding="utf-8")
    half = text[: len(text) // 2]
    pending.write_text(half, encoding="utf-8")
    assert "SEC-CRIT-1" in half, "the critical is still in the bytes"

    damaged = spawn_loop(repo, run)
    assert damaged.returncode == 3, damaged.stdout + damaged.stderr
    assert "the loop may exit" not in damaged.stdout, damaged.stdout
    assert "cannot be read" in damaged.stdout, damaged.stdout


def test_no_session_at_all_still_lets_a_clean_loop_exit(repo, run):
    """The CONTROL. Absent is not unreadable, and collapsing it the other way
    would be this same defect mirrored: a repository that has opened no review
    session has nothing to refuse."""
    _, pending = H.paths(repo)
    assert not pending.exists()
    clean = spawn_loop(repo, run)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "the loop may exit" in clean.stdout, clean.stdout


# ── T362: a PRE_EXISTING_SUFFIX finding is weighed as pre-existing, not open ──
#
# Item 109's whole-file-attribution class through a second door. prepass.py
# separates PRE-EXISTING own-tree findings under a source named
# `<tool> (pre-existing)` and EXCLUDES them from its own summary.total and
# summary.by_severity — but `loop.from_prepass`/`assess` had ZERO awareness of
# the suffix, so a finding the report already called pre-existing came back at
# full severity and landed in `open` exactly like an in-change one. The pre-pass
# excluded it from its counts; the loop that decides the gate did not.


def pre_existing_report(severity: str = "ERROR", rule: str = "no-eval") -> dict:
    """A prepass.json whose only finding is PRE-EXISTING: the change introduced
    nothing, but the touched file already carried a smell. Both gates fired ok
    (so `unreviewed` is satisfied) and the finding rides `ast-grep (pre-existing)`,
    exactly as prepass.py writes it."""
    return prepass_report("ast-grep" + PP.PRE_EXISTING_SUFFIX, severity, rule)


def test_from_prepass_marks_a_suffixed_source_and_recovers_its_tool_name():
    """The reader tags the finding and un-suffixes the tool, welded to prepass's
    one spelling. An in-change source is not pre-existing — the negative control
    that stops the flag being stuck on."""
    [pre] = LP.from_prepass(pre_existing_report("ERROR"))
    assert pre["pre_existing"] is True
    assert pre["tool"] == "ast-grep", "the suffix is stripped to the real tool"

    [mine] = LP.from_prepass(prepass_report("ast-grep", "ERROR"))
    assert mine["pre_existing"] is False


def test_a_purely_pre_existing_block_on_finding_does_not_hold_the_gate():
    """The headline defect (T362). `block_on: [critical, high]` and an ERROR that
    maps to `high`: pre-fix this landed in `open` and `may_exit` was False, so a
    change that introduced NOTHING could not exit — while prepass.py's own
    `exit_code` called the identical document clean. The two must agree."""
    verdict = LP.assess(LP.from_prepass(pre_existing_report("ERROR")),
                        ["critical", "high"])
    assert LP.may_exit(verdict), "a pre-existing finding must not hold the gate"
    assert verdict["open"] == []
    assert verdict["counts"] == {}, "not weighed into the change's severities"
    # ...and NOT silently dropped: it stays visible as context (zero silent drops).
    assert [f["id"] for f in verdict["pre_existing"]] == ["no-eval"]
    assert verdict["pre_existing_counts"] == {"high": 1}


def test_the_same_finding_in_the_change_still_holds_the_gate():
    """The negative control that pins the fix to the SUFFIX and not to severity:
    the identical ERROR, in an in-change `ast-grep` source, still blocks."""
    verdict = LP.assess(LP.from_prepass(prepass_report("ast-grep", "ERROR")),
                        ["critical", "high"])
    assert not LP.may_exit(verdict)
    assert verdict["counts"] == {"high": 1}
    assert verdict["pre_existing"] == []


def test_a_pre_existing_and_an_in_change_finding_are_weighed_apart():
    """Both in one verdict: only the in-change one gates; the pre-existing one is
    carried as context. The gate numbers stay the change's alone."""
    findings = (LP.from_prepass(prepass_report("gitleaks", "ERROR"))
                + LP.from_prepass(pre_existing_report("ERROR")))
    verdict = LP.assess(findings, ["critical", "high"])
    assert [f["id"] for f in verdict["open"]] == ["generic-api-key"]
    assert verdict["counts"] == {"critical": 1}          # gitleaks only
    assert verdict["pre_existing_counts"] == {"high": 1}  # the pre-existing one


def test_cli_a_purely_pre_existing_finding_lets_the_loop_exit(repo, run, tmp_path):
    """The gate's contract is a PROCESS, not a function (CLAUDE.md): only a spawn
    reads the exit code a runner acts on. Pre-fix the loop returned 1 (iterate
    again) on a change that introduced nothing; it must return 0, and it must say
    the pre-existing finding is there rather than dropping it."""
    report = tmp_path / "prepass.json"
    report.write_text(json.dumps(pre_existing_report("ERROR")), encoding="utf-8")
    done = spawn_loop(repo, run, "--prepass", str(report), "--iterate")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "no block_on severity is open" in done.stdout, done.stdout
    assert "context, not gated" in done.stdout, done.stdout
    # visible in the loop's own record, too — counted, never dropped.
    entry = LP.load_record(repo, run)["iterations"][-1]
    assert entry["pre_existing_counts"] == {"high": 1}


def test_the_pre_existing_suffix_is_welded_to_prepass_one_spelling():
    """Item 115: two spellings of one convention is how they drift. loop.py must
    reference `prepass.PRE_EXISTING_SUFFIX`, never restate the literal — a mutant
    that swaps the reference for a hard-coded `" (pre-existing)"` fails here, and
    a mutant that mis-spells the literal fails the behavioural tests above."""
    src = (ROOT / "tools" / "review" / "loop.py").read_text(encoding="utf-8")
    assert "prepass.PRE_EXISTING_SUFFIX" in src, \
        "loop.py must reference prepass's one spelling of the suffix"
    assert PP.PRE_EXISTING_SUFFIX not in src, \
        "loop.py restates the suffix literal instead of welding to the constant"


# ── T299: escalation and blank severity_class pinned by measurement ──────


def _prepass_with_severity_class(tool, severity_class, rdjson_severity=None,
                                 rule="test-rule"):
    """Build a prepass report whose diagnostic carries severity_class.

    If *rdjson_severity* is given the rdjson ``severity`` field is also
    present — the shape that verifies the could-not-grade contract.
    """
    gates = [{"name": g, "status": "ok", "detail": "",
              "findings": 1 if g == tool else 0} for g in PP.GATES]
    if tool not in PP.GATES:
        gates.append({"name": tool, "status": "ok", "detail": "",
                      "findings": 1})
    diag = {
        LP.SEVERITY_FIELD: severity_class,
        "message": "a finding",
        "code": {"value": rule},
        "location": {"path": "a.py", "range": {"start": {"line": 4}}},
    }
    if rdjson_severity is not None:
        diag["severity"] = rdjson_severity
    return {"schema_version": 1, "scope": {"mode": "diff", "files": ["a.py"]},
            "tools": gates,
            "sources": [{"source": {"name": tool}, "diagnostics": [diag]}],
            "summary": {"total": 1, "by_severity": {}}}


def _ungraded_finding(**over):
    row = {"id": "UG1", "source": "agent", "tool": "",
           "severity": "", "disposition": "",
           "path": "a.py", "line": 1, "message": "test",
           "pre_existing": False}
    row.update(over)
    return row


def test_ungraded_finding_cannot_be_deferred_past_the_gate():
    """T299 escalation — the ungraded check in the escalation branch runs
    before the deferral check, so an ungraded finding with
    disposition=deferred still holds the loop open."""
    verdict = LP.assess([_ungraded_finding(disposition="deferred")],
                        ["critical", "high"])
    assert not LP.may_exit(verdict)
    assert len(verdict["ungraded"]) == 1
    assert verdict["ungraded"][0]["why"] == "ungraded"


def test_ungraded_finding_can_be_cleared_by_rejection():
    """Rejection is judged before severity — somebody decided it is not real."""
    verdict = LP.assess([_ungraded_finding(disposition="rejected")],
                        ["critical", "high"])
    assert LP.may_exit(verdict)


def test_ungraded_finding_can_be_cleared_by_suppression():
    """Suppression by id removes the finding before severity is checked."""
    verdict = LP.assess([_ungraded_finding(id="SUPP1")],
                        ["critical", "high"], suppress=["SUPP1"])
    assert LP.may_exit(verdict)
    assert verdict["suppressed"] == ["SUPP1"]


def test_blank_severity_class_beside_rdjson_severity_is_ungraded():
    """T299 — a blank severity_class is an explicit could-not-grade from the
    tool.  The rdjson ``severity`` field is the tool's own vocabulary, not a
    grade; an explicit could-not-grade must not be overridden by it.  The
    finding must land as ungraded (severity ``""``), not as the rdjson
    mapping of ``ERROR``."""
    findings = LP.from_prepass(
        _prepass_with_severity_class("ast-grep", "", rdjson_severity="ERROR"))
    assert findings[0]["severity"] == ""


def test_whitespace_severity_class_is_treated_as_blank():
    """Whitespace-only is blank after strip() — same contract."""
    findings = LP.from_prepass(
        _prepass_with_severity_class("ast-grep", "  ", rdjson_severity="ERROR"))
    assert findings[0]["severity"] == ""


def test_valid_severity_class_still_works():
    """Sanity: a non-blank severity_class in the vocabulary is accepted."""
    findings = LP.from_prepass(
        _prepass_with_severity_class("ast-grep", "high"))
    assert findings[0]["severity"] == "high"


# ── T378: a present-null severity_class is ungraded, not refused as 'none' ──


def test_null_severity_class_is_ungraded_like_blank():
    """T378 — a JSON ``null`` (Python None) in ``severity_class`` is the
    same statement as blank: the tool looked and could not grade.  It must
    NOT become the string ``'none'`` and be refused as an unknown class.

    The rdjson ``severity`` field must not override an explicit
    could-not-grade, exactly as T299 established for blank."""
    findings = LP.from_prepass(
        _prepass_with_severity_class("ast-grep", None, rdjson_severity="ERROR"))
    assert findings[0]["severity"] == ""


def test_null_severity_class_without_rdjson_is_also_ungraded():
    """Same contract when no rdjson severity fallback is present."""
    findings = LP.from_prepass(
        _prepass_with_severity_class("ast-grep", None))
    assert findings[0]["severity"] == ""


def test_null_severity_class_holds_the_loop_open_through_cli(repo, run, tmp_path):
    """The gate contract through the spawned door: a null severity_class
    results in an ungraded finding that holds the loop open (exit 1, not
    exit 2 which would mean a refusal of the report itself)."""
    report = tmp_path / "prepass.json"
    report.write_text(json.dumps(
        _prepass_with_severity_class("ast-grep", None, rdjson_severity="ERROR")),
        encoding="utf-8")
    done = spawn_loop(repo, run, "--prepass", str(report), "--iterate")
    assert done.returncode == 1, \
        f"expected exit 1 (open/ungraded), got {done.returncode}: {done.stdout}"
    assert "carries no severity" in done.stdout, done.stdout


def test_unknown_severity_class_is_still_refused_by_name():
    """The CONTROL: a genuinely unknown class name like 'banana' must still
    be refused by its name, not silently accepted or treated as ungraded."""
    with pytest.raises(LP.LoopError, match="banana"):
        LP.from_prepass(
            _prepass_with_severity_class("ast-grep", "banana"))
