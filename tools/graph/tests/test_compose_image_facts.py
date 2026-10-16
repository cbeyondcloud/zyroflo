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
