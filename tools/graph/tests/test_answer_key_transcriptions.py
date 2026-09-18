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

import pytest

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


def _expected_chain_by_hop(text: str, heading: str) -> dict[int, dict]:
    """`_expected_chain_rows` keyed by the table's own leading `#` column, so a hop can be
    looked up by its number. `CHAIN_PROVENANCE` is a sparse map (four of five hops), not a
    positional list, so it must reconcile against the key row that shares its hop number
    rather than by position."""
    by_hop: dict[int, dict] = {}
    for cells in _table_after(text, heading):
        src, dst = _src_dst(cells[2])
        by_hop[int(_strip(cells[0]))] = {
            "edge": _strip(cells[1]), "src": src, "dst": dst,
            "citations": _citations(cells[3]), "source": cells[3]}
    return by_hop


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


#: `CHAIN_PROVENANCE` fields that carry a resolved VALUE, not a `file:line` coordinate, so the
#: weld does not mistake them for citations. `prefix` is the router prefix the extractor
#: assembles (the key states it as a mechanism, not a line); `host_file` is one half of a
#: coordinate and is consumed with its sibling `host_line`.
_PROVENANCE_VALUE_FIELDS = {"prefix", "host_file"}


def _provenance_coords(prov: dict, own_suffix: str) -> list[tuple[str, int]]:
    """The `(file_suffix, line)` coordinates one `CHAIN_PROVENANCE` hop entry cites.

    A `resolved_from` carries its own `file:line`; a `host_line` pairs with the sibling
    `host_file`; every other `*_line` is a second line in the hop's OWN source file — the file
    the already-welded `CHAIN` row names. An unclassified field RAISES rather than being
    dropped: zero silent drops means a provenance field added later cannot slip past this weld
    unseen (it forces the classification to be extended)."""
    coords: list[tuple[str, int]] = []
    for name, value in prov.items():
        if name == "resolved_from":
            path, _, line = str(value).rpartition(":")
            coords.append((path, int(line)))
        elif name == "host_line":
            coords.append((str(prov["host_file"]), int(value)))  # type: ignore[arg-type]
        elif name.endswith("_line"):
            coords.append((own_suffix, int(value)))  # type: ignore[arg-type]
        elif name in _PROVENANCE_VALUE_FIELDS:
            continue
        else:
            raise AssertionError(
                f"CHAIN_PROVENANCE field {name!r} is neither a known coordinate nor a "
                f"declared value field — classify it so the weld cannot silently skip it")
    return coords


def reconcile_provenance(prov_by_hop: dict[int, dict], own_by_hop: dict[int, str],
                         key_by_hop: dict[int, dict]) -> tuple[list[str], list[tuple]]:
    """Empty problem list when every `CHAIN_PROVENANCE` coordinate is cited by the answer
    key's Source cell for the SAME hop; otherwise one string per disagreement. Also returns
    the coordinates actually checked, so a caller can assert the comparison was not vacuous.
    Factored out so the self-test can drive it on a synthetic key."""
    problems: list[str] = []
    checked: list[tuple[int, str, int]] = []
    for hop in sorted(prov_by_hop):
        key = key_by_hop.get(hop)
        if key is None:
            problems.append(
                f"hop {hop}: CHAIN_PROVENANCE names a hop the key's chain table has no row for")
            continue
        coords = _provenance_coords(prov_by_hop[hop], own_by_hop[hop])
        if not coords:
            problems.append(f"hop {hop}: provenance entry resolved to no coordinate")
        for suffix, line in coords:
            checked.append((hop, suffix, line))
            if not _line_cited(key["citations"], key["source"], suffix, line):
                problems.append(
                    f"hop {hop}: provenance cites {suffix}:{line}, which the key's Source "
                    f"cell does not ({key['source']!r})")
    return problems, checked


def reconcile_key_coverage(key_by_hop: dict[int, dict], own_line_by_hop: dict[int, tuple[str, int]],
                           prov_by_hop: dict[int, dict]) -> tuple[list[str], list[int]]:
    """The REVERSE of `reconcile_provenance` (review-W388 BLOCK on T364 round 1): empty
    problem list when every coordinate the answer key's Source cell cites, for every hop the
    KEY names, is carried by CHAIN's own (suffix, line) or a `CHAIN_PROVENANCE` entry for that
    SAME hop; otherwise one string per uncovered citation.

    Loops over `sorted(key_by_hop)` — the answer key's own hops — and NEVER over
    `prov_by_hop`. That is the fix for the one-directional gap: `reconcile_provenance` above
    can only report a coordinate IT SEES drifting from the key, so a whole
    `CHAIN_PROVENANCE` hop entry going missing removes the coordinates it would have checked
    along with the check itself (its own non-vacuity guard, `{h for h,_,_ in checked} ==
    set(prov)`, is derived from `prov` itself and shrinks the same way). Driving the
    comparison from the KEY's hop set instead means a hop stays in scope even after
    `CHAIN_PROVENANCE` loses it, so the loss shows up as an uncovered citation instead of a
    silently smaller parametrize.

    Also returns the hops actually visited, so a caller can assert
    `set(visited) == set(key_by_hop)` — the KEY's own hop set, never `set(prov_by_hop)` (the
    same circularity `reconcile_provenance`'s guard has, turned the other way round)."""
    problems: list[str] = []
    visited: list[int] = []
    for hop in sorted(key_by_hop):
        visited.append(hop)
        key = key_by_hop[hop]
        carried: list[tuple[str, int]] = []
        if hop in own_line_by_hop:
            carried.append(own_line_by_hop[hop])
        if hop in prov_by_hop:
            carried += _provenance_coords(prov_by_hop[hop], own_line_by_hop[hop][0])
        for path, lines in key["citations"]:
            for line in sorted(lines):
                if not any(_path_match(path, suffix) and line == n for suffix, n in carried):
                    problems.append(
                        f"hop {hop}: the key cites {path}:{line}, which no transcription "
                        f"row carries")
    return problems, visited


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


def test_pantry_provenance_coordinates_match_the_answer_key():
    """T364: the OTHER coordinates `chain_pantry.CHAIN_PROVENANCE` names — the ones that ride
    in edge props because a hop's edge carries a single `line` — are a transcription of the
    key too, and until now ONLY the built graph held them. `test_answer_key_transcriptions`
    welded `CHAIN`'s main line to the key (T245) but never these; the built-graph golden test
    ties them to the extractor, not to the hand-written key. Measured: correcting the key's
    hop-1 `client.py:11` to `:12` left every current test green, so an answer-key correction
    or a transcription drift at a provenance coordinate passed unseen. This welds each
    provenance coordinate to `pantry/EXPECTED.md`'s chain-table Source cell — the same
    reconciliation, one level more granular."""
    text = PANTRY.read_text(encoding="utf-8")
    key_by_hop = _expected_chain_by_hop(text, "## The chain")
    assert len(key_by_hop) == len(chain_pantry.CHAIN), (
        f"parsed {len(key_by_hop)} chain rows from the key, expected "
        f"{len(chain_pantry.CHAIN)} (chain_pantry.CHAIN's own length, not a typed literal)")
    own_by_hop = {row[0]: row[4] for row in chain_pantry.CHAIN}  # hop -> its own source suffix
    prov = chain_pantry.CHAIN_PROVENANCE
    problems, checked = reconcile_provenance(prov, own_by_hop, key_by_hop)
    assert not problems, (
        "chain_pantry.CHAIN_PROVENANCE has drifted from pantry/EXPECTED.md:\n  "
        + "\n  ".join(problems))
    # Not vacuous: real coordinates were compared, and EVERY provenance hop was covered — a
    # guard over an empty set would pass while checking nothing.
    assert checked, "no provenance coordinate was checked — the weld is vacuous"
    assert {h for h, _, _ in checked} == set(prov), \
        "a CHAIN_PROVENANCE hop was not reconciled against the key"


def test_pantry_provenance_coordinates_are_all_cited_by_the_answer_key():
    """T364 round 2 (review-W388 BLOCK on round 1's commit 915e48d): the weld above is
    one-directional. It walks `CHAIN_PROVENANCE` and checks each entry against the key, so a
    whole hop ENTRY disappearing from `CHAIN_PROVENANCE` removes the coordinates it would have
    compared along with the comparison — nothing is left to report a drift, and
    `test_golden_chain_pantry.py`'s `@pytest.mark.parametrize("hop", sorted(CHAIN_PROVENANCE),
    ...)` (line ~121) loses a parametrized CASE rather than failing one (measured: 61 -> 60
    passed, exit 0, across all four chain_pantry consumers). The same gap swallows a citation
    ADDED to the key with nothing to carry it (measured: 61 -> 61 passed, exit 0 — an entirely
    silent survival, no case count even changes).

    This is the reverse question, walking the ANSWER KEY's own hops instead of
    `CHAIN_PROVENANCE`'s: every coordinate the key's Source cell cites, for every hop the key
    names, must be carried by `chain_pantry.CHAIN`'s own line or a `CHAIN_PROVENANCE` entry —
    a loop whose domain is the key, so it cannot shrink when `CHAIN_PROVENANCE` does."""
    text = PANTRY.read_text(encoding="utf-8")
    key_by_hop = _expected_chain_by_hop(text, "## The chain")
    assert len(key_by_hop) == len(chain_pantry.CHAIN), (
        f"parsed {len(key_by_hop)} chain rows from the key, expected "
        f"{len(chain_pantry.CHAIN)} (chain_pantry.CHAIN's own length, not a typed literal)")
    own_line_by_hop = {row[0]: (row[4], row[5]) for row in chain_pantry.CHAIN}
    problems, visited = reconcile_key_coverage(
        key_by_hop, own_line_by_hop, chain_pantry.CHAIN_PROVENANCE)
    assert not problems, (
        "pantry/EXPECTED.md cites a coordinate chain_pantry.py does not carry:\n  "
        + "\n  ".join(problems))
    # Not vacuous, and the domain is the KEY's hops, never CHAIN_PROVENANCE's (rule 28: the
    # comparison must not be derived from the very thing a hop can go missing from).
    assert visited, "no answer-key hop was checked — the reverse weld is vacuous"
    assert set(visited) == set(key_by_hop), \
        "the reverse weld did not visit every hop the answer key names"


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


def test_the_provenance_reconciliation_detects_a_drift():
    """T364's guard on the guard (item 115), the T245 shape applied to `reconcile_provenance`
    AND, since review-W388's round-1 BLOCK, to its reverse `reconcile_key_coverage` too — the
    provenance weld is only load-bearing if BOTH directions are exercised here, because both
    are green today only because the transcription is correct. FORWARD (walks
    `CHAIN_PROVENANCE`): silent on a match, loud on each drift class — a moved bare provenance
    line, a `resolved_from` off by one, a moved `host_line`, a provenance hop the key has no
    row for, and (the direction T245 names) a coordinate dropped from the KEY itself. It also
    pins that a value field never becomes a phantom coordinate and that an unclassified field
    raises rather than being skipped. REVERSE (walks the KEY): silent on a match, loud when
    the key cites a coordinate nothing carries (the review's mutation 1 — a citation ADDED to
    the key), and loud when a whole `CHAIN_PROVENANCE` hop entry goes missing (the review's
    mutation 2 — a hop DELETED), with the visited-hop set proven NOT to shrink along with it."""
    key_md = (
        "## The chain\n\n"
        "| # | Edge | From " + ARROW + " To | Source | Mechanism |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `DG_CALLS` | `svc:w` " + ARROW + " `ep:/a` | "
        "`client.py:11` + `client.py:13`, host value from `docker-compose.yml:18` | m |\n"
        "| 3 | `DG_PRODUCES` | `svc:a` " + ARROW + " `topic:t` | "
        "`publisher.py:27` " + ARROW + " `:18` " + ARROW + " `docker-compose.yml:10` | m |\n"
    )
    key = _expected_chain_by_hop(key_md, "## The chain")
    assert set(key) == {1, 3}
    own = {1: "client.py", 3: "publisher.py"}
    good = {
        1: {"key_line": 11, "host_file": "docker-compose.yml", "host_line": 18},
        3: {"call_line": 27, "resolved_from": "docker-compose.yml:10"},
    }

    problems, checked = reconcile_provenance(good, own, key)
    assert problems == [], f"a correct provenance transcription was reported as drifted: {problems}"
    # non-vacuous, and `prefix`/`host_file` did NOT become phantom coordinates: hop 1 resolves
    # to client.py:11 + docker-compose.yml:18, hop 3 to publisher.py:27 + docker-compose.yml:10.
    assert sorted(checked) == [
        (1, "client.py", 11), (1, "docker-compose.yml", 18),
        (3, "docker-compose.yml", 10), (3, "publisher.py", 27)]

    drifts = {
        "moved bare line": {1: dict(good[1], key_line=77), 3: good[3]},
        "off-by-one resolved_from": {1: good[1], 3: dict(good[3], resolved_from="docker-compose.yml:11")},
        "moved host_line": {1: dict(good[1], host_line=99), 3: good[3]},
        "hop absent from the key": {1: good[1], 3: good[3], 9: {"call_line": 1}},
    }
    own_plus = {**own, 9: "x.py"}
    for label, drifted in drifts.items():
        p, _ = reconcile_provenance(drifted, own_plus, key)
        assert p, f"a {label} drift went unreported: the provenance weld is vacuous"

    # the T245 direction: a coordinate removed from the answer KEY under a correct transcription.
    key_dropped = _expected_chain_by_hop(key_md.replace("`client.py:11` + ", ""), "## The chain")
    p, _ = reconcile_provenance(good, own, key_dropped)
    assert p, "a coordinate removed from the answer key went unreported"

    # an unclassified provenance field must RAISE, never be silently skipped (zero silent drops).
    with pytest.raises(AssertionError):
        _provenance_coords({"mystery_field": "x"}, "client.py")

    # ── REVERSE direction (review-W388 BLOCK): walk the KEY, not CHAIN_PROVENANCE ──────────
    # CHAIN's own (suffix, line) per hop — the third citation neither `key_line`/`call_line`
    # nor `host_line`/`resolved_from` accounts for. hop 1: client.py:13 (key cites :11 and
    # :13 separately); hop 3: publisher.py:18 (key's bare `:18` span, inherited from
    # publisher.py — `call_line` 27 and `resolved_from` docker-compose.yml:10 are the other
    # two). Together with `good`, this covers every citation `key_md` makes for hops 1 and 3.
    own_line = {1: ("client.py", 13), 3: ("publisher.py", 18)}

    r_problems, r_visited = reconcile_key_coverage(key, own_line, good)
    assert r_problems == [], \
        f"a correct transcription was reported as missing a key citation: {r_problems}"
    assert set(r_visited) == set(key), "the reverse weld did not visit every hop the key names"

    # the review's mutation 1: a citation ADDED to the key that nothing carries.
    key_with_extra = _expected_chain_by_hop(
        key_md.replace("host value from `docker-compose.yml:18`",
                        "host value from `docker-compose.yml:18` and `nowhere.py:5`"),
        "## The chain")
    p, _ = reconcile_key_coverage(key_with_extra, own_line, good)
    assert p, "a citation added to the answer key went unreported"
    assert any("nowhere.py:5" in problem for problem in p), \
        f"the uncited coordinate was not named in the report: {p}"

    # the review's mutation 2: a whole CHAIN_PROVENANCE hop entry DELETED (not merely
    # drifted). The forward weld's own non-vacuity guard is derived from `prov` itself
    # (`set(prov)`), so it cannot see a key of `prov` disappearing — the reverse weld's domain
    # is `key`, which does not shrink, and that is the property this drift pins.
    good_missing_hop1 = {3: good[3]}
    p, r_visited = reconcile_key_coverage(key, own_line, good_missing_hop1)
    assert p, "a CHAIN_PROVENANCE hop entry going missing went unreported"
    assert any(problem.startswith("hop 1:") for problem in p), \
        f"the missing hop was not named in the report: {p}"
    assert 1 in r_visited, \
        "the reverse weld's own domain shrank along with CHAIN_PROVENANCE — it is vacuous " \
        "in exactly the way the forward weld was"
