"""Several `[[canonical]]` blocks may name one service (T623, the owner's decision 41).

Two nodes can share a service name - `svc:web@a` and `svc:web@b`, each contested by two
packages of its own component - and every block needs an `owner`, whose block designates
only that component's files, so one block could never decide both: the draft held two
blocks, and pasted, the installer refused them (measured, plans/b21-evidence/probe_t623.out).
Now each block decides the nodes whose packages it designates; the installer refuses only an
exact repeat; a node two blocks designate is decided by the first, the other named in a row.
`mark-canonical`, the name join and compose's env ranking all take a node's block by the one
rule (`block_for`).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build, compose, lay_down, producer, spawn, svc  # noqa: E402

WEB = "services:\n  web:\n    build: .\n    environment:\n      TOPIC: {v}\n"


def two_nodes(*blocks: dict) -> dict:
    """`a` and `b` each build their own `web` from two compose packages; each holds code
    publishing to the topic its `TOPIC` names, so a test sees which value each resolved."""
    return {"a": {"compose.yaml": WEB.format(v="a1"), "compose.prod.yaml": WEB.format(v="a2"),
                  "app.py": producer("TOPIC")},
            "b": {"compose.yaml": WEB.format(v="b1"), "compose.prod.yaml": WEB.format(v="b2"),
                  "app.py": producer("TOPIC")},
            "__canonical__": list(blocks)}


def canonical_files(built) -> dict[str, str]:
    """Each node's canonical claim's file."""
    return {e["dst"]: e["src"].split(":", 1)[1] for e in built.deploys
            if e.get("canonical") is True and e["dst"].startswith("svc:web@")}


A_BLOCK = {"service": "web", "owner": "a", "path": "compose.yaml"}
B_BLOCK = {"service": "web", "owner": "b", "path": "compose.prod.yaml"}


def test_one_block_per_node_decides_each_node(tmp_path):
    """The draft's two blocks, filled and pasted: accepted, and each decides its own node."""
    built = build(tmp_path, two_nodes(A_BLOCK, B_BLOCK))
    assert built.code == 0
    assert not built.kinds("contested-service", "canonical-duplicate", "canonical-overlap")
    assert canonical_files(built) == {"svc:web@a": "a/compose.yaml",
                                      "svc:web@b": "b/compose.prod.yaml"}


def test_the_env_ranking_takes_each_nodes_own_block(tmp_path):
    """Compose ranks a component's values by the block deciding that component's node - `a`'s
    from `a`'s chosen file, `b`'s from `b`'s - never by whichever block came first."""
    built = build(tmp_path, two_nodes(A_BLOCK, B_BLOCK))
    assert {"topic:a1", "topic:b2"} <= set(built.topics)
    assert not {"topic:a2", "topic:b1"} & set(built.topics)


def test_an_exact_repeat_is_refused(tmp_path):
    """Two blocks naming one service, one owner and one path say nothing apart: refused,
    named, before anything is built."""
    lay_down(tmp_path, two_nodes(A_BLOCK, A_BLOCK))
    run = spawn(tmp_path)
    said = run.stdout + run.stderr
    assert run.returncode == 2, said
    assert "two [[canonical]] blocks name service 'web' with owner 'a' and path " \
           "'compose.yaml'" in said
    assert "Traceback" not in said


def test_a_node_two_blocks_designate_is_decided_by_the_first_and_the_other_named(tmp_path):
    """Both blocks designate a package of `svc:web@a`: the first decides, as every reader of
    the blocks takes the first, and a row names the other, at its line."""
    second = {"service": "web", "owner": "a", "path": "compose.prod.yaml"}
    built = build(tmp_path, two_nodes(A_BLOCK, second, B_BLOCK))
    assert canonical_files(built)["svc:web@a"] == "a/compose.yaml"
    (row,) = built.kinds("canonical-overlap")
    assert (row["service"], row["node"]) == ("web", "svc:web@a")
    assert row["source_file"] == "workspace.toml" and row["line"] > 0
    assert "decides it" in row["why"] and "compose.prod.yaml" in row["why"]


def test_a_respelt_name_with_another_owner_is_not_a_duplicate(tmp_path):
    """A respelt name is a duplicate only where the owner and path are one too: `my api` for
    `a` and `my-api` for `b` - one service once normalised (D534-24) - decide two nodes, and
    neither is ignored."""
    api = "services:\n  my-api:\n    build: .\n"
    built = build(tmp_path, {
        "a": {"compose.yaml": api, "compose.prod.yaml": api, "app.py": "x = 1\n"},
        "b": {"compose.yaml": api, "compose.prod.yaml": api, "app.py": "x = 1\n"},
        "__canonical__": [{"service": "my api", "owner": "a", "path": "compose.yaml"},
                          {"service": "my-api", "owner": "b", "path": "compose.prod.yaml"}]})
    assert built.code == 0
    assert not built.kinds("canonical-duplicate", "contested-service")
    assert {e["dst"]: e["src"].split(":", 1)[1] for e in built.deploys
            if e.get("canonical") is True} == {"svc:my-api@a": "a/compose.yaml",
                                               "svc:my-api@b": "b/compose.prod.yaml"}


def test_each_block_is_judged_on_its_own(tmp_path):
    """`canonical-unmatched` judges every block, not the first per service: `b`'s path names
    no file of `b`, while `a`'s block decides its node."""
    built = build(tmp_path, two_nodes(A_BLOCK, {**B_BLOCK, "path": "nope.yaml"}))
    rows = built.kinds("canonical-unmatched")
    assert [(r["component"], r["path"]) for r in rows] == [("b", "nope.yaml")]


def test_a_node_no_block_designates_is_told_a_new_block_decides_it(tmp_path):
    """A block for `a` designates none of `b`'s packages: the row for `svc:web@b` says so and
    offers a block of its own - never "change" `a`'s, which would undo `a`'s decision."""
    built = build(tmp_path, two_nodes(A_BLOCK))
    (row,) = built.kinds("contested-service")
    assert row["service"] == "svc:web@b"
    assert "no [[canonical]] block for this service designates one of these packages" \
        in row["why"]
    assert "Add one naming `service` and one of:" in row["why"]
    assert "change it" not in row["why"]


def test_the_name_join_takes_the_block_designating_its_packages(tmp_path):
    """A joined node stands as a `name-joined-service` row until a block decides it, judged
    as `mark-canonical` judges it: the block for `deploy` decides `svc:web@web` though a
    block for `a`'s `web` comes first."""
    def joined(*blocks: dict, where: str) -> list[dict]:
        estate = two_nodes(*blocks)
        estate["web"] = {"app.py": producer("TOPIC")}
        estate["deploy"] = {"compose.yaml": compose(svc("web", None, None, image="web:1"))}
        built = build(tmp_path / where, estate)
        assert built.code == 0
        return [r for r in built.kinds("name-joined-service") if r["service"] == "web"]

    assert joined(A_BLOCK, where="undecided"), "the joined node stands without its block"
    assert not joined(A_BLOCK, {"service": "web", "owner": "deploy"}, where="decided")


def test_a_block_whose_path_designates_none_of_a_nodes_packages_does_not_decide_it(tmp_path):
    """Its owner reads `svc:web@a`'s packages, but its path names neither: it does not reach
    the node, so the next block that does decides it - no contest, no overlap - and the stray
    block is judged on its own."""
    stray = {"service": "web", "owner": "a", "path": "nope.yaml"}
    built = build(tmp_path, two_nodes(stray, A_BLOCK, B_BLOCK))
    assert canonical_files(built)["svc:web@a"] == "a/compose.yaml"
    assert not built.kinds("contested-service", "canonical-overlap")
    assert [(r["component"], r["path"]) for r in built.kinds("canonical-unmatched")] == \
        [("a", "nope.yaml")]
