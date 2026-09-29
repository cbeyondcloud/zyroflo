"""Contexts that reach a component's directory only through the filesystem (T534 V2).

Placement is path arithmetic (`built_by`); main placed by `resolve()`. The two differ for the
spellings the filesystem canonicalises or follows - letter case, a trailing dot, an 8.3 short
name, a junction or symlink, an absolute path, and a context that leaves the root and comes
back in. In each, the node id and the env are main's; the declaring edge loses `component`
and `mapped_by`, and the row the design's matrix names says why, with a way on that is
accepted when followed. Where some component holds the respelt path as written (a nested
layout), it is placed on that holder instead, and there a child's own file moves its node.

Platform notes: case folding and a trailing dot resolve only on Windows, so main's env
reaches the component there only; an 8.3 name exists only on Windows; a junction stands in
for a symlink on Windows, which needs no privilege.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib import repo_walk  # noqa: E402
from tools.graph.tests._estate import (  # noqa: E402
    build, compose, lay_down, producer, spawn, svc, workspace_toml)

WINDOWS = os.name == "nt"
CELLS = ("letter case", "trailing dot", "8.3 short name", "junction", "absolute",
         "leaves the root and comes back")
TWIN = "differs only in letter case"
CATCH_ALL = "which no declared component holds as written"
ROOT_ONLY = "no declared component but the workspace root's, as written"
ABSOLUTE = "an absolute path"
OUT_REPO = "lies outside this repository as written"
OUT_WORKSPACE = "lies outside this workspace as written"

# V2's matrix, cell by layout: the row the declaring component's reading gets.
MATRIX = {
    "flat": (TWIN, CATCH_ALL, CATCH_ALL, CATCH_ALL, ABSOLUTE, OUT_WORKSPACE),
    "single": (TWIN, OUT_REPO, OUT_REPO, OUT_REPO, ABSOLUTE, OUT_REPO),
    "root": (OUT_REPO, OUT_REPO, OUT_REPO, OUT_REPO, ABSOLUTE, OUT_REPO),
    "root beside others": (TWIN, ROOT_ONLY, ROOT_ONLY, ROOT_ONLY, ABSOLUTE, OUT_WORKSPACE),
}
# Does main's `resolve()` reach the declaring component's own directory?
RESOLVES = dict(zip(CELLS, (WINDOWS, WINDOWS, True, True, True, True)))


def link_dir(link: Path, target: Path) -> None:
    """A directory junction on Windows (no privilege needed), a symlink elsewhere."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def short_name(path: Path) -> str | None:
    """The 8.3 name Windows gives a long directory name, or None where there is none."""
    if not WINDOWS:
        return None
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024):
        return None
    name = Path(buf.value).name
    return name if name.lower() != path.name.lower() else None


def respell(cell: str, parent: Path, name: str) -> str:
    """The context naming `parent/name` in `cell`'s spelling, from a file one level below
    `parent` (so `../name` is the plain spelling). A junction goes beside its target, at
    `parent/link`: outside any component in a flat layout, outside the root where the
    component IS the root, and inside the root's tree beside a root component."""
    if cell == "letter case":
        return f"../{name.upper()}"
    if cell == "trailing dot":
        return f"../{name}."
    if cell == "8.3 short name":
        short = short_name(parent / name)
        if short is None:
            pytest.skip("no 8.3 short name here (not Windows, or 8.3 names are off)")
        return f"../{short}"
    if cell == "junction":
        link_dir(parent / "link", parent / name)
        return "../link"
    if cell == "absolute":
        return str(parent / name)
    return f"../../{parent.name}/{name}"          # leaves the root and comes back


def matrix_estate(layout: str, cell: str, where: Path) -> tuple[dict, str, str, str]:
    """(estate, workspace dir name, declaring file's deploy source, main's node id)."""
    long = cell == "8.3 short name"
    if layout in ("flat", "single"):
        comp = "orders-service" if long else "orders"
        ws = where / "ws"
        (ws / comp).mkdir(parents=True)
        context = respell(cell, ws, comp)
        estate = {comp: {"docker-compose.yml": compose(svc("web", context)),
                         "app/pub.py": producer("TOPIC")}}
        if layout == "flat":
            estate["other"] = {"README.md": "x\n"}
        return estate, "ws", f"{comp}/docker-compose.yml", f"svc:web@{comp}"
    if layout == "root":
        ws_name = "workspace-root" if long else "ws"
        (where / ws_name).mkdir(parents=True)
        context = respell(cell, where, ws_name)
        if cell == "leaves the root and comes back":
            context = f"../{ws_name}"
        return ({".": {"docker-compose.yml": compose(svc("web", context)),
                       "app/pub.py": producer("TOPIC")}, "__names__": {".": "mono"}},
                ws_name, "mono/docker-compose.yml", "svc:web@mono")
    comp = "deploy-service" if long else "deploy"
    ws = where / "ws"
    (ws / comp).mkdir(parents=True)
    context = respell(cell, ws, comp)
    return ({".": {}, comp: {"docker-compose.yml": compose(svc("web", context)),
                             "app/pub.py": producer("TOPIC")},
             "orders": {"README.md": "x\n"}, "__names__": {".": "mono"}},
            "ws", f"{comp}/docker-compose.yml", f"svc:web@{comp}")


@pytest.mark.parametrize("cell", CELLS)
@pytest.mark.parametrize("layout", sorted(MATRIX))
def test_each_class_cell_gets_the_matrix_row_and_keeps_mains_id_and_env(
        tmp_path, monkeypatch, layout, cell):
    """D534-77, D534-90, D534-96, D534-104, D534-105, D534-111, D534-113."""
    if layout == "root beside others" and cell == "junction":
        if not WINDOWS:
            pytest.skip("a symlink inside the root's tree is walked differently by POSIX")
        # Every junction here lies inside the root component's tree, where the two
        # traversal backends disagree (T592): pin the pure-Python one (D534-107).
        monkeypatch.setattr(repo_walk, "_tool", lambda name: None)
    estate, ws_name, source, node = matrix_estate(layout, cell, tmp_path)
    built = build(tmp_path, estate, ws_name=ws_name)
    assert built.code == 0
    assert built.placed(source) == (node, "", ""), "main's id; the edge loses its labels"
    why = [r["why"] for r in built.kinds("compose-build-unresolved")
           if r["component"] == source.split("/")[0]]
    assert len(why) == 1 and MATRIX[layout][CELLS.index(cell)] in why[0], why
    assert ("topic:web-value" in built.topics) == RESOLVES[cell], "env is main's"
    if layout == "root beside others":
        # The root component reads the same file too (T591): for the cells that reach the
        # holders lookup it places the context as its own, with no row; the absolute and
        # leave-and-return cells are decided before that lookup, and row there too.
        root_reading = built.deploy("mono/" + source)
        if cell in ("absolute", "leaves the root and comes back"):
            assert root_reading["props"]["mapped_by"] == ""
            assert len(built.kinds("compose-build-unresolved")) == 2
        else:
            assert (root_reading["dst"], root_reading["props"]["mapped_by"],
                    root_reading["provenance"]) == ("svc:web@mono", "build", "EXTRACTED")
            assert root_reading["props"]["built_within"]


# ── the nested exception, and the one cell that moves a node (D534-86, D534-103) ─────────


def nested(where: Path) -> Path:
    ws = where / "ws"
    (ws / "platform" / "svc-a").mkdir(parents=True)
    return ws


def test_a_nested_childs_directory_respelt_is_placed_on_the_parent_with_no_row(tmp_path):
    ws = nested(tmp_path)
    built = build(tmp_path, {
        "platform": {"docker-compose.yml": compose(svc("web", "./svc-a.", None))},
        "platform/svc-a": {},
        "deploy": {"docker-compose.yml": compose(svc("api", "../platform/svc-a.", None))}})
    own = built.deploy("platform/docker-compose.yml")
    assert (own["dst"], own["props"]["mapped_by"], own["provenance"],
            own["props"]["built_within"]) == ("svc:web@platform", "build", "EXTRACTED",
                                              "platform/svc-a.")
    other = built.deploy("deploy/docker-compose.yml")
    assert (other["dst"], other["provenance"]) == ("svc:api@platform", "INFERRED")
    assert not built.kinds("compose-build-unresolved")
    assert ws.exists()


@pytest.mark.parametrize("cell", ["trailing dot", "letter case", "8.3 short name",
                                  "junction"])
def test_a_childs_own_file_respelling_its_directory_moves_the_node_to_the_parent(
        tmp_path, monkeypatch, cell):
    """D534-103, D534-110: main resolved this to the child (`svc:web@svc-a`, by build);
    as written the path lies inside the parent, so the claim is placed `within` it and the
    child's service node is gone. Fails on main."""
    ws = nested(tmp_path)
    if cell == "8.3 short name":
        (ws / "platform" / "svc-a").rmdir()
        (ws / "platform" / "service-a").mkdir()
    child = "service-a" if cell == "8.3 short name" else "svc-a"
    if cell == "junction":
        if not WINDOWS:
            pytest.skip("a symlink inside a component is walked differently by POSIX")
        monkeypatch.setattr(repo_walk, "_tool", lambda name: None)   # T592, D534-107
        link_dir(ws / "platform" / "link", ws / "platform" / child)
        context = "../link"
    else:
        context = respell(cell, ws / "platform", child)
    built = build(tmp_path, {
        "platform": {}, f"platform/{child}": {
            "docker-compose.yml": compose(svc("web", context)),
            "app/pub.py": producer("TOPIC")}})
    edge = built.deploy(f"{child}/docker-compose.yml")
    assert (edge["dst"], edge["props"]["component"], edge["provenance"]) == (
        "svc:web@platform", "platform", "INFERRED")
    assert f"svc:web@{child}" not in built.svc_ids()


@pytest.mark.parametrize("cell", ["absolute", "leaves the root and comes back"])
def test_a_childs_own_spelling_decided_before_the_holders_keeps_mains_id(tmp_path, cell):
    """D534-110: the absolute and leave-and-return checks come before the holders lookup,
    so these get their row and the id stays main's."""
    ws = nested(tmp_path)
    context = (str(ws / "platform" / "svc-a") if cell == "absolute"
               else "../../../ws/platform/svc-a")
    built = build(tmp_path, {"platform": {}, "platform/svc-a": {
        "docker-compose.yml": compose(svc("web", context))}})
    assert built.placed("svc-a/docker-compose.yml") == ("svc:web@svc-a", "", "")
    assert built.kinds("compose-build-unresolved")


# ── every row's way on is accepted when followed (D534-78, D534-84, D534-88, D534-97) ────


@pytest.mark.parametrize("layout", sorted(MATRIX))
def test_rewriting_the_context_as_the_components_directory_maps_it(tmp_path, layout):
    estate, ws_name, source, node = matrix_estate(layout, "trailing dot", tmp_path)
    assert build(tmp_path / "respelt", estate, ws_name=ws_name).kinds(
        "compose-build-unresolved"), "the row fires first"
    comp = next(k for k in estate if not k.startswith("__") and "docker-compose.yml"
                in estate[k])
    estate[comp]["docker-compose.yml"] = compose(svc("web", "."))
    built = build(tmp_path, estate, ws_name=ws_name)
    assert built.placed(source)[1:] == (node.split("@")[1], "build")
    assert not built.kinds("compose-build-unresolved", "contested-service")


def test_declaring_an_existing_directory_maps_it_and_a_missing_one_is_refused(tmp_path):
    """D534-84: the catch-all offers declaring only for a directory that exists, because
    the workspace parser refuses one that does not; followed, the other way on maps."""
    estate = {"orders": {"docker-compose.yml": compose(svc("web", "../extra", None))},
              "other": {"README.md": "x\n"}, "__dirs__": ["extra"]}
    first = build(tmp_path / "a", estate)
    assert CATCH_ALL in first.kinds("compose-build-unresolved")[0]["why"]
    declared = build(tmp_path / "b", {**estate, "extra": {"keep.txt": "x\n"}})
    assert declared.placed("orders/docker-compose.yml") == ("svc:web@extra", "extra", "build")
    assert not declared.kinds("compose-build-unresolved", "contested-service")

    missing = {"orders": {"docker-compose.yml": compose(svc("web", "../nope", None))},
               "other": {"README.md": "x\n"}}
    toml = lay_down(tmp_path / "c", missing)
    toml.write_bytes((workspace_toml(missing) + '\n[[component]]\nname = "nope"\n'
                      'path = "nope"\n').encode("utf-8"))
    refused = spawn(tmp_path / "c")
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "'nope' has path 'nope', which does not exist" in refused.stdout + refused.stderr
    # The row's other way on: the context is wrong. Corrected, it maps.
    missing["orders"]["docker-compose.yml"] = compose(svc("web", "../other", None))
    fixed = build(tmp_path / "d", missing)
    assert fixed.placed("orders/docker-compose.yml")[1:] == ("other", "build")


@pytest.mark.parametrize("root_component", [False, True])
def test_the_outside_rows_way_on_is_a_chain_where_the_directory_is_undeclared(
        tmp_path, root_component):
    """D534-97: rewriting hands over to the catch-all (or, beside a root component, the
    root-only) row, whose declaring way on finishes the job; each step is accepted."""
    base = {"deploy": {"docker-compose.yml": compose(svc("web", "../../ws/extra", None))},
            "code": {"README.md": "x\n"}, "__dirs__": ["extra"]}
    if root_component:
        base.update({".": {}, "__names__": {".": "mono"}})
    step1 = build(tmp_path / "a", base)
    assert any(OUT_WORKSPACE in r["why"] for r in step1.kinds("compose-build-unresolved"))
    base["deploy"] = {"docker-compose.yml": compose(svc("web", "../extra", None))}
    step2 = build(tmp_path / "b", base)
    expected = ROOT_ONLY if root_component else CATCH_ALL
    assert any(expected in r["why"] for r in step2.kinds("compose-build-unresolved"))
    step3 = build(tmp_path / "c", {**base, "extra": {"keep.txt": "x\n"}})
    assert step3.placed("deploy/docker-compose.yml") == ("svc:web@extra", "extra", "build")
    assert not step3.kinds("compose-build-unresolved", "contested-service")


def test_the_root_only_rows_declaring_way_on_contests_a_respelling_so_it_comes_second(
        tmp_path):
    """D534-115, pinned: on a cell that respells a declared component's directory, declaring
    the respelling is accepted and contests, losing the component's node - which is why the
    respelling way on comes first. Following that one maps from both readings."""
    if not WINDOWS:
        pytest.skip("`deploy.` names `deploy` only on Windows")
    estate = {".": {}, "deploy": {"docker-compose.yml": compose(svc("web", "../deploy.",
                                                                     None))},
              "orders": {"README.md": "x\n"}, "__names__": {".": "mono"}}
    toml = lay_down(tmp_path / "a", estate)
    toml.write_bytes((workspace_toml(estate) + '\n[[component]]\nname = "dot"\n'
                      'path = "deploy."\n').encode("utf-8"))
    declared = build(tmp_path / "a")
    assert declared.code == 0
    assert "svc:web@deploy" not in declared.svc_ids()
    assert declared.kinds("contested-service")
    estate["deploy"] = {"docker-compose.yml": compose(svc("web", ".", None))}
    respelt = build(tmp_path / "b", estate)
    assert respelt.placed("deploy/docker-compose.yml") == ("svc:web@deploy", "deploy", "build")
    assert respelt.placed("mono/deploy/docker-compose.yml")[0] == "svc:web@deploy"
    assert not respelt.kinds("compose-build-unresolved", "contested-service")
