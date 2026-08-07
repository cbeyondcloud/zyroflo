"""The Python source extractors: routes assembled from decorators, and environment reads.

The counterparts of the Java source extractors, testing the same contracts through
completely different syntax — which is the point of the second fixture.

Everything here is synthetic.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import compose, env_keys, http_routes  # noqa: E402

ROUTES = http_routes.HttpRoutesExtractor()
ENV = env_keys.EnvKeysExtractor()


def ctx_for(tmp_path: Path, files: dict[str, str], alias: str = "svc",
            cache: dict | None = None) -> ExtractContext:
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          files={alias: paths}, cache=cache or {})


def edges(res, type_):
    return [e for e in res.edges if e.type == type_]


# ── http_routes: the endpoint is ASSEMBLED, never the decorator alone ─────────

APP = '''\
from fastapi import APIRouter

router = APIRouter(prefix="/pantry/v1")


@router.get("/items")
async def list_items():
    return []
'''


def test_the_prefix_and_the_decorator_path_are_concatenated(tmp_path: Path):
    res = ROUTES.extract(ctx_for(tmp_path, {"routes.py": APP}))
    assert [e.dst for e in edges(res, "DG_EXPOSES")] == [ids.endpoint_id("/pantry/v1/items")]


def test_the_decorator_path_alone_is_never_the_endpoint(tmp_path: Path):
    """The failure this extractor exists to prevent. `/items` is what the decorator says and
    what no caller ever requests — the graph would hold endpoints nobody calls beside calls
    nobody serves, and BOTH halves look like findings."""
    res = ROUTES.extract(ctx_for(tmp_path, {"routes.py": APP}))
    assert ids.endpoint_id("/items") not in {e.dst for e in edges(res, "DG_EXPOSES")}


@pytest.mark.parametrize("var", ["{sku}", "{item_sku}", "{id}"])
def test_a_path_variable_normalises_however_it_is_spelled(tmp_path: Path, var: str):
    """E1. Two spellings of one path must be ONE node, or every impact query on this
    endpoint is split in half."""
    body = APP.replace('"/items"', f'"/items/{var}"')
    res = ROUTES.extract(ctx_for(tmp_path, {"routes.py": body}))
    assert [e.dst for e in edges(res, "DG_EXPOSES")] == [
        ids.endpoint_id("/pantry/v1/items/{}")]


def test_the_cited_line_is_the_decorators_not_the_functions(tmp_path: Path):
    """Where a human edits the path. Provenance one line off is wrong in a way nothing
    notices unless someone checks it."""
    res = ROUTES.extract(ctx_for(tmp_path, {"routes.py": APP}))
    assert [e.line for e in edges(res, "DG_EXPOSES")] == [6]


def test_a_router_with_no_prefix_still_routes(tmp_path: Path):
    body = 'from fastapi import APIRouter\nrouter = APIRouter()\n\n\n@router.get("/health")\ndef h():\n    return 1\n'
    res = ROUTES.extract(ctx_for(tmp_path, {"routes.py": body}))
    assert [e.dst for e in edges(res, "DG_EXPOSES")] == [ids.endpoint_id("/health")]


def test_a_decorator_that_is_not_a_route_is_ignored(tmp_path: Path):
    body = APP.replace('@router.get("/items")', '@router.on_event("startup")')
    res = ROUTES.extract(ctx_for(tmp_path, {"routes.py": body}))
    assert edges(res, "DG_EXPOSES") == []


def test_a_non_literal_path_is_skipped_rather_than_guessed(tmp_path: Path):
    body = APP.replace('@router.get("/items")', '@router.get(ITEMS_PATH)')
    res = ROUTES.extract(ctx_for(tmp_path, {"routes.py": body}))
    assert edges(res, "DG_EXPOSES") == []


def test_a_test_file_never_becomes_an_endpoint(tmp_path: Path):
    """E5's half of this extractor. A path named in a fixture is not a route this service
    serves, and an invented endpoint appears in every blast radius forever."""
    res = ROUTES.extract(ctx_for(tmp_path, {"test_routes.py": APP}))
    assert edges(res, "DG_EXPOSES") == []


def test_a_file_that_will_not_parse_is_recorded_not_fatal(tmp_path: Path):
    res = ROUTES.extract(ctx_for(tmp_path, {"routes.py": APP, "bad.py": "def (:\n"}))
    assert [r["kind"] for r in res.unresolved] == ["python-unparsed"]
    assert edges(res, "DG_EXPOSES"), "the readable file must still produce its endpoint"


def test_include_router_is_not_handled_and_that_is_recorded(tmp_path: Path):
    """Pins a stated limitation rather than an accident. A second prefix layer exists in the
    real framework and in no fixture here; building it would mean asserting nothing about an
    imagined input. If this test starts failing, the feature arrived — update it deliberately.
    """
    body = APP + '\napp = APIRouter(prefix="/api")\napp.include_router(router)\n'
    res = ROUTES.extract(ctx_for(tmp_path, {"routes.py": body}))
    assert [e.dst for e in edges(res, "DG_EXPOSES")] == [ids.endpoint_id("/pantry/v1/items")]


# ── env_keys: two sources, and the difference between them is the layering ───


def _compose_cache(alias: str, key: str, value: str) -> dict:
    return {compose.CACHE_KEY: {alias: {"values": {ids.relaxed_key(key): {
        "value": value, "file": "docker-compose.yml", "line": 1, "raw": value,
        "spellings": {}}}, "unresolved": {}}}}


def test_an_inline_default_is_a_value_this_component_declares(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"s.py": 'import os\nQ = os.getenv("QUEUE_KEY", "events-v2")\n'})
    ENV.extract(ctx)
    view = env_keys.view_for(ctx.cache, "svc")
    assert view["values"][ids.relaxed_key("QUEUE_KEY")]["value"] == "events-v2"


def test_environ_get_with_a_default_is_read_the_same_way(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"s.py": 'import os\nQ = os.environ.get("QUEUE_KEY", "e2")\n'})
    ENV.extract(ctx)
    assert env_keys.view_for(ctx.cache, "svc")["values"][ids.relaxed_key("QUEUE_KEY")][
        "value"] == "e2"


def test_an_empty_string_default_is_a_real_value_not_an_absent_one(tmp_path: Path):
    """`""` is a value somebody chose. Collapsing it into "no default" turns "this must be
    configured" into "this defaults to nothing", and nothing downstream could tell."""
    ctx = ctx_for(tmp_path, {"s.py": 'import os\nQ = os.getenv("QUEUE_KEY", "")\n'})
    res = ENV.extract(ctx)
    assert env_keys.view_for(ctx.cache, "svc")["values"][ids.relaxed_key("QUEUE_KEY")][
        "value"] == ""
    assert res.unresolved == []


def test_a_key_with_no_default_that_the_deployment_sets_is_not_a_hole(tmp_path: Path):
    """The REQUIRES ordering earning its place. Without it every deployment-supplied key is
    reported missing, and a report whose real holes are buried in false ones is one nobody
    reads twice."""
    ctx = ctx_for(tmp_path, {"s.py": 'import os\nQ = os.environ["QUEUE_KEY"]\n'},
                  cache=_compose_cache("svc", "QUEUE_KEY", "events-v2"))
    res = ENV.extract(ctx)
    assert res.unresolved == []


def test_a_key_nothing_sets_anywhere_is_reported(tmp_path: Path):
    """E7. A key read with no value is a real hole in the deployment, and dropping it hides
    exactly the thing worth knowing."""
    ctx = ctx_for(tmp_path, {"s.py": 'import os\nQ = os.environ["LEDGER_ENDPOINT"]\n'},
                  cache=_compose_cache("svc", "OTHER", "x"))
    res = ENV.extract(ctx)
    assert [r["kind"] for r in res.unresolved] == ["env-key-unset"]
    assert "nothing sets it" in res.unresolved[0]["why"]


def test_an_unread_deployment_layer_says_so_instead_of_claiming_a_hole(tmp_path: Path):
    """`--only` can exclude the deployment reader. "Nothing sets this key" and "the
    deployment layer was not read" produce the same emptiness and mean entirely different
    things; reporting the first when the second is true sends someone hunting a hole that
    is not there."""
    ctx = ctx_for(tmp_path, {"s.py": 'import os\nQ = os.environ["QUEUE_KEY"]\n'})
    res = ENV.extract(ctx)
    assert "was not read in this build" in res.unresolved[0]["why"]


@pytest.mark.parametrize("spelling", ["QUEUE_KEY", "queue.key", "queue-key"])
def test_spellings_of_one_key_collapse_to_one_node(tmp_path: Path, spelling: str):
    ctx = ctx_for(tmp_path, {"s.py": f'import os\nQ = os.getenv("{spelling}", "v")\n'})
    res = ENV.extract(ctx)
    assert [n.id for n in res.nodes] == [ids.env_key_id("QUEUE_KEY")]


def test_a_test_file_is_not_read_for_configuration(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"test_s.py": 'import os\nQ = os.environ["K"]\n'})
    res = ENV.extract(ctx)
    assert res.nodes == [] and res.unresolved == []


def test_a_file_that_will_not_parse_is_recorded_not_fatal(tmp_path: Path):
    ctx = ctx_for(tmp_path, {"bad.py": "def (:\n"})
    res = ENV.extract(ctx)
    assert [r["kind"] for r in res.unresolved] == ["python-unparsed"]


def test_no_service_to_key_edge_is_invented(tmp_path: Path):
    """"Which services read this key" is a real blast-radius question needing an edge type
    that does not exist, whose only consumer is the M2 impact engine. Inventing it now would
    be designing against an imagined query."""
    ctx = ctx_for(tmp_path, {"s.py": 'import os\nQ = os.getenv("K", "v")\n'})
    assert ENV.extract(ctx).edges == []


# ── bindings: shared code, not a shared cache entry ──────────────────────────


def test_bindings_map_a_module_name_back_to_its_key(tmp_path: Path):
    """A later extractor meeting `producer.produce(QUEUE, ...)` has to get from that name to
    the key. It reads the AST it already parsed, so there is one implementation of "what
    does this name hold" rather than a second contract to keep in step."""
    tree = ast.parse('import os\nQUEUE = os.environ["RESTOCK_QUEUE"]\nOTHER = 1\n')
    assert env_keys.bindings(tree) == {"QUEUE": "RESTOCK_QUEUE"}


def test_a_name_bound_to_something_other_than_an_env_read_is_not_a_binding(tmp_path: Path):
    tree = ast.parse('QUEUE = "literal"\n')
    assert env_keys.bindings(tree) == {}
