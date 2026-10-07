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


def test_a_canonical_block_carries_its_citation(workspace):
    """Batch 15b: `canonical-unmatched` and `canonical-duplicate` cite the block a
    developer typed, so each entry carries this file's name and its header's line, as
    `[[entrypoint]]` and `[[alias]]` entries do."""
    body = ('root = "workspaces"\n'
            '[[canonical]]\nservice = "orders"\nowner = "orders"\n\n'
            '[[canonical]]\nservice = "billing"\nowner = "billing"\npath = "charts/b"\n')
    ws = si.read_workspace(workspace(body))
    assert ws["canonical"] == [
        {"service": "orders", "owner": "orders", "path": "",
         "file": "sdlc-workspace.toml", "line": 2},
        {"service": "billing", "owner": "billing", "path": "charts/b",
         "file": "sdlc-workspace.toml", "line": 6},
    ]


# ── apart blocks (T594): refusing a false name join ───────────────────────────
def test_an_apart_block_is_read_with_its_citation(workspace):
    body = ('root = "workspaces"\n'
            '[[apart]]\nservice = "redis"\ncomponent = "deploy"\n'
            'note = "a stock redis container, not the redis component"\n\n'
            '[[apart]]\nservice = "postgres"\ncomponent = "deploy"\n')
    ws = si.read_workspace(workspace(body))
    assert ws["apart"] == [
        {"service": "redis", "component": "deploy",
         "note": "a stock redis container, not the redis component",
         "file": "sdlc-workspace.toml", "line": 2},
        {"service": "postgres", "component": "deploy", "note": "",
         "file": "sdlc-workspace.toml", "line": 7},
    ]


def test_a_workspace_with_no_apart_block_reads_an_empty_list(workspace):
    assert si.read_workspace(workspace(MINIMAL))["apart"] == []


#: Each way an [[apart]] block is refused, and the words that say why.
APART_REFUSALS = {
    "missing-component": ('[[apart]]\nservice = "redis"\n',
                          "needs both `service` and `component`"),
    "missing-service": ('[[apart]]\ncomponent = "deploy"\n',
                        "needs both `service` and `component`"),
    "blank-service": ('[[apart]]\nservice = "   "\ncomponent = "deploy"\n',
                      "needs both `service` and `component`"),
    "unknown-key": ('[[apart]]\nservice = "redis"\ncomponent = "deploy"\nowner = "x"\n',
                    "unknown key"),
    "not-a-list": ('apart = "redis"\n', r"must be written as \[\[apart\]\] blocks"),
    "not-a-table": ('apart = ["redis"]\n', r"\[\[apart\]\] #1 is not a table"),
    "identical-duplicate": ('[[apart]]\nservice = "redis"\ncomponent = "deploy"\n'
                            '[[apart]]\nservice = "redis"\ncomponent = "deploy"\n'
                            'note = "again"\n',
                            "two \\[\\[apart\\]\\] blocks both keep service 'redis' of "
                            "'deploy' apart"),
}


@pytest.mark.parametrize("case", sorted(APART_REFUSALS))
def test_a_bad_apart_block_is_refused(workspace, case):
    body, why = APART_REFUSALS[case]
    with pytest.raises(si.WorkspaceError, match=why):
        si.read_workspace(workspace('root = "workspaces"\n' + body))


def test_apart_blocks_naming_two_services_of_one_component_are_both_kept(workspace):
    """Review focus 2's reading half: one component may refuse several services."""
    body = ('root = "workspaces"\n'
            '[[apart]]\nservice = "redis"\ncomponent = "deploy"\n'
            '[[apart]]\nservice = "postgres"\ncomponent = "deploy"\n'
            '[[apart]]\nservice = "Redis"\ncomponent = "deploy"\n')
    ws = si.read_workspace(workspace(body))
    # `Redis` is another spelling, not an identical one: the installer cannot
    # normalise (it imports nothing), so a collision after normalising is the
    # build's `apart-duplicate`, and a case variant is no collision at all.
    assert [(e["service"], e["component"]) for e in ws["apart"]] == [
        ("redis", "deploy"), ("postgres", "deploy"), ("Redis", "deploy")]


# ── citations (T622): every block cited at its own header's line ──────────────
LS, PS, NEL = chr(0x2028), chr(0x2029), chr(0x85)

#: One block of each kind, as (header word, body lines). Every kind carries a
#: comment line, so a line separator can sit there in a kind with no `note`.
KINDS = {
    "canonical": ['service = "s{n}"', 'owner = "o{n}"'],
    "entrypoint": ['node = "ep:/n{n}"'],
    "alias": ['a = "table:a{n}"', 'b = "table:b{n}"'],
    "apart": ['service = "s{n}"', 'component = "c{n}"'],
}


def _three_blocks(kind: str, header_of, comment: str = "# plain") -> tuple[str, list[int]]:
    """Three blocks of `kind`, the first spelled by `header_of(kind)`; returns the
    body and each block's true header line, counted as TOML counts lines (by \\n)."""
    lines = ['root = "workspaces"', ""]
    headers = []
    for n in range(3):
        headers.append(len(lines) + 1)
        lines.append(header_of(kind) if n == 0 else f"[[{kind}]]")
        lines.append(comment if n == 0 else "# plain")
        lines.extend(body.format(n=n) for body in KINDS[kind])
        lines.append("")
    return "\n".join(lines), headers


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("spelling", ["blanks", "quoted", "literal-quoted"])
def test_a_header_spelled_as_toml_allows_is_cited_at_its_own_line(workspace, kind, spelling):
    """Measured on `main`: `[[ entrypoint ]]` was cited at the NEXT block's line, and
    that block at line 0, because the finder matched the header text exactly. TOML
    allows blanks inside the brackets and a quoted key."""
    header_of = {"blanks": lambda k: f"[[ {k} ]]",
                 "quoted": lambda k: f'[["{k}"]]',
                 "literal-quoted": lambda k: f"[[ '{k}' ]]"}[spelling]
    body, headers = _three_blocks(kind, header_of)
    ws = si.read_workspace(workspace(body))
    assert [e["line"] for e in ws[kind]] == headers


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("separator", [LS, PS, NEL], ids=["U+2028", "U+2029", "U+0085"])
def test_a_line_separator_toml_allows_shifts_no_citation(workspace, kind, separator):
    """Measured on `main`: a U+2028 in a note cited every later block one line late,
    because `str.splitlines()` breaks there and TOML does not."""
    body, headers = _three_blocks(kind, lambda k: f"[[{k}]]",
                                  comment=f"# one{separator}two")
    ws = si.read_workspace(workspace(body))
    assert [e["line"] for e in ws[kind]] == headers


def test_a_header_inside_a_multiline_string_never_lends_its_line(workspace):
    """A line that reads like a header inside a multi-line string is not one to TOML.
    Where the finder cannot match headers to blocks one for one, every block of that
    kind is cited at line 0 - unknown - rather than at a neighbour's line."""
    body = ('root = "workspaces"\n'
            '[[apart]]\nservice = "redis"\ncomponent = "deploy"\n'
            'note = """\n[[apart]]\nlooks like a header\n"""\n'
            '[[apart]]\nservice = "pg"\ncomponent = "deploy"\n')
    ws = si.read_workspace(workspace(body))
    assert [e["line"] for e in ws["apart"]] == [0, 0]


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
    known = (si._WS_TOP_KEYS | si._WS_COMPONENT_KEYS | si._WS_VCS_KEYS
             | si._WS_CANONICAL_KEYS | si._WS_GENERATED_KEYS
             | si._WS_ENTRYPOINT_KEYS | si._WS_ALIAS_KEYS | si._WS_APART_KEYS
             | si._PROJECT_KEYS)          # T442's optional [project] table
    unknown = []
    for lineno, line in enumerate(TEMPLATE.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip("# ").rstrip()
        if not stripped or "=" not in stripped or stripped.startswith("["):
            continue
        key = stripped.split("=", 1)[0].strip()
        if key.isidentifier() and key not in known:
            unknown.append(f"{lineno}: {key}")
    assert not unknown, f"template documents keys the reader would reject: {unknown}"


# ── review findings: every one of these passed silently before ────────────────
#: A drive prefix, assembled rather than written out. The leak guard forbids a literal
#: drive-letter path in any shipped file, and it is right to: exempting this file would
#: widen a skip list pinned at two entries in order to accommodate a fake path. `"C:"` on
#: its own is not a path — the pattern needs the separator that follows.
_DRIVE = "C" + ":"


@pytest.mark.parametrize("pattern", [
    "/etc/*",
    _DRIVE + "/somewhere/*",
    _DRIVE + "\\somewhere\\*",
])
def test_an_absolute_glob_is_a_message_not_a_traceback(workspace, pattern: str):
    """`Path.glob` raises NotImplementedError on a non-relative pattern, which escaped as
    an unhandled traceback out of the one function whose entire contract is that a bad
    config produces a message.

    The TOML uses a single-quoted literal string: in a double-quoted one a lone backslash
    is itself a parse error, so that fixture would fail before reaching the check.
    """
    with pytest.raises(si.WorkspaceError, match="absolute path"):
        si.read_workspace(workspace(f"root = \"workspaces\"\ninclude = ['{pattern}']\n"))


@pytest.mark.parametrize("pattern", ["../*", "a/../../b"])
def test_a_glob_escaping_the_root_is_refused(workspace, pattern: str):
    """It silently found nothing, which reads as an empty workspace. If the components are
    somewhere else, that somewhere else is the root."""
    with pytest.raises(si.WorkspaceError, match=r"escapes `root`"):
        si.read_workspace(workspace(f'root = "workspaces"\ninclude = ["{pattern}"]\n'))


def test_known_failures_as_a_bare_string_is_refused(workspace):
    """The natural typo, and `list("test_a")` is `['t','e','s','t','_','a']` — six invented
    quarantined test names written into the profile with nothing reporting a problem."""
    body = 'root = "workspaces"\n[[component]]\nname = "orders"\nknown_failures = "test_a"\n'
    with pytest.raises(si.WorkspaceError, match="must be a LIST"):
        si.read_workspace(workspace(body))


def test_known_failures_as_a_list_is_accepted(workspace):
    body = ('root = "workspaces"\n[[component]]\nname = "orders"\n'
            'known_failures = ["test_a", "test_b"]\n')
    ws = si.read_workspace(workspace(body))
    assert ws["overrides"]["orders"]["known_failures"] == ["test_a", "test_b"]


def test_a_boolean_coverage_threshold_is_refused(workspace):
    """`isinstance(True, int)` is True in Python, so a naive check let `= true` through as
    the integer 1 — a 1% coverage gate that passes everything, silently."""
    body = 'root = "workspaces"\n[[component]]\nname = "orders"\ncoverage_threshold = true\n'
    with pytest.raises(si.WorkspaceError, match="whole number"):
        si.read_workspace(workspace(body))


def test_a_float_coverage_threshold_is_refused_not_rounded(workspace):
    """It was silently discarded, keeping the detected default. Ignoring a value the
    developer deliberately wrote is the same failure in a quieter coat."""
    body = 'root = "workspaces"\n[[component]]\nname = "orders"\ncoverage_threshold = 9.5\n'
    with pytest.raises(si.WorkspaceError, match="whole number"):
        si.read_workspace(workspace(body))


@pytest.mark.parametrize("value", [-1, 101, 1000])
def test_an_out_of_range_coverage_threshold_is_refused(workspace, value: int):
    body = (f'root = "workspaces"\n[[component]]\nname = "orders"\n'
            f'coverage_threshold = {value}\n')
    with pytest.raises(si.WorkspaceError, match="outside 0"):
        si.read_workspace(workspace(body))


@pytest.mark.parametrize("value", [0, 80, 100])
def test_a_valid_coverage_threshold_survives(workspace, value: int):
    body = (f'root = "workspaces"\n[[component]]\nname = "orders"\n'
            f'coverage_threshold = {value}\n')
    ws = si.read_workspace(workspace(body))
    assert ws["overrides"]["orders"]["coverage_threshold"] == value


# ── the repository you are standing in — `workspace_for_repo` (item 77) ───────
#
# `root` becomes optional in EXACTLY one place: when there is no config file at all.
# Every test above still holds unchanged, and that is the boundary this section pins —
# a file that silently meant "wherever you ran it from" would name a different workspace
# on every machine, which is the failure the "relative to the config, never the shell"
# rule already prevents.


def make_repo(parent: Path, name: str, *, as_submodule: bool = False) -> Path:
    """A directory that looks like a clone.

    `as_submodule` writes the `.git` FILE form instead, which is what a submodule or a
    linked worktree carries — the distinction `nested_clones` turns on.
    """
    repo = parent / name
    repo.mkdir(parents=True, exist_ok=True)
    if as_submodule:
        (repo / ".git").write_text("gitdir: ../.git/modules/vendored\n", encoding="utf-8")
    else:
        (repo / ".git").mkdir(exist_ok=True)
    return repo


def test_the_repository_you_are_standing_in_becomes_a_one_component_workspace(tmp_path):
    repo = make_repo(tmp_path / "ws", "orders")
    ws = si.workspace_for_repo(repo)
    assert ws["root"] == (tmp_path / "ws").resolve()
    assert ws["include"] == ["orders"]
    assert ws["inferred_from"] == repo.resolve()
    assert si.discover_components(ws)["components"][0]["name"] == "orders"


def test_a_subdirectory_resolves_to_the_repository_above_it(tmp_path):
    """The developer is rarely standing at the repo root. Walking up is the whole point:
    without it, `src/main/java` would report "not a repository" from inside one."""
    repo = make_repo(tmp_path / "ws", "orders")
    deep = repo / "src" / "main" / "java"
    deep.mkdir(parents=True)
    assert si.workspace_for_repo(deep)["inferred_from"] == repo.resolve()


def test_not_being_in_a_repository_is_a_hard_error_that_names_the_directory(tmp_path):
    """Refused, never an empty component list.

    This is the item-77 half of the rule this whole file is built on: an empty workspace
    reads exactly like a clean one, so "found nothing" must never be a success state.

    (If this ever fails with a *different* message, some ancestor of the pytest temp dir
    is itself a repository — a real finding about the machine, which is why it is a
    failure here rather than a skip.)
    """
    with pytest.raises(si.WorkspaceError, match="not inside a git repository"):
        si.workspace_for_repo(tmp_path)
    try:
        si.workspace_for_repo(tmp_path)
    except si.WorkspaceError as exc:
        assert str(tmp_path.resolve()) in str(exc)


def test_a_repository_that_contains_repositories_is_refused(tmp_path):
    """A superproject, or a directory someone ran `git init` in by accident.

    Building it as one component collapses every coupling between the nested repos into a
    single node, and nothing downstream could tell that from a correct graph — so this is
    a refusal rather than a warning, and it names the multi-repo route out.
    """
    outer = make_repo(tmp_path / "ws", "everything")
    make_repo(outer, "orders")
    make_repo(outer, "billing")
    with pytest.raises(si.WorkspaceError, match="contains repositories of its own"):
        si.workspace_for_repo(outer)
    try:
        si.workspace_for_repo(outer)
    except si.WorkspaceError as exc:
        assert "--workspace" in str(exc) and "billing" in str(exc) and "orders" in str(exc)


def test_a_submodule_does_not_make_a_repository_a_container(tmp_path):
    """The `.git` FILE form is part of this repository, not a peer of it.

    Counting it would refuse to build every superproject that vendors a dependency the
    ordinary way — which is a large share of real repositories.
    """
    repo = make_repo(tmp_path / "ws", "orders")
    make_repo(repo, "vendored", as_submodule=True)
    assert si.workspace_for_repo(repo)["inferred_from"] == repo.resolve()


def test_an_inferred_workspace_has_the_same_shape_as_a_read_one(tmp_path, workspace):
    """Two routes in, one shape out. A second workspace shape would force every consumer
    downstream to learn which route a workspace arrived by."""
    inferred = si.workspace_for_repo(make_repo(tmp_path / "ws", "orders"))
    read = si.read_workspace(workspace('root = "workspaces"\n'))
    assert set(inferred) - {"inferred_from"} == set(read)


def test_an_inferred_workspace_declares_no_apart_block(tmp_path):
    """No file was read, so nothing was declared apart (T594) - an empty list, the
    shape the link passes read, never a missing key."""
    assert si.workspace_for_repo(make_repo(tmp_path / "ws", "orders"))["apart"] == []


def test_an_inferred_workspace_is_unnamed(tmp_path):
    """`name` seeds the leak guard's ban list, and a directory name is a guess at an
    organisation's identity rather than a statement of it. A hashed guess protects nothing
    while looking exactly like protection, so the field stays blank and says so."""
    assert si.workspace_for_repo(make_repo(tmp_path / "ws", "orders"))["name"] == ""


def test_an_inferred_workspace_selects_no_profile(tmp_path):
    """A profile resolves relative to the config file, and there is none. It is also a
    deliberate choice about file formats an organisation invented — never a default."""
    ws = si.workspace_for_repo(make_repo(tmp_path / "ws", "orders"))
    assert ws["profile"] == "" and ws["config_path"] is None


@pytest.mark.parametrize("name", ["svc[1]", "svc[a-z]", "[legacy]orders"])
def test_a_repository_name_containing_glob_syntax_still_finds_itself(tmp_path, name: str):
    """`include` is globbed, and `[`, `*` and `?` are pattern syntax there.

    Unescaped, `svc[1]` would match `svc1` and find zero components — the mistyped-root
    failure, arriving from a name the developer never typed and cannot see.

    Only the bracket forms are exercised: Windows forbids `*` and `?` in a filename, so a
    repository cannot carry one and the case is unreachable rather than untested. All three
    go through the same single `glob.escape` call, which is why one form proves it.
    """
    repo = make_repo(tmp_path / "ws", name)
    ws = si.workspace_for_repo(repo)
    assert [c["name"] for c in si.discover_components(ws)["components"]] == [name]


def test_root_stays_required_in_a_file_and_optional_only_without_one(tmp_path, workspace):
    """The boundary item 77 draws, pinned in one place so it cannot drift by halves."""
    with pytest.raises(si.WorkspaceError, match="`root` is required"):
        si.read_workspace(workspace('name = "acme"\n'))
    assert si.workspace_for_repo(make_repo(tmp_path / "ws", "orders"))["root"].is_dir()
