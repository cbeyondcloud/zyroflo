"""Compose `extends:` and `include:` are followed within the component (T620, decision 40).

`extends:` - a service with no build context of its own takes the first one up its chain of
bases, resolved from the directory of the file declaring it, as compose resolves it, and is
placed by it. `include:` - an included compose file this component reads is read on its own,
as before. Either is followed only into the compose files the reader is handed in the
component: a shard's stamp digests those alone, and batch 15a's guard refuses a read outside
them. What cannot be followed is a row naming why; the claim stays the component's, and the
env view does not move (ruling 10).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.tests._estate import build, producer  # noqa: E402

BASE = "services:\n  apibase:\n    build: ../orders\n"
CODE = {"main.py": "print(1)\n"}


def deploy(compose_yaml: str, **files: str) -> dict:
    """A deployment-only `deploy` component (no build of its own) beside `orders` and
    `billing`, which hold code to build."""
    return {"deploy": {"compose.yaml": compose_yaml, **files}, "orders": dict(CODE),
            "billing": dict(CODE)}


# ── extends: the build comes from the base ──────────────────────────────────────────────

PLACED = {
    "same-file": (deploy("services:\n  worker:\n    extends: local\n"
                         "  local:\n    build: ../billing\n"),
                  "worker", "svc:worker@billing", "compose.yaml:4"),
    "another-compose-file": (deploy("services:\n  api:\n    extends:\n"
                                    "      file: docker-compose.base.yml\n"
                                    "      service: apibase\n",
                                    **{"docker-compose.base.yml": BASE}),
                             "api", "svc:api@orders", "docker-compose.base.yml:2"),
    "a-chain-of-two": (deploy("services:\n  web:\n    extends: mid\n"
                              "  mid:\n    extends:\n      file: docker-compose.base.yml\n"
                              "      service: apibase\n",
                              **{"docker-compose.base.yml": BASE}),
                       "web", "svc:web@orders", "docker-compose.base.yml:2"),
    "resolved-from-the-base-files-directory": (
        deploy("services:\n  api:\n    extends:\n      file: infra/docker-compose.base.yml\n"
               "      service: apibase\n",
               **{"infra/docker-compose.base.yml":
                  "services:\n  apibase:\n    build: ../../orders\n"}),
        "api", "svc:api@orders", "infra/docker-compose.base.yml:2"),
}


@pytest.mark.parametrize("shape", sorted(PLACED))
def test_a_service_extending_a_base_that_builds_is_placed_by_that_build(tmp_path, shape):
    """On `main` each of these was placed as the deploying component's own infrastructure:
    the build `extends:` brings was not seen (measured, plans/b21-evidence/probe_t620.out)."""
    estate, service, target, where = PLACED[shape]
    built = build(tmp_path, estate)
    edge = built.deploy("deploy/compose.yaml", service)
    assert (edge["dst"], edge["props"]["mapped_by"]) == (target, "build")
    assert edge["props"]["build_from"] == where
    assert not built.kinds("compose-extends-unresolved")


def test_a_service_with_a_build_context_of_its_own_is_placed_by_it(tmp_path):
    """Its own context wins over its base's, as compose merges a `build:` the service writes."""
    built = build(tmp_path, deploy("services:\n  api:\n    build: ../billing\n    extends:\n"
                                   "      file: docker-compose.base.yml\n"
                                   "      service: apibase\n",
                                   **{"docker-compose.base.yml": BASE}))
    edge = built.deploy("deploy/compose.yaml", "api")
    assert edge["dst"] == "svc:api@billing" and "build_from" not in edge["props"]


UNFOLLOWED = {
    "not-a-compose-name": (deploy("services:\n  web:\n    extends:\n      file: common.yml\n"
                                  "      service: base\n",
                                  **{"common.yml": "services:\n  base:\n    build: ../orders\n"}),
                           "not named as a compose file"),
    "a-missing-base": (deploy("services:\n  web:\n    extends:\n"
                              "      file: docker-compose.base.yml\n      service: nope\n",
                              **{"docker-compose.base.yml": BASE}),
                       "`docker-compose.base.yml` does not declare"),
    "a-cycle": (deploy("services:\n  web:\n    extends: a\n  a:\n    extends: b\n"
                       "  b:\n    extends: a\n"), "extends itself"),
    "interpolated": (deploy("services:\n  web:\n    extends:\n      file: ${BASE}.yml\n"
                            "      service: base\n"), "interpolates"),
    "another-components-file": (
        {**deploy("services:\n  web:\n    extends:\n      file: ../shared/compose.yaml\n"
                  "      service: base\n"),
         "shared": {"compose.yaml": "services:\n  base:\n    build: ../orders\n"}},
        "a file of component `shared`"),
}


@pytest.mark.parametrize("shape", sorted(UNFOLLOWED))
def test_an_extends_the_graph_cannot_follow_is_a_row(tmp_path, shape):
    """Never silence: the build the base may bring is unknown, so the row says why and the
    claim stays the component's, as for a build that cannot be placed."""
    estate, fragment = UNFOLLOWED[shape]
    built = build(tmp_path, estate)
    rows = [r for r in built.kinds("compose-extends-unresolved") if r["service"] == "web"]
    assert len(rows) == 1 and fragment in rows[0]["why"], built.kinds("compose-extends-unresolved")
    assert rows[0]["source_file"] == "compose.yaml" and rows[0]["line"] == 2
    assert built.deploy("deploy/compose.yaml", "web")["dst"] == "svc:web@deploy"


# ── include: an included file this component does not read is said ─────────────────────

INCLUDED = {
    "not-a-compose-name": ("include:\n  - infra/redis.yml\n", {"infra/redis.yml":
                           "services:\n  redis:\n    image: redis:7\n"},
                           "infra/redis.yml", "not named as a compose file"),
    "another-components-file": ("include:\n  - path: ../shared/compose.yaml\n", {},
                                "../shared/compose.yaml", "a file of component `shared`"),
    "interpolated": ("include:\n  - ${ENV}/compose.yaml\n", {}, "${ENV}/compose.yaml",
                     "interpolates"),
    "remote": ("include:\n  - oci://registry.example/stack:1\n", {},
               "oci://registry.example/stack:1", "not a file in this workspace"),
    "a-project-directory-elsewhere": ("include:\n  - path: infra/compose.db.yaml\n"
                                      "    project_directory: .\n",
                                      {"infra/compose.db.yaml":
                                       "services:\n  db:\n    image: postgres:16\n"},
                                      "infra/compose.db.yaml", "project_directory"),
    "a-missing-compose-file": ("include:\n  - nowhere/compose.yaml\n", {},
                               "nowhere/compose.yaml", "was not handed"),
    "a-file-that-does-not-parse": ("include:\n  - infra/compose.bad.yaml\n",
                                   {"infra/compose.bad.yaml": "services: [\n"},
                                   "infra/compose.bad.yaml", "could not be read"),
    "a-declared-childs-file": ("include:\n  - sub/compose.yaml\n", {}, "sub/compose.yaml",
                               "declared inside this one"),
}


@pytest.mark.parametrize("shape", sorted(INCLUDED))
def test_an_include_the_graph_cannot_follow_is_a_row(tmp_path, shape):
    """On `main` a service in an included file the graph does not read vanished with no row
    (measured: `redis`), and so did every other include it could not follow."""
    head, files, written, fragment = INCLUDED[shape]
    estate = deploy(head + "services:\n  web:\n    image: nginx\n", **files)
    estate["shared"] = {"compose.yaml": "services:\n  cache:\n    image: memcached\n"}
    estate["deploy/sub"] = {"compose.yaml": "services:\n  side:\n    image: busybox\n"}
    estate["__names__"] = {"deploy/sub": "sub"}
    built = build(tmp_path, estate)
    rows = built.kinds("compose-include-unresolved")
    assert [r["include"] for r in rows] == [written], rows
    assert fragment in rows[0]["why"] and rows[0]["source_file"] == "compose.yaml"
    assert rows[0]["line"] == 2


def test_an_included_compose_file_the_component_reads_is_read_on_its_own(tmp_path):
    """As on `main`: its services are claimed by its own package and placed from its own
    directory - and a file holding nothing but `include:` is still read for it."""
    built = build(tmp_path, deploy("include:\n  - infra/compose.db.yaml\n",
                                   **{"infra/compose.db.yaml":
                                      "services:\n  db:\n    build: ../../billing\n"}))
    assert not built.kinds("compose-include-unresolved")
    assert built.placed("deploy/infra/compose.db.yaml", "db") == ("svc:db@billing", "billing",
                                                                  "build")


def test_the_env_view_does_not_move_with_the_placement(tmp_path):
    """Ruling 10: placement by an inherited build moves the node, never which code sees the
    service's settings - the extending service's values stay under the view `main` files
    them under, so `orders`' code does not start reading them."""
    estate = deploy("services:\n  api:\n    extends:\n      file: docker-compose.base.yml\n"
                    "      service: apibase\n    environment:\n      TOPIC: api-value\n",
                    **{"docker-compose.base.yml": BASE})
    estate["orders"] = {"app.py": producer("TOPIC")}
    built = build(tmp_path, estate)
    assert built.deploy("deploy/compose.yaml", "api")["dst"] == "svc:api@orders"
    assert "topic:api-value" not in built.topics
