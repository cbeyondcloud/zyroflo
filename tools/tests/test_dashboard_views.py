"""T447b: the dashboard's audit views and the Mermaid report.

The page gains the pipeline, steps, models, gates, rework and the audit trail
with a chain banner; the report says the same in Markdown. Held here, the way
the plan specified: no external URL anywhere a browser would fetch; an
unmeasurable number reads "not measurable" with its reason and no digit; two
renders are byte-identical; a headless HTML parse finds every view; a tampered
trail makes the banner say so; a planted secret appears nowhere.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import approve as A  # noqa: E402
from tools.sdlc import dashboard as V  # noqa: E402
from tools.sdlc import drive as D  # noqa: E402
from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import intake as entry  # noqa: E402
from tools.sdlc import metrics as M  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

RUNNER_CMD = '"' + sys.executable + '" -c pass'
SECRET = "sk-live-not-a-real-credential-dashboard"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "billing"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(
        "stack: python\ngate_profile: solo\n"
        "phases: [intake, requirements, design, implementation, test, review, "
        "triage, closeout]\n"
        "runners:\n  - name: writer\n"
        f"    command: '{RUNNER_CMD}'\n    capabilities: chat\n"
        "phase_runners:\n  intake: writer\n  requirements: writer\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(r), *args], check=True, capture_output=True)
    return r


def reporting(usage, text="doc\n"):
    def run(command, prompt, cwd, timeout=3600):
        path = os.environ.get(E.USAGE_ENV)
        if usage and path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.writelines(json.dumps(u) + "\n" for u in usage)
        return 0, text
    return run


def _walked(repo: Path) -> str:
    rid = entry.open_run(repo, "title: a change\n\nkind: requirement\n\n"
                               "## What\n\nA small change.\n")["run_id"]
    D.step(repo, rid, {}, run=reporting([{"input_tokens": 100, "output_tokens": 40}],
                                        text=f"answer with {SECRET}\n"))
    D.step(repo, rid, {}, run=reporting(None))
    R.park(repo, rid, "P3")
    A.record(repo, rid, "G2", "approve", "r@example.invalid")
    R.park(repo, rid, "P4", status="running")
    L.advance(repo, rid, phase="P3")
    return rid


def _page(repo: Path) -> str:
    states, _ = M.run_states(repo)
    return V.render(V.views(repo, M.compute(repo), states), str(repo))


class _Sections(HTMLParser):
    def __init__(self):
        super().__init__()
        self.h2, self._in, self.fetches = [], False, []

    def handle_starttag(self, tag, attrs):
        self._in = tag == "h2"
        for name, val in attrs:
            if name in ("src", "href") and val:
                self.fetches.append(val)

    def handle_data(self, data):
        if self._in:
            self.h2.append(data.strip())

    def handle_endtag(self, tag):
        self._in = False


def test_every_view_is_on_the_page_and_nothing_is_fetched(repo):
    _walked(repo)
    parser = _Sections()
    parser.feed(_page(repo))
    for view in ("Pipeline", "Steps", "Models", "Gates — human wait",
                 "Rework — loop-backs", "Audit trail"):
        assert view in parser.h2, parser.h2
    assert parser.fetches == [], "no src/href: the page is one offline file"
    page = _page(repo)
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page)
    assert "<script" not in page and "@import" not in page


def test_an_unmeasured_step_says_so_with_its_reason_and_no_digit(repo):
    _walked(repo)
    page = _page(repo)
    row = next(r for r in page.split("<tr>") if r.startswith("<td>requirements</td>"))
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row)
    tokens_in = cells[4]
    assert tokens_in.startswith("not measurable - "), tokens_in
    assert not re.search(r"\d", tokens_in), "no digit in an unmeasured cell"
    assert "<svg" in page, "measured numbers are charted, inline"


def test_two_renders_are_byte_identical(repo):
    _walked(repo)
    assert _page(repo) == _page(repo)


def test_a_tampered_trail_is_announced_not_rendered_quietly(repo):
    rid = _walked(repo)
    assert "Chain verified" in _page(repo)
    path = E.events_path(repo, rid)
    rows = path.read_text(encoding="utf-8").splitlines()
    rows[0] = rows[0].replace('"requirement"', '"defect"')
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    page = _page(repo)
    assert "CHAIN BROKEN" in page and rid in page.split("CHAIN BROKEN", 1)[1]


def test_a_planted_secret_reaches_neither_artifact(repo):
    _walked(repo)
    states, _ = M.run_states(repo)
    view = V.views(repo, M.compute(repo), states)
    assert SECRET not in V.render(view, str(repo)) and SECRET not in V.report(view, str(repo))


def test_the_report_is_mermaid_markdown_and_deterministic(repo):
    _walked(repo)
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "sdlc" / "metrics.py"),
                           "--target", str(repo), "--report"], capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    text = (repo / V.REPORT).read_text(encoding="utf-8")
    assert "```mermaid\npie title Step outcomes" in text
    assert "| requirements | 1 |" in text and "not measurable - " in text
    assert "Chain verified for all 1 run(s)." in text
    assert str(repo) not in text, "the name, never the path"
    states, _ = M.run_states(repo)
    view = V.views(repo, M.compute(repo), states)
    assert V.report(view, str(repo)) == V.report(view, str(repo))


def test_an_empty_workspace_says_so_rather_than_drawing_zeros(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    page = V.render(V.views(tmp_path, M.compute(tmp_path), []), str(tmp_path))
    assert "no step has run under the driver yet" in page or "not measurable" in page
    assert "no run has an audit trail yet." in page
