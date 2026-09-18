"""Item 22 — context packs: the blast radius delivered as the prompt's first page.

Fixture pins come from EXPECTED.md's facts: the producer seed's pack must open on the
golden chain — every row carrying its `source_file:line` and the graph route that put
it there — with item 78's generated flag riding through and item 20's seed records
verbatim. The budget trims loudly; pack.json never trims; the d2 render's absence is
a stated status, never a silence.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
import tools.graph.pack as P  # noqa: E402
import tools.graph.impact as I  # noqa: E402
from tools.graph.impact import load_graph  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"
PRODUCER = ("orders-api", "src/main/java/com/example/orderflow/api/OrderPublisher.java")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("packs") / "out"
    assert B.main(["--workspace", str(FIXTURE / "workspace.toml"),
                   "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def pack(built):
    nodes, edges = load_graph(built)
    result = P.build_pack(nodes, edges, PRODUCER[0], [(PRODUCER[1], None)],
                          graph_dir=built)
    assert result is not None
    return result


def rows_of(pack: dict, section: str) -> dict[str, dict]:
    return {r["path"]: r for r in pack["sections"][section]}


# ── the pack's content, against the answer key ────────────────────────────────


def test_the_contracts_list_the_interface_nodes_once_each(pack):
    ids = [c["id"] for c in pack["contracts"]]
    assert len(ids) == len(set(ids)), \
        "graph.jsonl keeps one node row per emitting extractor; the pack must merge"
    assert "topic:order-events-v1" in ids
    assert "table:order_events" in ids
    assert any(i.startswith("ep:/orders/v1/list") for i in ids)


def test_every_contract_names_where_it_is_declared(pack):
    by_id = {c["id"]: c for c in pack["contracts"]}
    cite = by_id["table:order_events"]["declared_at"]
    assert ":" in cite and cite.startswith("orders-worker/"), \
        "a contract without a file:line cannot be reviewed, only believed"


def test_every_row_carries_provenance_and_a_route_ending_at_itself(pack):
    for section, rows in pack["sections"].items():
        for row in rows:
            assert "@" in row["why"] and ":" in row["why"], (section, row["path"])
            assert row["route"], (section, row["path"])
    consumer = rows_of(pack, "direct")[
        "src/main/java/com/example/orderflow/worker/OrderConsumer.java"]
    assert consumer["route"] == ["topic:order-events-v1",
                                 "svc:orders-worker@orders-worker"], \
        "the route is the seed-side explanation: the topic reached the consumer"
    assert "OrderConsumer.java:8" in consumer["why"]


def test_direct_and_indirect_split_on_the_winning_facts_hop(pack):
    direct = rows_of(pack, "direct")
    indirect = rows_of(pack, "indirect")
    assert all(r["hops"] <= 1 for r in direct.values())
    assert all(r["hops"] >= 2 for r in indirect.values())
    assert any(p.endswith("OrderConsumer.java") for p in direct), \
        "one join from the seeded topic is the definition of direct"
    assert any(p.endswith("urls.js") for p in indirect), \
        "the UI reaches the chain through the exposed endpoint - further out"


def test_the_spec_file_sits_in_contracts_not_config(pack):
    specs = rows_of(pack, "contracts")
    assert any(p.endswith("openapi.yaml") for p in specs)
    assert not any(p.endswith("openapi.yaml") for p in rows_of(pack, "config"))


def test_the_tests_section_names_the_covering_suite(pack):
    tests = rows_of(pack, "tests")
    assert any(p.endswith("OrderControllerTest.java") for p in tests)


def test_the_generated_flag_rides_into_the_pack(pack):
    relay = rows_of(pack, "direct")["gen/EventsRelay.java"]
    assert relay["generated"] is True
    assert relay["regenerate_from"].endswith("openapi.yaml")
    hand = rows_of(pack, "direct")[
        "src/main/java/com/example/orderflow/api/AuditTrail.java"]
    assert "generated" not in hand


def test_the_docs_section_states_the_missing_capability(pack):
    assert pack["docs"]["rows"] == []
    assert "absence of capability" in pack["docs"]["note"]


def test_the_seed_block_is_item_20s_record_verbatim(pack):
    assert pack["seed"]["component"] == "orders-api"
    (f,) = pack["seed"]["files"]
    assert f["mode"] == "provenance" and f["seeds"] > 0


def test_the_pack_names_the_build_it_read(pack, built):
    report = json.loads((built / "build_report.json").read_text(encoding="utf-8"))
    assert pack["graph"]["run_id"] == report["run_id"]


# ── rendering: the budget trims loudly, pack.json never ───────────────────────


def test_the_md_renders_every_section_with_scores_and_routes(pack):
    md, _ = P.render_md(dict(pack), budget=100_000)
    for title in ("## Seeds", "Contracts — interface nodes", "## Direct",
                  "## Indirect", "Config coupling", "Tests to run-or-edit",
                  "## Docs"):
        assert title in md
    assert "regenerate, don't edit" in md
    assert "route:" in md and "why:" in md
    assert "elided" not in md, "an unconstrained budget elides nothing"


def test_a_tight_budget_trims_globally_and_says_so_per_section(pack):
    work = dict(pack)
    md, budget_info = P.render_md(work, budget=700)
    assert "elided by the token budget" in md
    assert "full list is in pack.json" in md
    assert budget_info["elided"], "the loss is recorded as data, not only prose"
    assert P.token_estimate(md) <= 700 + 150, \
        "the estimator is stated-approximate; the render must stay near the target"
    for f in pack["seed"]["files"]:
        assert f["path"] in md, "seeds are never trimmed — a pack that hides what " \
                                "it started from cannot be checked"


def test_trimming_keeps_the_highest_scores(pack):
    kept_or_not = [(r["score"], r["path"] in P.render_md(dict(pack), budget=700)[0])
                   for r in pack["sections"]["direct"]]
    top_score = max(s for s, _ in kept_or_not)
    assert all(kept for s, kept in kept_or_not if s == top_score), \
        "the trim drops ascending by score - the strongest rows survive any budget"


def test_the_json_is_complete_at_any_budget(pack, built, tmp_path):
    out = tmp_path / "p"
    assert P.main(["--graph-dir", str(built),
                   "--seed", f"{PRODUCER[0]}/{PRODUCER[1]}",
                   "--out", str(out), "--budget", "700", "--no-svg"]) == 0
    data = json.loads((out / "pack.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == P.PACK_SCHEMA_VERSION
    total_rows = sum(len(r) for r in data["sections"].values())
    full = P.build_pack(*load_graph(built), PRODUCER[0], [(PRODUCER[1], None)])
    assert total_rows == sum(len(r) for r in full["sections"].values()), \
        "the budget shapes pack.md only; pack.json is the machine's copy"
    assert data["budget"]["elided"]


# ── the CLI and the garnish ───────────────────────────────────────────────────


def test_the_cli_writes_all_three_files_and_reports_the_svg_status(built, tmp_path,
                                                                   capsys):
    out = tmp_path / "p"
    assert P.main(["--graph-dir", str(built),
                   "--seed", f"{PRODUCER[0]}/{PRODUCER[1]}",
                   "--out", str(out), "--no-svg"]) == 0
    text = capsys.readouterr().out
    assert (out / "pack.md").exists() and (out / "pack.json").exists()
    assert (out / "pack.d2").exists()
    data = json.loads((out / "pack.json").read_text(encoding="utf-8"))
    assert data["svg"] == "not rendered: --no-svg"
    assert "pack:" in text and "contracts:" in text


def test_the_d2_source_is_bounded_and_deterministic(pack):
    a, b = P.render_d2(dict(pack)), P.render_d2(dict(pack))
    assert a == b
    assert a.startswith("direction: right")
    assert "->" in a
    assert len(a.splitlines()) < 120


def test_a_missing_d2_binary_is_a_stated_status(tmp_path, monkeypatch):
    from tools import third_party as tp
    monkeypatch.setattr(tp, "bin_dir", lambda plat: tmp_path / "nowhere")
    (tmp_path / "x.d2").write_text("a -> b\n", encoding="utf-8")
    status = P._render_svg(tmp_path / "x.d2", tmp_path / "x.svg")
    assert status.startswith("not rendered: vendored d2 missing")
    assert "third_party.py --fetch" in status


def test_the_cli_refuses_the_same_half_requests_impact_does(built, tmp_path, capsys):
    out = tmp_path / "p"
    assert P.main(["--graph-dir", str(built), "--out", str(out)]) == 2
    assert "exactly one of --seed and --diff" in capsys.readouterr().out
    assert P.main(["--graph-dir", str(built), "--seed", "no-such/x.java",
                   "--out", str(out)]) == 2
    assert "not in this graph" in capsys.readouterr().out
    assert P.main(["--graph-dir", str(built), "--seed",
                   f"{PRODUCER[0]}/{PRODUCER[1]}", "--out", str(out),
                   "--budget", "50"]) == 2
    assert "floor" in capsys.readouterr().out


def test_a_diff_pack_carries_per_file_seed_records(built, tmp_path):
    diff = tmp_path / "change.diff"
    diff.write_text(
        "--- a/src/config/urls.js\n+++ b/src/config/urls.js\n"
        "@@ -5,1 +5,1 @@\n-x\n+y\n", encoding="utf-8")
    out = tmp_path / "p"
    assert P.main(["--graph-dir", str(built), "--diff", str(diff),
                   "--component", "web-console", "--out", str(out),
                   "--no-svg"]) == 0
    data = json.loads((out / "pack.json").read_text(encoding="utf-8"))
    assert data["seed"]["mode"] == "hunk"
    (f,) = data["seed"]["files"]
    assert f["path"] == "src/config/urls.js" and f["lines_missed"] is False


# ── item 128: a malformed seed is named as a SEED problem ──────────────────
#
# Walk 3. goose passed `--seed <absolute path>`, which contains a separator, so
# it sailed past the emptiness check and split into a component the graph had
# never heard of. The refusal read `component 'D:' is not in this graph` — true,
# and it names the wrong thing, so the runner went looking for a component
# called `D:`. Item 117's argument: a refusal nobody can act on is one nobody
# acts on.


#: The leak guard bans a drive-letter path shape, and it is right to: a
#: literal one is how a machine's coordinates leak into a repository. The
#: shape is what this item is ABOUT, so it is BUILT rather than written -
#: the runtime construction item 108 used for a PEM banner in a fixture.
DRIVE = chr(68) + ":"
ABSOLUTE_SEEDS = (
    DRIVE + "/work/repo/src/Main.java",        # a Windows drive
    chr(99) + ":/work/repo/src/Main.java",     # lower case is the same drive
    "/" + "home/dev/repo/src/Main.java",       # POSIX - the head is empty
    DRIVE + "\\work/repo/src/Main.java",       # mixed, one machine to another
)


@pytest.mark.parametrize("seed", ABSOLUTE_SEEDS)
def test_an_absolute_seed_is_named_as_a_seed_problem(seed):
    said = P.seed_problem(seed, {"orders-api"})
    assert "COMPONENT/RELPATH" in said, said
    assert "absolute path" in said, said
    assert "orders-api" in said, "a refusal must name what it WOULD accept"


def test_a_component_this_graph_knows_wins_over_the_shape():
    """Deliberate, and the reason is written in the function: the shapes are
    judged only when the head names no component, so a workspace that really
    does alias something oddly keeps working and gets the old answer."""
    assert P.seed_problem(DRIVE + "/x.java", {DRIVE}) == ""


def test_an_unknown_component_that_is_not_a_path_keeps_the_component_answer():
    """**The boundary, pinned where it can fail.** `core/a.md` is a COMPONENT
    question — somebody named a component that is not in this graph — and must
    fall through to the answer that lists them, not be renamed a seed problem.
    Widening this to every unknown head would trade one unusable message for
    another."""
    assert P.seed_problem("core/a.md", {"orders-api"}) == ""


def test_the_cli_refuses_an_absolute_seed_by_name(built, tmp_path, capsys):
    """**The composition**, against a real built graph: the message a runner
    actually sees, not the helper's return value.

    `seed_problem` having tests and the CLI having tests would be the
    producer/consumer gap this project has found more often than any other —
    and the whole defect is that the wrong one of two messages was printed.
    """
    code = P.main(["--graph-dir", str(built),
                   "--seed", DRIVE + "/repo/src/Main.java",
                   "--component", "orders-api",
                   "--out", str(tmp_path / "pack"), "--no-svg"])
    said = capsys.readouterr().out
    assert code == 2
    assert "absolute path" in said, said
    assert "is not in this graph" not in said, \
        "the old message names a component nobody wrote"


def test_a_seed_naming_a_real_component_still_builds_a_pack(built, tmp_path, capsys):
    """The control. A check that never passes is one somebody deletes, and this
    one sits in front of every P2 pack."""
    code = P.main(["--graph-dir", str(built),
                   "--seed", f"{PRODUCER[0]}/{PRODUCER[1]}",
                   "--out", str(tmp_path / "pack"), "--no-svg"])
    assert code == 0, capsys.readouterr().out
    assert (tmp_path / "pack" / "pack.md").is_file()


# ── item 137: a seed the graph cannot seed from looks like a good one ─────────
#
# **Walk 4 found this at G1, and it was invisible until a human rejected a scope
# document TWICE.** P2's SOP says to seed the impact pack from a file the
# requirements name. Measured on the real run: a CORRECT seed and a nonsense one
# behaved identically — both exit 0, both `mode: directory`, both `0 seed(s)`,
# and both rendered a full pack that reads like an impact map. Three consecutive
# scope drafts argued about 366 contracts and 176 direct files describing the
# COMPONENT rather than the change, and the numbers looked plausible throughout.
#
# The fallback is not the defect. Item 20 built it for a MEASURED case and `mode`
# has always named it. What was missing is that `directory` cannot separate a
# real file the graph has facts about, a real file with NO node, and a path that
# does not exist — absent vs measured-zero, collapsed at the top of P2's
# artifact.
#
# The owner ruled: LABEL loudly, do not refuse. Refusing would overturn item 20's
# measured fallback and reject every legitimate directory-scope pack, and a gate
# that refuses the ordinary case is one that gets switched off.

UNCITED_SEED = "src/main/java/com/example/orderflow/api/NoSuchClass.java"
WORKER = ("orders-worker",
          "src/main/java/com/example/orderflow/worker/OrderConsumer.java")


def _packed(built, component, path):
    nodes, edges = load_graph(built)
    result = P.build_pack(nodes, edges, component, [(path, None)], graph_dir=built)
    assert result is not None, f"{component} is not in this graph"
    return result


def test_an_uncited_seed_and_a_real_one_no_longer_render_the_same_kind_of_pack(built):
    """**The defect, stated as the behaviour it was.** Red at the pre-item SHA:
    both packs opened on a Seeds section that read like an impact map, and
    nothing in either said which one it was."""
    real, _ = P.render_md(_packed(built, *PRODUCER), budget=100_000)
    nothing, _ = P.render_md(_packed(built, PRODUCER[0], UNCITED_SEED), budget=100_000)
    assert "DIRECTORY-SCOPE ANSWER" in nothing
    assert "DIRECTORY-SCOPE ANSWER" not in real, (
        "a pack that really did seed must not warn about itself — the warning "
        "would stop being read within a week")


def test_the_statement_names_the_path_nothing_could_be_found_for(built):
    """A refusal nobody can act on is one nobody acts on (item 117, item 128).
    Naming the mode alone leaves the reader to guess WHICH seed failed."""
    md, _ = P.render_md(_packed(built, PRODUCER[0], UNCITED_SEED), budget=100_000)
    assert UNCITED_SEED in md
    assert "holds no fact citing" in md
    assert "INTEGRATION facts" in md, (
        "the reader needs to know this graph is not a code index, or the "
        "obvious conclusion is that the seed was typed wrong")


def test_a_zero_seed_pack_still_exits_zero(built, tmp_path):
    """**The owner's ruling, pinned.** Label, do not refuse: item 20's fallback
    was built for a measured case, and refusing every directory-scope pack is
    how a real check gets switched off."""
    out = tmp_path / "packed"
    code = P.main(["--graph-dir", str(built),
                   "--seed", f"{PRODUCER[0]}/{UNCITED_SEED}",
                   "--out", str(out), "--no-svg"])
    assert code == 0
    assert "DIRECTORY-SCOPE ANSWER" in (out / "pack.md").read_text(encoding="utf-8")


def test_pack_json_carries_the_fallback_so_a_machine_need_not_parse_english(built):
    """The document has two readers and only one of them reads prose."""
    empty = _packed(built, PRODUCER[0], UNCITED_SEED)["seed"]
    real = _packed(built, *PRODUCER)["seed"]
    assert empty["fallback"]["basis"] == I.UNCITED
    assert empty["fallback"]["path"] == UNCITED_SEED
    assert "fallback" not in real, (
        "a real impact map must carry no fallback record — its ABSENCE is what "
        "a reader tests, so a key present-and-empty would defeat the check")
    assert [f["basis"] for f in real["files"]] == [I.SEEDED]


def test_the_three_situations_get_three_answers(built):
    """**Absent vs measured-zero vs the wrong component**, which is what
    `directory` collapsed. Each needs a different repair, so each gets a
    different word."""
    nodes, edges = load_graph(built)
    cited = I.cited_paths(nodes, edges)
    assert I.seed_basis(PRODUCER[1], "provenance", cited) == I.SEEDED
    assert I.seed_basis(WORKER[1], "directory", cited) == I.CITED_NOT_SEEDED, (
        "a path this graph DOES know, seeded under another component, is a "
        "--component the caller got wrong and not an unknown file")
    assert I.seed_basis(UNCITED_SEED, "directory", cited) == I.UNCITED


def test_a_path_the_graph_knows_under_the_wrong_component_says_so(built):
    """The `cited-not-seeded` case end to end: the graph holds facts about this
    file and none of them belong to the component that was named."""
    md, _ = P.render_md(_packed(built, PRODUCER[0], WORKER[1]), budget=100_000)
    assert "DIRECTORY-SCOPE ANSWER" in md
    assert "check the component alias" in md
    assert "holds no fact citing" not in md, (
        "the graph does cite this path — saying otherwise sends the reader to "
        "fix a filename that is already right")


def test_cited_paths_ignores_ownership_which_is_what_makes_that_possible(built):
    """`_provenance_seeds` filters by owner because it is choosing seeds. This
    asks whether the graph knows the file AT ALL, and the gap between the two
    answers IS the wrong-component case."""
    nodes, edges = load_graph(built)
    cited = I.cited_paths(nodes, edges)
    assert PRODUCER[1] in cited and WORKER[1] in cited
    assert UNCITED_SEED not in cited


def test_a_diff_that_seeds_nothing_says_it_fell_back_to_the_first_file(built):
    """Item 20's fallback takes `targets[0][0]` however many files were seeded,
    and a reader of a twelve-file diff cannot know that from `mode` alone."""
    nodes, edges = load_graph(built)
    paths = [f"{UNCITED_SEED}", "src/main/java/com/example/orderflow/api/AlsoNot.java"]
    result = P.build_pack(nodes, edges, PRODUCER[0], [(p, None) for p in paths],
                          graph_dir=built)
    assert result["seed"]["fallback"]["files"] == 2
    md, _ = P.render_md(result, budget=100_000)
    assert "All 2 seeded files produced nothing" in md
    single, _ = P.render_md(_packed(built, PRODUCER[0], UNCITED_SEED), budget=100_000)
    assert "seeded files produced nothing" not in single, (
        "one file is not a set of files that all failed")


def test_impact_pys_own_cli_states_it_too(built, tmp_path):
    """**The other surface, and leaving it out would be this item's own lesson.**
    `impact.py --seed` prints a seed line that had exactly the same hole, and a
    rule hardened where somebody was looking is this project's dominant shape.
    Spawned rather than called: item 96's rule."""
    import subprocess
    root = Path(__file__).resolve().parents[3]
    done = subprocess.run(
        [sys.executable, str(root / "tools" / "graph" / "impact.py"),
         "--graph-dir", str(built),
         "--seed", f"{PRODUCER[0]}/{UNCITED_SEED}"],
        capture_output=True, text=True, cwd=root, timeout=300)
    assert done.returncode == 0, done.stderr
    assert "DIRECTORY-SCOPE ANSWER" in done.stdout


def test_the_sentence_has_one_home_and_pack_does_not_restate_it(built):
    """**Item 115's rule.** Two surfaces render this and a forced copy drifts —
    `http_runner`'s restatement dropped a token at birth and printed a secret
    the original would have redacted. `pack.py` imports the builder; it does not
    own a second wording."""
    source = (Path(P.__file__)).read_text(encoding="utf-8")
    assert "seed_warning" in source
    assert "DIRECTORY-SCOPE" not in source, (
        "pack.py spells the sentence itself, so impact.py's copy can drift "
        "away from it without anything failing")


def test_no_fallback_record_means_no_statement_at_all(built):
    """Empty IS the discriminator, so a caller cannot render the warning over a
    real impact map by forgetting a comparison."""
    assert I.seed_warning({"mode": "provenance"}) == []
    assert I.seed_warning({"mode": "directory", "fallback": {}}) == []


def test_p2s_sop_promises_the_words_the_renderer_actually_emits(built):
    """**Item 123's crossing rule.** A procedure that tells a runner to look for
    something its own producer never emits is item 33's class — a requirement the
    runner is never told — arriving from the other side."""
    sop = (Path(__file__).resolve().parents[3] / "sdlc" / "stages"
           / "P2-scoping.md").read_text(encoding="utf-8")
    assert "Seeds" in sop and "directory-scope" in sop.lower()
    md, _ = P.render_md(_packed(built, PRODUCER[0], UNCITED_SEED), budget=100_000)
    assert "directory-scope" in md.lower(), (
        "the SOP tells a runner to look for a directory-scope statement and the "
        "renderer emits none")


# ── what the mutation pass found: five gaps, every one of them real ───────────


def test_a_file_the_graph_knows_only_as_a_node_is_still_cited():
    """**Walk 4's shape exactly, and a mutation is what found it missing.**
    The run's subject class declared no edge; the graph held only its TEST file,
    as a node. A citation check that read edges alone would call such a path
    uncited and send the reader to fix a filename that is already right.
    """
    nodes = [{"id": "test:x", "type": "test", "props": {"path": "a/XTest.java"}}]
    assert I.cited_paths(nodes, []) == {"a/XTest.java"}
    assert I.cited_paths([{"id": "n"}], []) == set(), \
        "a node with no props is not a claim about any path"


def test_the_sentence_itself_names_the_path_not_merely_the_page(built):
    """**My own assertion was measuring the wrong thing** — the seed's path is
    printed in the per-file row below the statement too, so `path in md` passed
    over a sentence that had stopped naming it."""
    seed = _packed(built, PRODUCER[0], UNCITED_SEED)["seed"]
    said = " ".join(I.seed_warning(seed))
    assert UNCITED_SEED in said, (
        "the statement no longer names the seed it is about, and the row "
        "beneath it makes that invisible to a whole-document search")


def test_a_single_seed_query_records_the_fallback_only_when_it_fell_back(built):
    """`impact()` is the other producer — `impact.py --seed` and the MCP
    server reach it — and `diff_seeds` guards `seed_fallback` behind its own
    `if not union`, so the guard INSIDE `seed_fallback` is load-bearing for this
    path alone. Nothing composed the two until a mutation said so."""
    nodes, edges = load_graph(built)
    real = I.impact(nodes, edges, PRODUCER[0], PRODUCER[1])
    nothing = I.impact(nodes, edges, PRODUCER[0], UNCITED_SEED)
    assert "fallback" not in real["seed"], (
        "a query that really did seed carries a fallback record, so every "
        "reader of that key now has to check its basis too")
    assert nothing["seed"]["fallback"]["basis"] == I.UNCITED


def test_each_file_records_its_own_basis_and_not_the_packs(built):
    """A twelve-file diff where two files are unknown to the graph is a
    different fact from one where all twelve are, and only the per-file record
    can say which."""
    files = _packed(built, PRODUCER[0], UNCITED_SEED)["seed"]["files"]
    assert [f["basis"] for f in files] == [I.UNCITED]


def test_the_diff_surface_of_the_cli_states_it_too(built, tmp_path):
    """**The second printer, and covering one of two would be this item's own
    lesson** — a rule hardened where somebody was looking, leaving its
    neighbour. Spawned rather than called: item 96's rule."""
    import subprocess
    diff = tmp_path / "change.diff"
    diff.write_text(
        f"--- a/{UNCITED_SEED}\n+++ b/{UNCITED_SEED}\n"
        "@@ -1,2 +1,3 @@\n class X {\n+  // added\n }\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[3]
    done = subprocess.run(
        [sys.executable, str(root / "tools" / "graph" / "impact.py"),
         "--graph-dir", str(built), "--diff", str(diff),
         "--component", PRODUCER[0]],
        capture_output=True, text=True, cwd=root, timeout=300)
    assert done.returncode == 0, done.stderr
    assert "DIRECTORY-SCOPE ANSWER" in done.stdout


# ── T359: a damaged build_report must report could-not-look, not crash ───────


@pytest.mark.parametrize("content, why", [
    ('{"run_id": "abc', "truncated"),
    ("[1, 2, 3]", "wrong-type-list"),
    ("null", "wrong-type-null"),
    ("42", "wrong-type-int"),
])
def test_a_damaged_build_report_is_a_note_not_a_crash(built, tmp_path, content,
                                                       why):
    """T359. pack.py:182-186 used bare json.loads on build_report.json, so a
    truncated or wrong-shape report raised an uncaught exception where the
    mcp_server sibling already reports could-not-look per shape (T237).
    Absent is not zero: a report that cannot be read is never rendered as an
    empty or zero result."""
    nodes, edges = load_graph(built)
    gdir = tmp_path / "damaged"
    gdir.mkdir()
    (gdir / "build_report.json").write_text(content, encoding="utf-8")
    # Copy graph files so build_pack has something to work with
    for name in ("graph.jsonl", "unresolved.jsonl"):
        src = built / name
        if src.exists():
            (gdir / name).write_bytes(src.read_bytes())
    result = P.build_pack(nodes, edges, PRODUCER[0], [(PRODUCER[1], None)],
                          graph_dir=gdir)
    assert result is not None, f"a {why} build_report must not make build_pack return None"
    graph = result["graph"]
    assert "note" in graph, (
        f"a {why} build_report must carry a could-not-look note, not "
        f"silently fold into an empty dict; got {graph!r}")
    assert graph["note"], f"a {why} build_report note must not be empty"
    # Mutation pass (worker-W357.md, mutant M6): PAIRING is itself part of
    # the assigned fix, not just an implementation detail the renderer
    # happens to paper over — review-W356.md's fixer shopping list item 1
    # says "keep run_id/workspace keys and add note" explicitly, and a
    # mutant that replaces graph_meta with {"note": ...} only when note is
    # truthy survives every other assertion here (the renderer still finds
    # the note first). Assert the pairing directly so that mutant is killed
    # rather than merely "compensated for".
    assert graph.get("run_id") == "", (
        f"a {why} build_report must still carry the normal run_id key "
        f"(default \"\"), paired with the note rather than replacing the "
        f"dict outright; got {graph!r}")
    assert graph.get("workspace") == "", (
        f"a {why} build_report must still carry the normal workspace key "
        f"(default \"\"), paired with the note rather than replacing the "
        f"dict outright; got {graph!r}")
    # review-W356.md finding A: this test originally pinned "run_id absent
    # or None", which was true only because round 1 REPLACED graph_meta with
    # {"note": ...}. That shape is what let the note vanish at the rendered
    # surface (pack.py's renderer reads .get('run_id', '') and silently
    # prints an empty run id). Round 2 PAIRS the note with the normal keys
    # instead (mirroring the docs section's own rows=[] + note convention),
    # so the invariant that matters is not whether run_id is present — it is
    # whether the RENDERED pack.md shows the reason instead of a silently
    # empty run id. Superseding this assertion is the fix, not a workaround:
    # see review-W356.md's fixer shopping list item 1 and worker-W357.md.
    md, _ = P.render_md(dict(result), budget=100_000)
    assert graph["note"] in md, (
        f"a {why} build_report's note must reach the rendered pack.md, not "
        f"only pack.json; got graph={graph!r}, md head={md.splitlines()[:4]!r}")
    assert "graph: run `" not in md.split("## Seeds", 1)[0], (
        f"a {why} build_report must not render as if a run id had been "
        f"measured and found empty; got md head={md.splitlines()[:4]!r}")


# ── T359 round 2 (review-W356.md BLOCK): the surface, nested shapes, the ──────
# ── untested OSError branch, BOM, and the absent shape ────────────────────────


def _report_case(built: Path, tmp_path: Path, name: str, write) -> Path:
    """A fresh graph dir sharing `built`'s graph.jsonl/unresolved.jsonl, with
    build_report.json set up by `write` (a callable taking the path — may
    write text/bytes, or `.mkdir()` it for the directory-shaped case)."""
    gdir = tmp_path / name
    gdir.mkdir()
    for fname in ("graph.jsonl", "unresolved.jsonl"):
        src = built / fname
        if src.exists():
            (gdir / fname).write_bytes(src.read_bytes())
    write(gdir / "build_report.json")
    return gdir


def _spawn_pack_cli(root: Path, graph_dir: Path, out: Path):
    import subprocess
    return subprocess.run(
        [sys.executable, str(root / "tools" / "graph" / "pack.py"),
         "--graph-dir", str(graph_dir), "--seed", f"{PRODUCER[0]}/{PRODUCER[1]}",
         "--out", str(out), "--no-svg"],
        capture_output=True, text=True, cwd=root, timeout=300)


def test_a_truncated_report_renders_differently_from_a_good_empty_one(built, tmp_path):
    """Finding A (review-W356.md), the exact collision it measured: a GOOD
    report whose keys are simply absent (`{}`) and a TRUNCATED report both
    used to render `graph: run \\`\\`` — byte-identical, so could-not-look
    read as measured-empty at the one surface a person reads without opening
    pack.json. Spawned rather than called: item 96's rule, and a CLI's
    contract is a process (WORKER-RULES.md F4)."""
    root = Path(__file__).resolve().parents[3]
    good_empty = _report_case(built, tmp_path, "good_empty",
                              lambda p: p.write_text("{}", encoding="utf-8"))
    truncated = _report_case(built, tmp_path, "truncated",
                             lambda p: p.write_text('{"run_id": "abc',
                                                    encoding="utf-8"))
    out_good, out_bad = tmp_path / "out_good", tmp_path / "out_bad"
    done_good = _spawn_pack_cli(root, good_empty, out_good)
    done_bad = _spawn_pack_cli(root, truncated, out_bad)
    assert done_good.returncode == 0, done_good.stderr
    assert done_bad.returncode == 0, done_bad.stderr
    line_good = (out_good / "pack.md").read_text(encoding="utf-8").splitlines()[2]
    line_bad = (out_bad / "pack.md").read_text(encoding="utf-8").splitlines()[2]
    assert line_good != line_bad, (
        f"a truncated report must not render the same graph line as a good "
        f"empty one; both were {line_good!r}")
    assert "not valid JSON" in line_bad or "truncated" in line_bad, (
        f"the truncated report's rendered line must name the reason; "
        f"got {line_bad!r}")


@pytest.mark.parametrize("workspace_value, why", [
    (None, "workspace-null"),
    ("a string", "workspace-string"),
    ([], "workspace-list"),
])
def test_a_nested_wrong_shape_workspace_is_a_note_not_a_crash(built, tmp_path,
                                                               workspace_value, why):
    """Finding B (review-W356.md): pack.py:204's
    report.get("workspace", {}).get("name", "") guarded only the TOP-level
    isinstance; a dict report whose workspace value is itself not a dict
    still raised the exact AttributeError the T359 fix claims to remove.
    {"workspace": null} is not a contrived shape — it is what a build that
    recorded no workspace writes. Spawned: a CLI's contract is a process."""
    root = Path(__file__).resolve().parents[3]
    content = json.dumps({"run_id": "r2", "workspace": workspace_value})
    gdir = _report_case(built, tmp_path, f"ws_{why}",
                        lambda p: p.write_text(content, encoding="utf-8"))
    out = tmp_path / f"out_{why}"
    done = _spawn_pack_cli(root, gdir, out)
    assert done.returncode == 0, f"{why} must not crash the CLI; stderr={done.stderr}"
    pj = json.loads((out / "pack.json").read_text(encoding="utf-8"))
    assert pj["graph"].get("run_id") == "r2", (
        f"a readable run_id must survive an unreadable workspace; "
        f"got {pj['graph']!r}")


def test_a_build_report_that_is_a_directory_is_a_note_not_a_crash(built, tmp_path):
    """Finding C (review-W356.md): mutant "drop `except OSError`" survived
    round 1's tests because nothing exercised that branch. A DIRECTORY named
    build_report.json satisfies pack.py's `.exists()`-shaped path (now the
    try itself) and fails `read_text()` with PermissionError (Windows) /
    IsADirectoryError (POSIX) — both OSError, no ACL manipulation needed.
    This is the test that kills that mutant (mutation pass: worker-W357.md)."""
    root = Path(__file__).resolve().parents[3]
    gdir = _report_case(built, tmp_path, "directory_report", lambda p: p.mkdir())
    out = tmp_path / "out_directory"
    done = _spawn_pack_cli(root, gdir, out)
    assert done.returncode == 0, (
        f"a directory named build_report.json must not crash the CLI; "
        f"stderr={done.stderr}")
    pj = json.loads((out / "pack.json").read_text(encoding="utf-8"))
    assert "note" in pj["graph"] and pj["graph"]["note"], (
        f"a directory named build_report.json must carry a could-not-look "
        f"note; got {pj['graph']!r}")
    md = (out / "pack.md").read_text(encoding="utf-8")
    assert pj["graph"]["note"] in md, "the note must reach the rendered pack.md too"


def test_a_bom_prefixed_valid_report_is_read_as_valid(built, tmp_path):
    """Note in review-W356.md: a BOM-prefixed but otherwise valid report was
    mislabelled "not valid JSON (truncated or damaged)" — the BOM defeats a
    plain-utf-8 json.loads. Fixed in pack.py only; the identical bug at
    mcp_server.py:120 is a separate row the leader files on its own."""
    root = Path(__file__).resolve().parents[3]
    good_bytes = (built / "build_report.json").read_bytes()
    bom_dir = _report_case(built, tmp_path, "bom",
                           lambda p: p.write_bytes(b"\xef\xbb\xbf" + good_bytes))
    out = tmp_path / "out_bom"
    done = _spawn_pack_cli(root, bom_dir, out)
    assert done.returncode == 0, done.stderr
    pj = json.loads((out / "pack.json").read_text(encoding="utf-8"))
    assert "note" not in pj["graph"], (
        f"a BOM-prefixed but valid report must be read as valid, not "
        f"flagged could-not-look; got {pj['graph']!r}")
    assert pj["graph"]["run_id"], "the real run_id must survive the BOM"


def test_an_absent_report_renders_a_line_saying_so(built, tmp_path):
    """Note in review-W356.md: the absent shape (no build_report.json at
    all, the shape the row itself names first) was swallowed by pack.py's
    `.exists()` pre-check, so graph_meta stayed {} and the pack rendered NO
    graph line at all. Absent is not zero: absence must be stated, exactly
    as mcp_server.py already states it for this same file (T237)."""
    root = Path(__file__).resolve().parents[3]
    gdir = tmp_path / "absent_dir"
    gdir.mkdir()
    for fname in ("graph.jsonl", "unresolved.jsonl"):
        src = built / fname
        if src.exists():
            (gdir / fname).write_bytes(src.read_bytes())
    # Deliberately do NOT write build_report.json.
    out = tmp_path / "out_absent"
    done = _spawn_pack_cli(root, gdir, out)
    assert done.returncode == 0, done.stderr
    head = (out / "pack.md").read_text(encoding="utf-8").split("## Seeds", 1)[0]
    assert "graph:" in head, (
        f"an absent build_report.json must still render a graph line "
        f"stating that, not vanish silently; got head={head!r}")
    assert "absent" in head, f"the line must name the report absent; got {head!r}"


# ── T155: a fully-elided section, argument mutation, non-positive budget ──────


def _render_text_either_shape(pack_arg: dict, budget: int) -> str:
    """Call ``render_md`` and return only the rendered markdown, adapting
    across the T155 return-shape change: the pre-fix function (the commit
    this branch started from) returned a bare ``str``; the fixed function
    returns ``(str, dict)``.
    Exists so the behavioural elision assertion below is reachable — and
    fails for the real, behavioural reason — against BOTH shapes, instead
    of tripping a tuple-unpacking error at the pre-fix base that would mask
    the actual defect (review-W377.md step 4: two of the round-1 tests were
    red at the base on `ValueError: too many values to unpack`, not on the
    collision they claimed to catch)."""
    result = P.render_md(pack_arg, budget=budget)
    return result[0] if isinstance(result, tuple) else result


def test_a_fully_elided_section_does_not_say_none_reached(pack):
    """T155, RED-FIRST HONEST. pack.py's pre-fix `shown or ["(none reached)"]`
    makes a section whose every row was elided by the budget read as a
    section that measured zero rows, which is exactly what item 22's
    loud-elision rule forbids: "(none reached)" means "nothing was found",
    but something WAS found and then hidden. This assertion runs through
    `_render_text_either_shape` so it is reachable, and red for this exact
    behavioural reason, against the pre-T155 base — reproduced by checking
    out that base commit's pack.py/mcp_server.py into this worktree and
    re-running this test; see worker-W382.md."""
    md = _render_text_either_shape(dict(pack), P.BUDGET_FLOOR)
    for _section, title in P._TITLES:
        marker = f"## {title}"
        if marker not in md:
            continue
        rest = md[md.index(marker) + len(marker):]
        next_sec = rest.find("\n## ")
        chunk = rest[:next_sec] if next_sec > 0 else rest
        assert not ("(none reached)" in chunk and "elided" in chunk), (
            f"[{title}] has BOTH '(none reached)' AND an elided count — "
            f"a fully-elided section must say it was elided, not that "
            f"nothing was found; chunk={chunk!r}")


def test_a_fully_elided_section_names_its_elided_count(pack):
    """T155 (review-W377.md's clearing fix). The round-1 test above is the
    only assertion about elision and it is a pure negative: satisfied by a
    mutant that deletes the loud line for a fully-elided section entirely
    (MU1 — `elided[section] = lost` kept as data, the prose line gated on
    `shown`), and equally by a mutant that keeps a line but drops its count
    (MU2 — "... some row(s) elided ..."). This test is positive: for every
    section that budget_info reports as fully elided (its lost count equals
    that section's total row count, derived from the `pack` fixture, never
    typed), the rendered chunk must name that exact count, and the fixture
    must produce at least one such section or the test proves nothing. Uses
    the new (str, dict) return directly — new API surface (F4); red at the
    pre-T155 base by SHAPE (tuple-unpacking a bare str), not behaviour."""
    md, budget_info = P.render_md(dict(pack), budget=P.BUDGET_FLOOR)
    titles = dict(P._TITLES)

    def total_rows(section: str) -> int:
        if section == "contracts_nodes":
            return len(pack["contracts"])
        return len(pack["sections"][section])

    def chunk_for(section: str) -> str:
        marker = f"## {titles[section]}"
        assert marker in md, f"section {section!r} never rendered a heading"
        rest = md[md.index(marker) + len(marker):]
        next_sec = rest.find("\n## ")
        return rest[:next_sec] if next_sec > 0 else rest

    fully_elided = [s for s, lost in budget_info["elided"].items()
                    if lost > 0 and lost == total_rows(s)]
    assert fully_elided, (
        f"fixture/budget invariant: BUDGET_FLOOR must fully elide at "
        f"least one section or this test is vacuous; "
        f"budget_info={budget_info!r}")

    for section in fully_elided:
        lost = budget_info["elided"][section]
        chunk = chunk_for(section)
        assert f"{lost} row(s) elided" in chunk, (
            f"[{section}] fully elided ({lost} row(s), derived from "
            f"budget_info — not typed) but the chunk does not name that "
            f"count; chunk={chunk!r}")
        assert "(none reached)" not in chunk, (
            f"[{section}] fully elided but still prints '(none reached)'; "
            f"chunk={chunk!r}")


def test_render_md_does_not_mutate_its_argument(pack):
    """T155. pack.py:350 `pack["budget"] = ...` writes into the dict the
    caller passed. A function that mutates its argument without documenting
    it is a side-channel: the caller's data changes in a way nothing in
    the signature says, and a second call on the same dict sees stale
    budget data from the first."""
    import copy
    work = copy.deepcopy(pack)
    before_keys = set(work.keys())
    _, budget_info = P.render_md(work, budget=700)
    after_keys = set(work.keys())
    assert before_keys == after_keys, (
        f"render_md mutated its argument: added keys "
        f"{after_keys - before_keys}; the budget metadata must be "
        f"returned or written elsewhere, not injected into the caller's "
        f"dict")
    # Also verify the existing keys were not changed
    for k in before_keys:
        if k == "sections":
            # Deep-compare section contents
            for sec_name in pack["sections"]:
                assert len(work["sections"][sec_name]) == len(pack["sections"][sec_name]), (
                    f"render_md mutated sections[{sec_name!r}]")


def test_render_md_refuses_a_non_positive_budget(pack):
    """T155. render_md accepts 0 and -5 identically — only the CLI
    refuses a budget under 200. A function that silently accepts an
    impossible budget produces output that looks like a pack but contains
    nothing, and the caller has no way to know the budget was never
    honoured. The refusal must name the budget."""
    import copy
    for bad in (0, -5, -100):
        with pytest.raises(ValueError, match=str(bad)):
            P.render_md(copy.deepcopy(pack), budget=bad)
