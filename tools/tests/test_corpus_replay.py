"""Corpus floors and the nightly replay — item 51.

Item 17 harvested an adopter's merge history and item 18 proved once, blind,
that the impact engine beat grep on three real commits. Neither RECURS, and a
one-off proof is a claim about a day.

The two properties worth more than the rest of this file:

**The comparison is PER CASE.** An aggregate precision over a different sample
each night moves for reasons that have nothing to do with the engine — pick
three easy commits and it rises, pick three hard ones and it falls, and either
way somebody reads a trend into sampling noise. Every case is compared only
against the last run that scored THAT sha, which is item 97's "each question
gets its own reference" arriving from a different direction.

**A replay that scored nothing is a failure.** Zero cases replayed and zero
regressions found are the same output from a working corpus and a missing one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import corpus_harvest as HARVEST  # noqa: E402
from tools import corpus_replay as R  # noqa: E402

FILES = ["a/b/Thing.java", "a/b/Other.java", "a/b/Third.java"]


def sha_of(i: int) -> str:
    """A distinct sha whose FIRST characters differ.

    `f"{i:040d}"` varies only in its last digits, and the corpus names each
    file by `sha[:12]` — so three such cases were one file, and three of these
    tests were asserting over a corpus of one. A fixture has to exercise the
    property its test is named for.
    """
    return (f"{i:x}" * 40)[:40]


def case(sha: str, kind: str = "scoping", files=None, date="2026-08-01T00:00:00Z"):
    return {"schema_version": 1, "repo": "api", "sha": sha,
            "parent": "p" + sha, "date": date, "title": "a change",
            "classes": [kind], "files": list(files or FILES), "synthetic": False}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / R.CORPUS).mkdir(parents=True)
    return r


def store(repo: Path, rows: list[dict]) -> None:
    for row in rows:
        (repo / R.CORPUS / f"{row['sha'][:12]}.json").write_text(
            json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ranks_truth(case_row, alias, seed):
    """A perfect ranker: everything the merge touched, first."""
    return [(alias, f) for f in case_row["files"]]


def ranks_nothing(case_row, alias, seed):
    return [(alias, "z/z/Unrelated.java")]


def ranks_partly(case_row, alias, seed):
    """One true file, then filler — a real but weaker ranking."""
    return ([(alias, case_row["files"][-1])]
            + [(alias, f"z/z/Filler{i}.java") for i in range(19)])


def run(repo: Path, rows: list[dict], ranker, at: str = "t", limit: int = 9):
    return R.replay(repo, rows, [], repo, repo / "scratch", limit=limit,
                    ranker=ranker, at=at)


# ── the floors, read from the corpus an adopter actually has ────────────────


def test_floors_are_counted_from_the_stored_corpus(repo):
    """`corpus_harvest.probe_report` answers this at HARVEST time from live
    results, so nothing anywhere asked it of the corpus on disk."""
    store(repo, [case(sha_of(i), "defect") for i in range(4)])
    found = R.floors(R.cases(repo))
    assert found["cases"] == 4
    assert found["classes"]["defect"] == 4
    assert found["met"]["defect"] is False       # the floor is 10


def test_the_floors_are_the_harvesters_numbers_not_a_second_copy(repo):
    assert R.floors([])["floors"] == dict(HARVEST.FLOORS)


def test_a_met_floor_is_reported_met(repo):
    store(repo, [case(sha_of(i), "defect") for i in range(HARVEST.FLOORS["defect"])])
    stored = R.cases(repo)
    assert R.floors(stored)["met"]["defect"] is True
    assert not [p for p in R.floor_problems(stored) if "defect" in p]


def test_an_absent_corpus_is_said_out_loud_not_counted_as_zero(repo):
    """`--write` is the adopter's own act, so a repository that never harvested
    reports the same nothing as one whose corpus decayed. They need different
    sentences."""
    owed = R.floor_problems(R.cases(repo))
    assert len(owed) == 1 and "no corpus is stored" in owed[0]


def test_an_unreadable_case_is_refused_not_skipped(repo):
    """A floor counted over the readable remainder is a floor counted over a
    corpus nobody has."""
    store(repo, [case(sha_of(i)) for i in range(2)])
    (repo / R.CORPUS / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(R.ReplayError) as exc:
        R.cases(repo)
    assert "broken.json" in str(exc.value)


def test_a_case_without_a_sha_is_damage_too(repo):
    (repo / R.CORPUS / "x.json").write_text('{"classes": ["defect"]}',
                                            encoding="utf-8")
    with pytest.raises(R.ReplayError):
        R.cases(repo)


# ── the replay, and what "worse" means ──────────────────────────────────────


def test_a_replay_scores_every_case_it_picked(repo):
    rows = [case(sha_of(i)) for i in range(3)]
    now = run(repo, rows, ranks_truth)
    assert now["replayed"] == 3 and len(now["cases"]) == 3
    assert all(s["recall_at_20"] == 1.0 for s in now["detail"])


def test_a_first_run_has_nothing_to_compare_and_says_nothing(repo):
    rows = [case(sha_of(i)) for i in range(2)]
    assert R.problems(repo, run(repo, rows, ranks_truth), rows=[]) == []


def test_a_fall_to_zero_is_always_a_regression(repo):
    """The difference between "ranks worse" and "ranks nothing" is the
    difference item 97 exists for."""
    rows = [case(sha_of(i)) for i in range(2)]
    before = run(repo, rows, ranks_truth, at="t1")
    after = run(repo, rows, ranks_nothing, at="t2")
    owed = R.problems(repo, after, rows=[before])
    assert owed and all("fell" in p for p in owed)
    assert any("ranked nothing true" in p for p in owed)


def test_a_drop_inside_the_margin_is_noise_and_not_reported(repo):
    """The test that must NOT fire. A check that reddened on every wobble is
    one people switch off."""
    rows = [case("a" * 40)]
    before = {"cases": {"a" * 40: {"precision_at_10": 0.50, "recall_at_20": 0.90}}}
    now = {"replayed": 1,
           "cases": {"a" * 40: {"precision_at_10": 0.45, "recall_at_20": 0.85}}}
    assert R.problems(repo, now, rows=[before]) == []


def test_a_drop_beyond_the_margin_is_reported(repo):
    before = {"cases": {"a" * 40: {"precision_at_10": 0.50, "recall_at_20": 0.90}}}
    now = {"replayed": 1,
           "cases": {"a" * 40: {"precision_at_10": 0.20, "recall_at_20": 0.90}}}
    owed = R.problems(repo, now, rows=[before])
    assert len(owed) == 1 and "precision_at_10" in owed[0] and "margin" in owed[0]


def test_an_improvement_is_never_a_regression(repo):
    before = {"cases": {"a" * 40: {"precision_at_10": 0.20, "recall_at_20": 0.30}}}
    now = {"replayed": 1,
           "cases": {"a" * 40: {"precision_at_10": 0.90, "recall_at_20": 1.0}}}
    assert R.problems(repo, now, rows=[before]) == []


# ── per case, never per run ─────────────────────────────────────────────────


def test_a_case_not_picked_tonight_is_not_a_regression(repo):
    """The design's centre. A nightly replay samples, so a case absent from
    this run was not picked - calling that a regression would train people to
    ignore the ones that are."""
    before = {"cases": {"a" * 40: {"precision_at_10": 0.9, "recall_at_20": 0.9},
                        "b" * 40: {"precision_at_10": 0.9, "recall_at_20": 0.9}}}
    now = {"replayed": 1,
           "cases": {"a" * 40: {"precision_at_10": 0.9, "recall_at_20": 0.9}}}
    assert R.problems(repo, now, rows=[before]) == []


def test_a_case_is_compared_to_the_last_run_that_scored_IT(repo):
    """Not the last run. A case sampled on Monday and again on Friday is
    compared across those two, however many nights in between never picked it -
    which is why the reference is per sha."""
    monday = {"cases": {"a" * 40: {"precision_at_10": 0.9, "recall_at_20": 0.9}}}
    tuesday = {"cases": {"b" * 40: {"precision_at_10": 0.1, "recall_at_20": 0.1}}}
    friday = {"replayed": 1,
              "cases": {"a" * 40: {"precision_at_10": 0.1, "recall_at_20": 0.9}}}
    owed = R.problems(repo, friday, rows=[monday, tuesday])
    assert len(owed) == 1 and owed[0].startswith("a" * 12)


def test_a_measure_that_stopped_being_reported_is_a_finding(repo):
    """Item 97's fourth kind, `dropped`. An absent measure cannot be read as a
    measure that held."""
    before = {"cases": {"a" * 40: {"precision_at_10": 0.8, "recall_at_20": 0.8}}}
    now = {"replayed": 1, "cases": {"a" * 40: {"recall_at_20": 0.8}}}
    owed = R.problems(repo, now, rows=[before])
    assert len(owed) == 1 and "not reported at all" in owed[0]


def test_a_measure_that_was_never_taken_before_is_not_a_fall(repo):
    before = {"cases": {"a" * 40: {"recall_at_20": 0.8}}}
    now = {"replayed": 1,
           "cases": {"a" * 40: {"precision_at_10": 0.1, "recall_at_20": 0.8}}}
    assert R.problems(repo, now, rows=[before]) == []


# ── an empty replay is not a clean one ──────────────────────────────────────


def test_a_replay_that_scored_nothing_is_a_failure(repo):
    now = run(repo, [], ranks_truth)
    owed = R.problems(repo, now, rows=[])
    assert len(owed) == 1 and "scored no cases at all" in owed[0]


def test_a_case_with_no_parent_cannot_be_replayed_and_is_not_picked(repo):
    """A root commit has no "before", so there is nothing to rebuild at."""
    orphan = case("c" * 40)
    orphan["parent"] = ""
    now = run(repo, [orphan], ranks_truth)
    assert now["replayed"] == 0


# ── the history ─────────────────────────────────────────────────────────────


def test_a_history_with_unreadable_rows_is_refused(repo):
    (repo / R.HISTORY).parent.mkdir(parents=True, exist_ok=True)
    (repo / R.HISTORY).write_text('{"cases": {}}\n{not json\n', encoding="utf-8")
    with pytest.raises(R.ReplayError) as exc:
        R.history(repo)
    assert "cannot say whether anything got worse" in str(exc.value)


def test_appending_is_the_only_way_the_history_grows(repo):
    rows = [case("d" * 40)]
    R.append(repo, run(repo, rows, ranks_truth, at="t1"))
    R.append(repo, run(repo, rows, ranks_truth, at="t2"))
    got = R.history(repo)
    assert [r["at"] for r in got] == ["t1", "t2"]


def test_the_history_lives_beside_the_other_metrics(repo):
    """`policy.yml` protects `.sdlc/metrics/**`, and a record that becomes a
    gate must not be writable by a run - item 29's lesson."""
    assert R.HISTORY.parts[:2] == (".sdlc", "metrics")


# ── scoring has one implementation ──────────────────────────────────────────


def test_scoring_goes_through_the_ab_runners_metrics(repo):
    """Two implementations of a metric is how the number in a report stops
    meaning the number in the gate."""
    truth = {("api", "a/b/Thing.java")}
    ranked = [("api", "a/b/Thing.java"), ("api", "z/z/Other.java")]
    assert R.score(ranked, truth)["recall_at_20"] == R.ab.recall_at(ranked, truth)
    assert R.score(ranked, truth)["precision_at_10"] == R.ab.precision_at(ranked, truth)


def test_a_weaker_but_real_ranking_scores_between_the_extremes(repo):
    rows = [case("e" * 40)]
    good = run(repo, rows, ranks_truth, at="t1")["detail"][0]
    part = run(repo, rows, ranks_partly, at="t2")["detail"][0]
    dead = run(repo, rows, ranks_nothing, at="t3")["detail"][0]
    assert good["recall_at_20"] > part["recall_at_20"] > dead["recall_at_20"]


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_reports_floors_and_exits_one_when_unmet(repo, capsys):
    store(repo, [case(sha_of(i), "defect") for i in range(2)])
    assert R.main(["--target", str(repo), "--floors"]) == 1
    assert "defect: 2 of 10" in capsys.readouterr().out


def test_the_cli_refuses_a_replay_with_no_workspace(repo, capsys):
    store(repo, [case("f" * 40)])
    assert R.main(["--target", str(repo), "--replay"]) == 2
    assert "needs --workspace" in capsys.readouterr().err


def test_the_cli_reports_floors_as_data(repo, capsys):
    store(repo, [case(sha_of(i), "review") for i in range(3)])
    R.main(["--target", str(repo), "--floors", "--json"])
    found = json.loads(capsys.readouterr().out)
    assert found["classes"]["review"] == 3 and found["met"]["review"] is False
