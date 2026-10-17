"""Batch 23 (T589; design pass 23, D23-41, D23-69): the assembled config's key sets, pinned where
nothing pinned them. `deploys`, a workspace declaration, joins the persistent config at the
build's, the load's and `--check-fresh`'s sites; `unlisted`, run-scoped, joins the extraction
config and the link passes' copy only - never the config the freshness stamps digest.

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph.tests._estate import lay_down  # noqa: E402

#: The persistent config: what the stamps digest is drawn from, and every pass reads.
PERSISTENT = {"workspace_name", "canonical", "generated", "entrypoint", "alias", "apart",
              "deploys", "components", "profile"}
#: What the extractors' copy adds, run-scoped.
EXTRACTION = PERSISTENT | {"deployment_off_case", "unlisted"}
#: What each link pass's copy adds, run-scoped.
LINK = PERSISTENT | {"narrowed", "deployment_inputs", "deployment_claims",
                     "deployment_off_case", "unlisted"}

ESTATE = {"deploy": {"compose.yaml": "services:\n  web:\n    image: org/web:2\n"},
          "orders": {"main.py": "print(1)\n"}}


class Recorder:
    """A link pass that records the keys of the config it is handed."""

    def __init__(self) -> None:
        self.keys: list[set[str]] = []

    def link(self, ctx, nodes, edges):
        self.keys.append(set(ctx.config))
        return None


def test_the_assembled_config_key_sets_at_the_build_the_load_and_check_fresh(tmp_path,
                                                                            monkeypatch):
    toml = lay_down(tmp_path, ESTATE)
    recorder = Recorder()
    seen: dict[str, list[set[str]]] = {"extraction": [], "judged": []}
    real_links, real_extract, real_verdicts = (B.run_link_passes, B.run_extractors_owned,
                                               B.shard_verdicts)

    def links(link_regs, *args, **kwargs):
        return real_links([B.LinkRegistration(name="b23-recorder", layer="link",
                                              link_pass=recorder), *link_regs], *args, **kwargs)

    def extract(regs, act, components, root, run_id, config, *args, **kwargs):
        seen["extraction"].append(set(config))
        return real_extract(regs, act, components, root, run_id, config, *args, **kwargs)

    def verdicts(root, components, regs, act, config, *args, **kwargs):
        seen["judged"].append(set(config))
        return real_verdicts(root, components, regs, act, config, *args, **kwargs)

    monkeypatch.setattr(B, "run_link_passes", links)
    monkeypatch.setattr(B, "run_extractors_owned", extract)
    monkeypatch.setattr(B, "shard_verdicts", verdicts)
    B.build(toml, tmp_path / "built", run_id="keys", publish_snapshot=True)
    B.build_from_snapshot(toml, tmp_path / "loaded", run_id="keys")
    B.check_freshness(toml)
    assert seen["extraction"] == [EXTRACTION], seen["extraction"]
    assert recorder.keys == [LINK, LINK], "the build's link passes, then the load's"
    assert seen["judged"] and all(keys == PERSISTENT for keys in seen["judged"]), seen["judged"]
