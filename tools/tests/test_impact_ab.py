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
                           "wall_clock_secs"}
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
