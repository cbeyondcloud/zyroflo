"""Batch 23 (T589; design pass 23, section 3): the image rule PROPOSES and never moves. A deployment
that runs an image one build produces is named by `image-join-proposed`, two builds of it by
`image-ambiguous`, an image only a literal tail can compare by `image-unresolved` (the build's side)
or `image-consumer-unresolved` (the consumer's), each until the consumer's own `[[deploys]]` block
is followed or declined; a whole run that did not read everything compose merges files one
`image-proposals-withheld` row and judges no image; a narrowed run judges none and says so in its
line.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.model import Edge, Node  # noqa: E402
from tools.graph.links.namejoin import NameJoinPass  # noqa: E402
from tools.graph.tests._estate import build, lay_down, read, spawn  # noqa: E402
from tools.graph.tests._listing import allow_listing, deny_listing, lists  # noqa: E402

CODE = {"main.py": "print(1)\n"}
TEMPLATE = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: x\n"
CHART = "charts/web/values.yaml"
BLOCK = re.compile(r'`\[\[deploys\]\] service = "([^"]*)" component = "([^"]*)" '
                   r'onto = "([^"]*)"`')


def chart(service: str = "web", image: str = "org/web", *, name_field: str = "nameOverride",
          extra: str = "") -> dict:
    """A chart in its component's `web/` directory: `<name_field>: <service>`, running `image`."""
    return {"web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n",
            "web/values.yaml": f"{name_field}: {service}\nimage:\n  repository: {image}\n{extra}",
            "web/templates/deployment.yaml": TEMPLATE}


def build_of(image: str = "org/web", source: str = "../orders", service: str = "web") -> dict:
    """A compose file building `service` from `source`, producing `image` (its claim at line 2)."""
    return {"compose.yaml": f"services:\n  {service}:\n    build: {source}\n    image: {image}\n"}


def runner(image: str = "org/web", service: str = "web") -> dict:
    """A deployment-only compose file: it builds nothing, so its claim is a consumer."""
    return {"compose.yaml": f"services:\n  {service}:\n    image: {image}\n"}


def estate(*blocks: dict, **more) -> dict:
    """P1: a chart `web` in `charts` running `org/web`, which `deploy` builds from `orders`."""
    return {"charts": chart(), "deploy": build_of(), "orders": dict(CODE),
            "__deploys__": list(blocks), **more}


def rows(built, kind: str) -> list[dict]:
    return [r for r in built.rows if r.get("kind") == kind]


def image_rows(built) -> list[dict]:
    return [r for r in built.rows if str(r.get("kind", "")).startswith("image-")]


def block_of(row: dict) -> dict:
    """The `[[deploys]]` block a row spells, as a developer pastes it."""
    service, component, onto = BLOCK.search(row["why"]).groups()
    return {"service": service, "component": component, "onto": onto}


P1_WHY = ("`charts/web/values.yaml` runs `docker.io/org/web`, which `deploy/compose.yaml:2` builds "
          "(`svc:web@orders`) - the one build of it the graph read. If it deploys that build, "
          "declare it - `[[deploys]] service = \"web\" component = \"charts\" onto = "
          "\"svc:web@orders\"`, which moves the deployment claims `charts` makes on `web` "
          "(`charts/web/values.yaml`), and none other; if it does not, declare `onto = \"\"` and "
          "this stops - nothing joins by image until declared")


# --- P1 to P6 -------------------------------------------------------------------------------


def test_p1_a_chart_running_the_one_builds_image_is_proposed_and_nothing_moves(tmp_path):
    """D23-3: no declared block at all - the early return wakes for an image."""
    built = build(tmp_path, estate())
    (row,) = image_rows(built)
    assert (row["kind"], row["image"], row["node"]) == (
        "image-join-proposed", "docker.io/org/web", "svc:web@orders")
    assert (row["component"], row["source_file"]) == ("charts", "web/values.yaml")
    assert row["why"] == P1_WHY
    assert built.deploy(CHART)["dst"] == "svc:web@web", "a proposal moves nothing"


def test_p1_followed_the_pasted_block_joins_and_the_row_is_gone(tmp_path):
    first = build(tmp_path / "a", estate())
    (row,) = image_rows(first)
    built = build(tmp_path / "b", estate(block_of(row)))
    edge = built.deploy(CHART)
    assert edge["dst"] == "svc:web@orders"
    assert (edge["props"]["joined_by"], edge["props"]["declared"]) == ("declared", True)
    assert edge["props"]["declared_at"].startswith("workspace.toml:")
    assert image_rows(built) == []


def test_p1_declined_the_row_is_gone_and_nothing_moves(tmp_path):
    built = build(tmp_path, estate({"service": "web", "component": "charts", "onto": ""}))
    assert image_rows(built) == []
    assert built.deploy(CHART)["dst"] == "svc:web@web"


def test_p2_a_chart_named_otherwise_is_proposed_under_its_own_service(tmp_path):
    built = build(tmp_path, estate(charts=chart(service="orders-api")))
    (row,) = rows(built, "image-join-proposed")
    assert block_of(row) == {"service": "orders-api", "component": "charts",
                             "onto": "svc:web@orders"}
    assert built.deploy(CHART)["dst"] == "svc:orders-api@orders-api"
    followed = build(tmp_path / "b", estate(block_of(row), charts=chart(service="orders-api")))
    assert followed.deploy(CHART)["dst"] == "svc:web@orders"
    assert image_rows(followed) == []


def test_p3_a_deployment_only_compose_service_is_proposed(tmp_path):
    built = build(tmp_path, {"runner": runner("org/web:2"), "deploy": build_of(),
                             "orders": dict(CODE)})
    (row,) = image_rows(built)
    assert (row["kind"], row["component"], row["source_file"], row["line"]) == (
        "image-join-proposed", "runner", "compose.yaml", 2)
    assert row["why"].startswith("`runner/compose.yaml` runs `docker.io/org/web`, which "
                                 "`deploy/compose.yaml:2` builds (`svc:web@orders`)")
    followed = build(tmp_path / "b", {"runner": runner("org/web:2"), "deploy": build_of(),
                                      "orders": dict(CODE), "__deploys__": [block_of(row)]})
    assert followed.deploy("runner/compose.yaml")["dst"] == "svc:web@orders"
    assert image_rows(followed) == []


def test_p4_two_builds_of_the_image_are_ambiguous(tmp_path):
    two = {"deploy2": build_of(source="../billing"), "billing": dict(CODE)}
    built = build(tmp_path, estate(**two))
    (row,) = image_rows(built)
    assert (row["kind"], row["image"], row["targets"]) == (
        "image-ambiguous", "docker.io/org/web", ["svc:web@billing", "svc:web@orders"])
    assert row["why"] == (
        "`charts/web/values.yaml` runs `docker.io/org/web`, which 2 builds the graph read produce "
        "(`svc:web@billing`, `svc:web@orders`); declare which it deploys - `[[deploys]] service = "
        "\"web\" component = \"charts\" onto = \"<one of them>\"` - or `onto = \"\"` if none")
    assert built.deploy(CHART)["dst"] == "svc:web@web"
    chosen = build(tmp_path / "b", estate({**block_of(row), "onto": "svc:web@billing"}, **two))
    assert chosen.deploy(CHART)["dst"] == "svc:web@billing" and image_rows(chosen) == []
    declined = build(tmp_path / "c", estate({**block_of(row), "onto": ""}, **two))
    assert image_rows(declined) == []


def test_p5_an_interpolated_build_image_is_unresolved_and_its_way_on_is_followed(tmp_path):
    """D23-12: compared by its literal tail alone - `runner`'s `nginx` does not end with it - and
    a consumer it names has no proposal."""
    built = build(tmp_path, estate(deploy=build_of("${REGISTRY}/org/web:1.0"),
                                   runner=runner("nginx")))
    (row,) = image_rows(built)
    assert (row["kind"], row["component"], row["source_file"], row["line"]) == (
        "image-unresolved", "deploy", "compose.yaml", 2)
    assert (row["image"], row["consumers"]) == ("${REGISTRY}/org/web:1.0", [CHART])
    assert row["why"] == ("`deploy/compose.yaml:2` builds `${REGISTRY}/org/web:1.0`, which may be "
                          "the image `charts/web/values.yaml` runs - the graph cannot read it; "
                          "write the repository without interpolation, or declare the join")
    literal = build(tmp_path / "b", estate(deploy=build_of("org/web:1.0")))
    assert [r["kind"] for r in image_rows(literal)] == ["image-join-proposed"]
    declared = build(tmp_path / "c", estate({"service": "web", "component": "charts",
                                             "onto": "svc:web@orders"},
                                            deploy=build_of("${REGISTRY}/org/web:1.0")))
    assert image_rows(declared) == [], "D23-28: gone once every consumer it names declares"


def test_a_consumer_an_unresolved_build_names_is_never_proposed_onto_a_keyed_build_alone(tmp_path):
    """D23-12: `image-unresolved` stands whether or not another build has the key, and the
    consumer it names may run either build."""
    built = build(tmp_path, estate(d2=build_of("${REGISTRY}/org/web:1.0", "../billing"),
                                   billing=dict(CODE)))
    assert [r["kind"] for r in image_rows(built)] == ["image-unresolved"]


def test_an_unresolved_build_is_named_once_per_package(tmp_path):
    """Compose's default pair is one package: both claims carry its one merged image."""
    deploy = {"docker-compose.yml": "services:\n  web:\n    build: ../orders\n"
                                    "    image: ${REGISTRY}/org/web:1.0\n",
              "docker-compose.override.yml": "services:\n  web:\n    environment:\n"
                                             "      - A=1\n"}
    built = build(tmp_path, estate(deploy=deploy))
    (row,) = image_rows(built)
    assert (row["kind"], row["source_file"]) == ("image-unresolved", "docker-compose.yml")


def test_each_build_in_a_file_is_compared_on_its_own(tmp_path):
    """Zero silent drops: one file building two services is one package but two builds - the
    second build's image is compared, and named, though the first's is not the consumer's."""
    deploy = {"compose.yaml": "services:\n  api:\n    build: ../orders\n"
                              "    image: ${REGISTRY}/org/api:1\n  web:\n    build: ../orders\n"
                              "    image: ${REGISTRY}/org/web:1\n"}
    built = build(tmp_path, estate(deploy=deploy))
    (row,) = image_rows(built)
    assert (row["kind"], row["line"], row["image"]) == (
        "image-unresolved", 5, "${REGISTRY}/org/web:1")
    two = {"compose.yaml": "services:\n  web:\n    build: ../orders\n    image: org/web\n"
                           "  web2:\n    build: ../orders\n    image: org/web\n"}
    consumer = build(tmp_path / "b", {"runner": runner("${REGISTRY}/org/web:1"), "deploy": two,
                                      "orders": dict(CODE)})
    (row,) = image_rows(consumer)
    assert row["builds"] == ["deploy/compose.yaml:2", "deploy/compose.yaml:5"]


def test_p6_a_charts_registry_is_compared_with_the_builds_host(tmp_path):
    built = build(tmp_path, estate(charts=chart(extra="  registry: ghcr.io\n"),
                                   deploy=build_of("ghcr.io/org/web")))
    (row,) = image_rows(built)
    assert (row["kind"], row["image"], row["node"]) == (
        "image-join-proposed", "ghcr.io/org/web", "svc:web@orders")


# --- where the consumer ends ----------------------------------------------------------------


def test_a_consumer_the_name_rule_joins_elsewhere_is_proposed_saying_where(tmp_path):
    """D23-21: the name rule joins `runner`'s `web` onto `svc:web@web`, a component's node."""
    built = build(tmp_path, {"runner": runner(), "web": dict(CODE), "deploy": build_of(),
                             "orders": dict(CODE)})
    (row,) = image_rows(built)
    assert row["kind"] == "image-join-proposed"
    assert row["why"].endswith("nothing joins by image until declared. The name rule joins it "
                               "onto `svc:web@web` today")
    assert built.deploy("runner/compose.yaml")["dst"] == "svc:web@web"


def test_a_consumer_its_apart_block_holds_off_the_name_rule_is_not_said_to_be_joined(tmp_path):
    """The name rule skips a claim an `[[apart]]` block refuses, so it ends where it sits."""
    built = build(tmp_path, {"runner": runner(), "web": dict(CODE), "deploy": build_of(),
                             "orders": dict(CODE),
                             "__apart__": [{"service": "web", "component": "runner"}]})
    (row,) = rows(built, "image-join-proposed")
    assert "The name rule joins it" not in row["why"]
    assert built.deploy("runner/compose.yaml")["dst"] == "svc:web@runner"


def test_a_consumer_the_name_rule_joins_onto_the_producers_node_is_not_proposed(tmp_path):
    """D23-21: the build of `org/web` sits on `svc:web@web`, where the name rule joins it."""
    built = build(tmp_path, {"runner": runner(),
                             "web": {**CODE, **build_of(source=".")}})
    assert built.deploy("runner/compose.yaml")["dst"] == "svc:web@web"
    assert image_rows(built) == []


def test_the_proposal_names_what_the_block_leaves_and_what_it_unjoins(tmp_path):
    """D23-22, D23-29a, D23-81, D23-88: the stock service of a building component in the pair
    stays where it is; `runner`'s name-only claim, joined onto the chart's node through it,
    is left on its own."""
    charts = {**chart(), **CODE,
              "compose.yaml": "services:\n  web:\n    image: org/web\n  api:\n    build: .\n"}
    built = build(tmp_path, estate(charts=charts, runner=runner("nginx")))
    (row,) = rows(built, "image-join-proposed")
    assert row["source_file"] == "web/values.yaml"
    assert ("which moves the deployment claims `charts` makes on `web` (`charts/web/values.yaml`), "
            "and none other - `charts/compose.yaml`, which the image link does not join (a build, "
            "a building component's own service), stays where it is, and leaves "
            "`runner/compose.yaml`, joined by name onto `svc:web@web` through it today, on its "
            "own; if it does not") in row["why"]
    assert built.deploy("runner/compose.yaml")["dst"] == "svc:web@web"


def test_no_claim_is_said_to_be_left_where_the_name_rule_still_joins_it(tmp_path):
    """D23-22: a component named like the service still joins `runner`'s claim by meeting point
    (b) once the chart moves, and another chart on the node still declares it."""
    named = build(tmp_path / "a", estate(runner=runner("nginx"), web=dict(CODE)))
    (row,) = rows(named, "image-join-proposed")
    assert "joined by name onto" not in row["why"]
    other = build(tmp_path / "b", estate(runner=runner("nginx"),
                                         charts2=chart(image="org/other")))
    (row,) = [r for r in rows(other, "image-join-proposed") if r["component"] == "charts"]
    assert "joined by name onto" not in row["why"]


def test_a_chart_declared_apart_from_the_component_named_like_it_is_not_proposed_onto_it(tmp_path):
    """D23-66: the block says the chart is not `svc:web@web`'s, so its only build there is no
    producer of it."""
    built = build(tmp_path, {"charts": chart(), "web": {**CODE, **build_of(source=".")},
                             "__apart__": [{"service": "web", "component": "charts"}]})
    assert rows(built, "image-join-proposed") == []


def test_a_chart_declared_apart_is_proposed_onto_another_components_build(tmp_path):
    """D23-36, D23-50: the refusal agrees with a join onto a build's node."""
    built = build(tmp_path, estate(web=dict(CODE),
                                   __apart__=[{"service": "web", "component": "charts"}]))
    (row,) = rows(built, "image-join-proposed")
    assert row["node"] == "svc:web@orders"


def test_a_building_components_image_only_service_is_never_proposed(tmp_path):
    """Ruling 13."""
    built = build(tmp_path, {"api": {**CODE, "compose.yaml": "services:\n  api:\n    build: .\n"
                                                              "  web:\n    image: org/web\n"},
                             "deploy": build_of(), "orders": dict(CODE)})
    assert image_rows(built) == []


def test_an_unplaced_builds_node_is_proposed(tmp_path):
    """D23-60."""
    built = build(tmp_path, {"charts": chart(), "deploy": build_of(source="../nowhere")})
    (row,) = rows(built, "image-join-proposed")
    assert row["node"] == "svc:web@deploy"


# --- the producers --------------------------------------------------------------------------


def test_a_build_taking_its_image_from_a_base_cites_where_it_is_written(tmp_path):
    """D23-76, D23-86, D23-96."""
    deploy = {"compose.yaml": "services:\n  base:\n    image: org/web\n  web:\n"
                              "    build: ../orders\n    extends: base\n"}
    built = build(tmp_path, estate(deploy=deploy))
    (row,) = rows(built, "image-join-proposed")
    assert ("which `deploy/compose.yaml:4` builds (`svc:web@orders`), its `image:` written at "
            "`deploy/compose.yaml:3` - the one build of it the graph read") in row["why"]


def test_the_ambiguity_cites_where_a_base_writes_a_builds_image(tmp_path):
    deploy2 = {"compose.yaml": "services:\n  base:\n    image: org/web\n  web:\n"
                               "    build: ../billing\n    extends: base\n"}
    built = build(tmp_path, estate(deploy2=deploy2, billing=dict(CODE)))
    (row,) = rows(built, "image-ambiguous")
    assert ("(`svc:web@billing`, `svc:web@orders`; `svc:web@billing`'s `image:` written at "
            "`deploy2/compose.yaml:3`)") in row["why"]


def test_the_default_pair_is_one_producer_and_imageless_builds_are_none(tmp_path):
    """D23-65: three `build:`-only services produce nothing a deployment runs by name."""
    deploy = {"docker-compose.yml": "services:\n  web:\n    build: ../orders\n    image: org/web\n"
                                    "  a:\n    build: ../orders\n  b:\n    build: ../orders\n"
                                    "  c:\n    build: ../orders\n",
              "docker-compose.override.yml": "services:\n  web:\n    environment:\n"
                                             "      - A=1\n"}
    built = build(tmp_path, estate(deploy=deploy))
    (row,) = image_rows(built)
    assert row["kind"] == "image-join-proposed"
    assert "which `deploy/docker-compose.yml:2` builds (`svc:web@orders`) - the one" in row["why"]
    assert "cannot read" not in row["why"]


def test_unreadable_producers_are_named_one_per_package_and_capped(tmp_path):
    """D23-20, D23-42, D23-52: `${IMAGE}` has no key and no tail - it may be any image, so every
    proposal names it; a default pair is one package; an empty tail names no consumer."""
    more = {f"d{n}": build_of("${IMAGE}", service=f"s{n}") for n in range(2, 7)}
    more["d1"] = {"docker-compose.yml": "services:\n  s1:\n    build: ../orders\n"
                                        "    image: ${IMAGE}\n",
                  "docker-compose.override.yml": "services:\n  s1:\n    environment:\n"
                                                 "      - A=1\n"}
    built = build(tmp_path, estate(**more))
    (row,) = image_rows(built)
    assert row["kind"] == "image-join-proposed"
    assert ("the one build of it the graph read; `d1/docker-compose.yml`, `d2/compose.yaml`, "
            "`d3/compose.yaml`, `d4/compose.yaml`, `d5/compose.yaml` and 1 more build images the "
            "graph cannot read, which may be it too. If it deploys") in row["why"]


def test_one_unreadable_producer_is_named_in_the_singular(tmp_path):
    built = build(tmp_path, estate(d1=build_of("${IMAGE}", service="s1")))
    (row,) = image_rows(built)
    assert ("; `d1/compose.yaml` builds an image the graph cannot read, which may be it "
            "too.") in row["why"]


def test_refused_chains_hide_no_build_and_withhold_nothing(tmp_path):
    """D23-119, D23-129, D23-130, D23-132: a cycle and an undeclared base, on the keyed build
    and beside it, and a deployment-only consumer whose chain compose refuses - each judged on
    its own facts."""
    deploy = {"compose.yaml": "services:\n  web:\n    build: ../orders\n    image: org/web\n"
                              "    extends: ghost\n  cyc:\n    image: x/cyc\n    extends: cyc\n"}
    built = build(tmp_path, estate(deploy=deploy, runner={
        "compose.yaml": "services:\n  job:\n    image: org/web\n    extends: nobody\n"}))
    assert not rows(built, "image-proposals-withheld")
    proposed = sorted(r["source_file"] for r in rows(built, "image-join-proposed"))
    assert proposed == ["compose.yaml", "web/values.yaml"], built.rows


# --- the consumer's side --------------------------------------------------------------------


def test_an_interpolated_consumer_image_is_unresolved_on_its_own_side(tmp_path):
    """D23-35: `deploy2`'s `org/other` does not end with its tail, so it is not named."""
    built = build(tmp_path, {"runner": runner("${REGISTRY}/org/web:1"), "deploy": build_of(),
                             "orders": dict(CODE),
                             "deploy2": build_of("org/other", "../billing", "other"),
                             "billing": dict(CODE)})
    (row,) = image_rows(built)
    assert (row["kind"], row["component"], row["source_file"], row["line"]) == (
        "image-consumer-unresolved", "runner", "compose.yaml", 2)
    assert (row["image"], row["builds"]) == ("${REGISTRY}/org/web:1", ["deploy/compose.yaml:2"])
    assert row["why"] == ("`runner/compose.yaml` runs `${REGISTRY}/org/web:1`, which may be the "
                          "image `deploy/compose.yaml:2` builds - the graph cannot read it; write "
                          "the repository without interpolation, or declare the join")
    declared = build(tmp_path / "b", {"runner": runner("${REGISTRY}/org/web:1"),
                                      "deploy": build_of(), "orders": dict(CODE),
                                      "__deploys__": [{"service": "web", "component": "runner",
                                                       "onto": "svc:web@orders"}]})
    assert image_rows(declared) == [], "D23-45: gone once its consumer declares"


def test_a_charts_templated_registry_path_is_unresolved_and_a_templated_tag_is_keyed(tmp_path):
    """D23-55: the span is published as the token, shown as written."""
    built = build(tmp_path, estate(charts=chart(
        image="{{ .Values.global.registry }}/org/web", name_field="fullnameOverride")))
    (row,) = image_rows(built)
    assert (row["kind"], row["image"]) == ("image-consumer-unresolved", "{{ ... }}/org/web")
    assert row["why"].startswith("`charts/web/values.yaml` runs `{{ ... }}/org/web`, which may")
    tag = build(tmp_path / "b", estate(charts=chart(extra="  tag: {{ .Chart.AppVersion }}\n")))
    assert [r["kind"] for r in image_rows(tag)] == ["image-join-proposed"]


def test_as_written_shows_a_registry_and_a_template_span_as_the_files_write_them():
    from tools.graph.extractors._image import as_written
    assert as_written("org/web", "ghcr.io") == "ghcr.io/org/web"
    assert as_written("quay.io/org/web", "ghcr.io") == "quay.io/org/web", "never over its own host"
    assert as_written("org/web", "__TPL__") == "{{ ... }}/org/web"
    assert as_written("${REGISTRY}/org/web:1") == "${REGISTRY}/org/web:1"


def test_image_unresolved_names_each_consumer_until_its_block_is_followed(tmp_path):
    """D23-28, D23-40, D23-49: a contradicted block leaves its consumer named; a followed one
    clears it."""
    interpolated = {"deploy": build_of("${REGISTRY}/org/web:1.0"), "runner": runner()}
    both = build(tmp_path / "a", estate(**interpolated))
    (row,) = rows(both, "image-unresolved")
    assert row["consumers"] == [CHART, "runner/compose.yaml"]
    one = build(tmp_path / "b", estate({"service": "web", "component": "charts",
                                        "onto": "svc:web@orders"}, **interpolated))
    (row,) = rows(one, "image-unresolved")
    assert row["consumers"] == ["runner/compose.yaml"]
    other = {"deploy2": build_of("org/other", "../billing", "other"), "billing": dict(CODE)}
    contradicted = build(tmp_path / "c", estate({"service": "web", "component": "charts",
                                                 "onto": "svc:other@billing"},
                                                **interpolated, **other))
    assert [r["kind"] for r in contradicted.rows
            if r["kind"].startswith("deploys-")] == ["deploys-contradicted"]
    (row,) = rows(contradicted, "image-unresolved")
    assert row["consumers"] == [CHART, "runner/compose.yaml"]


# --- runs that cannot judge an image --------------------------------------------------------


def test_a_narrowed_run_judges_no_image(tmp_path):
    """D23-1, D23-73: `--components` may hide the second build; nothing is proposed, nothing
    named unreadable, and no row says so - the run's line does."""
    for where, shape, named in (
            ("p1", estate(misc=dict(CODE)), "charts,deploy,orders"),
            ("p5", estate(misc=dict(CODE), deploy=build_of("${REGISTRY}/org/web:1.0")),
             "charts,deploy,orders"),
            ("withheld", estate(misc=dict(CODE), broken={"compose.yaml": "services: [\n"}),
             "charts,deploy,orders,broken")):
        built = build(tmp_path / where, shape, "--components", named)
        assert built.code == 0, where
        assert image_rows(built) == [], where


def test_a_load_missing_a_shard_judges_no_image(tmp_path):
    """D23-1: a load is narrowed by the shard it lacks."""
    assert build(tmp_path, estate(misc=dict(CODE)), "--publish-snapshot").code == 0
    shutil.rmtree(tmp_path / "ws" / "misc" / ".sdlc")
    loaded = build(tmp_path, None, "--from-snapshot")
    assert loaded.code == 0
    assert image_rows(loaded) == []


WITHHELD_TAIL = (", so which builds produce which images is not known until it does: no image join "
                 "is proposed, no image named unreadable, and no `[[deploys]]` block refused for "
                 "its images - one is followed only where they agree over what it read, or cannot "
                 "be compared")
WITHHELD_WAY = (". Fix each as its own row says - a file an `include:` names by making the "
                "`include:` name a compose file this component's reader reads - and rebuild")
WITHHELD_LEAD = ("the graph did not read, as each component's own, everything compose merges into "
                 "the services it holds")


def test_a_compose_file_lost_to_its_case_withholds_every_image_judgement(tmp_path):
    """D23-2, D23-18, D23-31: in both spellings of a case loss; one row, against its cause."""
    for n, name in enumerate(("Docker-Compose.yml", "docker-compose.YML")):
        lost = {"deploy2": {name: "services:\n  web:\n    build: ../billing\n"
                                  "    image: org/web\n"}, "billing": dict(CODE)}
        # One directory per cell, never named by the file: two names one case apart are one
        # directory on a filesystem that folds case.
        built = build(tmp_path / f"cell{n}", estate(**lost))
        (row,) = image_rows(built)
        assert row["kind"] == "image-proposals-withheld", name
        assert (row["component"], row["source_file"], row["line"]) == ("deploy2", name, 0)
        assert (row["paths"], row["services"], row["blocks"]) == ([f"deploy2/{name}"], [], [])
        assert row["why"] == (WITHHELD_LEAD + f" - `deploy2/{name}` (named off its case)"
                              + WITHHELD_TAIL + WITHHELD_WAY)


def test_an_unparsable_compose_file_withholds_and_an_unread_chart_does_not(tmp_path):
    """D23-2, D23-56: a chart builds nothing, so a chart the run did not read hides no build."""
    broken = build(tmp_path / "a", estate(broken={"compose.yaml": "services: [\n"}))
    (row,) = image_rows(broken)
    assert (row["kind"], row["paths"]) == ("image-proposals-withheld", ["broken/compose.yaml"])
    assert "`broken/compose.yaml` (could not be read or parsed)" in row["why"]
    chart_lost = build(tmp_path / "b", estate(other={
        "web/Chart.yaml": "apiVersion: v2\nname: other\nversion: 0.1.0\n",
        "web/values.yaml": "image: [\n", "web/templates/deployment.yaml": TEMPLATE}))
    assert [r["kind"] for r in image_rows(chart_lost)] == ["image-join-proposed"]


def test_an_include_naming_a_file_compose_does_not_read_withholds_citing_the_entry(tmp_path):
    """D23-131, D23-156, D23-163: the included `stack.yml` holds the only build."""
    deploy = {"compose.yaml": "include:\n  - stack.yml\nservices:\n  side:\n    image: x/side\n",
              "stack.yml": "services:\n  web:\n    build: ../orders\n    image: org/web\n"}
    built = build(tmp_path, estate(deploy=deploy))
    (row,) = image_rows(built)
    assert row["kind"] == "image-proposals-withheld"
    assert (row["component"], row["source_file"], row["line"]) == ("deploy", "compose.yaml", 2)
    assert row["paths"] == ["deploy/stack.yml"]
    assert "`deploy/stack.yml` (named by an `include:` it does not read)" in row["why"]


def test_a_chain_whose_base_the_graph_cannot_reach_withholds_naming_it(tmp_path):
    """D23-34, D23-112, D23-113, D23-120: an unread file and an unreachable chain, each named by
    its cause, the row against the first cause in its own order."""
    deploy = {"compose.yaml": "services:\n  web:\n    build: ../orders\n    image: org/web\n"
                              "  job:\n    extends:\n      file: base.yml\n      service: job\n",
              "base.yml": "services:\n  job:\n    build: ../orders\n    image: org/web\n"}
    chain = build(tmp_path / "a", estate(deploy=deploy))
    (row,) = image_rows(chain)
    assert row["kind"] == "image-proposals-withheld"
    assert (row["component"], row["source_file"], row["line"]) == ("deploy", "compose.yaml", 5)
    assert (row["paths"], row["services"]) == ([], ["job of deploy/compose.yaml"])
    assert row["why"].startswith(WITHHELD_LEAD + "; `job` of `deploy/compose.yaml` (`job in "
                                 "base.yml` - its `file:` names `base.yml`, which is not named "
                                 "as a compose file (")
    assert row["why"].endswith(") extends a base it cannot reach" + WITHHELD_TAIL + WITHHELD_WAY)
    both = build(tmp_path / "b", estate(deploy=deploy, broken={"compose.yaml": "services: [\n"}))
    (row,) = image_rows(both)
    assert (row["component"], row["source_file"], row["line"]) == ("broken", "compose.yaml", 0)
    assert (row["paths"], row["services"]) == (["broken/compose.yaml"],
                                               ["job of deploy/compose.yaml"])
    assert (WITHHELD_LEAD + " - `broken/compose.yaml` (could not be read or parsed); `job` of "
            "`deploy/compose.yaml` (") in row["why"]
    late = build(tmp_path / "c", estate(deploy=deploy, zz={"compose.yaml": "services: [\n"}))
    (row,) = image_rows(late)
    assert row["paths"] == ["zz/compose.yaml"]
    assert (row["component"], row["source_file"], row["line"]) == ("deploy", "compose.yaml", 5), \
        "the first cause by component, though its words list the paths first"


def test_a_directory_the_walk_could_not_list_withholds_naming_it(tmp_path):
    """D23-32: cited as `unlisted-directory` cites one - the directory, line 0."""
    lay_down(tmp_path, estate(**{"deploy": {**build_of(), "sub/x.txt": "x\n"}}))
    guarded = tmp_path / "ws" / "deploy" / "sub"
    deny_listing(guarded)
    try:
        binds = not lists(guarded)
        done = spawn(tmp_path)
    finally:
        allow_listing(guarded)
    assert done.returncode == 0, done.stdout + done.stderr
    built = read(done.returncode, tmp_path / "out")
    if binds:
        (row,) = image_rows(built)
        assert row["kind"] == "image-proposals-withheld"
        assert (row["component"], row["source_file"], row["line"]) == ("deploy", "sub", 0)
        assert "`deploy/sub` (could not be listed)" in row["why"]
    else:
        assert [r["kind"] for r in image_rows(built)] == ["image-join-proposed"]


def test_the_withheld_row_names_the_blocks_it_left(tmp_path):
    """D23-122: a block whose images a run that read less cannot judge is left, and named."""
    built = build(tmp_path, estate({"service": "web", "component": "charts",
                                    "onto": "svc:web@orders"},
                                   charts=chart(image="ghcr.io/org/web"),
                                   broken={"compose.yaml": "services: [\n"}))
    (row,) = image_rows(built)
    (at,) = row["blocks"]
    assert re.fullmatch(r"workspace\.toml:\d+", at), at
    assert row["why"].endswith(WITHHELD_TAIL + f", and the `[[deploys]]` block at `{at}`, whose "
                               "images it could not judge, is left unfollowed until then"
                               + WITHHELD_WAY)
    assert built.deploy(CHART)["dst"] == "svc:web@web"


def test_nothing_is_withheld_where_no_deployment_runs_an_image(tmp_path):
    """Ruling: with no consumer whose image has a key or a tail there is no judgement to
    withhold - an unread compose file beside a build alone files no row."""
    built = build(tmp_path, {"deploy": build_of(), "orders": dict(CODE),
                             "broken": {"compose.yaml": "services: [\n"}})
    assert image_rows(built) == []
    imageless = {"web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n",
                 "web/values.yaml": "nameOverride: web\n",
                 "web/templates/deployment.yaml": TEMPLATE}
    charted = build(tmp_path / "b", {"charts": imageless, "deploy": build_of(),
                                     "orders": dict(CODE),
                                     "broken": {"compose.yaml": "services: [\n"}})
    assert charted.deploy(CHART)["dst"] == "svc:web@web", "a consumer, running no image"
    assert image_rows(charted) == []


def test_the_withheld_rows_paths_are_capped_in_its_words_and_whole_in_its_field(tmp_path):
    more = {f"b{n}": {"compose.yaml": "services: [\n"} for n in range(1, 8)}
    built = build(tmp_path, estate(**more))
    (row,) = image_rows(built)
    assert row["paths"] == [f"b{n}/compose.yaml" for n in range(1, 8)]
    assert "`b5/compose.yaml` (could not be read or parsed) and 2 more, so which" in row["why"]
    assert row["component"] == "b1"


# --- the pass alone -------------------------------------------------------------------------


class Ctx:
    def __init__(self, config: dict):
        self.config = config


def test_the_pass_wakes_for_an_image_with_no_mark_and_no_block():
    """D23-41: the early-return pin the note says does not exist - a chart and a build, nothing
    marked, no `[[apart]]`, no `[[deploys]]`."""
    nodes = [Node(id="deploy:charts/web/values.yaml", type="deploy",
                  props={"kind": "helm", "component": "charts"}),
             Node(id="deploy:deploy/compose.yaml", type="deploy",
                  props={"kind": "compose", "component": "deploy", "path": "compose.yaml",
                         "deployment_only": False})]
    edges = [Edge(src=nodes[0].id, type="DG_DEPLOYS", dst="svc:web@web",
                  source_file="web/values.yaml", line=1, extractor="helm-deployments",
                  props={"service": "web", "image": "org/web"}),
             Edge(src=nodes[1].id, type="DG_DEPLOYS", dst="svc:web@orders",
                  source_file="compose.yaml", line=2, extractor="compose-services",
                  props={"service": "web", "mapped_by": "build", "image": "org/web"})]
    res = NameJoinPass().link(Ctx({"narrowed": False, "components": [
        {"name": "charts", "path": "charts"}, {"name": "deploy", "path": "deploy"},
        {"name": "orders", "path": "orders"}]}), nodes, edges)
    assert [r["kind"] for r in res.unresolved] == ["image-join-proposed"]
