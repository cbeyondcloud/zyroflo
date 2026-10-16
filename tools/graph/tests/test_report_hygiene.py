"""Item 120 - build.py overwrote the report it was asked to check, and put the
build machine's directory layout into a file adopters commit.

Both found by item 114's walk, in the module that writes `.sdlc/graph/`.

F6 is *a check that writes*, the fourth instance here after items 96, 100 and 65.
Asking whether the graph was fresh replaced the committed record of how it was
built - 12 keys and 1,237 lines became 3 keys and 5 - and then blocked the run it
was asked during, because the worktree gate correctly refused a tree the harness
had just dirtied. **The hazard was already written down and fixed for ONE
caller**: `mcp_server.py`'s own docstring said `check_freshness` writes a
`build_report.json` wherever it is pointed, and handed it a scratch directory.

F7 is item 57's finding in a second module and a worse place: `.sdlc/graph/` is
committed BY DESIGN (item 102), so every adopter shipping their graph published
the absolute path of the machine that built it - and the harness's own
private-coordinate guard sweeps the HARNESS tree, so it cannot see a file the
harness writes into somebody else's repository.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph import build as B  # noqa: E402
from tools.graph.tests.test_incremental_freshness import (  # noqa: E402
    COMPONENTS, run, ws)


# ── F6: the question must not overwrite the answer to a different question ──


def test_check_fresh_leaves_a_committed_build_report_untouched(tmp_path):
    """**The item, reproduced against a real report the build itself wrote.**

    On the walk this replaced a 1,237-line committed record with a five-line
    freshness verdict, in a file `.sdlc/graph/` ships to every adopter.
    """
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    report = tmp_path / "seed" / "build_report.json"
    before = report.read_bytes()
    keys_before = set(json.loads(before))
    assert len(keys_before) > 5, (
        "the fixture build wrote a thin report, so this proves nothing")

    # Point the check at the directory holding it - which is what the CLI does,
    # and what the MCP server had to work around.
    assert run(work, "seed", "--check-fresh") in (0, 2)

    assert report.read_bytes() == before, (
        "a question about the graph overwrote the record of how it was built")
    assert set(json.loads(report.read_text(encoding="utf-8"))) == keys_before


def test_check_freshness_writes_no_file_at_all(tmp_path):
    """Not redirected to scratch - GONE. A question does not persist its answer
    into another question's file, and nothing anywhere read what it wrote.

    **The sweep is the WHOLE tree, not one directory.** The first cut watched
    only the graph output dir, and a mutation writing the same report into the
    workspace root survived it - "writes nothing" is a claim about everywhere.
    """
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")

    def tree() -> dict:
        return {str(f.relative_to(tmp_path)): f.stat().st_mtime_ns
                for f in tmp_path.rglob("*") if f.is_file()}

    before = tree()
    assert before, "the sweep looked at nothing"
    verdict = B.check_freshness(work / "workspace.toml")
    assert verdict["status"] in ("fresh", "stale")
    after = tree()
    assert set(after) == set(before), (
        f"the freshness question created or removed files: "
        f"{sorted(set(after) ^ set(before))[:5]}")
    assert after == before, (
        f"the freshness question rewrote: "
        f"{sorted(k for k in after if after[k] != before[k])[:5]}")


def test_the_snapshot_load_report_carries_no_path_either(tmp_path):
    """`--from-snapshot` writes its OWN report, at a second site the first cut of
    this item's tests never reached - a mutation putting `root` back there
    survived. Two writers, one rule."""
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    assert run(work, "loaded", "--from-snapshot") in (0, 2)
    doc = json.loads((tmp_path / "loaded" / "build_report.json")
                     .read_text(encoding="utf-8"))
    assert "root" not in doc["workspace"], doc["workspace"]
    assert doc["workspace"].get("name")
    assert str(tmp_path) not in json.dumps(doc["workspace"])


def test_the_cli_hands_the_printer_the_live_workspace(tmp_path, capsys):
    """**Composed, not merely present.** Every other printer test calls
    `_print_report` directly, so a mutation making `main` stop passing the live
    workspace survived all of them - the producer/consumer gap this project has
    found more often than any other. This runs the real CLI."""
    work = ws(tmp_path)
    capsys.readouterr()
    run(work, "cli", "--publish-snapshot")
    printed = capsys.readouterr().out
    assert str(work) in printed, (
        "the build summary no longer tells the operator where it ran")


def test_the_verdict_is_still_returned_and_still_decides_the_exit_code(tmp_path):
    """The control. Removing the write must not remove the answer: `--check-fresh`
    is a gate, and exit 0 has to keep meaning the committed graph is the tree."""
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    assert run(work, "fresh", "--check-fresh") == 0
    (work / COMPONENTS[0] / "app.properties").write_text(
        "changed=1" + chr(10), encoding="utf-8")
    assert run(work, "fresh", "--check-fresh") == 2


def test_check_freshness_takes_no_output_directory(tmp_path):
    """The parameter went with the write. It was used for nothing else - only
    `mkdir` and the write itself - so keeping it would leave a caller able to
    ask for an output that no longer exists."""
    import inspect
    assert "out" not in inspect.signature(B.check_freshness).parameters


# ── F7: the NAME, never the path ────────────────────────────────────────────


def _report_after_build(tmp_path) -> dict:
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    return json.loads((tmp_path / "seed" / "build_report.json")
                      .read_text(encoding="utf-8"))


def strings(node):
    """Every string a parsed document holds, its keys aside.

    The PARSED values, never `json.dumps` of them. The first cut of this sweep searched the
    serialized blob and PASSED at the pre-item SHA against a report carrying four absolute
    paths, because json escapes a backslash: a Windows path with single separators is never a
    substring of a document that spells them doubled. It was vacuous on exactly the platform
    it was written on, and the red proof is what said so."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)


def _carries_no_coordinate(doc, *coordinates: Path) -> None:
    found = list(strings(doc))
    assert found, "the sweep looked at nothing"
    for coordinate in map(str, coordinates):
        carried = [v for v in found if coordinate in v]
        assert not carried, (
            f"the committed document carries {coordinate}, which is the build "
            f"machine's directory layout published to every adopter: "
            f"{carried[:3]}")


def test_the_committed_report_carries_no_absolute_path(tmp_path):
    """**The property, swept over the whole document rather than the keys I
    happened to look at.** The walk named two paths; there were FOUR."""
    _carries_no_coordinate(_report_after_build(tmp_path), tmp_path, ROOT)


def test_the_load_report_and_the_run_manifest_carry_no_absolute_path_either(tmp_path):
    """The sweep at the load's report and the run's `manifest.json` too (batch 22, D22-104):
    both are written into `--out`, which a repository commits."""
    work = ws(tmp_path)
    run(work, "seed", "--publish-snapshot")
    assert run(work, "loaded", "--from-snapshot") in (0, 2)
    for doc in (tmp_path / "loaded" / "build_report.json", tmp_path / "seed" / "manifest.json"):
        _carries_no_coordinate(json.loads(doc.read_text(encoding="utf-8")), tmp_path, ROOT)


def test_the_workspace_block_keeps_its_identity_and_loses_its_coordinates(tmp_path):
    """Identity survives: a report that named nothing would be a worse record,
    not a safer one. `root` is gone rather than shortened, because a basename of
    it is `name` said twice."""
    ws_block = _report_after_build(tmp_path)["workspace"]
    assert "root" not in ws_block
    assert ws_block["name"], "the report no longer says which workspace it is about"
    for key in ("config", "inferred_from", "profile"):
        assert key in ws_block, f"{key} was dropped rather than shortened"
        assert "/" not in ws_block[key] and chr(92) not in ws_block[key], (
            f"{key} still carries directories: {ws_block[key]!r}")


def test_a_single_repo_report_still_names_its_workspace(tmp_path):
    """A repository with no workspace config declares no `name`, so the fallback
    is what keeps the report from identifying nothing at all."""
    assert B._workspace_name({"root": Path("/somewhere/orders")}) == "orders"
    assert B._workspace_name({"name": "declared",
                              "root": Path("/somewhere/orders")}) == "declared"
    assert B._workspace_name({}) == ""


def test_the_console_still_shows_the_operator_where_the_build_ran(tmp_path, capsys):
    """**The half that is kept, and why the fix is not just deletion.** A console
    line is nobody else's repository. The path comes from the LIVE workspace -
    reading it back out of the document is how it got into the document."""
    doc = _report_after_build(tmp_path)
    capsys.readouterr()          # the BUILD printed a path; this is about the
                                 # printer, and without the clear the assertion
                                 # below would pass on the build's own line
    B._print_report(doc, {"root": tmp_path / "here"})
    assert str(tmp_path / "here") in capsys.readouterr().out


def test_the_printer_without_a_live_workspace_prints_no_path(tmp_path, capsys):
    """The control: the report alone cannot produce a path, because it no longer
    holds one. Without this the previous test would pass on a report that still
    carried it."""
    doc = _report_after_build(tmp_path)
    capsys.readouterr()          # same reason, and here it is the difference
                                 # between a real check and a vacuous one
    B._print_report(doc)
    printed = capsys.readouterr().out
    assert str(tmp_path) not in printed
    assert doc["workspace"]["name"] in printed


@pytest.mark.parametrize("value,want", [
    ("/a/b/workspace.toml", "workspace.toml"),
    ("C:" + chr(92) + "a" + chr(92) + "b" + chr(92) + "orderflow", "orderflow"),
    ("", ""),
    (None, ""),
])
def test_a_name_is_the_last_segment_and_nothing_above_it(value, want):
    assert B._name_of(value) == want


def test_a_windows_path_is_stripped_on_every_platform():
    """**`Path(text).name` is not enough, and this is the assertion that says so.**

    On POSIX a backslash is an ordinary character, so `PurePosixPath` hands a
    Windows path back whole and a helper built on it would publish exactly the
    coordinates it exists to strip. A report is written on one machine and read
    on any.

    Item 122's first cut passed on Windows and failed on Linux; this is the same
    class, found by auditing rather than by CI.
    """
    from pathlib import PurePosixPath
    windows = "C:" + chr(92) + "a" + chr(92) + "b" + chr(92) + "orderflow"
    assert B._name_of(windows) == "orderflow"
    assert PurePosixPath(windows).name == windows, (
        "the control: if PurePosixPath ever learns backslashes this test is "
        "asserting nothing")
