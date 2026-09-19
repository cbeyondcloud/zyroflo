"""Every emission site provides the fields its kind promises — checked without running it.

Item 85 built the contract and a checker that enforces it on every row a build produces.
That checker is necessary and not sufficient, and the gap is exact: **it only ever sees rows
that were emitted.** An emission site on a cold branch — the one that runs when a grammar is
missing, or when a file that normally parses does not — produces no row on this machine or
on CI, so it can violate the contract indefinitely while every run stays green. Item 85
measured 530 violations on a real workspace and drove them to 0, and three of these survived
that measurement untouched, because none of the three branches ran.

Where they would eventually surface is the worst place available. A violation is reported as
`contract_violations` and exits 2, saying in as many words that this is a **defect in the
harness** rather than a finding about the workspace. So the first person to see it would be
an adopter, on their own repository, being told by the tool that the tool is broken — about
a code path the harness's own suite never took.

This walks the syntax tree instead, so a branch that never runs is checked anyway. What it
found on its first run:

* `tests` promised `error` on `unparsable` and passed none.
* `kafka` and `spring` each promised `language` on `grammar-unavailable` and named none —
  the second while carrying a `files` count, so the row said how much was lost and never
  what was lost.

It also sees emissions the existing kind-declaration test cannot. That one recognises a
`{"kind": ...}` dict literal, and `kafka` builds every one of its rows through a helper
call, so all five of its kinds were invisible to it and two had never been declared at all.
**A test that reads clean because it cannot see its subject** is this project's recurring
failure wearing the uniform of the guard against it.

**A site this cannot decide fails.** The alternative is a checker that quietly shrinks as
the code grows more indirect — the same failure again, one level further up. The fix for a
failure here is to build the row through `model.unresolved_row`, which is legible to the
runtime checker and to this test alike.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.graph.core.model import UNRESOLVED_COMMON

GRAPH = Path(__file__).resolve().parents[1]

#: `unresolved_row`'s positional order. A row built through it carries these whatever the
#: call site names them, so they are credited without reading its keywords.
_ROW_POSITIONAL = ("extractor", "component", "kind", "source_file", "line", "why")


@dataclass
class Site:
    """One place a row enters an `ExtractResult`, and what it can be shown to carry."""

    where: str
    kind: str | None
    keys: set[str]
    undecided: str = ""


@dataclass
class Helper:
    """A module-level function that returns a row literal, e.g. `kafka._unresolved`."""

    keys: set[str]
    kind: str | None
    passthrough: bool          # has `**extra`, so call-site keywords reach the row


def _dict_shape(node: ast.Dict) -> tuple[set[str], str | None, bool]:
    """(static keys, constant kind, whether a `**` spread widens it)."""
    keys, kind, spread = set(), None, False
    for k, v in zip(node.keys, node.values):
        if k is None:
            spread = True
            continue
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.add(k.value)
            if k.value == "kind" and isinstance(v, ast.Constant):
                kind = v.value
    return keys, kind, spread


def _helpers(tree: ast.Module) -> dict[str, Helper]:
    """Module-level functions whose body returns a row dict literal."""
    found: dict[str, Helper] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Dict):
                keys, kind, spread = _dict_shape(inner.value)
                found[node.name] = Helper(keys, kind, spread)
                break
    return found


def _from_call(call: ast.Call, helpers: dict[str, Helper]) -> Site | None:
    """What a constructor call can be shown to produce, or None if it is not one."""
    name = call.func.id if isinstance(call.func, ast.Name) else None
    if name is None:
        return None

    if name == "unresolved_row":
        keys = set(_ROW_POSITIONAL[: len(call.args)])
        kind = call.args[2].value if len(call.args) >= 3 and \
            isinstance(call.args[2], ast.Constant) else None
        for kw in call.keywords:
            if kw.arg is None:
                return Site("", None, set(), "a `**` spread into unresolved_row")
            keys.add(kw.arg)
            if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                kind = kw.value.value
        return Site("", kind, keys)

    helper = helpers.get(name)
    if helper is None:
        return None

    keys, kind = set(helper.keys), helper.kind
    # A helper that names its own kind decides it; otherwise the call site does, and the
    # helpers here all take it third, matching `unresolved_row`.
    if kind is None and len(call.args) >= 3 and isinstance(call.args[2], ast.Constant):
        kind = call.args[2].value
    for kw in call.keywords:
        if kw.arg is None:
            return Site("", None, set(), f"a `**` spread into {name}()")
        if helper.passthrough:
            keys.add(kw.arg)
        if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
            kind = kw.value.value
    return Site("", kind, keys)


def _is_merge(node: ast.expr) -> bool:
    """`res.unresolved.extend(other.unresolved)` — rows already accounted for at their own
    emission site. Provably a move rather than a creation, so it is skipped rather than
    failed; nothing else is granted that.
    """
    return isinstance(node, ast.Attribute) and node.attr == "unresolved"


def _sites(rel: str, tree: ast.Module) -> list[Site]:
    helpers = _helpers(tree)
    out: list[Site] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("append", "extend")
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "unresolved"):
            continue
        where = f"{rel}:{node.lineno}"
        arg = node.args[0] if node.args else None
        if arg is None or _is_merge(arg):
            continue
        candidates = arg.elts if isinstance(arg, (ast.List, ast.Tuple)) else [arg]
        for item in candidates:
            if isinstance(item, ast.Dict):
                keys, kind, spread = _dict_shape(item)
                out.append(Site(where, kind, keys,
                                "a `**` spread in the row literal" if spread else ""))
            elif isinstance(item, ast.Call) and (site := _from_call(item, helpers)):
                site.where = where
                out.append(site)
            else:
                out.append(Site(where, None, set(),
                                f"a {type(item).__name__} this test cannot read"))
    return out


def _declared(tree: ast.Module, attr: str = "UNRESOLVED_FIELDS") -> dict[str, tuple[str, ...]]:
    """A module's top-level `attr = {...}` contract table.

    A declaration is legible in three spellings, all of which bind the WHOLE mapping as one
    dict literal to the name at module scope. Each is exercised by a positive control
    (`test_declared_reads_each_supported_form`) so a future narrowing that drops one is caught:

    * plain            ``NAME = {...}``                     — ``ast.Assign``, one ``Name`` target
    * annotated        ``NAME: dict[...] = {...}``          — ``ast.AnnAssign``. A legal,
      idiomatic spelling that this walk could not see when it matched ``ast.Assign`` only, so a
      correctly-declared kind read as emitted-but-undeclared and ``core/lib/emit.py`` was forced
      to drop its annotation to satisfy the guard (T412; the gap `emit.py` filed separately).
    * chained/multiple ``A = NAME = {...}`` / ``NAME = A = {...}`` — ``ast.Assign``, >1 target

    Deliberately NOT read — swept 2026-09-19, and every one absent from the tree (all 23 real
    tables are plain assigns), so each is a recorded decision rather than an oversight. None is
    a single-literal declaration; reading it would mean partially evaluating assembly or
    guessing a runtime value:

    * tuple/list unpack ``NAME, A = {...}, {...}`` — the value is a tuple, not one dict bound to
      ``NAME``; recovering it needs a positional correspondence this table never needs.
    * augmented        ``NAME |= {...}`` / ``NAME += ...`` — mutates a prior binding, so the
      whole mapping is not one node to evaluate.
    * incremental      ``NAME = {}`` then ``NAME["k"] = ...`` / ``NAME.update(...)`` — only the
      initial literal is captured; a table built in pieces is not statically whole.
    * conditional/loop ``if C: NAME = {...}`` — bound inside a compound statement, not a stable
      module constant; this reads ``tree.body`` only, by design.
    * computed         ``NAME = f()`` — not a literal (``literal_eval`` would raise), and a
      computed contract could not be checked against a per-kind table regardless.
    """
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == attr for t in node.targets):
            return ast.literal_eval(node.value)
        if (isinstance(node, ast.AnnAssign) and node.value is not None
                and isinstance(node.target, ast.Name) and node.target.id == attr):
            return ast.literal_eval(node.value)
    return {}


def _all_sites() -> list[tuple[str, Site, dict[str, tuple[str, ...]]]]:
    found = []
    for path in sorted(GRAPH.rglob("*.py")):
        rel = path.relative_to(GRAPH).as_posix()
        if rel.startswith("tests/") or "__pycache__" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        declared = _declared(tree)
        for site in _sites(rel, tree):
            found.append((rel, site, declared))
    return found


# ── the checks ─────────────────────────────────────────────────────────────────
def test_this_test_can_see_the_emission_sites_at_all():
    """The guard on the guard. Every assertion below is vacuously true against an empty
    list, so a refactor that moves rows out of `res.unresolved.append(...)` would turn this
    file green by blinding it — which is the failure it exists to catch, committed by it.
    """
    sites = _all_sites()
    assert len(sites) >= 30, (
        f"only {len(sites)} emission site(s) found. The extractors emitted roughly forty "
        f"when this was written, so either a large number have gone or this test can no "
        f"longer recognise how they are written.")
    assert len({rel for rel, _, _ in sites}) >= 8, "too few modules seen to be believable"


def test_every_emission_site_is_legible():
    """An undecidable site fails. A checker that quietly narrows as the code grows more
    indirect protects less every year while reporting the same green."""
    undecided = [f"{s.where}  ({s.undecided})" for _, s, _ in _all_sites() if s.undecided]
    assert not undecided, (
        "these rows cannot be read statically, so nothing verifies they carry what their "
        "kind promises:\n  " + "\n  ".join(undecided) +
        "\nBuild the row through `model.unresolved_row` so both checkers can read it.")


def test_every_emission_site_names_its_kind():
    """A row whose kind is computed cannot be checked against a per-kind contract, and the
    per-kind half is the whole point."""
    nameless = [s.where for _, s, _ in _all_sites() if not s.undecided and s.kind is None]
    assert not nameless, (
        f"these rows do not state a literal `kind`: {nameless}. The contract is keyed on "
        f"kind, so a computed one is unenforceable.")


def test_every_emission_site_carries_what_its_kind_promises():
    """**The three this found.** All on branches that run when a grammar is missing or a
    parse fails — never here, never on CI, and so never once on a row item 85 could see."""
    problems = []
    for _, site, declared in _all_sites():
        if site.undecided or site.kind is None:
            continue
        required = set(UNRESOLVED_COMMON) | set(declared.get(site.kind, ()))
        if missing := sorted(required - site.keys):
            problems.append(f"{site.where}  kind={site.kind!r}  missing {missing}")
    assert not problems, (
        "these emission sites promise fields they do not pass:\n  " + "\n  ".join(problems))


def test_every_emitted_kind_is_declared_by_its_module():
    """The dict-literal version of this could not see `kafka` at all — it builds every row
    through a helper — so two of its kinds had never been declared and it read as clean."""
    undeclared: dict[str, set[str]] = {}
    for rel, site, declared in _all_sites():
        if site.undecided or site.kind is None or site.kind in declared:
            continue
        undeclared.setdefault(rel, set()).add(site.kind)
    assert not undeclared, (
        f"kinds emitted with no entry in their module's UNRESOLVED_FIELDS: "
        f"{ {k: sorted(v) for k, v in undeclared.items()} }. Add one, with an empty tuple "
        f"if the kind genuinely promises nothing beyond the common fields.")


def test_a_missing_promised_field_is_actually_detected():
    """Mutation check on the machinery: the assertions above are only worth their runtime if
    a real violation still trips them."""
    tree = ast.parse(
        'UNRESOLVED_FIELDS = {"k": ("language",)}\n'
        'def f(res):\n'
        '    res.unresolved.append({"extractor": "e", "component": "c", "kind": "k",\n'
        '                           "source_file": "f", "line": 0, "why": "w"})\n')
    site = _sites("x.py", tree)[0]
    required = set(UNRESOLVED_COMMON) | set(_declared(tree)[site.kind])
    assert sorted(required - site.keys) == ["language"]


def test_no_pattern_vocabulary_is_declared_for_a_kind_nothing_emits():
    """A vocabulary for a kind that does not exist is enforced against nothing and reads, in
    the declaration, exactly like one that is doing work. Same shape as the declarations that
    never reached a registration — a rule with no subject, printing green."""
    orphans: dict[str, list[str]] = {}
    for path in sorted(GRAPH.rglob("*.py")):
        rel = path.relative_to(GRAPH).as_posix()
        if rel.startswith("tests/") or "__pycache__" in rel:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        declared = _declared(tree, "UNRESOLVED_PATTERNS")
        if not declared:
            continue
        emitted = {s.kind for s in _sites(rel, tree) if s.kind}
        if missing := sorted(set(declared) - emitted):
            orphans[rel] = missing
    assert not orphans, (
        f"UNRESOLVED_PATTERNS declared for kinds this module never emits: {orphans}. "
        f"A vocabulary with no rows to govern enforces nothing.")


# ── T412: `_declared` must read every legal spelling of a top-level table ────────
def _emits(kind: str) -> str:
    """Source for a function that appends one row of `kind` through `res.unresolved.append`,
    so `_sites` reads a site whose kind is `kind`."""
    return (
        'def f(res):\n'
        '    res.unresolved.append({"extractor": "e", "component": "c", "kind": %r,\n'
        '                           "source_file": "s", "line": 0, "why": "w"})\n' % kind
    )


#: Every spelling `_declared` claims to support — each binds the WHOLE mapping as one dict
#: literal to the name at module scope. Exercised end to end by the parametrised control below,
#: so NARROWING `_declared` to miss any spelling reddens the matching case rather than silently
#: blinding the guard, which is how T412 (the annotated spelling) went unseen in the first place.
SUPPORTED_DECLARATION_FORMS = [
    ("plain assign", 'UNRESOLVED_FIELDS = {"k": ()}\n'),
    ("annotated assign", 'UNRESOLVED_FIELDS: dict[str, tuple[str, ...]] = {"k": ()}\n'),
    ("chained assign, name last", 'OTHER = UNRESOLVED_FIELDS = {"k": ()}\n'),
    ("chained assign, name first", 'UNRESOLVED_FIELDS = OTHER = {"k": ()}\n'),
]


def test_an_annotated_declaration_is_not_reported_undeclared():
    """Red-first for T412. ``UNRESOLVED_FIELDS: T = {...}`` (``ast.AnnAssign``) is a legal,
    idiomatic spelling; a kind it declares must not read as emitted-but-undeclared. Against the
    ``ast.Assign``-only checker ``_declared`` returned ``{}``, so this kind read as undeclared
    one line below its own declaration — the exact false red that forced ``core/lib/emit.py``
    to drop its annotation and carry a "Deliberately UNANNOTATED" comment."""
    tree = ast.parse(
        'UNRESOLVED_FIELDS: dict[str, tuple[str, ...]] = {"unknown-record-kind": ()}\n'
        + _emits("unknown-record-kind"))
    declared = _declared(tree)
    site = _sites("emit.py", tree)[0]
    assert site.kind == "unknown-record-kind"
    assert site.kind in declared, (
        "a kind declared via `NAME: T = {...}` read as emitted-but-undeclared; "
        "`_declared` sees only `ast.Assign`, not the annotated `ast.AnnAssign` form")


@pytest.mark.parametrize("label, declaration", SUPPORTED_DECLARATION_FORMS)
def test_declared_reads_each_supported_form(label, declaration):
    """Positive control over every supported spelling. Each is proven visible both to
    ``_declared`` directly and to the emitted-but-undeclared check, so a future narrowing that
    drops any one spelling — the way T412 will recur — is caught as a red on that case."""
    tree = ast.parse(declaration + _emits("k"))
    declared = _declared(tree)
    assert declared == {"k": ()}, f"{label}: `_declared` did not read the table"
    site = _sites("m.py", tree)[0]
    assert site.kind == "k" and site.kind in declared, (
        f"{label}: a correctly-declared kind read as undeclared")


def test_every_supported_form_has_a_control():
    """Guard on the control. Emptying or shrinking ``SUPPORTED_DECLARATION_FORMS`` would make
    the parametrised control vacuous (and, under ``SDLC_FORBID_SKIPS``, an empty parametrise
    skips → CI red); pinning the exact label set catches a form being dropped either way."""
    assert {label for label, _ in SUPPORTED_DECLARATION_FORMS} == {
        "plain assign", "annotated assign",
        "chained assign, name last", "chained assign, name first"}


def test_an_undeclared_kind_is_still_reported_after_the_widening():
    """The widened ``_declared`` must not soften the guard into never failing — a guard that
    stops failing at all is worse than the narrow one. A kind emitted with NO entry in the
    module's table, even when the table is written in the annotated form, is still caught."""
    tree = ast.parse(
        'UNRESOLVED_FIELDS: dict[str, tuple[str, ...]] = {"declared-kind": ()}\n'
        + _emits("ghost-kind"))
    declared = _declared(tree)
    site = _sites("m.py", tree)[0]
    assert "declared-kind" in declared, "the annotated table itself must be read"
    assert site.kind == "ghost-kind"
    assert site.kind not in declared, (
        "an undeclared kind must still be reported after widening `_declared`")
