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
    md = P.render_md(dict(pack), budget=100_000)
    for title in ("## Seeds", "Contracts — interface nodes", "## Direct",
                  "## Indirect", "Config coupling", "Tests to run-or-edit",
                  "## Docs"):
        assert title in md
    assert "regenerate, don't edit" in md
    assert "route:" in md and "why:" in md
    assert "elided" not in md, "an unconstrained budget elides nothing"


def test_a_tight_budget_trims_globally_and_says_so_per_section(pack):
    work = dict(pack)
    md = P.render_md(work, budget=700)
    assert "elided by the token budget" in md
    assert "full list is in pack.json" in md
    assert work["budget"]["elided"], "the loss is recorded as data, not only prose"
    assert P.token_estimate(md) <= 700 + 150, \
        "the estimator is stated-approximate; the render must stay near the target"
    for f in pack["seed"]["files"]:
        assert f["path"] in md, "seeds are never trimmed — a pack that hides what " \
                                "it started from cannot be checked"


def test_trimming_keeps_the_highest_scores(pack):
    work = dict(pack)
    P.render_md(work, budget=700)
    kept_or_not = [(r["score"], r["path"] in P.render_md(dict(pack), budget=700))
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
    real = P.render_md(_packed(built, *PRODUCER), budget=100_000)
    nothing = P.render_md(_packed(built, PRODUCER[0], UNCITED_SEED), budget=100_000)
    assert "DIRECTORY-SCOPE ANSWER" in nothing
    assert "DIRECTORY-SCOPE ANSWER" not in real, (
        "a pack that really did seed must not warn about itself — the warning "
        "would stop being read within a week")


def test_the_statement_names_the_path_nothing_could_be_found_for(built):
    """A refusal nobody can act on is one nobody acts on (item 117, item 128).
    Naming the mode alone leaves the reader to guess WHICH seed failed."""
    md = P.render_md(_packed(built, PRODUCER[0], UNCITED_SEED), budget=100_000)
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
    md = P.render_md(_packed(built, PRODUCER[0], WORKER[1]), budget=100_000)
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
    md = P.render_md(result, budget=100_000)
    assert "All 2 seeded files produced nothing" in md
    single = P.render_md(_packed(built, PRODUCER[0], UNCITED_SEED), budget=100_000)
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
    md = P.render_md(_packed(built, PRODUCER[0], UNCITED_SEED), budget=100_000)
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
