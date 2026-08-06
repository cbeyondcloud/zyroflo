"""`read_workspace()` — the one seam through which a workspace names itself.

Everything here is **synthetic**: invented org names, invented component names, and
`tmp_path` roots. That is not decoration. This file tests the reader of the file that
holds a developer's real coordinates, so a fixture copied from a real one would put
those coordinates in the repo permanently — which is precisely what the reader exists
to keep out.

The bias throughout is that **every ambiguity is a hard error**. A workspace config is
read once, at install time, and its output (the component list) is then trusted by
everything downstream. A tolerated mistake here does not surface as an error; it
surfaces as a component that is quietly missing from a blast radius three milestones
later, which reads as a graph bug rather than as a typo.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"
TEMPLATE = ROOT / "sdlc_templates" / "workspace.example.toml"


def load_installer():
    """Import sdlc-init.py despite the hyphen (not a valid module name)."""
    spec = importlib.util.spec_from_file_location("sdlc_init", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sdlc_init"] = mod
    spec.loader.exec_module(mod)
    return mod


si = load_installer()


@pytest.fixture()
def workspace(tmp_path: Path):
    """Write a config beside a real directory, and return a writer for its body."""
    root = tmp_path / "workspaces"
    root.mkdir()

    def write(body: str, *, name: str = "sdlc-workspace.toml") -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    write.root = root  # type: ignore[attr-defined]
    return write


MINIMAL = 'root = "workspaces"\n'


# ── the happy path, and the shape everything downstream depends on ────────────
def test_the_minimal_config_is_one_line(workspace):
    """`root` alone must be a complete, valid config.

    The design rule is "declare only what detection cannot reach". If anything else
    were needed to get off the ground, the template would be asking the developer
    questions they cannot answer before the first scan.
    """
    ws = si.read_workspace(workspace(MINIMAL))
    assert ws["root"] == workspace.root.resolve()
    assert ws["name"] == ""
    assert ws["include"] == [] and ws["exclude"] == []
    assert ws["overrides"] == {} and ws["canonical"] == []
    assert ws["schema_version"] == si.WORKSPACE_SCHEMA_VERSION


def test_a_relative_root_resolves_against_the_file_not_the_cwd(workspace, monkeypatch):
    """The same file must not mean different things depending on where it was run from.

    Resolving against the shell's cwd would make a config that works from the repo root
    silently point somewhere else — or nowhere — when run from a subdirectory.
    """
    path = workspace(MINIMAL)
    monkeypatch.chdir(ROOT)
    assert si.read_workspace(path)["root"] == workspace.root.resolve()


def test_an_absolute_root_is_taken_as_given(workspace):
    body = f'root = "{workspace.root.as_posix()}"\n'
    assert si.read_workspace(workspace(body))["root"] == workspace.root.resolve()


def test_a_bom_does_not_break_parsing(workspace):
    """Windows editors write a UTF-8 BOM invisibly, and it yields a baffling TOML error
    pointing at line 1 column 1 of a file that looks perfectly fine."""
    path = workspace.root.parent / "bom.toml"
    path.write_bytes(b"\xef\xbb\xbf" + MINIMAL.encode("utf-8"))
    assert si.read_workspace(path)["root"] == workspace.root.resolve()


# ── the one required field ────────────────────────────────────────────────────
def test_a_missing_root_names_the_field_and_says_why(workspace):
    with pytest.raises(si.WorkspaceError, match="`root` is required"):
        si.read_workspace(workspace('name = "acme"\n'))


def test_a_root_that_does_not_exist_is_refused(workspace):
    """The most valuable single check in this file.

    A mistyped root finds zero components and produces an empty graph — and **an empty
    workspace reads exactly like a clean one**. Nothing downstream can tell the
    difference, so it has to be caught at the only point where the intent is still
    visible.
    """
    with pytest.raises(si.WorkspaceError, match="reads exactly like a clean one"):
        si.read_workspace(workspace('root = "no-such-directory"\n'))


def test_a_root_that_is_a_file_is_refused(workspace):
    (workspace.root.parent / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(si.WorkspaceError, match="not a directory"):
        si.read_workspace(workspace('root = "notes.txt"\n'))


def test_a_missing_file_points_at_the_template(workspace):
    with pytest.raises(si.WorkspaceError, match="workspace.example.toml"):
        si.read_workspace(workspace.root / "absent.toml")


def test_invalid_toml_reports_the_path_and_the_parser_error(workspace):
    with pytest.raises(si.WorkspaceError, match="not valid TOML"):
        si.read_workspace(workspace('root = "workspaces"\nthis is not toml\n'))


# ── unknown keys are errors, never warnings ───────────────────────────────────
def test_an_unknown_top_level_key_is_fatal(workspace):
    with pytest.raises(si.WorkspaceError, match="unknown key `roots`"):
        si.read_workspace(workspace('root = "workspaces"\nroots = "x"\n'))


def test_a_near_miss_key_gets_a_spelling_hint(workspace):
    """A typo is the overwhelmingly likely cause, so the error should finish the
    developer's thought rather than making them diff against the template."""
    body = 'root = "workspaces"\n[[component]]\nname = "orders"\ntest_comand = "pytest"\n'
    with pytest.raises(si.WorkspaceError, match="Did you mean `test_command`"):
        si.read_workspace(workspace(body))


def test_an_unknown_component_key_is_fatal(workspace):
    body = 'root = "workspaces"\n[[component]]\nname = "orders"\nqqqq = 1\n'
    with pytest.raises(si.WorkspaceError, match="unknown key `qqqq`"):
        si.read_workspace(workspace(body))


def test_an_unknown_vcs_key_is_fatal(workspace):
    body = ('root = "workspaces"\n[[component]]\nname = "orders"\n'
            '[component.vcs]\ndeny_raw_gti = true\n')
    with pytest.raises(si.WorkspaceError, match="unknown key `deny_raw_gti`"):
        si.read_workspace(workspace(body))


def test_an_unknown_canonical_key_is_fatal(workspace):
    body = ('root = "workspaces"\n[[canonical]]\nservice = "orders"\n'
            'owner = "orders"\nownr = "x"\n')
    with pytest.raises(si.WorkspaceError, match="unknown key `ownr`"):
        si.read_workspace(workspace(body))


# ── component overrides ───────────────────────────────────────────────────────
def test_overrides_are_keyed_by_name_and_carry_the_default_role(workspace):
    body = ('root = "workspaces"\n'
            '[[component]]\nname = "orders"\npath = "services/orders"\n'
            'test_command = "pytest -q"\n')
    ws = si.read_workspace(workspace(body))
    assert set(ws["overrides"]) == {"orders"}
    assert ws["overrides"]["orders"]["role"] == si.DEFAULT_ROLE == "authoritative"
    assert ws["overrides"]["orders"]["test_command"] == "pytest -q"


def test_a_component_block_without_a_name_is_fatal(workspace):
    body = 'root = "workspaces"\n[[component]]\npath = "services/orders"\n'
    with pytest.raises(si.WorkspaceError, match="no `name`"):
        si.read_workspace(workspace(body))


def test_two_blocks_with_the_same_name_are_fatal(workspace):
    """Last-wins would be a silent choice between two things the developer wrote on
    purpose. There is no reading of a duplicate that is safe to guess."""
    body = ('root = "workspaces"\n'
            '[[component]]\nname = "orders"\ntest_command = "a"\n'
            '[[component]]\nname = "orders"\ntest_command = "b"\n')
    with pytest.raises(si.WorkspaceError, match="both named 'orders'"):
        si.read_workspace(workspace(body))


@pytest.mark.parametrize("role", si.COMPONENT_ROLES)
def test_every_declared_role_is_accepted(workspace, role: str):
    body = f'root = "workspaces"\n[[component]]\nname = "orders"\nrole = "{role}"\n'
    assert si.read_workspace(workspace(body))["overrides"]["orders"]["role"] == role


def test_an_unknown_role_is_fatal_and_lists_the_legal_ones(workspace):
    """`role` changes behaviour — non-authoritative components are never written to.
    A typo silently downgrading to the default would make the harness commit into a
    vendored tree, which is the one thing the field exists to prevent."""
    body = 'root = "workspaces"\n[[component]]\nname = "orders"\nrole = "authoritive"\n'
    with pytest.raises(si.WorkspaceError, match="authoritative, legacy, fork, vendor"):
        si.read_workspace(workspace(body))


def test_a_component_written_as_a_table_instead_of_a_block_is_rejected(workspace):
    """`[component]` (single brackets) is a plausible mistake and produces a table, not
    a list. Reading it as zero components would silently drop the developer's work."""
    body = 'root = "workspaces"\n[component]\nname = "orders"\n'
    with pytest.raises(si.WorkspaceError, match=r"\[\[component\]\] blocks"):
        si.read_workspace(workspace(body))


# ── canonical blocks ──────────────────────────────────────────────────────────
def test_a_canonical_block_needs_both_halves(workspace):
    body = 'root = "workspaces"\n[[canonical]]\nservice = "orders"\n'
    with pytest.raises(si.WorkspaceError, match="records the problem without deciding it"):
        si.read_workspace(workspace(body))


def test_two_canonical_blocks_for_one_service_are_fatal(workspace):
    body = ('root = "workspaces"\n'
            '[[canonical]]\nservice = "orders"\nowner = "orders"\n'
            '[[canonical]]\nservice = "orders"\nowner = "orders-legacy"\n')
    with pytest.raises(si.WorkspaceError, match="both claim service 'orders'"):
        si.read_workspace(workspace(body))


# ── globs ─────────────────────────────────────────────────────────────────────
def test_include_and_exclude_are_read_as_lists(workspace):
    body = ('root = "workspaces"\n'
            'include = ["services/*", "apps/web"]\nexclude = ["notes"]\n')
    ws = si.read_workspace(workspace(body))
    assert ws["include"] == ["services/*", "apps/web"]
    assert ws["exclude"] == ["notes"]


def test_a_scalar_where_a_glob_list_belongs_is_fatal(workspace):
    with pytest.raises(si.WorkspaceError, match="must be a list"):
        si.read_workspace(workspace('root = "workspaces"\ninclude = "services/*"\n'))


def test_an_empty_glob_is_rejected_rather_than_guessed_at(workspace):
    """An empty pattern could mean "everything" or "nothing" and the two are opposite.
    Neither is a defensible default."""
    with pytest.raises(si.WorkspaceError, match="no defensible meaning"):
        si.read_workspace(workspace('root = "workspaces"\ninclude = ["a", "  "]\n'))


def test_a_non_string_in_a_glob_list_is_fatal(workspace):
    with pytest.raises(si.WorkspaceError, match=r"`include\[1\]` must be a string"):
        si.read_workspace(workspace('root = "workspaces"\ninclude = ["a", 3]\n'))


# ── schema version ────────────────────────────────────────────────────────────
def test_the_version_may_be_omitted_while_only_one_version_exists(workspace):
    """Deliberate leniency with a written-down expiry: absence is unambiguous only
    while there is exactly one version. `WORKSPACE_SCHEMA_VERSION` carries the trigger
    for making this strict, so the next person does not have to infer it."""
    assert si.read_workspace(workspace(MINIMAL))["schema_version"] == 1


def test_a_future_version_is_refused_rather_than_read_optimistically(workspace):
    body = 'schema_version = 99\nroot = "workspaces"\n'
    with pytest.raises(si.WorkspaceError, match="not supported"):
        si.read_workspace(workspace(body))


# ── the shipped template must be the thing this reader accepts ────────────────
def test_the_shipped_template_parses_once_its_placeholder_root_is_real(tmp_path: Path):
    """A template that the reader rejects is worse than no template: the developer's
    first action after copying it is to hit an error they did not cause.

    Only `root` is substituted — it necessarily points at a path that exists on nobody's
    machine, which is the same reason no shipped file may contain a real one.
    """
    real = tmp_path / "workspaces"
    real.mkdir()
    body = TEMPLATE.read_text(encoding="utf-8")
    assert 'root = "/home/you/workspaces/my-workspace"' in body, \
        "the template's placeholder root moved — update this substitution"
    body = body.replace('root = "/home/you/workspaces/my-workspace"',
                        f'root = "{real.as_posix()}"')
    path = tmp_path / "sdlc-workspace.toml"
    path.write_text(body, encoding="utf-8")

    ws = si.read_workspace(path)
    assert ws["root"] == real.resolve()
    assert ws["name"] == "", "the template must ship with an empty name, never an example one"


def test_every_commented_example_in_the_template_uses_a_key_the_reader_knows():
    """The template's commented blocks are documentation that gets uncommented verbatim.

    A key that is illustrative-only — or misspelled — turns the developer's first
    override into a hard error, and they have no way to know the template was wrong
    rather than their edit.
    """
    known = si._WS_TOP_KEYS | si._WS_COMPONENT_KEYS | si._WS_VCS_KEYS | si._WS_CANONICAL_KEYS
    unknown = []
    for lineno, line in enumerate(TEMPLATE.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip("# ").rstrip()
        if not stripped or "=" not in stripped or stripped.startswith("["):
            continue
        key = stripped.split("=", 1)[0].strip()
        if key.isidentifier() and key not in known:
            unknown.append(f"{lineno}: {key}")
    assert not unknown, f"template documents keys the reader would reject: {unknown}"
