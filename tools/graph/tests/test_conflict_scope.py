"""A disagreement is only a conflict when both declarations are in the same scope.

Item 89. The properties extractor merged every `.properties` file in a component into one
namespace and reported any key whose spellings disagreed. Measured on a real workspace that
produced 2,850 `key-conflict` rows, of which **six** were one file declaring a key twice.
The other 2,844 were files no runtime ever merges:

    64.6%  a bundle beside its own locale variant   labels.properties + labels_en.properties
    31.5%  two independent services sharing a repo  digit-config-service + novu-bridge
     3.5%  a file beside its own test override      server.port = 8080 (main) vs 8280 (test)

The report was 99.8% artifact, and it read as a finding. The item was written expecting an
i18n classifier — most of the noise is message bundles — but i18n is only two thirds of one
defect, and the same merge produces the rest with no message bundle anywhere in sight. A
content heuristic would have fixed the visible majority and left the mechanism intact.

**Every test here forces its condition from literal file contents.** Nothing is asserted
about a real workspace, no threshold is tuned, and no test routes through a language, a
grammar or a platform — item 16's lesson, which item 88 had to learn a second time.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core import model  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import properties as P  # noqa: E402


def run(tmp_path: Path, files: dict[str, str], alias: str = "svc"):
    """Extract over exactly these files. Returns (view, ExtractResult)."""
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    ctx = ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                         files={alias: paths})
    res = P.PropertiesExtractor().extract(ctx)
    return ctx.cache[P.CACHE_KEY][alias], res


def rows(res, kind: str) -> list[dict]:
    return [r for r in res.unresolved if r["kind"] == kind]


#: A bundle and its English rendering. Before item 89 every shared key here was reported as a
#: configuration conflict — this pair alone is the shape behind 64.6% of the real workspace's
#: rows. `title.balance.entry` restating itself is the untranslated convention, not a defect.
LOCALE_PAIR = {
    "messages/labels.properties": ("title.balance.entry=title.balance.entry\n"
                                   "lbl.submit=lbl.submit\n"),
    "messages/labels_en.properties": ("title.balance.entry=Balance Entry\n"
                                      "lbl.submit=Submit\n"),
}

#: Two independent services that share a repository. Neither can see the other's file.
TWO_SERVICES = {
    "backend/config-service/src/main/resources/application.properties": "server.port=8080\n",
    "backend/novu-bridge/src/main/resources/application.properties": "server.port=8280\n",
}

#: A file beside its own test override — 3.5% of the real workspace's rows.
MAIN_AND_TEST = {
    "src/main/resources/application.properties": "server.port=8080\n",
    "src/test/resources/application.properties": "server.port=8280\n",
}


# ── the three shapes that were never conflicts ────────────────────────────────


def test_a_bundle_and_its_locale_variant_are_not_a_conflict(tmp_path: Path):
    """The headline regression. Pre-fix this produced one `key-conflict` per shared key,
    every one of them claiming a deployment ambiguity about a translated label."""
    _, res = run(tmp_path, LOCALE_PAIR)
    assert rows(res, "key-conflict") == []


def test_two_services_in_one_repository_are_not_a_conflict(tmp_path: Path):
    """`server.port` differs because these are two applications, not because one of them is
    wrong. Sharing a git repository is not sharing a configuration scope."""
    _, res = run(tmp_path, TWO_SERVICES)
    assert rows(res, "key-conflict") == []


def test_a_file_and_its_test_override_are_not_a_conflict(tmp_path: Path):
    """A test override is the intended answer for tests, and the least ambiguous fact in the
    repository. It was reported as a question about deployment."""
    _, res = run(tmp_path, MAIN_AND_TEST)
    assert rows(res, "key-conflict") == []


# ── but nothing is dropped ────────────────────────────────────────────────────


def test_the_locale_pair_is_still_reported(tmp_path: Path):
    """Suppressing a false conflict must not delete the observation. A silent exclusion is
    this project's cardinal failure and it re-enters through the door marked 'tidy'."""
    _, res = run(tmp_path, LOCALE_PAIR)
    overlap = rows(res, "cross-scope-keys")
    assert len(overlap) == 1
    assert overlap[0]["pattern"] == "locale-or-profile-variant"
    assert overlap[0]["keys"] == 2
    assert overlap[0]["files"] == ["messages/labels.properties",
                                   "messages/labels_en.properties"]


def test_two_services_are_reported_as_separate_directories(tmp_path: Path):
    _, res = run(tmp_path, TWO_SERVICES)
    overlap = rows(res, "cross-scope-keys")
    assert len(overlap) == 1
    assert overlap[0]["pattern"] == "separate-directories"
    assert overlap[0]["keys"] == 1


def test_no_disagreeing_key_escapes_both_reports(tmp_path: Path):
    """The accounting check. Every key that resolves differently somewhere must appear either
    as a within-file conflict or inside a cross-scope bucket's count — never in neither."""
    files = dict(LOCALE_PAIR)
    files.update(TWO_SERVICES)
    files["one.properties"] = "dup=first\nDUP=second\n"
    _, res = run(tmp_path, files)

    # Recomputed from the file text, independently of the extractor's own grouping: a key
    # disagrees when two declarations of it resolve differently, wherever they live. Asking
    # the extractor which keys disagree would let one bug hide the other.
    decls = []
    for rel, body in files.items():
        for line_no, line in enumerate(body.splitlines(), 1):
            if "=" in line:
                k, v = line.split("=", 1)
                decls.append((P.ids.relaxed_key(k.strip()), v.strip()))
    by_key: dict[str, set] = {}
    for k, v in decls:
        by_key.setdefault(k, set()).add(v)
    disagreeing = {k for k, vs in by_key.items() if len(vs) > 1}

    reported = {r["key"] for r in rows(res, "key-conflict")}
    counted = sum(r["keys"] for r in rows(res, "cross-scope-keys"))
    assert disagreeing, "the fixture stopped producing disagreements — this test proves nothing"
    assert len(reported) + counted >= len(disagreeing), (
        f"{len(disagreeing)} key(s) disagree but only {len(reported)} conflict row(s) and "
        f"{counted} counted overlap(s) account for them — the rest were dropped")


# ── the case that IS a conflict, so the fix cannot be over-applied ────────────


def test_a_key_declared_twice_in_one_file_is_still_a_conflict(tmp_path: Path):
    """Six of the real workspace's 2,850 rows were this, and they are the only ones the
    repository can actually support. Scoping must not take them too."""
    _, res = run(tmp_path, {"a.properties": "orders.publish.topic=one\n"
                                            "ORDERS_PUBLISH_TOPIC=two\n"})
    conflict = rows(res, "key-conflict")
    assert len(conflict) == 1
    assert conflict[0]["values"] == ["one", "two"]
    assert conflict[0]["key"] == "orders.publish.topic"


def test_one_file_conflicting_is_unaffected_by_an_unrelated_second_file(tmp_path: Path):
    """The within-file conflict is found by scope, not by being the only file present."""
    _, res = run(tmp_path, {"a.properties": "k=one\nK=two\n",
                            "far/away/b.properties": "unrelated=x\n"})
    assert len(rows(res, "key-conflict")) == 1


def test_files_that_agree_across_scopes_are_not_reported_at_all(tmp_path: Path):
    """Overlap is only worth reporting when the values differ. Two scopes agreeing is the
    normal state of a repository and reporting it would train a reader to ignore the report."""
    _, res = run(tmp_path, {"a/x.properties": "k=same\n", "b/x.properties": "k=same\n"})
    assert rows(res, "cross-scope-keys") == []
    assert rows(res, "key-conflict") == []


# ── the family rule, whose failure mode is a plausible wrong answer ───────────


def test_a_hyphenated_name_is_not_a_profile_when_no_base_file_exists(tmp_path: Path):
    """`common-labels` parses just as happily as `common` with a profile called `labels`, and
    the first cut of this code made exactly that mistake — silently, because a wrong family
    name is still a family name. Only the directory listing can tell the two apart."""
    present = {("messages", "common-labels"), ("messages", "common-labels_en")}
    assert P.family_of("messages/common-labels.properties", present) == \
        ("messages", "common-labels")
    assert P.family_of("messages/common-labels_en.properties", present) == \
        ("messages", "common-labels")


def test_a_profile_variant_joins_its_base_when_the_base_is_really_there(tmp_path: Path):
    present = {("cfg", "application"), ("cfg", "application-dev")}
    assert P.family_of("cfg/application-dev.properties", present) == ("cfg", "application")


def test_a_variant_whose_base_is_in_another_directory_does_not_join_it(tmp_path: Path):
    """A base file one directory over is a different scope, so the tagged file heads its own
    family. Matching on stem alone would silently merge two directories."""
    present = {("one", "application"), ("two", "application-dev")}
    assert P.family_of("two/application-dev.properties", present) == ("two", "application-dev")


def test_a_locale_pair_in_different_directories_is_not_a_locale_variant(tmp_path: Path):
    _, res = run(tmp_path, {"a/labels.properties": "k=k\n",
                            "b/labels_en.properties": "k=Key\n"})
    overlap = rows(res, "cross-scope-keys")
    assert len(overlap) == 1
    assert overlap[0]["pattern"] == "separate-directories"


def test_two_unrelated_files_in_one_directory_are_reported_as_such(tmp_path: Path):
    """Two Struts action bundles side by side. Same directory, unrelated stems — a genuine
    third case, and the real workspace had four of them."""
    _, res = run(tmp_path, {"act/CentralFCAction.properties": "revenue.title=Central Finance\n",
                            "act/EntertainmentAction.properties": "revenue.title=Entertainment\n"})
    overlap = rows(res, "cross-scope-keys")
    assert len(overlap) == 1
    assert overlap[0]["pattern"] == "same-directory"


# ── the row contract, and the guards on these tests ───────────────────────────


def test_the_overlap_row_carries_everything_its_kind_promises(tmp_path: Path):
    _, res = run(tmp_path, LOCALE_PAIR)
    problems = model.check_unresolved(rows(res, "cross-scope-keys"),
                                      P.UNRESOLVED_FIELDS, "properties")
    assert problems == [], problems


def test_every_reason_this_module_can_emit_is_declared(tmp_path: Path):
    """A pattern outside the declared vocabulary becomes its own group of one, which is how a
    vocabulary rots into free text. Produce all three for real rather than reading the table."""
    produced = set()
    for i, files in enumerate((LOCALE_PAIR, TWO_SERVICES,
                               {"act/A.properties": "k=one\n", "act/B.properties": "k=two\n"})):
        _, res = run(tmp_path / f"case{i}", files)
        produced |= {r["pattern"] for r in rows(res, "cross-scope-keys")}
    assert produced == set(P.UNRESOLVED_PATTERNS["cross-scope-keys"]), (
        f"produced {sorted(produced)} but the module declares "
        f"{sorted(P.UNRESOLVED_PATTERNS['cross-scope-keys'])}")


def test_the_sample_names_a_disagreement_that_is_really_there(tmp_path: Path):
    """A count with nothing to look at is a number a reader learns to scroll past, and a
    sample nobody checks is a number with decoration. So it is checked."""
    _, res = run(tmp_path, TWO_SERVICES)
    sample = rows(res, "cross-scope-keys")[0]["sample"]
    assert sample["key"] == "server.port"
    assert sample["values"] == ["8080", "8280"]
    assert len(sample["files"]) == 2


def test_these_fixtures_still_produce_rows_at_all(tmp_path: Path):
    """The guard on the guard. Every assertion above about an EMPTY conflict list passes
    vacuously if the extractor stopped emitting anything, so something must be non-empty."""
    _, res = run(tmp_path, LOCALE_PAIR)
    assert rows(res, "cross-scope-keys"), (
        "the locale fixture produced no rows of any kind — the tests asserting no conflict "
        "are passing because nothing was extracted, not because the scoping works")


def test_the_report_does_not_depend_on_the_order_files_were_walked(tmp_path: Path):
    """Determinism is a feature: the committed snapshot is diffed in PRs."""
    files = dict(LOCALE_PAIR)
    files.update(TWO_SERVICES)
    _, forward = run(tmp_path / "f", files)
    _, backward = run(tmp_path / "b", dict(reversed(list(files.items()))))
    assert ([r["files"] for r in rows(forward, "cross-scope-keys")]
            == [r["files"] for r in rows(backward, "cross-scope-keys")])
    assert ([r["keys"] for r in rows(forward, "cross-scope-keys")]
            == [r["keys"] for r in rows(backward, "cross-scope-keys")])
