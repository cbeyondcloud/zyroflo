"""Item 107 - the wizard's contract: one derived schema per governing document.

The owner ruled on 2026-08-30 that configs STAY YAML and the wizard's contract
is a SCHEMA rather than a storage format. So this is not a migration; it is a
machine-readable statement of what a valid document looks like, DERIVED from the
constants the harness already reads them with.

**Three properties, and the third is the one that makes the other two worth
having.** The schema must be derived (not transcribed), it must be committed and
current (the `requirements.txt` guard), and it must ACCEPT AND REJECT WHAT THE
REAL READER DOES - because a schema a wizard obeys that disagrees with the
harness is a wizard producing documents the harness refuses.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# A plain import, not `importorskip`, and for `pydantic`'s reason one file over:
# the dependency guard reads IMPORTS, so a package loaded by a call looks
# undeclared-and-unused to it - and CI runs with SDLC_FORBID_SKIPS, so a skip
# would fail the suite rather than excuse the absence.

from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import schema as SC  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402


def validator(name: str):
    return jsonschema.Draft202012Validator(SC.BUILDERS[name]())


def accepts(name: str, doc: dict) -> bool:
    return validator(name).is_valid(doc)


# ── committed, and current ─────────────────────────────────────────────────


def test_every_committed_schema_matches_the_constants_today():
    """**The `requirements.txt` guard.** That file's header had said *regenerate
    me* since item 68 and it drifted anyway - silently, for exactly the people
    with no lock to fall back on. A generated file nobody diffs is not a
    contract."""
    assert SC.drift() == [], SC.drift()


def test_a_drifted_schema_is_named_with_the_line_that_differs(tmp_path):
    """A guard nobody can watch fail is a guard nobody has checked - item 115's
    lesson, applied when the guard is written."""
    fake = tmp_path / "harness"
    (fake / SC.SCHEMA_DIR).mkdir(parents=True)
    for doc in SC.BUILDERS:
        SC.path_of(doc, fake).write_text(SC.rendered(doc), encoding="utf-8")
    assert SC.drift(fake) == []

    bad = SC.path_of("policy", fake)
    bad.write_text(bad.read_text(encoding="utf-8")
                   .replace('"title"', '"titel"', 1), encoding="utf-8")
    said = SC.drift(fake)
    assert len(said) == 1 and said[0].startswith("policy:")
    assert "first at line" in said[0] and "--write" in said[0]


def test_a_missing_schema_is_named_rather_than_passed(tmp_path):
    """Absent is not current. The distinction this package keeps having to
    make, at the one place a wizard would notice it."""
    fake = tmp_path / "harness"
    (fake / SC.SCHEMA_DIR).mkdir(parents=True)
    said = SC.drift(fake)
    assert len(said) == len(SC.BUILDERS)
    assert all("is not committed" in s for s in said)


def test_the_committed_schemas_are_valid_json_schema():
    """A schema that is not itself valid is a contract nothing can read."""
    for name in SC.BUILDERS:
        doc = json.loads(SC.path_of(name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(doc)
        assert doc["$id"].endswith(f"{name}.schema.json")


# ── derived, not transcribed ───────────────────────────────────────────────


def test_the_enums_come_from_the_shipped_constants():
    """The whole design. Rename a constant and this fails by name rather than
    emitting a schema with no enum on it."""
    policy = SC.BUILDERS["policy"]()["properties"]
    assert policy["gate_profile_floor"]["enum"] == list(P.GATE_PROFILES)
    assert policy["signing_floor"]["enum"] == list(P.SIGNING_MODES)
    assert policy["self_approval"]["enum"] == list(P.SELF_APPROVAL)
    assert policy["block_on_floor"]["items"]["enum"] == list(P.SEVERITIES)

    profile = SC.BUILDERS["profile"]()["properties"]
    assert profile["phases"]["items"]["enum"] == list(ST.NAMES)
    assert profile["gate_profile"]["enum"] == list(P.GATE_PROFILES)


def test_a_missing_constant_refuses_rather_than_emitting_a_blank_enum(monkeypatch):
    """**The failure mode worth naming.** A schema quietly missing its enum
    accepts every string, which is a contract that agrees with nothing."""
    monkeypatch.setattr(P, "GATE_PROFILES", ())
    with pytest.raises(SC.SchemaError, match="GATE_PROFILES"):
        SC.BUILDERS["policy"]()


def test_every_policy_key_the_harness_ships_is_in_the_schema():
    """Derived from `DEFAULTS`, so a dial added to the constitution appears
    here without anybody remembering to add it."""
    assert set(SC.BUILDERS["policy"]()["properties"]) == set(P.DEFAULTS)


def test_the_profile_schema_carries_every_component_key():
    """`COMPONENT_KEYS` is the reader's own list, and item 46 paid for the case
    where a key the installer writes is absent from the reader's."""
    comp = (SC.BUILDERS["profile"]()["properties"]["components"]
            ["items"]["properties"])
    for key in P.COMPONENT_KEYS:
        assert key in comp, key


def test_the_release_dials_come_from_the_steps_the_cut_runs():
    """Including item 124's `artifact_version_command`, which is a dial rather
    than a step - so a schema built from `CUT_STEPS` alone would omit it."""
    from tools.sdlc import release as R
    dials = SC.BUILDERS["profile"]()["properties"]["release"]["properties"]
    for step in R.CUT_STEPS:
        assert f"{step}_command" in dials, step
    assert R.ARTIFACT_VERSION_DIAL in dials


def test_the_quarantine_keys_are_asked_for_not_spelled():
    """Item 94's rule: one home per name. All three keys are owned by other
    modules and this schema asks them."""
    from tools.review import sca
    from tools.sdlc import learning, suite
    props = SC.BUILDERS["profile"]()["properties"]
    for key in (suite.KNOWN_FAILURES, sca.QUARANTINE_KEY, learning.SUPPRESS_KEY):
        assert key in props, key
        assert props[key]["type"] == "array"


# ── the schema agrees with the READER, which is the point ──────────────────


def test_the_policy_schema_accepts_everything_the_reader_accepts():
    """**The property that makes the schema a contract rather than a
    document.** A wizard obeying a schema stricter than the harness produces
    documents the harness would have taken."""
    from tools.tests.test_policy import ACCEPTED
    bad = [doc for doc in ACCEPTED if not accepts("policy", doc)]
    assert bad == [], bad


def test_the_policy_schema_rejects_what_the_reader_rejects():
    """The other direction, and the one that matters more: a policy misread is
    worse than one missing, because it looks enforced."""
    from tools.tests.test_policy import REJECTED
    # `.items()`, not the dict itself. The first cut iterated REJECTED as a
    # LIST and got its English labels - and a string is not an object, so the
    # schema "rejected" all nineteen without ever seeing a document. It passed
    # on the first run, which is the only reason it was looked at.
    assert isinstance(REJECTED, dict) and len(REJECTED) > 10, REJECTED
    taken = [why for why, doc in REJECTED.items() if accepts("policy", doc)]
    assert taken == [], taken


def test_the_shipped_policy_template_validates_against_its_own_schema():
    """The document every adopter starts from. A schema its own template fails
    is one nobody can act on."""
    text = (ROOT / "sdlc_templates" / "policy.yml").read_text(encoding="utf-8")
    validator("policy").validate(P.parse(text))


def test_the_shipped_profile_template_validates_against_its_own_schema():
    """The profile carries `@@PLACEHOLDER@@` values before the installer fills
    them, so this validates the RENDERED shape rather than the raw template -
    which is what a wizard would be emitting."""
    yaml = pytest.importorskip("yaml")
    raw = (ROOT / "sdlc_templates" / "profile.yml").read_text(encoding="utf-8")
    rendered = (raw.replace("@@SCOPE@@", "repo")
                   .replace("@@GATE_PROFILE@@", "solo")
                   .replace("@@COVERAGE_THRESHOLD@@", "80")
                   .replace("@@COMPONENTS@@", ""))
    rendered = chr(10).join(
        l for l in rendered.splitlines() if "@@" not in l)
    doc = {k: v for k, v in (yaml.safe_load(rendered) or {}).items()
           if v is not None}
    validator("profile").validate(doc)


@pytest.mark.parametrize("doc,why", [
    ({"gate_profile": "yolo"}, "a regime nobody defined"),
    ({"signing": "pgp"}, "a signing mode the harness cannot do"),
    ({"phases": ["intake", "nonsense"]}, "a phase that is not one"),
    ({"coverage_threshold": 101}, "a percentage above 100"),
    ({"coverage_scope": "some"}, "a scope neither changed nor whole"),
])
def test_the_profile_schema_refuses_what_the_harness_would(doc, why):
    assert not accepts("profile", doc), why


def test_the_profile_schema_does_not_refuse_keys_the_harness_accepts():
    """**Deliberately open.** The profile is the document an adopter EXTENDS -
    the installer writes keys per stack - so refusing an unknown key would make
    this schema a stricter gate than the reader it describes."""
    assert accepts("profile", {"something_a_stack_added": "value"})
    assert SC.BUILDERS["profile"]()["additionalProperties"] is True
    # The constitution is the opposite: a key nobody defined is a dial nobody
    # implements, and `policy.parse` refuses one.
    assert SC.BUILDERS["policy"]()["additionalProperties"] is False
    assert not accepts("policy", {"invented_floor": "x"})


def test_the_workspace_schema_requires_the_one_field_that_is_required():
    """Item 69's pre-INIT template has exactly one required field, and a
    workspace without it is not a workspace."""
    assert not accepts("workspace", {})
    assert accepts("workspace", {"root": "/somewhere"})
    assert not accepts("workspace", {"root": ""})


# ── the teaching layer stays where it is ───────────────────────────────────


def test_every_property_points_at_the_template_that_teaches_it():
    """The schema CITES the teaching layer rather than copying it. Lifting a
    paragraph onto a key means guessing which key it teaches, at the places
    where the teaching matters most - so the template stays the one home."""
    for name, build in SC.BUILDERS.items():
        for key, prop in build()["properties"].items():
            assert "description" in prop, f"{name}.{key}"
            assert SC.TEACHES[name] in prop["description"], f"{name}.{key}"


def test_the_templates_the_schemas_cite_actually_exist():
    """A citation to a file nobody ships is worse than none: it reads as
    guidance and resolves to nothing."""
    for name, rel in SC.TEACHES.items():
        assert (ROOT / rel).is_file(), f"{name} cites {rel}, which is not there"


def test_the_schemas_are_not_installed_into_an_adopters_repository():
    """Their live consumer is the drift guard; a wizard is external and reads
    the harness. A file installed everywhere and read nowhere is the dead-config
    class this project has found seven times."""
    installer = (ROOT / "sdlc-init.py").read_text(encoding="utf-8")
    assert "schemas" not in installer or "schema.json" not in installer


def test_the_generator_ships_no_third_party_import():
    """`tools/` must run where the harness runs, which is a machine with no
    packages. The VALIDATION lives in the suite; the generation does not."""
    import ast
    tree = ast.parse((ROOT / "tools" / "sdlc" / "schema.py")
                     .read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "jsonschema" not in imported and "pydantic" not in imported
    assert "yaml" not in imported
