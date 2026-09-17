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
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# `jsonschema` and `yaml` are PLAIN imports, never `importorskip`, and for
# `pydantic`'s reason one file over: the dependency guard reads IMPORTS, so a
# package loaded by a call looks undeclared-and-unused to it - and CI runs with
# SDLC_FORBID_SKIPS, so an `importorskip` that ever fired would fail the suite
# rather than excuse the absence. pyyaml is a base dependency and jsonschema a
# dev one, so neither can be missing where the suite runs and an `importorskip`
# on either is dead code masquerading as a guard.

from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import schema as SC  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402


def validator(name: str):
    return jsonschema.Draft202012Validator(SC.BUILDERS[name]())


def accepts(name: str, doc: dict) -> bool:
    return validator(name).is_valid(doc)


def load_installer():
    """Import sdlc-init.py despite the hyphen - the home of `read_workspace` and
    of the workspace key sets the workspace schema is derived to match. Same
    mechanism test_workspace_config.py uses to reach the reader."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sdlc_init", ROOT / "sdlc-init.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


SI = load_installer()


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


def test_a_whitespace_only_drift_shows_the_real_difference(tmp_path):
    """The message names the committed line and the derived line so a human can
    act on it. When the two differ ONLY in whitespace, stripping both before
    printing collapses the message to two identical strings - a diff that hides
    the very thing that differs (item 262)."""
    fake = tmp_path / "harness"
    (fake / SC.SCHEMA_DIR).mkdir(parents=True)
    for doc in SC.BUILDERS:
        SC.path_of(doc, fake).write_text(SC.rendered(doc), encoding="utf-8")

    # Add trailing whitespace to the first line of the committed policy schema:
    # the raw lines now differ, but the difference is whitespace ONLY.
    bad = SC.path_of("policy", fake)
    lines = bad.read_text(encoding="utf-8").splitlines(keepends=True)
    i = next(n for n, ln in enumerate(lines) if ln.strip())
    lines[i] = lines[i].rstrip("\n") + "   \n"
    bad.write_text("".join(lines), encoding="utf-8")

    said = SC.drift(fake)
    assert len(said) == 1 and said[0].startswith("policy:"), said
    committed = said[0].split("committed ", 1)[1].split(" vs derived ", 1)[0]
    derived = said[0].split(" vs derived ", 1)[1].split(" - run", 1)[0]
    assert committed != derived, (
        "a whitespace-only difference printed the same string on both sides: "
        + said[0])


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
    # A vacuity guard, the mirror of the one on the REJECTED case below: an empty
    # or non-list ACCEPTED makes `bad` empty and this test pass while asserting
    # nothing (item 115). The reader ships twelve accepted documents today.
    assert isinstance(ACCEPTED, list) and len(ACCEPTED) > 5, ACCEPTED
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


def test_the_workspace_schema_covers_every_key_the_reader_knows():
    """Derived, not transcribed - the workspace reader's OWN key sets. The
    hand-typed schema had drifted three ways at once: it omitted `schema_version`
    and `profile`, and it spelled the component list `components` where the reader
    and the TOML template both write `component`, so that property described no
    real document at all (item 259). A key added to `read_workspace` now surfaces
    here by name rather than silently, the way `DEFAULTS` does for policy."""
    props = SC.BUILDERS["workspace"]()["properties"]
    assert set(props) == set(SI._WS_TOP_KEYS), set(props) ^ set(SI._WS_TOP_KEYS)
    comp = props["component"]["items"]["properties"]
    assert set(comp) == set(SI._WS_COMPONENT_KEYS), (
        set(comp) ^ set(SI._WS_COMPONENT_KEYS))


def test_the_shipped_workspace_template_validates_against_its_own_schema():
    """The pre-INIT document every multi-repo adopter starts from. A schema its
    own template fails is one nobody can act on - the sibling policy and profile
    templates each carry this guard and the workspace one had none (item 259)."""
    import tomllib
    raw = (ROOT / "sdlc_templates" / "workspace.example.toml").read_text(
        encoding="utf-8")
    validator("workspace").validate(tomllib.loads(raw))


def test_the_workspace_schema_agrees_with_the_reader(tmp_path):
    """**The property that makes the schema a contract, tested in BOTH directions.**
    Run the SAME documents through `read_workspace` and through the schema and
    require the same verdict. The corpus deliberately spans structurally-invalid
    documents (missing root, unknown key, off-enum role) AND valid-SHAPED-but-wrong
    ones (a blank required value, a node id without its colon, an unknown vcs key).
    The second kind is the one a first cut of this test omitted, so the schema came
    out LOOSER than the reader and accepted workspaces the installer refuses - the
    exact failure this file's own docstring calls dangerous (rule 28: a control
    whose negatives are all structurally invalid proves only that valid is
    required, not that the guard is right).

    Two disagreements JSON Schema 2020-12 cannot state are documented and left OUT
    of the corpus rather than papered over: a `[[alias]]` whose `a` equals its `b`
    (a cross-field inequality), and a `[[generated]]` `path` of only slashes and
    interior spaces (the reader strips `/` after whitespace). Both are refused by
    the reader; the schema cannot express either."""
    import tomllib
    (tmp_path / "repos").mkdir()
    # (TOML body, does the reader accept it). Roots are relative to the file, so
    # no absolute machine path is written down.
    cases = [
        # valid - accepted by both, one per block type so the schema is caught if
        # it grows STRICTER than the reader too (rule 33).
        ('root = "repos"\n', True),
        ('root = "repos"\nname = "acme"\nprofile = "orderflow"\n', True),
        ('root = "repos"\n[[component]]\nname = "web"\nrole = "vendor"\n', True),
        ('root = "repos"\n[[canonical]]\nservice = "orders"\nowner = "web"\n', True),
        ('root = "repos"\n[[generated]]\ncomponent = "web"\npath = "src/gen"\n', True),
        ('root = "repos"\n[[entrypoint]]\nnode = "ep:/orders/v1"\n', True),
        ('root = "repos"\n[[alias]]\na = "table:orders"\nb = "table:reports"\n', True),
        ('root = "repos"\n[[component]]\nname = "web"\n'
         '[component.vcs]\ndeny_raw_git = true\n', True),
        # structurally invalid - rejected by both (wrong shape / type / enum).
        ('', False),                                        # no root
        ('root = ""\n', False),                             # blank root
        ('root = "repos"\nschema_version = 2\n', False),    # unsupported version
        ('root = "repos"\ninvented_key = 1\n', False),      # unknown top-level key
        ('root = "repos"\n[[component]]\nname = "web"\n'
         'role = "banana"\n', False),                       # role off the enum
        # valid-SHAPED but a wrong VALUE - the direction the first cut missed. Each
        # was ACCEPTED by the looser schema and REFUSED by the reader until the
        # value constraints landed.
        ('root = "   "\n', False),                          # root blank after strip
        ('root = "repos"\n[[component]]\nname = ""\n', False),         # blank component name
        ('root = "repos"\n[[canonical]]\nservice = "   "\n'
         'owner = "web"\n', False),                         # whitespace service (\S, not minLength)
        ('root = "repos"\n[[generated]]\ncomponent = "web"\n'
         'path = "/"\n', False),                            # slash-only path collapses to blank
        ('root = "repos"\n[[entrypoint]]\nnode = "orders"\n', False),  # node id with no colon
        ('root = "repos"\n[[alias]]\na = "orders"\nb = "reports"\n', False),  # alias ids, no colon
        ('root = "repos"\n[[component]]\nname = "web"\n'
         '[component.vcs]\nbogus = "x"\n', False),          # unknown vcs sub-key
    ]
    disagreements = []
    for body, reader_ok in cases:
        path = tmp_path / "sdlc-workspace.toml"
        path.write_text(body, encoding="utf-8")
        try:
            SI.read_workspace(path)
            reader = True
        except SI.WorkspaceError:
            reader = False
        schema = accepts("workspace", tomllib.loads(body))
        if not (reader == schema == reader_ok):
            disagreements.append((body, reader_ok, reader, schema))
    assert disagreements == [], disagreements


# ── the teaching layer stays where it is ───────────────────────────────────


def test_every_property_points_at_the_template_that_teaches_it():
    """The schema CITES the teaching layer rather than copying it. Lifting a
    paragraph onto a key means guessing which key it teaches, at the places
    where the teaching matters most - so the template stays the one home.

    Recurses into component ITEMS: a component's own keys are properties too, and
    the top-level walk alone never reached them - so a component key could cite
    nothing and no test would notice (item 264)."""
    def cites(name, where, props):
        for key, prop in props.items():
            assert "description" in prop, f"{name}.{where}{key}"
            assert SC.TEACHES[name] in prop["description"], f"{name}.{where}{key}"

    for name, build in SC.BUILDERS.items():
        schema = build()
        cites(name, "", schema["properties"])
        # The component list is spelled `component` (workspace/TOML) or
        # `components` (profile/YAML); recurse into whichever this schema has.
        for comp_key in ("component", "components"):
            comp = schema["properties"].get(comp_key)
            if isinstance(comp, dict) and comp.get("type") == "array":
                cites(name, f"{comp_key}[].", comp["items"]["properties"])


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


# ── item 130: the derived schema rejected the installer's own output ────────


def test_a_multi_component_profile_the_installer_writes_validates():
    """**Item 130's by-product, and it needed the reader's fix to state.**

    Every component key except `coverage_threshold` was typed `string`,
    including the three the TOP LEVEL of this same schema types `array` - so a
    wizard obeying this document would reject every multi-component profile
    `sdlc-init.py` writes, because `render_components` puts `known_failures: []`
    into each one.

    It survived because `test_the_shipped_profile_template_validates_against_
    its_own_schema` replaces `@@COMPONENTS@@` with the empty string, so nothing
    in the suite had ever validated a component at all. The array-ness is
    derived from `policy.COMPONENT_LIST_KEYS`, which item 130 created for the
    READER, rather than from a fourth hand-written list.
    """
    from tools.sdlc import policy as guardrails

    validator = jsonschema.Draft202012Validator(SC.BUILDERS["profile"]())
    doc = {"components": [{
        "name": "web", "path": "web", "known_failures": [],
        "sca_known_findings": ["npm ws 1.0.0 CVE-WS"],
        "probes": ["http://localhost:8080/health"]}]}
    errors = sorted(e.message for e in validator.iter_errors(doc))
    assert errors == [], errors

    assert set(guardrails.COMPONENT_LIST_KEYS) <= set(guardrails.COMPONENT_KEYS)
    props = SC.BUILDERS["profile"]()["properties"]["components"]["items"][
        "properties"]
    for key in guardrails.COMPONENT_LIST_KEYS:
        assert props[key]["type"] == "array", (
            f"{key} is a list to the reader and {props[key]['type']!r} to the "
            f"schema that claims to describe it")
