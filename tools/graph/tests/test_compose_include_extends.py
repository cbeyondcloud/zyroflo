"""Compose `extends:` and `include:` are followed within the component (T620, decision 40).

`extends:` - a service with no build context of its own takes the first one up its chain of
bases, resolved from the directory of the file declaring it, as compose resolves it, and is
placed by it. `include:` - an included compose file this component reads is read on its own,
as before. Either is followed only into the compose files the reader is handed in the
component: a shard's stamp digests those alone, and batch 15a's guard refuses a read outside
them. What cannot be followed is a row naming why; the claim stays the component's. The env
view follows the placement where the chain's build places the service on a component's own
directory (decision 55, design pass 24 - ruling 10 revised).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.extractors import compose  # noqa: E402
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
    # Review B21-15: a base file whose `services:` is not a mapping was read as declaring no
    # such service, beside its own row saying its services could not be read.
    "a-base-file-whose-services-are-not-a-mapping": (
        deploy("services:\n  web:\n    extends:\n      file: docker-compose.base.yml\n"
               "      service: b\n", **{"docker-compose.base.yml": "services:\n  - b\n"}),
        "whose services could not be read"),
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
    assert rows[0]["why"].endswith("so the build it would take is unknown. The claim stays this "
                                   "component's")
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
    "a-file-whose-services-are-not-a-mapping": ("include:\n  - infra/compose.list.yaml\n",
                                                {"infra/compose.list.yaml":
                                                 "services:\n  - b\n"},
                                                "infra/compose.list.yaml",
                                                "whose services could not be read"),
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


def test_the_env_view_moves_with_a_chain_builds_placement(tmp_path):
    """Decision 55: the build a T620 chain brings counts as the service's own, so a chain build
    placed on `orders`' own directory (`exact`) keys the extending service's values to `orders`,
    whose code reads them - `main` kept them under the service's name (its view `api`)."""
    estate = deploy("services:\n  api:\n    extends:\n      file: docker-compose.base.yml\n"
                    "      service: apibase\n    environment:\n      TOPIC: api-value\n",
                    **{"docker-compose.base.yml": BASE})
    estate["orders"] = {"app.py": producer("TOPIC")}
    built = build(tmp_path, estate)
    assert built.deploy("deploy/compose.yaml", "api")["dst"] == "svc:api@orders"
    assert "topic:api-value" in built.topics


def test_an_extending_services_unplaced_build_is_said_where_the_path_is(tmp_path):
    """Review B21-3: the row for an extending service whose base's build cannot be placed was
    cited at the extending file with "relative to this file" - a file holding no `build:` -
    while the path resolves from the base's directory. It now names the base and its file,
    and the remedy is relative to that file; followed, the build is placed."""
    def estate(context: str) -> dict:
        return deploy("services:\n  web:\n    extends:\n      file: infra/docker-compose.base.yml\n"
                      "      service: b\n",
                      **{"infra/docker-compose.base.yml": f"services:\n  b:\n    build: {context}\n"})

    built = build(tmp_path / "typo", estate("../../orders-typo"))
    rows = {(r["source_file"], r["service"]): r for r in built.kinds("compose-build-unresolved")}
    web = rows[("compose.yaml", "web")]["why"]
    assert web.startswith("`web` takes its build through `extends:` from `b` at "
                          "`infra/docker-compose.base.yml:2`: it builds `../../orders-typo`")
    assert "relative to `infra/docker-compose.base.yml`" in web
    assert "this file" not in web
    assert "relative to this file" in rows[("infra/docker-compose.base.yml", "b")]["why"]
    fixed = build(tmp_path / "fixed", estate("../../orders"))
    assert fixed.deploy("deploy/compose.yaml", "web")["dst"] == "svc:web@orders"
    assert not fixed.kinds("compose-build-unresolved")


@pytest.mark.parametrize("extends", ["{file: docker-compose.base.yml}", "[a, b]"])
def test_an_extends_naming_no_base_records_none(tmp_path, extends):
    """Review B21-7: the row's `extends` field - the base as written - read "None" where no
    base service was written."""
    built = build(tmp_path, deploy(f"services:\n  web:\n    extends: {extends}\n",
                                   **{"docker-compose.base.yml": BASE}))
    (row,) = [r for r in built.kinds("compose-extends-unresolved") if r["service"] == "web"]
    assert row["extends"] == "" and "None" not in row["why"]


def test_an_extends_the_graph_cannot_follow_is_a_row_whatever_builds_the_service(tmp_path):
    """Batch 21's review (T620's ruling, the owner's decision 40: "a row for what can't be
    followed"): a service with a build of its own is placed by it, and an `extends:` the
    graph cannot follow is still a row - compose would not even start that project - worded
    for a claim that does not stay the component's."""
    built = build(tmp_path, deploy("services:\n  web:\n    build: ../orders\n    extends:\n"
                                   "      file: nowhere/compose.yaml\n      service: b\n"))
    assert built.deploy("deploy/compose.yaml", "web")["dst"] == "svc:web@orders"
    (row,) = [r for r in built.kinds("compose-extends-unresolved") if r["service"] == "web"]
    assert "was not handed" in row["why"] and "placed by its own `build:`" in row["why"]
    assert "The claim stays this component's" not in row["why"]


def test_an_extends_past_the_build_that_places_a_service_is_followed_too(tmp_path):
    """A chain is followed whole: past the base whose build places the service, a base the
    graph cannot reach is a row naming the chain, never one about the service's own
    `extends:` alone."""
    built = build(tmp_path, deploy("services:\n  web:\n    extends: mid\n"
                                   "  mid:\n    build: ../orders\n    extends:\n"
                                   "      file: nowhere/compose.yaml\n      service: c\n"))
    assert built.deploy("deploy/compose.yaml", "web")["dst"] == "svc:web@orders"
    (row,) = [r for r in built.kinds("compose-extends-unresolved") if r["service"] == "web"]
    assert row["why"].startswith("`web` reaches `mid` in `compose.yaml` through `extends:`, "
                                 "which extends `c`")
    assert "placed by the `build:` at `compose.yaml:4`" in row["why"]
    assert row["extends"] == "c in nowhere/compose.yaml"


def test_the_compose_file_names_are_spelled_once():
    """Review B21-10: `reach` refuses a file by the names the reader is handed files by -
    one tuple, never a second copy that could drift (T616's class)."""
    # Equality, not identity: CPython shares equal constants within a module, so `is`
    # holds of two copies too.
    assert compose.ComposeExtractor.INPUT_GLOBS == compose.COMPOSE_GLOBS


def test_an_override_placed_by_its_base_is_told_so(tmp_path):
    """An override that declares no build takes its base's claim (T588), so its row about an
    `extends:` it cannot follow is worded from that claim - here placed by its base's build,
    though on its own the override's layer was ambiguous (two files build `web` from two
    components) - never "the claim stays this component's", decided before it inherited."""
    built = build(tmp_path, {
        "billing": dict(CODE), "payments": dict(CODE),
        "orders": {"main.py": "print(1)\n",
                   "docker-compose.yml": "services:\n  web:\n    build: ../billing\n",
                   "docker-compose.prod.yml": "services:\n  web:\n    build: ../payments\n",
                   "docker-compose.override.yml": "services:\n  web:\n    extends:\n"
                                                  "      file: nowhere/compose.yaml\n"
                                                  "      service: b\n"}})
    edge = built.deploy("orders/docker-compose.override.yml", "web")
    assert (edge["dst"], edge["props"]["mapped_by"]) == ("svc:web@billing", "build")
    (row,) = [r for r in built.kinds("compose-extends-unresolved")
              if r["source_file"] == "docker-compose.override.yml"]
    assert row["why"].endswith("It is placed by the build its component's other compose files "
                               "declare")


def test_a_build_the_chain_brings_is_never_called_unknown(tmp_path):
    """Its base's build cannot be placed - its own row says so - and its chain breaks past that
    base: the `extends:` row names the break and never calls the build unknown."""
    built = build(tmp_path, deploy(
        "services:\n  web:\n    extends:\n      file: infra/docker-compose.base.yml\n"
        "      service: b\n",
        **{"infra/docker-compose.base.yml": "services:\n  b:\n    build: ../../orders-typo\n"
                                            "    extends:\n      file: nowhere/compose.yaml\n"
                                            "      service: c\n"}))
    (row,) = [r for r in built.kinds("compose-extends-unresolved")
              if r["source_file"] == "compose.yaml"]
    assert "unknown" not in row["why"] and row["why"].endswith("The claim stays this component's")
    assert [r for r in built.kinds("compose-build-unresolved")
            if (r["source_file"], r["service"]) == ("compose.yaml", "web")]


def test_a_service_whose_own_build_writes_no_context_is_told_its_own_build_places_it(tmp_path):
    """Review B21-17: a `build:` mapping with no `context:` builds the project directory - the
    service's own build - and its row about an `extends:` it cannot follow says so, never
    "the build its component's other compose files declare" of a component with one file."""
    built = build(tmp_path, deploy("services:\n  web:\n    extends: mid\n  mid:\n    build:\n"
                                   "      dockerfile: D\n    extends:\n"
                                   "      file: nowhere/compose.yaml\n      service: c\n"))
    (row,) = [r for r in built.kinds("compose-extends-unresolved") if r["service"] == "mid"]
    assert row["why"].endswith("It is placed by its own `build:`")


def test_a_build_the_chain_declares_is_kept_where_the_chain_breaks_further_up(tmp_path):
    """Review B21-18: `web` takes `mid`'s build, a mapping with no context, whose chain then
    breaks: the build is known and places `web` as it does where the chain ends - never
    "unknown" because of a break past it."""
    def estate(tail: str) -> dict:
        return deploy("services:\n  web:\n    extends: mid\n  mid:\n    build:\n"
                      "      dockerfile: D\n" + tail)

    ends = build(tmp_path / "ends", estate(""))
    breaks = build(tmp_path / "breaks", estate("    extends:\n      file: nowhere/compose.yaml\n"
                                               "      service: c\n"))
    for built in (ends, breaks):
        edge = built.deploy("deploy/compose.yaml", "web")
        assert (edge["dst"], edge["props"]["mapped_by"]) == ("svc:web@deploy", "build")
    (row,) = [r for r in breaks.kinds("compose-extends-unresolved") if r["service"] == "web"]
    assert row["why"].endswith("It is placed by the `build:` at `compose.yaml:4`")
    assert "unknown" not in row["why"]


def test_an_extends_written_as_null_names_no_base(tmp_path):
    """Review B21-19: `extends:` with no value is a compose error, said as `{}` is."""
    built = build(tmp_path, deploy("services:\n  web:\n    extends:\n"))
    (row,) = [r for r in built.kinds("compose-extends-unresolved") if r["service"] == "web"]
    assert "names no base service to extend" in row["why"] and row["extends"] == ""


def test_an_override_whose_chain_brings_a_build_with_no_context_layers_over_its_base(tmp_path):
    """Review B21-25: compose merges a default pair into one service, and a `build:` with no
    context, brought through `extends:`, leaves the base file's context standing - so the
    override layers over its base's claim (T588) and the service is one node, as on `main`,
    never a second node on the declaring component."""
    built = build(tmp_path, {
        "billing": dict(CODE),
        "orders": {"main.py": "print(1)\n",
                   "docker-compose.yml": "services:\n  web:\n    build: ../billing\n",
                   "docker-compose.override.yml": "services:\n  web:\n    extends: localbase\n"
                                                  "  localbase:\n    build:\n"
                                                  "      dockerfile: D\n"}})
    edge = built.deploy("orders/docker-compose.override.yml", "web")
    assert (edge["dst"], edge["props"]["mapped_by"]) == ("svc:web@billing", "build")
    assert "build_from" not in edge["props"]
    assert {s for s in built.svc_ids() if s.startswith("svc:web@")} == {"svc:web@billing"}


def test_two_services_on_one_line_are_told_apart_for_layering(tmp_path):
    """Review B21-33: the declarations a base's written context places were keyed by file and
    line, and flow style puts two services on one line - so `web`'s chain, bringing a context,
    stopped T588's layering for `api` beside it, splitting `api` into a second node with a row
    that told the reader to declare a build. Each declaration is its own."""
    built = build(tmp_path, {
        "billing": dict(CODE), "shared": dict(CODE),
        "orders": {"main.py": "print(1)\n",
                   "docker-compose.yml": "services:\n  api:\n    build: ../billing\n",
                   "docker-compose.prod.yml": "services:\n  api:\n    build: ../shared\n",
                   "docker-compose.override.yml": "services: {localbase: {build: ../orders}, "
                                                  "web: {extends: localbase}, "
                                                  "api: {environment: {X: y}}}\n"}})
    edge = built.deploy("orders/docker-compose.override.yml", "api")
    assert (edge["dst"], edge["props"]["mapped_by"]) == ("svc:api@billing", "build")
    assert not [r for r in built.kinds("compose-build-ambiguous")
                if r["source_file"] == "docker-compose.override.yml"]


def test_a_context_a_base_wrote_keeps_its_service_out_of_the_layer_on_one_line(tmp_path):
    """Review B21-40: round 3's rule on one line - `web`'s chain brings a context `localbase`
    WROTE, so `web` is placed by it and never layers over its base file's `web`. `api` beside
    it, with no build of its own, is a control: it takes its base's claim either way, through
    compose's cross-file `built` map as through T588's layering, which the neighbouring cells
    pin (review B21-43). `web` comes first, so its index in the file (0) is not its line (1):
    a set keyed by either one alone is caught."""
    built = build(tmp_path, {
        "billing": dict(CODE),
        "orders": {"main.py": "print(1)\n",
                   "docker-compose.yml": "services:\n  web:\n    build: ../billing\n"
                                         "  api:\n    build: ../billing\n",
                   "docker-compose.override.yml": "services: {web: {extends: localbase}, "
                                                  "localbase: {build: ../orders}, "
                                                  "api: {environment: {X: y}}}\n"}})
    web = built.deploy("orders/docker-compose.override.yml", "web")
    assert (web["dst"], web["props"]["mapped_by"]) == ("svc:web@orders", "build")
    assert web["props"]["build_from"] == "docker-compose.override.yml:1"
    api = built.deploy("orders/docker-compose.override.yml", "api")
    assert (api["dst"], api["props"]["mapped_by"]) == ("svc:api@billing", "build")


def test_a_service_with_its_own_build_is_never_placed_by_a_bases(tmp_path):
    """Review B21-26: `web` declares its own `build:` with no context, and extends `mid`, whose
    `build:` has none either: `web`'s own build places it, so its edge records no base's and
    its row, where the chain breaks, says its own build places it - whether the chain ends or
    breaks."""
    def estate(tail: str) -> dict:
        return deploy("services:\n  web:\n    build:\n      dockerfile: D\n    extends: mid\n"
                      "  mid:\n    build:\n      dockerfile: E\n" + tail)

    ends = build(tmp_path / "ends", estate(""))
    breaks = build(tmp_path / "breaks", estate("    extends:\n      file: nowhere/compose.yaml\n"
                                               "      service: c\n"))
    for built in (ends, breaks):
        edge = built.deploy("deploy/compose.yaml", "web")
        assert (edge["dst"], edge["props"]["mapped_by"]) == ("svc:web@deploy", "build")
        assert "build_from" not in edge["props"]
    (row,) = [r for r in breaks.kinds("compose-extends-unresolved") if r["service"] == "web"]
    assert row["why"].endswith("It is placed by its own `build:`")
