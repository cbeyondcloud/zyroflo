"""Every machine-readable transcription is held equal to the `EXPECTED.md` table it copies.

`chain.py`, `chain_further.py`, `chain_pantry.py` and `cross_repo.py` each open with the same
sentence — *this file is a transcription, never a source* — and each copies the answer key's
own table into Python, line numbers included. Until this file, **nothing composed the
transcription with the key**: the golden tests read the Python tuples and the integrity test
read only two prose substrings and a git ordering check, so the key's table was parsed by
nothing. A line number or an edge type could drift on either side and every test stayed green
(W153: mutating `urls.js:4` -> `:77`, `sql:2` -> `:99`, or `DG_EXPOSES` -> a non-type all
survived). Producer and consumer were never composed, inside the kill gate's own evidence
document.

Each test below parses the relevant `| ... |` table straight out of `EXPECTED.md` and
reconciles it against the imported transcription, row by row and in order. The reconciliation
runs on the fields that survive the key's own shorthand — edge type, cited `file:line`, and
(where the key writes the node id in full) the source and target nodes; the further-chain key
abbreviates its nodes with an ellipsis, so those rows are held by edge type and cited line.

`test_the_reconciliation_detects_a_drift` is the guard on the guard (item 115): the tests
here are green today only because the transcriptions are correct, so a synthetic key is
drifted three ways and the comparison is watched reporting each — a guard nobody can watch
fail is the exact class this file exists to close.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests import (  # noqa: E402
    chain,
    chain_further,
    chain_pantry,
    cross_repo,
)

TESTS = Path(__file__).resolve().parent
ORDERFLOW = TESTS / "fixtures" / "orderflow" / "EXPECTED.md"
PANTRY = TESTS / "fixtures" / "pantry" / "EXPECTED.md"

#: The `->` in every "From -> To" cell. Written as an escape, never typed as the raw
#: character (WORKER-RULES 18: an exotic character that vanishes between editor and disk is
#: indistinguishable from one the code handled).
ARROW = "→"

_BACKTICK = re.compile(r"`([^`]*)`")
#: A `path:line` or `path:line-line` citation, or a bare `:line` (whose path is the previous
#: span's — the key writes `OrderController.java:9` + `:12`).
_LINEREF = re.compile(r"(?P<path>[^\s`]*?):(?P<a>\d+)(?:-(?P<b>\d+))?")


# ── markdown table parsing ───────────────────────────────────────────────────
def _cells(line: str) -> list[str]:
    """The cells of one pipe-table row, outer pipes dropped and each cell stripped."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_after(text: str, heading: str) -> list[list[str]]:
    """The data rows of the first pipe-table following the line that contains `heading`.

    A markdown table is the run of consecutive `|`-lines; its first line is the header and its
    second is the `---` separator, so the data rows are the remainder.
    """
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if heading in ln)
    first = next(k for k in range(start + 1, len(lines)) if lines[k].lstrip().startswith("|"))
    body = []
    k = first
    while k < len(lines) and lines[k].lstrip().startswith("|"):
        body.append(lines[k])
        k += 1
    return [_cells(r) for r in body[2:]]


def _strip(cell: str) -> str:
    return cell.strip().strip("`").strip()


def _src_dst(from_to: str) -> tuple[str, str]:
    sep = ARROW if ARROW in from_to else "->"
    left, _, right = from_to.partition(sep)
    return _strip(left), _strip(right)


def _citations(source_cell: str) -> list[tuple[str, set[int]]]:
    """Every `(file, {lines})` the Source cell points at.

    Citations live inside backtick spans; a bare `:12` span inherits the previous span's path,
    and a `5-6` range expands to both lines.
    """
    out: list[tuple[str, set[int]]] = []
    last_path = ""
    for span in _BACKTICK.findall(source_cell):
        m = _LINEREF.search(span)
        if not m:
            continue
        path = m.group("path") or last_path
        if not path:
            continue
        last_path = path
        a = int(m.group("a"))
        b = int(m.group("b")) if m.group("b") else a
        out.append((path, set(range(a, b + 1))))
    return out


# ── reconciliation ───────────────────────────────────────────────────────────
def _path_match(a: str, b: str) -> bool:
    """True when the shorter path is a trailing segment run of the longer — the key and the
    transcription cite the same file with different amounts of directory prefix."""
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _line_cited(citations: list[tuple[str, set[int]]], source: str,
                suffix: str, line: int) -> bool:
    if line == 0:
        # A whole-file claim ("(whole file; line 0)"): the key names the file, not a line.
        return suffix.rsplit("/", 1)[-1] in source
    return any(_path_match(path, suffix) and line in lines for path, lines in citations)


def _src_match(py_src: str, key_src: str) -> bool:
    # The key writes `svc:orders-api`; the transcription carries the full `svc:orders-api@repo`.
    return py_src == key_src or py_src.startswith(key_src + "@")


def _expected_chain_rows(cells_rows: list[list[str]]) -> list[dict]:
    rows = []
    for cells in cells_rows:  # [#, Edge, From -> To, Source, Mechanism]
        src, dst = _src_dst(cells[2])
        rows.append({"edge": _strip(cells[1]), "src": src, "dst": dst,
                     "citations": _citations(cells[3]), "source": cells[3]})
    return rows


def _py_chain_rows(tuples, edge, src, dst, suffix, line) -> list[dict]:
    return [{"edge": r[edge], "src": r[src], "dst": r[dst],
             "suffix": r[suffix], "line": r[line]} for r in tuples]


def reconcile_chain(py_rows: list[dict], key_rows: list[dict], *,
                    check_src: bool, check_dst: bool) -> list[str]:
    """Empty when the transcription and the answer key agree; otherwise one string per
    disagreement. Factored out so the self-test can drive it on a synthetic key."""
    if len(py_rows) != len(key_rows):
        return [f"row count: transcription {len(py_rows)} vs answer key {len(key_rows)}"]
    problems: list[str] = []
    for i, (py, key) in enumerate(zip(py_rows, key_rows), start=1):
        if py["edge"] != key["edge"]:
            problems.append(f"hop {i}: edge {py['edge']!r} but the key says {key['edge']!r}")
        if check_src and not _src_match(py["src"], key["src"]):
            problems.append(f"hop {i}: source {py['src']!r} but the key says {key['src']!r}")
        if check_dst and py["dst"] != key["dst"]:
            problems.append(f"hop {i}: target {py['dst']!r} but the key says {key['dst']!r}")
        if not _line_cited(key["citations"], key["source"], py["suffix"], py["line"]):
            problems.append(
                f"hop {i}: {py['suffix']}:{py['line']} is not cited by the key "
                f"(the key's Source is {key['source']!r})")
    return problems


# ── the four transcriptions ──────────────────────────────────────────────────
def test_orderflow_chain_matches_its_answer_key():
    key = _expected_chain_rows(_table_after(ORDERFLOW.read_text(encoding="utf-8"), "## The chain"))
    assert len(key) == 6, f"parsed {len(key)} chain rows from the key, expected 6"
    py = _py_chain_rows(chain.CHAIN, 1, 2, 3, 4, 5)
    assert len(py) == 6
    problems = reconcile_chain(py, key, check_src=True, check_dst=True)
    assert not problems, "chain.py has drifted from orderflow/EXPECTED.md:\n  " + "\n  ".join(problems)


def test_pantry_chain_matches_its_answer_key():
    key = _expected_chain_rows(_table_after(PANTRY.read_text(encoding="utf-8"), "## The chain"))
    assert len(key) == 5, f"parsed {len(key)} chain rows from the key, expected 5"
    py = _py_chain_rows(chain_pantry.CHAIN, 1, 2, 3, 4, 5)
    assert len(py) == 5
    problems = reconcile_chain(py, key, check_src=True, check_dst=True)
    assert not problems, "chain_pantry.py has drifted from pantry/EXPECTED.md:\n  " + "\n  ".join(problems)


def test_further_chains_match_their_answer_key():
    text = ORDERFLOW.read_text(encoding="utf-8")
    key = (_expected_chain_rows(_table_after(text, "### Chain A"))
           + _expected_chain_rows(_table_after(text, "### Chain B"))
           + _expected_chain_rows(_table_after(text, "### Chain C")))
    assert len(key) == 7, f"parsed {len(key)} further-chain rows from the key, expected 7"
    py = _py_chain_rows(chain_further.ALL_HOPS, 2, 3, 4, 5, 6)
    assert len(py) == 7
    # The further-chain key writes module and test node ids with an ellipsis
    # (`mod:...:orders-shared`), so nodes cannot be compared; edge type and cited line can.
    problems = reconcile_chain(py, key, check_src=False, check_dst=False)
    assert not problems, "chain_further.py has drifted from orderflow/EXPECTED.md:\n  " + "\n  ".join(problems)


def test_dependent_closure_matches_its_answer_key():
    rows = _table_after(ORDERFLOW.read_text(encoding="utf-8"), "## Item 39")
    assert len(rows) == 3, f"parsed {len(rows)} dependent rows from the key, expected 3"
    key = [{"dep": _strip(c[0]), "citations": _citations(c[1]), "source": c[1],
            "build": _strip(c[2])} for c in rows]  # [Dependent, Declared at, Build system]
    py = [{"dep": dep, "suffix": mani, "line": line, "build": build}
          for (dep, mani, line, build) in cross_repo.DEPENDENTS]
    assert len(py) == 3
    problems = []
    for i, (p, k) in enumerate(zip(py, key), start=1):
        if p["dep"] != k["dep"]:
            problems.append(f"dependent {i}: {p['dep']!r} but the key says {k['dep']!r}")
        if p["build"] != k["build"]:
            problems.append(f"dependent {i}: build {p['build']!r} but the key says {k['build']!r}")
        if not _line_cited(k["citations"], k["source"], p["suffix"], p["line"]):
            problems.append(
                f"dependent {i}: {p['suffix']}:{p['line']} is not cited by the key "
                f"(the key's 'Declared at' is {k['source']!r})")
    assert not problems, "cross_repo.py has drifted from orderflow/EXPECTED.md:\n  " + "\n  ".join(problems)


# ── the guard on the guard (item 115) ────────────────────────────────────────
def test_the_reconciliation_detects_a_drift():
    """The four tests above are green because the transcriptions are correct, which on its own
    cannot tell agreement from a comparison that checks nothing. This drives `reconcile_chain`
    on a synthetic key and watches it stay silent on a match and speak up on each drift class
    T245 is about — a moved line, a changed edge type, a changed node, and a drift in the KEY
    itself — so the green above means the two agree rather than that nothing was compared."""
    key_md = (
        "## The chain\n\n"
        "| # | Edge | From " + ARROW + " To | Source | Mechanism |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `DG_CALLS` | `ui:x` " + ARROW + " `ep:/a` | `dir/u.js:4` | m |\n"
        "| 2 | `DG_EXPOSES` | `svc:y` " + ARROW + " `ep:/a` | `C.java:9` + `:12` | m |\n"
    )
    key = _expected_chain_rows(_table_after(key_md, "## The chain"))
    assert len(key) == 2
    # the `:12` bare span must have inherited C.java, and the range/first-line nuances hold
    assert key[1]["citations"] == [("C.java", {9}), ("C.java", {12})]

    good = [
        {"edge": "DG_CALLS", "src": "ui:x@x", "dst": "ep:/a", "suffix": "u.js", "line": 4},
        {"edge": "DG_EXPOSES", "src": "svc:y@y", "dst": "ep:/a", "suffix": "C.java", "line": 12},
    ]
    assert reconcile_chain(good, key, check_src=True, check_dst=True) == []

    drifts = {
        "moved line": [dict(good[0], line=77), good[1]],
        "changed edge": [good[0], dict(good[1], edge="DG_NOT_A_REAL_TYPE")],
        "changed node": [dict(good[0], dst="ep:/WRONG"), good[1]],
    }
    for label, drifted in drifts.items():
        assert reconcile_chain(drifted, key, check_src=True, check_dst=True), \
            f"a {label} drift went unreported: the comparison is vacuous"

    # the direction T245 names: drifting the KEY under a correct transcription.
    key_moved = _expected_chain_rows(
        _table_after(key_md.replace("`dir/u.js:4`", "`dir/u.js:77`"), "## The chain"))
    assert reconcile_chain(good, key_moved, check_src=True, check_dst=True), \
        "a line moved in the answer key went unreported"
