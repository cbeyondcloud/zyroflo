"""Batch 23 (T589; design pass 23, section 1): a chart's claim carries its image as the reader
publishes it - `image` (`image.repository`) and `tag`, and now `registry` where the values set
`image.registry` - each through `_yaml`'s de-templating, so a template span is `_yaml.TOKEN`
(D23-55). A chart whose only name source is a templated image path publishes no claim (D23-70).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.extractors._image import image_key, literal_tail  # noqa: E402
from tools.graph.extractors._yaml import TOKEN  # noqa: E402
from tools.graph.tests._estate import build  # noqa: E402

TEMPLATE = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: x\n"


def chart(values: str) -> dict:
    return {"charts": {"web/Chart.yaml": "apiVersion: v2\nname: web\nversion: 0.1.0\n",
                       "web/values.yaml": values,
                       "web/templates/deployment.yaml": TEMPLATE}}


def claim(built) -> dict:
    return built.deploy("charts/web/values.yaml")["props"]


def test_a_charts_registry_is_carried_as_the_values_set_it(tmp_path):
    built = build(tmp_path, chart("nameOverride: web\nimage:\n  registry: ghcr.io\n"
                                  "  repository: org/web\n  tag: \"1.4\"\n"))
    props = claim(built)
    assert (props["image"], props["tag"], props["registry"]) == ("org/web", "1.4", "ghcr.io")


def test_a_chart_setting_no_registry_carries_none(tmp_path):
    built = build(tmp_path, chart("nameOverride: web\nimage:\n  repository: org/web\n"))
    assert "registry" not in claim(built)


@pytest.mark.parametrize("values, field, published", [
    ("nameOverride: web\nimage:\n  registry: {{ .Values.global.registry }}\n"
     "  repository: org/web\n", "registry", TOKEN),
    ("fullnameOverride: web\nimage:\n  repository: {{ .Values.global.registry }}/org/web\n",
     "image", f"{TOKEN}/org/web"),
    ("nameOverride: web\nimage:\n  repository: org/web\n  tag: {{ .Chart.AppVersion }}\n",
     "tag", TOKEN),
], ids=["a-templated-registry", "a-templated-repository-path", "a-templated-tag"])
def test_a_template_span_is_published_as_the_token(tmp_path, values, field, published):
    """D23-55: a span written bare is no YAML, so helm reads the values through `_yaml`'s
    de-templating, which writes it as the token - the image link reads it as interpolated,
    never as an ordinary key."""
    assert claim(build(tmp_path, chart(values)))[field] == published


def test_a_quoted_template_span_is_published_as_written_and_has_no_key(tmp_path):
    """Quoted, the span is YAML and is published as written; `_image` reads a brace as
    interpolation, so it has no key either, and its literal tail is what follows it."""
    props = claim(build(tmp_path, chart(
        "fullnameOverride: web\nimage:\n"
        "  repository: \"{{ .Values.global.registry }}/org/web\"\n")))
    assert props["image"] == "{{ .Values.global.registry }}/org/web"
    assert image_key(props["image"], props.get("registry", "")) == ""
    assert literal_tail(props["image"]) == "org/web"


def test_a_chart_named_only_by_a_templated_image_path_publishes_no_claim(tmp_path):
    """D23-70: `helm.logical_service` refuses a name holding the token, so the chart claims
    nothing and its `unnamed-deployment` row asks for a name."""
    built = build(tmp_path, chart(
        "image:\n  repository: {{ .Values.global.registry }}/org/web\n"))
    assert not [e for e in built.deploys if e["src"] == "deploy:charts/web/values.yaml"]
    assert [r["source_file"] for r in built.kinds("unnamed-deployment")] == ["web/values.yaml"]
