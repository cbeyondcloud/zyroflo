"""Item 31 — the run ledger: one folder per run, one state.json inside it.

Pins the three rules the record depends on: a terminal run does not move, every
transition leaves a history entry, and an artifact is its hash at the moment it
was recorded (so a later edit is visible rather than assumed away — the
property item 32's approvals will rest on).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import state as L  # noqa: E402


@pytest.fixture
def target(tmp_path: Path) -> Path:
    (tmp_path / ".sdlc").mkdir(parents=True)
    return tmp_path


def _run(target: Path, title: str = "a change worth making") -> dict:
    return L.create(target, "requirement", title)


# ── opening a run ────────────────────────────────────────────────────────────


def test_a_new_run_starts_at_p0_running_with_a_versioned_schema(target):
    st = _run(target)
    on_disk = json.loads((L.run_dir(target, st["run_id"]) / "state.json")
                         .read_text(encoding="utf-8"))
    assert on_disk == st
    assert on_disk["schema_version"] == L.SCHEMA_VERSION
    assert on_disk["phase"] == "P0" and on_disk["status"] == "running"
    assert on_disk["history"][0]["note"] == "run opened"
    assert on_disk["harness_version"], "the run records which harness drove it"


def test_an_unknown_kind_and_an_empty_title_are_both_refused(target):
    with pytest.raises(ValueError, match="unknown kind"):
        L.create(target, "epic", "x")
    with pytest.raises(ValueError, match="needs a title"):
        L.create(target, "requirement", "   ")


def test_a_run_never_reuses_a_folder(target):
    st = _run(target)
    with pytest.raises(RuntimeError, match="already exists"):
        L.create(target, "defect", "another", run_id=st["run_id"])


def test_an_archived_id_is_still_taken(target):
    """Reusing an archived id would interleave two runs in one history."""
    st = _run(target)
    L.advance(target, st["run_id"], status="closed")
    L.archive(target, st["run_id"])
    with pytest.raises(RuntimeError, match="already exists"):
        L.create(target, "requirement", "reuse", run_id=st["run_id"])


def test_run_ids_sort_by_time_and_are_stable_for_a_title():
    early = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    late = datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc)
    assert L.new_run_id("same", early) < L.new_run_id("same", late)
    assert L.new_run_id("same", early) == L.new_run_id(" same ", early)
    assert L.new_run_id("other", early) != L.new_run_id("same", early)


# ── moving ───────────────────────────────────────────────────────────────────


def test_advance_moves_and_records_why(target):
    st = _run(target)
    moved = L.advance(target, st["run_id"], phase="P2",
                      status="awaiting_approval", note="G1")
    assert (moved["phase"], moved["status"]) == ("P2", "awaiting_approval")
    assert moved["history"][-1]["note"] == "G1"
    assert moved["updated_at"] >= moved["created_at"]


def test_moving_backwards_is_legal(target):
    """P5 failures loop to P4 and re-trigger G3; a ledger that forbade it would
    be lying about the lifecycle it records."""
    st = _run(target)
    L.advance(target, st["run_id"], phase="P5")
    back = L.advance(target, st["run_id"], phase="P4", note="tests failed")
    assert back["phase"] == "P4"


def test_a_terminal_run_does_not_move(target):
    for final in ("closed", "rejected"):
        st = L.create(target, "requirement", f"terminal {final}")
        L.advance(target, st["run_id"], status=final)
        with pytest.raises(RuntimeError, match="terminal run does not move"):
            L.advance(target, st["run_id"], phase="P3")


def test_advance_refuses_nonsense(target):
    st = _run(target)
    with pytest.raises(ValueError, match="unknown phase"):
        L.advance(target, st["run_id"], phase="P99")
    with pytest.raises(ValueError, match="unknown status"):
        L.advance(target, st["run_id"], status="probably-fine")
    with pytest.raises(ValueError, match="needs a phase"):
        L.advance(target, st["run_id"])


def test_loading_a_missing_or_corrupt_run_says_which(target):
    with pytest.raises(RuntimeError, match="no run"):
        L.load(target, "nope")
    st = _run(target)
    (L.run_dir(target, st["run_id"]) / "state.json").write_text("{oops",
                                                                encoding="utf-8")
    with pytest.raises(RuntimeError, match="not readable JSON"):
        L.load(target, st["run_id"])


# ── artifacts are their hashes ───────────────────────────────────────────────


def test_recording_an_artifact_stores_its_hash(target):
    st = _run(target)
    (L.run_dir(target, st["run_id"]) / "intake.md").write_text("hello",
                                                               encoding="utf-8")
    after = L.record_artifact(target, st["run_id"], "intake.md")
    assert after["artifacts"]["intake.md"].startswith("sha256:")
    assert L.verify_artifacts(target, st["run_id"]) == []


def test_recording_something_that_is_not_there_is_refused(target):
    st = _run(target)
    with pytest.raises(RuntimeError, match="record what exists"):
        L.record_artifact(target, st["run_id"], "absent.md")


@pytest.mark.parametrize("wreck, expect", [
    (lambda p: p.write_text("edited", encoding="utf-8"), "changed since"),
    (lambda p: p.unlink(), "recorded, now missing"),
])
def test_a_changed_or_deleted_artifact_is_named(target, wreck, expect):
    st = _run(target)
    path = L.run_dir(target, st["run_id"]) / "plan.md"
    path.write_text("original", encoding="utf-8")
    L.record_artifact(target, st["run_id"], "plan.md")
    wreck(path)
    assert expect in " ".join(L.verify_artifacts(target, st["run_id"]))


# ── the change-folder lifecycle ──────────────────────────────────────────────


def test_archiving_a_run_still_in_flight_is_refused(target):
    st = _run(target)
    L.advance(target, st["run_id"], status="awaiting_approval")
    with pytest.raises(RuntimeError, match="still waiting"):
        L.archive(target, st["run_id"])
    assert L.archive(target, st["run_id"], force=True).is_dir(), \
        "the operator may override - and the refusal names the flag"


def test_a_closed_run_moves_to_the_archive_and_is_still_findable(target):
    st = _run(target)
    L.advance(target, st["run_id"], status="closed", note="done")
    dest = L.archive(target, st["run_id"])
    assert dest.parent.name == "archive"
    assert not (L.runs_dir(target) / st["run_id"]).exists()
    assert L.load(target, st["run_id"])["status"] == "closed", \
        "run_dir resolves an archived run, so the record survives closure"


def test_archiving_twice_refuses_rather_than_merging(target):
    st = _run(target)
    L.advance(target, st["run_id"], status="closed")
    L.archive(target, st["run_id"])
    (L.runs_dir(target) / st["run_id"]).mkdir(parents=True)
    (L.runs_dir(target) / st["run_id"] / "state.json").write_text(
        json.dumps({"run_id": st["run_id"], "status": "closed"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing to merge"):
        L.archive(target, st["run_id"])


def test_list_runs_shows_active_and_archived(target):
    live = L.create(target, "defect", "still going")
    done = L.create(target, "requirement", "finished")
    L.advance(target, done["run_id"], status="closed")
    L.archive(target, done["run_id"])
    rows = {r["run_id"]: r for r in L.list_runs(target)}
    assert rows[live["run_id"]]["where"] == "active"
    assert rows[done["run_id"]]["where"] == "archived"
    assert rows[live["run_id"]]["kind"] == "defect"
    assert "archive" not in rows, "the archive directory is not itself a run"


def test_state_json_is_written_lf_and_sorted(target):
    st = _run(target)
    raw = (L.run_dir(target, st["run_id"]) / "state.json").read_bytes()
    assert b"\r\n" not in raw, "one record, appended from Windows and from CI alike"
    keys = list(json.loads(raw.decode("utf-8")))
    assert keys == sorted(keys), "sorted keys keep the committed diff readable"


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_opens_shows_and_lists(target, capsys):
    assert L.main(["--target", str(target), "--new", "--kind", "defect",
                   "--title", "crash on boot"]) == 0
    rid = capsys.readouterr().out.split()[1].rstrip(":")
    assert L.main(["--target", str(target), "--show", rid]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "crash on boot"
    assert L.main(["--target", str(target), "--list"]) == 0
    assert rid in capsys.readouterr().out


def test_cli_verify_exits_one_when_an_artifact_moved(target, capsys):
    st = _run(target)
    path = L.run_dir(target, st["run_id"]) / "a.md"
    path.write_text("one", encoding="utf-8")
    L.record_artifact(target, st["run_id"], "a.md")
    assert L.main(["--target", str(target), "--verify", st["run_id"]]) == 0
    path.write_text("two", encoding="utf-8")
    assert L.main(["--target", str(target), "--verify", st["run_id"]]) == 1
    assert "changed since" in capsys.readouterr().out


def test_cli_reports_refusals_as_exit_two(target, capsys):
    assert L.main(["--target", str(tmp := target / "nowhere"), "--list"]) == 2
    assert str(tmp) in capsys.readouterr().out
    assert L.main(["--target", str(target), "--advance", "ghost",
                   "--phase", "P1"]) == 2
    assert "no run" in capsys.readouterr().out
    st = _run(target)
    assert L.main(["--target", str(target), "--record", st["run_id"]]) == 2
    assert "--artifact" in capsys.readouterr().out


# -- item 139: the bytes behind a pin are kept, so a pin can be undone -------


def _pin(target: Path, run_id: str, rel: str, body: str) -> str:
    (L.run_dir(target, run_id) / rel).write_text(body, encoding="utf-8",
                                                 newline="\n")
    return L.record_artifact(target, run_id, rel)["artifacts"][rel]


def test_the_bytes_a_pin_is_about_are_kept(target):
    """**Item 139, found by the reject-to-re-scope loop WORKING.** Item 32 pins
    a gate to exact bytes so an approval cannot silently come to mean something
    else, and kept no copy of them - so a re-run answering a rejection rewrote
    `20-scope.md` from 1,801 bytes to 569 and nothing anywhere could restore
    it. The pin detected the loss and could not undo it."""
    rid = _run(target)["run_id"]
    digest = _pin(target, rid, "20-scope.md", "the first draft\n")
    kept = L.kept_path(target, rid, digest)
    assert kept.is_file(), "the pin kept nothing"
    assert kept.read_text(encoding="utf-8") == "the first draft\n"


def test_a_rewritten_artifact_keeps_both_versions(target):
    """Content-addressed, so the draft a gate saw survives the draft that
    replaced it - which is the whole case: every re-run after a rejection
    overwrites the one document a human has their attention on."""
    rid = _run(target)["run_id"]
    first = _pin(target, rid, "20-scope.md", "the first draft\n")
    second = _pin(target, rid, "20-scope.md", "a much shorter one\n")
    assert first != second
    assert L.kept_path(target, rid, first).read_text(
        encoding="utf-8") == "the first draft\n"
    assert L.kept_path(target, rid, second).read_text(
        encoding="utf-8") == "a much shorter one\n"


def test_recording_unchanged_bytes_keeps_one_copy(target):
    """Keyed by digest rather than by name, so re-recording is free. The
    measured cost of the whole store is 405,535 bytes across five real runs,
    and that number only holds while identical content is not copied twice."""
    rid = _run(target)["run_id"]
    digest = _pin(target, rid, "20-scope.md", "the first draft\n")
    before = L.kept_path(target, rid, digest).stat().st_mtime_ns
    assert _pin(target, rid, "20-scope.md", "the first draft\n") == digest
    assert L.kept_path(target, rid, digest).stat().st_mtime_ns == before
    folder = L.run_dir(target, rid) / L.KEPT_DIR
    assert len(list(folder.iterdir())) == 1


def test_restoring_puts_the_draft_back_and_records_nothing(target):
    """**Deliberately not re-pinned.** Putting a destroyed draft back is the
    operator looking at what was lost; re-recording it is a separate act with
    a gate's opinion attached, and doing it here would silently move what the
    run claims to have delivered."""
    rid = _run(target)["run_id"]
    first = _pin(target, rid, "20-scope.md", "the first draft\n")
    second = _pin(target, rid, "20-scope.md", "a much shorter one\n")
    L.restore(target, rid, "20-scope.md", first)
    assert (L.run_dir(target, rid) / "20-scope.md").read_text(
        encoding="utf-8") == "the first draft\n"
    assert L.load(target, rid)["artifacts"]["20-scope.md"] == second


def test_a_directory_artifact_keeps_its_map_and_says_it_is_not_a_restore(
        target):
    """**The limit, stated rather than hidden.** `40-impl/` is a directory
    artifact and a diff the run's own branch already holds, so its per-file
    digest map is kept instead of its content - WHICH file moved is answerable,
    restoring it is not, and the refusal says so rather than failing vaguely.
    """
    rid = _run(target)["run_id"]
    where = L.run_dir(target, rid) / "40-impl"
    where.mkdir(parents=True, exist_ok=True)
    (where / "change.patch").write_text("--- a\n+++ b\n", encoding="utf-8",
                                        newline="\n")
    digest = L.record_artifact(target, rid, "40-impl")["artifacts"]["40-impl"]
    tree = L.kept_path(target, rid, digest)
    tree = tree.with_name(tree.name + ".tree")
    assert tree.is_file(), "a directory kept nothing at all"
    assert "change.patch" in json.loads(tree.read_text(encoding="utf-8"))
    with pytest.raises(RuntimeError) as exc:
        L.restore(target, rid, "40-impl", digest)
    assert ".tree" in str(exc.value) and "not a restore" in str(exc.value)


def test_the_restore_is_reachable_as_a_command_a_person_can_run(target):
    """**Item 96's rule, and item 102's lesson.** A store nobody can read back
    is a wall of digests - the class this project has found most, worst in
    item 102 where 7,502 of them shipped into every adopter's repository and
    no code consumed one. So the reader is spawned, not called."""
    rid = _run(target)["run_id"]
    first = _pin(target, rid, "20-scope.md", "the first draft\n")
    _pin(target, rid, "20-scope.md", "a much shorter one\n")

    def spawn(*args):
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "sdlc" / "state.py"),
             "--target", str(target), *args],
            capture_output=True, text=True, timeout=120)

    missing = spawn("--restore", rid, "--artifact", "20-scope.md")
    assert missing.returncode == 2 and "--digest" in missing.stdout

    said = spawn("--restore", rid, "--artifact", "20-scope.md",
                 "--digest", first)
    assert said.returncode == 0, said.stdout + said.stderr
    assert (L.run_dir(target, rid) / "20-scope.md").read_text(
        encoding="utf-8") == "the first draft\n"


def test_a_draft_that_was_overwritten_is_still_somewhere_in_the_run(target):
    """**The behavioural red, naming no new API.** Item 139's whole complaint
    is that after a re-run overwrote `20-scope.md` NOTHING anywhere held the
    old bytes - not the run folder, not `feedback.jsonl` (a correction COUNT),
    not `state.json` (a hash). So the question is asked of the run folder
    itself, which is the thing an operator would search.
    """
    rid = _run(target)["run_id"]
    _pin(target, rid, "20-scope.md", "the first draft, with its citations\n")
    _pin(target, rid, "20-scope.md", "a much shorter one\n")
    folder = L.run_dir(target, rid)
    survives = [p for p in folder.rglob("*") if p.is_file()
                and b"the first draft, with its citations" in p.read_bytes()]
    assert survives, (
        "the draft a gate was about is gone from the run entirely - the pin "
        "detected the loss and nothing can undo it")


# ── a run whose record cannot be read ────────────────────────────────────────


def _spawn_state(target: Path, *args) -> subprocess.CompletedProcess:
    """The ledger CLI as a person meets it. A CLI's contract is a process: an
    in-process call cannot tell a refusal from a traceback, and every defect
    below reached its operator as a traceback."""
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "state.py"),
         "--target", str(target), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)


def test_a_record_that_is_not_an_object_is_a_row_beside_the_healthy_runs(target):
    """`load` guards the shape of what `json.loads` returned; `list_runs` did
    not, so one run folder holding `[]` answered `--list` with an
    `AttributeError` — which `main`'s `except (ValueError, RuntimeError)`
    cannot catch. Could-not-read is a row with a reason, like absent already
    was."""
    healthy = _run(target, "a run that is fine")["run_id"]
    broken = _run(target, "a run whose record is not an object")["run_id"]
    (L.run_dir(target, broken) / "state.json").write_text("[]", encoding="utf-8")

    p = _spawn_state(target, "--list")
    out = p.stdout + p.stderr
    assert "Traceback" not in out, out
    assert p.returncode == 0, out
    assert healthy in out and "a run that is fine" in out
    assert broken in out and "unreadable" in out, out

    rows = {r["run_id"]: r for r in L.list_runs(target)}
    assert rows[broken]["kind"] == "unreadable"
    assert "state object" in rows[broken]["unreadable"]


def test_a_new_intake_beside_an_unreadable_run_says_it_could_not_look(tmp_path):
    """The blast radius: `intake.duplicates` lists runs before `create`, so one
    damaged run folder made EVERY future P0 traceback. It cannot claim the
    specification is or is not a duplicate of a run it could not read, so it
    says which run it could not read."""
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    broken = L.create(repo, "requirement", "an older run")["run_id"]
    (L.run_dir(repo, broken) / "state.json").write_text("[]", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, timeout=180)

    spec = tmp_path / "spec.md"
    spec.write_text("# Add a health endpoint\n\nThe service should expose /health "
                    "returning 200 so the load balancer can probe it.\n",
                    encoding="utf-8")
    p = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "intake.py"),
         "--target", str(repo), "--spec", str(spec), "--allow-dirty"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    out = p.stdout + p.stderr
    assert "Traceback" not in out, out
    assert broken in out and "could not read" in out.lower(), out


def test_one_damaged_record_does_not_hide_every_healthy_run(target):
    """A truncated `state.json` aborted the whole listing at rc 2 and the
    healthy runs were never printed — the operator is told least at the moment
    something is wrong."""
    healthy = _run(target, "a run that is fine")["run_id"]
    broken = _run(target, "a run whose record was cut off")["run_id"]
    (L.run_dir(target, broken) / "state.json").write_text('{"run_id": "x"',
                                                          encoding="utf-8")
    p = _spawn_state(target, "--list")
    out = p.stdout + p.stderr
    assert "Traceback" not in out, out
    assert p.returncode == 0, out
    assert healthy in out, "one damaged record hid every healthy run"
    assert broken in out and "unreadable" in out, out


def test_a_listing_that_could_read_no_run_at_all_exits_two(target):
    """Reported, and NOT reported as clean: if nothing could be read the
    listing is worthless, so the exit code says so."""
    only = _run(target, "the only run there is")["run_id"]
    (L.run_dir(target, only) / "state.json").write_text("[]", encoding="utf-8")
    p = _spawn_state(target, "--list")
    out = p.stdout + p.stderr
    assert p.returncode == 2, out
    assert only in out and "unreadable" in out, out


def test_a_repository_with_no_runs_is_not_a_ledger_that_could_not_be_read(target):
    """The positive control for the row above: absent is not unreadable."""
    p = _spawn_state(target, "--list")
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert "no runs" in out, out


# ── one artifact, one key, inside the run folder ─────────────────────────────


def test_one_artifact_in_two_spellings_is_one_key(target):
    """`record_artifact` normalised AFTER keying while `main` re-indexed with
    the raw argument, so the Windows spelling of a path — on a platform this
    project calls first-class — answered with a `KeyError` traceback."""
    rid = _run(target)["run_id"]
    d = L.run_dir(target, rid) / "10-intake"
    d.mkdir(parents=True)
    (d / "00-intake.json").write_text("{}\n", encoding="utf-8")

    def keys() -> list[str]:
        return sorted(json.loads((L.run_dir(target, rid) / "state.json")
                                 .read_text(encoding="utf-8"))["artifacts"])

    back = _spawn_state(target, "--record", rid, "--artifact",
                        "10-intake\\00-intake.json")
    out = back.stdout + back.stderr
    assert "Traceback" not in out, out
    assert back.returncode == 0, out
    assert "10-intake/00-intake.json" in out, out
    assert keys() == ["10-intake/00-intake.json"]

    fwd = _spawn_state(target, "--record", rid, "--artifact",
                       "10-intake/00-intake.json")
    assert fwd.returncode == 0, fwd.stdout + fwd.stderr
    assert keys() == ["10-intake/00-intake.json"], "one artifact became two rows"


def test_an_artifact_path_outside_the_run_folder_is_refused(target):
    """`rel` was joined onto the run folder unvalidated, so an ABSOLUTE path
    was recorded verbatim into `state.json` — a committed file — carrying a
    machine path with it."""
    rid = _run(target)["run_id"]
    outside = target / "outside.txt"
    outside.write_text("not part of the run\n", encoding="utf-8")

    def artifacts() -> dict:
        return json.loads((L.run_dir(target, rid) / "state.json")
                          .read_text(encoding="utf-8"))["artifacts"]

    absolute = _spawn_state(target, "--record", rid, "--artifact", str(outside))
    out = absolute.stdout + absolute.stderr
    assert "Traceback" not in out, out
    assert absolute.returncode == 2, out
    assert "outside.txt" in out, "the refusal does not name the path it refused"
    assert artifacts() == {}, "an absolute path reached the committed record"

    up = _spawn_state(target, "--record", rid, "--artifact", "../outside.txt")
    out = up.stdout + up.stderr
    assert "Traceback" not in out, out
    assert up.returncode == 2, out
    assert "outside.txt" in out, out
    assert artifacts() == {}, "a path that climbs out reached the record"


def test_a_restore_does_not_write_outside_the_run_folder(target):
    """Item 139's door swung both ways: `--restore` put the kept bytes at any
    path the caller named, including one outside the run folder entirely."""
    rid = _run(target)["run_id"]
    digest = _pin(target, rid, "20-scope.md", "the first draft\n")
    victim = L.run_dir(target, rid).parent / "victim.txt"
    assert not victim.exists()

    p = _spawn_state(target, "--restore", rid, "--artifact", "../victim.txt",
                     "--digest", digest)
    out = p.stdout + p.stderr
    assert "Traceback" not in out, out
    assert p.returncode == 2, out
    assert "victim.txt" in out, "the refusal does not name the path it refused"
    assert not victim.exists(), "a restore wrote a file outside the run folder"
