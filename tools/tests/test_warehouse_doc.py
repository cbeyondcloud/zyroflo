"""T453: the warehouse loader recipe (`docs/warehouse-loading.md`) held to the code.

The page's Postgres tables are what an organisation copies into its warehouse.
A column renamed in `metrics.export_columns` but not on the page hands every
such loader a `COPY` that shifts values into the wrong columns, so each table's
column list is held EQUAL, in order, to the export's header for its event type.
The declared types are held to a REAL export too: every non-empty cell must
parse as the type the page declares, and every NOT NULL column must be filled.

The test reads the export layout-agnostically (every `*.csv` under `--out`,
grouped by each row's own `event` field), because the file layout is T454's.
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import events as E  # noqa: E402
from tools.sdlc import metrics as M  # noqa: E402
from tools.tests.test_metrics_export import _walked, repo  # noqa: E402,F401

DOC = ROOT / "docs" / "warehouse-loading.md"
METRICS = ROOT / "tools" / "sdlc" / "metrics.py"
TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS sdlc_(\w+) \((.*?)\n\);", re.S)


def tables(text: str) -> dict[str, list[tuple[str, str, bool]]]:
    """{event type: [(column, type, not_null), ...]} from the page's DDL."""
    out: dict[str, list[tuple[str, str, bool]]] = {}
    for event, body in TABLE.findall(text):
        cols = []
        for line in body.strip().splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            name, kind, *rest = line.split()
            constraint = " ".join(rest).upper()
            cols.append((name.strip('"'), kind.upper(),
                         "NOT NULL" in constraint or "PRIMARY KEY" in constraint))
        out[event] = cols
    return out


def test_every_event_type_has_a_table_whose_columns_are_the_export_header():
    found = tables(DOC.read_text(encoding="utf-8"))
    assert set(found) == set(E.TYPES), (
        f"tables on the page {sorted(found)} vs event types {sorted(E.TYPES)}")
    for event, cols in found.items():
        assert [c for c, _, _ in cols] == M.export_columns(event), event


def test_a_renamed_column_is_caught():
    """Positive control: the parser above must see a rename, or the test
    above proves nothing."""
    text = DOC.read_text(encoding="utf-8").replace("    tokens_input BIGINT,",
                                                   "    tokens_in BIGINT,")
    assert [c for c, _, _ in tables(text)["step_finished"]] != \
        M.export_columns("step_finished")


def test_the_page_names_the_column_dictionary_and_loads_idempotently():
    text = DOC.read_text(encoding="utf-8")
    assert "docs/metrics-columns.md" in text
    assert (ROOT / "docs" / "metrics-columns.md").is_file()
    assert "ON CONFLICT (hash) DO NOTHING" in text
    assert "WITH (FORMAT csv, HEADER true)" in text
    assert "T454" in text
    # The file layout is T454's; the recipe must not pin today's flat names.
    assert "events-" not in text


def _parses(kind: str, value: str) -> bool:
    try:
        if kind in ("INTEGER", "BIGINT"):
            int(value)
        elif kind == "NUMERIC":
            Decimal(value)
        elif kind == "BOOLEAN":
            # Postgres's boolean input, case-insensitive.
            return value.strip().lower() in {"true", "false", "t", "f", "yes", "no",
                                             "on", "off", "1", "0"}
        elif kind == "TIMESTAMPTZ":
            return datetime.fromisoformat(value).tzinfo is not None
        elif kind == "JSONB":
            json.loads(value)
        elif kind != "TEXT":
            return False
    except (ValueError, ArithmeticError):
        return False
    return True


def test_a_real_export_loads_into_the_declared_types(repo, tmp_path):
    _walked(repo)
    out = tmp_path / "out"
    done = subprocess.run([sys.executable, str(METRICS), "--target", str(repo),
                           "--export", "csv", "--out", str(out)], capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    declared = tables(DOC.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    for path in sorted(out.rglob("*.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                event = row["event"]
                cols = declared[event]
                assert list(reader.fieldnames or []) == [c for c, _, _ in cols], path.name
                seen[event] = seen.get(event, 0) + 1
                for name, kind, not_null in cols:
                    value = row[name]
                    if value == "":
                        assert not not_null, f"{event}.{name} is NOT NULL but empty"
                    else:
                        assert _parses(kind, value), f"{event}.{name}={value!r} is not {kind}"
    # The export must actually exercise the tables the recipe leads with.
    assert {"run_opened", "step_finished", "gate_decided"} <= set(seen), seen
