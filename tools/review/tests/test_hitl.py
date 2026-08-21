"""Item 27 — HITL instrumentation: review time and finding acceptance, captured.

The M4 gate "median review < 15 min" reads what this records, so the pins are
about the DATA's honesty: durations from a held clock, abandoned sessions
recorded rather than deleted (dropping the sessions that went badly biases the
exact median the gate reads), and — the anti-Goodhart posture as a schema fact —
no reviewer identity anywhere in a row.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.review.hitl as H  # noqa: E402


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return tmp_path


class Clock:
    """A held clock: each call returns the next stamp."""

    def __init__(self, *stamps: str):
        self.stamps = list(stamps)

    def __call__(self) -> str:
        return self.stamps.pop(0)


def rows_of(repo: Path) -> list[dict]:
    reviews, _ = H.paths(repo)
    return [json.loads(line) for line
            in reviews.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── the session lifecycle ─────────────────────────────────────────────────────


def test_begin_opens_a_session_with_a_utc_stamp(repo, monkeypatch):
    monkeypatch.setattr(H, "_now", Clock("2026-08-21T10:00:00+00:00"))
    message = H.begin(repo, component="orders")
    assert "recording" in message
    session = H.load_pending(repo)
    assert session["started_at"] == "2026-08-21T10:00:00+00:00"
    assert session["component"] == "orders"
    assert session["findings"] == []


def test_a_second_begin_refuses_and_names_the_honest_exits(repo, monkeypatch):
    monkeypatch.setattr(H, "_now", Clock("2026-08-21T10:00:00+00:00"))
    H.begin(repo)
    message = H.begin(repo)
    assert message.startswith("!!")
    assert "--end" in message and "--abandon" in message
    assert "data" in message, "the refusal teaches why abandoning beats deleting"


def test_findings_accumulate_in_the_open_session(repo, monkeypatch):
    monkeypatch.setattr(H, "_now", Clock("2026-08-21T10:00:00+00:00"))
    H.begin(repo)
    H.finding(repo, "github-pat", "prepass", "accepted", severity="ERROR",
              path="src/config.py", line=2)
    message = H.finding(repo, "stale-contract", "graph", "rejected")
    assert "2 finding(s)" in message
    session = H.load_pending(repo)
    assert session["findings"][0] == {"id": "github-pat", "source": "prepass",
                                      "disposition": "accepted",
                                      "severity": "ERROR",
                                      "path": "src/config.py", "line": 2}


def test_a_finding_without_an_open_session_is_refused(repo):
    assert H.finding(repo, "x", "agent", "accepted").startswith("!!")


def test_end_writes_one_row_with_the_held_clocks_duration(repo, monkeypatch):
    monkeypatch.setattr(H, "_now", Clock("2026-08-21T10:00:00+00:00",
                                         "2026-08-21T10:12:30+00:00"))
    H.begin(repo, component="orders", diff_files=7)
    H.finding(repo, "a", "prepass", "accepted")
    H.finding(repo, "b", "graph", "rejected")
    H.finding(repo, "c", "agent", "accepted")
    message = H.end(repo, "approved")
    assert "12.5 min" in message
    (row,) = rows_of(repo)
    assert row["kind"] == "review" and row["schema_version"] == H.SCHEMA_VERSION
    assert row["duration_s"] == 750
    assert row["verdict"] == "approved" and row["diff_files"] == 7
    assert row["counts"] == {"by_disposition": {"accepted": 2, "rejected": 1},
                             "by_source": {"agent": 1, "graph": 1, "prepass": 1}}
    assert H.load_pending(repo) is None, "the session closed"


def test_end_without_an_open_session_is_refused(repo):
    assert H.end(repo, "approved").startswith("!!")


def test_an_abandoned_review_is_a_row_not_a_deletion(repo, monkeypatch):
    """Dropping the sessions that went badly would bias the exact median the
    M4 gate reads — the failure mode this row exists to prevent."""
    monkeypatch.setattr(H, "_now", Clock("2026-08-21T10:00:00+00:00",
                                         "2026-08-21T10:40:00+00:00"))
    H.begin(repo)
    H.finding(repo, "a", "human", "deferred")
    message = H.abandon(repo)
    assert "RECORDED" in message
    (row,) = rows_of(repo)
    assert row["kind"] == "review-abandoned"
    assert row["findings_so_far"] == 1
    assert H.load_pending(repo) is None


def test_the_log_is_append_only_across_sessions(repo, monkeypatch):
    monkeypatch.setattr(H, "_now", Clock(
        "2026-08-21T10:00:00+00:00", "2026-08-21T10:05:00+00:00",
        "2026-08-21T11:00:00+00:00", "2026-08-21T11:20:00+00:00"))
    H.begin(repo)
    H.end(repo, "approved")
    H.begin(repo)
    H.end(repo, "rework")
    assert [r["duration_s"] for r in rows_of(repo)] == [300, 1200]


# ── the anti-Goodhart schema fact ─────────────────────────────────────────────


def test_no_row_carries_reviewer_identity(repo, monkeypatch):
    """By construction, not by policy alone: a field that exists gets queried.
    The M4 policy layer adds view rules; v1 removes the temptation at capture."""
    monkeypatch.setattr(H, "_now", Clock("2026-08-21T10:00:00+00:00",
                                         "2026-08-21T10:01:00+00:00"))
    H.begin(repo, component="orders")
    H.finding(repo, "a", "human", "accepted")
    H.end(repo, "approved")
    (row,) = rows_of(repo)
    assert set(row) == {"schema_version", "kind", "started_at", "finished_at",
                        "duration_s", "verdict", "component", "findings",
                        "counts"}
    blob = json.dumps(row).lower()
    for banned in ("user", "author", "email", "login", "reviewer"):
        assert banned not in blob


# ── the aggregate view ────────────────────────────────────────────────────────


def test_show_reports_median_and_acceptance_by_source(repo, monkeypatch):
    stamps = ["2026-08-21T10:00:00+00:00", "2026-08-21T10:05:00+00:00",
              "2026-08-21T11:00:00+00:00", "2026-08-21T11:10:00+00:00",
              "2026-08-21T12:00:00+00:00", "2026-08-21T12:30:00+00:00"]
    monkeypatch.setattr(H, "_now", Clock(*stamps))
    H.begin(repo)
    H.finding(repo, "a", "prepass", "accepted")
    H.finding(repo, "b", "prepass", "rejected")
    H.end(repo, "approved")
    H.begin(repo)
    H.finding(repo, "c", "graph", "accepted")
    H.end(repo, "approved")
    H.begin(repo)
    H.end(repo, "rework")
    out = H.show(repo)
    assert "reviews: 3 completed, 0 abandoned" in out
    assert "median duration: 10.0 min" in out
    assert "prepass: 1/2 accepted" in out
    assert "graph: 1/1 accepted" in out


def test_show_is_honest_about_nothing_recorded(repo, monkeypatch):
    assert "no reviews recorded" in H.show(repo)
    monkeypatch.setattr(H, "_now", Clock("2026-08-21T10:00:00+00:00",
                                         "2026-08-21T10:01:00+00:00"))
    H.begin(repo)
    H.abandon(repo)
    assert "no completed reviews recorded (1 abandoned)" in H.show(repo)


# ── the CLI ───────────────────────────────────────────────────────────────────


def test_the_cli_round_trips_a_session(repo, monkeypatch, capsys):
    monkeypatch.setattr(H, "_now", Clock("2026-08-21T10:00:00+00:00",
                                         "2026-08-21T10:16:00+00:00"))
    assert H.main(["--target", str(repo), "--begin", "--component", "orders"]) == 0
    assert H.main(["--target", str(repo), "--finding", "a", "--source", "prepass",
                   "--disposition", "accepted"]) == 0
    assert H.main(["--target", str(repo), "--end", "approved"]) == 0
    assert "16.0 min" in capsys.readouterr().out
    assert H.main(["--target", str(repo), "--show"]) == 0
    assert "median duration: 16.0 min" in capsys.readouterr().out


def test_the_cli_refuses_ambiguity_and_bad_input(repo, tmp_path, capsys):
    assert H.main(["--target", str(tmp_path / "nope"), "--show"]) == 2
    capsys.readouterr()
    assert H.main(["--target", str(repo)]) == 2
    assert "exactly one of" in capsys.readouterr().out
    assert H.main(["--target", str(repo), "--begin", "--show"]) == 2
    capsys.readouterr()
    assert H.main(["--target", str(repo), "--finding", "x"]) == 2
    assert "--source and --disposition" in capsys.readouterr().out
    assert H.main(["--target", str(repo), "--end", "approved"]) == 2, \
        "a refusal message must land as exit 2, not a green exit"


def test_the_cli_rejects_unknown_sources_and_verdicts(repo):
    with pytest.raises(SystemExit):
        H.main(["--target", str(repo), "--finding", "x", "--source", "psychic",
                "--disposition", "accepted"])
    with pytest.raises(SystemExit):
        H.main(["--target", str(repo), "--end", "maybe"])
