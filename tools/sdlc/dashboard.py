"""The productivity dashboard: three views over what the harness measured. Item 57.

`metrics.py` computes; this renders. Two artifacts, because they answer to two
different readers: a **self-contained HTML page** a human opens, and a **flat series
file** a time-series tool scrapes.

**The three views are mapped to units this harness ACTUALLY HAS**, and that mapping
is the item's one real decision. The task line asks for org / team / developer views,
and the harness has no organisation and no team:

| view | what it means here | where the data is |
|---|---|---|
| org | the whole workspace | the metrics document as computed |
| team | one component | `worktree.components_of`, per run |
| developer | one gate approver | the `approver` on each approval |

Inventing a team model would be the harness asserting an organisational structure
nobody declared, which is the adopter's to state and not this tool's to guess.

**The developer view reads the APPROVAL LEDGER and never `reviews.jsonl`.** Item 27
made review records carry no identity by construction, and rebuilding a per-person
view out of them would silently overturn a decision this project made on purpose. Who
took a gate is a different fact, recorded deliberately, and signed for. A test holds
this rather than a comment.

**What cannot be attributed is a row, not a silence.** A run whose components cannot
be determined is counted under `(unattributed)` rather than dropped, because a view
that quietly omits what it could not place reports a smaller, cleaner workspace than
the one that exists.

**No external assets.** The page is one file with its own CSS: this harness installs
into repositories with no network, and a dashboard that needs a CDN is a dashboard
that renders blank exactly where it is most needed.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

#: The two artifacts, named here because this module writes them and item 94's
#: rule is that a document name has one home.
DASHBOARD = Path(".sdlc") / "metrics" / "dashboard.html"
SERIES = Path(".sdlc") / "metrics" / "series.json"

#: What a run with no determinable component is filed under. A named bucket, so a
#: reader can see how much of the workspace the team view could not place.
UNATTRIBUTED = "(unattributed)"


def per_component(target: Path, states: list[dict]) -> dict[str, dict]:
    """Runs and their phases, per component. The `team` view.

    `worktree.components_of` is the reader, not a second one: it answers from the
    run's own record when there is one and from the PROFILE before P4 has written
    it, and it says which basis it used - the distinction item 100 built because
    reading an absent record as an empty set made a whole class of check
    structurally silent.
    """
    from tools.sdlc import worktree as trees

    out: dict[str, dict] = {}
    for st in states:
        rid = str(st.get("run_id") or "")
        names: set[str] = set()
        if rid:
            try:
                names, _basis = trees.components_of(Path(target), rid)
            except Exception:
                names = set()
        for name in (sorted(names) or [UNATTRIBUTED]):
            row = out.setdefault(name, {"runs": 0, "open": 0, "closed": 0,
                                        "by_phase": {}, "by_kind": {}})
            row["runs"] += 1
            status = str(st.get("status") or "")
            if status == "closed":
                row["closed"] += 1
            elif status not in ("rejected",):
                row["open"] += 1
            phase = str(st.get("phase") or "?")
            row["by_phase"][phase] = row["by_phase"].get(phase, 0) + 1
            kind = str(st.get("kind") or "?")
            row["by_kind"][kind] = row["by_kind"].get(kind, 0) + 1
    for row in out.values():
        row["by_phase"] = dict(sorted(row["by_phase"].items()))
        row["by_kind"] = dict(sorted(row["by_kind"].items()))
    return dict(sorted(out.items()))


def per_approver(target: Path, states: list[dict]) -> dict[str, dict]:
    """Gates taken, per person. The `developer` view.

    **Reads approvals and NOTHING else.** `reviews.jsonl` carries no identity by
    construction (item 27), and reconstructing a per-person view from it would
    overturn that on the quiet. Who took a gate is a separate fact, recorded on
    purpose and hash-chained.

    `forced` is beside the count deliberately: a person who approved ten gates and
    forced three of them has a different record from one who forced none, and a
    bare total hides exactly the thing somebody would want to know.
    """
    from tools.sdlc import approve
    from tools.sdlc import state as ledger

    out: dict[str, dict] = {}
    for st in states:
        rid = str(st.get("run_id") or "")
        if not rid:
            continue
        path = ledger.run_dir(Path(target), rid) / approve.APPROVALS
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue                  # damage is metrics.py's to count, not ours
            if not isinstance(row, dict):
                continue
            who = str(row.get("approver") or "").strip() or UNATTRIBUTED
            entry = out.setdefault(who, {"approvals": 0, "forced": 0,
                                         "by_gate": {}, "by_verdict": {}})
            entry["approvals"] += 1
            if row.get("forced"):
                entry["forced"] += 1
            gate = str(row.get("gate") or "?")
            entry["by_gate"][gate] = entry["by_gate"].get(gate, 0) + 1
            verdict = str(row.get("verdict") or "?")
            entry["by_verdict"][verdict] = entry["by_verdict"].get(verdict, 0) + 1
    for entry in out.values():
        entry["by_gate"] = dict(sorted(entry["by_gate"].items()))
        entry["by_verdict"] = dict(sorted(entry["by_verdict"].items()))
    return dict(sorted(out.items()))


def views(target: Path, doc: dict, states: list[dict]) -> dict:
    """The three views. `org` is the computed document, not a second computation."""
    return {
        "schema_version": 1,
        "org": {k: v for k, v in doc.items()
                if k not in ("schema_version", "unreadable", "unmeasured")},
        "team": per_component(Path(target), states),
        "developer": per_approver(Path(target), states),
        "unmeasured": list(doc.get("unmeasured") or []),
        "unreadable": dict(doc.get("unreadable") or {}),
    }


# ── the series a time-series tool can scrape ────────────────────────────────


def series(view: dict) -> list[dict]:
    """Flat `{metric, value, labels}` rows — the shape Grafana and friends ingest.

    **A metric nobody can compute is ABSENT from this file, never zero.** That is
    `metrics.py`'s rule carried into the export, and it matters more here than
    anywhere: a dashboard plots what it is given, so a null rendered as 0 becomes a
    flat line somebody reads as a measurement.
    """
    rows: list[dict] = []

    def emit(metric: str, val: Any, **labels: str) -> None:
        if isinstance(val, dict) and "value" in val:
            val = val["value"]
        if val is None or isinstance(val, (dict, list)):
            return
        if isinstance(val, bool):
            val = int(val)
        if isinstance(val, (int, float)):
            rows.append({"metric": metric, "value": val, "labels": dict(labels)})

    for section, body in (view.get("org") or {}).items():
        if not isinstance(body, dict):
            continue
        for key, val in body.items():
            if isinstance(val, dict) and "value" in val and isinstance(
                    val["value"], dict):
                for sub, n in (val["value"] or {}).items():
                    emit(f"{section}_{key}", n, view="org", bucket=str(sub))
            else:
                emit(f"{section}_{key}", val, view="org")

    for name, body in (view.get("team") or {}).items():
        for key in ("runs", "open", "closed"):
            emit(f"component_{key}", body.get(key), view="team", component=name)

    for who, body in (view.get("developer") or {}).items():
        for key in ("approvals", "forced"):
            emit(f"approver_{key}", body.get(key), view="developer", approver=who)

    return rows


# ── the page ────────────────────────────────────────────────────────────────

_CSS = """
:root { --fg:#1a1a1a; --dim:#666; --line:#e3e3e3; --bg:#fff; --warn:#8a5a00; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e8e8; --dim:#9a9a9a; --line:#333; --bg:#161616; --warn:#e0a33a; }
}
body { background:var(--bg); color:var(--fg); margin:0; padding:2rem;
  font:15px/1.55 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif; }
h1 { font-size:1.4rem; margin:0 0 .2rem; }
h2 { font-size:1.05rem; margin:2rem 0 .5rem; }
.sub { color:var(--dim); margin:0 0 1.5rem; }
table { border-collapse:collapse; width:100%; max-width:60rem; margin:.4rem 0 1rem; }
th,td { text-align:left; padding:.35rem .6rem; border-bottom:1px solid var(--line);
  vertical-align:top; }
th { color:var(--dim); font-weight:600; font-size:.82rem; text-transform:uppercase;
  letter-spacing:.03em; }
td.n { text-align:right; font-variant-numeric:tabular-nums; }
.note { color:var(--warn); }
.empty { color:var(--dim); font-style:italic; }
code { font:13px ui-monospace,SFMono-Regular,Consolas,monospace; }
"""


def _esc(x: Any) -> str:
    return html.escape(str(x), quote=True)


def _table(headers: list[str], rows: list[list[Any]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{_esc(empty)}</p>'
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(
            f'<td class="n">{_esc(c)}</td>' if isinstance(c, (int, float))
            else f"<td>{_esc(c)}</td>" for c in row) + "</tr>"
        for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render(view: dict, target: str = "") -> str:
    """One self-contained page. No external assets, and no invented numbers."""
    org = view.get("org") or {}

    def scalar(section: str, key: str) -> Any:
        body = org.get(section)
        if not isinstance(body, dict):
            return "-"
        cell = body.get(key)
        if isinstance(cell, dict):
            return "unmeasured" if cell.get("value") is None else cell["value"]
        return cell if cell is not None else "-"

    org_rows = [
        ["runs", scalar("runs", "total")],
        ["open", scalar("runs", "open")],
        ["closed", scalar("runs", "closed")],
        ["approvals", scalar("gates", "approvals")],
        ["forced approvals", scalar("gates", "forced")],
        ["forced advances", scalar("gates", "forced_advances")],
        ["review sessions", scalar("review", "sessions")],
        ["debt outstanding", scalar("debt", "outstanding")],
    ]
    team_rows = [[name, b.get("runs", 0), b.get("open", 0), b.get("closed", 0),
                  ", ".join(f"{k}:{v}" for k, v in (b.get("by_phase") or {}).items())]
                 for name, b in (view.get("team") or {}).items()]
    dev_rows = [[who, b.get("approvals", 0), b.get("forced", 0),
                 ", ".join(f"{k}:{v}" for k, v in (b.get("by_gate") or {}).items())]
                for who, b in (view.get("developer") or {}).items()]

    unmeasured = view.get("unmeasured") or []
    unmeasured_html = (
        "<h2>Not measured, and why</h2>"
        "<p class=\"sub\">A metric nobody can compute is never zero. Each line is a "
        "measurement this workspace cannot make yet, with its reason.</p>"
        + _table(["metric", "reason"],
                 [[line.split(":", 1)[0], line.split(":", 1)[-1].strip()]
                  for line in unmeasured],
                 "everything this document reports was measured.")
        if unmeasured else "")

    # **Rows nobody could read are shown, not swallowed.** Without this a corrupt
    # `approvals.jsonl` renders as "no gate has been taken yet" - a page reporting
    # a quieter workspace than the one that exists, which is the silent drop this
    # project refuses everywhere else. Found by reading the renderer rather than
    # running it, which is the only reason it is here.
    unreadable = view.get("unreadable") or {}
    unreadable_html = (
        "<h2 class=\"note\">Rows that could not be read</h2>"
        "<p class=\"sub\">Every count above is over the rows that PARSED. These did "
        "not, so each view is narrower than the workspace by this much — which is a "
        "different thing from a workspace with less in it.</p>"
        + _table(["record", "unreadable rows"],
                 [[k, v] for k, v in sorted(unreadable.items())], "")
        if unreadable else "")

    return (
        "<!doctype html><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>SDLC harness — dashboard</title>"
        f"<style>{_CSS}</style>"
        "<h1>SDLC harness — dashboard</h1>"
        # The repository NAME, never the absolute path. This page is written into
        # `.sdlc/metrics/`, which adopters commit - so a full path would publish one
        # machine's directory layout to everyone who clones, and the harness's own
        # private-coordinate guard cannot see a file it writes into someone else's
        # repository.
        f'<p class="sub">{_esc(Path(target).name) or "this workspace"} · '
        "org, team and developer views over what the harness measured. "
        "<strong>Team means one component and developer means one gate approver</strong> "
        "— this harness has no organisation or team model, and does not invent one.</p>"
        "<h2>Org — the whole workspace</h2>"
        + _table(["measure", "value"], org_rows, "nothing measured yet.")
        + "<h2>Team — per component</h2>"
        + _table(["component", "runs", "open", "closed", "phases"], team_rows,
                 "no run could be attributed to a component.")
        + "<h2>Developer — per gate approver</h2>"
        '<p class="sub">Read from the approval ledger. Review records carry no '
        "identity by construction, and are not read here.</p>"
        + _table(["approver", "gates taken", "forced", "by gate"], dev_rows,
                 "no gate has been taken yet.")
        + unmeasured_html
        + unreadable_html
    )


def write(target: Path, view: dict) -> tuple[Path, Path]:
    """Both artifacts. Returns where each went."""
    page = Path(target) / DASHBOARD
    rows = Path(target) / SERIES
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(render(view, str(target)), encoding="utf-8", newline="\n")
    rows.write_text(json.dumps({"schema_version": 1, "series": series(view)},
                               indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return page, rows
