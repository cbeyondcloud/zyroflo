"""A gradle module in a declared child resolves its identity from the build above it (T614).

Gradle keeps its reach across a nested layout (batch 15a's ruling 8): the parent's reading,
handed the child's `build.gradle`, gives the subproject the parent's group. The child's own
reading saw no `settings.gradle` and no `group` in its tree and fell back to the component's
alias, so one module was two nodes - `mod:com.acme:svc-a` and `mod:svc-a:svc-a`, measured.
A reader declaring `READS_ANCESTORS` is now also handed the files its globs match directly in
each directory above a component, up to the root of the outermost component holding it -
inside the component's stamp - and gradle reads them for the module's identity alone.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
from tools.graph.tests._estate import build, lay_down  # noqa: E402

SETTINGS = "rootProject.name = 'platform'\ninclude 'svc-a'\n"
ROOT_BUILD = "group = 'com.acme'\nversion = '1.0.0'\n"
CHILD_BUILD = "dependencies {\n    implementation 'org.lib:util:2.0'\n}\n"


def platform(**extra) -> dict:
    return {"platform": {"settings.gradle": SETTINGS, "build.gradle": ROOT_BUILD},
            "platform/svc-a": {"build.gradle": CHILD_BUILD},
            "__names__": {"platform": "platform", "platform/svc-a": "svc-a"}, **extra}


def mods(built) -> list[str]:
    return sorted(n["id"] for n in built.nodes if n.get("type") == "mod"
                  and not n["id"].startswith("mod:org.lib:"))


SHAPES = {
    "a-parent-component": (platform(), ["mod:com.acme:platform", "mod:com.acme:svc-a"]),
    "the-root-component": ({".": {"settings.gradle": SETTINGS, "build.gradle": ROOT_BUILD},
                            "svc-a": {"build.gradle": CHILD_BUILD},
                            "__names__": {".": "mono"}},
                           ["mod:com.acme:platform", "mod:com.acme:svc-a"]),
    "two-levels-up": ({"platform": {"settings.gradle": "rootProject.name = 'platform'\n"
                                                       "include 'services:svc-a'\n",
                                    "build.gradle": ROOT_BUILD},
                       "platform/services/svc-a": {"build.gradle": CHILD_BUILD},
                       "__names__": {"platform": "platform",
                                     "platform/services/svc-a": "svc-a"}},
                      ["mod:com.acme:platform", "mod:com.acme:svc-a"]),
    "named-unlike-its-directory": (platform(__names__={"platform": "platform",
                                                       "platform/svc-a": "orders"}),
                                   ["mod:com.acme:platform", "mod:com.acme:svc-a"]),
    # Two components hold it - the root and `platform`, which has no build of its own - so it
    # reads up to the OUTERMOST one's root; on `main` it was three nodes, one per reading.
    "two-holders": ({".": {"settings.gradle": "rootProject.name = 'mono'\n"
                                              "include 'platform:svc-a'\n",
                           "build.gradle": ROOT_BUILD},
                     "platform": {"README.md": "x\n"},
                     "platform/svc-a": {"build.gradle": CHILD_BUILD},
                     "__names__": {".": "mono", "platform": "platform",
                                   "platform/svc-a": "svc-a"}},
                    ["mod:com.acme:mono", "mod:com.acme:svc-a"]),
    # Two settings above it, the root's and `platform`'s: the NEAREST governs, as every
    # reading of the parents finds it.
    "nearest-of-two-settings-above": (
        {".": {"settings.gradle": "rootProject.name = 'mono'\ninclude 'platform'\n",
               "build.gradle": "group = 'org.mono'\n"},
         "platform": {"settings.gradle": SETTINGS, "build.gradle": ROOT_BUILD},
         "platform/svc-a": {"build.gradle": CHILD_BUILD},
         "__names__": {".": "mono", "platform": "platform", "platform/svc-a": "svc-a"}},
        ["mod:com.acme:platform", "mod:com.acme:svc-a", "mod:org.mono:mono"]),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_child_takes_its_identity_from_the_build_above_it(tmp_path, shape):
    """One module, one node: the child's own reading names it as the parent's reading does -
    by the build root above it, and by its directory, as gradle names a subproject, never by
    the component's name."""
    estate, expected = SHAPES[shape]
    built = build(tmp_path, estate)
    assert built.code == 0
    assert mods(built) == expected


def test_a_child_with_its_own_settings_keeps_its_own_build_root(tmp_path):
    """A `settings.gradle` in the child's own tree governs it, from above as from inside: the
    nearest settings wins, as the parent's reading finds it."""
    estate = platform()
    estate["platform/svc-a"] = {"settings.gradle": "rootProject.name = 'svc-x'\n",
                                "build.gradle": "group = 'org.x'\n"}
    assert mods(build(tmp_path, estate)) == ["mod:com.acme:platform", "mod:org.x:svc-x"]


def _components(*pairs: tuple[str, str]) -> list[dict]:
    return [{"name": name, "path": path} for name, path in pairs]


def _gradle() -> list:
    return [r for r in B.discover_extractors() if r.name == "gradle-modules"]


def test_the_build_above_publishes_nothing_in_the_childs_shard(tmp_path):
    """Read for identity alone: the parent's own module, and what the parent depends on, are
    the parent's facts; the child's reading emits its own module and dependencies only."""
    estate = platform()
    estate["platform"]["build.gradle"] = (ROOT_BUILD + "dependencies {\n"
                                          "    implementation 'org.parent:only:1.0'\n}\n")
    lay_down(tmp_path, estate)
    root, comps = tmp_path / "ws", _components(("platform", "platform"),
                                               ("svc-a", "platform/svc-a"))
    regs = _gradle()
    act = B.activate(regs, comps, root, workspace=comps)
    owned, failures, _ = B.run_extractors_owned(regs, act, comps, root, "fixed", {})
    assert failures == {}
    child = sorted(n.id for n in owned["gradle-modules"]["svc-a"].nodes if n.type == "mod")
    assert child == ["mod:com.acme:svc-a", "mod:org.lib:util"]


def test_an_edit_to_the_build_above_stales_the_child(tmp_path):
    """The files above are inside the child's stamp: the parent's group changes the child's
    identity, so a `--check-fresh` or `--incremental` that missed it would reuse a shard
    naming the module by the old group."""
    lay_down(tmp_path, platform())
    root, comps = tmp_path / "ws", _components(("platform", "platform"),
                                               ("svc-a", "platform/svc-a"))

    def stamp() -> str:
        act = B.activate(_gradle(), comps, root, workspace=comps)
        return B.input_digests(act, comps, root)["svc-a"]["gradle-modules"]

    before = stamp()
    (root / "platform" / "build.gradle").write_bytes(b"group = 'com.other'\nversion = '1.0.0'\n")
    assert stamp() != before


def test_a_childs_stamp_reads_the_same_wherever_the_tree_is(tmp_path):
    """A file above the component is spelled relative to it in the stamp (`../build.gradle`),
    never by the absolute path a moved tree changes - or every committed shard of a nested
    gradle child would be stale on every other machine."""
    comps = _components(("platform", "platform"), ("svc-a", "platform/svc-a"))
    stamps = []
    for where in ("one", "elsewhere/two"):
        lay_down(tmp_path / where, platform())
        act = B.activate(_gradle(), comps, tmp_path / where / "ws", workspace=comps)
        stamps.append(B.input_digests(act, comps, tmp_path / where / "ws")["svc-a"])
    assert stamps[0] == stamps[1]


def test_a_directory_above_that_cannot_be_listed_is_said(tmp_path, monkeypatch):
    """Could-not-look is never absence: a directory above the child that cannot be listed
    leaves its build configuration unknown, recorded as the child's unlisted directory - a
    row and a stamp that is never current - not a child read as if nothing governed it."""
    lay_down(tmp_path, platform())
    root, comps = tmp_path / "ws", _components(("platform", "platform"),
                                               ("svc-a", "platform/svc-a"))
    real = os.scandir

    def refusing(path=".", *args, **kwargs):
        if Path(path) == root / "platform":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(B.os, "scandir", refusing)
    act = B.activate(_gradle(), comps, root, workspace=[*comps])
    assert [rel for rel, _ in act.unlisted.get("svc-a", [])] == [".."]
    assert B.input_digests(act, comps, root)["svc-a"][B.ACTIVATION_UNLISTED].startswith(
        "unreadable-")
