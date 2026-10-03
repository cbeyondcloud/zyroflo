"""T588: a compose project's base and override are one package (batch 15a).

The owner's ruling A (2026-10-02): in one directory of one component, compose's own default
pair - the base `docker compose up` reads and the override it merges over it with no `-f`
(compose-go `cli/options.go`, `DefaultFileNames` and `DefaultOverrideFileNames`) - is one
package, named by its base. Every other compose file stays its own package. The compose
extractor declares the pairing on the override's deployment node as `package`, and an
override service with no `build:` of its own takes its base's claim for the same name: eight
of `Claim`'s eleven fields, never `view` (ruling 10 keeps env main's) nor the reasons behind
the base's own rows (`unplaced`, `ambiguous`). Design: plans/batch-15a-design-note.md.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.core.model import Edge, Node  # noqa: E402
from tools.graph.links.canonical import CanonicalPass  # noqa: E402
from tools.graph.tests._estate import build, compose, producer, svc  # noqa: E402

WEB = compose(svc("web", ".", None))
LAYER = compose(svc("web", None, "layer-value"))
BROKEN = "services:\n  web:\n    environment:\n      KEY: \"unterminated\n"


def packages(built) -> dict[tuple[str, str], str]:
    """Each compose deployment node's (component, file), mapped to the `package` it declares
    ("" for none)."""
    return {(n["props"]["component"], n["props"]["path"]): n["props"].get("package", "")
            for n in built.nodes
            if n.get("type") == "deploy" and n["props"].get("kind") == "compose"}


@pytest.mark.parametrize("files, want", [
    ({"docker-compose.yml": WEB, "docker-compose.override.yml": LAYER},
     {"docker-compose.yml": "", "docker-compose.override.yml": "docker-compose.yml"}),
    ({"compose.yaml": WEB, "compose.override.yaml": LAYER},
     {"compose.yaml": "", "compose.override.yaml": "compose.yaml"}),
    ({"docker-compose.yml": WEB, "compose.override.yml": LAYER},       # either family
     {"docker-compose.yml": "", "compose.override.yml": "docker-compose.yml"}),
    ({"compose.yaml": WEB, "docker-compose.yml": WEB, "compose.override.yml": LAYER},
     {"compose.yaml": "", "docker-compose.yml": "", "compose.override.yml": "compose.yaml"}),
    ({"docker-compose.yml": WEB, "compose.override.yml": LAYER,
      "docker-compose.override.yml": LAYER},
     {"docker-compose.yml": "", "compose.override.yml": "docker-compose.yml",
      "docker-compose.override.yml": ""}),
    ({"docker-compose.override.yml": LAYER}, {"docker-compose.override.yml": ""}),
    ({"docker-compose.yml": WEB, "docker-compose.prod.yml": LAYER},
     {"docker-compose.yml": "", "docker-compose.prod.yml": ""}),
    ({"deploy/docker-compose.yml": WEB, "deploy/docker-compose.override.yml": LAYER,
      "deploy/legacy/docker-compose.yml": WEB},
     {"deploy/docker-compose.yml": "",
      "deploy/docker-compose.override.yml": "deploy/docker-compose.yml",
      "deploy/legacy/docker-compose.yml": ""}),
    ({"docker-compose.yml": WEB, "ops/docker-compose.override.yml": LAYER},
     {"docker-compose.yml": "", "ops/docker-compose.override.yml": ""}),
    ({"docker-compose.yml": WEB, "docker-compose.Override.yml": LAYER},
     {"docker-compose.yml": "", "docker-compose.Override.yml": ""}),
    ({"docker-compose.yml": BROKEN, "docker-compose.override.yml": LAYER},
     {"docker-compose.override.yml": "docker-compose.yml"}),
    ({"docker-compose.yml": WEB, "docker-compose.override.yml": BROKEN},
     {"docker-compose.yml": ""}),
], ids=["docker-compose", "compose", "mixed-families", "two-bases", "two-overrides",
        "no-base", "prod-is-not-a-layer", "subdirectory", "base-in-another-directory",
        "off-case-override", "base-not-parsing", "override-not-parsing"])
def test_the_override_names_its_base_as_its_package(tmp_path, files, want):
    """By compose's own discovery, per directory, from the files the component READS (so a
    base that does not parse still names the project), with names matched exactly: the
    globs read `docker-compose.Override.yml`, and compose's lists do not name it."""
    assert packages(build(tmp_path, {"orders": files})) == \
        {("orders", rel): package for rel, package in want.items()}


def test_a_nested_childs_pair_is_the_childs_package(tmp_path):
    """The pair is found in the component that reads it: a declared child's files are its
    own (T591), so the child's override names the child's base and the parent, beside its
    own base, reads neither."""
    built = build(tmp_path, {"platform": {"docker-compose.yml": WEB},
                             "platform/svc-a": {"docker-compose.yml": WEB,
                                                "docker-compose.override.yml": LAYER}})
    assert packages(built) == {("platform", "docker-compose.yml"): "",
                               ("svc-a", "docker-compose.yml"): "",
                               ("svc-a", "docker-compose.override.yml"): "docker-compose.yml"}


def three_way(override: str) -> dict:
    """D534-100's shape: two components each built by one of `deploy`'s files, and an
    override over the base."""
    return {"x": {"app.py": producer("TOPIC")}, "y": {"app.py": producer("TOPIC")},
            "deploy": {"docker-compose.yml": compose(svc("web", "../x", None)),
                       "docker-compose.prod.yml": compose(svc("web", "../y", None)),
                       "docker-compose.override.yml": override}}


def edge_of(built, source: str, service: str | None = None) -> tuple[str, str, str, str]:
    """(target, `mapped_by`, provenance, `built_within`) of the one edge `source` deploys."""
    edge = built.deploy(source, service)
    props = edge.get("props") or {}
    return (edge["dst"], props.get("mapped_by", ""), edge["provenance"],
            props.get("built_within", ""))


def test_an_override_with_no_build_takes_its_bases_placement(tmp_path):
    """D534-100: a build-less layer over two building files was `compose-build-ambiguous`,
    the one build row with no way on. As its base's layer it takes the base's claim, and
    the two builds stay their own edges."""
    built = build(tmp_path, three_way(compose(svc("web", None, "over-value"))))
    assert edge_of(built, "deploy/docker-compose.override.yml") == \
        edge_of(built, "deploy/docker-compose.yml") == ("svc:web@x", "build", "EXTRACTED", "")
    assert edge_of(built, "deploy/docker-compose.prod.yml")[0] == "svc:web@y"
    assert not built.kinds("compose-build-ambiguous")


def test_an_overrides_environment_keeps_mains_view(tmp_path):
    """Ruling 10: `view` is not taken. The base builds `deploy` itself, so its values are
    `deploy`'s; the override's stay filed under the service's name, main's view, so
    `deploy`'s code reads the base's value although the override is read after it - the
    first of the three consequences the design leaves for T590, pinned so that taking the
    base's view cannot happen silently."""
    built = build(tmp_path, {"deploy": {
        "app.py": producer("TOPIC"),
        "compose.yml": compose(svc("web", ".", "base-value")),
        "docker-compose.override.yml": compose(svc("web", None, "over-value"))}})
    assert "topic:base-value" in built.topics
    assert "topic:over-value" not in built.topics


def test_an_override_with_its_own_build_keeps_its_own_placement(tmp_path):
    """Review focus 5: an override that declares its own, different `build:` inherits
    nothing - its own build places it."""
    built = build(tmp_path, three_way(compose(svc("web", "../y", "over-value"))))
    assert edge_of(built, "deploy/docker-compose.override.yml")[0] == "svc:web@y"
    assert edge_of(built, "deploy/docker-compose.yml")[0] == "svc:web@x"


def test_an_override_takes_its_bases_name_only_claim_too(tmp_path):
    """D15a-15: the claim is taken whole but for three fields, `join` included - so a
    deployment-only component's override joins the same-named component with its base,
    rather than staying on a node of its own with no record."""
    built = build(tmp_path, {
        "redis": {"app/api.py": producer("TOPIC")},
        "deploy": {"docker-compose.yml": compose(svc("redis", None, None, image="redis:7")),
                   "docker-compose.override.yml": compose(svc("redis", None, None,
                                                              image="redis:6"))}})
    assert edge_of(built, "deploy/docker-compose.yml") == \
        edge_of(built, "deploy/docker-compose.override.yml") == \
        ("svc:redis@redis", "name", "INFERRED", "")


@pytest.mark.parametrize("files, service, want_edge, want_rows", [
    ({"docker-compose.yml": compose(svc("web", "../x/app", None)),
      "docker-compose.prod.yml": compose(svc("web", "../y", None))},
     "web", ("svc:web@x", "build", "INFERRED", "x/app"), []),
    ({"docker-compose.yml": compose(svc("web", "../nope", None)),
      "docker-compose.prod.yml": compose(svc("web", "../x", None))},
     "web", ("svc:web@deploy", "", "EXTRACTED", ""),
     [("compose-build-unresolved", "docker-compose.yml")]),
    ({"docker-compose.yml": compose(svc("web", None, None)),
      "docker-compose.prod.yml": compose(svc("web", "../x", None)),
      "docker-compose.staging.yml": compose(svc("web", "../y", None))},
     "web", ("svc:web@deploy", "", "EXTRACTED", ""),
     [("compose-build-ambiguous", "docker-compose.yml")]),
    ({"docker-compose.yml": compose(svc("web", "../x", None))},
     "debug", ("svc:debug@deploy", "", "EXTRACTED", ""), []),
], ids=["placement-inside-another-component", "unplaced-base", "ambiguous-base",
        "a-service-the-base-lacks"])
def test_what_an_override_takes_from_its_base(tmp_path, files, service, want_edge,
                                              want_rows):
    """Each kind of claim a base can hold, under a build-less override.

    - A build strictly inside another component: the label and the inference come with
      the target.
    - An unplaced build, beside a prod file that places one: the override layers the base,
      not prod, so it lands on the base's node - and the row saying why the build is not
      read stays the base's alone.
    - An ambiguous base: the same, and the base's row's remedy (declare the `build:` it
      layers there) would place the override with it.
    - A service the base does not declare: nothing to take, so its own claim stands."""
    override = compose(svc("web", None, "over-value"), svc("debug", None, None))
    built = build(tmp_path, {
        "x": {"app/main.py": producer("TOPIC")}, "y": {"main.py": producer("TOPIC")},
        "deploy": {**files, "docker-compose.override.yml": override}})
    assert edge_of(built, "deploy/docker-compose.override.yml", service) == want_edge
    assert [(r["kind"], r["source_file"]) for r in built.kinds(
        "compose-build-unresolved", "compose-build-ambiguous")] == want_rows


def test_an_overrides_env_origin_follows_its_base(tmp_path):
    """D15a-27: an override's env ORIGIN follows its base. Service `x`, built from `../x` by
    `compose.yml` and from `../y` by a prod file, is set by a build-less override that sorts
    after its base, so its value is the one `x`'s code reads (one of the four pair spellings
    where the override's value resolves). On `main` that override was a building
    component's service it does not place, and `env_keys` warned so in `x`'s shard
    (`env-key-from-building-repo`). As its base's layer it is placed, and the warning goes;
    the value read is unchanged."""
    built = build(tmp_path, {
        "x": {"reader.py": producer("TOPIC")}, "y": {"reader.py": producer("TOPIC")},
        "deploy": {"compose.yml": compose(svc("x", "../x", "base-value")),
                   "docker-compose.prod.yml": compose(svc("x", "../y", None)),
                   "docker-compose.override.yml": compose(svc("x", None, "over-value"))}})
    warned = [r for r in built.kinds("env-key-from-building-repo")
              if "docker-compose.override.yml" in r.get("why", "")]
    assert warned == []
    assert "topic:over-value" in built.topics


# ── the link passes read `package` (decisions 3 and 4) ───────────────────────────────────


def contested(built) -> dict:
    row, = built.kinds("contested-service")
    return row


THREE = {"docker-compose.yml": WEB, "docker-compose.override.yml": LAYER,
         "docker-compose.prod.yml": WEB}


@pytest.mark.parametrize("files", [
    {"docker-compose.yml": WEB, "docker-compose.override.yml": LAYER},
    {"compose.yaml": WEB, "compose.override.yaml": LAYER},
    {"docker-compose.yml": WEB, "docker-compose.override.yml": WEB},
], ids=["env-only-override", "compose-family", "both-building"])
def test_a_base_and_its_override_do_not_contest(tmp_path, files):
    """Decision 3: the pair is one package, so its two claims on one node are no contest
    (D9-21) - the shapes of the design's first table, each of which `main` reported."""
    built = build(tmp_path, {"orders": files})
    assert {e["dst"] for e in built.deploys} == {"svc:web@orders"}
    assert not built.kinds("contested-service")


def test_a_prod_file_still_contests_and_the_row_counts_files(tmp_path):
    """Ruling 2: a prod file is its own package. The row cites every file of every package
    once and counts both (decision 4), and the pair's choice names its base."""
    row = contested(build(tmp_path, {"orders": THREE}))
    assert row["claims"] == ["orders/docker-compose.override.yml:2",
                             "orders/docker-compose.prod.yml:2", "orders/docker-compose.yml:2"]
    assert row["why"].startswith("2 deployment packages (3 files) claim svc:web@orders,")
    assert row["choices"] == [{"owner": "orders", "path": "docker-compose.prod.yml"},
                              {"owner": "orders", "path": "docker-compose.yml"}]


@pytest.mark.parametrize("path, won, damped", [
    ("docker-compose.yml", "orders/docker-compose.yml", 1),
    ("docker-compose.prod.yml", "orders/docker-compose.prod.yml", 2),
])
def test_each_choice_followed_decides(tmp_path, path, won, damped):
    """Followed, either choice decides, and the losing package's every edge is damped with
    `superseded_by` naming the winner's representative - the decided case's record."""
    built = build(tmp_path, {"orders": THREE, "__canonical__": [
        {"service": "web", "owner": "orders", "path": path}]})
    assert not built.kinds("contested-service")
    lost = [e for e in built.deploys if e.get("canonical") is False]
    assert [e["props"]["superseded_by"] for e in lost] == [won] * damped


def test_a_decided_pair_is_named_by_its_base(tmp_path):
    """D15a-8, D15a-14: the pair's representative is its base even where its override sorts
    first by file - `compose.override.yml` before `docker-compose.yml` - so the damped
    edge's `superseded_by` names the base."""
    estate = {"orders": {"docker-compose.yml": WEB, "compose.override.yml": LAYER},
              "deploy": {"docker-compose.yml": compose(svc("web", "../orders", None))}}
    row = contested(build(tmp_path / "a", estate))
    assert row["claims"] == ["deploy/docker-compose.yml:2", "orders/compose.override.yml:2",
                             "orders/docker-compose.yml:2"]
    assert row["choices"] == [{"owner": "deploy"}, {"owner": "orders"}]
    decided = build(tmp_path / "b", {**estate, "__canonical__": [
        {"service": "web", "owner": "orders"}]})
    lost, = [e for e in decided.deploys if e.get("canonical") is False]
    assert lost["props"]["superseded_by"] == "orders/docker-compose.yml"


def test_a_non_parsing_bases_choice_names_its_override_and_decides(tmp_path):
    """D15a-8: a package is named by its base, but a row cites the claims it HAS. A base
    that does not parse has none, so its override stands for the package: the choice names
    the override, and followed, it decides. (One naming the base would decide nothing.)"""
    files = {**THREE, "docker-compose.yml": BROKEN}
    row = contested(build(tmp_path / "a", {"orders": files}))
    pair = {"owner": "orders", "path": "docker-compose.override.yml"}
    assert pair in row["choices"]
    assert row["why"].startswith("2 deployment packages (2 files) claim svc:web@orders,")
    followed = build(tmp_path / "b", {"orders": files,
                                      "__canonical__": [{"service": "web", **pair}]})
    assert not followed.kinds("contested-service")


def test_two_readers_of_a_pair_cite_each_file_once(tmp_path):
    """D15a-37: a file is one file by its normalised WORKSPACE path and line, so two
    components declared over one directory (T615's shape) cite each of the pair's files once,
    by the first reader by name - while two components' `docker-compose.yml` stay two."""
    built = build(tmp_path, {
        "shared": {"docker-compose.yml": WEB, "docker-compose.override.yml": LAYER},
        "deploy": {"docker-compose.yml": compose(svc("web", "../shared", None))},
        "__components__": [("shared", "shared"), ("shared2", "./shared"),
                           ("deploy", "deploy")]})
    row = contested(built)
    assert row["claims"] == ["deploy/docker-compose.yml:2",
                             "shared/docker-compose.override.yml:2",
                             "shared/docker-compose.yml:2"]
    assert row["why"].startswith("2 deployment packages (3 files) claim svc:web@shared,")


REDIS_OVERRIDE = {"docker-compose.override.yml": compose(svc("redis", None, None,
                                                             image="redis:6"))}


@pytest.mark.parametrize("override, claims, counted", [
    (REDIS_OVERRIDE, ["deploy/docker-compose.override.yml:2", "deploy/docker-compose.yml:2"],
     "1 deployment package (2 files)"),
    ({}, ["deploy/docker-compose.yml:2"], "1 deployment package (1 file)"),
], ids=["pair", "base-alone"])
def test_a_name_joined_pair_is_one_package_citing_both_files(tmp_path, override, claims,
                                                             counted):
    """Decision 3 for `join-by-name`: the redis pair joins `svc:redis@redis` as ONE package,
    whose row cites both files and counts them - each count in the singular where it is one
    (D15a-48) - and whose two claims contest nothing."""
    built = build(tmp_path, {
        "redis": {"app/api.py": producer("TOPIC")},
        "deploy": {"docker-compose.yml": compose(svc("redis", None, None, image="redis:7")),
                   **override}})
    row, = built.kinds("name-joined-service")
    assert row["claims"] == claims
    assert row["choices"] == [{"owner": "deploy"}]
    assert row["why"].startswith(f"{counted} names `redis` and nothing more")
    assert not built.kinds("contested-service")


def test_with_no_layout_the_pair_is_still_one_package():
    """D15a-32, D15a-46: an in-process caller that gives no component list still gets the
    pair as one package - keyed on its base's deployment node - with its files cited, and
    counted, by component name."""
    def deploy(rel: str, **props) -> Node:
        return Node(id=ids.deployment_id("orders", rel), type="deploy", props={
            "kind": "compose", "path": rel, "component": "orders", **props})

    def claim(rel: str) -> Edge:
        return Edge(src=ids.deployment_id("orders", rel), type="DG_DEPLOYS",
                    dst="svc:web@orders", source_file=rel, line=2,
                    extractor="compose-services",
                    props={"service": "web", "component": "orders", "mapped_by": "build"})

    web = Node(id="svc:web@orders", type="svc", props={"component": "orders"})
    pair = [deploy("docker-compose.yml"),
            deploy("docker-compose.override.yml", package="docker-compose.yml")]
    ctx = SimpleNamespace(config={})
    assert CanonicalPass().link(ctx, [*pair, web], [
        claim("docker-compose.yml"), claim("docker-compose.override.yml")]).unresolved == []
    row, = CanonicalPass().link(ctx, [*pair, deploy("docker-compose.prod.yml"), web], [
        claim("docker-compose.yml"), claim("docker-compose.override.yml"),
        claim("docker-compose.prod.yml")]).unresolved
    assert row["claims"] == ["orders/docker-compose.override.yml:2",
                             "orders/docker-compose.prod.yml:2", "orders/docker-compose.yml:2"]
    assert row["why"].startswith("2 deployment packages (3 files) claim svc:web@orders,")
