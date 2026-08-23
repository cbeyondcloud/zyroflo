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
