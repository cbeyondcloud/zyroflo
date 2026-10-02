"""The stdlib profile reader REFUSES a shape it cannot read (defect class 17).

The hand reader enumerates the value SHAPES it understands; a construct the real
parser reads as non-scalar matched no branch, so the reader returned an
EMPTY/misread result and NOTHING refused. Six such shapes, each of which made the
adopter's profile silently do something other than what it says:

    list of mappings  a suppression `- rule: a` / `  why: b` read as the string
                      'rule: a' (why: b dropped) and then ENFORCED
    nested list       `- - a` read as the string '- a'
    anchor            `gate_profile: &g regulated` read as the string
                      '&g regulated', a gate profile no preset matches
    alias             `gate_profile: *g` read as the literal '*g'
    tag               `gate_profile: !!str 123` read as '!!str 123'
    flow mapping      `review: {block_on: [critical]}` read as the string
                      '{block_on: [critical]}', so `review.block_on` reads absent

`parse` refuses each of these for `policy.yml`; item 117's altitude is that the
profile reader must too — and, because it is the one that runs on the
zero-dependency machine, it must refuse WITHOUT PyYAML. The crosscheck catches
the anchor/alias/tag class only where PyYAML is installed and the
list/nested/flow class NOT AT ALL (a non-scalar value matches neither of its
scalar/list-of-scalars branches), which is why the refusal lives in the stdlib
reader rather than beside PyYAML.

Written with the Write tool and not a heredoc, because a quoted heredoc mangles
the very anchors and tags these fixtures are about (repo memory).
"""
import builtins
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import policy as P  # noqa: E402


def profile_at(tmp_path: Path, name: str, text: str) -> Path:
    root = tmp_path / name
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(text, encoding="utf-8")
    return root


#: (label -> (profile text, the word the refusal must say)). Each names ONE shape
#: as the first offending line so the message is unambiguous. `scope: repo` leads
#: so the rest is an ordinary profile but for the one shape under test.
UNREADABLE_SHAPES = {
    "list-of-maps": ("scope: repo\nreview_suppress:\n  - rule: a\n    why: b\n",
                     "list of mappings"),
    "nested-list": ("scope: repo\nknown_failures:\n  - - a\n    - b\n",
                    "nested list"),
    "anchor": ("scope: repo\ngate_profile: &g regulated\n", "YAML anchor"),
    "alias": ("scope: repo\ngate_profile: *g\n", "YAML alias"),
    "tag": ("scope: repo\ngate_profile: !!str 123\n", "YAML tag"),
    "flow-mapping": ("scope: repo\nreview: {block_on: [critical]}\n",
                     "YAML flow mapping"),
    # T556, T601 (review B13-6): a block scalar anywhere, and the value shapes inside
    # the two list-of-maps blocks, which the rule used to skip outright.
    "block-scalar": ("scope: repo\ntest_command: |\n  pytest -q\n", "YAML block scalar"),
    "entry-anchor": ("scope: repo\ncomponents:\n  - name: web\n    path: web\n"
                     "    test_command: &cmd pytest\n", "YAML anchor"),
    "entry-block-scalar": ("scope: repo\nrunners:\n  - name: local\n    command: >\n"
                           "      agent run\n", "YAML block scalar"),
}


def _read_without_pyyaml(monkeypatch, root: Path):
    """`_profile_lines(root)` with PyYAML forced absent — the zero-dependency
    machine the stdlib reader exists for, so the crosscheck cannot be the thing
    that refuses."""
    real_import = builtins.__import__

    def refuse_yaml(name, *rest, **kw):
        if name == "yaml":
            raise ImportError("no yaml here")
        return real_import(name, *rest, **kw)

    monkeypatch.setattr(builtins, "__import__", refuse_yaml)
    monkeypatch.setattr(P, "_CROSSCHECK_MEMO", None)
    return P._profile_lines(root)


@pytest.mark.parametrize("shape", sorted(UNREADABLE_SHAPES))
def test_the_stdlib_reader_refuses_an_unreadable_shape_with_no_pyyaml(
        tmp_path, monkeypatch, shape):
    """RED against the unfixed reader: with PyYAML absent it returned the misread
    lines and NOTHING refused. The refusal must hold here, because this is the
    supported configuration — not a degraded one."""
    text, word = UNREADABLE_SHAPES[shape]
    root = profile_at(tmp_path, f"noyaml-{shape}", text)
    with pytest.raises(P.ProfileError) as caught:
        _read_without_pyyaml(monkeypatch, root)
    said = str(caught.value)
    assert word in said, f"{shape}: refusal did not name the shape: {said}"
    assert "profile.yml:" in said, f"{shape}: refusal named no line: {said}"


@pytest.mark.parametrize("shape", sorted(UNREADABLE_SHAPES))
def test_a_consumer_refuses_an_unreadable_shape_through_the_one_door(
        tmp_path, shape):
    """The same refusal reaches the profile's call sites for free: a consumer
    that reads the profile stops rather than enforcing the misread value. With
    PyYAML present too, so the list/nested/flow class — which the crosscheck
    cannot see — is proven refused by the stdlib rule and not by the second
    opinion."""
    text, word = UNREADABLE_SHAPES[shape]
    root = profile_at(tmp_path, f"door-{shape}", text)
    with pytest.raises(P.ProfileError) as caught:
        P.profile_dials(root)
    assert word in str(caught.value), (shape, str(caught.value))


def test_the_headline_list_of_maps_suppression_is_refused_not_reinterpreted(
        tmp_path):
    """The concrete case from the backlog: a suppression written as a list of
    mappings was read as the one-item string list `['rule: a']` — `why` dropped —
    and that STRING was then enforced as a suppression. PyYAML reads the same
    bytes as a list of mappings, so the reader was enforcing a value nobody
    wrote."""
    text = "scope: repo\nreview_suppress:\n  - rule: a\n    why: b\n"
    reference = yaml.safe_load(text)["review_suppress"]
    assert reference == [{"rule": "a", "why": "b"}], (
        "the fixture must really be a list of mappings to PyYAML")
    root = profile_at(tmp_path, "headline", text)
    with pytest.raises(P.ProfileError, match="list of mappings"):
        P.profile_list(root, "review_suppress")


@pytest.mark.parametrize("shape", ["list-of-maps", "nested-list", "anchor",
                                   "tag", "flow-mapping"])
def test_the_refused_shape_really_is_one_pyyaml_reads_differently(shape):
    """Not strawmen: PyYAML reads each shape (where it does not reject the file
    outright) as something OTHER than the bare string the stdlib reader had
    enforced — a real hand-vs-reference disagreement, not a made-up one."""
    text = UNREADABLE_SHAPES[shape][0]
    loaded = yaml.safe_load(text)          # none of these five make PyYAML raise
    assert isinstance(loaded, dict), (shape, loaded)
    flattened = {"list-of-maps": "rule: a", "nested-list": "- a",
                 "anchor": "&g regulated", "tag": "!!str 123",
                 "flow-mapping": "{block_on: [critical]}"}[shape]
    values = list(loaded.values())
    assert flattened not in values and [flattened] not in values, (
        f"{shape}: PyYAML agreed with the misread {flattened!r}, so the fixture "
        f"does not exercise the defect")


PROFILE_SHAPE_CLIS = (
    ("tools/sdlc/policy.py", ["--validate"]),
    ("tools/sdlc/stage.py", ["--list"]),
)


@pytest.mark.parametrize("shape", sorted(UNREADABLE_SHAPES))
@pytest.mark.parametrize("script,extra", PROFILE_SHAPE_CLIS,
                         ids=[Path(s).stem for s, _ in PROFILE_SHAPE_CLIS])
def test_an_unreadable_shape_reaches_the_cli_as_a_refusal_not_a_traceback(
        tmp_path, script, extra, shape):
    """A CLI's contract is a PROCESS — item 117 found `policy.py --validate`
    handing a traceback for the one document class it exists to refuse, and
    in-process tests could not tell a refusal from a crash. SPAWNED: a clean,
    controlled non-zero exit and a readable sentence, never exit 0 and never a
    traceback."""
    text, word = UNREADABLE_SHAPES[shape]
    repo = profile_at(tmp_path, f"cli-{Path(script).stem}-{shape}", text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    done = subprocess.run(
        [sys.executable, str(ROOT / script), "--target", str(repo), *extra],
        capture_output=True, text=True, cwd=str(ROOT))
    said = done.stdout + done.stderr
    assert "Traceback" not in said, (
        f"{script} handed a person a traceback for {shape}:\n{said}")
    assert done.returncode != 0, (
        f"{script} carried on at exit 0 for {shape} — the silent accept is the "
        f"defect:\n{said}")
    assert word in said and "profile.yml:" in said, (
        f"{script} refused {shape} without naming the shape or line:\n{said}")


#: Review B13-1: a component's `deny_raw_git` that is not a boolean - text to PyYAML
#: where the reader read a boolean - was refused at the door on `main`, by the
#: crosscheck; T563's refusal, raised inside it and swallowed, let both CLIs say OK.
ENTRY_BOOLEANS = ('"yes"', "maybe", "1")


@pytest.mark.parametrize("value", ENTRY_BOOLEANS)
@pytest.mark.parametrize("script,extra", PROFILE_SHAPE_CLIS,
                         ids=[Path(s).stem for s, _ in PROFILE_SHAPE_CLIS])
def test_a_components_unreadable_boolean_is_refused_at_the_door(tmp_path, script, extra,
                                                                 value):
    """SPAWNED, as the matrix above: `policy.py --validate` answered `OK - within
    policy.` for a component whose `deny_raw_git` is `maybe` - the one document class
    it exists to refuse - and `stage.py --list` listed the pipeline."""
    text = ("scope: estate\ngate_profile: team\ncomponents:\n  - name: web\n"
            f"    path: web\n    vcs:\n      deny_raw_git: {value}\n")
    label = value.strip(chr(34))
    repo = profile_at(tmp_path, f"bool-{Path(script).stem}-{label}", text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    done = subprocess.run(
        [sys.executable, str(ROOT / script), "--target", str(repo), *extra],
        capture_output=True, text=True, cwd=str(ROOT))
    said = done.stdout + done.stderr
    assert "Traceback" not in said, said
    assert done.returncode != 0, f"{script} carried on at exit 0 for {value}:\n{said}"
    assert "deny_raw_git" in said and "write true or false" in said, said


@pytest.mark.parametrize("entry, words", [
    ('  - name: web\n    test_command: "pytest"\n', "`path:` is missing or empty"),
    ('  - name: web\n    path: web\n    vcs:\n      deny_raw_git: "true"\n',
     "is quoted"),
])
def test_a_component_readers_refusal_reaches_a_cli_as_a_sentence(tmp_path, entry, words):
    """Review B13-6: T562's empty path and T563's quoted boolean are the component
    reader's own refusals - raised where the entries are read, not at the door - and
    a refusal a person sees is a process. SPAWNED through `suite.py --baseline`, which
    reads the components: a sentence at a non-zero exit, never a traceback."""
    text = "scope: estate\ngate_profile: team\ncomponents:\n" + entry
    repo = profile_at(tmp_path, "reader-" + words.split()[-1].strip("`:"), text)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools/sdlc/suite.py"), "--target", str(repo),
         "--baseline"], capture_output=True, text=True, cwd=str(ROOT))
    said = done.stdout + done.stderr
    assert "Traceback" not in said, said
    assert done.returncode != 0 and words in said, f"exit {done.returncode}:\n{said}"


VALID_PROFILE_SHAPES = {
    "scalars-and-dials": "scope: repo\ngate_profile: team\nsigning: none\n",
    "inline-list": "scope: repo\nphases: [intake, design, test]\n",
    "block-list-scalars": "scope: repo\nknown_failures:\n  - a\n  - b\n",
    "pytest-nodeids-with-colons":
        'scope: repo\nknown_failures:\n  - "tests/t.py::test_a[case-b]"\n'
        "  - tests/t.py::test_c\n",
    "one-level-mapping": "scope: repo\nvcs:\n  push_remote: origin\n",
    "nested-inline-list": "scope: repo\nreview:\n  block_on: [critical, high]\n",
    "components-list-of-maps":
        "scope: repo\ncomponents:\n  - name: web\n    test_command: pytest\n",
}


@pytest.mark.parametrize("label", sorted(VALID_PROFILE_SHAPES))
def test_a_valid_profile_shape_is_not_refused(tmp_path, monkeypatch, label):
    """The positive control, and item 116's caution made a test: a naive 'refuse
    anything non-scalar' regressed four legitimate readers. These read cleanly —
    with PyYAML absent too, so the new stdlib rule is what is being controlled,
    not the crosscheck. `components:` list-of-maps is the ONE such shape the
    harness reads, through its own reader, so the rule keeps that shape there - and
    still refuses an unreadable VALUE inside it (T556, T601)."""
    text = VALID_PROFILE_SHAPES[label]
    root = profile_at(tmp_path, f"valid-{label}", text)
    lines = _read_without_pyyaml(monkeypatch, root)     # raises if wrongly refused
    assert isinstance(lines, list)


def test_a_valid_profile_still_validates_at_exit_zero(tmp_path):
    """The process half of the control: the door this reader protects still opens
    for a profile it CAN read."""
    repo = profile_at(tmp_path, "valid-cli",
                      "scope: repo\ngate_profile: team\nknown_failures:\n  - a\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools/sdlc/policy.py"), "--target",
         str(repo), "--validate"], capture_output=True, text=True, cwd=str(ROOT))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "OK - within policy." in done.stdout, done.stdout
