"""A graph that is damaged is not a graph that is empty.

Six callers read `graph.jsonl` — `impact.py`'s own CLI, `pack.py`, the MCP
server, `adr.py`, `impact_ab.py`, and the ADR trigger through `adr` — and every
one of them checks that the file EXISTS and refuses by name and exit 2 if it
does not. Not one of them handled a file that exists and cannot be read, so a
build interrupted part-way through its last line, or a shard a human damaged
resolving a merge conflict, arrived at all six as a `JSONDecodeError`
traceback.

Absent, invalid and empty are three answers. Folding any two of them is how a
wrong one gets given confidently, and this repository has now recorded that
class four times: `prepass.missing_gates` vs `unfired`, the `runner_tier` that
was laundered into `contract`, `sca`'s empty scan reading as clean, and here.

The refusal is deliberately NOT tolerance. `hitl.py` can drop one bad row from
an aggregate and still be honest about the rest; a graph with a hole in it
produces impact answers that are confidently wrong, and CLAUDE.md is explicit
about which of those is worse: *"a graph that looks complete but isn't is worse
than one that admits its gaps, because a human will act on it."*
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph import impact as IM          # noqa: E402
from tools.graph import pack as PACK          # noqa: E402
from tools.graph import snapshot as SN        # noqa: E402
from tools.graph import validate as VA        # noqa: E402
from tools.sdlc import adr as ADR             # noqa: E402

#: One header and one usable node, so the damage is never the only content —
#: a reader that refused an entirely unreadable file but truncated a partly
#: readable one would pass a cruder test than this.
GOOD = (json.dumps({"kind": "header", "schema_version": 1}) + "\n"
        + json.dumps({"kind": "node", "id": "repo:svc", "type": "repo",
                      "props": {"component": "svc"}}) + "\n")

DAMAGE = {
    "a line that does not parse": "{ this was never finished",
    "a line that is a JSON scalar": '"a string, not a row"',
    "a line that is a JSON array": "[1, 2, 3]",
}

#: T413. The header is well-formed JSON and parses fine — `cli.jsonl_rows` sees nothing
#: wrong with it — but declares a schema this build has never heard of. Measured directly
#: (the row's own words): a graph.jsonl with exactly this shape made the impact CLI exit 0
#: and print a full ranked answer, and made the dashboard print its whole report, neither
#: naming the version.
BAD_VERSION = (json.dumps({"kind": "header", "schema_version": 999}) + "\n"
               + json.dumps({"kind": "node", "id": "repo:svc", "type": "repo",
                             "props": {"component": "svc"}}) + "\n")

#: The other cause T111 already learned to word separately at the shard layer: a header
#: that never mentions schema_version at all is a different claim about the file than one
#: that mentions a version this build does not recognise, so it gets its own message below
#: rather than reusing the mismatch sentence for a cause that sentence is not true of.
NO_VERSION_AT_ALL = (json.dumps({"kind": "header"}) + "\n"
                     + json.dumps({"kind": "node", "id": "repo:svc", "type": "repo",
                                   "props": {"component": "svc"}}) + "\n")


def _graph(tmp_path: Path, tail: str = "", *, raw: bytes | None = None) -> Path:
    out = tmp_path / "graph-out"
    out.mkdir(exist_ok=True)
    path = out / "graph.jsonl"
    if raw is not None:
        path.write_bytes(raw)
    else:
        path.write_text(GOOD + (tail + "\n" if tail else ""), encoding="utf-8")
    return out


def _spawn(module: str, args: list[str]) -> subprocess.CompletedProcess:
    """A door as a person meets it: a process, read by exit code and printed text — not
    a function call pytest can render identically for a refusal and a traceback
    (CLAUDE.md; `test_cli_refusals.py`'s own docstring names the item 117 cost of relying
    on the in-process form for a refusal branch)."""
    return subprocess.run([sys.executable, "-m", module, *args],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=60)


# ── the reader ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(DAMAGE))
def test_a_damaged_graph_refuses_by_line_rather_than_truncating(tmp_path, kind):
    """It used to raise `JSONDecodeError` for the first case and
    `AttributeError` for the others — two different tracebacks for one
    condition, neither of them naming the file."""
    out = _graph(tmp_path, DAMAGE[kind])
    with pytest.raises(IM.GraphError) as exc:
        IM.load_graph(out)
    assert "graph.jsonl:3" in str(exc.value)
    assert "damaged" in str(exc.value)


def test_one_bad_byte_is_the_same_refusal_not_a_decode_traceback(tmp_path):
    """`read_text(encoding="utf-8")` with no `errors=` put the decode outside
    every guard, and `UnicodeDecodeError` is a `ValueError`, so it also
    slipped past handlers written for bad JSON."""
    out = _graph(tmp_path, raw=GOOD.encode("utf-8") + b"\xff\xfe nope\n")
    with pytest.raises(IM.GraphError):
        IM.load_graph(out)


#: T635: a fact holding the three separators a JSON string may carry raw - `emit` writes with
#: `ensure_ascii=False` - is one line, never damage.
SEPARATED = json.dumps({"kind": "node", "id": "repo:sep", "type": "repo",
                        "props": {"component": "sep",
                                  "note": "a" + chr(0x2028) + "b" + chr(0x2029) + "c"
                                          + chr(0x85) + "d"}}, ensure_ascii=False)


def test_a_fact_holding_a_unicode_line_separator_loads_whole(tmp_path):
    nodes, _ = IM.load_graph(_graph(tmp_path, SEPARATED))
    assert [n["id"] for n in nodes] == ["repo:svc", "repo:sep"]
    assert nodes[1]["props"]["note"] == json.loads(SEPARATED)["props"]["note"]


@pytest.mark.parametrize("module, extra", [("tools.graph.impact", ["--seed", "svc/x.py"]),
                                           ("tools.graph.validate", [])])
def test_every_consumer_reads_a_fact_holding_a_line_separator(tmp_path, module, extra):
    """T635, spawned: `str.splitlines()` cut the fact's line in three, and each consumer of
    `graph.jsonl` refused an intact graph as damaged at exit 2. Each now answers as it does
    for the same graph without the fact."""
    def answer(where: Path, tail: str) -> subprocess.CompletedProcess:
        where.mkdir()
        out = _graph(where, tail)
        (out / "unresolved.jsonl").write_text("", encoding="utf-8")
        (out / "build_report.json").write_text("{}", encoding="utf-8")
        return _spawn(module, ["--graph-dir", str(out), *extra])

    plain = answer(tmp_path / "plain", "")
    held = answer(tmp_path / "held", SEPARATED)
    combined = held.stdout + held.stderr
    assert "damaged" not in combined, combined
    assert "Traceback (most recent call last)" not in combined, combined
    assert held.returncode == plain.returncode, (plain.stdout + plain.stderr, combined)


def test_an_intact_graph_still_loads(tmp_path):
    """The negative control: the header is dropped, the node is kept, and the
    refusal must not fire on an ordinary build."""
    nodes, edges = IM.load_graph(_graph(tmp_path))
    assert [n["id"] for n in nodes] == ["repo:svc"] and edges == []


def test_an_empty_graph_is_empty_and_not_an_error(tmp_path):
    """Empty is the third answer, and it must stay distinct from damaged: a
    build that legitimately found nothing is not a build that broke."""
    out = tmp_path / "empty"
    out.mkdir()
    (out / "graph.jsonl").write_text("", encoding="utf-8")
    assert IM.load_graph(out) == ([], [])


# ── T413: the header's schema_version, not just its "kind" ───────────────────
#
# `load_graph` read the header row only to decide what to DROP (`kind != "header"`); it
# never looked at what the header DECLARED. A shard from a schema this build cannot
# interpret sailed through the merged graph.jsonl the same way it used to sail through a
# shard before T111 — except T111 only closed that gap at the shard layer
# (`core/lib/emit.py`), and the merged document is read by a second, independent
# function. Two distinct causes get two distinct sentences (the warning this row itself
# carries): a header that never mentions schema_version is a different claim about the
# file than one that names a version nobody recognises.


def test_load_graph_refuses_a_schema_version_it_does_not_know(tmp_path):
    """MEASURED before this fix: `IM.load_graph(_graph(tmp_path, raw=BAD_VERSION...))`
    returned `([{"id": "repo:svc", ...}], [])` with no complaint at all — the exact
    silent pass-through the row's own impact-CLI measurement describes.

    T413 ROUND 2: the converse of the sibling test's own check, closing a surviving
    mutant the reviewer found — making this arm raise the ABSENT-key sentence (with
    999 merely appended, e.g. "...(got 999)") passed every assertion that only checked
    for "schema_version" and "999" being present. That message would be FALSE of this
    cause: a version WAS declared, so claiming "no schema_version at all" mis-describes
    the very input that produced it (the warning this row itself carries). Checking
    presence alone is not enough; the wrong sentence must be provably absent too."""
    out = _graph(tmp_path, raw=BAD_VERSION.encode("utf-8"))
    with pytest.raises(IM.GraphError) as exc:
        IM.load_graph(out)
    msg = str(exc.value)
    assert "schema_version" in msg and "999" in msg, msg
    assert "no schema_version" not in msg, msg


def test_load_graph_refuses_a_header_with_no_schema_version_at_all(tmp_path):
    """The other cause. Must NOT reuse the mismatch sentence above: there is no wrong
    version here to name, so a message that named one would be false of this input."""
    out = _graph(tmp_path, raw=NO_VERSION_AT_ALL.encode("utf-8"))
    with pytest.raises(IM.GraphError) as exc:
        IM.load_graph(out)
    msg = str(exc.value)
    assert "no schema_version" in msg, msg
    assert "999" not in msg, msg


def test_the_store_loader_refuses_an_unknown_schema_version(tmp_path):
    """`core/store/load.py`'s own docstring: 'The reader is `impact.load_graph`, not a
    second one.' `load()` reaches `load_graph` before it ever touches `store`, so a
    `store=None` proves the delegation without needing a real backend."""
    from tools.graph.core.store import load as SL

    out = _graph(tmp_path, raw=BAD_VERSION.encode("utf-8"))
    with pytest.raises(IM.GraphError) as exc:
        SL.load(out, None)
    assert "schema_version" in str(exc.value)


@pytest.mark.parametrize("bad", ["BAD_VERSION", "NO_VERSION_AT_ALL"])
def test_the_impact_cli_refuses_an_unknown_schema_version(tmp_path, bad):
    """T413's own measurement, closed: 'the impact command EXITED 0 AND PRINTED A FULL
    RANKED ANSWER.' Spawned per CLAUDE.md — a CLI's contract is a process."""
    out = _graph(tmp_path, raw=globals()[bad].encode("utf-8"))
    proc = _spawn("tools.graph.impact",
                  ["--graph-dir", str(out), "--seed", "svc/x.py"])
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2, combined
    assert "schema_version" in combined, combined
    assert "Traceback (most recent call last)" not in combined, combined


@pytest.mark.parametrize("bad", ["BAD_VERSION", "NO_VERSION_AT_ALL"])
def test_the_validate_cli_refuses_an_unknown_schema_version(tmp_path, bad):
    """The other half of the same measurement: the validator 'printed its whole
    dashboard — NEITHER NAMED THE VERSION.' Spawned, same reason as above."""
    out = _graph(tmp_path, raw=globals()[bad].encode("utf-8"))
    (out / "unresolved.jsonl").write_text("", encoding="utf-8")
    (out / "build_report.json").write_text("{}", encoding="utf-8")
    proc = _spawn("tools.graph.validate", ["--graph-dir", str(out)])
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2, combined
    assert "schema_version" in combined, combined
    assert "Traceback (most recent call last)" not in combined, combined


def test_the_mcp_server_refuses_an_unknown_schema_version_at_startup(tmp_path):
    """The third door, named in the store docstring's own reasoning: `GraphService.data`
    calls the same `load_graph`, so the startup probe that already refuses a damaged
    graph (sibling test below) must refuse a merely-unfamiliar one too. Skipped, not
    failed, where `fastmcp` (the `serve` extra) is not installed — same guard the sibling
    damaged-graph MCP test already uses."""
    pytest.importorskip("fastmcp")
    out = _graph(tmp_path, raw=BAD_VERSION.encode("utf-8"))
    proc = _spawn("tools.graph.mcp_server", ["--graph-dir", str(out)])
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 2, combined
    assert "schema_version" in combined, combined
    assert "Traceback (most recent call last)" not in combined, combined


# ── T413: a test fails if a new reader is added without a refusal ────────────
#
# `["schema_version"]` / `.get("schema_version"` / `"schema_version" in`/`not in` are all
# READS of an already-parsed row. A dict LITERAL that STATES one (`"schema_version":
# SCHEMA_VERSION` — every module's own output stamp: metrics.json, the MCP status reply,
# impact's own pack/diff output, build_report.json) never matches this, because a colon
# right after the key is construction, not inspection. Confirmed against the live tree
# before this fix: the only match anywhere under tools/graph was `core/lib/emit.py`
# (T111's shard-level reader) — nowhere read the field for the merged document at all,
# which is the defect this row was filed over.

GRAPH_ROOT = ROOT / "tools" / "graph"

#: One entry per document layer: the shard reader (T111, core/lib/emit.py) and the
#: merged-document reader (T413, impact.py's `load_graph` — every other consumer
#: delegates to it, proven above and by grep in the worker log). A THIRD entry appearing
#: here uninvited is a second, undelegated reader — the exact shape this row closed.
KNOWN_SCHEMA_VERSION_READERS = {"core/lib/emit.py", "impact.py"}

_SCHEMA_VERSION_READ = re.compile(
    r'\["schema_version"\]|\.get\("schema_version"|"schema_version" (?:not )?in ')


def _read_shaped_schema_version_files(root: Path) -> set[str]:
    found = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/") or "__pycache__" in rel:
            continue
        if _SCHEMA_VERSION_READ.search(path.read_text(encoding="utf-8")):
            found.add(rel)
    return found


def test_only_the_known_readers_inspect_an_incoming_schema_version():
    """The guard on the shape T413 chose. A file added to this set without a matching
    entry in `KNOWN_SCHEMA_VERSION_READERS` (and a refusal test beside it, per the
    section above) is a new, independent reader of a document header — the version
    check silently stops covering whatever door does not delegate to one of these two."""
    found = _read_shaped_schema_version_files(GRAPH_ROOT)
    assert found == KNOWN_SCHEMA_VERSION_READERS, (
        f"files reading an incoming schema_version: {sorted(found)}, expected exactly "
        f"{sorted(KNOWN_SCHEMA_VERSION_READERS)} — a new entry is a new, independent "
        f"reader of graph.jsonl's (or a shard's) header and needs its own refusal test, "
        f"or should delegate to impact.load_graph instead of reading the field itself; "
        f"a missing entry means a refusal this project relies on was deleted")


def test_the_guard_actually_catches_a_new_reader(tmp_path):
    """Positive control: a synthetic third reader is not silently invisible to the scan
    above (Auditor class 8 — a guard proven only by its own green is not proven)."""
    (tmp_path / "rogue.py").write_text(
        'def f(row):\n    return row["schema_version"]\n', encoding="utf-8")
    assert _read_shaped_schema_version_files(tmp_path) == {"rogue.py"}


def test_the_guard_does_not_flag_writing_a_schema_version(tmp_path):
    """Negative control: stating a schema_version in an output dict literal (what every
    module's own SCHEMA_VERSION constant is for) is construction, not inspection, and
    must not trip this — otherwise the guard's 'exactly two' would be uncheckable noise."""
    (tmp_path / "writer.py").write_text(
        'SCHEMA_VERSION = 1\ndef f():\n    return {"schema_version": SCHEMA_VERSION}\n',
        encoding="utf-8")
    assert _read_shaped_schema_version_files(tmp_path) == set()


# ── T413 ROUND 2: a test fails if a new reader is added WITHOUT a refusal too ──
#
# The guard above is keyed on READING the version, so it fires when a new reader is
# added WITH a check (the set grows past what is pinned) and is BLIND to one added
# WITHOUT — exactly the shape this row was filed over: a reader that opens the document
# and never looks at the version leaves it silent at exit 0. This one is keyed on the
# OPEN instead: every module that calls `cli.jsonl_rows` at all, whether or not it goes
# on to check anything. The reviewer established the known set directly: the only
# non-test callers of that row helper under tools/graph are the two modules T413
# already fixed (`impact.load_graph`, `validate._rows` — the latter now only for
# unresolved.jsonl, but it still opens the row stream, so it still belongs here).
# Kept BESIDE the read-shaped guard, not in place of it: one catches a reader that
# stops checking (the set shrinks), the other catches a reader that never started.

KNOWN_JSONL_ROWS_CALLERS = {"impact.py", "validate.py"}


def _jsonl_rows_callers(root: Path) -> set[str]:
    """Every module under `root` (tests excluded) that CALLS `cli.jsonl_rows` — by AST,
    not text, so a bare `from tools.cli import jsonl_rows; jsonl_rows(path)` is seen
    exactly like the `cli.jsonl_rows(path)` spelling both current callers use, and a
    docstring that merely NAMES the function (`tools/cli.py`'s own module docstring
    does, twice) is not mistaken for a call."""
    found = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("tests/") or "__pycache__" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else None)
            if name == "jsonl_rows":
                found.add(rel)
                break
    return found


def test_only_the_known_modules_open_the_graph_documents_row_stream():
    """The pin. MEASURED against the real tree (not simulated) both before writing this
    test and again just now: `_jsonl_rows_callers(GRAPH_ROOT)` returns exactly
    `{"impact.py", "validate.py"}` today — the same two files at T413 round 1's base
    commit and at HEAD, since round 1 changed WHAT `validate.py` does with the stream,
    never WHETHER it opens one. A third entry here — checked or not — is a new,
    independent reader of a document header this project has not decided anything
    about."""
    found = _jsonl_rows_callers(GRAPH_ROOT)
    assert found == KNOWN_JSONL_ROWS_CALLERS, (
        f"modules that open a JSONL row stream directly: {sorted(found)}, expected "
        f"exactly {sorted(KNOWN_JSONL_ROWS_CALLERS)} — a new one needs its own refusal "
        f"(and a place in this set), or should call impact.load_graph instead of "
        f"opening the file itself")


def test_the_open_guard_catches_a_reader_that_never_checks_the_version(tmp_path):
    """T413 round 2's own measurement, reproduced: a probe that opens the document
    through the shared row helper and never looks at the version left the read-shaped
    guard silent at exit 0. This guard is keyed on the open, not the read, so it does
    not miss it — the row's own defect shape, closed."""
    (tmp_path / "rogue.py").write_text(
        'from tools import cli\n'
        'def f(path):\n'
        '    return [line.row for line in cli.jsonl_rows(path) if line.row]\n',
        encoding="utf-8")
    assert _jsonl_rows_callers(tmp_path) == {"rogue.py"}


def test_the_open_guard_does_not_flag_a_module_that_never_opens_one(tmp_path):
    """Negative control: `tools/cli.py`'s own module docstring NAMES `jsonl_rows` twice
    in prose without calling it — text that would defeat a substring-based scan. Must
    not trip this, or the pinned set would be noise the moment prose mentions the name."""
    (tmp_path / "innocent.py").write_text(
        '"""See cli.jsonl_rows for the contract every other reader follows.\n\n'
        'And `impact.load_graph`, `graph/validate._rows` decoded strictly."""\n'
        'def f():\n    return 1\n',
        encoding="utf-8")
    assert _jsonl_rows_callers(tmp_path) == set()


def test_the_open_guard_sees_a_bare_imported_call_not_only_the_cli_prefixed_one(
        tmp_path):
    """A text search for the literal `cli.jsonl_rows(` would miss a caller that
    imported the name directly — `from tools.cli import jsonl_rows` — and called it
    unprefixed. The AST walk above resolves either spelling to the same `jsonl_rows`
    name; this is the case a naive substring version of this guard would have missed."""
    (tmp_path / "bare_import.py").write_text(
        'from tools.cli import jsonl_rows\n'
        'def f(path):\n'
        '    return list(jsonl_rows(path))\n',
        encoding="utf-8")
    assert _jsonl_rows_callers(tmp_path) == {"bare_import.py"}


# ── every caller answers the way it answers an ABSENT graph ──────────────────


def test_the_impact_cli_refuses_with_a_reason_and_exit_2(tmp_path, capsys):
    """Two lines above, an absent graph prints a reason and returns 2. A
    damaged one exited 1 through a traceback — and in this CLI 1 is not a
    "could not look" code, it is what Python gives an escaping exception."""
    out = _graph(tmp_path, DAMAGE["a line that does not parse"])
    code = IM.main(["--graph-dir", str(out), "--seed", "svc/x.py"])
    assert code == 2 and "damaged" in capsys.readouterr().out


def test_the_pack_cli_refuses_the_same_way(tmp_path, capsys):
    """`pack.py` carries the identical absent-graph refusal, and carried the
    identical hole behind it."""
    out = _graph(tmp_path, DAMAGE["a line that is a JSON scalar"])
    code = PACK.main(["--graph-dir", str(out), "--seed", "svc/x.py",
                      "--out", str(tmp_path / "pack")])
    assert code == 2 and "damaged" in capsys.readouterr().out


def test_the_dashboard_refuses_rather_than_grading_a_short_denominator(
        tmp_path, capsys):
    """The sharpest of the six. `validate.py` grades RATIOS, so truncating at
    the damaged line would not fail — it would report a smaller denominator
    and a plausible percentage, and the M1 gate verdict is read off exactly
    these numbers.

    T413 CORRECTED this assertion. `validate.py` used to read graph.jsonl through
    its own second implementation of the header/row split (`_rows`), worded
    "these ratios would be computed over a truncated denominator"; it now
    delegates to `impact.load_graph` for graph.jsonl (only `unresolved.jsonl`
    still goes through `_rows`), so this door's line-damage wording is now the
    same as every other caller's ("damaged"), which is what the other five
    tests in this file already assert. The line-level damage behaviour this
    test pins is unchanged — only the WORDING changed, as a direct,
    deliberate consequence of removing the second reader (T413's own point:
    two independent readers of one header are how a check silently stops
    covering one of the doors). OLD: `"truncated denominator" in ...`. NEW,
    below: `"damaged" in ...`, matching `test_the_impact_cli_refuses_with_a_
    reason_and_exit_2` and `test_the_pack_cli_refuses_the_same_way` above."""
    out = _graph(tmp_path, DAMAGE["a line that does not parse"])
    (out / "unresolved.jsonl").write_text("", encoding="utf-8")
    (out / "build_report.json").write_text("{}", encoding="utf-8")
    code = VA.main(["--graph-dir", str(out)])
    assert code == 2
    assert "damaged" in capsys.readouterr().out


def test_the_adr_check_names_the_damage_instead_of_inventing_obligations(
        tmp_path):
    """`adr.load`'s own docstring says the absence check lives there so no
    caller has to remember it — and a DAMAGED graph went straight past every
    `AdrError` handler. This CLI's exit 1 means *there are obligations*, so a
    truncated build reported ADR obligations that do not exist and held G2
    shut for a reason nobody could act on."""
    ADR._PARSED.clear()
    gdir = ADR.graph_dir(tmp_path)
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "graph.jsonl").write_text(
        GOOD + DAMAGE["a line that does not parse"] + "\n", encoding="utf-8")
    with pytest.raises(ADR.AdrError) as exc:
        ADR.load(tmp_path)
    assert "damaged" in str(exc.value)


def test_the_mcp_server_still_refuses_a_damaged_graph_at_startup(tmp_path,
                                                                 capsys):
    """It caught `(RulesError, OSError, ValueError)`, and `JSONDecodeError`
    IS a `ValueError` — so this was the one caller already covered, by
    accident of the exception hierarchy. Naming the refusal `GraphError`
    (a `RuntimeError`) would have QUIETLY REMOVED that cover; this pins that
    it did not."""
    pytest.importorskip("fastmcp")
    from tools.graph import mcp_server as MCP
    out = _graph(tmp_path, DAMAGE["a line that does not parse"])
    assert MCP.main(["--graph-dir", str(out)]) == 2
    assert "damaged" in capsys.readouterr().out


def test_graph_error_is_not_a_value_error_so_the_cover_had_to_be_explicit():
    """The mechanism behind the test above, stated so it cannot regress
    silently: if `GraphError` were a `ValueError` the MCP assertion would pass
    for the wrong reason and prove nothing about the tuple."""
    assert not issubclass(IM.GraphError, ValueError)


# ── the committed snapshot, which humans resolve conflicts in ────────────────


def _shard(tmp_path: Path, manifest: str) -> tuple[Path, dict]:
    comp = {"name": "svc", "path": "."}
    d = SN.shard_dir(tmp_path, comp)
    d.mkdir(parents=True, exist_ok=True)
    (d / SN.SHARD).write_text(GOOD, encoding="utf-8")
    (d / SN.MANIFEST).write_text(manifest, encoding="utf-8")
    return d, comp


@pytest.mark.parametrize("manifest", [
    "<<<<<<< HEAD\n",           # the realistic one: a conflict left in place
    "",                         # truncated by an interrupted write
    '"not an object"',
    "{ half written",
])
def test_a_shard_whose_manifest_cannot_be_read_is_mismatched_not_a_traceback(
        tmp_path, manifest):
    """`snapshot.json` is COMMITTED and diffed in pull requests — CLAUDE.md
    makes `rebuild == snapshot` an extractor regression test — so a human
    editing it is the ordinary case, not the exotic one. The manifest was read
    with a bare `json.loads` BEFORE the digest check that would otherwise have
    caught the damage, so the guard never got the chance.

    Batch 16: the one reading (`read_shard`) refuses such a shard and names the
    manifest's cause; `mismatched` is the load's list of refused shards, and the
    caller reports each by component name with its own words."""
    _, comp = _shard(tmp_path, manifest)
    _, present, missing, mismatched = SN.load(tmp_path, [comp])
    assert mismatched == ["svc"] and present == [] and missing == []
    assert SN.read_shard(tmp_path, comp).refusal_kind == "manifest"


def test_an_unreadable_manifest_is_refused_never_read_as_no_stamps(tmp_path):
    """The freshness half of the same read. Batch 16 gives it a verdict of its own:
    refused, with its cause. Folded into "no stamps", it told `--check-fresh` "no
    committed shard" of a shard that exists, while the load called it a digest
    mismatch; `stamps_of` reads the manifest the reading holds, so it has none."""
    _, comp = _shard(tmp_path, "{ half written")
    reading = SN.read_shard(tmp_path, comp)
    assert reading.refusal_kind == "manifest"
    assert reading.refused == ("its committed snapshot.json exists but could not be read "
                               "(not JSON)")
    assert SN.stamps_of(SN.read_manifest(SN.shard_dir(tmp_path, comp)).manifest) is None


def test_an_intact_shard_still_loads(tmp_path):
    """The negative control, and the one that would catch a manifest reader
    that returned None for everything."""
    comp = {"name": "svc", "path": "."}
    d = SN.shard_dir(tmp_path, comp)
    d.mkdir(parents=True, exist_ok=True)
    (d / SN.SHARD).write_text(GOOD, encoding="utf-8")
    (d / SN.MANIFEST).write_text(
        json.dumps({"sha256": SN._digest(d)}), encoding="utf-8")
    _, present, missing, mismatched = SN.load(tmp_path, [comp])
    assert present == ["svc"] and mismatched == [] and missing == []
