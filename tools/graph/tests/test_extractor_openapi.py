"""The OpenAPI extractor: what an API promises, joined to what the code serves.

Item 9a. "Endpoints spec-matched" is an M1 coverage target that had no producer: nothing
read a spec, so the join it names could not exist. The join is by NODE ID — the spec's
paths normalise through the same `ids.norm_path` the source extractors use, so an `ep`
node carrying both `DG_EXPOSES` and `DG_SPECIFIES` is served-and-documented, and one
carrying only `DG_SPECIFIES` is a promise nothing keeps. Neither is invented.

Recognition is by CONTENT (a top-level `openapi:`/`swagger:` key), never by filename —
real estates name spec files after their services. The fixture's answer key claimed both
edges by line before this extractor existed (`22cbfcb`), per the fixture's own P2 rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import ids, props  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import _java, openapi, spring  # noqa: E402
from tools.graph.tests._mutate import build_mutated  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def run(tmp_path: Path, files: dict[str, str], alias: str = "svc"):
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    ctx = ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                         files={alias: paths})
    return openapi.OpenApiExtractor().extract(ctx)


def specifies(res):
    return [e for e in res.edges if e.type == "DG_SPECIFIES"]


SPEC = """\
openapi: 3.0.1
info:
  title: Invoicing
  version: "1.0"
paths:
  /bills/v1/list:
    get:
      responses:
        "200": {description: OK}
    post:
      responses:
        "200": {description: OK}
"""


# ── recognition: content, never filename ──────────────────────────────────────


def test_a_spec_is_recognised_by_its_content(tmp_path):
    res = run(tmp_path, {"invoicing.yaml": SPEC})
    assert len(specifies(res)) == 1
    assert specifies(res)[0].dst == "ep:/bills/v1/list"


def test_an_ordinary_yaml_is_not_a_spec_and_not_a_row(tmp_path):
    """A values file is not a loss — rowing it would double-report files other
    extractors own. `openapi:` indented under something is somebody's field, not a
    document declaration."""
    res = run(tmp_path, {"values.yaml": "service:\n  name: invoicing\n",
                         "nested.yaml": "config:\n  openapi: enabled\n"})
    assert res.edges == [] and res.nodes == [] and res.unresolved == []


def test_a_sniffed_file_that_cannot_parse_is_a_loss_and_says_so(tmp_path):
    res = run(tmp_path, {"broken.yaml": "openapi: 3.0.1\npaths:\n\t- tab garbage\n"})
    (row,) = res.unresolved
    assert row["kind"] == "unparsable"
    assert row["error"]
    assert "declares itself an API document" in row["why"]


def test_a_swagger_2_document_counts_too(tmp_path):
    res = run(tmp_path, {"api.yaml": 'swagger: "2.0"\npaths:\n  /v1/x:\n    get: {}\n'})
    assert [e.dst for e in specifies(res)] == ["ep:/v1/x"]


# ── the fixture chain, at the answer key's own lines ──────────────────────────


def test_the_fixture_chain_resolves_at_the_cited_lines(tmp_path):
    rel = "src/main/resources/openapi.yaml"
    text = (FIXTURE / "orders-api" / rel).read_text(encoding="utf-8")
    res = run(tmp_path, {rel: text}, alias="orders-api")
    edges = {e.dst: e for e in specifies(res)}
    assert set(edges) == {"ep:/orders/v1/list", "ep:/orders/v1/{}/history"}
    assert edges["ep:/orders/v1/list"].line == 6, "EXPECTED.md cites openapi.yaml:6"
    assert edges["ep:/orders/v1/{}/history"].line == 12, "EXPECTED.md cites openapi.yaml:12"
    assert edges["ep:/orders/v1/list"].src == \
        ids.config_id("orders-api", rel)
    assert edges["ep:/orders/v1/list"].props["methods"] == ["get"]


@pytest.mark.skipif(not _java.available(), reason="no parser installed for this language")
def test_the_join_is_by_node_id_and_the_decoy_gains_no_exposes(tmp_path):
    """THE deliverable. `ep:/orders/v1/list` carries both edge types — that is what
    "spec-matched" means — and the specified-but-unserved path carries only the spec's
    claim. A spec is intent, not evidence that anything serves it."""
    api = FIXTURE / "orders-api"
    files = {p.relative_to(api).as_posix(): p.read_text(encoding="utf-8")
             for p in api.rglob("*") if p.is_file()}
    root = tmp_path / "orders-api"
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)

    def ctx():
        return ExtractContext(repos=[RepoRef(alias="orders-api", path=root)], run_id="r",
                              files={"orders-api": paths})
    pctx = ctx()
    from tools.graph.extractors import properties as P
    P.PropertiesExtractor().extract(pctx)
    served = spring.ControllersExtractor().extract(
        ExtractContext(repos=[RepoRef(alias="orders-api", path=root)], run_id="r",
                       cache=pctx.cache,
                       files={"orders-api": [p for p in paths if p.suffix == ".java"]}))
    spec = run(tmp_path / "s", files, alias="orders-api")

    exposed = {e.dst for e in served.edges if e.type == "DG_EXPOSES"}
    specified = {e.dst for e in specifies(spec)}
    assert "ep:/orders/v1/list" in exposed & specified, "the spec-matched endpoint"
    assert "ep:/orders/v1/{}/history" in specified - exposed, "the promise nothing keeps"


def test_broken_mapping_yml_is_not_sniffed_and_gains_no_second_row(tmp_path):
    rel = "src/main/resources/broken-mapping.yml"
    text = (FIXTURE / "orders-api" / rel).read_text(encoding="utf-8")
    res = run(tmp_path, {rel: text}, alias="orders-api")
    assert res.unresolved == [], "D6's story is owned elsewhere; a second row is noise"
    assert res.edges == []


# ── the cut: the claim is falsifiable ─────────────────────────────────────────


def test_cutting_the_path_key_kills_exactly_the_spec_hop(tmp_path):
    """Rename the documented path in the spec: the DG_SPECIFIES edge to the served
    endpoint dies, the DG_EXPOSES from the controller survives — a spec cut must never
    take the serving evidence with it."""
    _, edges, _ = build_mutated(FIXTURE, tmp_path,
                                rel="orders-api/src/main/resources/openapi.yaml",
                                old="/orders/v1/list:", new="/orders/v1/list-renamed:")
    spec_edges = [e for e in edges if e["type"] == "DG_SPECIFIES"]
    assert "ep:/orders/v1/list" not in {e["dst"] for e in spec_edges}
    assert "ep:/orders/v1/list-renamed" in {e["dst"] for e in spec_edges}
    assert any(e["type"] == "DG_EXPOSES" and e["dst"] == "ep:/orders/v1/list"
               for e in edges), "the serving evidence must survive a spec cut"


# ── degradation under the zero-dependency gate ────────────────────────────────


def test_json_specs_read_in_full_without_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(props, "yaml", None)
    res = run(tmp_path, {"api.json":
                         '{"openapi": "3.0.1", "paths": {"/v1/x": {"get": {}}}}'})
    assert [e.dst for e in specifies(res)] == ["ep:/v1/x"]
    assert res.unresolved == []


def test_yaml_specs_degrade_one_row_per_component(tmp_path, monkeypatch):
    monkeypatch.setattr(props, "yaml", None)
    res = run(tmp_path, {"a.yaml": SPEC, "b.yaml": SPEC,
                         "plain.yaml": "service:\n  name: x\n"})
    (row,) = res.unresolved
    assert row["kind"] == "grammar-unavailable" and row["language"] == "yaml"
    assert row["files"] == 2, "only the SNIFFED files are a loss; plain yaml is not"
    assert specifies(res) == []


def test_json_line_numbers_cite_the_quoted_key(tmp_path):
    body = '{\n  "openapi": "3.0.1",\n  "paths": {\n    "/v1/x": {"get": {}}\n  }\n}\n'
    res = run(tmp_path, {"api.json": body})
    (edge,) = specifies(res)
    assert edge.line == 4


# ── the row contract and the registry ─────────────────────────────────────────


def test_a_spec_with_no_paths_is_rowed_not_silent(tmp_path):
    res = run(tmp_path, {"fragment.yaml":
                         "openapi: 3.0.1\ncomponents:\n  schemas: {}\n"})
    (row,) = res.unresolved
    assert row["kind"] == "spec-without-paths"


def test_the_package_registers_through_the_real_pipeline():
    regs = {r.name: r for r in B.discover_extractors()}
    assert "openapi-specs" in regs
    assert regs["openapi-specs"].layer == "stack"
    assert regs["openapi-specs"].globs == ("*.yaml", "*.yml", "*.json")
