"""One declared-child rule, read by the build and by the canonical pass (T616).

Which component reads a file (T591) and whether a deployment node's declared `package` names a
file of a component declared inside the declaring one (review B15a-11) are one question: does a
component declared inside this one hold the path? It had two implementations -
`build._declared_children` with `_child_holding`, and `links/canonical._in_a_declared_child` -
that agreed on every shape measured, so an edit to one would have gone unnoticed by the other's
tests (review B15a-14). Both now read `_canonical.declared_children` and
`_canonical.child_holding`, and these tests hold them to it.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
from tools.graph.extractors import _canonical  # noqa: E402
from tools.graph.links import canonical as C  # noqa: E402
from tools.graph.tests._estate import lay_down  # noqa: E402

COMPOSE = "services:\n  web:\n    image: x\n"


def _readers(root: Path, components: list[dict], rel: str) -> list[str]:
    """The components whose compose reading `activate` hands the workspace file `rel`."""
    regs = [r for r in B.discover_extractors() if r.name == "compose-services"]
    act = B.activate(regs, components, root, workspace=components)
    return sorted(c["name"] for c in components
                  if any(p == root / rel for p in act.files.get(("compose-services", c["name"]),
                                                                  [])))


def test_the_build_and_the_canonical_pass_read_one_declared_child_rule(tmp_path, monkeypatch):
    """The rule as it is first - the child holds its own file, by both readings - then another
    rule stood in for the shared one, under which no component holds another's files: both
    readings move with it, which two implementations would not."""
    lay_down(tmp_path, {"platform": {"build.gradle": "\n"},
                        "platform/svc-a": {"compose.yaml": COMPOSE}})
    root = tmp_path / "ws"
    components = [{"name": "platform", "path": "platform"},
                  {"name": "svc-a", "path": "platform/svc-a"}]
    layout = {c["name"]: c["path"] for c in components}
    path = "platform/svc-a/compose.yaml"
    assert _readers(root, components, path) == ["svc-a"]
    assert C._in_a_declared_child(path, "platform", layout) is True

    monkeypatch.setattr(_canonical, "child_holding", lambda rel, children: None)
    assert _readers(root, components, path) == ["platform", "svc-a"]
    assert C._in_a_declared_child(path, "platform", layout) is False


#: (the layout, the component asked about, a workspace path, the child holding it or None).
SHAPES = [
    ([("platform", "platform"), ("svc-a", "platform/svc-a")],
     "platform", "platform/svc-a/compose.yaml", "svc-a"),
    ([("platform", "platform"), ("svc-a", "platform/svc-a"), ("deep", "platform/svc-a/deep")],
     "platform", "platform/svc-a/deep/compose.yaml", "deep"),
    ([("root", "."), ("svc-a", "svc-a"), ("svc-a-legacy", "svc-a-legacy")],
     "root", "svc-a-legacy/compose.yaml", "svc-a-legacy"),
    ([("root", "."), ("svc-a", "svc-a")], "root", "svc-a-legacy/compose.yaml", None),
    ([("root", "."), ("svc-a", "svc-a")], "root", "compose.yaml", None),
    ([("a", "shared"), ("b", "shared")], "a", "shared/compose.yaml", None),
    ([("a", "."), ("b", ".")], "a", ".", None),
    ([("platform", "platform"), ("svc-a", "platform/svc-a")], "svc-a",
     "platform/svc-a/compose.yaml", None),
]


@pytest.mark.parametrize("layout,component,path,holder", SHAPES,
                         ids=["nested", "most-specific", "sibling-prefix-held",
                              "sibling-prefix-trap", "root-own-file", "two-over-one-directory",
                              "two-at-the-root", "the-child-itself"])
def test_one_rule_answers_each_shape_for_both_readings(layout, component, path, holder):
    """By whole path segments (`svc-a` never holds `svc-a-legacy/x`, T530's trap), the most
    specific child first, the root holding every other, and two components over one directory
    holding neither (T615's shape)."""
    layout = dict(layout)
    mine = layout[component]
    rel = path if mine == "." else path[len(mine) + 1:]
    assert _canonical.child_holding(rel, _canonical.declared_children(component, layout)) \
        == holder
    assert C._in_a_declared_child(path, component, layout) is (holder is not None)


def test_a_path_outside_the_component_lies_in_none_of_its_children():
    """`svc-a/compose.yaml` is not under `platform` at all, though `platform`'s child is
    `svc-a` relative to it: a workspace path is asked of the component's children only once
    it lies under the component."""
    layout = {"platform": "platform", "svc-a": "platform/svc-a"}
    assert C._in_a_declared_child("svc-a/compose.yaml", "platform", layout) is False
