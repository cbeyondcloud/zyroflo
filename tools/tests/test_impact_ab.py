"""The A/B runner, pinned clause by clause to the locked judging protocol.

Item 18. `docs/m0/judging-protocol.md` fixed the selection rules, the seed rule, the
blinding, the metrics and the pass threshold before any impact code existed. These tests
transcribe those clauses one per test, so a drift between the runner and the protocol
fails a named sentence rather than a vibe. The end-to-end tests run the whole machinery
against a synthetic two-repo estate whose merge history is built here, commit by commit
— including the proof that the graph is built at the commit's FIRST PARENT, because a
tool that has been shown the answer is not being tested on anything.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import impact_ab as AB  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


# -- section 2: what qualifies ------------------------------------------------------------

MDIRS = {"", "m1", "m2"}


def test_a_single_module_commit_does_not_qualify():
    ok, why = AB.qualifies("add feature", ["m1/A.java", "m1/B.java", "m1/C.java"], MDIRS)
    assert not ok and "single module" in why


def test_fewer_than_three_non_test_files_does_not_qualify():
    files = ["m1/A.java", "m2/B.java", "m1/test/T.java", "m2/src/test/U.java"]
    ok, why = AB.qualifies("add feature", files, MDIRS)
    assert not ok and "criterion 3" in why


def test_a_dependency_bump_subject_does_not_qualify():
    files = ["m1/A.java", "m2/B.java", "pom.xml"]
    ok, why = AB.qualifies("Bump libfoo from 1.2 to 1.3", files, MDIRS)
    assert not ok and "criterion 2" in why


def test_generated_files_only_does_not_qualify():
    files = ["m1/generated/A.java", "m2/generated/B.java", "m1/generated/C.java"]
    ok, why = AB.qualifies("regen stubs", files, MDIRS)
    assert not ok and "generated" in why


def test_a_documentation_only_merge_does_not_qualify():
    """Criterion 2 gained the documentation exclusion on 2026-08-21 (protocol
    section 7). The runner had not learned it: a docs-only merge cleared every
    other check - three non-test files, two modules, a subject matching no
    excluded word, a .md happily chosen as the seed - so a mechanical re-run of
    the walk would pick a commit the governing protocol forbids."""
    files = ["m1/README.md", "m2/GUIDE.rst", "m1/docs/notes.txt"]
    ok, why = AB.qualifies("document the calculator", files, MDIRS)
    assert not ok and "documentation-only" in why


def test_documentation_beside_code_still_qualifies():
    """'Documentation-only' means every non-test file, not any of them."""
    ok, why = AB.qualifies("wire it up and say so",
                           ["m1/A.java", "m2/B.java", "m1/README.md"], MDIRS)
    assert ok and why == ""


def test_the_runner_stamps_the_amended_protocol_version():
    """The output cited '2026-08-06' after the 2026-08-21 amendment, misdating
    its own authority on every record it writes."""
    assert "2026-08-21" in AB.__doc__
    src = (Path(AB.__file__).read_text(encoding="utf-8"))
    assert '"docs/m0/judging-protocol.md (2026-08-05, amended 2026-08-06, 2026-08-21)"' \
        in src


def test_a_real_cross_module_change_qualifies():
    ok, why = AB.qualifies("wire the calculator through both halves",
                           ["m1/A.java", "m2/B.java", "pom.xml"], MDIRS)
    assert ok and why == ""


# -- section 3.1: seed and ground truth ---------------------------------------------------


def test_seed_is_the_first_non_config_non_test_file_in_diff_order():
    files = ["conf/app.yml", "m1/test/T.java", "m1/Impl.java", "m2/Other.java"]
    seed, truth = AB.seed_and_truth(files)
    assert seed == "m1/Impl.java"
    assert truth == ["conf/app.yml", "m2/Other.java"], \
        "TRUTH is TOUCHED minus SEED — the config file stays in the answer key"


def test_an_all_config_commit_has_no_seed_and_is_rejected():
    ok, why = AB.qualifies("tune things", ["a/x.yml", "b/y.yml", "c.properties"],
                           {"a", "b", ""})
    assert not ok and "3.1" in why


# -- section 3.5: metrics and the fixed threshold -----------------------------------------

T = {("r", "a"), ("r", "b"), ("r", "c"), ("r", "d")}


def test_recall_at_k_is_hits_over_truth():
    ranked = [("r", "a"), ("r", "x"), ("r", "b")]
    assert AB.recall_at(ranked, T, k=20) == 0.5
    assert AB.recall_at(ranked, T, k=2) == 0.25
    assert AB.recall_at([], T) == 0.0


def test_precision_at_k_charges_every_slot():
    ranked = [("r", "a"), ("r", "x")]
    assert AB.precision_at(ranked, T, k=10) == 0.1, \
        "a short list is not excused its empty slots"


def test_first_true_positive_rank_is_one_based_or_none():
    assert AB.first_tp_rank([("r", "x"), ("r", "b")], T) == 2
    assert AB.first_tp_rank([("r", "x")], T) is None


def wins(a: float, b: float) -> dict:
    return {"recall_a": a, "recall_b": b}


def test_two_wins_and_a_small_third_loss_pass():
    v = AB.verdict([wins(0.5, 0.6), wins(0.2, 0.4), wins(0.5, 0.35)])
    assert v["verdict"] == "PASS" and v["wins_b"] == 2


def test_two_wins_but_a_catastrophic_third_loss_fail():
    v = AB.verdict([wins(0.5, 0.6), wins(0.2, 0.4), wins(0.5, 0.25)])
    assert v["verdict"] == "FAIL", "the -0.20 floor exists so a tool that is " \
                                   "confidently empty once cannot pass on two hairs"


def test_a_tie_is_not_a_beat():
    v = AB.verdict([wins(0.5, 0.5), wins(0.5, 0.5), wins(0.2, 0.4)])
    assert v["verdict"] == "FAIL" and v["wins_b"] == 1


# -- section 3.3: blinding ----------------------------------------------------------------


def test_the_flip_swaps_the_mapping_and_the_scores_follow():
    a, b, truth = [("r", "miss")], [("r", "hit")], {("r", "hit")}
    heads = AB.blind_and_score(a, b, truth, flip=0)
    tails = AB.blind_and_score(a, b, truth, flip=1)
    assert heads["mapping"] == {"X": "A", "Y": "B"}
    assert tails["mapping"] == {"X": "B", "Y": "A"}
    assert heads["scores"]["Y"]["recall_at_20"] == 1.0
    assert tails["scores"]["X"]["recall_at_20"] == 1.0


# -- condition A mechanics ----------------------------------------------------------------


def test_grep_ranking_sums_hits_filters_tests_and_maps_aliases(tmp_path):
    def runner(term):
        return {"tok": [("alpha/src/A.java", 3), ("alpha/src/test/T.java", 9),
                        ("beta/x.txt", 1)],
                "tok2": [("alpha/src/A.java", 1)]}[term], True
    ranked, ok = AB.grep_rank(["tok", "tok2"], tmp_path,
                              {"alpha": "alpha", "beta": "beta"},
                              exclude=("alpha", "seed.java"), runner=runner)
    assert ranked == [("alpha", "src/A.java"), ("beta", "x.txt")], \
        "4 hits beat 1; the test file's 9 hits count for nothing"
    assert ok is True, "every term's search finished cleanly"


# -- a timed subprocess the clock kills ----------------------------------------------------
#
# A REAL `subprocess.TimeoutExpired`, so the helper and the module's own
# handling both run; only the hang is simulated, there being no portable way
# to make ripgrep or git hang on demand.


def _timeout_raiser(seconds, out, err):
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0] if a else "x",
                                        timeout=k.get("timeout", seconds),
                                        output=out, stderr=err)
    return raise_timeout


def test_a_hung_ripgrep_keeps_its_counts_and_says_they_are_partial(
        tmp_path, monkeypatch, capsys):
    """It raised past `grep_rank` and the A/B ranking died with a traceback.
    Now the counts ripgrep DID print survive - and a smaller number that looks
    like a real one is announced as partial rather than ranked in silence."""
    from tools import cli

    monkeypatch.setattr(cli.subprocess, "run", _timeout_raiser(
        600, "alpha/src/A.java:3\nbeta/x.txt:1\n", "rg: some/path: No such file"))
    rows, ok = AB._rg_counts(Path("rg"), "tok", tmp_path)
    assert rows == [("alpha/src/A.java", 3), ("beta/x.txt", 1)], rows
    assert ok is False, "a hang that kept partial counts still could not look"
    said = capsys.readouterr().err
    assert "did not finish within 600s" in said[:60], said
    assert "PARTIAL" in said and "'tok'" in said, said


# -- the vendored ripgrep's own exit code --------------------------------------------------
#
# THE REAL BINARY, deliberately. Every other test of this function hands `grep_rank` a
# dict runner or monkeypatches `cli.subprocess.run`, and no fake was ever going to
# produce the case that matters: ripgrep answering 2 while printing a PARTIAL count.
#
# MEASURED against the vendored rg (15.2.0) by driving it, not by reading its docs:
# 0 a match, 1 a clean no-match with empty stdout AND empty stderr, 2 an error — and
# 2 wins over both, so a loop with matches is `2` with counts on stdout and a loop
# without them is `2` with nothing. Exit 1 is therefore the only cheap silent answer.
#
# `third_party/bin/` is gitignored (`test_repo_contract.py` asserts no binary is ever
# tracked), so the binary is ABSENT until something fetches it. **T344: this comment used
# to say CI never fetches it, which was already false before it was written down.** CI's
# suite job fetches ripgrep today: `tools/third_party.py`'s `SPAWNED_BY_TESTS` is the fetch
# set, welded to `ci.yml`'s `--fetch --only` list by
# `test_ci_fetches_exactly_the_tools_some_test_spawns` in `test_third_party.py`, so this
# stops restating that fact by hand and checks it against that same set instead —
# `test_the_ripgrep_availability_comments_match_the_fetch_set` below fails BY NAME the day
# "rg" leaves it. So the absent branch below is exercised wherever the binary genuinely is
# missing — a plain `uv run pytest` before `--fetch` has run, most local dev shells, or a
# future change that drops "rg" from the fetch set — and it asserts the resolver's own
# refusal, naming the contract that goes unexercised there.


def _vendored_rg() -> Path | None:
    try:
        rg = AB.resolve_rg()
    except SystemExit:
        return None
    return rg if rg.exists() else None


def test_the_ripgrep_availability_comments_match_the_fetch_set():
    """T344: a prose claim about what CI fetches has gone stale three times in two days, in
    three different files, and this file's own comments (around `_vendored_rg` and the block
    before `_stub_rg`) were the pre-existing instance — false before the branch that made the
    other two false. Hand-repairing prose a fourth time would not fix the class, so this
    checks the claim against the one place the fact is actually welded, `tools/third_party.
    py`'s `SPAWNED_BY_TESTS`, which `test_ci_fetches_exactly_the_tools_some_test_spawns` (in
    `test_third_party.py`) already holds equal to `ci.yml`'s `--only` list.

    Not a re-implementation of that weld — a second one would be a second thing to keep
    honest. This only ties THIS file's claim to the SAME already-derived set, so if "rg" is
    ever dropped from it, this fails BY NAME and points at the two comments to update, instead
    of the two comments quietly going stale a fourth time.
    """
    from tools import third_party as tp
    assert "rg" in tp.SPAWNED_BY_TESTS, (
        "this file's comments around _vendored_rg and _stub_rg say CI fetches ripgrep, so "
        "test_the_vendored_ripgreps_exit_code_decides_the_three_answers normally runs its "
        "real-binary branch on the gate of record. 'rg' is no longer in SPAWNED_BY_TESTS: "
        "if that is an intentional change, update both comments to say CI lacks it again; "
        "if not, this is the regression to fix."
    )


def test_a_ripgrep_that_could_not_look_is_not_a_measured_zero(tmp_path, capsys):
    """A REAL could-not-start — no fake and no monkeypatch, just a binary that is
    not there, so the search never happened. Empty output from a search that never
    ran must not be handed back as a term nobody uses."""
    rows, ok = AB._rg_counts(tmp_path / "no-such-rg", "tok", tmp_path)
    said = capsys.readouterr().err
    assert rows == [] and ok is False, (rows, ok)
    assert "'tok'" in said and "NOT a measured absence" in said, said
    assert "PARTIAL" not in said, said


def test_the_vendored_ripgreps_exit_code_decides_the_three_answers(tmp_path, capsys):
    """A match, a no-match and a broken search are three answers, never one.

    The loop is not a hypothetical: `-L` is passed on purpose and the workspace this
    module builds is components joined by junctions, so a component that is an
    ancestor of the scratch root loops. Before this test, ripgrep said 2, printed a
    partial count, and the ranking used it in silence."""
    rg = _vendored_rg()
    if rg is None:                      # binary missing — see the comment above _vendored_rg
        with pytest.raises(SystemExit) as refused:
            AB.resolve_rg()
        assert "--fetch" in str(refused.value), refused.value
        return

    ws = tmp_path / "ws"
    (ws / "a").mkdir(parents=True)
    (ws / "a" / "file.txt").write_text("HITTERM\nHITTERM again\n", encoding="utf-8")

    rows, ok = AB._rg_counts(rg, "HITTERM", ws)
    assert rows == [("a/file.txt", 2)] and ok is True, (rows, ok)
    assert capsys.readouterr().err == "", "a plain match says nothing"

    rows, ok = AB._rg_counts(rg, "ZZNOSUCHTOKENZZ", ws)
    assert rows == [] and ok is True, (rows, ok)
    assert capsys.readouterr().err == "", \
        "a genuine no-match is the one answer that must stay cheap and silent"

    AB.make_link(ws, ws / "loop")       # the workspace's own shape, pointed at itself
    rows, ok = AB._rg_counts(rg, "HITTERM", ws)
    said = capsys.readouterr().err
    assert ("a/file.txt", 2) in rows and ok is False, (rows, ok)
    assert "PARTIAL" in said, said
    assert "loop" in said.lower(), \
        f"the reason `--no-messages` used to delete: {said!r}"

    rows, ok = AB._rg_counts(rg, "ZZNOSUCHTOKENZZ", ws)
    assert rows == [] and ok is False, (rows, ok)
    broke = capsys.readouterr().err
    assert "NOT a measured absence" in broke, broke
    assert "PARTIAL" not in broke, broke


# -- the same contract, on a machine with no vendored binary ---------------------------------
#
# The test above needs ripgrep. CI's suite job fetches it today (see the comment above
# `_vendored_rg`, and T344), so on the gate of record that test now normally runs its REAL
# branch rather than this one — this file used to claim the opposite. This half of the
# contract still needs covering wherever the binary genuinely is missing, which is what the
# tests below are for. MEASURED by substituting each half of this fix back out and running
# the file with the resolver blinded: with the binary present, *reading the exit code*,
# *splitting partial from failed*, and *`code not in (0, 1)` rather than `(0, 1, 2)`* are
# each killed; with it absent, only the split is — the other two survive, and the defect
# this whole change exists to fix ships green. A guard blinded by its environment is this
# project's could-not-look failure wearing a test's costume.
#
# `_rg_counts` takes the binary as an ARGUMENT, so the contract does not need ripgrep to be
# tested — it needs a process that exits the way ripgrep exits. This spawns one, and with
# `break_on` the stub READS its argv: it breaks (exit 2, a filesystem loop) when a chosen
# token is among the arguments it was spawned with, so a test can show one term's search
# breaking while another cleanly finds nothing — the argv-awareness the broken-vs-zero
# tests below rest on. (`--no-messages` is no longer passed at all: the source measured
# that it changed neither the exit code nor stdout and dropped it, so there is no such flag
# left for a stub to cover.)


def _stub_rg(d: Path, name: str, stdout: str, stderr: str, code: int,
             break_on: str | None = None) -> Path:
    """A REAL executable that prints what it is told and exits how it is told.
    No fake runner and no monkeypatch: `run_bounded` spawns this and reads a
    genuine exit status off the process, which is the whole point.

    `break_on` gives the stub REAL argv parsing: when that token appears among the
    arguments the process was spawned with (the search term is one of them), the
    stub exits 2 with a filesystem-loop message on stderr — ripgrep's could-not-look
    — instead of the fixed output. That is what lets a test show the invocation's
    argv changing the outcome, and it is how the broken-vs-zero end-to-end test
    forces one term's search to break while another cleanly finds nothing."""
    if os.name == "nt":
        stub = d / f"{name}.cmd"
        scan = ([":__scan",
                 'if "%~1"=="" goto __run',
                 f'if "%~1"=="{break_on}" (',
                 "echo rg: File system loop found 1>&2",
                 "exit /b 2",
                 ")",
                 "shift",
                 "goto __scan",
                 ":__run"] if break_on else [])
        body = (["@echo off"] + scan
                + [f"echo {line}" for line in stdout.splitlines()]
                + [f"echo {line} 1>&2" for line in stderr.splitlines()]
                + [f"exit /b {code}"])
        stub.write_text("\r\n".join(body) + "\r\n", encoding="ascii")
    else:
        stub = d / name
        scan = (['for __a in "$@"; do',
                 f'  if [ "x$__a" = "x{break_on}" ]; then',
                 "    printf '%s\\n' 'rg: File system loop found' >&2",
                 "    exit 2",
                 "  fi",
                 "done"] if break_on else [])
        body = (["#!/bin/sh"] + scan
                + [f"printf '%s\\n' '{line}'" for line in stdout.splitlines()]
                + [f"printf '%s\\n' '{line}' >&2" for line in stderr.splitlines()]
                + [f"exit {code}"])
        stub.write_text("\n".join(body) + "\n", encoding="ascii")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return stub


#: `(name, exit code, stdout, stderr, the sentence a human must be shown)`. A literal
#: list, so it can never quietly become empty and assert nothing.
EXIT_CONTRACT = [
    ("matched", 0, "a/f.txt:2", "", None),
    ("clean_no_match", 1, "", "", None),
    ("broke_with_counts", 2, "a/f.txt:2", "rg: File system loop found", "PARTIAL"),
    ("broke_with_nothing", 2, "", "rg: File system loop found", "NOT a measured absence"),
    ("an_exit_nobody_named", 3, "a/f.txt:2", "rg: gone", "PARTIAL"),
]


@pytest.mark.parametrize("name,code,out,err,expected", EXIT_CONTRACT,
                         ids=[row[0] for row in EXIT_CONTRACT])
def test_the_exit_code_decides_even_where_ripgrep_is_not_installed(
        name, code, out, err, expected, tmp_path, capsys):
    """Four exits, three answers, and a real process for every one of them."""
    stub = _stub_rg(tmp_path, f"rg_{name}", out, err, code)
    rows, ok = AB._rg_counts(stub, "tok", tmp_path)

    assert rows == ([("a/f.txt", 2)] if out else []), rows
    assert ok == (expected is None), \
        f"exit {code}: could_look must be {expected is None}, got {ok}"
    said = capsys.readouterr().err
    if expected is None:
        assert said == "", f"exit {code} must stay silent, said {said!r}"
    else:
        assert expected in said, said
        assert "'tok'" in said, said
        other = "NOT a measured absence" if expected == "PARTIAL" else "PARTIAL"
        assert other not in said, f"exit {code} got BOTH sentences: {said!r}"


def test_a_hung_git_archive_refuses_by_name_instead_of_raising(tmp_path,
                                                               monkeypatch):
    """Guarded BY HAND, because stdout here is a tar and the shared helper
    decodes both streams as text. The sweep's rule is two-way for this case."""
    monkeypatch.setattr(subprocess, "run", _timeout_raiser(
        600, b"", b"fatal: nothing yet"))
    with pytest.raises(SystemExit) as caught:
        AB.extract_parent(tmp_path, "deadbeefcafe0000", tmp_path / "out")
    said = str(caught.value)
    assert "did not finish within 600s" in said[:60], said
    assert "fatal: nothing yet" in said and "deadbeefcafe" in said, said


def test_git_archive_still_reads_a_real_archive_as_bytes(tmp_path):
    """The control. A helper that decoded stdout as text would corrupt the tar,
    and every assertion above would still pass."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.bin").write_bytes(bytes(range(256)))
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@e", "-c",
                    "user.name=t", "commit", "-qm", "b"], check=True,
                   capture_output=True, timeout=120)
    dest = tmp_path / "out"
    AB.extract_parent(repo, "HEAD", dest)
    assert (dest / "a.bin").read_bytes() == bytes(range(256))


# -- links: created beside the estate, removed without touching it ------------------------


def test_link_roundtrip_leaves_the_target_intact(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "keep.txt").write_text("keep\n")
    link = tmp_path / "scratch" / "real"
    AB.make_link(real, link)
    assert (link / "keep.txt").read_text() == "keep\n"
    AB.remove_link(link)
    assert not link.exists()
    assert (real / "keep.txt").exists(), "removing the link must never touch the target"


def test_a_crashed_runs_scratch_does_not_block_the_next_run(tmp_path):
    """A run that dies mid-derivation leaves links behind; the next derivation must
    clean them link-aware (never following a link while doing so) and proceed."""
    root = tmp_path / "estate"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "keep.txt").write_text("keep\n")
    (root / "beta").mkdir()
    git(root / "beta", "init", "-q", "-b", "main")
    git(root / "beta", "config", "user.email", "t@example.invalid")
    git(root / "beta", "config", "user.name", "t")
    (root / "beta" / "f.txt").write_text("f\n")
    git(root / "beta", "add", "."); git(root / "beta", "commit", "-qm", "base")
    sha = subprocess.run(["git", "-C", str(root / "beta"), "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    components = [{"name": "alpha", "path": "alpha", "role": "authoritative"},
                  {"name": "beta", "path": "beta", "role": "authoritative"}]
    scratch = tmp_path / "scratch"

    stale = scratch / f"ab-beta-{sha[:8]}"
    stale.mkdir(parents=True)
    AB.make_link(root / "alpha", stale / "alpha")
    (stale / "junk").mkdir()

    toml, _, _ = AB.derived_workspace(components, root, "beta", sha, scratch)
    assert toml.exists()
    assert (root / "alpha" / "keep.txt").exists(), \
        "clearing the stale scratch must never reach through its links"


def test_a_committed_link_is_skipped_and_counted_not_fatal(tmp_path):
    """Real repos commit symlinks — including links to absolute paths, which the tar
    'data' filter rightly refuses to materialize. The extraction must skip the link,
    keep the count on the record, and extract everything else."""
    import io
    import tarfile as tf
    payload = io.BytesIO()
    with tf.open(fileobj=payload, mode="w") as tar:
        real = tf.TarInfo("kept.txt")
        real.size = 5
        tar.addfile(real, io.BytesIO(b"kept\n"))
        link = tf.TarInfo("node_modules")
        link.type = tf.SYMTYPE
        link.linkname = "/absolute/elsewhere"
        tar.addfile(link)
    skipped = AB._extract_tar(payload.getvalue(), tmp_path / "out")
    assert skipped == 1
    assert (tmp_path / "out" / "kept.txt").read_text() == "kept\n"
    assert not (tmp_path / "out" / "node_modules").exists()


# -- the synthetic estate -----------------------------------------------------------------


@pytest.fixture(scope="module")
def estate(tmp_path_factory) -> Path:
    """Two repos. alpha's history: a qualifying cross-module merge, then a newer
    bump merge that must be walked past and recorded. beta has no merges at all."""
    root = tmp_path_factory.mktemp("estate")
    alpha = root / "alpha"
    (alpha / "m1" / "src").mkdir(parents=True)
    (alpha / "m2" / "src").mkdir(parents=True)
    git(alpha, "init", "-q", "-b", "main")
    git(alpha, "config", "user.email", "t@example.invalid")
    git(alpha, "config", "user.name", "t")
    (alpha / "pom.xml").write_text("<project>root</project>\n")
    (alpha / "m1" / "pom.xml").write_text("<project>m1</project>\n")
    (alpha / "m2" / "pom.xml").write_text("<project>m2</project>\n")
    (alpha / "m1" / "src" / "App.java").write_text("class App { int base; }\n")
    (alpha / "m2" / "src" / "Util.java").write_text("class Util { int orderTotal; }\n")
    git(alpha, "add", "."); git(alpha, "commit", "-qm", "base")

    git(alpha, "checkout", "-qb", "feature")
    (alpha / "m1" / "src" / "App.java").write_text(
        "class App { int base; int orderTotal; int more = orderTotal + orderTotal; }\n")
    (alpha / "m2" / "src" / "NewCalc.java").write_text("class NewCalc { }\n")
    (alpha / "pom.xml").write_text("<project>root v2</project>\n")
    git(alpha, "add", ".")
    git(alpha, "commit", "-qm", "wire order totals through both modules")
    git(alpha, "checkout", "-q", "main")
    git(alpha, "merge", "-q", "--no-ff", "-m", "wire order totals through both modules",
        "feature")

    git(alpha, "checkout", "-qb", "bumps")
    for f in ("pom.xml", "m1/pom.xml", "m2/pom.xml"):
        (alpha / f).write_text((alpha / f).read_text() + "<!-- v+1 -->\n")
    git(alpha, "add", ".")
    git(alpha, "commit", "-qm", "Bump every module version")
    git(alpha, "checkout", "-q", "main")
    git(alpha, "merge", "-q", "--no-ff", "-m", "Bump every module version", "bumps")

    beta = root / "beta"
    beta.mkdir()
    git(beta, "init", "-q", "-b", "main")
    git(beta, "config", "user.email", "t@example.invalid")
    git(beta, "config", "user.name", "t")
    (beta / "pom.xml").write_text("<project>beta</project>\n")
    git(beta, "add", "."); git(beta, "commit", "-qm", "beta base")

    (root / "workspace.toml").write_text(f'root = "{root.as_posix()}"\n',
                                         encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def selection(estate):
    root, components = AB.read_workspace_components(estate / "workspace.toml")
    return root, components, AB.select(components, root, limit_back=50)


def test_selection_walks_past_the_bump_and_records_it(selection):
    _, _, sel = selection
    assert [p["component"] for p in sel["picks"]] == ["alpha"]
    assert sel["picks"][0]["subject"].startswith("wire order totals")
    assert any(r["component"] == "alpha" and "criterion 2" in r["rule"]
               for r in sel["rejections"]), "the newer bump merge must be on the record"
    assert any(c["component"] == "beta" for c in sel["componentless"])


def test_the_selected_seed_and_truth_follow_the_rules(selection):
    _, _, sel = selection
    pick = sel["picks"][0]
    assert pick["seed"] == "m1/src/App.java"
    assert sorted(pick["truth"]) == ["m2/src/NewCalc.java", "pom.xml"]


def test_terms_come_from_the_seeds_own_hunks_by_frequency(selection):
    root, _, sel = selection
    pick = sel["picks"][0]
    terms, err = AB.extract_terms(root / pick["path"], pick["sha"], pick["seed"])
    assert err == "", err
    assert terms[0] == "orderTotal", "three mentions in the hunk outrank everything else"
    assert "base" in terms
    assert all(len(t) >= 4 for t in terms)


def fake_grep(term: str, ws_root: Path) -> tuple[list[tuple[str, int]], bool]:
    """A stand-in for ripgrep with the same output contract, so the end-to-end path
    needs no vendored binary on machines that do not carry one. It always looked, so
    the second element is True — a broken search is exercised by `_stub_rg` instead."""
    rows = []
    for p in sorted(ws_root.rglob("*")):
        if not p.is_file() or "graph-out" in p.parts or p.name.endswith(".toml"):
            continue
        try:
            count = p.read_text(encoding="utf-8", errors="replace").count(term)
        except OSError:
            continue
        if count:
            rows.append((p.relative_to(ws_root).as_posix(), count))
    return rows, True


@pytest.fixture(scope="module")
def case(selection, tmp_path_factory):
    root, components, sel = selection
    out = tmp_path_factory.mktemp("ab-out")
    record = AB.run_one(sel["picks"][0], components, root, out / "scratch", out,
                        flip=1, rg=None, keep=False, grep_runner=fake_grep)
    return root, out, record


def test_the_case_record_carries_both_conditions_and_the_blinding(case):
    _, out, record = case
    assert set(record) >= {"recall_a", "recall_b", "terms", "blinded", "truth_size",
                           "wall_clock_secs", "seed_mode"}
    assert record["seed_mode"] in {"hunk", "provenance", "directory", "component"}, \
        "item 20 / gate-record limitation 5: the mode the ranking rested on is part " \
        "of the record — measured, all three judged estate seeds had provenance-" \
        "seeded nothing"
    assert record["truth_size"] == 2
    assert record["blinded"]["mapping"] == {"X": "B", "Y": "A"}
    case_dir = out / f"alpha-{record['sha'][:8]}"
    assert (case_dir / "X.txt").exists() and (case_dir / "Y.txt").exists()
    assert json.loads((case_dir / "case.json").read_text(encoding="utf-8"))


def test_the_graph_was_built_at_the_first_parent(case):
    """The extraction handed to the builder must predate the judged commit — the file
    the merge added may appear in NEITHER condition's input tree."""
    root, out, record = case
    x = (out / f"alpha-{record['sha'][:8]}" / "X.txt").read_text(encoding="utf-8")
    y = (out / f"alpha-{record['sha'][:8]}" / "Y.txt").read_text(encoding="utf-8")
    assert "NewCalc" not in x and "NewCalc" not in y, \
        "a ranked list naming the merge's new file means the tool saw the answer"


def test_the_scratch_workspace_is_gone_and_the_estate_is_untouched(case):
    root, out, _ = case
    assert not any((out / "scratch").glob("ab-*")), "links removed, extraction deleted"
    assert (root / "alpha" / "m2" / "src" / "NewCalc.java").exists(), \
        "the real repo still has its merged file — nothing reached through a link"


def test_select_only_needs_no_graph_and_writes_the_record(estate, tmp_path, capsys):
    out = tmp_path / "sel"
    assert AB.main(["--workspace", str(estate / "workspace.toml"), "--out", str(out),
                    "--select-only"]) == 0
    assert (out / "selection.json").exists()
    assert "1 commit(s)" in capsys.readouterr().out


def test_fewer_than_three_picks_refuses_to_judge(estate, tmp_path, capsys):
    assert AB.main(["--workspace", str(estate / "workspace.toml"),
                    "--out", str(tmp_path / "run")]) == 2
    assert "3 qualifying commits" in capsys.readouterr().out


def test_a_workspace_with_declared_blocks_is_refused(tmp_path):
    (tmp_path / "alpha").mkdir()
    ws = tmp_path / "ws.toml"
    ws.write_text(f'root = "{tmp_path.as_posix()}"\n\n'
                  f'[[component]]\nname = "alpha"\npath = "alpha"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="discovery-mode"):
        AB.read_workspace_components(ws)


# -- an empty truth is could-not-look, never a measured zero ------------------------------


def test_an_empty_truth_is_unmeasurable_in_all_three_metrics():
    """A case with nothing to find is not a case where nothing was found.

    `first_tp_rank` already answered `None` here and its two siblings answered
    `0.0` — the same number as a ranker that found none of a real truth. One of
    three knowing the difference is how a zero nobody could have scored reached
    the nightly trend as a fall.
    """
    ranked = [("r", "x"), ("r", "y")]
    assert AB.recall_at(ranked, set()) is None
    assert AB.precision_at(ranked, set()) is None
    assert AB.first_tp_rank(ranked, set()) is None
    assert AB.recall_at([], set()) is None, "an empty ranking does not make it scoreable"
    assert AB.precision_at([], set()) is None


def test_a_real_truth_the_ranker_missed_is_still_a_measured_zero():
    """The positive control for the test above: `0.0` must survive where it is
    the right answer, or the fix has traded one folded pair for another."""
    assert AB.recall_at([("r", "x")], T) == 0.0
    assert AB.precision_at([("r", "x")], T) == 0.0
    assert AB.first_tp_rank([("r", "x")], T) is None


def test_blind_and_score_carries_the_unmeasurable_through_both_labels():
    out = AB.blind_and_score([("r", "a")], [("r", "b")], set(), flip=0)
    for label in ("X", "Y"):
        assert out["scores"][label] == {"recall_at_20": None,
                                        "precision_at_10": None,
                                        "first_true_positive": None}


def test_a_commit_that_could_not_be_scored_is_named_not_counted_as_a_loss():
    """`verdict` compares B against A. A commit with no truth is neither a win
    nor a loss, and dropping it quietly would shrink the denominator the gate is
    read against without saying so."""
    out = AB.verdict([wins(0.2, 0.9), wins(0.3, 0.8),
                      {"recall_a": None, "recall_b": None}])
    assert out["wins_b"] == 2
    assert out["of"] == 2, "the unscoreable commit is not in the denominator"
    assert out["unmeasurable"] == 1, "and it is counted where a person can see it"
    assert out["verdict"] == "PASS"


def test_a_run_of_nothing_but_unscoreable_commits_does_not_verdict_fail():
    """FAIL is a claim about the engine. A run that could not look must not make
    it — `unmeasurable` is this project's word and it never escalates."""
    out = AB.verdict([{"recall_a": None, "recall_b": None}])
    assert out["of"] == 0 and out["unmeasurable"] == 1
    assert out["verdict"] == "UNMEASURABLE"


# -- git's own failures are could-not-look, never a protocol verdict -----------------------


def _repo_with_one_qualifying_merge(repo: Path) -> dict:
    """One real repo, one cross-module merge, built with real git — the damage
    below then deletes a real object out of a real object store.

    **If git is absent this RAISES**: `git()` runs with `check=True`, so a machine
    with no git reddens here instead of quietly agreeing. That matters more than
    usual for these tests, because an absent git produces the very answer they
    exist to forbid — measured: every one of the four calls returns empty and
    `qualifies` reports `criterion 3`. A skip is not available either
    (`SDLC_FORBID_SKIPS`), and it would be the wrong answer if it were.
    """
    (repo / "m1" / "src").mkdir(parents=True)
    (repo / "m2" / "src").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    (repo / "pom.xml").write_text("<project>root</project>\n")
    (repo / "m1" / "pom.xml").write_text("<project>m1</project>\n")
    (repo / "m2" / "pom.xml").write_text("<project>m2</project>\n")
    (repo / "m1" / "src" / "App.java").write_text("class App { int base; }\n")
    (repo / "m2" / "src" / "Util.java").write_text("class Util { int orderTotal; }\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature")
    (repo / "m1" / "src" / "App.java").write_text(
        "class App { int base; int orderTotal; int more = orderTotal + orderTotal; }\n")
    (repo / "m2" / "src" / "NewCalc.java").write_text("class NewCalc { }\n")
    (repo / "pom.xml").write_text("<project>root v2</project>\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "wire order totals through both modules")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m",
        "wire order totals through both modules", "feature")
    return {"merge": _rev(repo, "HEAD"), "tree": _rev(repo, "HEAD^{tree}"),
            "parent": _rev(repo, "HEAD^1")}


def _rev(repo: Path, rev: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", rev], check=True,
                         capture_output=True, text=True).stdout.strip()
    assert len(out) == 40, f"the fixture needs a real object id for {rev}, got {out!r}"
    return out


def _delete_object(repo: Path, sha: str) -> None:
    """Damage a real repository the way a partial clone or a pruned object store is
    damaged: the commit stays listable, the tree behind it stops being readable."""
    obj = repo / ".git" / "objects" / sha[:2] / sha[2:]
    assert obj.exists(), "the fixture must delete a real loose object, not a missing one"
    obj.chmod(stat.S_IWRITE | stat.S_IREAD)
    obj.unlink()


@pytest.fixture(scope="module")
def unreadable_tree(tmp_path_factory):
    """A workspace whose one merge commit `git log` still lists and whose tree is
    gone. MEASURED: `git log` reads no trees, so selection walks straight past
    `merge_commits` into three calls that cannot read anything."""
    root = tmp_path_factory.mktemp("damaged")
    repo = root / "alpha"
    repo.mkdir()
    shas = _repo_with_one_qualifying_merge(repo)
    _delete_object(repo, shas["tree"])
    (root / "workspace.toml").write_text(f'root = "{root.as_posix()}"\n',
                                         encoding="utf-8")
    return root, shas


def test_a_tree_git_could_not_read_is_not_a_protocol_rejection(unreadable_tree):
    """The defect, through the real `select`: `git show` and `git ls-tree` both fail,
    both errors are dropped, and the commit lands in the gate's own record as
    *rejected by criterion 3* — a sentence about the commit, produced by a run that
    never saw it."""
    root, shas = unreadable_tree
    _, components = AB.read_workspace_components(root / "workspace.toml")
    sel = AB.select(components, root, limit_back=50)
    assert not any(r["sha"] == shas["merge"] for r in sel["rejections"]), \
        "git could not read this commit; the protocol did not reject it"
    named = [u for u in sel["unmeasurable"] if u["sha"] == shas["merge"]]
    assert named, "a commit nobody could look at belongs on the record as such"
    assert "tree" in named[0]["why"], named[0]["why"]
    assert not sel["picks"], "and it is certainly not a judged pick"
    why = [c["why"] for c in sel["componentless"] if c["component"] == "alpha"]
    assert why and "could not be read" in why[0], why


def test_both_tree_reads_answer_with_the_reason_not_with_emptiness(unreadable_tree):
    """The two halves separately, because they fail differently: one cannot read the
    tree, the other is not handed one. An empty answer is legitimate for
    `manifest_dirs` — a repo with no build manifests — so the value can never
    distinguish them and only the error can."""
    root, shas = unreadable_tree
    files, err = AB.files_of_commit(root / "alpha", shas["merge"])
    assert files == [] and "tree" in err, (files, err)
    dirs, err2 = AB.manifest_dirs(root / "alpha", shas["merge"])
    assert dirs == set() and "tree" in err2, (dirs, err2)


def test_terms_git_could_not_read_are_not_a_term_list(unreadable_tree):
    """Condition A's whole input. Empty means *this hunk had no identifiers*, which
    is a real state — so a failure that also answers empty hands the grep baseline
    nothing and scores it 0.0 against a real truth, which is a win for the engine
    being judged."""
    root, shas = unreadable_tree
    terms, err = AB.extract_terms(root / "alpha", shas["merge"], "m1/src/App.java")
    assert terms == [] and "tree" in err, (terms, err)


def test_a_case_whose_terms_could_not_be_read_refuses_instead_of_scoring_zero(
        unreadable_tree, tmp_path):
    """The consequence on the evidence, end to end: the parent still extracts and the
    graph still builds, so nothing stops the run before Condition A is handed an
    empty term list. Refusing by name is the only answer that is not a recorded
    0.0 for the baseline."""
    root, shas = unreadable_tree
    components = [{"name": "alpha", "path": "alpha", "role": "authoritative"}]
    pick = {"component": "alpha", "path": "alpha", "sha": shas["merge"],
            "parent": shas["parent"], "date": "", "subject": "",
            "files": ["m1/src/App.java", "m2/src/NewCalc.java", "pom.xml"],
            "seed": "m1/src/App.java",
            "truth": ["m2/src/NewCalc.java", "pom.xml"]}
    with pytest.raises(SystemExit) as raised:
        AB.run_one(pick, components, root, tmp_path / "scratch", tmp_path / "out",
                   flip=0, rg=None, keep=False, grep_runner=fake_grep)
    said = str(raised.value)
    assert "tree" in said, said
    assert "term" in said, said


def test_a_first_parent_git_cannot_resolve_is_not_an_empty_sha(estate, tmp_path):
    """The force-push shape: a shallow clone has grafted the base commit away, so
    `rev-parse <sha>^1` answers nothing. `""` is not a sha — it reaches
    `selection.json` as `"parent": ""`, `--select-only` exits 0, and the first
    complaint comes later from `git archive` under another name.

    MEASURED: `rev-parse` returning empty on SUCCESS does not happen — it prints 40
    hex or exits non-zero — so there is no unreachable branch here to defend, only
    an error nobody read."""
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth=1", (estate / "alpha").as_uri(),
                    str(shallow)], check=True, capture_output=True, text=True)
    head = _rev(shallow, "HEAD")
    sha, err = AB.parent_of(shallow, head)
    assert sha == ""
    assert "unknown revision" in err or "ambiguous" in err, err


def test_the_four_reads_answer_with_no_error_on_a_healthy_repo(selection):
    """The control, and the one that would catch a fix that called everything broken:
    on an undamaged repo all four answer, and every error is empty."""
    root, _, sel = selection
    pick = sel["picks"][0]
    repo = root / pick["path"]
    files, err = AB.files_of_commit(repo, pick["sha"])
    assert err == "" and "m1/src/App.java" in files
    parent, err = AB.parent_of(repo, pick["sha"])
    assert err == "" and len(parent) == 40
    dirs, err = AB.manifest_dirs(repo, pick["sha"])
    assert err == "" and dirs == {"", "m1", "m2"}
    terms, err = AB.extract_terms(repo, pick["sha"], pick["seed"])
    assert err == "" and terms[0] == "orderTotal"


def _repo_with_a_module_the_merge_never_touches(repo: Path) -> dict:
    """The same qualifying cross-module merge, plus a third module it does not touch.

    This is the one shape where the two tree reads genuinely disagree, and it is why
    they need two guards rather than one. MEASURED: `git show --name-only` does not
    descend into a subtree whose OID the merge left unchanged, while `ls-tree -r` has
    to read every subtree to answer at all. So deleting the object of a tree the merge
    never touched leaves `files_of_commit` answering cleanly and stops `manifest_dirs`
    dead — `mderr` without `err`, deterministically.

    As with the fixture above, `git()` runs with `check=True`: a machine without git
    reddens here instead of quietly agreeing with the answer this test forbids.
    """
    for d in ("m1/src", "m2/src", "m3/src/deep"):
        (repo / d).mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    for rel, text in (("pom.xml", "<project>root</project>"),
                      ("m1/pom.xml", "<project>m1</project>"),
                      ("m1/src/App.java", "class App { int base; }"),
                      ("m1/src/Two.java", "class Two { }"),
                      ("m2/pom.xml", "<project>m2</project>"),
                      ("m2/src/Util.java", "class Util { int total; }"),
                      ("m3/pom.xml", "<project>m3</project>"),
                      ("m3/src/deep/Quiet.java", "class Quiet { }")):
        (repo / rel).write_text(text + "\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature")
    # touches m1 and m2, so it qualifies; never m3, whose subtree OID is therefore
    # unchanged and is not descended into by `git show`
    (repo / "m1" / "src" / "App.java").write_text(
        "class App { int orderTotal; int more = orderTotal + orderTotal; }\n",
        encoding="utf-8")
    (repo / "m1" / "src" / "Two.java").write_text(
        "class Two { int orderTotal; }\n", encoding="utf-8")
    (repo / "m2" / "src" / "Util.java").write_text(
        "class Util { int orderTotal; }\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "wire order totals through both modules")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m",
        "wire order totals through both modules", "feature")
    merge = _rev(repo, "HEAD")
    return {"merge": merge,
            "quiet_subtree": _rev(repo, f"{merge}^{{tree}}:m3/src/deep")}


@pytest.fixture(scope="module")
def unreadable_untouched_subtree(tmp_path_factory):
    """A workspace whose merge is wholly readable and whose MANIFEST walk is not."""
    root = tmp_path_factory.mktemp("untouched")
    repo = root / "alpha"
    repo.mkdir()
    shas = _repo_with_a_module_the_merge_never_touches(repo)
    _delete_object(repo, shas["quiet_subtree"])
    (root / "workspace.toml").write_text(f'root = "{root.as_posix()}"\n',
                                         encoding="utf-8")
    return root, shas


def test_the_manifest_walk_failing_alone_is_still_a_could_not_look(
        unreadable_untouched_subtree):
    """The other half of the same guard — and the reason it cannot be one condition.

    Here the first read succeeds and only the second fails, so a guard that consulted
    `err` alone would let this commit through with an empty manifest set. That set maps
    every path to the same module, and the record would then read *touches a single
    module (criterion 1)*: a criterion applied to a commit whose modules were never
    read, with a genuinely qualifying pick lost behind it. The laundered verdict and
    the true verdict are deliberately different on this repo — a fixture that agreed
    with both answers would be testing nothing."""
    root, shas = unreadable_untouched_subtree
    repo = root / "alpha"
    files, err = AB.files_of_commit(repo, shas["merge"])
    assert err == "", f"the merge's own diff is readable here; only the walk is not: {err}"
    assert files == ["m1/src/App.java", "m1/src/Two.java", "m2/src/Util.java"], files
    dirs, mderr = AB.manifest_dirs(repo, shas["merge"])
    assert dirs == set(), dirs
    assert shas["quiet_subtree"] in mderr, mderr
    assert AB.qualifies("wire order totals through both modules", files, dirs) == \
        (False, "touches a single module (criterion 1)"), "the laundered verdict"
    assert AB.qualifies("wire order totals through both modules", files,
                        {"", "m1", "m2", "m3"})[0], "the true verdict"
    _, components = AB.read_workspace_components(root / "workspace.toml")
    sel = AB.select(components, root, limit_back=50)
    assert not any(r["sha"] == shas["merge"] for r in sel["rejections"]), \
        "the manifest walk failed; the protocol did not reject this commit"
    named = [u for u in sel["unmeasurable"] if u["sha"] == shas["merge"]]
    assert named, "a commit whose modules nobody could read belongs on the record as such"
    assert named[0]["why"] == mderr, (named[0]["why"], mderr)
    assert not sel["picks"], "and it is certainly not a judged pick"


# -- Condition A's own could-not-look: a broken grep is not a measured zero ------------------
#
# `recall_at`/`precision_at` already answer `None` for an empty truth (T268). This is the
# same distinction one step upstream, in the SEARCH: a term whose ripgrep run BROKE folds to
# an empty ranking exactly like a term that genuinely matched nothing, so the return — not a
# stderr line the scorer never reads — has to carry the could-not-look, or Condition A's
# recall@20 is a fabricated 0.0 against a real truth: a win manufactured for the engine under
# test, in the evidence a kill gate is read from.


def test_grep_rank_flags_a_broken_search_apart_from_a_measured_zero(tmp_path):
    """T308: a term whose search BROKE and a term that genuinely matched nothing both
    reduce to an empty ranking. `grep_rank`'s RETURN must tell them apart — a stderr
    line cannot, because nothing downstream reads it."""
    stub = _stub_rg(tmp_path, "rg_mix", "", "", 1, break_on="LOOPTERM")
    def runner(term):
        return AB._rg_counts(stub, term, tmp_path)
    broke = AB.grep_rank(["LOOPTERM"], tmp_path, {"a": "a"}, exclude=("a", "seed"),
                         runner=runner)
    clean = AB.grep_rank(["QUIETMISS"], tmp_path, {"a": "a"}, exclude=("a", "seed"),
                         runner=runner)
    assert broke != clean, \
        "a broken search returns the same as a zero-hit search — could-not-look folded to zero"
    ranked_broke, ok_broke = broke
    ranked_clean, ok_clean = clean
    assert ranked_broke == [] and ranked_clean == []
    assert ok_broke is False, "the broken search must report it could not look"
    assert ok_clean is True, "a genuine no-match did look and found nothing"


def test_a_condition_a_search_that_broke_is_unmeasurable_not_a_scored_zero(
        selection, tmp_path, monkeypatch):
    """T308 end to end: the parent extracts, the graph builds and the terms are read,
    so nothing stops the run before Condition A. When ripgrep's search BROKE (a
    filesystem loop, exit 2), recall@20 for A must be unmeasurable — the way an empty
    truth already is — never the 0.0 that hands B a win A never lost."""
    root, components, sel = selection
    pick = sel["picks"][0]
    broke = _stub_rg(tmp_path, "rg_broke", "", "rg: File system loop found", 2)
    monkeypatch.setattr(AB, "resolve_rg", lambda explicit=None: broke)
    record = AB.run_one(pick, components, root, tmp_path / "scratch", tmp_path / "out",
                        flip=0, rg=None, keep=False)
    assert record["recall_a"] is None, \
        "a broken Condition-A search was scored a measured zero instead of unmeasurable"
    assert record["recall_b"] is not None, "Condition B was measured; only A could not look"
    assert record["a_could_look"] is False, "the record must say A could not look"


def test_the_stub_rg_reads_its_argv_so_a_broken_search_can_be_forced(tmp_path, capsys):
    """T313: a ripgrep double that ignored its argv could not show that ripgrep's exit
    depends on WHAT it was asked to search — so a mutation to the invocation survived
    it. The same stub, given the term it is set to break on, exits could-not-look;
    given any other term it cleanly finds nothing. The outcome turns on the argv and
    on nothing else, which is what makes the broken-vs-zero cases above reachable."""
    stub = _stub_rg(tmp_path, "rg_reads_argv", "", "", 1, break_on="LOOPTOKEN")
    broke, broke_ok = AB._rg_counts(stub, "LOOPTOKEN", tmp_path)
    assert broke == [] and broke_ok is False, (broke, broke_ok)
    assert "NOT a measured absence" in capsys.readouterr().err
    clean, clean_ok = AB._rg_counts(stub, "SOMETHINGELSE", tmp_path)
    assert clean == [] and clean_ok is True, (clean, clean_ok)
    assert capsys.readouterr().err == "", "a real no-match from the same stub stays silent"


# -- section 3.5: `main` runs all the way to the verdict line --------------------------------


@pytest.fixture(scope="module")
def three_component_estate(tmp_path_factory):
    """Three authoritative components, each with one qualifying cross-module merge — the
    smallest estate that gives `select` its three picks, so `main` runs past the 'fewer
    than three' door all the way to the verdict line."""
    root = tmp_path_factory.mktemp("trio")
    for name in ("alpha", "beta", "gamma"):
        repo = root / name
        repo.mkdir()
        _repo_with_one_qualifying_merge(repo)
    (root / "workspace.toml").write_text(f'root = "{root.as_posix()}"\n',
                                         encoding="utf-8")
    return root


def test_main_runs_to_the_verdict_when_three_commits_qualify(
        three_component_estate, tmp_path, monkeypatch, capsys):
    """T295: the other two `main` tests stop at early doors — `--select-only` and the
    'fewer than three qualifying commits' refusal — so nothing ever drove `main` to the
    verdict line, and a mutation that broke that print shipped green. This runs the whole
    pipeline (selection, then a graph build and blinded scoring per commit) with a
    ripgrep stand-in in place of the vendored binary, and asserts the verdict summary
    reaches both stdout and the result file."""
    def fake_counts(rg, term, root):
        rows, _ = fake_grep(term, Path(root))
        return rows, True
    monkeypatch.setattr(AB, "resolve_rg", lambda explicit=None: Path("stub-rg"))
    monkeypatch.setattr(AB, "_rg_counts", fake_counts)
    out = tmp_path / "run"
    rc = AB.main(["--workspace", str(three_component_estate / "workspace.toml"),
                  "--out", str(out), "--flips", "0,0,0"])
    assert rc == 0, rc
    printed = capsys.readouterr().out
    assert "verdict:" in printed, printed
    assert "B wins" in printed and "had nothing to rank against" in printed, printed
    payload = json.loads((out / "ab_result.json").read_text(encoding="utf-8"))
    assert len(payload["cases"]) == 3
    assert payload["verdict"]["verdict"] in {"PASS", "FAIL", "UNMEASURABLE"}


# ── T272 / item 117: a workspace it cannot read is a REFUSAL, not a traceback ─
#
# W156 A3c-10 recorded that every module in this partition "calls cli.door" —
# but impact_ab.py did NOT: `main` read the workspace straight, so a `--workspace`
# naming no file handed the operator a `WorkspaceError` traceback at exit 1, the
# exact class item 117 recorded for `policy.py --validate`. A CLI's contract is
# a process, so this is SPAWNED: called in-process, a traceback and a refusal are
# the same failing assertion and neither could be told from the other.


def test_a_workspace_it_cannot_read_refuses_as_a_process_not_a_traceback(tmp_path):
    """SPAWNED. `--workspace` pointing at no file is a refusal impact_ab must
    give cleanly; before the door it was an uncaught traceback (exit 1). The door
    names the reason and answers could-not-look (exit 3) — the code corpus_replay
    already gives for the SAME shared workspace reader. Removing the door drops
    the exit code back to 1 with a traceback, which this catches by name."""
    cli = Path(__file__).resolve().parents[2] / "tools" / "impact_ab.py"
    proc = subprocess.run(
        [sys.executable, str(cli), "--workspace", str(tmp_path / "nope.toml"),
         "--out", str(tmp_path / "out")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    said = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 3, said
    assert "Traceback" not in said, said
    assert "!!" in said and "did not finish" in said, said
    assert "no such workspace file" in said, said
