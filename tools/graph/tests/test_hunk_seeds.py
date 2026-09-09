"""Item 20 — hunk-level diff→seed refinement.

Measured before designed, and the measurement rewrote the item: on the real workspace
the median cited file declares ONE fact (refinement is a no-op there), 10% of files
hold ≥10 facts each — 64% of all edges — which is where file-level seeding floods; and
all three judged A/B seed files declared ZERO facts, so the single-seed prototype never
provenance-seeded at all. Hence the two mechanisms here: line-refined seeds within a
file, and the whole diff's files seeding as a union — with the fallback firing only
when nothing seeded, and every mode named.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
import tools.graph.impact as I  # noqa: E402
from tools.graph.impact import parse_diff, parse_lines  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"
URLS = ("web-console", "src/config/urls.js")
DETAIL_JSX = ("web-console", "src/components/OrderDetail.jsx")
TEST_FILE = ("orders-api", "src/test/java/com/example/orderflow/api/OrderControllerTest.java")


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    out = tmp_path_factory.mktemp("hunks") / "out"
    assert B.main(["--workspace", str(FIXTURE / "workspace.toml"),
                   "--out", str(out)]) == 0
    return I.load_graph(out), out


def files_of(result: dict) -> dict[tuple[str, str], float]:
    return {(f["component"], f["path"]): f["score"] for f in result["files"]}


# ── parse_lines ───────────────────────────────────────────────────────────────


def test_singles_and_ranges_parse():
    assert parse_lines("5") == [(5, 5)]
    assert parse_lines("12-40, 55") == [(12, 40), (55, 55)]


def test_junk_is_refused_loudly():
    with pytest.raises(ValueError, match="wants N or A-B"):
        parse_lines("12-x")
    with pytest.raises(ValueError, match="named no lines"):
        parse_lines(" , ")


def test_a_backwards_range_is_refused():
    with pytest.raises(ValueError, match="backwards"):
        parse_lines("9-3")


# ── parse_diff ────────────────────────────────────────────────────────────────


def test_two_files_with_new_side_ranges():
    got = parse_diff(
        "--- a/src/config/urls.js\n+++ b/src/config/urls.js\n"
        "@@ -4,1 +5,1 @@\n-old\n+new\n"
        "--- a/pom.xml\n+++ b/pom.xml\n"
        "@@ -1,3 +10,4 @@\n context\n+added\n")
    assert got == {"src/config/urls.js": [(5, 5)], "pom.xml": [(10, 13)]}


def test_headers_are_recognised_statefully():
    """An added content line may itself begin '+++ '; only a line right after '--- '
    opens a file, or a diff of a file ABOUT diffs would seed phantom paths."""
    got = parse_diff(
        "--- a/notes.md\n+++ b/notes.md\n@@ -1,1 +1,2 @@\n"
        "+++ not/a/header\n+more\n")
    assert got == {"notes.md": [(1, 2)]}


def test_a_deleted_file_maps_its_old_path_to_the_whole_file():
    got = parse_diff("--- a/gone.properties\n+++ /dev/null\n@@ -1,9 +0,0 @@\n-x=1\n")
    assert got == {"gone.properties": None}
    assert "/dev/null" not in got


def test_a_new_file_keeps_its_ranges():
    got = parse_diff("--- /dev/null\n+++ b/fresh.yaml\n@@ -0,0 +1,4 @@\n+a: 1\n")
    assert got == {"fresh.yaml": [(1, 4)]}


def test_a_pure_deletion_hunk_touches_the_removal_site():
    got = parse_diff("--- a/f.java\n+++ b/f.java\n@@ -6,2 +7,0 @@\n-x\n-y\n")
    assert got == {"f.java": [(7, 7)]}


def test_a_headerless_pair_and_prose_are_the_whole_file_and_nothing():
    """The `---`/`+++`-only input git never emits for a mode-only change, kept
    because it is the one shape no scratch repository can produce: a caller that
    hands parse_diff a hand-assembled pair still gets the whole file. What git
    ACTUALLY emits for a mode-only change is measured in
    `test_a_mode_only_change_git_really_emits_names_the_file`."""
    assert parse_diff("--- a/script.sh\n+++ b/script.sh\n") == {"script.sh": None}
    assert parse_diff("nothing that resembles a diff\n") == {}


# ── the same grammar, with git as the producer ────────────────────────────────
#
# Every parse_diff case above is a hand-typed literal, and item 154 found what
# that hides: the literal for "a mode-only change" is an input git does not
# emit. For a pure rename, a mode-only change and a binary change git emits a
# `diff --git` header and NO `---`/`+++` pair at all, so a parser reading only
# that pair saw nothing. These cases run REAL `git diff` over a scratch
# repository, so the producer is git.


def _git(repo: Path, *args: str) -> str:
    import subprocess
    done = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)
    assert done.returncode == 0, f"git {args}: {done.stderr}"
    return done.stdout


@pytest.fixture(scope="module")
def real_git(tmp_path_factory) -> dict[str, str]:
    """Real `git diff` text for the four shapes item 154 measured."""
    repo = tmp_path_factory.mktemp("t154repo")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "worker@example.invalid")
    _git(repo, "config", "user.name", "worker")
    (repo / "svc").mkdir()
    (repo / "svc" / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (repo / "svc" / "img.bin").write_bytes(bytes(range(256)))
    (repo / "svc" / "mode.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    out: dict[str, str] = {}

    _git(repo, "mv", "svc/a.py", "svc/b.py")
    _git(repo, "add", "-A")
    out["pure rename"] = _git(repo, "diff", "--cached", "-M")
    _git(repo, "reset", "-q", "--hard")

    _git(repo, "mv", "svc/a.py", "svc/b.py")
    (repo / "svc" / "b.py").write_text("one\nTWO\nthree\n", encoding="utf-8")
    _git(repo, "add", "-A")
    out["rename+edit"] = _git(repo, "diff", "--cached", "-M")
    _git(repo, "reset", "-q", "--hard")

    _git(repo, "update-index", "--chmod=+x", "svc/mode.sh")
    out["mode only"] = _git(repo, "diff", "--cached")
    _git(repo, "reset", "-q", "--hard")

    (repo / "svc" / "img.bin").write_bytes(bytes(range(255, -1, -1)))
    _git(repo, "add", "-A")
    out["binary"] = _git(repo, "diff", "--cached")
    _git(repo, "reset", "-q", "--hard")

    (repo / "svc" / "a.py").write_text("one\nTWO\nthree\n", encoding="utf-8")
    _git(repo, "add", "-A")
    out["ordinary edit"] = _git(repo, "diff", "--cached")
    _git(repo, "reset", "-q", "--hard")

    return out


def test_a_pure_rename_git_really_emits_keeps_the_old_name(real_git):
    """The graph is built from the tree BEFORE the change, so it knows this file
    by its OLD path; the old path's facts ARE the change. Dropping it made the
    whole file vanish from the diff, and a diff of only renames then seeded
    nothing at all."""
    text = real_git["pure rename"]
    assert "rename from svc/a.py" in text and "--- " not in text, \
        "git emits a pure rename with no ---/+++ pair; the fixture must show that"
    assert parse_diff(text) == {"svc/a.py": None, "svc/b.py": None}


def test_a_rename_with_an_edit_records_both_names(real_git):
    """The new path carries the hunk's new-side lines. The old path carries no
    line information — the ranges address the new side — so it is the whole
    file, which is what the graph can cite."""
    text = real_git["rename+edit"]
    assert "rename from svc/a.py" in text and "+++ b/svc/b.py" in text
    assert parse_diff(text) == {"svc/a.py": None, "svc/b.py": [(1, 3)]}


def test_a_mode_only_change_git_really_emits_names_the_file(real_git):
    """Real git emits `old mode`/`new mode` and no ---/+++ pair. The answer is
    the path with no line information — the same whole-file shape a deleted file
    gets — because `{}` is indistinguishable from a file the diff never touched,
    and a diff of only a mode change then reached diff_seeds with no targets."""
    text = real_git["mode only"]
    assert "old mode " in text and "--- " not in text
    assert parse_diff(text) == {"svc/mode.sh": None}


def test_a_binary_change_names_the_file_too(real_git):
    """Undocumented until item 154: git says `Binary files … differ` and emits no
    hunks. Touched, with no line information — not absent."""
    text = real_git["binary"]
    assert "Binary files " in text and "--- " not in text
    assert parse_diff(text) == {"svc/img.bin": None}


def test_an_ordinary_edit_names_one_path_only(real_git):
    """The control on the rename fix: an unrenamed file must NOT gain a second
    entry, and its ranges must survive."""
    assert parse_diff(real_git["ordinary edit"]) == {"svc/a.py": [(1, 3)]}


# ── hunk-refined seeding, on the fixture's answer-key lines ───────────────────


def test_the_two_url_lines_discriminate(graph):
    """urls.js:4 calls the list endpoint, :5 the detail endpoint (EXPECTED.md hop 1 and
    D1). Seeded at line 5, OrderDetail.jsx's own fact is fully reached; at line 4 it is
    only reachable through traversal — the whole point of refinement."""
    (nodes, edges), _ = graph
    at5 = I.impact(nodes, edges, *URLS, lines=[(5, 5)])
    at4 = I.impact(nodes, edges, *URLS, lines=[(4, 4)])
    assert at5["seed"]["mode"] == "hunk" and at4["seed"]["mode"] == "hunk"
    detail_at5 = files_of(at5)[DETAIL_JSX]
    detail_at4 = files_of(at4)[DETAIL_JSX]
    assert detail_at5 > detail_at4, \
        "the changed line must decide which endpoint's dependents rank first"
    assert detail_at5 == pytest.approx(1.0)


def test_the_seed_block_records_the_ranges(graph):
    (nodes, edges), _ = graph
    refined = I.impact(nodes, edges, *URLS, lines=[(4, 4)])
    assert refined["seed"]["lines"] == [[4, 4]]
    assert refined["seed"]["lines_missed"] is False
    whole = I.impact(nodes, edges, *URLS)
    assert "lines" not in whole["seed"] and "lines_missed" not in whole["seed"]


def test_a_miss_falls_back_to_the_whole_file_and_says_so(graph):
    """Line 2 of urls.js is a comment: ranges that hit no cited line must not read as
    'no impact' — the whole file stands in, and the seed block names the miss."""
    (nodes, edges), _ = graph
    missed = I.impact(nodes, edges, *URLS, lines=[(2, 2)])
    assert missed["seed"]["mode"] == "provenance"
    assert missed["seed"]["lines_missed"] is True
    assert files_of(missed) == files_of(I.impact(nodes, edges, *URLS))


def test_a_whole_file_claim_survives_refinement(graph):
    """B1 (DG_TESTS at line 0) is a claim about the whole test file; a hunk anywhere in
    the file touches it. Refining to B2's literal line must not drop it."""
    (nodes, edges), _ = graph
    result = I.impact(nodes, edges, *TEST_FILE, lines=[(15, 15)])
    assert result["seed"]["mode"] == "hunk"
    services = {s["id"]: s["score"] for s in result["services"]}
    covered = [s for s in services if s.startswith("svc:orders-api")]
    assert covered and services[covered[0]] == pytest.approx(1.0), \
        "the line-0 tests-this-service claim must still seed at full strength"


def test_only_line0_facts_plus_ranges_is_provenance_not_a_miss():
    """Synthetic: a file whose only facts are whole-file claims has nothing line-
    addressed to miss — ranges neither refine nor flag."""
    nodes = [{"id": "svc:a@a", "type": "svc", "props": {"component": "a"}},
             {"id": "repo:a", "type": "repo", "props": {}}]
    edges = [{"src": "svc:a@a", "dst": "repo:a", "type": "DG_IN_REPO",
              "source_file": "f.java", "line": 0, "extractor": "x"}]
    by_id = {n["id"]: n for n in nodes}
    seeds, mode, missed = I.seed_scores("a", "f.java", nodes, edges, by_id, {"a"},
                                        lines=[(3, 9)])
    assert mode == "provenance" and missed is False
    assert seeds["svc:a@a"] == 1.0


def test_a_factless_file_with_ranges_still_reaches_the_directory_fallback():
    nodes = [{"id": "svc:a@a", "type": "svc", "props": {"component": "a"}}]
    edges = [{"src": "svc:a@a", "dst": "svc:a@a", "type": "DG_CALLS",
              "source_file": "src/other.java", "line": 3, "extractor": "x"}]
    by_id = {n["id"]: n for n in nodes}
    seeds, mode, missed = I.seed_scores("a", "src/f.java", nodes, edges, by_id, {"a"},
                                        lines=[(1, 5)])
    assert mode == "directory" and missed is False
    assert seeds["svc:a@a"] == 0.8


# ── the diff as one blast radius ──────────────────────────────────────────────


def test_a_diff_unions_its_files_and_excludes_them_all(graph):
    (nodes, edges), _ = graph
    result = I.impact_diff(nodes, edges, "web-console",
                           [(URLS[1], [(5, 5)]), (DETAIL_JSX[1], None)])
    assert result["seed"]["mode"] == "hunk"
    by_path = {f["path"]: f for f in result["seed"]["files"]}
    assert by_path[URLS[1]]["mode"] == "hunk"
    assert by_path[DETAIL_JSX[1]]["mode"] == "provenance"
    ranked = files_of(result)
    assert URLS not in ranked and DETAIL_JSX not in ranked, \
        "every file of the diff is a seed, and a seed is never its own impact"


def test_the_fallback_fires_only_when_no_file_seeded(graph):
    (nodes, edges), _ = graph
    nothing = I.impact_diff(nodes, edges, "web-console",
                            [("docs/CHANGES.md", None)])
    assert nothing["seed"]["mode"] == "directory", \
        "an all-factless diff falls back once, and the mode says so"
    assert nothing["seed"]["files"][0]["mode"] == "none"

    mixed = I.impact_diff(nodes, edges, "web-console",
                          [("docs/CHANGES.md", None), (URLS[1], None)])
    assert mixed["seed"]["mode"] == "provenance", \
        "one factless file must not drag its directory in beside a real seed"
    assert {f["mode"] for f in mixed["seed"]["files"]} == {"none", "provenance"}
    assert files_of(mixed) == files_of(I.impact(nodes, edges, *URLS))


def test_empty_targets_and_unknown_components_are_none(graph):
    (nodes, edges), _ = graph
    assert I.impact_diff(nodes, edges, "web-console", []) is None
    assert I.impact_diff(nodes, edges, "no-such", [(URLS[1], None)]) is None


# ── the CLI ───────────────────────────────────────────────────────────────────


def test_the_cli_runs_a_diff_and_names_each_files_mode(graph, tmp_path, capsys):
    _, out = graph
    diff = tmp_path / "change.diff"
    diff.write_text(
        "--- a/src/config/urls.js\n+++ b/src/config/urls.js\n"
        "@@ -5,1 +5,1 @@\n-x\n+y\n"
        "--- a/docs/CHANGES.md\n+++ b/docs/CHANGES.md\n",
        encoding="utf-8")
    assert I.main(["--graph-dir", str(out), "--diff", str(diff),
                   "--component", "web-console"]) == 0
    text = capsys.readouterr().out
    assert "seed web-console diff  (mode: hunk" in text
    assert "hunk" in text and "none" in text


def test_the_cli_refuses_half_specified_requests(graph, tmp_path, capsys):
    _, out = graph
    assert I.main(["--graph-dir", str(out)]) == 2
    assert "exactly one of --seed and --diff" in capsys.readouterr().out
    diff = tmp_path / "d.diff"
    diff.write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
    assert I.main(["--graph-dir", str(out), "--diff", str(diff)]) == 2
    assert "--component" in capsys.readouterr().out
    assert I.main(["--graph-dir", str(out), "--seed", "web-console/x",
                   "--lines", "3-1"]) == 2
    assert "backwards" in capsys.readouterr().out


def test_the_cli_annotates_a_missed_range(graph, capsys):
    _, out = graph
    assert I.main(["--graph-dir", str(out), "--seed", f"{URLS[0]}/{URLS[1]}",
                   "--lines", "2"]) == 0
    assert "MISSED, whole file stands in" in capsys.readouterr().out


# ── the A/B runner records what the ranking rested on ─────────────────────────


def test_impact_rank_returns_the_seed_mode(graph):
    """Gate-record limitation 5: the mode was derivable and unrecorded — and when
    finally measured, all three judged seeds had provenance-seeded nothing."""
    _, out = graph
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
    from tools.impact_ab import impact_rank
    ranked, mode = impact_rank(out, URLS[0], URLS[1], exclude=URLS)
    assert mode == "provenance"
    assert ranked and all(isinstance(c, str) and isinstance(p, str)
                          for c, p in ranked)
