"""Where a compose declaration lands (T534 V1, V2, V8, V9; T579).

A component is BUILDING when any of its compose files declares `build:` (ruling 1), and its
other services stay its own. A built service lands on the component it is built from
(ruling 2), placed by `built_by`: pure path arithmetic on the workspace's component list, so
the same tree gives the same ids on every platform and machine, and a context that cannot
be placed gets a row whose remedy is accepted when followed.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import compose as C  # noqa: E402
from tools.graph.tests._estate import build, compose, producer, svc  # noqa: E402

FLAT = {"deploy": "deploy", "orders": "orders", "platform": "platform",
        "svc-a": "platform/svc-a"}


# ── V1: a building component's other services stay its own ─────────────────────────────


@pytest.mark.parametrize("files", [
    {"docker-compose.yml": compose(svc("web", ".", None), svc("db", None, None,
                                                              image="postgres:16"))},
    {"docker-compose.yml": compose(svc("web", ".", None)),
     "docker-compose.override.yml": compose(svc("db", None, None, image="postgres:16"))},
    {"docker-compose.yml": compose(svc("web", ".", None)),
     "infra/docker-compose.yml": compose(svc("db", None, None, image="postgres:16"))},
    {"docker-compose.yml": compose(svc("worker", "../billing", None),
                                   svc("db", None, None, image="postgres:16"))},
], ids=["one file", "an override", "a split infra directory", "a sibling-only build"])
def test_a_building_components_other_services_stay_repo_scoped(tmp_path, files):
    """D9-1 and D534-1's three shapes, pinned against main's ids: `db` never becomes a
    component's node or joins one, however the building statement is spread across files."""
    built = build(tmp_path, {"orders": files, "billing": {"README.md": "x\n"},
                             "db": {"app/api.py": 'from fastapi import APIRouter\n'}})
    db = [e for e in built.deploys if e["props"]["service"] == "db"]
    assert [(e["dst"], e["props"]["component"], e["props"]["mapped_by"]) for e in db] == [
        ("svc:db@orders", "", "")]
    assert "join" not in db[0]["props"]


def test_three_deployment_only_repos_do_not_contest_among_themselves(tmp_path):
    """D534-2: marked claims never meet each other directly, so three deployment files
    running a stock `redis` stay three repo-scoped nodes, with no contest."""
    built = build(tmp_path, {f"deploy-{c}": {"docker-compose.yml": compose(
        svc("redis", None, None, image="redis:7"))} for c in "abc"})
    assert sorted(e["dst"] for e in built.deploys) == [
        "svc:redis@deploy-a", "svc:redis@deploy-b", "svc:redis@deploy-c"]
    assert not built.kinds("contested-service", "name-joined-service")


# ── V2: `built_by`, the pure placement ───────────────────────────────────────────────────


@pytest.mark.parametrize("file_dir, context, own, expected", [
    ("deploy", "../orders", "deploy", ("orders", "exact")),
    ("orders", ".", "orders", ("orders", "exact")),
    ("orders", "./svc-a", "orders", ("orders", "own")),
    ("orders", "./nope", "orders", ("orders", "own")),
    ("deploy", "../platform/svc-ab", "deploy", ("platform", "within")),
    ("deploy", "../platform/svc-a", "deploy", ("svc-a", "exact")),
    ("deploy", "..\\orders", "deploy", ("orders", "exact")),
])
def test_built_by_places_by_path_arithmetic(file_dir, context, own, expected):
    comp, how, why = C.built_by(file_dir, context, FLAT, own, True)
    assert ((comp, how), why) == (expected, "")


@pytest.mark.parametrize("context, says", [
    (None, "context that is not a path"),
    ("${CTX}", "compose interpolates when it runs"),
    ("https://github.com/x/y.git", "not a directory in this workspace"),
    ("/opt/app", "an absolute path"),
    ("D" + ":/opt/app", "an absolute path"),          # a drive path, spelt the repo's way
    ("../../elsewhere", "lies outside this workspace as written"),
    ("../ORDERS", "differs only in letter case from component `orders` at `orders`"),
    ("../nowhere", "which no declared component holds as written"),
])
def test_a_context_that_cannot_be_placed_says_why(context, says):
    comp, how, why = C.built_by("deploy", context, FLAT, "deploy", True)
    assert (comp, how) == ("", "")
    assert says in why


def test_a_context_only_the_root_component_holds_is_not_placed():
    """D534-21: a root component holds every path, so it would turn any typo into a
    declared build of the root."""
    layout = {"mono": ".", "deploy": "deploy", "code": "code"}
    comp, how, why = C.built_by("deploy", "../misc", layout, "deploy", True)
    assert (comp, how) == ("", "")
    assert "no declared component but the workspace root's, as written" in why
    assert C.built_by(".", "./misc", layout, "mono", True)[:2] == ("mono", "own")


def test_a_single_component_layout_says_this_repository():
    layout = {"orders": "orders"}
    for context in ("../extra", "../orders.", "../../ws/orders"):
        comp, how, why = C.built_by("orders", context, layout, "orders", True)
        assert (comp, how) == ("", "")
        assert "lies outside this repository as written" in why
        assert "write it relative to this file" in why


@pytest.mark.parametrize("build_yaml, where, placed", [
    ("    build:\n      dockerfile: Dockerfile\n", "docker-compose.yml", ("orders", "")),
    ("    build:\n      context:\n", "docker-compose.yml", ("orders", "")),
    ("    build:\n      dockerfile: Dockerfile\n", "deploy/docker-compose.yml",
     ("orders", "orders/deploy")),
])
def test_a_build_mapping_with_no_context_builds_the_files_own_directory(
        tmp_path, build_yaml, where, placed):
    """D534-71, D534-89: the Compose Specification defaults `context` to the project
    directory, which the extractor takes as the file's own, like main. So `exact` from the
    component's root, `own` with `built_within` below it; the env stays main's."""
    built = build(tmp_path, {"orders": {where: (
        "services:\n  web:\n" + build_yaml + "    environment:\n      TOPIC: web-value\n")},
        "other": {"README.md": "x\n"}})
    edge = built.deploy(f"orders/{where}")
    assert (edge["dst"], edge["props"]["component"], edge["props"]["mapped_by"]) == (
        "svc:web@orders", "orders", "build")
    assert (edge["props"].get("built_within", ""), edge.get("provenance")) == (
        placed[1], "EXTRACTED")
    assert not built.kinds("compose-build-unresolved")


def test_two_declarations_one_file_types_apart_keep_their_own_claims(tmp_path):
    """D534-76: `on:` (a YAML boolean) and `"on":` (a string) are two declarations, and
    each keeps its own placement and env view: the first builds `.` and so files its
    setting under the component, as main does."""
    built = build(tmp_path, {"orders": {
        "docker-compose.yml": ("services:\n  on:\n    build: .\n    environment:\n"
                               "      TOPIC: a\n  \"on\":\n    environment:\n      TOPIC: b\n"),
        "app/pub.py": producer("TOPIC")}})
    assert built.topics == ["topic:a"]
    assert sorted((e["dst"], e["props"]["mapped_by"]) for e in built.deploys) == [
        ("svc:on@orders", "build"), ("svc:on@orders", "build")]


# ── V2: a layer inherits, in all four cells (D534-41, D534-51) ───────────────────────────

LAYERS = {
    "over an exact build": (
        {"deploy": {"docker-compose.yml": compose(svc("web", "../orders", None)),
                    "docker-compose.zz.yml": compose(svc("web"))},
         "orders": {"README.md": "x\n"}},
        ("svc:web@orders", "orders", "EXTRACTED", "")),
    "over an own typo": (
        {"orders": {"docker-compose.yml": compose(svc("web", "./nope", None)),
                    "docker-compose.zz.yml": compose(svc("web"))},
         "other": {"README.md": "x\n"}},
        ("svc:web@orders", "orders", "EXTRACTED", "orders/nope")),
    "over a within build": (
        {"deploy": {"docker-compose.yml": compose(svc("web", "../platform/svc-ab", None)),
                    "docker-compose.zz.yml": compose(svc("web"))},
         "platform": {"svc-a/Dockerfile": "FROM scratch\n"}, "platform/svc-a": {}},
        ("svc:web@platform", "platform", "INFERRED", "platform/svc-ab")),
    "over building files that disagree": (
        {"deploy": {"docker-compose.a.yml": compose(svc("web", "../platform/svc-ab", None)),
                    "docker-compose.b.yml": compose(svc("web", "../platform", None)),
                    "docker-compose.zz.yml": compose(svc("web"))},
         "platform": {"README.md": "x\n"}},
        ("svc:web@platform", "platform", "INFERRED", "")),
}


@pytest.mark.parametrize("cell", sorted(LAYERS))
def test_a_layer_inherits_the_placement_of_the_build_it_layers(tmp_path, cell):
    estate, (target, component, provenance, within) = LAYERS[cell]
    built = build(tmp_path, estate)
    edge = built.deploy("deploy/docker-compose.zz.yml" if "deploy" in estate
                        else "orders/docker-compose.zz.yml")
    assert (edge["dst"], edge["props"]["component"], edge.get("provenance"),
            edge["props"].get("built_within", "")) == (target, component, provenance, within)


def test_a_layer_over_builds_from_two_components_gets_the_ambiguous_row(tmp_path):
    built = build(tmp_path, {
        "deploy": {"docker-compose.a.yml": compose(svc("web", "../x", None)),
                   "docker-compose.b.yml": compose(svc("web", "../y", None)),
                   "docker-compose.c.yml": compose(svc("web"))},
        "x": {"README.md": "x\n"}, "y": {"README.md": "y\n"}})
    assert built.placed("deploy/docker-compose.a.yml")[:2] == ("svc:web@x", "x")
    assert built.placed("deploy/docker-compose.b.yml")[:2] == ("svc:web@y", "y")
    assert built.placed("deploy/docker-compose.c.yml") == ("svc:web@deploy", "", "")
    row, = built.kinds("compose-build-ambiguous")
    assert (row["source_file"], row["components"]) == ("docker-compose.c.yml", ["x", "y"])
    assert "Declaring the `build:` it layers here places it" in row["why"]


def test_letter_case_file_names_place_the_same_on_every_platform(tmp_path):
    """D534-7: each file places its own `build:`, so no order - case-folded or not -
    decides which component a service lands on."""
    built = build(tmp_path, {
        "deploy": {"compose-a.yml": compose(svc("api", "../y", None)),
                   "compose-B.yml": compose(svc("api", "../x", None)),
                   "compose-c.yml": compose(svc("api"))},
        "x": {"README.md": "x\n"}, "y": {"README.md": "y\n"}})
    assert built.placed("deploy/compose-a.yml")[0] == "svc:api@y"
    assert built.placed("deploy/compose-B.yml")[0] == "svc:api@x"
    assert built.placed("deploy/compose-c.yml")[0] == "svc:api@deploy"
    assert built.kinds("compose-build-ambiguous")


# ── V8: with no component list, only the component's own tree places ─────────────────────


def test_with_no_component_list_only_the_components_own_tree_places(tmp_path):
    """D534-10: an in-process caller that gives no list gets the component's own tree and
    a row for anything leaving it, never a guess at another component."""
    compose_file = tmp_path / "orders" / "docker-compose.yml"
    compose_file.parent.mkdir(parents=True)
    compose_file.write_bytes(compose(svc("a", ".", None), svc("b", "./nope", None),
                                     svc("c", "../billing", None)).encode("utf-8"))
    repo = RepoRef(alias="orders", path=tmp_path / "orders")
    res = C.ComposeExtractor().extract(ExtractContext(
        repos=[repo], run_id="r", files={"orders": [compose_file]}, config={}))
    placed = {e.props["service"]: (e.props["component"], e.props["mapped_by"])
              for e in res.edges if e.type == "DG_DEPLOYS"}
    assert placed == {"a": ("orders", "build"), "b": ("orders", "build"), "c": ("", "")}
    row, = [r for r in res.unresolved if r["kind"] == "compose-build-unresolved"]
    assert "the workspace's component list was not given" in row["why"]


# ── V9: no read or construction over main; bodies that are not services are skipped ─────


def test_each_file_is_read_once_and_each_body_constructed_once(tmp_path, monkeypatch):
    """Pinned as main's (D534-68, D534-75): the claims pre-pass works from the one parse,
    and constructs each service body once and hands it on."""
    from tools.graph.core.lib import repo_walk
    from tools.graph.extractors import _yaml
    counts = {"read": 0, "construct": 0}
    read, construct = repo_walk.read_text_reporting, _yaml.constructed

    def counting_read(*a, **k):
        counts["read"] += 1
        return read(*a, **k)

    def counting_construct(*a, **k):
        counts["construct"] += 1
        return construct(*a, **k)

    monkeypatch.setattr(repo_walk, "read_text_reporting", counting_read)
    monkeypatch.setattr(_yaml, "constructed", counting_construct)
    built = build(tmp_path, {"orders": {
        "docker-compose.yml": compose(svc("web", ".", None),
                                      svc("db", None, None, image="postgres:16")),
        "docker-compose.prod.yml": compose(svc("web", None, "b"))}},
        "--only", "compose-services")
    assert built.code == 0
    assert counts == {"read": 4, "construct": 3}


def test_a_scalar_or_list_body_is_skipped_and_the_next_service_keeps_its_line(tmp_path):
    """D534-81: which bodies are services moved into the pre-pass; the line cursor did
    not. Pinned against main's lines."""
    built = build(tmp_path, {"orders": {"docker-compose.yml": (
        "services:\n  worker:\n  scalar: hello\n  listy:\n    - a\n  web:\n    build: .\n"
        "    environment:\n      TOPIC: web-value\n")}})
    assert sorted((e["dst"], e["line"]) for e in built.deploys) == [
        ("svc:web@orders", 6), ("svc:worker@orders", 2)]
