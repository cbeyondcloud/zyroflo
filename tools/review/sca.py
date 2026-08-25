"""The SCA gate — item 40: known vulnerabilities in what the changed components
actually depend on.

`osv-scanner` is pinned like the other vendored tools and run over the changed
components. Its findings land in the same rdjson-shaped document the review
pre-pass produces, so `loop.assess` weighs them against `block_on` and they
reach G3 and G4 through the machinery that already carries findings there.

**The formats are the tool's answer, not a list here.** `scan source -r` walks
a component and reads whatever manifests it recognises, so "whichever formats
the changed components actually use" is decided by the binary rather than by a
table that would silently stop matching the day someone adds a `pnpm-lock.yaml`.
Nothing in this file names an ecosystem.

**Four things measured on osv-scanner 2.5.1 rather than read in documentation:**

*Exit 1 is findings, not failure* — the JSON is on stdout and parses. Exit 0 is
clean. Exit 127 is the tool refusing: an unreadable path, or a format it cannot
identify. The discriminator between a findings exit and a crash is whether
stdout parses, which is the rule `prepass.py` already applies to ast-grep.

*A `pom.xml` naming a module the public registry does not have FAILS THE WHOLE
SCAN.* The transitive resolver tries to fetch it and gives up with exit 127 —
so on any estate with internal, unpublished modules the gate would be
permanently broken rather than useful. The scan therefore retries with that one
resolver disabled and **records the reduction**: direct dependencies only, said
out loud, because a quietly narrowed gate reporting zero is the failure this
whole file exists to avoid.

*`results[]` carries only the sources that HAVE findings.* A clean scan and a
scan that read nothing produce the same empty array, so "nothing was scanned"
is taken from the tool's own `Scanned <path> ... found N packages` log line.
When nothing was scanned the status says so and is not a pass.

*`max_severity` is a CVSS SCORE, not a label.* rdjson has three severities and
`block_on` has five, so a 9.8 arriving as `ERROR` would become `high` and a
profile blocking on `critical` alone would let it through. Each diagnostic
therefore states its severity in `block_on`'s own vocabulary; a vulnerability
OSV has not scored is stated as UNGRADED, which holds the loop open rather than
being rounded to something convenient.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.review import prepass  # noqa: E402

SCHEMA_VERSION = 1

#: The pinned binary's base name in `third_party/manifest.json`.
BINARY = "osv-scanner"

#: The name this tool reports itself under, in the pre-pass document's
#: vocabulary. `loop.assess` groups findings by it.
TOOL = "osv-scanner"

#: CVSS bands, tightest first. The standard v3 cut points, used because they are
#: the ones the scores were assigned against — inventing a different mapping
#: would make `block_on: [critical]` mean something no advisory database does.
CVSS_BANDS: tuple[tuple[float, str], ...] = (
    (9.0, "critical"), (7.0, "high"), (4.0, "medium"), (0.0, "low"))

#: The one plugin that turns an internal module into a failed scan. Disabled
#: only on the retry, so a workspace whose dependencies ARE published keeps its
#: transitive coverage.
TRANSITIVE_PLUGIN = "transitivedependency/pomxml"

#: osv-scanner's own log line naming each source it read. Parsed because the
#: JSON cannot answer "was anything scanned at all" — `results[]` holds only
#: sources with findings. Absence is tolerated: this supports the verdict, it
#: does not decide it.
_SCANNED = re.compile(r"^Scanned (.+?) file and found (\d+) package", re.M)

#: How long one component's scan may take. It queries the OSV API, so this is
#: network-bound rather than CPU-bound.
TIMEOUT = 600


class ScaError(RuntimeError):
    """A scan that cannot be run, or a result that cannot be judged."""


def severity_of(max_severity: object) -> str:
    """A CVSS score in `block_on`'s vocabulary, or "" for ungraded.

    "" is not "low". OSV records a vulnerability with no score when nobody has
    assessed it, and a gate that reads that as the mildest thing it knows has
    decided the question it was asked to raise.
    """
    text = str(max_severity if max_severity is not None else "").strip()
    if not text:
        return ""
    try:
        score = float(text)
    except ValueError:
        return ""
    for floor, name in CVSS_BANDS:
        if score >= floor:
            return name
    return ""


#: rdjson's three levels, for readers that only speak that. The authoritative
#: value is `severity_class`; this is the lossy projection, kept so the document
#: stays a valid rdjson one.
_RDJSON = {"critical": "ERROR", "high": "ERROR", "medium": "WARNING",
           "low": "INFO", "": "WARNING"}


def parse(report: dict, root: Path) -> list[dict]:
    """An osv-scanner JSON report → rdjson diagnostics carrying an explicit
    severity.

    One diagnostic per (package, advisory group): a group is the set of ids
    describing ONE vulnerability across databases, so emitting a row per id
    would report a single CVE three times and make the count a measure of how
    many databases carry it.

    The location is the manifest at line 1. A vulnerability is a property of a
    resolved dependency rather than of a line, and pointing at a plausible line
    inside a lockfile would be a citation nobody can check.
    """
    out: list[dict] = []
    # `or []`, not a `.get` default: measured on 2.5.1, a scan with no findings
    # emits `"results": null` — the key is PRESENT and the default never fires.
    # The field run crashed here on the commonest case in the world, a clean
    # scan, because the canned fixture spelled the shape more optimistically
    # than the tool does.
    for result in report.get("results") or []:
        path = prepass.rel_to(str((result.get("source") or {}).get("path", "")), root)
        for entry in result.get("packages") or []:
            pkg = entry.get("package") or {}
            name = str(pkg.get("name", "?"))
            version = str(pkg.get("version", "?"))
            ecosystem = str(pkg.get("ecosystem", "?"))
            for group in entry.get("groups", []) or []:
                ids = [str(i) for i in (group.get("ids") or [])]
                aliases = [str(a) for a in (group.get("aliases") or [])]
                grade = severity_of(group.get("max_severity"))
                shown = ", ".join(dict.fromkeys(aliases or ids)) or "(unnamed)"
                out.append({
                    "message": (f"{ecosystem} {name} {version} is affected by "
                                f"{shown}" +
                                (f" (CVSS {group.get('max_severity')})" if grade
                                 else " — no CVSS score recorded, so this is "
                                      "UNGRADED and holds the loop open")),
                    "location": {"path": path,
                                 "range": {"start": {"line": 1, "column": 1}}},
                    "severity": _RDJSON[grade],
                    "severity_class": grade,
                    "code": {"value": ids[0] if ids else "OSV",
                             "url": f"https://osv.dev/vulnerability/{ids[0]}"
                                    if ids else ""},
                })
    return out


def scanned_paths(stderr: str, root: Path) -> list[str]:
    """Every source the tool says it read, from its own log."""
    return sorted({prepass.rel_to(m.group(1), root)
                   for m in _SCANNED.finditer(stderr or "")})


def _invoke(exe: Path, where: Path, disable: str = "") -> tuple[int, str, str]:
    cmd = [str(exe), "scan", "source", "-r", "--allow-no-lockfiles",
           "--format", "json"]
    if disable:
        cmd += ["--experimental-disable-plugins", disable]
    cmd.append(str(where))
    return prepass.run_tool(cmd, timeout=TIMEOUT)


def scan_one(exe: Path, where: Path, root: Path) -> dict:
    """Scan one component. Returns its tool row and its diagnostics."""
    code, out, err = _invoke(exe, where)
    degraded = ""
    if code not in (0, 1):
        # Measured: an internal module the public registry cannot resolve fails
        # the WHOLE scan. Retrying without that one resolver keeps the gate
        # usable on an estate, at a cost this row states.
        retry_code, retry_out, retry_err = _invoke(exe, where, TRANSITIVE_PLUGIN)
        if retry_code in (0, 1):
            code, out, err = retry_code, retry_out, retry_err
            degraded = (f"transitive resolution failed, so this component was "
                        f"scanned for DIRECT dependencies only - a transitively "
                        f"vulnerable package would not be found here. Publish the "
                        f"internal coordinates, or commit a resolved lockfile")
    report = None
    if code in (0, 1):
        try:
            report = json.loads(out or "{}")
        except ValueError:
            report = None
    if not isinstance(report, dict):
        tail = (err or "").strip().splitlines()
        return {"row": {"component": where.name, "status": "error",
                        "detail": f"exit {code}: {tail[-1] if tail else 'no output'}",
                        "findings": 0, "scanned": [], "degraded": degraded},
                "diagnostics": []}
    read = scanned_paths(err, root)
    diags = parse(report, root)
    status = "ok" if read else "nothing-scanned"
    detail = degraded
    if not read:
        detail = (detail + " | " if detail else "") + (
            "osv-scanner read no dependency manifest here - it reports only "
            "sources with findings, so this is 'nothing was scanned', which is "
            "not the same as clean")
    return {"row": {"component": where.name, "status": status, "detail": detail,
                    "findings": len(diags), "scanned": read,
                    "degraded": degraded},
            "diagnostics": diags}


def run(target: Path, components: list[dict]) -> dict:
    """The SCA gate over the named components, as a pre-pass-shaped document."""
    target = Path(target)
    exe, why = prepass.binary(BINARY)
    tools_out: list[dict] = []
    sources_out: list[dict] = []
    if exe is None:
        # The vendored-binary doctrine, and it matters most here: an empty
        # result from a scanner that never ran reads exactly like "no known
        # vulnerabilities".
        tools_out.append({"name": TOOL, "status": "not-run", "detail": why,
                          "findings": 0, "components": [c["name"] for c in components]})
        return _document(target, tools_out, sources_out, components)
    per: list[dict] = []
    diags: list[dict] = []
    for comp in components:
        where = target / (comp.get("path") or ".")
        if not where.is_dir():
            per.append({"component": comp["name"], "status": "error",
                        "detail": f"{where} is not a directory", "findings": 0,
                        "scanned": [], "degraded": ""})
            continue
        found = scan_one(exe, where, target)
        per.append(found["row"])
        diags += found["diagnostics"]
    worst = ("error" if any(r["status"] == "error" for r in per)
             else "nothing-scanned" if per and all(r["status"] == "nothing-scanned"
                                                   for r in per)
             else prepass.STATUS_OK)
    # A COUNT at the tool level and the specifics on the rows. Joining every
    # component's detail here produced one wall of text that read as if all of
    # it applied to the whole scan — the field run's own output argued this.
    notes = []
    if degraded := [r["component"] for r in per if r["degraded"]]:
        notes.append(f"{len(degraded)} component(s) scanned for direct "
                     f"dependencies only ({', '.join(degraded)})")
    if quiet := [r["component"] for r in per if r["status"] == "nothing-scanned"]:
        notes.append(f"{len(quiet)} component(s) had no manifest this scanner "
                     f"reads ({', '.join(quiet)})")
    if broke := [r["component"] for r in per if r["status"] == "error"]:
        notes.append(f"{len(broke)} component(s) errored ({', '.join(broke)})")
    tools_out.append({"name": TOOL, "status": worst,
                      "detail": "; ".join(notes),
                      "findings": len(diags),
                      "components": [r["component"] for r in per],
                      "per_component": per})
    if diags:
        sources_out.append({"source": {"name": TOOL}, "diagnostics": diags})
    return _document(target, tools_out, sources_out, components)


def _document(target: Path, tools_out: list[dict], sources_out: list[dict],
              components: list[dict]) -> dict:
    by_class: dict[str, int] = {}
    for source in sources_out:
        for diag in source["diagnostics"]:
            key = diag.get("severity_class") or "(ungraded)"
            by_class[key] = by_class.get(key, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {"mode": "components", "target": str(target),
                  "components": [c["name"] for c in components]},
        "tools": tools_out,
        "sources": sources_out,
        "summary": {"total": sum(t["findings"] for t in tools_out),
                    "by_severity": dict(sorted(by_class.items()))},
    }


def render_md(result: dict) -> str:
    lines = ["# SCA gate", "",
             f"Components: {', '.join(result['scope']['components']) or '(none)'}", ""]
    for tool in result["tools"]:
        lines.append(f"- **{tool['name']}** — {tool['status']}, "
                     f"{tool['findings']} finding(s)"
                     + (f": {tool['detail']}" if tool["detail"] else ""))
        for row in tool.get("per_component", []):
            lines.append(f"    - {row['component']}: {row['status']}, "
                         f"{row['findings']} finding(s), "
                         f"{len(row['scanned'])} manifest(s) read")
    for source in result["sources"]:
        lines.append("")
        for diag in source["diagnostics"]:
            grade = diag.get("severity_class") or "UNGRADED"
            lines.append(f"- `[{grade}]` {diag['location']['path']} — "
                         f"{diag['message']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--components", metavar="NAMES",
                    help="comma-separated component names (default: all)")
    ap.add_argument("--out", metavar="DIR", help="write sca.json and sca.md here")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from tools.sdlc import policy as guardrails

    target = Path(args.target).resolve()
    every = guardrails.components(target)
    wanted = ([c.strip() for c in args.components.split(",") if c.strip()]
              if args.components else None)
    if wanted is not None:
        known = {c["name"] for c in every}
        if unknown := sorted(set(wanted) - known):
            print(f"refused: no component named {', '.join(map(repr, unknown))} - "
                  f"one of {', '.join(sorted(known))}", file=sys.stderr)
            return 2
        every = [c for c in every if c["name"] in wanted]
    result = run(target, every)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "sca.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        (out / "sca.md").write_text(render_md(result), encoding="utf-8", newline="\n")
        print(f"sca: wrote {out / 'sca.json'} and sca.md")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_md(result), end="")
    # Exit 3 outranks exit 1, the rule `prepass.py` states - and now the rule
    # `prepass.py` OWNS. This was a deny-list of three named statuses beside a
    # comment claiming to be that rule; on a status neither anticipated the two
    # gave opposite answers, and a deny-list is silent about every status added
    # after it was written. `nothing-scanned` was itself added that way.
    if prepass.unfired(result):
        return 3
    return 1 if result["summary"]["total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
