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
#: T447b: the Markdown report - Mermaid charts that render where a team already
#: reads (GitHub, Claude Code, Cursor), deterministic so it diffs in a PR.
REPORT = Path(".sdlc") / "metrics" / "REPORT.md"

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


def audit(target: Path, states: list[dict]) -> dict:
    """T447b: the audit trail as the page shows it - every event, and whether
    each run's chain VERIFIED. A trail that was edited must say so on the page,
    not render quietly as though nothing happened."""
    from tools.sdlc import events as ev
    from tools.sdlc import stage

    trail, chain = [], {}
    for run_id in sorted(str(st.get("run_id")) for st in states if st.get("run_id")):
        problems = ev.verify(target, run_id)
        chain[run_id] = problems
        for e in ev.read(target, run_id):
            if "_unreadable" in e:
                continue
            trail.append({"run": run_id, "seq": e.get("seq"), "at": e.get("at"),
                          "event": e.get("event"), "step": e.get("step_name") or e.get("step"),
                          "what": e.get("gate") or e.get("outcome") or e.get("status")
                          or (f"{e.get('from')} -> {e.get('to')}" if e.get("from") else "")})
    try:
        pipe = stage.pipeline(target)
        pipeline = stage.render_text(pipe, set(stage.resolved_hard_gates(target)))
    except Exception as exc:        # noqa: BLE001 - the page says why, never blanks
        pipeline = [f"(the pipeline could not be resolved: {exc})"]
    return {"trail": trail, "chain": chain, "pipeline": pipeline}


def views(target: Path, doc: dict, states: list[dict]) -> dict:
    """The three views. `org` is the computed document, not a second computation."""
    return {
        "steps": doc.get("steps") or {},
        "audit": audit(Path(target), states),
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


def _cell(node: Any) -> Any:
    """A metric cell as the page shows it: the number, or "not measurable -
    <why>" for a null. Never a 0 for a number nobody could compute."""
    if isinstance(node, dict) and "value" in node:
        if node["value"] is None:
            return f"not measurable - {node.get('why') or 'no reason recorded'}"
        return node["value"]
    return node if node is not None else "-"


def _bars(pairs: list[tuple[str, Any]], unit: str = "") -> str:
    """A horizontal bar chart as INLINE SVG - no library, no CDN (a dashboard
    that needs the network renders blank exactly where it is most needed).
    Only measured numbers are drawn; an unmeasured one is not a short bar."""
    drawn = [(label, v) for label, v in pairs
             if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not drawn:
        return ""
    top = max(v for _, v in drawn) or 1
    row, width = 22, 420
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" role="img" width="{width + 260}" '
             f'height="{row * len(drawn) + 6}" style="max-width:100%">']
    for i, (label, v) in enumerate(drawn):
        y = i * row + 3
        w = max(1, round(width * v / top))
        parts.append(f'<text x="0" y="{y + 14}" font-size="12" fill="currentColor">'
                     f'{_esc(label)}</text>'
                     f'<rect x="150" y="{y}" width="{w}" height="16" rx="2" '
                     f'fill="currentColor" opacity=".35"/>'
                     f'<text x="{150 + w + 6}" y="{y + 14}" font-size="12" '
                     f'fill="currentColor">{_esc(v)}{_esc(unit)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _audit_html(view: dict) -> str:
    """The T447b sections: the pipeline, steps, models, gates, rework, the trail."""
    steps = view.get("steps") or {}
    audit_view = view.get("audit") or {}
    out = ["<h2>Pipeline</h2>",
           "<pre><code>" + _esc("\n".join(audit_view.get("pipeline") or [])) + "</code></pre>"]

    by_step = steps.get("by_step")
    step_rows, dur_bars = [], []
    if isinstance(by_step, dict) and "value" not in by_step:
        for name, b in by_step.items():
            p50 = _cell((b.get("duration_ms") or {}).get("p50"))
            step_rows.append([name, _cell(b.get("attempts")), p50,
                              _cell((b.get("duration_ms") or {}).get("p90")),
                              _cell((b.get("tokens") or {}).get("input")),
                              _cell((b.get("tokens") or {}).get("output")),
                              _cell(b.get("cost")),
                              ", ".join(f"{k}:{v}" for k, v in
                                        (_cell(b.get("outcomes")) or {}).items())
                              if isinstance(_cell(b.get("outcomes")), dict) else "-"])
            dur_bars.append((name, p50))
    out += ["<h2>Steps</h2>", _bars(dur_bars, " ms"),
            _table(["step", "attempts", "p50 ms", "p90 ms", "tokens in", "tokens out",
                    "cost", "outcomes"], step_rows,
                   str(_cell(by_step)) if isinstance(by_step, dict) and "value" in by_step
                   else "no step has run under the driver yet.")]

    by_model = steps.get("by_model")
    model_rows, token_bars = [], []
    if isinstance(by_model, dict) and "value" not in by_model:
        for spec, b in by_model.items():
            tin = _cell((b.get("tokens") or {}).get("input"))
            model_rows.append([spec, _cell(b.get("steps")), tin,
                               _cell((b.get("tokens") or {}).get("output")), _cell(b.get("cost"))])
            token_bars.append((spec, tin))
    out += ["<h2>Models</h2>", _bars(token_bars, " tok"),
            _table(["provider/model", "steps", "tokens in", "tokens out", "cost"], model_rows,
                   str(_cell(by_model)) if isinstance(by_model, dict) and "value" in by_model
                   else "no step was routed to a model.")]

    waits = steps.get("gate_wait_ms")
    gate_rows = ([[g, _cell(w.get("p50")), _cell(w.get("p90"))] for g, w in waits.items()]
                 if isinstance(waits, dict) and "value" not in waits else [])
    out += ["<h2>Gates — human wait</h2>",
            _table(["gate", "p50 ms", "p90 ms"], gate_rows, "no gate has been decided yet.")]

    loops = _cell(steps.get("loop_backs"))
    out += ["<h2>Rework — loop-backs</h2>",
            _table(["from -> to", "times"],
                   [[k, v] for k, v in loops.items()] if isinstance(loops, dict) else [],
                   "no run has looped back.")]

    chain = audit_view.get("chain") or {}
    broken = {r: ps for r, ps in chain.items() if ps}
    banner = ('<p class="note"><strong>CHAIN BROKEN</strong> - '
              + "; ".join(f"{_esc(r)}: {_esc(ps[0])}" for r, ps in sorted(broken.items()))
              + "</p>" if broken else
              f'<p class="sub"><strong>Chain verified</strong> for all {len(chain)} run(s).</p>'
              if chain else '<p class="empty">no run has an audit trail yet.</p>')
    trail_rows = [[e["run"], e["seq"], e["at"], e["event"], e["step"], e["what"]]
                  for e in audit_view.get("trail") or []]
    out += ["<h2>Audit trail</h2>", banner,
            _table(["run", "seq", "at", "event", "step", "gate / outcome"], trail_rows,
                   "no event recorded yet.")]
    return "".join(out)


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
        + _audit_html(view)
        + unmeasured_html
        + unreadable_html
    )


def report(view: dict, target: str = "") -> str:
    """`REPORT.md` (T447b): the same numbers as Markdown with Mermaid charts,
    which render inline where a team discusses a run. Deterministic - no time,
    no path - so it diffs cleanly and can be pasted into a pull request."""
    steps = view.get("steps") or {}
    lines = [f"# SDLC harness report - {Path(target).name or 'this workspace'}", "",
             "Generated by `metrics.py --report` from the committed audit trail. "
             "A number nobody could measure reads *not measurable*, never 0.", ""]
    lines += ["## Pipeline", "", "```", *(view.get("audit") or {}).get("pipeline", []), "```", ""]
    by_step = steps.get("by_step")
    lines += ["## Steps", ""]
    if isinstance(by_step, dict) and "value" not in by_step:
        lines += ["| step | attempts | p50 ms | tokens in | tokens out | cost |",
                  "|---|---|---|---|---|---|"]
        for name, b in by_step.items():
            lines.append(f"| {name} | {_cell(b.get('attempts'))} | "
                         f"{_cell((b.get('duration_ms') or {}).get('p50'))} | "
                         f"{_cell((b.get('tokens') or {}).get('input'))} | "
                         f"{_cell((b.get('tokens') or {}).get('output'))} | "
                         f"{_cell(b.get('cost'))} |")
        outcomes: dict[str, int] = {}
        for b in by_step.values():
            for k, v in (_cell(b.get("outcomes")) or {}).items():
                outcomes[k] = outcomes.get(k, 0) + v
        if outcomes:
            lines += ["", "```mermaid", "pie title Step outcomes",
                      *(f'    "{k}" : {v}' for k, v in sorted(outcomes.items())), "```"]
    else:
        lines.append(f"*{_cell(by_step)}*")
    loops = _cell(steps.get("loop_backs"))
    lines += ["", "## Rework", ""]
    lines += ([f"- {k}: {v}" for k, v in loops.items()] if isinstance(loops, dict) and loops
              else ["*no run has looped back*"])
    chain = (view.get("audit") or {}).get("chain") or {}
    broken = sorted(r for r, ps in chain.items() if ps)
    lines += ["", "## Audit trail", "",
              (f"**CHAIN BROKEN** in {', '.join(broken)}" if broken
               else f"Chain verified for all {len(chain)} run(s).")]
    return "\n".join(lines) + "\n"


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
