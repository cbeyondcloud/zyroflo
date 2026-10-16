"""Batch 16 (the owner's ruling 30; design rounds 3 and 4, D16-26, D16-34): the parser stamp is
exact per file and conservative across components.

Exact: for each reader's edge files, a publish with the parser installed and one with it
denied stamp a component's own half differently exactly when they leave different shard
bytes - a sniffed non-spec YAML, a module under `src/test/`, `src/test/Helper.JAVA` and a test
helper outside a test directory change no bytes, so they stamp nothing a suffix would have.

Conservative (T633): the closure half counts a closure reader's files in every component, so
where that reader's cache is keyed per component a shard is stale while its bytes would not
change - never current while they would.
"""
from __future__ import annotations

import json
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import freshness, snapshot as SN  # noqa: E402
from tools.graph.core.lib import props, srcfacts  # noqa: E402
from tools.graph.tests._estate import compose, lay_down, svc  # noqa: E402

ORDERFLOW = Path(__file__).resolve().parent / "fixtures" / "orderflow"
SPEC = "openapi: 3.0.0\ninfo:\n  title: t\n  version: '1'\npaths: {}\n"
JSON_SPEC = json.dumps({"openapi": "3.0.0", "info": {"title": "t", "version": "1"},
                        "paths": {}})
CM = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: web\ndata:\n  URL: http://x\n"


@contextmanager
def denied(*parsers: str):
    """Publish as a tree without these parsers reads: YAML through `props`, a grammar
    through `srcfacts`, each reader asking as it always does."""
    saved = props.yaml_available, srcfacts.available
    real = srcfacts.available
    if "yaml" in parsers:
        props.yaml_available = lambda: False
    srcfacts.available = lambda name: name not in parsers and real(name)
    try:
        yield
    finally:
        props.yaml_available, srcfacts.available = saved


def publish(where: Path) -> None:
    assert B.main(["--workspace", str(where / "ws" / "workspace.toml"), "--out",
                   str(where / "out"), "--run-id", "fixed", "--empty-ok",
                   "--publish-snapshot"]) == 0


def shard(where: Path, path: str) -> tuple[dict, bytes]:
    """A component's stamped parsers and its shard's bytes."""
    dir_ = where / "ws" / path / ".sdlc" / "graph"
    manifest = json.loads((dir_ / SN.MANIFEST).read_text(encoding="utf-8"))
    return manifest["parsers"], (dir_ / SN.SHARD).read_bytes() + b"\x00" + (
        dir_ / SN.SHARD_UNRESOLVED).read_bytes()


#: Each reader's edge files, the parsers denied, and whether a parser reads them at all.
CASES = [
    ("openapi-spec", {"openapi.yaml": SPEC}, ("yaml",), True),
    ("openapi-non-spec-yaml", {"config.yaml": "key: value\n"}, ("yaml",), False),
    ("openapi-json-spec", {"openapi.json": JSON_SPEC}, ("yaml",), False),
    ("tests-module-under-src-test", {"src/test/x.mjs": "export const u = 'http://x/y'\n"},
     ("javascript",), False),
    ("tests-helper-JAVA-under-src-test", {"src/test/Helper.JAVA": "class Helper {}\n"},
     ("java",), True),
    ("tests-resource-under-src-test", {"src/test/resources/data.txt": "x\n"},
     ("java", "python", "javascript", "typescript", "tsx"), False),
    ("tests-python-test", {"tests/test_app.py": "def test_x():\n    assert 'http://x'\n"},
     ("python",), True),
    ("frontend-test-helper", {"tests/util.js": "export const u = 'http://x/y'\n"},
     ("javascript",), False),
    ("frontend-package-json", {"package.json": '{"name": "a"}\n'}, ("javascript",), False),
    ("frontend-source", {"src/app.js": "fetch('http://x/y')\n"}, ("javascript",), True),
    ("spring-rest-calls-properties", {"application.properties": "x.url=http://y/z\n"},
     ("java",), False),
    ("helm-template-only", {"charts/web/templates/cm.yaml": CM}, ("yaml",), True),
    ("helm-values-only", {"charts/web/values.yaml": "fullnameOverride: web\n"}, ("yaml",),
     True),
    ("compose", {"docker-compose.yml": compose(svc("web", None, None, image="org/web:1"))},
     ("yaml",), True),
    ("java-source", {"src/main/java/P.java": "class P {}\n"}, ("java",), True),
    ("maven-pom", {"pom.xml": "<project><artifactId>a</artifactId></project>\n"},
     ("java",), False),
    ("gradle-build", {"build.gradle": "plugins { id 'java' }\n"}, ("java",), False),
    ("sql-migration", {"V1__init.sql": "create table t (id int);\n"}, ("yaml",), False),
    ("python-source", {"app.py": "import os\n\nX = os.environ['X']\n"}, ("python",), False),
]


@pytest.mark.parametrize("files, parsers, read", [c[1:] for c in CASES],
                         ids=[c[0] for c in CASES])
def test_the_own_half_differs_exactly_when_the_shard_bytes_do(tmp_path, files, parsers, read):
    on, off = tmp_path / "on", tmp_path / "off"
    lay_down(on, {"a": files})
    lay_down(off, {"a": files})
    publish(on)
    with denied(*parsers):
        publish(off)
    (stamp_on, bytes_on), (stamp_off, bytes_off) = shard(on, "a"), shard(off, "a")
    assert (stamp_on != stamp_off) == (bytes_on != bytes_off), (stamp_on, stamp_off)
    assert (stamp_on != stamp_off) is read, (stamp_on, stamp_off)


@pytest.mark.parametrize("parsers, moved", [
    (("typescript", "tsx"), set()),
    (("javascript",), {"web-console"}),
], ids=["typescript-denied", "javascript-denied"])
def test_orderflow_stamps_exactly_what_a_denied_grammar_changed(tmp_path, parsers, moved):
    """The real estate: each component's own half differs exactly when its shard bytes do.
    Orderflow's front end is JavaScript and JSX, so denied TypeScript nothing moves - no stamp
    a suffix table would have written - and denied JavaScript, `web-console` alone does."""
    on, off = tmp_path / "on", tmp_path / "off"
    for where in (on, off):
        shutil.copytree(ORDERFLOW, where / "ws")
    publish(on)
    with denied(*parsers):
        publish(off)
    ws = B.resolve_workspace(on / "ws" / "workspace.toml")
    si = B.load_installer()
    components = si.discover_components(ws)["components"]
    seen: dict[str, str] = {}
    regs, _links = B.discover(None, "stack", seen)
    regs = regs + B.discover(B.LINK_ROOT, "link", seen)[0]
    if (profile := B.resolve_profile(ws)) is not None:
        regs = regs + B.discover_package(profile, "profile", seen, root_label="profiles")[0]
    regs = B.order_extractors(regs)
    act = B.activate(regs, components, ws["root"],
                     root_exclusions=B.installer_root_exclusions(si), workspace=components)
    rule, closure = B.parser_rule(regs), B.cross_closure(regs)
    differs = {}
    for comp in components:
        own, _near = freshness.parser_halves(comp["name"], act, closure, rule, ws["root"],
                                             components)
        differs[comp["name"]] = bool(set(parsers) & set(own))
        assert differs[comp["name"]] == (
            shard(on, comp["path"])[1] != shard(off, comp["path"])[1]), comp
    assert {name for name, moves in differs.items() if moves} == moved


def test_a_closure_reader_keyed_per_component_leaves_a_shard_current_its_bytes_unchanged(
        tmp_path):
    """T633, fixed (batch 22): `spring-rest-calls` in `b` requires `spring-call-concats`, whose
    cache is keyed per component (`CACHE_SCOPE`), so `a`'s Java reaches none of `b`'s facts and
    `b`'s closure half no longer counts it - batch 16 named this remainder, `b` stale by claim
    10 while a publish left its bytes as they were. Denied the grammar, `b` stays current, and
    a publish still leaves its bytes as they are."""
    estate = {"a": {"src/main/java/Client.java": "class Client {}\n"},
              "b": {"application.properties": "orders.url=http://orders/api\n"}}
    lay_down(tmp_path, estate)
    publish(tmp_path)
    _stamp, before = shard(tmp_path, "b")
    with denied("java"):
        verdict = B.check_freshness(tmp_path / "ws" / "workspace.toml")["freshness"]
        publish(tmp_path)
    assert "b" in verdict["fresh"] and "b" not in verdict["stale"], verdict
    assert shard(tmp_path, "b")[1] == before, "its bytes would not have changed"
