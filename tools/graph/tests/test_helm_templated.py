"""Go-templated charts, read by de-templating rather than rendering (item 87).

139 of a real workspace's 143 unparsable rows were chart templates — YAML skeletons
whose `{{ … }}` spans broke the parse while the host map around them was ordinary YAML.
The de-templating pass is LINE-PRESERVING, which is the whole contract: every citation
against the original file stays true, a value a span passed through is refused as a
literal, and a file that still will not parse stays a loud `unparsable` row.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib import ids, props  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import _yaml, helm  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"

pytestmark = pytest.mark.skipif(not props.yaml_available(),
                                reason="no parser installed for this language")


def run(tmp_path: Path, files: dict[str, str], extractor, alias: str = "deploy"):
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return extractor.extract(ExtractContext(
        repos=[RepoRef(alias=alias, path=root)], run_id="r", files={alias: paths}))


# ── detemplate: line-preserving, and only as clever as it needs to be ─────────


def test_detemplate_never_changes_the_line_count():
    text = ('apiVersion: v1\n{{- if .Values.on }}\nname: {{ template "x" . }}\n'
            'plain: yes\n{{- end }}\n')
    out = _yaml.detemplate(text)
    assert out.count("\n") == text.count("\n")
    assert "plain: yes" in out and _yaml.TOKEN in out


def test_a_span_left_open_across_lines_becomes_comments_not_garbage():
    text = "a: 1\nb: {{ multi\n  line span }}\nc: 2\n"
    out = _yaml.detemplate(text)
    assert out.splitlines()[1].startswith("#") and out.splitlines()[2].startswith("#")
    assert props.yaml.safe_load(out) == {"a": 1, "c": 2}


# ── the fixture chain, at the answer key's own lines ──────────────────────────


def fixture_files() -> dict[str, str]:
    comp = FIXTURE / "deploy-charts"
    return {p.relative_to(comp).as_posix(): p.read_text(encoding="utf-8")
            for p in comp.rglob("*") if p.is_file()}


def test_the_literal_key_resolves_at_its_own_line_despite_the_control_construct(tmp_path):
    res = run(tmp_path, fixture_files(), helm.ConfigMapsExtractor(), alias="deploy-charts")
    (edge,) = [e for e in res.edges
               if e.source_file.endswith("tpl-configmap.yaml")]
    assert edge.dst == ids.env_key_id("NOTIFIER_HOST")
    assert edge.line == 7, "EXPECTED.md cites :7 — line 5's {{- if }} must not shift it"
    assert edge.props["value"] == "http://orders-notifier:8085"


def test_a_templated_value_is_a_row_never_an_address(tmp_path):
    res = run(tmp_path, fixture_files(), helm.ConfigMapsExtractor(), alias="deploy-charts")
    (row,) = [r for r in res.unresolved if r["kind"] == "templated-value"]
    assert row["line"] == 8 and "NOTIFIER_TOKEN" in row["raw"]
    assert ".Values.notifier.token" in row["raw"], "the raw line is the record"
    assert ids.env_key_id("NOTIFIER_TOKEN") not in {e.dst for e in res.edges}
    assert _yaml.TOKEN not in str({e.props.get("value") for e in res.edges}), \
        "the placeholder must never be published as a value"


def test_a_templated_name_is_unnamed_not_the_token(tmp_path):
    res = run(tmp_path, fixture_files(), helm.ConfigMapsExtractor(), alias="deploy-charts")
    (node,) = [n for n in res.nodes
               if n.props.get("path", "").endswith("tpl-configmap.yaml")]
    assert node.props["name"] == ""


def test_the_plain_configmap_beside_it_is_untouched(tmp_path):
    res = run(tmp_path, fixture_files(), helm.ConfigMapsExtractor(), alias="deploy-charts")
    plain = [e for e in res.edges if e.source_file.endswith("templates/configmap.yaml")]
    assert any(e.dst == ids.env_key_id("ORDERS_API_HOST") and e.line == 6 for e in plain)


# ── the deployments reader and the still-broken case ──────────────────────────


def test_a_templated_values_file_falls_back_and_refuses_the_token_as_a_name(tmp_path):
    res = run(tmp_path, {"values.yaml":
                         'service:\n  name: {{ .Values.override }}\n'
                         'image:\n  repository: registry/invoicing-svc\n'},
              helm.DeploymentsExtractor())
    (edge,) = [e for e in res.edges if e.type == "DG_DEPLOYS"]
    assert "invoicing-svc" in edge.dst, \
        "the templated name is skipped; the literal image path names the service"


def test_a_file_broken_beyond_detemplating_stays_a_loud_unparsable(tmp_path):
    res = run(tmp_path, {"templates/broken.yaml":
                         "kind: ConfigMap\n\t{{ .x }}\n  bad:\n indent\n"},
              helm.ConfigMapsExtractor())
    (row,) = [r for r in res.unresolved if r["kind"] == "unparsable"]
    assert row["error"]


# ── W301 / T120: provenance, templated data blocks, and per-document identity ──


def test_the_deploy_line_points_at_the_field_even_when_the_name_is_an_image_path(tmp_path):
    """T120(a). The service name is the last segment of `image.repository`, so a search for
    the exact substring `repository: <name>` never matches the fuller `repository: registry/
    <name>` line and the DG_DEPLOYS edge cites line 0. A key-anchored finder cites the field."""
    res = run(tmp_path, {"values.yaml":
                         "image:\n  repository: registry/invoicing-svc\n  tag: '1.2'\n"},
              helm.DeploymentsExtractor())
    (edge,) = [e for e in res.edges if e.type == "DG_DEPLOYS"]
    assert edge.line == 2, "provenance must cite the image path's own line, not 0"


def test_the_deploy_line_survives_a_quoted_service_name(tmp_path):
    """T120(a). A quoted value (`name: "orders-api"`) never equals the parsed name
    `orders-api`, so the exact-substring finder misses and the edge cites line 0."""
    res = run(tmp_path, {"values.yaml": 'service:\n  name: "orders-api"\n'},
              helm.DeploymentsExtractor())
    (edge,) = [e for e in res.edges if e.type == "DG_DEPLOYS"]
    assert edge.line == 2


def test_a_wholly_templated_data_block_is_a_row_not_a_silent_skip(tmp_path):
    """T120(b). `data: {{ toYaml ... }}` de-templates to a scalar, so `not isinstance(data,
    dict)` used to `continue` and emit NOTHING — the very silence templated-value exists to
    prevent, one level up from a single value."""
    res = run(tmp_path, {"templates/cm.yaml":
                         "kind: ConfigMap\nmetadata:\n  name: gen\n"
                         "data: {{ .Values.config | toYaml | nindent 2 }}\n"},
              helm.ConfigMapsExtractor())
    (row,) = [r for r in res.unresolved if r["kind"] == "templated-value"]
    assert row["line"] == 4 and "toYaml" in row["raw"]
