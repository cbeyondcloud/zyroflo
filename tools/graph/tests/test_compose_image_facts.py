"""Batch 23 (T589; design pass 23, section 1): the facts compose publishes for the image link - a
claim's `unplaced`, `unfollowed` and `extends` (D23-43), and `extends_unread` where its chain broke
at a base the graph does not read, which may declare a build of any image; a chain compose itself
refuses hides no build and carries none (D23-119, D23-132). An override taking its base's claim
takes its `extends_unread` too, a fact no row is emitted from (D23-118, D23-134).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build  # noqa: E402

CODE = {"main.py": "print(1)\n"}
BASE = "services:\n  base:\n    build: ../orders\n"


def deploy(compose_yaml: str, **files: str) -> dict:
    return {"deploy": {"compose.yaml": compose_yaml, **files}, "orders": dict(CODE)}


def extending(file: str) -> str:
    return f"services:\n  web:\n    extends:\n      file: {file}\n      service: base\n"


#: A chain broken at a base the graph does not read - `reach`'s every reason but "names no
#: file" - and the reason `extends_unread` carries.
UNREAD = {
    "not-a-compose-name": (deploy(extending("common.yml"), **{"common.yml": BASE}),
                           "names `common.yml`, which is not named as a compose file"),
    "not-handed": (deploy(extending("docker-compose.gone.yml")),
                   "names `docker-compose.gone.yml`, which this component's compose reading was "
                   "not handed"),
    "services-not-read": (deploy(extending("docker-compose.base.yml"),
                                 **{"docker-compose.base.yml": "services:\n  - base\n"}),
                          "names `docker-compose.base.yml`, whose services could not be read"),
    "interpolated": (deploy(extending("${BASE}.yml")),
                     "names `${BASE}.yml`, which compose interpolates when it runs"),
    "remote": (deploy(extending("git@example.com:org/compose.yaml")),
               "names `git@example.com:org/compose.yaml`, which is not a file in this workspace"),
    "absolute": (deploy(extending("/srv/compose.yaml")),
                 "names `/srv/compose.yaml`, an absolute path"),
    "outside": (deploy(extending("../../elsewhere/compose.yaml")),
                "names `../../elsewhere/compose.yaml`, which lies outside this component"),
    "another-components": ({**deploy(extending("../shared/compose.yaml")),
                            "shared": {"compose.yaml": BASE}},
                           "names `../shared/compose.yaml`, a file of component `shared`"),
    # The chain walked whole past the context that places it, then broken at an unread file.
    "past-its-own-build": (deploy("services:\n  web:\n    build: ../orders\n    extends:\n"
                                  "      file: common.yml\n      service: base\n",
                                  **{"common.yml": BASE}),
                           "names `common.yml`, which is not named as a compose file"),
}


@pytest.mark.parametrize("shape", sorted(UNREAD))
def test_a_chain_broken_at_a_base_the_graph_does_not_read_carries_extends_unread(tmp_path,
                                                                                shape):
    estate, reason = UNREAD[shape]
    props = build(tmp_path, estate).deploy("deploy/compose.yaml", "web")["props"]
    assert props["extends_unread"].startswith(reason), props.get("extends_unread")
    assert props["unfollowed"] and props["extends"].startswith("base in ")


#: A chain compose refuses - it hides no build, so it withholds nothing (D23-119, D23-132).
REFUSED = {
    "no-base-service-named": deploy("services:\n  web:\n    extends:\n"
                                    "      file: docker-compose.base.yml\n",
                                    **{"docker-compose.base.yml": BASE}),
    "a-file-naming-no-file": deploy("services:\n  web:\n    extends:\n      file: \"\"\n"
                                    "      service: base\n"),
    "a-cycle": deploy("services:\n  web:\n    extends: a\n  a:\n    extends: web\n"),
    "a-base-its-read-file-does-not-declare": deploy(
        "services:\n  web:\n    extends:\n      file: docker-compose.base.yml\n"
        "      service: nope\n", **{"docker-compose.base.yml": BASE}),
    "a-typo-in-its-own-file": deploy("services:\n  web:\n    extends: bsae\n"
                                     "  base:\n    image: org/base\n"),
    # The chain walked whole past the context that places it, then refused.
    "a-typo-past-its-own-build": deploy("services:\n  web:\n    build: ../orders\n"
                                        "    extends: bsae\n  base:\n    image: org/base\n"),
}


@pytest.mark.parametrize("shape", sorted(REFUSED))
def test_a_chain_compose_refuses_carries_no_extends_unread_and_keeps_its_row(tmp_path, shape):
    built = build(tmp_path, REFUSED[shape])
    props = built.deploy("deploy/compose.yaml", "web")["props"]
    assert "extends_unread" not in props, props["extends_unread"]
    assert props["unfollowed"], "its own row's reason stands on the edge"
    assert [r for r in built.kinds("compose-extends-unresolved") if r["service"] == "web"]


def test_an_unplaced_build_is_published_on_its_claim(tmp_path):
    built = build(tmp_path, deploy("services:\n  web:\n    build: ../nowhere\n"))
    props = built.deploy("deploy/compose.yaml", "web")["props"]
    (row,) = built.kinds("compose-build-unresolved")
    assert props["unplaced"] and props["unplaced"] in row["why"]


def test_a_claim_whose_chain_is_followed_carries_none_of_them(tmp_path):
    built = build(tmp_path, deploy("services:\n  web:\n    extends: base\n" + BASE[10:]))
    props = built.deploy("deploy/compose.yaml", "web")["props"]
    assert not {"unplaced", "unfollowed", "extends", "extends_unread"} & set(props), props


def test_an_override_taking_its_bases_claim_takes_its_extends_unread_but_no_row(tmp_path):
    """D23-118, D23-134: the override carries the base's `extends_unread` and its own empty
    `unfollowed`; the base's chain is one row, its own."""
    built = build(tmp_path, deploy(extending("common.yml"), **{
        "common.yml": BASE,
        "compose.override.yml": "services:\n  web:\n    environment:\n      A: \"1\"\n"}))
    base = built.deploy("deploy/compose.yaml", "web")["props"]
    override = built.deploy("deploy/compose.override.yml", "web")["props"]
    assert override["extends_unread"] == base["extends_unread"]
    assert "unfollowed" not in override and "extends" not in override
    rows = [r for r in built.kinds("compose-extends-unresolved") if r["service"] == "web"]
    assert [r["source_file"] for r in rows] == ["compose.yaml"], rows


def test_an_override_with_an_unread_chain_of_its_own_keeps_its_own(tmp_path):
    """D23-134: one row per chain - the override's own reason, never its base's."""
    built = build(tmp_path, deploy(extending("common.yml"), **{
        "common.yml": BASE, "other.yml": BASE,
        "compose.override.yml": extending("other.yml")}))
    override = built.deploy("deploy/compose.override.yml", "web")["props"]
    assert override["extends_unread"].startswith("names `other.yml`")
    rows = [r for r in built.kinds("compose-extends-unresolved") if r["service"] == "web"]
    assert sorted(r["source_file"] for r in rows) == ["compose.override.yml", "compose.yaml"]


# ── include: what it names that the reader does not read ─────────────────────────────────

STACK = "services:\n  web:\n    build: ../orders\n"


def node_of(built, source: str) -> dict:
    (node,) = [n for n in built.nodes if n["id"] == "deploy:" + source]
    return node["props"]


def include_rows(built) -> list[str]:
    return sorted(r["why"] for r in built.kinds("compose-include-unresolved"))


@pytest.mark.parametrize("include", ["include: stack.yml\n", "include:\n  path: stack.yml\n",
                                     "include:\n  - stack.yml\n"],
                         ids=["a-scalar", "a-single-mapping", "a-list"])
def test_an_include_naming_a_file_the_reader_does_not_read_is_unread_on_its_node(tmp_path,
                                                                                include):
    """D23-141, D23-161: written as a scalar or a single mapping, `include: stack.yml` left the
    run complete over a graph missing its only build; it is read as a list of what it spells, the
    file named on the compose node, with a row of its own beside the "is not a list" row."""
    built = build(tmp_path, deploy(include + "services:\n  other:\n    image: org/other\n",
                                   **{"stack.yml": STACK}))
    (entry,) = node_of(built, "deploy/compose.yaml")["include_unread"]
    assert entry == {"file": "stack.yml", "line": 1 if include.startswith("include: ") else 2}, \
        entry
    rows = include_rows(built)
    assert any("names `stack.yml`, which is not named as a compose file" in r for r in rows)
    assert len(rows) == (1 if include.startswith("include:\n  -") else 2), rows


@pytest.mark.parametrize("include, row", [
    ("include: 7\n", "`include:` is not a list of files"),
    ("include:\n  - 7\n", "`include:` has an entry that names no file"),
    ("include:\n  project_directory: ../x\n", "`include:` is not a list of files"),
    ("include:\n  - \"\"\n", "`include:` names no file"),
], ids=["a-number", "a-list-of-a-number", "a-mapping-with-no-path", "an-empty-entry"])
def test_an_include_that_spells_no_file_hides_nothing_and_its_row_stands_alone(tmp_path,
                                                                              include, row):
    """D23-149, D23-160: a value that is no string names no file, in either spelling - one
    compose refuses, which hides nothing."""
    built = build(tmp_path, deploy(include + "services:\n  other:\n    image: org/other\n"))
    assert "include_unread" not in node_of(built, "deploy/compose.yaml")
    (only,) = include_rows(built)
    assert only.startswith(row), only


def test_a_single_mapping_listing_two_unread_files_names_both(tmp_path):
    """D23-161: a row for each, both in `include_unread`."""
    built = build(tmp_path, deploy("include:\n  path:\n    - stack.yml\n    - more.yml\n"
                                   "services:\n  other:\n    image: org/other\n",
                                   **{"stack.yml": STACK, "more.yml": STACK}))
    assert [e["file"] for e in node_of(built, "deploy/compose.yaml")["include_unread"]] == [
        "stack.yml", "more.yml"]
    rows = include_rows(built)
    assert sum("names `stack.yml`" in r or "names `more.yml`" in r for r in rows) == 2, rows


def test_an_include_naming_a_file_the_reader_reads_withholds_nothing(tmp_path):
    """D23-148: counted whole, a scalar naming a file the reader read withheld every judgement;
    its row says what it is, and nothing is unread."""
    built = build(tmp_path, deploy("include: docker-compose.stack.yml\n"
                                   "services:\n  other:\n    image: org/other\n",
                                   **{"docker-compose.stack.yml": STACK}))
    assert "include_unread" not in node_of(built, "deploy/compose.yaml")
    assert include_rows(built) == [
        "`include:` is not a list of files. The services it declares are missing from the "
        "graph, not absent"]


def test_a_single_mappings_project_directory_is_checked_as_a_list_entrys_is(tmp_path):
    """D23-162: its misplacement row with it, nothing unread."""
    built = build(tmp_path, deploy("include:\n  path: infra/docker-compose.stack.yml\n"
                                   "  project_directory: .\n"
                                   "services:\n  other:\n    image: org/other\n",
                                   **{"infra/docker-compose.stack.yml": STACK}))
    assert "include_unread" not in node_of(built, "deploy/compose.yaml")
    rows = include_rows(built)
    assert any("with `project_directory: .`" in r for r in rows) and len(rows) == 2, rows


def test_a_file_holding_only_an_include_of_an_unread_file_has_a_node_saying_so(tmp_path):
    """A top-level compose file that declares no service of its own and includes an unread one:
    its node, carrying no claim, names the file - else nothing in the graph would."""
    built = build(tmp_path, deploy("include:\n  - stack.yml\n", **{"stack.yml": STACK}))
    props = node_of(built, "deploy/compose.yaml")
    assert props["include_unread"] == [{"file": "stack.yml", "line": 2}]
    assert not [e for e in built.deploys if e["src"] == "deploy:deploy/compose.yaml"]


def test_a_file_holding_only_an_include_it_reads_has_no_node(tmp_path):
    built = build(tmp_path, deploy("include:\n  - docker-compose.stack.yml\n",
                                   **{"docker-compose.stack.yml": STACK}))
    assert not [n for n in built.nodes if n["id"] == "deploy:deploy/compose.yaml"]


# ── deployment_only: what compose read of a component, per component ────────────────────

IMAGE_ONLY = "services:\n  web:\n    image: org/web:2\n"


def only_of(built, component: str = "deploy") -> dict[str, object]:
    """Each compose node of `component`: its `deployment_only`, or "unpublished"."""
    return {n["id"]: n["props"].get("deployment_only", "unpublished") for n in built.nodes
            if n["type"] == "deploy" and n["props"].get("kind") == "compose"
            and n["props"].get("component") == component}


def test_a_component_compose_read_whole_with_no_build_is_deployment_only(tmp_path):
    """D23-6: a fact compose publishes, never a mark read as one."""
    built = build(tmp_path, deploy(IMAGE_ONLY, **{"compose.prod.yml": IMAGE_ONLY}))
    assert only_of(built) == {"deploy:deploy/compose.yaml": True,
                              "deploy:deploy/compose.prod.yml": True}


def test_a_component_one_of_whose_read_files_builds_is_not_deployment_only_on_any_node(
        tmp_path):
    built = build(tmp_path, deploy("services:\n  web:\n    build: ../orders\n",
                                   **{"compose.prod.yml": IMAGE_ONLY}))
    assert set(only_of(built).values()) == {False}


@pytest.mark.parametrize("files", [
    {"compose.prod.yml": "services: [\n"},
    {"compose.prod.yml": "include:\n  - stack.yml\n", "stack.yml": STACK},
    {"compose.prod.yml": extending("common.yml"), "common.yml": BASE},
], ids=["a-sibling-that-does-not-parse", "an-unread-include", "a-chain-to-a-base-unread"])
def test_a_component_compose_did_not_read_whole_publishes_none(tmp_path, files):
    """D23-61, D23-101, D23-116, D23-131: what compose did not read may declare a build - read
    per component, so a sibling file's gap leaves every node unpublished."""
    built = build(tmp_path, deploy(IMAGE_ONLY, **files))
    assert set(only_of(built).values()) == {"unpublished"}, only_of(built)


def test_a_chain_compose_refuses_withholds_nothing(tmp_path):
    """D23-119: a typo'd base hides no build - the component is still deployment-only."""
    built = build(tmp_path, deploy("services:\n  web:\n    image: org/web\n    extends: bsae\n"))
    assert set(only_of(built).values()) == {True}


@pytest.mark.parametrize("lost_name", ["Docker-Compose.yml", "docker-compose.YML"])
def test_a_compose_file_lost_to_its_case_withholds_it(tmp_path, lost_name):
    """D23-2, D23-31: in both spellings of a case loss."""
    built = build(tmp_path, deploy(IMAGE_ONLY, **{lost_name: IMAGE_ONLY}))
    assert set(only_of(built).values()) == {"unpublished"}, only_of(built)


def test_a_building_components_only_build_lost_to_its_case_publishes_none(tmp_path):
    """D23-17: its only build sat in a case-lost file, so it read as deployment-only."""
    built = build(tmp_path, deploy(IMAGE_ONLY, **{"Docker-Compose.yml": STACK}))
    assert set(only_of(built).values()) == {"unpublished"}, only_of(built)


def test_a_charts_case_loss_never_withholds_composes(tmp_path):
    """D23-31: compose applies its own globs to the component's case record."""
    built = build(tmp_path, deploy(IMAGE_ONLY, **{
        "charts/web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n",
        "charts/web/Templates/deployment.yaml": "kind: Deployment\n"}))
    assert set(only_of(built).values()) == {True}, only_of(built)


def test_a_component_with_a_directory_the_walk_cannot_list_publishes_none(tmp_path):
    """D23-68: compose could not see one, so a component whose only build sat in an unlistable
    directory read as deployment-only. Whether the refusal binds is measured (`_listing`)."""
    from tools.graph.tests._estate import lay_down, read, spawn
    from tools.graph.tests._listing import allow_listing, deny_listing, lists
    lay_down(tmp_path, deploy(IMAGE_ONLY, **{"infra/docker-compose.yml": STACK}))
    guarded = tmp_path / "ws" / "deploy" / "infra"
    deny_listing(guarded)
    try:
        binds = not lists(guarded)
        proc = spawn(tmp_path)
    finally:
        allow_listing(guarded)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    built = read(proc.returncode, tmp_path / "out")
    assert set(only_of(built).values()) == ({"unpublished"} if binds else {False}), (
        binds, only_of(built))
