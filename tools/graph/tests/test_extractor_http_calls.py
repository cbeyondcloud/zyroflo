"""Outbound calls paired from a configured host and a path literal.

The half that makes the routes extractor worth anything: an endpoint nobody calls and a call
nobody serves produce the same graph, and only the join says which one you are looking at.

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
from tools.graph.extractors import compose, env_keys, http_calls  # noqa: E402

CALLS = http_calls.HttpCallsExtractor()

CLIENT = '''\
import os

import httpx

API_BASE = os.environ["INVENTORY_URL"]

ITEMS_PATH = "/inventory/v1/items"


def fetch() -> list[dict]:
    return httpx.get(f"{API_BASE}{ITEMS_PATH}").json()
'''


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


def deployed(alias: str, key: str, value: str, line: int = 7) -> dict:
    """A resolved view as the deployment reader publishes it."""
    return {compose.CACHE_KEY: {alias: {"values": {ids.relaxed_key(key): {
        "value": value, "file": "docker-compose.yml", "line": line, "raw": value,
        "spellings": {}}}, "unresolved": {}}}}


def calls(res):
    return [e for e in res.edges if e.type == "DG_CALLS"]


# ── the pairing, and the three coordinates it must carry ─────────────────────


def test_a_configured_host_and_a_path_literal_make_one_call(tmp_path: Path):
    res = CALLS.extract(ctx_for(tmp_path, {"client.py": CLIENT},
                                cache=deployed("svc", "INVENTORY_URL", "http://inv:8000")))
    assert [e.dst for e in calls(res)] == [ids.endpoint_id("/inventory/v1/items")]


def test_neither_half_alone_is_a_call(tmp_path: Path):
    """The claim the whole extractor rests on. A host with no path and a path with no host
    are both silent; only together do they mean anything."""
    host_only = 'import os\nAPI_BASE = os.environ["INVENTORY_URL"]\n'
    path_only = 'ITEMS_PATH = "/inventory/v1/items"\n'
    cache = deployed("svc", "INVENTORY_URL", "http://inv:8000")
    assert calls(CALLS.extract(ctx_for(tmp_path, {"a.py": host_only}, cache=cache))) == []
    assert calls(CALLS.extract(ctx_for(tmp_path / "b", {"b.py": path_only}, cache=cache))) == []


def test_the_cited_line_is_the_paths_not_the_hosts(tmp_path: Path):
    """Where a human edits which endpoint is called. Provenance pointing at a plausible
    wrong line is wrong in the way nothing notices."""
    res = CALLS.extract(ctx_for(tmp_path, {"client.py": CLIENT},
                                cache=deployed("svc", "INVENTORY_URL", "http://inv:8000")))
    assert [e.line for e in calls(res)] == [7]


def test_all_three_coordinates_of_the_chain_are_recorded(tmp_path: Path):
    """The path, the read, and the value live in three places — two of them in different
    files and one in a different component. A reader landing on any one needs the others,
    and an edge citing only the far end leaves them to find the near end themselves."""
    res = CALLS.extract(ctx_for(tmp_path, {"client.py": CLIENT},
                                cache=deployed("svc", "INVENTORY_URL", "http://inv:8000", 12)))
    props = calls(res)[0].props
    assert calls(res)[0].line == 7                      # the path literal
    assert props["key_line"] == 5                       # where this module reads the address
    assert (props["host_file"], props["host_line"]) == ("docker-compose.yml", 12)


def test_the_pairing_is_scored_as_an_inference_not_a_fact(tmp_path: Path):
    """The two halves are joined because they share a module, not because anything links
    them. Adjacency is usually right and never certain, and the edge says so."""
    res = CALLS.extract(ctx_for(tmp_path, {"client.py": CLIENT},
                                cache=deployed("svc", "INVENTORY_URL", "http://inv:8000")))
    assert calls(res)[0].confidence == 0.9
    assert calls(res)[0].provenance.value == "INFERRED"


@pytest.mark.parametrize("var", ["{sku}", "{item_sku}", "{id}"])
def test_a_path_variable_normalises_however_it_is_spelled(tmp_path: Path, var: str):
    """E1, from the calling side. The server and the caller name the same variable
    differently; two nodes would split every impact query on this endpoint in half."""
    body = CLIENT.replace('"/inventory/v1/items"', f'"/inventory/v1/items/{var}"')
    res = CALLS.extract(ctx_for(tmp_path, {"client.py": body},
                                cache=deployed("svc", "INVENTORY_URL", "http://inv:8000")))
    assert [e.dst for e in calls(res)] == [ids.endpoint_id("/inventory/v1/items/{}")]


def test_a_host_from_an_inline_default_resolves_the_same_way(tmp_path: Path):
    """The other configuration layer. Which layer supplied the value is not the caller's
    concern — that is exactly what the shared resolver exists to hide."""
    body = ('import os\nAPI_BASE = os.getenv("INVENTORY_URL", "http://inv:8000")\n'
            'ITEMS_PATH = "/inventory/v1/items"\n')
    ctx = ctx_for(tmp_path, {"client.py": body})
    env_keys.EnvKeysExtractor().extract(ctx)          # publishes the inline-default view
    assert [e.dst for e in calls(CALLS.extract(ctx))] == [
        ids.endpoint_id("/inventory/v1/items")]


# ── what counts as a host, decided from the value and never from the name ────


@pytest.mark.parametrize("value", ["http://inv:8000", "https://inv.example", "inv:8000",
                                   "inventory-api"])
def test_these_values_address_an_http_service(value: str):
    assert http_calls.looks_like_host(value)


@pytest.mark.parametrize("value", ["postgresql://db:5432/pantry", "/dev/usb/lp0", "",
                                   "  ", "events-v2 and more", "amqp://broker"])
def test_these_values_do_not(value: str):
    """`/dev/usb/lp0` is the negative control component's own configured value, and
    `postgresql://...` is a real DSN one component over. Both would put a non-HTTP thing on
    the receiving end of a call edge, which looks exactly as correct as a real one."""
    assert not http_calls.looks_like_host(value)


def test_a_database_dsn_beside_a_path_does_not_become_a_call(tmp_path: Path):
    """The name says URL, the value says database. Deciding from the name is reading intent
    out of a naming convention — the guess this workspace refuses everywhere else."""
    body = ('import os\nDB_URL = os.environ["PANTRY_DB_URL"]\n'
            'ITEMS_PATH = "/inventory/v1/items"\n')
    res = CALLS.extract(ctx_for(tmp_path, {"s.py": body},
                                cache=deployed("svc", "PANTRY_DB_URL",
                                               "postgresql://db:5432/x")))
    assert calls(res) == []


# ── refusals: the cases where emitting nothing is the correct answer ─────────


def test_two_resolvable_hosts_produce_no_edges_and_an_ambiguity(tmp_path: Path):
    """Which path is fetched from which host is a dataflow question. Picking the first
    would produce edges indistinguishable from correct ones, which is worse than none."""
    body = ('import os\nA = os.environ["ONE_URL"]\nB = os.environ["TWO_URL"]\n'
            'ITEMS_PATH = "/inventory/v1/items"\n')
    cache = deployed("svc", "ONE_URL", "http://one:8000")
    cache[compose.CACHE_KEY]["svc"]["values"][ids.relaxed_key("TWO_URL")] = {
        "value": "http://two:8000", "file": "docker-compose.yml", "line": 9,
        "raw": "http://two:8000", "spellings": {}}
    res = CALLS.extract(ctx_for(tmp_path, {"s.py": body}, cache=cache))
    assert calls(res) == []
    assert [r["kind"] for r in res.unresolved] == ["ambiguous-call-host"]


def test_paths_with_no_resolvable_address_are_reported(tmp_path: Path):
    """This module has said it intends to call something and the address is missing. That
    is a hole in the deployment, and dropping it hides the thing worth knowing."""
    res = CALLS.extract(ctx_for(tmp_path, {"client.py": CLIENT}))
    assert calls(res) == []
    assert [r["kind"] for r in res.unresolved] == ["call-host-unresolved"]
    assert res.unresolved[0]["paths"] == ["/inventory/v1/items"]


def test_a_path_literal_with_no_configured_address_is_silent(tmp_path: Path):
    """The deliberate asymmetry with the test above. Path-shaped constants are ordinary —
    filesystem paths, URL fragments, format strings — and one report per occurrence trains
    its reader to skip the report, which costs more than the rows are worth."""
    res = CALLS.extract(ctx_for(tmp_path, {"s.py": 'ITEMS_PATH = "/inventory/v1/items"\n'}))
    assert calls(res) == [] and res.unresolved == []


def test_a_path_built_inside_a_function_is_not_read(tmp_path: Path):
    """Pins a stated limitation rather than an accident. Attributing it needs dataflow, and
    half-followed dataflow yields confident wrong answers rather than missing ones. If this
    starts failing the feature arrived — update it deliberately."""
    body = ('import os\nAPI_BASE = os.environ["INVENTORY_URL"]\n\n\n'
            'def fetch():\n    return API_BASE + "/inventory/v1/items"\n')
    res = CALLS.extract(ctx_for(tmp_path, {"s.py": body},
                                cache=deployed("svc", "INVENTORY_URL", "http://inv:8000")))
    assert calls(res) == []


def test_a_test_file_never_becomes_a_caller(tmp_path: Path):
    """E5's half of this extractor. A phantom caller, once created, arrives in every blast
    radius for that endpoint forever."""
    res = CALLS.extract(ctx_for(tmp_path, {"test_client.py": CLIENT},
                                cache=deployed("svc", "INVENTORY_URL", "http://inv:8000")))
    assert calls(res) == [] and res.unresolved == []


def test_a_file_that_will_not_parse_is_recorded_not_fatal(tmp_path: Path):
    res = CALLS.extract(ctx_for(tmp_path, {"client.py": CLIENT, "bad.py": "def (:\n"},
                                cache=deployed("svc", "INVENTORY_URL", "http://inv:8000")))
    assert [r["kind"] for r in res.unresolved] == ["python-unparsed"]
    assert calls(res), "the readable file must still produce its call"


# ── the shared resolver, which exists so this is not implemented twice ───────


def test_the_resolver_reads_either_layer(tmp_path: Path):
    cache = deployed("svc", "INVENTORY_URL", "http://inv:8000")
    assert env_keys.resolve(cache, "svc", "inventory.url")["value"] == "http://inv:8000"
    assert env_keys.resolve(cache, "svc", "INVENTORY_URL")["value"] == "http://inv:8000"
    assert env_keys.resolve(cache, "other", "INVENTORY_URL") is None
    assert env_keys.resolve({}, "svc", "INVENTORY_URL") is None


def test_binding_sites_carry_the_line_and_bindings_still_do_not(tmp_path: Path):
    """`bindings` is the older, narrower view and its contract does not change — a consumer
    that only wants the key should not have to learn a richer shape to keep working."""
    tree = ast.parse('import os\nQ = os.environ["RESTOCK_QUEUE"]\n')
    assert env_keys.bindings(tree) == {"Q": "RESTOCK_QUEUE"}
    assert env_keys.binding_sites(tree) == {"Q": {"key": "RESTOCK_QUEUE", "line": 2}}
