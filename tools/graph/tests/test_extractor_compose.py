"""The compose extractor — the second fixture's configuration substrate.

Its product is the resolved environment view, not its own edges. That is the same shape the
properties extractor publishes, on purpose: one resolution rule rather than two that can
disagree about what a key means.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import compose  # noqa: E402
from tools.graph.extractors.properties import lookup  # noqa: E402

EXT = compose.ComposeExtractor()


def ctx_for(tmp_path: Path, files: dict[str, str], aliases: tuple[str, ...] = ("deploy",),
            claimed: str = "deploy") -> ExtractContext:
    """A context whose `claimed` component holds `files`, plus empty sibling components."""
    paths = []
    for rel, body in files.items():
        p = tmp_path / claimed / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    repos = [RepoRef(alias=a, path=tmp_path / a) for a in aliases]
    for r in repos:
        r.path.mkdir(parents=True, exist_ok=True)
    return ExtractContext(repos=repos, run_id="r", files={claimed: paths})


def edges(res, type_):
    return [e for e in res.edges if e.type == type_]


def ctx_canonical(tmp_path: Path, files: dict[str, str], canonical: list[dict],
                  aliases: tuple[str, ...] = ("deploy",),
                  claimed: str = "deploy") -> ExtractContext:
    """`ctx_for` plus the workspace's `[[canonical]]` blocks, as the pipeline supplies them.

    `ctx.config` already carries these — the SPI seam existed before anything used it, which
    is why resolving a contested service's VALUE needed no core change.
    """
    ctx = ctx_for(tmp_path, files, aliases, claimed)
    ctx.config = {"canonical": canonical}
    return ctx


BASIC = """\
services:
  api:
    build: ../api
    environment:
      QUEUE_KEY: events-v2
  db:
    image: postgres:16
"""


# ── services become claims the canonical pass can group on ───────────────────


def test_each_service_becomes_a_deploy_claim(tmp_path: Path):
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": BASIC}, ("deploy", "api")))
    claims = {e.dst for e in edges(res, "DG_DEPLOYS")}
    assert claims == {ids.service_id("api", "deploy"), ids.service_id("db", "deploy")}


def test_two_files_claiming_one_service_produce_two_identical_claims(tmp_path: Path):
    """Extraction records the collision and does not decide it. Deciding which variant is
    authoritative is a statement about intent, and neither file contains it."""
    res = EXT.extract(ctx_for(tmp_path, {
        "docker-compose.yml": BASIC,
        "legacy/docker-compose.yml": BASIC}, ("deploy", "api")))
    api = [e for e in edges(res, "DG_DEPLOYS") if e.dst == ids.service_id("api", "deploy")]
    assert len(api) == 2
    assert {e.confidence for e in api} == {1.0}
    assert {e.canonical for e in api} == {True}


# ── the line-number defect this fixture found ────────────────────────────────


def test_a_service_key_is_not_matched_inside_a_value(tmp_path: Path):
    """The regression this extractor's own fixture exposed.

    `props.find_line` is a substring search, right for a properties file and wrong for YAML.
    A compose file declaring a service `db` also contains `postgresql://db:5432/...` in a
    value on an earlier line, so the substring search reported the service as declared
    eleven lines before it was — plausible, otherwise-correct, and invisible to anyone not
    checking the number against the file.
    """
    body = """\
services:
  api:
    environment:
      DSN: postgresql://db:5432/app
  db:
    image: postgres:16
"""
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy", "api")))
    db = [e for e in edges(res, "DG_DEPLOYS") if e.dst == ids.service_id("db", "deploy")]
    assert [e.line for e in db] == [5]


def test_two_services_declaring_one_variable_each_get_their_own_line(tmp_path: Path):
    """Scanning forward per service, rather than from the top each time."""
    body = """\
services:
  api:
    environment:
      SHARED: one
  worker:
    environment:
      SHARED: two
"""
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy",)))
    lines = sorted(e.line for e in edges(res, "DG_CONFIGURES"))
    assert lines == [4, 7]


# ── interpolation: compose's grammar, resolved by the core's existing regex ──


def test_the_compose_default_grammar_resolves(tmp_path: Path):
    """`${VAR:-default}`, which is not the properties spelling. The core's PLACEHOLDER
    already covers both, so this extractor adds no interpolation of its own — a second
    implementation is how one workspace resolves a placeholder two ways."""
    body = 'services:\n  api:\n    environment:\n      Q: ${UNSET:-events-v2}\n'
    ctx = ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy",))
    EXT.extract(ctx)
    view = compose.view_for(ctx.cache, "api")
    assert lookup(view, "Q")["value"] == "events-v2"


def test_a_placeholder_with_no_default_is_kept_out_of_the_view(tmp_path: Path):
    """Absence is the safe failure. Returning the literal would hand every consumer a
    plausible string and name an entity after a placeholder."""
    body = 'services:\n  api:\n    environment:\n      Q: ${NOWHERE}\n'
    ctx = ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy",))
    res = EXT.extract(ctx)
    view = compose.view_for(ctx.cache, "api")
    assert lookup(view, "Q") is None
    assert any(r["kind"] == "compose-env-unresolved" for r in res.unresolved)


# ── both spellings of the environment block ──────────────────────────────────


def test_the_list_form_of_environment_is_read(tmp_path: Path):
    """A reader handling only the map form produces a service with no environment, which is
    indistinguishable from a service that genuinely has none."""
    body = 'services:\n  api:\n    environment:\n      - QUEUE_KEY=events-v2\n'
    ctx = ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy",))
    EXT.extract(ctx)
    assert lookup(compose.view_for(ctx.cache, "api"), "QUEUE_KEY")["value"] == "events-v2"


def test_a_bare_key_in_list_form_is_kept_rather_than_dropped(tmp_path: Path):
    """`- QUEUE_KEY` means inherit from the host. The key is still referenced, and a key
    this workspace names is one the graph should know about."""
    body = 'services:\n  api:\n    environment:\n      - QUEUE_KEY\n'
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy",)))
    assert [e.dst for e in edges(res, "DG_CONFIGURES")] == [ids.env_key_id("QUEUE_KEY")]


@pytest.mark.parametrize("spelling", ["QUEUE_KEY", "queue.key", "queue-key"])
def test_spellings_of_one_key_collapse_to_one_node(tmp_path: Path, spelling: str):
    body = f'services:\n  api:\n    environment:\n      {spelling}: events-v2\n'
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy",)))
    assert [e.dst for e in edges(res, "DG_CONFIGURES")] == [ids.env_key_id("QUEUE_KEY")]


# ── which component a service is ─────────────────────────────────────────────


def test_a_build_context_maps_a_service_to_its_component(tmp_path: Path):
    """A declared link, so it is taken at full confidence."""
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": BASIC}, ("deploy", "api")))
    api = [e for e in edges(res, "DG_DEPLOYS") if e.props["service"] == "api"][0]
    assert api.props["mapped_by"] == "build" and api.props["component"] == "api"


def test_name_equality_is_a_fallback_and_says_so(tmp_path: Path):
    """Usually right, and a coincidence rather than a declaration — so it is recorded as a
    different kind of fact instead of being presented as the same one."""
    body = 'services:\n  api:\n    image: x\n    environment:\n      Q: v\n'
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy", "api")))
    api = [e for e in edges(res, "DG_DEPLOYS") if e.props["service"] == "api"][0]
    assert api.props["mapped_by"] == "name"


def test_a_service_matching_no_component_still_publishes_its_environment(tmp_path: Path):
    """A service this workspace does not build is still a service it runs."""
    body = 'services:\n  cache:\n    image: redis\n    environment:\n      TTL: "60"\n'
    ctx = ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy",))
    res = EXT.extract(ctx)
    assert [e.props["mapped_by"] for e in edges(res, "DG_DEPLOYS")] == [""]
    assert lookup(compose.view_for(ctx.cache, "cache"), "TTL")["value"] == "60"


# ── failure is recorded, never silent, and never fatal ───────────────────────


def test_a_malformed_file_is_recorded_and_the_others_still_build(tmp_path: Path):
    res = EXT.extract(ctx_for(tmp_path, {
        "docker-compose.yml": BASIC,
        "docker-compose.override.yml": 'services:\n  x:\n    environment:\n      K: "open\n'},
        ("deploy", "api")))
    bad = [r for r in res.unresolved if r["kind"] == "compose-unparsed"]
    assert len(bad) == 1 and bad[0]["source_file"] == "docker-compose.override.yml"
    assert edges(res, "DG_DEPLOYS"), "a readable file must still produce its claims"


def test_a_file_with_no_services_block_is_not_an_error(tmp_path: Path):
    res = EXT.extract(ctx_for(tmp_path, {"compose.yml": "version: '3'\n"}, ("deploy",)))
    assert res.edges == [] and res.unresolved == []


# ── the shape the cache promises ─────────────────────────────────────────────


def test_the_view_is_the_shape_the_properties_extractor_publishes(tmp_path: Path):
    """So `properties.lookup` reads it unchanged. Two shapes would mean two resolution
    rules, and two rules can disagree about what a key means."""
    ctx = ctx_for(tmp_path, {"docker-compose.yml": BASIC}, ("deploy", "api"))
    EXT.extract(ctx)
    view = compose.view_for(ctx.cache, "api")
    assert set(view) == {"values", "unresolved"}
    entry = view["values"][ids.relaxed_key("QUEUE_KEY")]
    assert set(entry) == {"value", "file", "line", "raw", "spellings"}
    assert entry["spellings"]["QUEUE_KEY"]["line"] == entry["line"]


def test_view_for_returns_none_when_nothing_published_one(tmp_path: Path):
    """None is a real answer, not an error: `--only` can exclude this extractor entirely,
    and a consumer that gets None records an unresolved row rather than crashing."""
    assert compose.view_for({}, "api") is None


def test_a_later_file_wins_the_value_and_no_spelling_is_lost(tmp_path: Path):
    ctx = ctx_for(tmp_path, {
        "docker-compose.yml": 'services:\n  api:\n    environment:\n      Q: first\n',
        "docker-compose.override.yml":
            'services:\n  api:\n    environment:\n      q-key: second\n'}, ("deploy",))
    EXT.extract(ctx)
    view = compose.view_for(ctx.cache, "api")
    assert lookup(view, "Q")["value"] == "first"
    assert lookup(view, "q-key")["value"] == "second"


# ── what this extractor deliberately does not emit ───────────────────────────


def test_depends_on_is_not_emitted_as_a_dependency_edge(tmp_path: Path):
    """`DG_DEPENDS_ON` currently carries build-time module dependencies with versions.
    A runtime start-order edge under the same name makes a version-carrying query return
    rows that have none. It needs its own type, which needs a consumer; neither exists.
    """
    body = ('services:\n  api:\n    image: x\n    depends_on:\n      - db\n'
            '  db:\n    image: postgres:16\n')
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": body}, ("deploy",)))
    assert edges(res, "DG_DEPENDS_ON") == []


# ── a contested service: last-wins is right for layering, wrong for a rival ───

OWNER = """\
services:
  api:
    build: ../api
    environment:
      QUEUE_KEY: events-v2
"""

SUPERSEDED = """\
services:
  api:
    build: ../../api
    environment:
      QUEUE_KEY: events-v1
"""

CLAIM = [{"service": "api", "owner": "deploy", "path": "docker-compose.yml"}]


def test_a_superseded_file_cannot_overwrite_the_owners_value(tmp_path: Path):
    """REGRESSION, and the bug it pins shipped: `legacy/` sorts AFTER `docker-compose.yml`,
    so the deterministic last-wins rule handed a whole component a topic one version number
    off, resolved from a file whose own header says it is dead. Every step looked healthy.

    Last-wins is correct for layering and catastrophic for a rival claim, and the two are
    the same shape on disk — only the workspace's `[[canonical]]` block tells them apart,
    because neither file contains the intent."""
    ctx = ctx_canonical(tmp_path, {"docker-compose.yml": OWNER,
                                   "legacy/docker-compose.yml": SUPERSEDED},
                        CLAIM, ("deploy", "api"))
    EXT.extract(ctx)
    assert compose.view_for(ctx.cache, "api")["values"]["queue.key"]["value"] == "events-v2"


def test_the_superseded_value_is_reported_rather_than_dropped(tmp_path: Path):
    """A stale deployment nobody has noticed is a finding. Silently discarding the loser
    would make the graph correct and the reader none the wiser."""
    ctx = ctx_canonical(tmp_path, {"docker-compose.yml": OWNER,
                                   "legacy/docker-compose.yml": SUPERSEDED},
                        CLAIM, ("deploy", "api"))
    res = EXT.extract(ctx)
    rows = [r for r in res.unresolved if r["kind"] == "superseded-declaration"]
    assert [r["value"] for r in rows] == ["events-v1"]
    assert rows[0]["source_file"] == "legacy/docker-compose.yml"


def test_the_owner_arriving_second_still_reports_what_it_displaced(tmp_path: Path):
    """The reverse arrival order, and the branch nothing else covers.

    Written first as "the owner wins whichever order the files arrive in" — which **passed
    against the pre-fix source** and was therefore proving nothing: with the owner sorting
    last, plain last-wins already gives the right value. The discriminating claim in that
    direction is not the value but the REPORT: a rival declaration that briefly held the
    value must still be named, or the ranking silently hides the very conflict it resolved.
    """
    ctx = ctx_canonical(tmp_path, {"compose.yml": SUPERSEDED,
                                   "docker-compose.yml": OWNER},
                        [{"service": "api", "owner": "deploy", "path": "docker-compose.yml"}],
                        ("deploy", "api"))
    res = EXT.extract(ctx)
    assert compose.view_for(ctx.cache, "api")["values"]["queue.key"]["value"] == "events-v2"
    rows = [r for r in res.unresolved if r["kind"] == "superseded-declaration"]
    assert [r["value"] for r in rows] == ["events-v1"]


def test_without_a_canonical_block_last_wins_is_unchanged(tmp_path: Path):
    """The layering case, untouched. An override file legitimately supersedes a base one,
    and a workspace only fills in `[[canonical]]` for collisions the build reports."""
    ctx = ctx_for(tmp_path, {"docker-compose.yml": OWNER,
                             "legacy/docker-compose.yml": SUPERSEDED}, ("deploy", "api"))
    EXT.extract(ctx)
    assert compose.view_for(ctx.cache, "api")["values"]["queue.key"]["value"] == "events-v1"


def test_owner_alone_decides_when_the_variants_are_in_different_components(tmp_path: Path):
    claim = [{"service": "api", "owner": "deploy"}]
    assert compose.is_canonical_source(claim[0], "deploy", "anything.yml")
    assert not compose.is_canonical_source(claim[0], "other", "anything.yml")


def test_a_claim_naming_a_file_does_not_cover_a_package_below_its_directory(tmp_path: Path):
    """T529, the review's reproduction. The claim names `deploy/docker-compose.yml`, and its
    parent rule covered everything under `deploy/` - the superseded `deploy/legacy/` file
    included - so both were canonical, the legacy one sorted last and won, and no
    `superseded-declaration` row was written."""
    owner = OWNER.replace("../api", "../../api")
    stale = SUPERSEDED.replace("../../api", "../../../api")
    claim = [{"service": "api", "owner": "deploy", "path": "deploy/docker-compose.yml"}]
    ctx = ctx_canonical(tmp_path, {"deploy/docker-compose.yml": owner,
                                   "deploy/legacy/docker-compose.yml": stale},
                        claim, ("deploy", "api"))
    res = EXT.extract(ctx)
    assert compose.view_for(ctx.cache, "api")["values"]["queue.key"]["value"] == "events-v2"
    rows = [r for r in res.unresolved if r["kind"] == "superseded-declaration"]
    assert [r["source_file"] for r in rows] == ["deploy/legacy/docker-compose.yml"]


def test_path_decides_when_one_component_holds_both_variants(tmp_path: Path):
    """`owner` decides nothing here — it would name that component twice."""
    assert compose.is_canonical_source(CLAIM[0], "deploy", "docker-compose.yml")
    assert not compose.is_canonical_source(CLAIM[0], "deploy", "legacy/docker-compose.yml")


def test_a_service_with_no_claim_is_not_contested(tmp_path: Path):
    assert compose.canonical_claim({"canonical": CLAIM}, "api") == CLAIM[0]
    assert compose.canonical_claim({"canonical": CLAIM}, "db") is None
    assert compose.canonical_claim({}, "api") is None


# ── W301 / T121: an invalid services block is not an absent one ────────────────


def test_a_service_with_an_empty_body_still_reaches_the_graph(tmp_path: Path):
    """T121. `worker:` with nothing under it is a declared service with no config, but a
    non-dict (here None) body was `continue`d, so it produced no svc node and no DG_DEPLOYS
    edge — three services declared, two reaching the graph."""
    res = compose.ComposeExtractor().extract(ctx_for(tmp_path, {"docker-compose.yml":
        "services:\n  api:\n    image: x\n  worker:\n  db:\n    image: y\n"}))
    deployed = {e.props["service"] for e in edges(res, "DG_DEPLOYS")}
    assert deployed == {"api", "worker", "db"}, "the empty-body service must not vanish"


def test_services_written_as_a_list_is_reported_not_silently_absent(tmp_path: Path):
    """T121. `services:` as a LIST is valid YAML, so no parse error fired and a bare `return`
    emitted nothing — an invalid file read as one that simply declares no services."""
    res = compose.ComposeExtractor().extract(ctx_for(tmp_path, {"docker-compose.yml":
        "services:\n  - api\n  - db\n"}))
    assert [r["kind"] for r in res.unresolved] == ["compose-unparsed"]


# ── T536 / T539: what a value was WRITTEN as, and where a key is DECLARED ──────

TYPED = """\
services:
  api:
    image: x
    environment:
      VERSION: 1.10
      FEATURE: yes
      AUDIT: off
      DEBUG: true
      PORT: 8080
      MODE: "yes"
"""


def test_a_value_is_recorded_as_written_not_as_yaml_types_it(tmp_path: Path):
    """T536. The values went through `safe_load`'s typing: `1.10` was recorded as `1.1`,
    `yes` as `True` and `off` as `False` - values no file contains, in the edge, its `raw`
    and the environment view every later reader resolves against."""
    ctx = ctx_for(tmp_path, {"docker-compose.yml": TYPED})
    res = EXT.extract(ctx)
    said = {e.props["declared_as"]: (e.props["value"], e.props["raw"])
            for e in edges(res, "DG_CONFIGURES")}
    assert said == {"VERSION": ("1.10", "1.10"), "FEATURE": ("yes", "yes"),
                    "AUDIT": ("off", "off"), "DEBUG": ("true", "true"),
                    "PORT": ("8080", "8080"), "MODE": ("yes", "yes")}
    view = compose.view_for(ctx.cache, "api")
    assert view["values"][ids.relaxed_key("VERSION")]["value"] == "1.10"


def test_a_value_a_yaml_reader_may_type_otherwise_gets_a_row_naming_how(tmp_path: Path):
    """The value is recorded as written, but a service may receive what a YAML reader types
    it as - which one depends on the tool reading the file - so a row says so, and names the
    readings. `true`, `8080` and a quoted value arrive as written under every reader."""
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": TYPED}))
    rows = {r["key"]: r for r in res.unresolved if r["kind"] == "yaml-typed-value"}
    assert {k: r["readings"] for k, r in rows.items()} == {
        "VERSION": ["1.1"], "FEATURE": ["true"], "AUDIT": ["false"]}
    assert rows["VERSION"]["line"] == 5 and rows["VERSION"]["value"] == "1.10"
    assert "quoting it makes it certain" in rows["FEATURE"]["why"]


ANCHORED = """\
services:
  base:
    image: x
    environment: &env
      QUEUE: orders
      LOG: info
  worker:
    image: x
    environment:
      <<: *env
      LOG: debug
  other:
    image: x
    environment:
      QUEUE: payments
"""


def test_a_key_merged_in_through_an_anchor_is_cited_where_it_is_declared(tmp_path: Path):
    """T539. `worker` inherits QUEUE through `<<: *env`, and the forward scan for its line
    found `other`'s QUEUE - a declaration of a DIFFERENT value - and cited it."""
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": ANCHORED}))
    cited = {(e.props["service"], e.props["declared_as"]): (e.line, e.props["value"])
             for e in edges(res, "DG_CONFIGURES")}
    assert cited[("worker", "QUEUE")] == (5, "orders"), "cited at the anchor's declaration"
    assert cited[("worker", "LOG")] == (11, "debug"), "an explicit key wins, on its own line"
    assert cited[("other", "QUEUE")] == (15, "payments")


def test_an_environment_given_whole_by_an_alias_is_cited_at_the_anchor(tmp_path: Path):
    body = ("services:\n  base:\n    image: x\n    environment: &env\n      QUEUE: orders\n"
            "  worker:\n    image: x\n    environment: *env\n"
            "  other:\n    image: x\n    environment:\n      QUEUE: payments\n")
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": body}))
    cited = {(e.props["service"], e.props["declared_as"]): e.line
             for e in edges(res, "DG_CONFIGURES")}
    assert cited[("worker", "QUEUE")] == 5 and cited[("other", "QUEUE")] == 12


def test_the_list_form_cites_each_item_and_keeps_its_text(tmp_path: Path):
    body = ("services:\n  api:\n    image: x\n    environment:\n"
            "      - VERSION=1.10\n      - QUEUE\n")
    res = EXT.extract(ctx_for(tmp_path, {"docker-compose.yml": body}))
    said = {e.props["declared_as"]: (e.line, e.props["raw"])
            for e in edges(res, "DG_CONFIGURES")}
    assert said == {"VERSION": (5, "1.10"), "QUEUE": (6, "")}
    assert not [r for r in res.unresolved if r["kind"] == "yaml-typed-value"], \
        "a list item is text to every reader"
