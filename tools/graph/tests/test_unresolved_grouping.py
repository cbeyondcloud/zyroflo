"""The unresolved report is grouped by pattern, and grouping loses nothing.

Found by item 76 against a real workspace: 2,721 of 4,066 rows were a single kind from the
property substrate, spanning 2,537 distinct keys. **Every row was right** — and one
systematic shape reported 2,721 times reads as 2,721 independent decisions. Nobody
adjudicates that list, and a report nobody reads protects nothing, which is the same failure
as a permanently-red gate reached from the opposite direction.

**Suppression is not the fix.** The ambiguity is real and dropping it silently is this
project's cardinal sin, so `sum(group.count) == len(rows)` is the load-bearing assertion in
this file and every group keeps its whole membership.

**The premise the item was written on turned out to be wrong, and that is why the vocabulary
is declared by the extractor rather than assumed by core.** Item 76 recorded the dominant
shape as a production address against a `localhost` default. Measured: that is 2.8%. **81%
are a value restating its own key** — i18n message bundles, where the convention for an
untranslated string is to repeat its key, being read as configuration. Had the grouping
hard-coded the plan's assumption, 2,206 rows would have landed in a bucket labelled with the
wrong cause and looked adjudicated.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.model import (  # noqa: E402
    PATTERN_FIELD, check_patterns, group_unresolved, unresolved_row)
from tools.graph.extractors import properties as P  # noqa: E402


def row(extractor="ext", kind="k", pattern=None, component="c", **over) -> dict:
    r = unresolved_row(extractor, component, kind, "a/b.properties", 1, "because")
    if pattern is not None:
        r[PATTERN_FIELD] = pattern
    r.update(over)
    return r


# ── the mechanism loses nothing ────────────────────────────────────────────────
def test_every_row_survives_grouping():
    """**The assertion this file exists for.** Grouping is a way of READING the report, not
    of shortening it: a group that dropped its members would have replaced four thousand rows
    nobody reads with one line nobody can act on."""
    rows = [row(pattern="a") for _ in range(5)] + [row(pattern="b") for _ in range(3)]
    groups = group_unresolved(rows)
    assert sum(g.count for g in groups) == len(rows)
    assert sorted(r for g in groups for r in map(id, g.rows)) == sorted(map(id, rows))


def test_groups_are_keyed_on_extractor_kind_and_pattern():
    rows = [row(extractor="x", kind="k", pattern="p"),
            row(extractor="y", kind="k", pattern="p"),
            row(extractor="x", kind="j", pattern="p"),
            row(extractor="x", kind="k", pattern="q"),
            row(extractor="x", kind="k", pattern="p")]
    assert {(g.extractor, g.kind, g.pattern, g.count) for g in group_unresolved(rows)} == {
        ("x", "k", "p", 2), ("y", "k", "p", 1), ("x", "j", "p", 1), ("x", "k", "q", 1)}


def test_the_largest_pattern_comes_first():
    rows = [row(pattern="small")] + [row(pattern="large") for _ in range(4)]
    assert [g.pattern for g in group_unresolved(rows)] == ["large", "small"]


def test_ties_break_deterministically():
    """A report that reorders itself between runs cannot be diffed, and a validation report
    nobody can diff is one nobody watches."""
    rows = [row(extractor="b", pattern="z"), row(extractor="a", pattern="y")]
    first = [(g.extractor, g.pattern) for g in group_unresolved(rows)]
    assert first == [(g.extractor, g.pattern) for g in group_unresolved(list(reversed(rows)))]
    assert first == [("a", "y"), ("b", "z")]


def test_an_empty_report_groups_to_nothing_rather_than_failing():
    assert group_unresolved([]) == []


# ── a pattern is declared, never merely permitted ──────────────────────────────
def test_a_row_with_no_pattern_is_ungraded_and_says_so():
    """`graded` is what stops a whole KIND in one bucket from being rendered as a pattern
    somebody chose. The two read identically in a summary table and only one has been
    thought about."""
    (g,) = group_unresolved([row()])
    assert g.pattern == "" and g.graded is False
    (h,) = group_unresolved([row(pattern="named")])
    assert h.graded is True


def test_components_are_collected_and_deduplicated():
    rows = [row(component="a"), row(component="b"), row(component="a")]
    (g,) = group_unresolved(rows)
    assert g.components == ("a", "b")


def test_a_group_whose_rows_name_no_component_reports_none_rather_than_an_empty_name():
    """`contested-service` leaves `component` empty ON PURPOSE — the finding is a collision
    BETWEEN components and filing it against one would imply the decision the link pass just
    refused to make. That must not surface as a component named the empty string."""
    (g,) = group_unresolved([row(component=""), row(component="")])
    assert g.components == ()


# ── the checker ────────────────────────────────────────────────────────────────
def test_a_declared_kind_must_carry_a_pattern():
    problems = check_patterns([row(kind="k")], {"k": ("a", "b")}, "ext")
    assert len(problems) == 1 and "names none" in problems[0]


def test_a_pattern_outside_the_declared_vocabulary_is_reported():
    """An undeclared pattern becomes its own group of one, which is how a closed vocabulary
    rots into free text — one plausible spelling at a time."""
    problems = check_patterns([row(kind="k", pattern="typo")], {"k": ("a",)}, "ext")
    assert len(problems) == 1 and "not in the declared vocabulary" in problems[0]


def test_a_kind_with_no_declared_vocabulary_is_left_alone():
    assert check_patterns([row(kind="other")], {"k": ("a",)}, "ext") == []


def test_a_conforming_row_is_not_reported():
    assert check_patterns([row(kind="k", pattern="a")], {"k": ("a", "b")}, "ext") == []


def test_every_violation_is_reported_not_just_the_first():
    rows = [row(kind="k"), row(kind="k", pattern="typo"), row(kind="k", pattern="a")]
    assert len(check_patterns(rows, {"k": ("a",)}, "ext")) == 2


# ── the declaration reaches registration ───────────────────────────────────────
def test_the_pattern_declaration_actually_reaches_a_registration():
    """Item 85's mechanism first shipped doing nothing because a module-level declaration was
    read off the instance and resolved to `{}` — indistinguishable from no declaration. This
    is the same trap one item later, so it gets the same catcher rather than the same lesson.
    """
    regs = {r.name: r for r in B.discover_extractors()}
    assert regs["properties"].unresolved_patterns.get("key-conflict"), (
        "the properties extractor declares UNRESOLVED_PATTERNS and the registration has an "
        "empty one, so the vocabulary is silently unenforced")


def test_a_bare_string_vocabulary_is_fatal():
    class Bad:
        name, INPUT_GLOBS = "bad", ("*.x",)
        UNRESOLVED_PATTERNS = {"k": "one-pattern"}
    with pytest.raises(B.BuildError, match="per character"):
        B._unresolved_patterns(Bad(), "bad", "where")


def test_a_non_dict_vocabulary_is_fatal():
    class Bad:
        name, INPUT_GLOBS = "bad", ("*.x",)
        UNRESOLVED_PATTERNS = ["k"]
    with pytest.raises(B.BuildError, match="expected a dict"):
        B._unresolved_patterns(Bad(), "bad", "where")


def test_the_two_declarations_are_read_by_one_checked_path():
    """`UNRESOLVED_FIELDS` and `UNRESOLVED_PATTERNS` have the same shape and the same traps.
    Two readers would be two chances to tighten one and not the other."""
    class Both:
        name, INPUT_GLOBS = "both", ("*.x",)
        UNRESOLVED_FIELDS = {"k": ("f",)}
        UNRESOLVED_PATTERNS = {"k": ("p",)}
    obj = Both()
    assert B._unresolved_fields(obj, "both", "w") == {"k": ("f",)}
    assert B._unresolved_patterns(obj, "both", "w") == {"k": ("p",)}


# ── what the properties extractor actually classifies ──────────────────────────
@pytest.mark.parametrize("key, values, expected", [
    # The 81% case, and the one the plan did not predict: a label bundle where the
    # convention for an untranslated string is to repeat its own key.
    ("lbl.party.billdate", ["Party Bill Date", "lbl.party.billdate"], "value-restates-key"),
    # Relaxed binding collapsed the case difference that made these "two spellings", so the
    # restatement test has to be exactly as loose as the binding that created the conflict.
    ("contract.applto", ["Applied To", "contract.applTo"], "value-restates-key"),
    ("a.b.host", ["a_b_HOST", "Something"], "value-restates-key"),
    # The shape item 76 predicted would dominate. It is 2.8%.
    ("svc.host", ["http://svc.prod:8080", "http://localhost:8086"], "local-vs-remote"),
    ("svc.host", ["http://127.0.0.1:1", "http://localhost:2"], "all-local"),
    ("svc.host", ["http://a.prod:8080", "http://b.prod:8080"], "differing-values"),
])
def test_a_key_conflict_is_classified_by_the_shape_of_its_disagreement(key, values, expected):
    assert P.conflict_pattern(key, values) == expected


def test_restating_the_key_wins_over_the_address_shapes():
    """Order is load-bearing. A row whose value restates its key is not a configuration
    conflict at all, and classifying it by its addresses would file a translation gap beside
    a service pointing at two hosts."""
    assert P.conflict_pattern("x.localhost.y", ["x.localhost.y", "http://localhost"]) == \
        "value-restates-key"


def test_every_pattern_the_classifier_can_return_is_declared():
    """A pattern the checker would reject the moment it occurred. The classifier and the
    vocabulary are two lists of the same strings, and nothing else keeps them in step."""
    produced = {
        P.conflict_pattern("k", ["k"]),
        P.conflict_pattern("k", ["http://localhost", "http://prod"]),
        P.conflict_pattern("k", ["http://localhost:1", "http://127.0.0.1:2"]),
        P.conflict_pattern("k", ["http://a", "http://b"]),
    }
    assert produced == set(P.UNRESOLVED_PATTERNS["key-conflict"]), (
        "the classifier and the declared vocabulary have drifted apart")
