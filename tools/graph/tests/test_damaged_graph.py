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

import json
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


def _graph(tmp_path: Path, tail: str = "", *, raw: bytes | None = None) -> Path:
    out = tmp_path / "graph-out"
    out.mkdir(exist_ok=True)
    path = out / "graph.jsonl"
    if raw is not None:
        path.write_bytes(raw)
    else:
        path.write_text(GOOD + (tail + "\n" if tail else ""), encoding="utf-8")
    return out


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
    these numbers."""
    out = _graph(tmp_path, DAMAGE["a line that does not parse"])
    (out / "unresolved.jsonl").write_text("", encoding="utf-8")
    (out / "build_report.json").write_text("{}", encoding="utf-8")
    code = VA.main(["--graph-dir", str(out)])
    assert code == 2
    assert "truncated denominator" in capsys.readouterr().out


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

    `mismatched` is the right answer because it is already what this function
    means by *I cannot vouch for this shard*, and the caller already reports
    it by component name."""
    _, comp = _shard(tmp_path, manifest)
    _, present, missing, mismatched = SN.load(tmp_path, [comp])
    assert mismatched == ["svc"] and present == [] and missing == []


def test_an_unreadable_manifest_reads_as_no_stamps_so_the_shard_is_stale(
        tmp_path):
    """The freshness half of the same read. None already covers "no shard" and
    "published before stamps existed"; unreadable belongs with them, because
    all three mean the same thing to a rebuild — do not reuse this."""
    _, comp = _shard(tmp_path, "{ half written")
    assert SN.stamps_of(tmp_path, comp) is None


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
