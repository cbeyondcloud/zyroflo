"""The field corpus and its census — item 96.

Two questions, and they are not the same one.

**Does the walk record what happened.** Every gate reports a fixed set of
counters whatever befalls it, absence stays `None` and never becomes `0`, and a
gate that could not look is a different sentence from a gate that broke and
from one nobody asked. Those distinctions are the census's whole value: item
97 reads them, and folding any two would let a dead gate look like a clean
repository — which is the defect this corpus exists because of.

**Does it compose with the real gates.** The driver spawns the CLIs rather than
importing their functions, because all three defects the first field run turned
up lived at or below that boundary. So the tests run the driver against a real
workspace and require it to read back what the real commands wrote. A test that
faked the subprocesses would reproduce the very blindness being tested.

Nothing here skips on an absent binary. A machine without `osv-scanner`
produces a `not-run` row, and that is a case worth asserting rather than
stepping over: CI is such a machine on purpose, so these tests exercise the
could-not-look path there and the ok path on a maintainer's box.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import field_run as F  # noqa: E402
from tools.review import prepass as PP  # noqa: E402
from tools.review import sca as SCA  # noqa: E402

PROFILE = """\
scope: estate
gate_profile: team
test_command: ""
review:
  block_on: [critical, high]
components:
  - name: "alpha"
    path: "alpha"
    role: authoritative
    test_command: "{command}"
"""

#: A command that exits 0 and writes no test report. It is the interesting
#: baseline case, not a degenerate one: a suite that ran and reported nothing
#: is exactly what `baseline green` was claimed of once.
SILENT = f'"{sys.executable}" -c "pass"'


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, timeout=120,
                   capture_output=True)


def repo(root: Path, command: str = SILENT, installed: bool = True) -> Path:
    """A small git repository with one component, optionally installed."""
    (root / "alpha").mkdir(parents=True, exist_ok=True)
    (root / "alpha" / "app.py").write_text("x = 1\n", encoding="utf-8")
    if installed:
        (root / ".sdlc").mkdir(parents=True, exist_ok=True)
        (root / ".sdlc" / "profile.yml").write_text(
            PROFILE.format(command=command), encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
         "commit", "-qm", "base")
    return root


def entry(root: Path, **over) -> dict:
    return {"name": "alpha-repo", "path": str(root), "commit": "", "stack": "",
            "seed": "", "install_args": [], **over}


@pytest.fixture(scope="module")
def walked(tmp_path_factory) -> dict:
    """One real walk, shared: four spawned CLIs is the expensive part."""
    root = repo(tmp_path_factory.mktemp("field") / "repo")
    return F.walk(entry(root), verbose=False)


# ── the row contract, which is what item 97 will read ───────────────────────


def test_every_gate_reports_its_full_key_set_whatever_happened(walked):
    """A key that DISAPPEARS must mean the harness stopped reporting it.

    That only works if a gate's keys never depend on its outcome. Otherwise a
    counter vanishing is ambiguous between "this repository has none" and "the
    gate broke", and the second is the one worth waking up for.
    """
    assert set(walked["gates"]) == set(F.GATES)
    for name, gate in walked["gates"].items():
        assert set(gate["measures"]) == set(F.MEASURES[name]), name
        assert gate["status"] in F.STATUSES


def test_a_counter_that_could_not_be_taken_is_none_and_never_zero(walked):
    """`tests` on a suite that emitted no report is the case that was got wrong.

    The component's command exits 0 and writes no JUnit XML, so nothing here
    knows whether a single test ran. `0` would be a claim; `None` is the truth.
    """
    baseline = walked["gates"]["baseline"]
    assert baseline["status"] == "ok"
    assert baseline["measures"]["executed"] == 1
    assert baseline["measures"]["tests"] is None


def test_a_gate_that_was_not_asked_is_not_a_gate_that_could_not_look(tmp_path):
    """`skipped` and `not-run` are different sentences and stay apart."""
    root = repo(tmp_path / "repo")
    census = F.walk(entry(root), execute=False, verbose=False)
    assert census["gates"]["baseline"]["status"] == "skipped"
    assert census["gates"]["baseline"]["measures"]["tests"] is None
    assert F.quiet(census) and not F.problems(census)


def test_row_refuses_a_counter_the_gate_does_not_report():
    """A typo'd counter would be a key nothing ever compares. It refuses."""
    with pytest.raises(F.CorpusError) as exc:
        F.row("sca", "ok", manifest=3)
    assert "manifest" in str(exc.value) and "manifests" in str(exc.value)


def test_row_refuses_a_status_outside_the_vocabulary():
    with pytest.raises(F.CorpusError):
        F.row("sca", "green")


def test_the_walk_fails_on_a_broken_gate_and_not_on_a_quiet_one(walked):
    """`problems` is about the WALK; a quiet gate is history's question.

    A recurring check that reddens because a repository has no graph is a
    check somebody switches off inside a month, and a switched-off check
    measures nothing at all.
    """
    assert F.problems(walked) == []
    for note in F.quiet(walked):
        assert "did not run" in note


# ── composed with the real gates ────────────────────────────────────────────


def test_the_pre_pass_census_is_read_from_what_the_real_pre_pass_wrote(walked):
    """The document's name comes from its writer, and the walk spawned it."""
    gate = walked["gates"]["prepass"]
    assert gate["status"] in ("ok", "not-run")
    if gate["status"] == "ok":
        assert gate["measures"]["gates_ok"] == len(PP.GATES)
        assert gate["measures"]["findings"] == 0
    else:
        # Every counter a could-not-look row cannot honestly fill stays empty.
        assert gate["measures"]["findings"] is None


def test_the_sca_census_distinguishes_scanning_nothing_from_finding_nothing(walked):
    """The exact defect the corpus exists for, asserted as a shape.

    `manifests` is what tells a clean repository apart from a scan that never
    reached one, and a findings count alone cannot: both are zero.
    """
    gate = walked["gates"]["sca"]
    assert "manifests" in gate["measures"]
    if gate["status"] == "ok":
        assert gate["measures"]["manifests"] is not None
    else:
        assert gate["measures"]["findings"] is None


def test_the_gates_are_spawned_as_the_command_lines_they_really_are():
    """A driver that imported the functions would have reproduced all three
    of the field run's defects faithfully and called them green. So the paths
    it spawns must be paths that exist, and this is what says so when one moves."""
    for rel in (Path("tools") / "sdlc" / "suite.py",
                Path("tools") / "graph" / "impact.py",
                Path("tools") / "review" / "sca.py",
                Path("tools") / "review" / "prepass.py",
                Path("sdlc-init.py")):
        assert (F.HARNESS / rel).is_file(), rel


def test_the_document_names_come_from_the_modules_that_write_them():
    """Asked for, never spelled again - item 94's rule, at this reader."""
    source = (ROOT / "tools" / "field_run.py").read_text(encoding="utf-8")
    for owned in (SCA.RESULT_NAME, PP.RESULT_NAME):
        assert owned not in source, owned


def test_the_walk_leaves_nothing_in_the_repository_but_its_census(tmp_path):
    """A recurring check that accumulated run folders in somebody's checkout
    would be turned off, and a check that is off measures nothing."""
    root = repo(tmp_path / "repo")
    before = {p.relative_to(root) for p in root.rglob("*") if p.is_file()}
    census = F.walk(entry(root), verbose=False)
    F.append(root, census)
    after = {p.relative_to(root) for p in root.rglob("*") if p.is_file()}
    assert after - before == {Path(".sdlc") / "metrics" / F.CENSUS_NAME}


# ── the graph the scoping gate reads ────────────────────────────────────────


def _graph(root: Path, lines: list[str]) -> None:
    where = root / F.GRAPH_DIR
    where.mkdir(parents=True, exist_ok=True)
    (where / "graph.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_scoping_counts_nodes_and_edges_through_the_module_that_owns_them(tmp_path):
    """Not by hand. A graph gains row kinds, and a second answer to what a
    node is drifts from the first the moment it does."""
    root = repo(tmp_path / "repo")
    _graph(root, [json.dumps({"kind": "header", "schema": 1}),
                  json.dumps({"id": "repo:alpha", "type": "repo"}),
                  json.dumps({"id": "file:alpha/app.py", "type": "file"}),
                  json.dumps({"src": "file:alpha/app.py", "dst": "repo:alpha",
                              "type": "DG_BUILT_FROM"})])
    gate = F.gate_scoping(root, seed="")
    assert gate["measures"]["nodes"] == 2 and gate["measures"]["edges"] == 1
    # No seed means no traversal, and the row says so rather than reporting 0.
    assert gate["status"] == "not-run" and gate["measures"]["ranked"] is None


def test_a_damaged_graph_is_an_error_and_not_an_empty_one(tmp_path):
    """Absent, damaged and empty are three answers. A build interrupted
    part-way through its last line must not be counted as a small graph."""
    root = repo(tmp_path / "repo")
    _graph(root, [json.dumps({"id": "repo:alpha", "type": "repo"}), '{"id": "fi'])
    gate = F.gate_scoping(root, seed="")
    assert gate["status"] == "error"
    assert gate["measures"]["nodes"] is None


def test_an_absent_graph_is_not_run_rather_than_an_error(tmp_path):
    root = repo(tmp_path / "repo")
    gate = F.gate_scoping(root, seed="")
    assert gate["status"] == "not-run" and "no graph at" in gate["detail"]


# ── the manifest ────────────────────────────────────────────────────────────


def corpus(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sdlc-corpus.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_manifest_names_every_problem_at_once(tmp_path):
    """One reason at a time is how a file gets fixed four times. Item 31's
    refusal shape, applied to the corpus."""
    path = corpus(tmp_path, '[[repo]]\nname = "a"\n\n[[repo]]\npath = "/x"\n')
    with pytest.raises(F.CorpusError) as exc:
        F.read_corpus(path)
    said = str(exc.value)
    assert "`path`" in said and "`commit`" in said and "`name`" in said


def test_a_missing_manifest_says_where_the_template_is(tmp_path):
    with pytest.raises(F.CorpusError) as exc:
        F.read_corpus(tmp_path / "nope.toml")
    assert "corpus.example.toml" in str(exc.value)


def test_a_manifest_with_no_entries_is_refused(tmp_path):
    with pytest.raises(F.CorpusError) as exc:
        F.read_corpus(corpus(tmp_path, "# nothing here\n"))
    assert "[[repo]]" in str(exc.value)


def test_two_entries_with_one_name_are_refused(tmp_path):
    """A census is filed under the name, so they would silently merge into
    one history and each would look like the other going quiet."""
    body = ('[[repo]]\nname = "a"\npath = "/x"\ncommit = "abc"\n\n'
            '[[repo]]\nname = "a"\npath = "/y"\ncommit = "def"\n')
    with pytest.raises(F.CorpusError) as exc:
        F.read_corpus(corpus(tmp_path, body))
    assert "named 'a'" in str(exc.value)


def test_install_args_must_be_a_list_of_strings(tmp_path):
    body = ('[[repo]]\nname = "a"\npath = "/x"\ncommit = "abc"\n'
            'install_args = "--single-component"\n')
    with pytest.raises(F.CorpusError) as exc:
        F.read_corpus(corpus(tmp_path, body))
    assert "install_args" in str(exc.value)


def test_a_filled_manifest_round_trips(tmp_path):
    body = ('[[repo]]\nname = "a"\npath = "/x"\ncommit = "abc123"\n'
            'stack = "java"\nseed = "a/Main.java"\n'
            'install_args = ["--single-component"]\n')
    got = F.read_corpus(corpus(tmp_path, body))
    assert got == [{"name": "a", "path": "/x", "commit": "abc123",
                    "stack": "java", "seed": "a/Main.java",
                    "install_args": ["--single-component"]}]


def test_the_shipped_template_is_a_manifest_this_reader_accepts():
    """A template that does not parse is a template nobody can start from."""
    got = F.read_corpus(ROOT / "sdlc_templates" / "corpus.example.toml")
    assert [e["name"] for e in got] == ["example-one"]
    assert got[0]["commit"] and got[0]["path"]


# ── the history ─────────────────────────────────────────────────────────────


def test_a_census_appends_and_reads_back(tmp_path):
    root = repo(tmp_path / "repo")
    for i in range(2):
        F.append(root, {"schema_version": 1, "at": f"t{i}", "repo": "alpha-repo",
                        "gates": {}})
    got = F.history(root, "alpha-repo")
    assert [row["at"] for row in got] == ["t0", "t1"]


def test_an_unreadable_history_line_is_counted_and_never_dropped(tmp_path):
    """A history that silently shortened itself would answer "did this gate go
    quiet" wrong, in the direction that looks fine."""
    root = repo(tmp_path / "repo")
    F.append(root, {"schema_version": 1, "at": "t0", "repo": "alpha-repo",
                    "gates": {}})
    path = F.census_path(root)
    path.write_text(path.read_text(encoding="utf-8") + "{ truncated\n",
                    encoding="utf-8")
    got = F.history(root, "alpha-repo")
    assert len(got) == 2 and got[1]["_unreadable"]


def test_history_filters_to_the_named_repository(tmp_path):
    root = repo(tmp_path / "repo")
    F.append(root, {"schema_version": 1, "at": "t0", "repo": "alpha-repo",
                    "gates": {}})
    F.append(root, {"schema_version": 1, "at": "t1", "repo": "other", "gates": {}})
    assert [r["at"] for r in F.history(root, "alpha-repo")] == ["t0"]
    assert len(F.history(root)) == 2


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_a_repository_with_no_harness_installed_is_refused(tmp_path):
    """Installing writes into somebody's checkout. It is asked for, not assumed."""
    root = repo(tmp_path / "repo", installed=False)
    with pytest.raises(F.CorpusError) as exc:
        F.walk(entry(root), verbose=False)
    assert "--install" in str(exc.value)


def test_a_path_with_no_name_is_refused(capsys):
    assert F.main(["--path", "."]) == 2
    assert "--name" in capsys.readouterr().err


def test_neither_corpus_nor_path_is_refused(capsys):
    assert F.main([]) == 2
    assert "--corpus" in capsys.readouterr().err


def test_both_corpus_and_path_is_refused(capsys):
    assert F.main(["--corpus", "x.toml", "--path", "."]) == 2
    assert "pass one" in capsys.readouterr().err


def test_an_unknown_repo_name_is_refused(tmp_path, capsys):
    path = corpus(tmp_path, '[[repo]]\nname = "a"\npath = "/x"\ncommit = "abc"\n')
    assert F.main(["--corpus", str(path), "--repo", "nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_the_cli_walks_and_records(tmp_path, capsys):
    root = repo(tmp_path / "repo")
    code = F.main(["--path", str(root), "--name", "alpha-repo"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert F.census_path(root).is_file()
    assert len(F.history(root, "alpha-repo")) == 1


def test_dry_run_walks_and_writes_no_row(tmp_path, capsys):
    root = repo(tmp_path / "repo")
    assert F.main(["--path", str(root), "--name", "alpha-repo", "--dry-run",
                   "--no-commands"]) == 0
    capsys.readouterr()
    assert not F.census_path(root).exists()


def _wide_graph(root: Path, files: int = 25) -> None:
    """One seed file declaring facts about `files` others — more than the
    impact CLI's default `--top 20`, which is the whole point of it."""
    rows: list[dict] = [
        {"kind": "header", "schema": 1},
        {"id": "repo:demo", "type": "repo"},
        {"id": "svc:demo/seed", "type": "svc",
         "props": {"component": "demo", "path": "seed.py"}},
    ]
    for i in range(files):
        rows.append({"id": f"svc:demo/f{i}", "type": "svc",
                     "props": {"component": "demo", "path": f"f{i}.py"}})
        rows.append({"src": "svc:demo/seed", "dst": f"svc:demo/f{i}",
                     "type": "DG_CALLS", "source_file": "seed.py", "line": 1,
                     "extractor": "t", "confidence": 1.0,
                     "props": {"component": "demo"}})
    _graph(root, [json.dumps(r) for r in rows])


def test_the_traversal_counter_is_not_read_off_a_capped_list(tmp_path):
    """`impact.py` lists 20 files unless told otherwise, so a counter read off
    its default would report 20 for every repository above that size and go on
    reporting 20 after the traversal broke. A silent ceiling is the exact
    shape of quiet this module exists to notice, so the cap is lifted and this
    is what says so if it comes back."""
    root = repo(tmp_path / "repo")
    _wide_graph(root, files=25)
    gate = F.gate_scoping(root, seed="demo/seed.py")
    assert gate["status"] == "ok", gate["detail"]
    assert gate["measures"]["ranked"] == 25
