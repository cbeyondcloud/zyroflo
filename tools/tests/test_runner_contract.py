"""Item 33 — the runner contract, and the reference runner that proves it runs.

Two kinds of test here. The currency guards weld `RUNNER_CONTRACT.md` to the
code it describes: every CLI and flag it cites must exist in a shipped
argparse, and the exact-value keys it documents must be the ones the prompt
actually emits. A contract that drifts from the harness is worse than none,
because an adapter author would follow it.

The rest exercise the reference runner as what it is — an outside process that
knows only its prompt and the repository. It imports nothing from the harness,
so these tests drive it the way the harness does.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import tools.provider_smoke as S  # noqa: E402
from tools.sdlc import reference_runner as R  # noqa: E402

CONTRACT = (ROOT / "RUNNER_CONTRACT.md").read_text(encoding="utf-8")


def _prompt(tmp_path: Path, component: str = "widgets") -> str:
    pre, pack = S.artifacts(tmp_path / "out")
    return S.PROMPT.format(diff=tmp_path / "d.patch", prepass=pre, pack=pack,
                           metrics=tmp_path / "out", component=component)


# ── the contract must describe the harness that exists ───────────────────────


def test_the_contract_ships_and_names_the_conformance_command():
    assert "provider_smoke.py --runner" in CONTRACT
    assert "provider-smoke.jsonl" in CONTRACT


@pytest.mark.parametrize("cli, flags", [
    ("tools/review/hitl.py", ["--target", "--begin", "--finding", "--source",
                              "--disposition", "--end", "--abandon"]),
    ("tools/review/prepass.py", ["--target", "--diff", "--out"]),
    ("tools/graph/pack.py", ["--graph-dir", "--diff", "--component", "--out",
                             "--no-svg"]),
    # The CLI whose surface this contract most recently GAINED was the one
    # nothing checked: sections 7 and 8 teach four provider_smoke flags, and
    # renaming any of them left the whole suite green while an adapter author
    # following the documented command line got argparse exit 2.
    ("tools/provider_smoke.py", ["--target", "--diff", "--component",
                                 "--runner", "--milestone", "--agent",
                                 "--status", "--require"]),
    ("tools/sdlc/stage.py", ["--target", "--run", "--phase", "--verify"]),
])
def test_every_flag_the_contract_cites_exists_in_the_shipped_cli(cli, flags):
    """The workflows' currency rule, applied to the contract: a document that
    teaches a flag the CLI dropped is a document that breaks an adapter."""
    src = (ROOT / cli).read_text(encoding="utf-8")
    for flag in flags:
        assert f'"{flag}"' in src, f"{cli} no longer accepts {flag}"
        assert flag in CONTRACT, f"the contract stopped naming {flag} for {cli}"


def test_the_documented_prompt_keys_are_the_ones_the_prompt_emits(tmp_path):
    """§2's block is the whole interface between harness and adapter.

    It was an EQUALITY against this prompt until item 36 made the block
    per-phase and generated: §2 now shows P6's full set, and the conformance
    prompt drives the impact-review spine, which is a subset of it. The
    equality did not disappear — it moved to where the block is now generated
    from (`test_stages`, both directions against `stage.by_phase("P6")`), and
    what is left here is the half that matters to an adapter: every key this
    prompt emits is one the contract told them to expect.
    """
    emitted = {m.group(1) for m in
               re.finditer(r"^([a-z-]+): ", _prompt(tmp_path), flags=re.M)}
    documented = {m.group(1) for m in
                  re.finditer(r"^([a-z-]+): <", CONTRACT, flags=re.M)}
    assert emitted <= documented, \
        f"the prompt emits keys the contract never names: {sorted(emitted - documented)}"
    assert emitted == {"component", "diff", "prepass-out", "pack-out",
                       "hitl-target"}


def test_the_contract_states_the_prepass_exit_codes_the_runner_relies_on():
    for code, meaning in (("1", "not a failure"), ("3", "outranks")):
        assert code in CONTRACT and meaning in CONTRACT


def test_the_contract_does_not_claim_an_unexercised_runner_was_exercised():
    """codex is not installed here; the table must say so rather than imply a
    green nobody produced."""
    row = next(line for line in CONTRACT.splitlines() if "**codex**" in line)
    assert "**no**" in row and "not installed" in row


# ── the reference runner, driven as an outside process ───────────────────────


def test_it_imports_nothing_from_the_harness():
    """An adapter is an outside process. One that reached into tools/ would
    prove nothing about a runner that cannot."""
    src = (ROOT / "tools" / "sdlc" / "reference_runner.py").read_text(
        encoding="utf-8")
    for line in src.splitlines():
        if line.startswith(("import ", "from ")):
            assert "tools" not in line, f"reached into the harness: {line}"


def test_the_prompt_is_the_final_argument():
    assert R.read_prompt(["--flag", "x", "the prompt"]) == "the prompt"
    with pytest.raises(SystemExit):
        R.read_prompt([])


def test_values_run_to_the_end_of_the_line_so_paths_may_contain_spaces(tmp_path):
    spaced = tmp_path / "a dir with spaces"
    (spaced / ".sdlc").mkdir(parents=True)
    (spaced / "AGENTS.md").write_text(
        'see `x/workflows/impact-review.md`\nuv run --project "/h" python /h/x.py\n',
        encoding="utf-8")
    prompt = S.PROMPT.format(diff=spaced / "d.patch", prepass=spaced / "p",
                             pack=spaced / "k", metrics=spaced / "m",
                             component="widgets")
    found = R.discover(prompt, spaced)
    assert found["diff"] == str(spaced / "d.patch")
    assert " " in found["diff"], "the fixture must actually exercise the space"
    assert found["component"] == "widgets"


def test_a_prompt_missing_a_value_is_refused_by_name(tmp_path):
    """Silence about a path the verification reads is the harness's bug, and
    the runner says which one rather than guessing."""
    (tmp_path / "AGENTS.md").write_text(
        'uv run --project "/h" python /h/x.py\n', encoding="utf-8")
    partial = _prompt(tmp_path).replace("pack-out:", "packout:")
    with pytest.raises(SystemExit, match="never states the pack"):
        R.discover(partial, tmp_path)


def test_discovery_needs_the_quoted_cli_prefix(tmp_path):
    (tmp_path / "AGENTS.md").write_text("no prefix here\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="not discoverable"):
        R.discover(_prompt(tmp_path), tmp_path)
    with pytest.raises(SystemExit, match="no AGENTS.md"):
        R.discover(_prompt(tmp_path), tmp_path / "elsewhere")


def test_it_drives_the_clis_the_way_agents_md_teaches(tmp_path, monkeypatch):
    """The taught command is `uv run --project "<harness>" python <script>` —
    the adapter uses that form, so the quoting fix stays exercised."""
    (tmp_path / "AGENTS.md").write_text(
        'see `h/workflows/impact-review.md`\n'
        'uv run --project "/harness" python "/harness/tools/graph/impact.py"\n',
        encoding="utf-8")
    calls = []

    def fake(cmd, cwd):
        calls.append(cmd)
        return 0, "ok"

    monkeypatch.setattr(R, "run", fake)
    assert R.execute(_prompt(tmp_path), tmp_path) == 0
    joined = [" ".join(c) for c in calls]
    expect = ["uv", "run", "--project", str(Path("/harness"))]
    assert all(c[:4] == expect for c in calls)
    assert any("--begin" in c for c in joined)
    assert any("prepass.py" in c for c in joined)
    assert any("pack.py" in c and "--component widgets" in c for c in joined)
    assert "--end approved" in joined[-1]


def test_a_findings_exit_from_the_prepass_is_not_a_failure(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text(
        'uv run --project "/h" python "/h/x.py"\n', encoding="utf-8")
    monkeypatch.setattr(R, "run", lambda cmd, cwd:
                        (1, "findings") if "prepass.py" in " ".join(cmd)
                        else (0, "ok"))
    assert R.execute(_prompt(tmp_path), tmp_path) == 0


def test_a_failed_step_abandons_the_session_rather_than_approving_it(tmp_path,
                                                                     monkeypatch):
    (tmp_path / "AGENTS.md").write_text(
        'uv run --project "/h" python "/h/x.py"\n', encoding="utf-8")
    calls = []

    def fake(cmd, cwd):
        calls.append(" ".join(cmd))
        return (2, "boom") if "pack.py" in " ".join(cmd) else (0, "ok")

    monkeypatch.setattr(R, "run", fake)
    assert R.execute(_prompt(tmp_path), tmp_path) == 1
    assert "--abandon" in calls[-1], "a run that did not finish closes as abandoned"


def test_a_refused_begin_stops_before_doing_any_work(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text(
        'uv run --project "/h" python "/h/x.py"\n', encoding="utf-8")
    calls = []

    def fake(cmd, cwd):
        calls.append(" ".join(cmd))
        return (2, "a review session is already open"), (0, "ok")

    monkeypatch.setattr(R, "run", lambda cmd, cwd: (calls.append(" ".join(cmd)),
                                                    (2, "already open"))[1])
    assert R.execute(_prompt(tmp_path), tmp_path) == 1
    assert len(calls) == 1, "nothing runs after the session could not be opened"


def test_the_cli_reports_the_root_it_was_pointed_at(tmp_path, capsys, monkeypatch):
    (tmp_path / "AGENTS.md").write_text(
        'uv run --project "/h" python "/h/x.py"\n', encoding="utf-8")
    monkeypatch.setattr(R, "run", lambda cmd, cwd: (0, "ok"))
    assert R.main(["--root", str(tmp_path), _prompt(tmp_path)]) == 0
    assert str(tmp_path) in capsys.readouterr().out


# ── the exit table, derived rather than restated ─────────────────────────────
#
# `test_the_contract_states_the_prepass_exit_codes_the_runner_relies_on` above
# asserts that certain PHRASES appear somewhere in the document. That catches a
# wholesale deletion, because the phrases go with the table - and it cannot see
# a code RENUMBERED (1 to 7 passes), a row DELETED (dropping the 0 row passes),
# or a code the CLI gained that nobody wrote down. Exit 2 was reachable in
# `prepass.py` and absent from a table the contract introduces as "codes the
# runner must interpret correctly".

CODE_TABLE_HEADER = "| CLI | Code | Means |"

#: Named in the contract as an ADAPTER — a runner, not a CLI a runner calls.
#: The only exemption, and it is spelled out so that adding a CLI to the
#: contract cannot quietly skip the table.
CONTRACT_ADAPTERS = {"reference_runner.py", "http_runner.py"}

#: Where each CLI lives. Every entry is asserted to exist below, so a rename
#: fails here rather than silently dropping a CLI out of the derivation.
CLI_PATHS = {
    "prepass.py": "tools/review/prepass.py",
    "pack.py": "tools/graph/pack.py",
    "hitl.py": "tools/review/hitl.py",
    "stage.py": "tools/sdlc/stage.py",
    "state.py": "tools/sdlc/state.py",
    "provider_smoke.py": "tools/provider_smoke.py",
}


def _contract_clis() -> set[str]:
    """Every CLI the contract tells a runner to call, read from the contract.

    Derived, not transcribed. `CLI_PATHS` used to be a hand-written copy
    of the table's own first column, so the test that compared them was
    comparing the table against a transcription of itself — and `stage.py` and
    `state.py`, which section 4 tells a runner to call with load-bearing exit
    codes, were missing from both and the assertion passed.
    """
    named = set(re.findall(r"`?([a-z_]+\.py)`?", CONTRACT))
    return named - CONTRACT_ADAPTERS


def _returnable(rel: str) -> set[int]:
    """Every integer `main()` can hand back, by reading the source.

    Follows a bare `return helper(...)` one hop, because that is how these
    CLIs are written: `_main` returns `exit_code(result)`, and the 0 and 1 a
    runner actually sees are decided in there. A derivation that stopped at
    `main` would have reported prepass as returning {2, 3} and called the
    table complete."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    funcs = {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
    seen, todo, out = set(), ["main", "_main"], set()
    while todo:
        name = todo.pop()
        if name in seen or name not in funcs:
            continue
        seen.add(name)
        for node in ast.walk(funcs[name]):
            if isinstance(node, ast.Return) and node.value is not None:
                out |= {c.value for c in ast.walk(node.value)
                        if isinstance(c, ast.Constant) and type(c.value) is int}
                for call in ast.walk(node.value):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                        todo.append(call.func.id)
    return out


def _documented() -> dict[str, set[str]]:
    """{cli: the code cells written for it}. `non-zero` stays a string; it is
    a range, not a code, and it covers whatever the CLI returns."""
    lines = CONTRACT.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(CODE_TABLE_HEADER))
    out: dict[str, set[str]] = {}
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        for name in re.findall(r"`([\w.]+\.py)`", cells[0]):
            out.setdefault(name, set()).add(cells[1])
    return out


def test_the_exit_table_names_every_cli_the_contract_tells_a_runner_to_call():
    """The weld: mention a CLI anywhere in the contract and its exit codes
    must be in the table. Both sides are derived from the document."""
    assert set(_documented()) == _contract_clis()


def test_every_cli_the_table_names_is_one_this_repository_ships():
    missing = sorted(name for name, rel in CLI_PATHS.items()
                     if not (ROOT / rel).is_file())
    assert missing == [], f"CLI_PATHS points at files that do not exist: {missing}"
    assert set(CLI_PATHS) == _contract_clis()


@pytest.mark.parametrize("cli", sorted(CLI_PATHS))
def test_every_code_a_contract_cli_can_return_is_in_the_table(cli):
    """A code a runner can receive and the contract never mentions is a code
    the runner cannot interpret correctly, which is what this table is for."""
    documented = _documented()[cli]
    if "non-zero" in documented:
        return                      # a range, and it covers the lot
    missing = sorted(c for c in _returnable(CLI_PATHS[cli])
                     if str(c) not in documented)
    assert missing == [], (
        f"{cli} can return {missing} and RUNNER_CONTRACT.md does not say what "
        f"that means to a runner")


@pytest.mark.parametrize("cli", sorted(CLI_PATHS))
def test_every_code_the_table_states_is_one_that_cli_can_actually_return(cli):
    """The other direction, which is what catches a renumbered row: a table
    that documents a code nothing returns is describing a program that is no
    longer there."""
    returnable = _returnable(CLI_PATHS[cli])
    stale = sorted(c for c in _documented()[cli]
                   if c != "non-zero" and int(c) not in returnable)
    assert stale == [], (
        f"RUNNER_CONTRACT.md documents exit {stale} for {cli}, which it cannot "
        f"return")


def test_the_derivation_can_tell_two_different_programs_apart():
    """The guard's own guard. Both assertions above rest on `_returnable`, and
    a derivation that returned the same set for everything - or nothing at all
    - would pass them while checking nothing."""
    assert _returnable(CLI_PATHS["prepass.py"]) >= {0, 1, 2, 3}
    assert 1 not in _returnable(CLI_PATHS["pack.py"])
    assert _returnable("tools/review/sca.py") != _returnable(
        CLI_PATHS["hitl.py"])
