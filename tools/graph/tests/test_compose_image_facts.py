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
