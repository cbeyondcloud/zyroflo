"""Item 90: the resolved view no longer merges scopes it cannot merge.

Item 89 stopped a component-wide merge from being *reported* as a conflict; this item
stops it from being *believed* as a value. Before it, `ctx.cache["properties"][alias]`
was one flat namespace per component: two applications sharing a repository and both
declaring `server.port` left ONE entry — whichever file sorted last — and every consumer
got a confident answer that was wrong for one of them, with provenance pointing at a real
line in a real file.

Measured on the same workspace as item 89 before any of this was designed: 618 of 9,777
resolved keys were declared in more than one scope with differing values; of the 219
graph edges that resolved a configuration key, 10 cited one of the 618 and **7 were
wrong** — one application's consumer attached to an unrelated application's topic, another
reading a sibling deployment's variant of its own. Every one was corrected by asking-file
affinity with zero ties, interpolation was measured to never cross a scope boundary in
either direction, and 46 host/path call pairs existed in no single scope at all.

The settled principle, from the plan: **a consumer must never receive one arbitrary
answer where two exist.** Every test here forces its condition from literal file
contents; nothing is asserted about a real workspace and no threshold is tuned.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import _java, kafka, spring  # noqa: E402
from tools.graph.extractors import properties as P  # noqa: E402

needs_java = pytest.mark.skipif(not _java.available(),
                                reason="no parser installed for this language")


# ── helpers: literal files through the real extractor ─────────────────────────


def write_tree(tmp_path: Path, files: dict[str, str], alias: str = "svc") -> Path:
    root = tmp_path / alias
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def view_of(tmp_path: Path, files: dict[str, str], alias: str = "svc"):
    root = write_tree(tmp_path, files, alias)
    paths = [root / rel for rel in files]
    ctx = ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                         files={alias: paths})
    res = P.PropertiesExtractor().extract(ctx)
    return ctx.cache[P.CACHE_KEY][alias], res


def consumer_ctx(tmp_path: Path, files: dict[str, str], alias: str = "svc"):
    """A context whose cache holds the real resolved view and whose inputs are the
    non-properties files — the exact hand-off the pipeline performs."""
    root = write_tree(tmp_path, files, alias)
    props = [root / rel for rel in files if rel.endswith(".properties")]
    rest = [root / rel for rel in files if not rel.endswith(".properties")]
    pctx = ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          files={alias: props})
    P.PropertiesExtractor().extract(pctx)
    return ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          cache=pctx.cache, files={alias: rest})


#: Two applications in one repository, each declaring the same keys for itself. The
#: billing tree sorts FIRST, so under the old flat merge the orders tree's declarations
#: won both keys — a test that used the winner's own scope would pass by accident.
TWO_APPS = {
    "backend/billing-app/src/main/resources/application.properties":
        "events.audit.topic=audit-billing\nserver.servlet.context-path=/billing\n",
    "backend/orders-app/src/main/resources/application.properties":
        "events.audit.topic=audit-orders\nserver.servlet.context-path=/orders\n",
}


# ── the view: a contested key leaves `values` ─────────────────────────────────


def test_a_key_two_scopes_answer_differently_is_not_in_values(tmp_path: Path):
    view, _ = view_of(tmp_path, TWO_APPS)
    assert "events.audit.topic" not in view["values"]
    assert "events.audit.topic" in view["ambiguous"]


def test_lookup_without_an_asking_file_returns_none_for_it(tmp_path: Path):
    """The settled principle, verbatim: never one arbitrary answer where two exist. The
    old view returned whichever file sorted last, silently."""
    view, _ = view_of(tmp_path, TWO_APPS)
    assert P.lookup(view, "events.audit.topic") is None


def test_the_disagreement_is_recorded_not_dropped(tmp_path: Path):
    view, _ = view_of(tmp_path, TWO_APPS)
    contested = P.ambiguity_of(view, "EVENTS_AUDIT_TOPIC")   # any spelling, relaxed
    assert contested is not None
    assert contested["values"] == ["audit-billing", "audit-orders"]
    assert len(contested["scopes"]) == 2


def test_scopes_that_agree_keep_their_key_in_values(tmp_path: Path):
    """Agreement is the measured common case (2,133 of 2,751 multi-scope keys) and it
    stays exactly where it was, first-declaration provenance included."""
    files = {"one/app.properties": "shared.flag=on\n",
             "two/app.properties": "shared.flag=on\n"}
    view, _ = view_of(tmp_path, files)
    assert view["ambiguous"] == {}
    assert view["values"]["shared.flag"]["value"] == "on"
    assert view["values"]["shared.flag"]["file"] == "one/app.properties"


# ── the asking file selects a scope ───────────────────────────────────────────


def test_the_asking_file_selects_its_own_scope(tmp_path: Path):
    view, _ = view_of(tmp_path, TWO_APPS)
    for app, topic in (("billing-app", "audit-billing"), ("orders-app", "audit-orders")):
        hit = P.lookup(view, "events.audit.topic",
                       f"backend/{app}/src/main/java/a/b/Listener.java")
        assert hit is not None
        assert hit["value"] == topic
        assert app in hit["file"]


def test_a_file_in_neither_scope_selects_nothing(tmp_path: Path):
    """Depth ties between disagreeing scopes answer nothing. On the measured estate this
    retired 714 endpoint prefixes borrowed from other applications' configuration."""
    view, _ = view_of(tmp_path, TWO_APPS)
    assert P.lookup(view, "events.audit.topic", "tools/Standalone.java") is None


def test_tied_scopes_that_agree_still_answer(tmp_path: Path):
    files = {"x/a/app.properties": "k=same\n",
             "x/b/app.properties": "k=same\n",
             "y/c/app.properties": "k=other\n"}
    view, _ = view_of(tmp_path, files)
    hit = P.lookup(view, "k", "x/lib/F.java")
    assert hit is not None and hit["value"] == "same"
    assert P.lookup(view, "k", "q/F.java") is None


def test_resolve_reference_passes_the_asking_file_through(tmp_path: Path):
    view, _ = view_of(tmp_path, TWO_APPS)
    hit = P.resolve_reference(view, "${events.audit.topic}",
                              "backend/billing-app/src/main/java/a/b/L.java")
    assert hit is not None and hit["value"] == "audit-billing"
    assert P.resolve_reference(view, "${events.audit.topic}") is None


def test_an_inline_default_still_answers_when_no_scope_can(tmp_path: Path):
    """When the configuration cannot decide, the read site's own default is the one
    answer that is nobody's guess."""
    view, _ = view_of(tmp_path, TWO_APPS)
    hit = P.resolve_reference(view, "${events.audit.topic:fallback}", "tools/S.java")
    assert hit is not None and hit["value"] == "fallback"


# ── resolution is scope-honest even where no scope disagrees ──────────────────


def test_interpolation_cannot_cross_a_scope_boundary(tmp_path: Path):
    """`t=${base.host}` in one scope, `base.host` declared only in another: the old flat
    merge resolved `t` from a file its application never loads. Now `t` is unresolved,
    with the scope's own record saying what is missing."""
    files = {"one/app.properties": "t=${base.host}\n",
             "two/app.properties": "base.host=http://real\n"}
    view, res = view_of(tmp_path, files)
    assert "t" not in view["values"]
    assert view["unresolved"]["t"]["missing"] == ["base.host"]
    assert any(r["kind"] == "placeholder" and r["key"] == "t" for r in res.unresolved)
    # the declaring scope's own key is untouched by its neighbour's reference
    assert view["values"]["base.host"]["value"] == "http://real"


@pytest.mark.parametrize("resolving_dir, deferring_dir", [("a", "z"), ("z", "a")],
                         ids=("resolving-sorts-first", "resolving-sorts-last"))
def test_a_scope_resolving_what_another_defers_wins_either_sort_order(
        tmp_path: Path, resolving_dir: str, deferring_dir: str):
    """One scope says `k=real`, another says `k=${DEPLOY_ONLY}` (no default — deferred to
    deployment). Deferring is not disagreeing, so the known value stands — and which file
    sorts last must not decide the doctrine, which under the old merge it did."""
    files = {f"{resolving_dir}/app.properties": "k=real\n",
             f"{deferring_dir}/app.properties": "k=${DEPLOY_ONLY}\n"}
    view, _ = view_of(tmp_path, files)
    assert view["values"]["k"]["value"] == "real"
    assert "k" not in view["unresolved"]


def test_a_within_file_conflict_on_a_contested_key_is_still_reported(tmp_path: Path):
    """The guard that skips wholly-unresolved keys must not swallow ambiguous ones —
    their within-file conflicts are the six-in-2,850 real kind item 89 kept."""
    files = {"a/x.properties": "k=one\nK=two\n",
             "b/y.properties": "k=three\n"}
    _, res = view_of(tmp_path, files)
    conflict = [r for r in res.unresolved if r["kind"] == "key-conflict"]
    assert len(conflict) == 1 and conflict[0]["values"] == ["one", "two"]
    overlap = [r for r in res.unresolved if r["kind"] == "cross-scope-keys"]
    assert sum(r["keys"] for r in overlap) >= 1


# ── the kafka consumer asks as the file it is reading ─────────────────────────


LISTENER = """
package a.b;
public class L {
    @KafkaListener(topics = "${events.audit.topic}", groupId = "g")
    public void on(String p) { }
}
"""


@needs_java
def test_a_subscription_is_attached_to_its_own_applications_topic(tmp_path: Path):
    """The headline consumer regression. The listener lives in the billing tree, whose
    declaration the old flat merge OVERWROTE with the orders tree's — the edge said the
    billing app consumed the orders app's topic, confidently, with real provenance."""
    files = dict(TWO_APPS)
    files["backend/billing-app/src/main/java/a/b/L.java"] = LISTENER
    ctx = consumer_ctx(tmp_path, files)
    res = kafka.ConsumersExtractor().extract(ctx)
    consumes = [e for e in res.edges if e.type == "DG_CONSUMES"]
    assert [e.dst for e in consumes] == ["topic:audit-billing"]


@needs_java
def test_a_subscription_no_scope_can_answer_becomes_a_row_not_a_guess(tmp_path: Path):
    files = dict(TWO_APPS)
    files["shared/src/main/java/a/b/L.java"] = LISTENER
    ctx = consumer_ctx(tmp_path, files)
    res = kafka.ConsumersExtractor().extract(ctx)
    assert [e for e in res.edges if e.type == "DG_CONSUMES"] == []
    row = next(r for r in res.unresolved if r["kind"] == "unresolved-topic")
    assert "2 different values" in row["why"]
    assert "selects none of them" in row["why"]


# ── the spring consumers stop borrowing across scopes ─────────────────────────


CONTROLLER = """
package a.b;
@RestController
public class C {
    @GetMapping("/list")
    public String list() { return "[]"; }
}
"""


@needs_java
def test_each_controller_gets_its_own_applications_prefix(tmp_path: Path):
    """Before: one arbitrary context path for the whole component — on the measured
    estate, 714 of 1,442 endpoints carried another application's prefix."""
    files = dict(TWO_APPS)
    files["backend/billing-app/src/main/java/a/b/C.java"] = CONTROLLER
    files["backend/orders-app/src/main/java/a/b/D.java"] = CONTROLLER
    ctx = consumer_ctx(tmp_path, files)
    res = spring.ControllersExtractor().extract(ctx)
    assert sorted(e.dst for e in res.edges if e.type == "DG_EXPOSES") == \
        ["ep:/billing/list", "ep:/orders/list"]


@needs_java
def test_a_controller_in_no_scope_borrows_no_prefix(tmp_path: Path):
    files = dict(TWO_APPS)
    files["shared/src/main/java/a/b/C.java"] = CONTROLLER
    ctx = consumer_ctx(tmp_path, files)
    res = spring.ControllersExtractor().extract(ctx)
    exposes = [e for e in res.edges if e.type == "DG_EXPOSES"]
    assert [e.dst for e in exposes] == ["ep:/list"]
    assert exposes[0].confidence == 0.7
    row = next(r for r in res.unresolved if r["kind"] == "missing-context-path")
    assert "differing values" in row["why"]
    assert "borrowing" in row["why"]


def test_a_host_and_path_in_different_scopes_configure_no_call(tmp_path: Path):
    """46 of the measured estate's config-pair calls existed in no single scope — a
    database URL paired with a migration directory was the sample. Zero real pairs are
    lost: a scope that declares both still pairs them."""
    files = {"a-app/src/main/resources/application.properties":
                 "billing.svc.host=http://billing\n",
             "z-app/src/main/resources/application.properties":
                 "billing.svc.path=/bills/list\n"}
    root = write_tree(tmp_path, files)
    paths = [root / rel for rel in files]
    pctx = ExtractContext(repos=[RepoRef(alias="svc", path=root)], run_id="r",
                          files={"svc": paths})
    P.PropertiesExtractor().extract(pctx)
    ctx = ExtractContext(repos=[RepoRef(alias="svc", path=root)], run_id="r",
                         cache=pctx.cache, files={"svc": paths})
    res = spring.RestCallsExtractor().extract(ctx)
    assert [e for e in res.edges if e.type == "DG_CALLS"] == []
    row = next(r for r in res.unresolved if r["kind"] == "unpaired-host")
    assert row["key"] == "billing.svc.host"


def test_a_pair_declared_within_one_scope_still_becomes_a_call(tmp_path: Path):
    files = {"a-app/src/main/resources/application.properties":
                 "billing.svc.host=http://billing\nbilling.svc.path=/bills/list\n"}
    root = write_tree(tmp_path, files)
    paths = [root / rel for rel in files]
    pctx = ExtractContext(repos=[RepoRef(alias="svc", path=root)], run_id="r",
                          files={"svc": paths})
    P.PropertiesExtractor().extract(pctx)
    ctx = ExtractContext(repos=[RepoRef(alias="svc", path=root)], run_id="r",
                         cache=pctx.cache, files={"svc": paths})
    res = spring.RestCallsExtractor().extract(ctx)
    calls = [e for e in res.edges if e.type == "DG_CALLS"]
    assert [e.dst for e in calls] == ["ep:/bills/list"]


# ── determinism, since the ambiguity record feeds reports ─────────────────────


def test_the_scoped_view_does_not_depend_on_walk_order(tmp_path: Path):
    forward, _ = view_of(tmp_path / "f", TWO_APPS)
    backward, _ = view_of(tmp_path / "b", dict(reversed(list(TWO_APPS.items()))))
    assert forward == backward
