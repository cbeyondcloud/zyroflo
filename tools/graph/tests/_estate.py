"""A throwaway workspace laid down from a dict, built through the real CLI, and read back.

T534's tests describe each shape as `{component dir: {relpath: body}}` plus a few meta keys,
the way the design review measured them, so a test reads as the shape it pins:

    __names__      {dir: component name} - default: the dir's last segment ("." needs one)
    __canonical__  [ {service, owner, path}, ... ]
    __entrypoint__ [ node id, ... ]
    __dirs__       [ extra directories to create ]

`build` runs `tools.graph.build.main` in-process, which is the pipeline a person runs; a test
that asks what a PERSON sees from a refusal spawns it instead (`spawn`).
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402


def producer(key: str) -> str:
    """Python that publishes to the topic an environment key names: the value-derived node
    (`topic:<value>`) is how a test sees which value a component's code resolved."""
    return ('import os\n\nfrom confluent_kafka import Producer\n\n'
            f'VALUE = os.environ["{key}"]\n\n'
            '_producer = Producer({"bootstrap.servers": "broker:9092"})\n\n\n'
            'def send(sku):\n    _producer.produce(VALUE, key=sku, value="x")\n')


def svc(name: str, build: str | None = None, value: str | None = "web-value",
        key: str = "TOPIC", image: str | None = None) -> str:
    """One compose service block (under `services:`), with a quoted `build:` context."""
    body = f"  {name}:\n"
    if build is not None:
        body += f"    build: '{build}'\n"
    if image is not None:
        body += f"    image: {image}\n"
    if value is not None:
        body += f"    environment:\n      {key}: {value}\n"
    return body


def compose(*services: str) -> str:
    return "services:\n" + "".join(services)


@dataclass
class Built:
    code: int
    out: Path
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)

    @property
    def topics(self) -> list[str]:
        return sorted({n["id"] for n in self.nodes if n.get("type") == "topic"})

    @property
    def deploys(self) -> list[dict]:
        return [e for e in self.edges if e.get("type") == "DG_DEPLOYS"]

    def deploy(self, source: str, service: str | None = None) -> dict:
        """The one DG_DEPLOYS edge from `deploy:<source>` (e.g. `orders/docker-compose.yml`),
        for `service` when the file declares several."""
        found = [e for e in self.deploys if e["src"] == "deploy:" + source
                 and (service is None or (e.get("props") or {}).get("service") == service)]
        assert len(found) == 1, (source, service, [e["src"] for e in self.deploys])
        return found[0]

    def placed(self, source: str, service: str | None = None) -> tuple[str, str, str]:
        """(target node, `component`, `mapped_by`) of that one edge."""
        edge = self.deploy(source, service)
        props = edge.get("props") or {}
        return edge["dst"], props.get("component", ""), props.get("mapped_by", "")

    def kinds(self, *kinds: str) -> list[dict]:
        return [r for r in self.rows if r.get("kind") in kinds]

    def svc_ids(self) -> set[str]:
        return {n["id"] for n in self.nodes if n.get("type") == "svc"}


def workspace_toml(estate: dict) -> str:
    names = estate.get("__names__", {})
    comps = [c for c in estate if not c.startswith("__")]
    text = 'schema_version = 1\nroot = "."\nname = ""\nprofile = ""\n'
    for comp in comps:
        text += f'\n[[component]]\nname = "{names.get(comp, comp.split("/")[-1])}"\n'
        text += f'path = "{comp}"\n'
    for block in estate.get("__canonical__", []):
        text += "\n[[canonical]]\n" + "".join(f'{k} = "{v}"\n' for k, v in block.items())
    for node in estate.get("__entrypoint__", []):
        text += f'\n[[entrypoint]]\nnode = "{node}"\nnote = "n"\n'
    return text


def lay_down(where: Path, estate: dict, ws_name: str = "ws") -> Path:
    """Write `estate` under `where/<ws_name>` and return its workspace.toml."""
    ws = where / ws_name
    for comp in (c for c in estate if not c.startswith("__")):
        (ws / comp).mkdir(parents=True, exist_ok=True)
        for rel, body in estate[comp].items():
            path = ws / comp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body.encode("utf-8"))
    for extra in estate.get("__dirs__", []):
        (ws / extra).mkdir(parents=True, exist_ok=True)
    toml = ws / "workspace.toml"
    toml.write_bytes(workspace_toml(estate).encode("utf-8"))
    return toml


def read(code: int, out: Path) -> Built:
    built = Built(code, out)
    if (out / "graph.jsonl").exists():
        for line in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            (built.nodes if record.get("kind") == "node" else built.edges).append(record)
    if (out / "unresolved.jsonl").exists():
        built.rows = [json.loads(line) for line in
                      (out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines()]
    return built


def build(where: Path, estate: dict | None = None, *flags: str, ws_name: str = "ws") -> Built:
    """Lay `estate` down (unless it already is) and build it in-process."""
    toml = (lay_down(where, estate, ws_name) if estate is not None
            else where / ws_name / "workspace.toml")
    out = where / "out"
    code = B.main(["--workspace", str(toml), "--out", str(out), "--run-id", "fixed",
                   "--empty-ok", *flags])
    return read(code, out)


def spawn(where: Path, *flags: str, out: str = "out") -> subprocess.CompletedProcess:
    """The same build as a PROCESS: a refusal is only a refusal if a person gets an exit code
    and a message rather than a traceback, and only a spawn can tell those apart."""
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "graph" / "build.py"),
         "--workspace", str(where / "ws" / "workspace.toml"), "--out", str(where / out),
         "--run-id", "fixed", "--empty-ok", *flags],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
