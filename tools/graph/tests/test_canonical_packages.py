"""The canonical pass on T534's ids: packages, owners, choices and the proposal (T534 V6).

One FILE is one package, keyed on its workspace-relative path. Since T591 a component reads
its own tree and not its declared children's, and since T615 the installer refuses two
components over one directory, so a file has two readers only where a child is declared in
another case than its directory on a filesystem that folds case (D15a-19); it is then one
package, named once. A block's `owner` selects the packages that component reads and its `path` the
packages it covers; a decision needs exactly one. `--propose-canonical` drafts only choices
that decide, and a draft pasted unfilled decides nothing.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.model import Edge, Node  # noqa: E402
from tools.graph.extractors import _canonical  # noqa: E402
from tools.graph.links import canonical as C  # noqa: E402
from tools.graph.tests._estate import build, compose, lay_down, spawn, svc  # noqa: E402

ROUTER = 'from fastapi import APIRouter\nrouter = APIRouter(prefix="/cache")\n'
BUILD_DOT = compose(svc("web", ".", None))


def nested(extra: dict | None = None) -> dict:
    """`platform` holds its declared child `svc-a`'s file, which `svc-a` alone reads since
    T591; `deploy` builds `svc-a` too."""
    return {"platform": {"svc-a/docker-compose.yml": BUILD_DOT}, "platform/svc-a": {},
            "deploy": {"docker-compose.yml": compose(svc("web", "../platform/svc-a", None))},
            **(extra or {})}


def parent_and_child(extra: dict | None = None) -> dict:
    return {"platform": {"docker-compose.yml": compose(svc("web", "./svc-a", None)),
                         "svc-a/docker-compose.yml": BUILD_DOT},
            "platform/svc-a": {}, **(extra or {})}


def two_readers_of_one_directory(extra: dict | None = None) -> dict:
    """Two components declared over one directory, by respelling it (T615's shape), which
    the installer refuses: every file there would have two readers (D15a-4)."""
    return {"shared": {"docker-compose.yml": BUILD_DOT},
            "deploy": {"docker-compose.yml": compose(svc("web", "../shared", None))},
            "__components__": [("shared", "shared"), ("shared2", "./shared"),
                               ("deploy", "deploy")], **(extra or {})}


def two_own_beside_one(extra: dict | None = None) -> dict:
    """A component reading two of its own packages beside another component's one: the
    choices mix an owner alone with an owner and a path (D15a-11)."""
    return {"orders": {"docker-compose.yml": BUILD_DOT, "docker-compose.prod.yml": BUILD_DOT},
            "deploy": {"docker-compose.yml": compose(svc("web", "../orders", None))},
            **(extra or {})}


def root_and_children(extra: dict | None = None) -> dict:
    return {".": {"svc-a/Dockerfile": "FROM scratch\n",
                  "other/docker-compose.yml": compose(svc("web", "../svc-a", None)),
                  "deploy/docker-compose.yml": compose(svc("web", "../svc-a", None))},
            "svc-a": {}, "deploy": {}, "__names__": {".": "mono"}, **(extra or {})}


def one_component_two_files(extra: dict | None = None) -> dict:
    return {"orders": {"docker-compose.yml": BUILD_DOT, "docker-compose.prod.yml": BUILD_DOT},
            **(extra or {})}


def two_repos(extra: dict | None = None) -> dict:
    return {"svc-a": {"README.md": "x\n"},
            "deploy-a": {"docker-compose.yml": compose(svc("web", "../svc-a", None))},
            "deploy-b": {"docker-compose.yml": compose(svc("web", "../svc-a", None))},
            **(extra or {})}


def contested(built) -> dict:
    row, = built.kinds("contested-service")
    return row


# ── packages are files, each cited once (D534-18, D534-42; T591) ─────────────────────────


def test_two_components_over_one_directory_never_reach_the_graph(tmp_path):
    """T615: the installer refuses two components over one directory, so the build refuses
    the workspace, naming both, rather than reading every file there twice."""
    lay_down(tmp_path, two_readers_of_one_directory())
    run = spawn(tmp_path)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "both describe" in run.stdout + run.stderr
    assert "'shared'" in run.stdout + run.stderr and "'shared2'" in run.stdout + run.stderr
    assert "Traceback" not in run.stdout + run.stderr


def _claim(component: str, file: str, package: str = "") -> Edge:
    return Edge(src=f"deploy:{component}/{file}", type="DG_DEPLOYS", dst="svc:web@shared",
                source_file=file, line=2, extractor="compose-services", props={"service": "web"})


def _nodes(layout: dict[str, str], files: dict[str, str]) -> dict[str, Node]:
    """A compose deployment node per (component, file); `files` maps a file to the package
    file it names (an override names its base, T588), or ""."""
    return {f"deploy:{c}/{f}": Node(id=f"deploy:{c}/{f}", type="deploy", props={
                "kind": "compose", "path": f, "component": c, **({"package": p} if p else {})})
            for c in layout for f, p in files.items()}


def test_a_file_two_components_read_is_one_package_and_one_file():
    """D534-18 and D15a-37, asked of the keying itself: two readings of one file - the shape
    a case-twin child still makes where the filesystem folds case (D15a-19), since T615
    refuses two components over one directory - are one package and one file, and a pair's
    override keys on its base (T588) from either reader; an owner naming EITHER reader
    selects the package (D534-30)."""
    layout = {"shared": "shared", "shared2": "shared", "deploy": "deploy"}
    nodes = _nodes(layout, {"docker-compose.yml": "",
                            "docker-compose.override.yml": "docker-compose.yml"})
    base_a, base_b = _claim("shared", "docker-compose.yml"), _claim("shared2", "docker-compose.yml")
    over_a = _claim("shared", "docker-compose.override.yml")
    over_b = _claim("shared2", "docker-compose.override.yml")
    deploy = _claim("deploy", "docker-compose.yml")
    assert {C.package_of(e, nodes, layout) for e in (base_a, base_b, over_a, over_b)} == \
        {"shared/docker-compose.yml"}
    assert C.file_of(base_a, nodes, layout) == C.file_of(base_b, nodes, layout)
    assert C.file_of(over_a, nodes, layout) == C.file_of(over_b, nodes, layout)
    assert C.package_of(deploy, nodes, layout) == "deploy/docker-compose.yml"
    packages = {"shared/docker-compose.yml": [(0, base_a), (1, base_b)],
                "deploy/docker-compose.yml": [(2, deploy)]}
    assert C.select_package(packages, {"service": "web", "owner": "shared2"}, nodes)[0] \
        == "shared/docker-compose.yml"


def test_a_nested_or_root_layout_invents_no_contest(tmp_path):
    nested_only = build(tmp_path / "a", {"platform": {"svc-a/docker-compose.yml": BUILD_DOT},
                                         "platform/svc-a": {}})
    root = build(tmp_path / "b", {".": {"svc-a/Dockerfile": "FROM scratch\n",
                                        "deploy/docker-compose.yml": compose(
                                            svc("a", "../svc-a", None),
                                            svc("b", None, None, image="x:1"))},
                                  "svc-a": {}, "deploy": {}, "__names__": {".": "mono"}})
    assert not nested_only.kinds("contested-service")
    assert not root.kinds("contested-service")


def test_a_component_spelt_dot_slash_names_its_packages_the_same(tmp_path):
    """D534-42: `./platform` and `platform` are one place, through the one normalizing
    reading of the component list that compose, this pass and the join share - so the parent
    declared as `./platform` leaves `platform/svc-a`'s file to that child (T591)."""
    built = build(tmp_path, {"./platform": {"svc-a/docker-compose.yml": BUILD_DOT},
                             "platform/svc-a": {},
                             "deploy": {"docker-compose.yml": compose(
                                 svc("web", "../platform/svc-a", None))},
                             "__names__": {"./platform": "platform"}})
    assert contested(built)["claims"] == ["deploy/docker-compose.yml:2",
                                          "svc-a/docker-compose.yml:2"]


@pytest.mark.parametrize("make, owner, won", [
    (nested, "svc-a", "svc-a/docker-compose.yml"),
    (nested, "deploy", "deploy/docker-compose.yml"),
])
def test_an_owner_naming_a_packages_reader_decides_it(tmp_path, make, owner, won):
    built = build(tmp_path, make({"__canonical__": [{"service": "web", "owner": owner}]}))
    assert not built.kinds("contested-service")
    lost = [e for e in built.deploys if e.get("canonical") is False]
    assert lost and all(e["props"]["superseded_by"] == won for e in lost)


def test_an_owner_naming_a_parent_for_its_childs_file_decides_nothing(tmp_path):
    """T591, the first direction (the owner's ruling, 2026-10-02): the parent no longer
    reads its child's file, so a block naming it there selects nothing; the row stands and
    offers the child."""
    built = build(tmp_path, nested({"__canonical__": [{"service": "web",
                                                        "owner": "platform"}]}))
    assert {"owner": "svc-a"} in contested(built)["choices"]


def test_an_owner_naming_a_parent_now_decides_its_own_package(tmp_path):
    """T591, the second direction (D15a-3): a parent that read its own file and its child's
    selected neither on `main` (D534-39); reading only its own, it now decides for it."""
    built = build(tmp_path, parent_and_child({"__canonical__": [{"service": "web",
                                                                  "owner": "platform"}]}))
    assert not built.kinds("contested-service")
    lost = [e for e in built.deploys if e.get("canonical") is False]
    assert [e["src"] for e in lost] == ["deploy:svc-a/docker-compose.yml"]
    assert lost[0]["props"]["superseded_by"] == "platform/docker-compose.yml"


@pytest.mark.parametrize("make, block, decides", [
    (parent_and_child, {"owner": "platform"}, True),      # its own package alone (T591)
    (parent_and_child, {"owner": "svc-a"}, True),
    (parent_and_child, {"owner": "platform", "path": "docker-compose.yml"}, True),
    (root_and_children, {"owner": "mono"}, True),             # what lies outside its children
    (root_and_children, {"owner": "deploy"}, True),
    (one_component_two_files, {"owner": "orders"}, False),    # two packages of its own
])
def test_an_owner_that_reads_two_packages_decides_nothing(tmp_path, make, block, decides):
    """D534-39: selecting takes exactly one package. Since T591 a parent or the root reads
    only its own tree, so it is a component reading two of its OWN packages that selects
    two, and needs a `path`."""
    built = build(tmp_path, make({"__canonical__": [{"service": "web", **block}]}))
    assert (not built.kinds("contested-service")) is decides


@pytest.mark.parametrize("make, choices", [
    (two_repos, [{"owner": "deploy-a"}, {"owner": "deploy-b"}]),
    (one_component_two_files, [{"owner": "orders", "path": "docker-compose.prod.yml"},
                               {"owner": "orders", "path": "docker-compose.yml"}]),
    (parent_and_child, [{"owner": "platform"}, {"owner": "svc-a"}]),
    (root_and_children, [{"owner": "deploy"}, {"owner": "mono"}]),
    (two_own_beside_one, [{"owner": "deploy"},
                          {"owner": "orders", "path": "docker-compose.prod.yml"},
                          {"owner": "orders", "path": "docker-compose.yml"}]),
])
def test_each_package_is_offered_the_smallest_block_that_selects_exactly_it(
        tmp_path, make, choices):
    assert contested(build(tmp_path, make()))["choices"] == choices


# ── --propose-canonical: three forms, each decides when filled (D534-50) ─────────────────

FORMS = {
    "an owner list": (two_repos, 'owner = ""            # one of: deploy-a | deploy-b',
                      ('owner = ""', 'owner = "deploy-b"')),
    "an owner pre-filled with a path slot": (
        one_component_two_files,
        'owner = "orders"\npath = ""             # one of: "docker-compose.prod.yml" | '
        '"docker-compose.yml"',
        ('path = ""', 'path = "docker-compose.prod.yml"')),
    "choice lines": (two_own_beside_one, '# choice: owner = "deploy"',
                     ('owner = ""', 'owner = "deploy"')),
}


def drafted(tmp_path: Path, make) -> str:
    built = build(tmp_path, make(), "--propose-canonical")
    assert built.code == 0
    text = (tmp_path / "out" / "canonical.proposed.toml").read_text(encoding="utf-8")
    return "[[canonical]]" + text.split("[[canonical]]", 1)[1]


def paste(where: Path, block: str) -> None:
    toml = where / "ws" / "workspace.toml"
    toml.write_bytes(toml.read_bytes() + ("\n" + block + "\n").encode("utf-8"))


@pytest.mark.parametrize("form", sorted(FORMS))
def test_each_draft_form_decides_once_filled(tmp_path, form):
    make, shows, (blank, filled) = FORMS[form]
    block = drafted(tmp_path, make)
    assert shows in block
    paste(tmp_path, re.sub(r"^" + re.escape(blank) + r".*$", filled, block, count=1,
                           flags=re.M))
    rebuilt = build(tmp_path)
    assert rebuilt.code == 0
    assert not rebuilt.kinds("contested-service")


@pytest.mark.parametrize("form, refused", [("an owner list", True),
                                           ("an owner pre-filled with a path slot", False),
                                           ("choice lines", True)])
def test_a_draft_pasted_unfilled_decides_nothing(tmp_path, form, refused):
    """As a process: a blank owner is refused loudly by the workspace parser; a blank path
    builds and stays reported. Neither decides silently."""
    make, _, _ = FORMS[form]
    paste(tmp_path, drafted(tmp_path, make))
    run = spawn(tmp_path, out="again")
    if refused:
        assert run.returncode == 2, run.stdout + run.stderr
        assert "Traceback" not in run.stderr
    else:
        assert run.returncode == 0, run.stdout + run.stderr
        from tools.graph.tests._estate import read
        assert read(0, tmp_path / "again").kinds("contested-service")


# ── the shared reading of the component list (D534-42) ──────────────────────────────────


@pytest.mark.parametrize("written, read", [
    ("./x", "x"), ("x/", "x"), ("x//y", "x/y"), ("x/./y", "x/y"), (".", "."), ("", "."),
    ("x\\y", "x/y"), ("./", "."),
])
def test_the_component_layout_reads_each_spelling_as_one_place(written, read):
    layout = _canonical.workspace_layout({"components": [{"name": "c", "path": written}]})
    assert layout == {"c": read}


# ── block names compare normalized; a second spelling is reported (D534-24, D534-34) ─────

HALVES = {"orders": {
    "docker-compose.yml": 'services:\n  "my api":\n    build: .\n    environment:\n'
                          '      TOPIC: base\n',
    "docker-compose.prod.yml": 'services:\n  "my api":\n    build: .\n    environment:\n'
                               '      TOPIC: prod\n'}}


@pytest.mark.parametrize("spelling", ["my api", "my-api"])
def test_a_block_decides_both_halves_in_either_spelling(tmp_path, spelling):
    """D534-4, D534-24: the edges and the env, in the name as compose writes it and as the
    id spells it."""
    built = build(tmp_path, {**HALVES, "__canonical__": [
        {"service": spelling, "owner": "orders", "path": "docker-compose.yml"}]})
    assert not built.kinds("contested-service")
    row, = built.kinds("superseded-declaration")
    assert (row["source_file"], row["value"]) == ("docker-compose.prod.yml", "prod")


def test_a_raw_spelling_acknowledges_a_name_join(tmp_path):
    built = build(tmp_path, {"deploy": {"docker-compose.yml": 'services:\n  "my redis":\n'
                                                               '    image: redis:7\n'},
                             "my-redis": {"app/api.py": ROUTER},
                             "__canonical__": [{"service": "my redis", "owner": "deploy"}]})
    assert built.deploy("deploy/docker-compose.yml")["dst"] == "svc:my-redis@my-redis"
    assert not built.kinds("name-joined-service")


def test_two_blocks_naming_one_service_the_first_decides_and_the_other_is_reported(tmp_path):
    """D534-34: the first block after normalizing decides; `canonical-duplicate` names the
    ignored one."""
    built = build(tmp_path, {**HALVES, "__canonical__": [
        {"service": "my api", "owner": "orders", "path": "docker-compose.prod.yml"},
        {"service": "my-api", "owner": "orders", "path": "docker-compose.yml"}]})
    dup, = built.kinds("canonical-duplicate")
    assert dup["service"] == "my-api"
    lost, = [e for e in built.deploys if e.get("canonical") is False]
    assert lost["props"]["superseded_by"] == "orders/docker-compose.prod.yml"
