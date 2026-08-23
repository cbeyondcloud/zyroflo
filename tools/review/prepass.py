"""Deterministic review pre-pass: what a machine finds before an agent reads code.

Item 25. Two vendored, pinned tools run over the change and their findings land in
one rdjson-style document BEFORE agent review starts: `gitleaks` (the mandatory
secret gate — a leak is a finding no reviewer may miss because they were reading
logic) and `ast-grep` (rule matches: the harness's small universal set plus the
repository's own rules under `.sdlc/review-rules/`). The agent review then starts
from machine facts, and the two never blur: a pre-pass diagnostic cites tool, rule
and location; an agent finding cites reasoning.

Three rules, each load-bearing:

* **A tool that could not run is a stated status and exit 3, never a silent
  clean.** The vendored-binary doctrine: an empty result from a tool that did not
  execute reads exactly like "no findings", and for a SECRET gate that misreading
  is the worst one available. Exit 3 outranks exit 1 — findings from the tool that
  ran do not make the change reviewed while the other gate never fired.
* **Secrets are redacted at the boundary.** gitleaks reports carry the matched
  secret verbatim (measured on the real binary); this document travels into review
  artifacts and PR comments, so diagnostics keep rule, file:line and fingerprint
  and NEVER the matched text — a leak report that re-publishes the leak is a
  second incident, not a finding.
* **Scope is the change.** With a diff, the touched files' current content is
  copied into a scratch tree and scanned there, plus the diff text itself as
  `_diff.patch` — a secret visible in the hunks but already edited out of the
  file must still block, because the diff is what gets pushed.

Line numbers: ast-grep's JSON is zero-based (measured, 0.45.1); rdjson and every
graph artifact in this repository are one-based, so the converter adds one.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1

#: The harness's default rule set — deliberately tiny and universally defensible;
#: anything opinionated belongs in the repository's own .sdlc/review-rules/.
RULES_DIR = Path(__file__).resolve().parent / "rules"
REPO_RULES = Path(".sdlc") / "review-rules"

#: gitleaks exit code requested for "leaks found", distinct from its error exit 1.
LEAK_EXIT = 9


def run_tool(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    """Run one vendored tool. The seam tests replace; the program is absolute."""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def binary(name: str) -> tuple[Path | None, str]:
    """The vendored binary's absolute path, or (None, how-to-get-it)."""
    from tools import third_party as tp
    plat = tp.current_platform()
    exe = tp.bin_dir(plat) / (f"{name}.exe" if plat.startswith("windows") else name)
    if not exe.exists():
        return None, (f"not run: vendored {name} missing at {exe} "
                      f"(fetch: python tools/third_party.py --fetch)")
    return exe, ""


def rel_to(path_text: str, root: Path) -> str:
    """A tool-reported absolute path as a forward-slash path relative to root."""
    p = Path(path_text.replace("\\", "/"))
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def parse_gitleaks(rows: list[dict], root: Path) -> list[dict]:
    """gitleaks report rows → redacted rdjson diagnostics.

    `Match`/`Secret` are dropped on purpose — see the module docstring. The
    fingerprint (file:rule:line) survives: it identifies the leak without
    containing it, and it is what a suppression would reference.
    """
    out = []
    for r in rows:
        out.append({
            "message": r.get("Description", r.get("RuleID", "secret detected")),
            "location": {"path": rel_to(r.get("File", ""), root),
                         "range": {"start": {"line": int(r.get("StartLine", 0)),
                                             "column": int(r.get("StartColumn", 0))}}},
            "severity": "ERROR",
            "code": {"value": r.get("RuleID", "")},
            "fingerprint": rel_to(r.get("Fingerprint", ""), root),
        })
    return out


def parse_astgrep(rows: list[dict], root: Path) -> list[dict]:
    """ast-grep --json rows → rdjson diagnostics. Lines arrive zero-based."""
    out = []
    for r in rows:
        start = r.get("range", {}).get("start", {})
        out.append({
            "message": r.get("message", ""),
            "location": {"path": rel_to(r.get("file", ""), root),
                         "range": {"start": {"line": int(start.get("line", 0)) + 1,
                                             "column": int(start.get("column", 0)) + 1}}},
            "severity": str(r.get("severity", "warning")).upper(),
            "code": {"value": r.get("ruleId", "")},
        })
    return out


def collect_rules(target: Path, extra_dirs: list[Path]) -> tuple[str, list[str]]:
    """Every rule document, joined with `---` for one --inline-rules invocation.

    Order: harness defaults, the repository's own `.sdlc/review-rules/`, then
    --rules-dir extras — later files can shadow by id at ast-grep's discretion,
    and the sources list in the output says exactly what ran.
    """
    docs: list[str] = []
    sources: list[str] = []
    for d in [RULES_DIR, target / REPO_RULES, *extra_dirs]:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yml")) + sorted(d.glob("*.yaml")):
            docs.append(f.read_text(encoding="utf-8").strip())
            sources.append(f.name if f.parent == RULES_DIR
                           else rel_to(str(f), target))
    return "\n---\n".join(docs), sources


def excluded_by(target: Path) -> list[str]:
    """`review.review_exclude` from the profile — the globs a repository folds
    out of review (lockfiles, vendored trees, generated code).

    It shipped in the template at item 60 and until now NOTHING READ IT, which
    is the worse of the two failures available: a reviewer who believes the
    lockfile was folded out reads a diff they think is smaller than it is.
    """
    from tools.sdlc import policy as guardrails
    listed = guardrails.profile_list(target, "review_exclude")
    return [str(v) for v in listed] if listed else []


def diff_scope(target: Path, diff_text: str, scratch: Path,
               exclude: list[str] | None = None) -> tuple[list[str], list[str], list[str]]:
    """Copy the diff's touched files (current content) into `scratch`, plus the
    diff text itself as `_diff.patch`. Returns (copied, excluded, notes).

    **An exclusion narrows the review, never the secret gate.** `_diff.patch`
    carries the whole diff regardless of what was folded out, so a credential in
    an excluded file's hunks still blocks — the property this module's own
    header already promised for deleted files, holding for excluded ones too.
    Otherwise `review_exclude` would be the one config field that can hide a key.
    """
    from tools.graph.impact import parse_diff
    from tools.sdlc import policy as guardrails
    patterns = exclude or []
    copied: list[str] = []
    dropped: list[str] = []
    notes: list[str] = []
    for rel in sorted(parse_diff(diff_text)):
        if patterns and guardrails.matches(rel, patterns):
            dropped.append(rel)
            continue
        src = target / rel
        if not src.is_file():
            notes.append(f"{rel}: not in the tree (deleted by the change, or the "
                         f"diff is from another root) — its hunks are still "
                         f"scanned via _diff.patch")
            continue
        dst = scratch / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        copied.append(rel)
    if dropped:
        notes.append(f"review_exclude folded out {len(dropped)} file(s): "
                     + ", ".join(dropped)
                     + " - their hunks are still scanned via _diff.patch, because "
                       "an exclusion narrows the review and never the secret gate")
    (scratch / "_diff.patch").write_text(diff_text, encoding="utf-8")
    return copied, dropped, notes


def prepass(target: Path, diff_text: str | None = None,
            extra_rule_dirs: list[Path] | None = None,
            exclude: list[str] | None = None) -> dict:
    """The whole pre-pass as data. Never raises for tool trouble — statuses say."""
    tools_out: list[dict] = []
    sources_out: list[dict] = []
    scope: dict = {"mode": "tree", "root": str(target)}
    notes: list[str] = []

    with tempfile.TemporaryDirectory(prefix="sdlc-prepass-") as td:
        scratch = Path(td)
        if diff_text is not None:
            patterns = excluded_by(target) if exclude is None else exclude
            copied, dropped, notes = diff_scope(target, diff_text, scratch, patterns)
            scope = {"mode": "diff", "files": copied, "excluded": dropped,
                     "exclude_patterns": patterns, "notes": notes,
                     # Every changed file folded out is not a small review, it is
                     # no review. `exit_code` reads this and refuses to call it a
                     # pass; the secret gate still ran, which is why the message
                     # says what did happen rather than only what did not.
                     "empty_after_exclude": bool(dropped and not copied)}
            scan_root, report_root = scratch, scratch
        else:
            scan_root, report_root = target, target

        # ── gitleaks: the secret gate ────────────────────────────────────────
        exe, why = binary("gitleaks")
        if exe is None:
            tools_out.append({"name": "gitleaks", "status": "not-run",
                              "detail": why, "findings": 0})
        else:
            report = scratch / "gitleaks-report.json"
            code, _, err = run_tool([str(exe), "dir", str(scan_root),
                                     "--report-format", "json",
                                     "--report-path", str(report),
                                     "--no-banner", "--exit-code", str(LEAK_EXIT)])
            if code == 0:
                tools_out.append({"name": "gitleaks", "status": "ok",
                                  "detail": "", "findings": 0})
            elif code == LEAK_EXIT:
                rows = json.loads(report.read_text(encoding="utf-8"))
                diags = parse_gitleaks(rows, report_root)
                tools_out.append({"name": "gitleaks", "status": "ok",
                                  "detail": "", "findings": len(diags)})
                sources_out.append({"source": {"name": "gitleaks"},
                                    "diagnostics": diags})
            else:
                tail = err.strip().splitlines()[-1] if err.strip() else f"exit {code}"
                tools_out.append({"name": "gitleaks", "status": "error",
                                  "detail": f"exit {code}: {tail}", "findings": 0})

        # ── ast-grep: the rule pass ──────────────────────────────────────────
        rules_text, rule_sources = collect_rules(target, extra_rule_dirs or [])
        exe, why = binary("sg")
        if exe is None:
            tools_out.append({"name": "ast-grep", "status": "not-run",
                              "detail": why, "findings": 0,
                              "rules": rule_sources})
        elif not rules_text:
            tools_out.append({"name": "ast-grep", "status": "not-run",
                              "detail": "no rule files found (harness defaults "
                                        "missing?)", "findings": 0, "rules": []})
        else:
            code, out, err = run_tool([str(exe), "scan", "--inline-rules",
                                       rules_text, "--json=compact",
                                       str(scan_root)])
            # Measured on 0.45.1: exit 0 means clean OR warnings-only; exit 1
            # with the JSON still on stdout means "scan succeeded and found
            # error level diagnostics" — a findings exit, not a failure. The
            # discriminator is the stdout: a findings exit carries the array, a
            # crash does not parse.
            rows = None
            if code in (0, 1):
                try:
                    rows = json.loads(out or "[]")
                except ValueError:
                    rows = None
            if isinstance(rows, list):
                diags = parse_astgrep(rows, report_root)
                # the diff patch is text, not code; ast-grep never matches it,
                # and the copied tree's own findings carry repo-relative paths.
                tools_out.append({"name": "ast-grep", "status": "ok",
                                  "detail": "", "findings": len(diags),
                                  "rules": rule_sources})
                if diags:
                    sources_out.append({"source": {"name": "ast-grep"},
                                        "diagnostics": diags})
            else:
                tail = err.strip().splitlines()[-1] if err.strip() else f"exit {code}"
                tools_out.append({"name": "ast-grep", "status": "error",
                                  "detail": f"exit {code}: {tail}", "findings": 0,
                                  "rules": rule_sources})

    total = sum(t["findings"] for t in tools_out)
    by_sev: dict[str, int] = {}
    for s in sources_out:
        for d in s["diagnostics"]:
            by_sev[d["severity"]] = by_sev.get(d["severity"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": scope,
        "tools": tools_out,
        "sources": sources_out,
        "summary": {"total": total, "by_severity": dict(sorted(by_sev.items()))},
    }


def render_md(result: dict) -> str:
    lines = ["# Deterministic review pre-pass", ""]
    scope = result["scope"]
    if scope["mode"] == "diff":
        folded = (f", with {len(scope['excluded'])} folded out by review_exclude"
                  if scope.get("excluded") else "")
        lines.append(f"Scope: the diff's {len(scope['files'])} touched file(s){folded}, "
                     f"plus the diff text itself.")
        if scope.get("empty_after_exclude"):
            lines.append("- **review_exclude removed every changed file.** The secret "
                         "gate still read the diff, but nothing was reviewed.")
        lines += [f"- note: {n}" for n in scope.get("notes", [])]
    else:
        lines.append(f"Scope: the whole tree at {scope['root']}.")
    lines.append("")
    for t in result["tools"]:
        status = t["status"] if t["status"] != "ok" else \
            f"ok — {t['findings']} finding(s)"
        lines.append(f"- **{t['name']}**: {status}"
                     + (f" ({t['detail']})" if t["detail"] else ""))
        if t.get("rules") is not None:
            lines.append(f"    rules: {', '.join(t['rules']) or '(none)'}")
    lines.append("")
    for s in result["sources"]:
        lines.append(f"## {s['source']['name']}")
        lines.append("")
        for d in s["diagnostics"]:
            loc = d["location"]
            lines.append(f"- `{loc['path']}:{loc['range']['start']['line']}` "
                         f"[{d['severity']}] {d['code']['value']}: {d['message']}")
        lines.append("")
    if not result["sources"]:
        lines.append("No findings from the tools that ran.")
        lines.append("")
    return "\n".join(lines)


def exit_code(result: dict) -> int:
    """3 beats 1 beats 0: an unfired gate outranks findings from the one that
    fired — the change is not reviewed while a gate never ran. A `review_exclude`
    that folded out every changed file lands here too: nothing ran on anything,
    which is a 3 wearing a 0's clothes."""
    if any(t["status"] != "ok" for t in result["tools"]):
        return 3
    if result["scope"].get("empty_after_exclude"):
        return 3
    return 1 if result["summary"]["total"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, metavar="DIR",
                    help="the repository root the change belongs to")
    ap.add_argument("--diff", metavar="FILE",
                    help="a unified diff; scope narrows to its touched files "
                         "plus the diff text itself (default: the whole tree)")
    ap.add_argument("--out", required=True, metavar="DIR",
                    help="where prepass.json + prepass.md are written")
    ap.add_argument("--rules-dir", action="append", default=[], metavar="DIR",
                    help="extra ast-grep rule directories (repeatable), on top "
                         "of the harness defaults and <target>/.sdlc/review-rules")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if not target.is_dir():
        print(f"!! --target is not a directory: {target}")
        return 2
    diff_text = None
    if args.diff:
        diff_path = Path(args.diff)
        if not diff_path.is_file():
            print(f"!! {diff_path} does not exist")
            return 2
        diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
        from tools.graph.impact import parse_diff
        if not parse_diff(diff_text):
            print(f"!! {diff_path} contains no file headers a unified diff would have")
            return 2

    result = prepass(target, diff_text, [Path(d) for d in args.rules_dir])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "prepass.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "prepass.md").write_text(render_md(result), encoding="utf-8")

    for t in result["tools"]:
        line = t["status"] if t["status"] != "ok" else f"ok, {t['findings']} finding(s)"
        print(f"prepass: {t['name']}: {line}" + (f" - {t['detail']}" if t['detail'] else ""))
    print(f"prepass: wrote {out / 'prepass.json'} and prepass.md")
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
