"""The fixture workspace holds itself to its own rules.

No extractor exists yet, so nothing here checks extraction. What it checks is that the
fixture is **still the thing `EXPECTED.md` describes** — because the failure mode for a
pre-registered answer key is not that it is wrong on day one, it is that the fixture drifts
away from it later, one convenient edit at a time, while every test stays green.

The rules being enforced are the ones in the fixture's own README, and each is here because
breaking it silently degrades the M0 gate rather than failing it:

* the answer key must land BEFORE the extractors, and stay hand-written;
* every anchor `EXPECTED.md` cites must still exist at the line it names;
* the chain must stay cross-component;
* the negative control must stay inert;
* no file may grow past the point where people stop re-reading it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tools.tests import _repo

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"
EXPECTED = FIXTURE / "EXPECTED.md"

COMPONENTS = ["orders-api", "orders-worker", "orders-shared", "web-console",
              "orders-notifier", "deploy-charts", "legacy-reports"]

#: Above this, nobody re-reads the file when a test fails and the fixture rots into a
#: snapshot. Below ~5 components the chain stops being cross-component. Both bounds are
#: from the fixture's README and both are load-bearing.
MAX_FIXTURE_FILE_LINES = 40


def fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURE.rglob("*") if p.is_file())


def component_files(name: str) -> list[Path]:
    return sorted(p for p in (FIXTURE / name).rglob("*") if p.is_file())


# ── the fixture exists and has the declared shape ─────────────────────────────
def test_every_declared_component_is_present():
    missing = [c for c in COMPONENTS if not (FIXTURE / c).is_dir()]
    assert not missing, f"components named in EXPECTED.md but absent: {missing}"


def test_no_undeclared_component_appeared():
    """A seventh component added without an EXPECTED.md entry is invisible to the gate: it
    contributes nodes and edges nothing asserts, so it can be wrong forever."""
    dirs = {p.name for p in FIXTURE.iterdir() if p.is_dir()}
    assert dirs == set(COMPONENTS), f"undeclared: {sorted(dirs - set(COMPONENTS))}"


#: Not subject to the 40-line rule, and each for its own reason. The rule exists so that a
#: failing golden test sends you back to re-read the INPUT; it does not apply to the answer
#: key, the fixture's documentation, or the config — whose length is driven by how many
#: components (seven since 9b) and claimed MECHANISMS (a section per extractor class
#: since 9a/9b/9c) — both numbers the fixture deliberately keeps small.
NOT_EXTRACTION_INPUT = {"EXPECTED.md": 230, "README.md": 200, "workspace.toml": 100}


@pytest.mark.parametrize("path", [p.relative_to(FIXTURE).as_posix() for p in fixture_files()])
def test_no_fixture_file_grows_past_the_readable_limit(path: str):
    """The moment a fixture file is too long to re-read, a failing golden test gets
    'fixed' by regenerating the expectation instead of by reading the input."""
    lines = len((FIXTURE / path).read_text(encoding="utf-8").splitlines())
    limit = NOT_EXTRACTION_INPUT.get(path, MAX_FIXTURE_FILE_LINES)
    assert lines <= limit, f"{path} is {lines} lines (limit {limit})"


def test_the_length_exemptions_are_pinned():
    """Three files, all of them prose or config. A component source file appearing here
    would be the fixture buying itself room to become unreadable."""
    assert set(NOT_EXTRACTION_INPUT) == {"EXPECTED.md", "README.md", "workspace.toml"}


def test_the_workspace_config_is_the_real_shape():
    """The fixture builds through the same `read_workspace` an adopter uses. Reaching the
    components any other way would test a path nobody takes."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("sdlc_init", ROOT / "sdlc-init.py")
    si = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = si
    spec.loader.exec_module(si)

    ws = si.read_workspace(FIXTURE / "workspace.toml")
    assert ws["root"] == FIXTURE.resolve()
    assert [c["service"] for c in ws["canonical"]] == ["orders-api"], \
        "D4's canonical decision must be declared, never inferred"

    found = {c["name"] for c in si.discover_components(ws)["components"]}
    assert found == set(COMPONENTS), f"discovery disagrees with EXPECTED.md: {sorted(found)}"


# ── the answer key is pre-registered, not regenerated ─────────────────────────
def test_the_answer_key_says_it_was_written_first():
    """The single most important property of this file, and the easiest to lose. It is
    asserted so that replacing it with generated output requires deleting this sentence,
    which someone has to do on purpose."""
    text = EXPECTED.read_text(encoding="utf-8")
    assert "before any extractor existed" in text
    assert "Never regenerated" in text


def test_the_answer_key_landed_before_any_extractor():
    """The pre-registration, checked against git rather than trusted.

    If an extractor is ever committed first, the answer key stops being a claim and becomes
    a description — and no test can tell the difference afterwards.
    """
    extractors = ROOT / "tools" / "graph" / "extractors"
    if not extractors.exists():
        return  # nothing to race yet, which is the intended state at M0

    # A shallow checkout does not merely leave this unanswerable — it ANSWERS it, wrongly and
    # in the passing direction. `git clone --depth 1` holds one commit that adds every file,
    # so both timestamps below are that commit's and `key <= ext` is trivially true: the
    # pre-registration would be "verified" by comparing a commit with itself. CI's default
    # checkout depth is 1, so this is not hypothetical.
    assert not _repo.is_shallow(ROOT), _repo.SHALLOW_MESSAGE

    def first_commit(path: Path) -> str:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", "--diff-filter=A", "--format=%ct", "--", str(path)],
            capture_output=True, text=True).stdout.split()
        return out[-1] if out else ""

    key_ts, ext_ts = first_commit(EXPECTED), first_commit(extractors)
    # Silence here used to pass. It cannot: git reporting no adding commit for either path
    # means the ordering is unknown, and unknown is not the same as verified — the same rule
    # the skip gate applies to a skip count it cannot read.
    assert key_ts and ext_ts, (
        "git reported no adding commit for the answer key or for the extractors, so which "
        "landed first cannot be established. Failing rather than assuming the good case.")
    assert int(key_ts) <= int(ext_ts), \
        "EXPECTED.md must be committed no later than the first extractor"


@pytest.mark.parametrize("path,line,needle", [
    ("web-console/src/config/urls.js", 4, "ORDERS_LIST"),
    ("web-console/src/config/urls.js", 5, "ORDERS_DETAIL"),
    ("orders-api/src/main/java/com/example/orderflow/api/OrderController.java", 9, '@RequestMapping("/v1")'),
    ("orders-api/src/main/java/com/example/orderflow/api/OrderController.java", 12, '@GetMapping("/list")'),
    ("orders-api/src/main/java/com/example/orderflow/api/OrderController.java", 17, "/detail/{orderId}"),
    ("orders-api/src/main/java/com/example/orderflow/api/OrderPublisher.java", 9, "orders.publish.topic"),
    ("orders-api/src/main/resources/application.properties", 2, "context-path=/orders"),
    ("orders-api/src/main/resources/application.properties", 5, "${ORDER_TOPIC:order-events-v1}"),
    ("orders-api/src/main/resources/application.properties", 14, "${AUDIT_TOPIC}"),
    ("orders-worker/src/main/resources/application.properties", 5, "orders.consume.topic"),
    ("orders-worker/config/write-mapping.yml", 5, "order-events-v1"),
    ("orders-worker/config/write-mapping.yml", 6, "order_events"),
    ("orders-worker/src/main/resources/db/migration/V1__create_order_events.sql", 2, "CREATE TABLE order_events"),
    ("web-console/src/components/OrderDetail.jsx", 4, "/orders/v1/detail/{id}"),
    # Item 14's three further chains cite these.
    ("orders-api/pom.xml", 6, "<dependency>"),
    ("orders-api/pom.xml", 9, "2.1.0"),
    ("orders-worker/pom.xml", 6, "<dependency>"),
    ("orders-worker/pom.xml", 9, "2.1.0"),
    ("orders-shared/pom.xml", 3, "<artifactId>orders-shared</artifactId>"),
    ("orders-api/src/test/java/com/example/orderflow/api/OrderControllerTest.java", 15, '"/orders/v1/list"'),
    ("orders-api/src/test/java/com/example/orderflow/api/OrderControllerTest.java", 6, "/orders/v1/list"),
    ("deploy-charts/charts/orderflow/values.yaml", 6, "name: orders-api"),
    ("deploy-charts/charts/orderflow/templates/configmap.yaml", 6, "ORDERS_API_HOST"),
    ("deploy-charts/legacy/orders-api/values.yaml", 9, "name: orders-api"),
    # Item 9a's spec chain cites these.
    ("orders-api/src/main/resources/openapi.yaml", 6, "/orders/v1/list:"),
    ("orders-api/src/main/resources/openapi.yaml", 12, "/orders/v1/{orderId}/history:"),
    # Item 9b's module chain cites these; :6 is the line that must produce NOTHING.
    ("orders-notifier/build.gradle", 1, "group = 'com.example.orderflow'"),
    ("orders-notifier/build.gradle", 5, "com.example.orderflow:orders-shared:2.1.0"),
    ("orders-notifier/build.gradle", 6, "com.example.orderflow:notifier-core"),
    ("orders-notifier/settings.gradle", 2, "include ':notifier-core'"),
    ("orders-notifier/notifier-core/build.gradle", 2, "org.apache.kafka:kafka-clients:3.7.0"),
    # Item 9c's dependency layer cites these; :11 is the line that must produce NOTHING.
    ("web-console/package.json", 2, '"name": "web-console"'),
    ("web-console/package.json", 11, '"@orderflow/ui-kit"'),
    ("web-console/package.json", 14, '"jest": "29.7.0"'),
    ("web-console/packages/ui-kit/package.json", 2, '"name": "@orderflow/ui-kit"'),
    ("web-console/packages/ui-kit/package.json", 5, '"date-fns": "^3.6.0"'),
])
def test_every_anchor_the_answer_key_cites_is_still_there(path: str, line: int, needle: str):
    """`EXPECTED.md` asserts `file:line`, so provenance is part of the claim — a graph that
    points at the wrong line is wrong in a way nothing notices unless someone checks the
    number. Inserting a line above any of these silently invalidates the key."""
    lines = (FIXTURE / path).read_text(encoding="utf-8").splitlines()
    assert len(lines) >= line, f"{path} has only {len(lines)} lines; key cites {line}"
    assert needle in lines[line - 1], \
        f"{path}:{line} is {lines[line - 1]!r}, not the {needle!r} the answer key cites"


# ── the chain stays cross-component ───────────────────────────────────────────
def test_the_topic_is_produced_and_consumed_under_different_property_names():
    """The hop most likely to be 'simplified' into uselessness. Matching producers to
    consumers by KEY NAME works here only if the names differ — make them the same and the
    fixture stops testing the thing that is actually hard in real workspaces."""
    api = (FIXTURE / "orders-api/src/main/resources/application.properties").read_text(encoding="utf-8")
    worker = (FIXTURE / "orders-worker/src/main/resources/application.properties").read_text(encoding="utf-8")
    assert "orders.publish.topic" in api and "orders.consume.topic" in worker
    assert "orders.consume.topic" not in api, "the property names must NOT match"
    assert "order-events-v1" in api and "order-events-v1" in worker, "the VALUES must match"


def test_the_chain_spans_at_least_four_components():
    """Three would fit inside one repo's build and stop exercising the thing the graph is
    for. The chain is web-console -> orders-api -> topic -> orders-worker -> table."""
    hops = {"web-console", "orders-api", "orders-worker"}
    assert hops <= set(COMPONENTS)
    text = EXPECTED.read_text(encoding="utf-8")
    for c in hops:
        assert c in text, f"{c} is a chain hop but is not named in the answer key"


def test_the_shared_library_is_depended_on_by_two_components():
    """A shared-lib change must reach every dependent. One dependent proves nothing."""
    dependents = [c for c in ("orders-api", "orders-worker")
                  if "orders-shared" in (FIXTURE / c / "pom.xml").read_text(encoding="utf-8")]
    assert dependents == ["orders-api", "orders-worker"]


# ── P5: the negative control stays inert ──────────────────────────────────────
@pytest.mark.parametrize("shared", ["order-events-v1", "order_events", "orders-shared",
                                    "/orders/v1/", "inventory"])
def test_the_negative_control_shares_nothing(shared: str):
    """P5. If deleting an inert component changes any edge between the others, the
    extractor is inventing links from proximity — the failure that makes a graph plausible
    and useless, because every query returns something and nothing returned is
    trustworthy."""
    for path in component_files("legacy-reports"):
        body = path.read_text(encoding="utf-8", errors="replace")
        assert shared not in body, \
            f"the negative control references {shared!r} in {path.name} — it is no longer inert"


def test_the_negative_control_is_still_a_real_component():
    """Inert, not empty. A directory with nothing in it proves nothing about over-linking:
    it has to look like a component the extractor would happily process."""
    files = {p.name for p in component_files("legacy-reports")}
    assert "pom.xml" in files and "application.properties" in files


# ── the decoys are all still present ──────────────────────────────────────────
@pytest.mark.parametrize("decoy,path,needle", [
    ("D1 norm_path", "web-console/src/components/OrderDetail.jsx", "{id}"),
    ("D2 relaxed_key", "orders-api/src/main/resources/application.properties", "ORDERS_PUBLISH_TOPIC"),
    ("D3 interpolate", "orders-api/src/main/resources/application.properties", "${ORDER_TOPIC:"),
    ("D4 canonical", "deploy-charts/legacy/orders-api/values.yaml", "orders-api"),
    ("D5 precision", "orders-api/src/test/java/com/example/orderflow/api/OrderControllerTest.java", "/orders/v1/list"),
    ("D6 fail-loud", "orders-api/src/main/resources/broken-mapping.yml", "unbalanced"),
    ("D7 zero drops", "orders-worker/config/orphan-mapping.yml", "shipment-events-v1"),
    ("D8 no default", "orders-api/src/main/resources/application.properties", "${AUDIT_TOPIC}"),
])
def test_each_decoy_is_present(decoy: str, path: str, needle: str):
    """A decoy quietly removed takes its precision check with it, and the gate keeps
    passing — measuring recall only, while still calling itself a gate."""
    assert needle in (FIXTURE / path).read_text(encoding="utf-8"), f"{decoy} is gone"


def test_every_decoy_names_the_contract_it_exercises():
    """A decoy with no named contract is decoration: when it fails, nobody knows what it
    was defending or whether the extractor or the fixture is at fault."""
    text = EXPECTED.read_text(encoding="utf-8")
    for i in range(1, 9):
        assert f"**D{i}**" in text, f"D{i} has no row in the answer key"
    for contract in ("ids.norm_path", "ids.relaxed_key", "props.interpolate",
                     "canonical resolution", "precision", "fail-loud", "zero silent drops"):
        assert contract in text, f"no decoy names the {contract!r} contract"


def test_the_orphan_topic_is_produced_by_nobody():
    """D7 only tests 'zero silent drops' while nothing in the workspace produces the topic.
    Add a producer and the decoy quietly becomes an ordinary edge."""
    prose = {"orphan-mapping.yml", "EXPECTED.md", "README.md"}
    producers = [p for p in fixture_files()
                 if p.name not in prose
                 and "shipment-events-v1" in p.read_text(encoding="utf-8", errors="replace")]
    assert not producers, f"shipment-events-v1 is no longer orphaned: {producers}"


# ── P6: no exemption, and no collection ───────────────────────────────────────
def test_the_fixture_needs_no_leak_guard_exemption():
    """P6, a free forcing function on the naming: a fixture that needed an exemption would
    be using a name too close to a real one."""
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.tests import _scan

    banned = _scan.load_banned(ROOT / "tools" / "tests" / "banned_tokens.txt")
    offenders = {}
    for path in fixture_files():
        hits = _scan.scan_file(path, banned)
        if hits:
            offenders[path.relative_to(FIXTURE).as_posix()] = [h.evidence for h in hits]
    assert not offenders, f"fixture trips the leak guard: {offenders}"


def test_pytest_does_not_collect_the_fixture_tree():
    """`fixtures/` holds workspaces, not test code. A fixture's own `conftest.py` collected
    for real would override this directory's for every test in it — a failure that looks
    like the suite, not like the fixture."""
    from . import conftest  # noqa: F401  (import for its side-effect-free constant)

    assert any("fixtures" in g for g in conftest.collect_ignore_glob)
