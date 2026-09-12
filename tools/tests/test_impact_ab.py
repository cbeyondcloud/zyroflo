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
                "tok2": [("alpha/src/A.java", 1)]}[term]
    ranked = AB.grep_rank(["tok", "tok2"], tmp_path, {"alpha": "alpha", "beta": "beta"},
                          exclude=("alpha", "seed.java"), runner=runner)
    assert ranked == [("alpha", "src/A.java"), ("beta", "x.txt")], \
        "4 hits beat 1; the test file's 9 hits count for nothing"


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
    rows = AB._rg_counts(Path("rg"), "tok", tmp_path)
    assert rows == [("alpha/src/A.java", 3), ("beta/x.txt", 1)], rows
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
# tracked), so on CI the binary is ABSENT — the note at the top of `.github/workflows/
# ci.yml` says so and calls it an accepted gap. There are no tolerated skips in this
# repository, so the absent branch asserts the resolver's own refusal instead and names
# the contract that went unexercised there.


def _vendored_rg() -> Path | None:
    try:
        rg = AB.resolve_rg()
    except SystemExit:
        return None
    return rg if rg.exists() else None


def test_a_ripgrep_that_could_not_look_is_not_a_measured_zero(tmp_path, capsys):
    """A REAL could-not-start — no fake and no monkeypatch, just a binary that is
    not there, so the search never happened. Empty output from a search that never
    ran must not be handed back as a term nobody uses."""
    rows = AB._rg_counts(tmp_path / "no-such-rg", "tok", tmp_path)
    said = capsys.readouterr().err
    assert rows == [], rows
    assert "'tok'" in said and "NOT a measured absence" in said, said
    assert "PARTIAL" not in said, said


def test_the_vendored_ripgreps_exit_code_decides_the_three_answers(tmp_path, capsys):
    """A match, a no-match and a broken search are three answers, never one.

    The loop is not a hypothetical: `-L` is passed on purpose and the workspace this
    module builds is components joined by junctions, so a component that is an
    ancestor of the scratch root loops. Before this test, ripgrep said 2, printed a
    partial count, and the ranking used it in silence."""
    rg = _vendored_rg()
    if rg is None:                      # CI, by design — see the block comment above
        with pytest.raises(SystemExit) as refused:
            AB.resolve_rg()
        assert "--fetch" in str(refused.value), refused.value
        return

    ws = tmp_path / "ws"
    (ws / "a").mkdir(parents=True)
    (ws / "a" / "file.txt").write_text("HITTERM\nHITTERM again\n", encoding="utf-8")

    assert AB._rg_counts(rg, "HITTERM", ws) == [("a/file.txt", 2)]
    assert capsys.readouterr().err == "", "a plain match says nothing"

    assert AB._rg_counts(rg, "ZZNOSUCHTOKENZZ", ws) == []
    assert capsys.readouterr().err == "", \
        "a genuine no-match is the one answer that must stay cheap and silent"

    AB.make_link(ws, ws / "loop")       # the workspace's own shape, pointed at itself
    rows = AB._rg_counts(rg, "HITTERM", ws)
    said = capsys.readouterr().err
    assert ("a/file.txt", 2) in rows, rows
    assert "PARTIAL" in said, said
    assert "loop" in said.lower(), \
        f"the reason `--no-messages` used to delete: {said!r}"

    assert AB._rg_counts(rg, "ZZNOSUCHTOKENZZ", ws) == []
    broke = capsys.readouterr().err
    assert "NOT a measured absence" in broke, broke
    assert "PARTIAL" not in broke, broke


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
    terms = AB.extract_terms(root / pick["path"], pick["sha"], pick["seed"])
    assert terms[0] == "orderTotal", "three mentions in the hunk outrank everything else"
    assert "base" in terms
    assert all(len(t) >= 4 for t in terms)


def fake_grep(term: str, ws_root: Path) -> list[tuple[str, int]]:
    """A stand-in for ripgrep with the same output contract, so the end-to-end path
    needs no vendored binary on machines that do not carry one."""
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
    return rows


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
