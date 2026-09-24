"""T469 - a component entry is a sequence item, whichever key it writes first.

`policy.components` and `component_count` opened a new entry only on a `- name:`
line. An entry written `- path: ../billing` / `name: billing` is the same YAML -
PyYAML reads two components - but the reader folded it into the entry before it,
which then carried billing's `test_command`, and counted one component. Under
`gate_profile: solo`, which a multi-component estate may not select, that let one
reviewer approve a change spanning two repositories (found by the T468 review,
re-run by the lead: exit 0 where the `name:`-first control refuses with exit 2).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import policy as P  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

NAME_FIRST = ("components:\n"
              "  - name: orders\n    path: ../orders\n    test_command: \"mvn -q test\"\n"
              "  - name: billing\n    path: ../billing\n    test_command: \"npm test\"\n")
PATH_FIRST = ("components:\n"
              "  - name: orders\n    path: ../orders\n    test_command: \"mvn -q test\"\n"
              "  - path: ../billing\n    name: billing\n    test_command: \"npm test\"\n")


def _repo(tmp_path: Path, name: str, profile: str) -> Path:
    root = tmp_path / name
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "profile.yml").write_text(profile, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    return root


def _picked(root: Path) -> list[tuple[str, str, str]]:
    return [(c["name"], c["path"], c.get("test_command", "")) for c in P.components(root)]


def test_an_entry_led_by_another_key_is_still_its_own_component(tmp_path):
    root = _repo(tmp_path, "path-first", "stack: python\n" + PATH_FIRST)
    assert _picked(root) == [("orders", "../orders", "mvn -q test"),
                             ("billing", "../billing", "npm test")]
    assert P.component_count(root) == 2
    # the control: the shape the installer writes reads the same way it always did
    control = _repo(tmp_path, "name-first", "stack: python\n" + NAME_FIRST)
    assert _picked(control) == _picked(root) and P.component_count(control) == 2


def test_an_entrys_own_lists_stay_with_it_whichever_key_leads(tmp_path):
    root = _repo(tmp_path, "lists", "stack: python\ncomponents:\n"
                 "  - path: ../orders\n    name: orders\n    known_failures:\n"
                 "      - \"tests/test_flaky.py::test_x\"\n"
                 "  - known_failures:\n      - \"a::b\"\n    name: billing\n    path: ../billing\n")
    got = {c["name"]: (c["path"], c.get("known_failures")) for c in P.components(root)}
    assert got == {"orders": ("../orders", ["tests/test_flaky.py::test_x"]),
                   "billing": ("../billing", ["a::b"])}


def test_an_entry_with_no_name_is_refused_by_name(tmp_path):
    """Read as nameless it could only be dropped or merged; either is a silent
    wrong answer about the estate, so it is refused and the entry is named."""
    root = _repo(tmp_path, "nameless", "stack: python\ncomponents:\n"
                 "  - name: orders\n    path: ../orders\n  - path: ../billing\n")
    with pytest.raises(P.ProfileError, match=r"entry 2 .*no `name:`"):
        P.components(root)
    assert P.component_count(root) == 2, "it is still counted: two entries are declared"


def test_a_runner_entry_led_by_another_key_is_its_own_runner(tmp_path):
    """`runners:` is read the same way, and merged the same way: the second
    runner's command overwrote the first's, so a phase routed to `local` would
    have run `remote`'s command."""
    root = _repo(tmp_path, "runners", "stack: python\nrunners:\n"
                 "  - name: local\n    command: \"claude -p\"\n    capabilities: [tools]\n"
                 "  - command: \"goose run\"\n    name: remote\n    capabilities: [chat]\n")
    got = {r["name"]: r["command"] for r in P.runners_declared(root)}
    assert got == {"local": "claude -p", "remote": "goose run"}


@pytest.mark.parametrize("items", ["      - review\n      - \"triage\"\n",
                                   "    - review\n    - \"triage\"\n"],
                         ids=["deeper", "at-the-key"])
def test_a_runners_block_list_scope_is_its_scope(tmp_path, items):
    """T480 (found by the T478 review, run as Opus 5). A bare `phases:` stored an
    empty list and its `- review` rows, which carry no colon, were skipped - and
    `runner_for` reads an empty scope as UNSCOPED, the permissive direction. The
    inline spelling of the same YAML was refused. Both legal indents for the
    items, and the refusal a person sees, spawned."""
    root = _repo(tmp_path, "scope", "stack: python\nrunners:\n  - name: bot\n"
                 "    command: \"bot exec\"\n    capabilities: tools\n    phases:\n" + items
                 + "phase_runners:\n  implementation: bot\n")
    assert P.runners_declared(root)[0]["phases"] == ["review", "triage"]
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "stage.py"),
                           "--target", str(root), "--list"],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "its self-scope is a bound, not a hint" in done.stdout + done.stderr


@pytest.mark.parametrize("profile", [NAME_FIRST, PATH_FIRST], ids=["name-first", "path-first"])
def test_solo_is_refused_on_two_components_whichever_key_leads(tmp_path, profile):
    """The consequence, held as a process: the approval a person records."""
    root = _repo(tmp_path, "solo", "stack: python\ngate_profile: solo\n" + profile)
    run = L.create(root, "requirement", "a change spanning two repositories")["run_id"]
    R.park(root, run, "P3")
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "sdlc" / "approve.py"), "--target", str(root),
         "--run", run, "--gate", "G2", "--verdict", "approve",
         "--approver", "dev@example.invalid"],
        capture_output=True, text=True, timeout=300)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "solo is unselectable with 2 components" in done.stdout + done.stderr
