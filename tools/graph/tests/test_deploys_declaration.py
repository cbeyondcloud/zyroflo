"""Batch 23 (T589; design pass 23, section 4): `[[deploys]]` - a join declared. Each block judged in
its order, each claim gated one by one, the joined consumer moved onto `onto`, and every state that
does not join said in its own row.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build  # noqa: E402

CODE = {"main.py": "print(1)\n"}
TEMPLATE = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: x\n"


def chart(service: str = "web", image: str = "org/web") -> dict:
    return {"charts/web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n",
            "charts/web/values.yaml": f"nameOverride: {service}\nimage:\n  repository: {image}\n",
            "charts/web/templates/deployment.yaml": TEMPLATE}


def estate(*blocks: dict, build_image: str = "org/web", chart_image: str = "org/web",
           **more) -> dict:
    """P1: a chart `web` in `charts` running `chart_image`, and a compose build of `web` in
    `deploy` from `orders` producing `build_image`."""
    return {"charts": chart(image=chart_image),
            "deploy": {"compose.yaml": f"services:\n  web:\n    build: ../orders\n"
                                       f"    image: {build_image}\n"},
            "orders": dict(CODE), "__deploys__": list(blocks), **more}


def join(onto: str = "svc:web@orders", **extra) -> dict:
    return {"service": "web", "component": "charts", "onto": onto, **extra}


def chart_claim(built) -> dict:
    return built.deploy("charts/charts/web/values.yaml")


def deploys_rows(built) -> list[dict]:
    return [r for r in built.rows if str(r.get("kind", "")).startswith("deploys-")]


def test_a_declared_join_moves_the_chart_onto_the_builds_node(tmp_path):
    """P1 followed: the claim on `onto`, saying how it got there, and nothing left behind."""
    built = build(tmp_path, estate(join()))
    edge = chart_claim(built)
    props = edge["props"]
    assert edge["dst"] == "svc:web@orders"
    assert (props["mapped_by"], props["joined_by"], props["declared"], props["component"],
            props["extracted_from"]) == ("image", "declared", True, "orders", "svc:web@web")
    assert props["declared_at"].startswith("workspace.toml:")
    assert edge["provenance"] == "INFERRED"
    assert "svc:web@web" not in built.svc_ids(), "no edge-less node left behind (D23-8)"
    assert deploys_rows(built) == []


def test_a_declining_block_moves_nothing_and_says_nothing(tmp_path):
    built = build(tmp_path, estate(join(onto="")))
    assert chart_claim(built)["dst"] == "svc:web@web"
    assert deploys_rows(built) == []


def test_a_pair_equal_once_normalised_is_a_duplicate_the_first_deciding(tmp_path):
    """D23-5, D15b-56: never withheld."""
    built = build(tmp_path, estate({"service": "my api", "component": "charts", "onto": ""},
                                   {"service": "my-api", "component": "charts",
                                    "onto": "svc:web@orders"}))
    (row,) = [r for r in deploys_rows(built) if r["kind"] == "deploys-duplicate"]
    assert (row["service"], row["ignored"]) == ("my-api", "my-api"), row
    assert "block #1" in row["why"] and "decides nothing" in row["why"]


def test_a_join_onto_the_node_an_apart_refuses_is_refused_and_any_other_is_followed(tmp_path):
    """D23-25, D23-36, D23-50: the refusal wins over a join onto its node; a join beside it
    onto a build's node agrees with it (decision 54)."""
    apart = {"__apart__": [{"service": "web", "component": "charts"}]}
    refused = build(tmp_path / "a", estate(join(onto="svc:web@web"), **apart))
    (row,) = deploys_rows(refused)
    assert row["kind"] == "deploys-apart" and "the refusal wins" in row["why"]
    followed = build(tmp_path / "b", estate(join(), **apart))
    assert chart_claim(followed)["dst"] == "svc:web@orders"
    assert "apart" not in chart_claim(followed)["props"], "the move drops what the refusal set"


def test_a_block_whose_pair_the_run_holds_no_claim_of_is_unmatched_on_its_claim_side(tmp_path):
    """D23-44, D23-89, D23-98: worded by the joining framing - what it read, by each claim's own
    service, and the block's own way on."""
    built = build(tmp_path, estate({"service": "nope", "component": "charts",
                                    "onto": "svc:web@orders"}))
    (row,) = deploys_rows(built)
    assert (row["kind"], row["side"], row["service"]) == ("deploys-unmatched", "claim", "nope")
    assert "It read: `web`. Correct the block's `service` or `component`, or remove it" in row["why"]


def test_an_onto_no_build_of_the_run_is_on_is_unmatched_on_its_onto_side(tmp_path):
    """D23-47, D23-59: an undiscovered component, or a component written unnormalised -
    compared as an id, never parsed."""
    for onto in ("svc:web@nowhere", "svc:web@My Comp"):
        built = build(tmp_path / onto.replace(" ", "_").replace(":", "_").replace("@", "_"),
                      estate(join(onto=onto)))
        (row,) = deploys_rows(built)
        assert (row["kind"], row["side"]) == ("deploys-unmatched", "onto")
        assert row["why"].startswith(f"names `{onto}`, which no build the graph read is on")
        assert chart_claim(built)["dst"] == "svc:web@web"


def test_a_block_naming_only_a_build_claim_names_a_service_that_is_no_consumer(tmp_path):
    """D23-14, D23-23: a build claim is known not to be a deployment the image link joins."""
    built = build(tmp_path, estate({"service": "web", "component": "deploy",
                                    "onto": "svc:web@orders"}))
    (row,) = deploys_rows(built)
    assert row["kind"] == "deploys-not-a-consumer" and "a build" in row["why"]


def test_images_the_files_contradict_are_not_followed_in_a_complete_run(tmp_path):
    """D23-19, decision 52: the chart runs `ghcr.io/org/web`, the build produces `org/web`."""
    built = build(tmp_path, estate(join(), chart_image="ghcr.io/org/web"))
    (row,) = deploys_rows(built)
    assert (row["kind"], row["node"]) == ("deploys-contradicted", "svc:web@orders")
    assert "joins two images, so it is not followed" in row["why"]
    assert chart_claim(built)["dst"] == "svc:web@web"


def test_a_build_image_that_cannot_be_compared_is_followed(tmp_path):
    """D23-19: interpolated whole, the build's image has no key - the declaration states what
    the files cannot show."""
    built = build(tmp_path, estate(join(), build_image="${IMAGE}"))
    assert chart_claim(built)["dst"] == "svc:web@orders"
    assert deploys_rows(built) == []


def test_a_claim_whose_component_compose_did_not_read_whole_is_unconfirmed(tmp_path):
    """D23-61, D23-150: never read as not-a-consumer - a fact about this run's reading."""
    built = build(tmp_path, {
        "runner": {"compose.yaml": "services:\n  web:\n    image: org/web\n",
                   "compose.prod.yml": "services: [\n"},
        "deploy": {"compose.yaml": "services:\n  web:\n    build: ../orders\n"
                                   "    image: org/web\n"},
        "orders": dict(CODE),
        "__deploys__": [{"service": "web", "component": "runner", "onto": "svc:web@orders"}]})
    (row,) = deploys_rows(built)
    assert row["kind"] == "deploys-unconfirmed"
    assert "`compose.prod.yml` was not read" in row["why"]
    assert "fix them as their own rows say" in row["why"]
    assert built.deploy("runner/compose.yaml")["dst"] != "svc:web@orders"


def test_a_block_naming_a_component_no_reader_read_is_unconfirmed_never_silent(tmp_path):
    """D23-78: no deployment file at all."""
    built = build(tmp_path, estate({"service": "web", "component": "orders",
                                    "onto": "svc:web@orders"}))
    (row,) = deploys_rows(built)
    assert row["kind"] == "deploys-unconfirmed"
    assert "no deployment file of `orders` was read" in row["why"]
    assert "correct the block's `component`, or add the deployment file it means" in row["why"]


def test_a_declared_compose_consumer_is_never_name_joined_again(tmp_path):
    """D23-4, D23-16, D23-27: beside a component named like its service, the declared claim
    stays where the block put it, with no name-joined-service row, and records where
    extraction placed it."""
    built = build(tmp_path, {
        "runner": {"compose.yaml": "services:\n  web:\n    image: org/web\n"},
        "web": dict(CODE),
        "deploy": {"compose.yaml": "services:\n  web:\n    build: ../orders\n"
                                   "    image: org/web\n"},
        "orders": dict(CODE),
        "__deploys__": [{"service": "web", "component": "runner", "onto": "svc:web@orders"}]})
    edge = built.deploy("runner/compose.yaml")
    assert edge["dst"] == "svc:web@orders"
    assert edge["props"]["extracted_from"] == "svc:web@runner"
    assert not built.kinds("name-joined-service")


def test_a_pair_holding_a_consumer_and_a_build_moves_the_consumer_alone(tmp_path):
    """D23-37, D23-61, D23-71: a narrowed run too - no row beside the move."""
    charts = {**chart(), "compose.yaml": "services:\n  web:\n    build: ../orders\n"
                                          "    image: org/web\n"}
    for flags in ((), ("--components", "charts")):
        where = tmp_path / ("narrowed" if flags else "whole")
        built = build(where, {"charts": charts, "orders": dict(CODE),
                              "__deploys__": [join()]}, *flags)
        assert chart_claim(built)["dst"] == "svc:web@orders", flags
        assert built.deploy("charts/compose.yaml")["props"]["mapped_by"] == "build", flags
        assert deploys_rows(built) == [], flags


def test_an_unplaced_builds_node_can_be_declared_onto(tmp_path):
    """D23-60: an unplaced build's claim carries `mapped_by` empty, and is still a build."""
    built = build(tmp_path, {"charts": chart(),
                             "deploy": {"compose.yaml": "services:\n  web:\n"
                                                        "    build: ../nowhere\n"
                                                        "    image: org/web\n"},
                             "__deploys__": [join(onto="svc:web@deploy")]})
    assert chart_claim(built)["dst"] == "svc:web@deploy"
    assert not [r for r in deploys_rows(built) if r["kind"] == "deploys-unmatched"]


def test_an_entrypoint_on_the_node_a_declaration_moved_the_claim_off_is_unmatched(tmp_path):
    """D23-8: a declared join moves the claim off its old node - re-aim the entrypoint."""
    built = build(tmp_path, estate(join(), __entrypoint__=["svc:web@web"]))
    assert [r for r in built.rows if r.get("kind") == "entrypoint-unmatched"]


def test_a_held_chart_declared_onto_its_own_components_build_drops_the_hold(tmp_path):
    """D23-57, decision 54: ruling 15 holds a refused chart on its estate node where its own
    component's node holds a build; a declaration onto that node moves it, ranked."""
    charts = {**chart(), "compose.yaml": "services:\n  web:\n    build: .\n"}
    built = build(tmp_path, {"charts": {**charts, **CODE},
                             "__apart__": [{"service": "web", "component": "charts"}],
                             "__deploys__": [join(onto="svc:web@charts")]})
    edge = chart_claim(built)
    assert edge["dst"] == "svc:web@charts"
    assert edge.get("canonical", True) is not False, "the hold's `canonical: false` dropped"
    assert "apart" not in edge["props"]


def test_the_mcp_door_serves_where_a_join_was_declared(tmp_path):
    """D23-26: `row()` whitelists props, so `declared_at` is served beside `declared`."""
    from tools.graph.mcp_server import GraphService
    built = build(tmp_path, estate(join()))
    assert built.code == 0
    incoming = GraphService(graph_dir=tmp_path / "out").node_neighbors("svc:web@orders")["in"]
    declared = [r for r in incoming if r.get("declared")]
    assert declared and declared[0]["declared_at"].startswith("workspace.toml:")


def test_images_a_run_that_is_not_complete_cannot_judge_leave_the_block(tmp_path):
    """D23-122: a run holding a compose file it could not parse may hold the build that runs the
    consumer's image - the block is neither refused nor followed: LEFT, no row of its own."""
    built = build(tmp_path, estate(join(), chart_image="ghcr.io/org/web",
                                   broken={"compose.yaml": "services: [\n"}))
    assert chart_claim(built)["dst"] == "svc:web@web"
    assert not [r for r in deploys_rows(built) if r["kind"] == "deploys-contradicted"]


def test_a_narrowed_run_judges_no_absence(tmp_path):
    """D23-44: a narrowed run may hold a pair's build claims without its consumers, so it says
    nothing of a block naming only a build."""
    built = build(tmp_path, estate({"service": "web", "component": "deploy",
                                    "onto": "svc:web@orders"}), "--components", "deploy")
    assert not deploys_rows(built)


def test_extracted_at_reads_a_declared_moves_record_never_a_name_join():
    """D23-27, D23-39: `joined_by` tested for truth read a declared move as a name join - a
    chart's claim then 'extracted' onto its own component's node."""
    from tools.graph.core.model import Edge, Node
    from tools.graph.links.namejoin import extracted_at
    node = Node(id="deploy:charts/web/values.yaml", type="deploy",
                props={"kind": "helm", "component": "charts"})
    edge = Edge(src=node.id, type="DG_DEPLOYS", dst="svc:web@orders", source_file="v.yaml",
                line=1, extractor="helm-deployments",
                props={"service": "web", "joined_by": "declared",
                       "extracted_from": "svc:web@web"})
    assert extracted_at(edge, {node.id: node}) == "svc:web@web"
