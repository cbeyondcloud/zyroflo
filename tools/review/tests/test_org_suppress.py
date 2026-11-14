"""T716 (S3) - an organisation's review suppressions, `policy.yml`'s
`org_review_suppress`.

Design: `docs/design/2026-11-14-s3-s4-org-suppressions.md`, sections 1 to 4. The
owner signed off on the two conditions it stops on:

1. the key lives only in `policy.yml`, never in a profile;
2. an org-set entry silences a finding OCCURRENCE only when that occurrence has a
   legal severity outside the run's resolved `block_on` - ungraded or blocking
   occurrences still fire, and the verdict says why.

With both, an org-set entry can change what a review REPORTS and never whether it
may EXIT. Test 6 pins that as an invariant over every combination the loop can
see; the rest pin the shape, the visibility and the doors.
"""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.review import hitl as H  # noqa: E402
from tools.review import loop as LP  # noqa: E402
from tools.sdlc import learning as LEARN  # noqa: E402
from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as L  # noqa: E402

LOOP_CLI = Path(LP.__file__)
LEARNING_CLI = ROOT / "tools" / "sdlc" / "learning.py"
KEY = "org_review_suppress"
PROFILE = ("gate_profile: solo\nreview:\n  block_on: [critical, high]\n"
           "  max_iterations: 3\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    return r


def policy(root: Path, body: str, where: str = ".sdlc") -> Path:
    path = root / where / "policy.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def finding(fid: str = "ORG1", severity: str = "low", disposition: str = "accepted",
            source: str = "agent", **over) -> dict:
    row = {"id": fid, "source": source, "tool": "", "severity": severity,
           "disposition": disposition, "path": "a.py", "line": 1, "message": ""}
    row.update(over)
    return row


def org(*ids: str, repository: tuple[str, ...] = ()) -> LP.Suppressions:
    return LP.Suppressions(repository=tuple(repository), org=tuple(ids))


# ── 1. an absent key, or an empty one, changes no byte ───────────────────────
#
# Captured on `main` at "docs(backlog): file T714 (fixed on its branch, not
# merged) and T715 as Deferred", before any of this row's code existed, from the
# fixture below: one open, one below the line, one rejected while ungraded, one
# pre-existing, one silenced by the repository's own list. The comparison is of
# `json.dumps(..., indent=2, sort_keys=True)` - the bytes the CLI prints and the
# loop record is written as - so a key that appears, even empty, turns it red.

GOLDEN_FINDINGS = [
    finding("OPEN1", "high", "accepted", "agent"),
    finding("LOW1", "low", "accepted", "human", path="b.py", line=2),
    finding("REJ1", "", "rejected", "agent", path="c.py", line=3),
    finding("PRE1", "critical", "", "prepass", tool="semgrep", path="d.py", line=4,
            message="m", pre_existing=True),
    finding("SUP1", "critical", "", "agent", path="e.py", line=5),
]

GOLDEN_VERDICT = {
    "block_on": ["critical", "high"],
    "counts": {"high": 1, "low": 1},
    "deferred_blockers": [],
    "open": [{"disposition": "accepted", "id": "OPEN1", "line": 1, "message": "",
              "path": "a.py", "severity": "high", "source": "agent", "tool": "",
              "why": "open"}],
    "pre_existing": [{"disposition": "", "id": "PRE1", "line": 4, "message": "m",
                      "path": "d.py", "pre_existing": True, "severity": "critical",
                      "source": "prepass", "tool": "semgrep"}],
    "pre_existing_counts": {"critical": 1},
    "rejected": [{"disposition": "rejected", "id": "REJ1", "line": 3, "message": "",
                  "path": "c.py", "severity": "", "source": "agent", "tool": "",
                  "why": "rejected-ungraded"}],
    "suppressed": ["SUP1"],
    "ungraded": [],
}

GOLDEN_ENTRY = {
    "at": "2026-11-14T00:00:00Z",
    "block_on": ["critical", "high"],
    "counts": {"high": 1, "low": 1},
    "iteration": 1,
    "note": "n",
    "open": [{"id": "OPEN1", "line": 1, "path": "a.py", "severity": "high",
              "source": "agent"}],
    "pre_existing_counts": {"critical": 1},
    "rejected": [{"id": "REJ1", "line": 3, "path": "c.py", "severity": "",
                  "source": "agent", "why": "rejected-ungraded"}],
    "score_reported": 4.0,
}

GOLDEN_RENDER = (
    "iteration 1 of at most 3 - OPEN\n"
    "block_on: critical, high\n"
    "findings by severity: high 1, low 1\n"
    "pre-existing (context, not gated): critical 1\n"
    "rejected (judged not real, recorded not dropped): 1, 1 of them ungraded\n"
    "rubric score: 4.0 - reported for continuity, never the gate\n"
    "  !! OPEN1 [high] at a.py:1 is open")


def _bytes(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


@pytest.mark.parametrize("policy_body", [None, "schema_version: 1\n",
                                         f"{KEY}: []\n"],
                         ids=["no policy", "policy without the key", "an empty key"])
def test_an_absent_or_empty_key_changes_no_byte_of_any_output(repo, monkeypatch,
                                                              policy_body):
    (repo / ".sdlc" / "profile.yml").write_text(PROFILE + "review_suppress: [SUP1]\n",
                                                encoding="utf-8")
    if policy_body is not None:
        policy(repo, policy_body)
    monkeypatch.setattr(LP, "_now", lambda: "2026-11-14T00:00:00Z")
    run = L.create(repo, "requirement", "golden")["run_id"]
    verdict = LP.assess(GOLDEN_FINDINGS, LP.block_on_of(repo), LP.suppress_of(repo))
    result = LP.iterate(repo, run, GOLDEN_FINDINGS, 4.0, "n")
    stored = json.loads(LP.record_path(repo, run).read_text(encoding="utf-8"))
    assert _bytes(verdict) == _bytes(GOLDEN_VERDICT)
    assert _bytes(stored["iterations"][-1]) == _bytes(GOLDEN_ENTRY)
    assert LP.render(result) == GOLDEN_RENDER


# ── 2-5, 8. what an org-set entry silences, and what it may not ──────────────


def test_an_org_listed_finding_below_the_line_is_silenced_and_reported_as_org_set():
    verdict = LP.assess([finding("ORG1", "low"), finding("OTHER", "low")],
                        ["critical", "high"], org("ORG1"))
    assert verdict["org_suppressed"] == ["ORG1"]
    assert verdict["suppressed"] == [], "org-set is never reported as the repository's"
    assert verdict["counts"] == {"low": 1}, "the silenced one left the counts"
    assert verdict["org_suppress_held"] == []
    assert LP.may_exit(verdict)


def test_the_same_id_at_a_block_on_severity_in_the_same_review_still_fires():
    """Per occurrence, never per id: one id graded low once and high once is
    silenced once and held once."""
    verdict = LP.assess([finding("ORG1", "low"), finding("ORG1", "high", line=9)],
                        ["critical", "high"], org("ORG1"))
    assert [(f["id"], f["severity"]) for f in verdict["open"]] == [("ORG1", "high")]
    assert verdict["org_suppressed"] == ["ORG1"]
    held = verdict["org_suppress_held"]
    assert [(h["id"], h["source"], h["severity"]) for h in held] == [
        ("ORG1", "agent", "high")]
    assert "block_on" in held[0]["why"], held
    assert not LP.may_exit(verdict)


def test_an_ungraded_org_listed_finding_still_holds_the_loop():
    """The contrast with `test_ungraded_finding_can_be_cleared_by_suppression`:
    the repository's own list silences an ungraded finding; the organisation's
    may not, because nobody showed it to be outside block_on."""
    verdict = LP.assess([finding("ORG1", "")], ["critical", "high"], org("ORG1"))
    assert [f["id"] for f in verdict["ungraded"]] == ["ORG1"]
    held = verdict["org_suppress_held"]
    assert [(h["id"], h["severity"]) for h in held] == [("ORG1", "")]
    assert "grade" in held[0]["why"] or "ungraded" in held[0]["why"], held
    assert verdict["org_suppressed"] == []
    assert not LP.may_exit(verdict)


@pytest.mark.parametrize("profile_body", [
    "gate_profile: regulated\nreview:\n  block_on: [critical, high]\n",
    "gate_profile: solo\nreview:\n  block_on: [critical, high, medium]\n",
], ids=["a regulated run", "a profile that adds medium"])
def test_a_run_that_tightened_block_on_keeps_its_tightening(repo, profile_body):
    """Against the RESOLVED block_on, not `block_on_floor`: the organisation
    cannot loosen past a run that tightened."""
    (repo / ".sdlc" / "profile.yml").write_text(profile_body, encoding="utf-8")
    policy(repo, f"{KEY}: [ORG1]\n")
    block_on = LP.block_on_of(repo)
    assert "medium" in block_on, "the premise"
    verdict = LP.assess([finding("ORG1", "medium")], block_on, LP.suppress_of(repo))
    assert [f["id"] for f in verdict["open"]] == ["ORG1"]
    assert [h["severity"] for h in verdict["org_suppress_held"]] == ["medium"]
    assert not LP.may_exit(verdict)


def test_an_id_in_both_lists_is_the_repositorys_and_silenced_at_any_severity():
    verdict = LP.assess([finding("BOTH", "critical"), finding("BOTH", "")],
                        ["critical", "high"], org("BOTH", repository=("BOTH",)))
    assert verdict["suppressed"] == ["BOTH"]
    assert verdict["org_suppressed"] == []
    assert verdict["org_suppress_held"] == []
    assert LP.may_exit(verdict), "the repository's own power is unchanged"


# ── 6, 7. the invariant: an org list never moves a gate ──────────────────────

SEVERITY_CASES = ("critical", "high", "medium", "low", "")
DISPOSITION_CASES = ("accepted", "rejected", "deferred", "")


def _outcome(findings, block_on, suppress):
    try:
        v = LP.assess(findings, block_on, suppress)
    except LP.LoopError as exc:
        return ("raised", str(exc))
    return ("verdict", _bytes({k: v[k] for k in ("open", "ungraded",
                                                  "deferred_blockers",
                                                  "pre_existing")}),
            LP.may_exit(v))


@pytest.mark.parametrize("block_on", [["critical", "high"],
                                      ["critical", "high", "medium"]])
def test_an_org_list_changes_no_gate_over_every_combination(block_on):
    combos = list(itertools.product(SEVERITY_CASES, DISPOSITION_CASES, H.SOURCES,
                                    (False, True)))
    assert len(combos) == 5 * 4 * len(H.SOURCES) * 2, "vacuity guard"
    moved = []
    for severity, disposition, source, pre in combos:
        rows = [finding("ORG1", severity, disposition, source, pre_existing=pre)]
        # And beside a finding the list does not name, so the filter is seen
        # leaving the rest of a review alone.
        rows.append(finding("KEEP", "high", "accepted", "agent", line=2))
        if (_outcome(rows, block_on, None)
                != _outcome(rows, block_on, org("ORG1"))):
            moved.append((severity, disposition, source, pre))
    assert moved == []


@pytest.mark.parametrize("over", [{"severity": "hgih"}, {"severity": "info"},
                                  {"disposition": "acepted"},
                                  {"severity": "low", "disposition": "acepted"},
                                  {"severity": "hgih", "disposition": "rejected"}],
                         ids=["misspelled severity", "info", "unknown disposition",
                              "unknown disposition below the line",
                              "illegal severity, rejected"])
def test_damage_on_an_org_listed_finding_is_refused_exactly_as_without_the_list(over):
    rows = [finding("ORG1", **over)]
    assert _outcome(rows, ["critical", "high"], None) == \
        _outcome(rows, ["critical", "high"], org("ORG1"))


# ── 9, 10. one reader, and it never reads the profile for this key ───────────


def test_a_profile_carrying_the_key_silences_nothing(repo):
    """Condition 1. Passes on main too (nothing read the key), so it is proved
    by mutation M5: reading the key from the profile turns it red."""
    (repo / ".sdlc" / "profile.yml").write_text(PROFILE + f"{KEY}: [ORG1]\n",
                                                encoding="utf-8")
    got = LP.suppress_of(repo)
    assert got.org == ()
    verdict = LP.assess([finding("ORG1", "low")], LP.block_on_of(repo), got)
    assert verdict["counts"] == {"low": 1}
    assert "org_suppressed" not in verdict


@pytest.mark.parametrize("where", ["sdlc", ".sdlc"])
def test_suppress_of_reads_the_org_list_from_either_policy_location(repo, where):
    (repo / ".sdlc" / "profile.yml").write_text(PROFILE + "review_suppress: [MINE]\n",
                                                encoding="utf-8")
    policy(repo, f"{KEY}: [ORG1, ORG2, ORG1]\n", where)
    got = LP.suppress_of(repo)
    assert isinstance(got, LP.Suppressions)
    assert got.repository == ("MINE",)
    assert got.org == ("ORG1", "ORG2"), "a duplicate is read once"
    with pytest.raises(TypeError):
        iter(got)
    with pytest.raises(TypeError):
        list(got)


def test_a_plain_list_still_means_the_repositorys_entries():
    """The ~35 existing `assess(..., suppress=[...])` calls stand."""
    verdict = LP.assess([finding("X", "")], ["critical"], ["X"])
    assert verdict["suppressed"] == ["X"]
    assert "org_suppressed" not in verdict


# ── 11. the validator ────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, said", [
    ("ORG1", "must be a list"),
    ([""], "blank"),
    (["   "], "blank"),
    ([7], "blank"),
    (["(unnamed)"], "(unnamed)"),
    ([" (unnamed) "], "(unnamed)"),
    (["*"], "glob"),
    (["a?b"], "glob"),
    (["rule[1]"], "glob"),
])
def test_the_validator_refuses_each_malformed_list_by_name(value, said):
    problems = P.validate({KEY: value})
    assert problems and all(KEY in p for p in problems), problems
    assert any(said in p for p in problems), problems


def test_the_validator_accepts_exact_ids_and_duplicates():
    assert P.validate({KEY: ["py/lint", "SEC-1", "py/lint"]}) == []
    assert P.DEFAULTS[KEY] == []


def test_the_spawned_validator_knows_the_key(repo):
    policy(repo, f"{KEY}: [py/lint]\n")
    ok = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "policy.py"), "--validate",
         "--target", str(repo)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    policy(repo, f'{KEY}: ["(unnamed)"]\n')
    bad = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "policy.py"), "--validate",
         "--target", str(repo)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    assert bad.returncode != 0, bad.stdout + bad.stderr
    assert KEY in bad.stdout + bad.stderr, bad.stdout + bad.stderr
    assert "Traceback" not in bad.stdout + bad.stderr


# ── 12, 13. the doors a person reads ─────────────────────────────────────────


def spawn(argv: list[str], cwd: Path):
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(cwd),
                          timeout=300)


def test_learning_audit_names_org_set_entries_apart_from_hand_written_ones(repo):
    policy(repo, f"{KEY}: [ORG1, ORG2]\n")
    clean = spawn([str(LEARNING_CLI), "--target", str(repo), "--audit"], repo)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "2 org-set entr(ies) in policy.yml" in clean.stdout, clean.stdout
    assert "not learned here" in clean.stdout, clean.stdout
    assert "written by hand" not in clean.stdout, clean.stdout

    (repo / ".sdlc" / "profile.yml").write_text(PROFILE + "review_suppress: [MINE]\n",
                                                encoding="utf-8")
    owed = spawn([str(LEARNING_CLI), "--target", str(repo), "--audit"], repo)
    assert owed.returncode == 1, owed.stdout + owed.stderr
    assert "'MINE'" in owed.stdout and "written by hand" in owed.stdout, owed.stdout
    assert "'ORG1'" not in owed.stdout, "an org-set entry is not called hand-written"
    assert "2 org-set entr(ies) in policy.yml" in owed.stdout, owed.stdout


def test_learning_audit_without_the_key_prints_what_it_printed(repo):
    out = spawn([str(LEARNING_CLI), "--target", str(repo), "--audit"], repo)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout == (f"every review_suppress entry is accounted for in "
                          f"{LEARN.LOG}\n"), out.stdout


@pytest.mark.parametrize("listed", [True, False])
def test_the_iterate_door_shows_org_lines_only_when_the_list_is_non_empty(repo,
                                                                          listed):
    if listed:
        policy(repo, f"{KEY}: [ORG1, ORG2]\n")
    run = L.create(repo, "requirement", "a change")["run_id"]
    H.begin(repo, component="pay", run=run)
    H.finding(repo, "ORG1", "agent", "accepted", severity="low")
    H.finding(repo, "ORG2", "human", "accepted", severity="high")
    out = spawn([str(LOOP_CLI), "--target", str(repo), "--run", run, "--iterate"], repo)
    assert out.returncode == 1, out.stdout + out.stderr  # ORG2 high is open either way
    record = LP.record_path(repo, run).read_text(encoding="utf-8")
    entry = json.loads(record)["iterations"][-1]
    if listed:
        assert ("silenced by the organisation's policy (org_review_suppress): ORG1"
                in out.stdout), out.stdout
        assert "org-set, still reported: ORG2 [high]" in out.stdout, out.stdout
        assert entry["org_suppressed"] == ["ORG1"]
        assert [(h["id"], h["severity"]) for h in entry["org_suppress_held"]] == [
            ("ORG2", "high")]
    else:
        assert "org" not in out.stdout.replace("organis", ""), out.stdout
        assert "org_suppress" not in record
        assert "org_review_suppress" not in out.stdout


# ── 14. a run cannot write the file that holds the key ───────────────────────


@pytest.mark.parametrize("rel", ["sdlc/policy.yml", ".sdlc/policy.yml"])
def test_a_runs_write_to_either_policy_location_is_refused(tmp_path, rel):
    """Passes on main; it guards the default `protected_paths` against a later
    narrowing (mutation M7)."""
    said = P.protected(dict(P.DEFAULTS), tmp_path, [str(tmp_path / rel)])
    assert said and "protected path" in said[0], said
