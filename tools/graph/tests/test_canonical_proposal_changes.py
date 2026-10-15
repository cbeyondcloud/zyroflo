"""Where a block already designates a contested node's packages, the draft changes it (T619).

`--propose-canonical` drafted a new `[[canonical]]` block for a node whose block decides none
of its packages - while the contested row itself said "change the block at
`workspace.toml:10`". Pasted, the second block was refused on `main`; under decision 41 it
would be accepted and overlap, the first block deciding nothing (measured,
plans/b21-evidence/probe_t619.out). The draft now carries that change as comments naming the
block, and drafts a new block only for a node no block designates - which pastes clean.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build  # noqa: E402

WEB = "services:\n  web:\n    build: .\n"
#: An owner reading both of `a`'s packages selects neither: it designates them, deciding none.
A_OWNER = {"service": "web", "owner": "a"}


def estate(*blocks: dict) -> dict:
    """`a` and `b` each build their own `web` from two compose packages."""
    return {"a": {"compose.yaml": WEB, "compose.prod.yaml": WEB, "app.py": "x = 1\n"},
            "b": {"compose.yaml": WEB, "compose.prod.yaml": WEB, "app.py": "x = 1\n"},
            "__canonical__": list(blocks)}


def test_a_node_its_block_designates_is_drafted_as_a_change_to_that_block(tmp_path, capsys):
    built = build(tmp_path, estate(A_OWNER), "--propose-canonical")
    printed = capsys.readouterr().out
    rows = {r["service"]: r for r in built.kinds("contested-service")}
    assert rows["svc:web@a"]["block"].startswith("workspace.toml:")
    assert "block" not in rows["svc:web@b"]
    text = (tmp_path / "out" / "canonical.proposed.toml").read_text(encoding="utf-8")
    blocks = tomllib.loads(text).get("canonical", [])
    assert [b["owner"] for b in blocks] == ["b"], "a second block for `a` would overlap it"
    assert f"# ── change the block at {rows['svc:web@a']['block']} (service = \"web\")" in text
    assert '#   owner = "a", path = "compose.yaml"' in text
    assert "2 of 2 contested service(s) drafted" in printed
    assert "1 of them as a change to an existing block" in printed


def test_following_the_change_decides_the_node_and_the_new_block_pastes_clean(tmp_path):
    """The change made to `a`'s block, and the new block for `b` filled and pasted: both
    nodes decided, nothing refused, nothing overlapping."""
    build(tmp_path / "1", estate(A_OWNER), "--propose-canonical")
    text = (tmp_path / "1" / "out" / "canonical.proposed.toml").read_text(encoding="utf-8")
    (drafted,) = tomllib.loads(text)["canonical"]
    pasted = {**drafted, "path": "compose.yaml"}
    done = build(tmp_path / "2", estate({**A_OWNER, "path": "compose.yaml"}, pasted))
    assert done.code == 0
    assert not done.kinds("contested-service", "canonical-overlap", "canonical-duplicate")


def test_a_draft_holding_only_changes_asks_for_no_blank_block(tmp_path, capsys):
    """Review B21-11: where every contested node already has a block, the draft is changes
    alone - comments - and neither the build's line nor the header asks anyone to fill a
    blank block it does not hold."""
    build(tmp_path, estate(A_OWNER, {"service": "web", "owner": "b"}), "--propose-canonical")
    printed = capsys.readouterr().out
    text = (tmp_path / "out" / "canonical.proposed.toml").read_text(encoding="utf-8")
    assert tomllib.loads(text).get("canonical", []) == []
    assert "2 of 2 contested service(s) drafted" in printed
    assert "2 of them as a change to an existing block" in printed
    assert "blank" not in printed.split("propose-canonical:")[1].lower()
    assert "BLANK" not in text and "blank" not in text
    assert text.count("# ── change the block at ") == 2
