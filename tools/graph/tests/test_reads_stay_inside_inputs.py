"""No extractor reads outside the files the build handed it (T591, design review D15a-17).

T591 made a component's reading exclude its declared children's files, and a component's
freshness stamp digests exactly those files. An extractor that opened a file it was not
handed - maven reading a `<module>`'s pom by path - would then depend on bytes its stamp no
longer covers, and a stale shard would look current. maven and gradle declare that reach
(`READS_NESTED`) and are handed the children's build files; every other extractor must read
only its `ctx.inputs`.

The seam is the file-opening calls themselves - `builtins.open`, `io.open` and `Path`'s
`read_text`, `read_bytes` and `open` - for the window of each `extract(ctx)` call. Patching
the harness's own readers would leave an extractor that used `pathlib` directly invisible,
the grep's hole one level down. What this proves is that no extractor READS outside its
inputs, not that no fact depends on a filesystem answer outside them: a `stat` or a
`resolve()` is not a read, and it cannot see a declaration granted to an extractor that
never needed it, which only the facts and digests pins defend (D15a-31).

Everything here is synthetic and none of it names a real workspace.
"""
from __future__ import annotations

import builtins
import contextlib
import dataclasses
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
from tools.graph.tests._estate import build, compose, svc  # noqa: E402

PY = ('import os\n\nimport requests\nfrom fastapi import APIRouter\n'
      'from confluent_kafka import Producer\n\nrouter = APIRouter(prefix="/orders")\n'
      'TOPIC = os.environ["TOPIC"]\n\n\n@router.get("/x")\ndef x():\n'
      '    return requests.get("http://billing/api/v1/x").json()\n')
JAVA = ('package com.acme;\n\n@RestController\npublic class OrdersTest {\n'
        '    @GetMapping("/orders")\n    public String all() { return ""; }\n}\n')
CONTROLLER = ('package com.acme;\n\n@RestController\npublic class OrdersController {\n'
              '    private final RestTemplate rest = new RestTemplate();\n'
              '    private KafkaTemplate<String, String> kafka;\n'
              '    @GetMapping("/orders")\n    public String all() {\n'
              '        kafka.send("orders", "x");\n'
              '        return rest.getForObject("${billing.url}" + "/x", String.class);\n'
              '    }\n    @KafkaListener(topics = "payments")\n'
              '    public void on(String m) {}\n}\n')
AGGREGATOR = ('<project><groupId>com.acme</groupId><artifactId>platform</artifactId>'
              '<version>1.0.0</version><packaging>pom</packaging>'
              '<modules><module>svc-a</module></modules></project>\n')
CHILD_POM = ('<project><parent><groupId>com.acme</groupId><artifactId>platform</artifactId>'
             '<version>1.0.0</version></parent><artifactId>svc-a</artifactId></project>\n')
CM = ('apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: hosts\n'
      'data:\n  ORDERS_HOST: orders:8080\n')


def every_reader() -> dict:
    """A parent and its declared child holding a file for every registered extractor; the
    parent's pom is an aggregator over the child's (`<module>svc-a</module>`)."""
    own = {"docker-compose.yml": compose(svc("api", ".", None)),
           "package.json": '{"name": "@acme/platform", "version": "1.0.0"}\n',
           "web/app.js": 'fetch("/api/v1/orders");\n',
           "settings.gradle": "rootProject.name = 'platform'\n",
           "build.gradle": "group = 'com.acme'\n",
           "charts/orders/values.yaml": "image:\n  repository: reg/orders\n",
           "charts/orders/Chart.yaml": "name: orders\n",
           "charts/orders/templates/cm.yaml": CM,
           "openapi.yaml": "openapi: 3.0.0\npaths: {}\n",
           "app/api.py": PY,
           "pom.xml": AGGREGATOR,
           "src/main/resources/application.properties": "orders.url=http://orders\n",
           "db/migration/V1__init.sql": "CREATE TABLE orders (id INT);\n",
           "src/main/java/com/acme/OrdersController.java": CONTROLLER,
           "src/test/java/com/acme/OrdersTest.java": JAVA}
    child = {"svc-a/pom.xml": CHILD_POM, "svc-a/app/worker.py": PY,
             "svc-a/docker-compose.yml": compose(svc("worker", ".", None))}
    return {"platform": {**own, **child}, "platform/svc-a": {}}


@contextlib.contextmanager
def recording(monkeypatch, *, without_reach: str = ""):
    """Every path each extractor opens inside its own `extract` call, beside the inputs that
    call was handed: yields (a list of (extractor, opened path, inputs), the set of
    extractors whose `extract` ran)."""
    seen: list[tuple[str, Path, set[Path]]] = []
    called: set[str] = set()
    current: list[tuple[str, set[Path]]] = []

    def norm(target) -> Path | None:
        if isinstance(target, int):
            return None                   # a file descriptor names no path
        text = os.fspath(target)
        text = text[4:] if text.startswith("\\\\?\\") else text
        return Path(text).resolve()

    def note(target) -> None:
        if current and (path := norm(target)) is not None:
            seen.append((current[-1][0], path, current[-1][1]))

    real_open, real_io_open = builtins.open, io.open
    real_read_text, real_read_bytes, real_path_open = (Path.read_text, Path.read_bytes,
                                                       Path.open)

    def wrap(real):
        def opener(target, *args, **kwargs):
            note(target)
            return real(target, *args, **kwargs)
        return opener

    monkeypatch.setattr(builtins, "open", wrap(real_open))
    monkeypatch.setattr(io, "open", wrap(real_io_open))
    monkeypatch.setattr(Path, "read_text", wrap(real_read_text))
    monkeypatch.setattr(Path, "read_bytes", wrap(real_read_bytes))
    monkeypatch.setattr(Path, "open", wrap(real_path_open))

    class Recorded:
        def __init__(self, reg: B.Registration):
            self._inner, self._name = reg.extractor, reg.name

        def __getattr__(self, attr):
            return getattr(self._inner, attr)

        def extract(self, ctx):
            inputs = {p.resolve() for repo in ctx.repos for p in ctx.inputs(repo)}
            called.add(self._name)
            current.append((self._name, inputs))
            try:
                return self._inner.extract(ctx)
            finally:
                current.pop()

    real_discover = B.discover

    def discover(*args, **kwargs):
        regs, links = real_discover(*args, **kwargs)
        return [dataclasses.replace(
            r, extractor=Recorded(r),
            reads_nested=r.reads_nested and r.name != without_reach) for r in regs], links

    monkeypatch.setattr(B, "discover", discover)
    yield seen, called


def outside(seen) -> list[tuple[str, str]]:
    return sorted({(name, path.name if path.name == "pom.xml" else str(path))
                   for name, path, inputs in seen if path not in inputs})


def test_no_extractor_reads_outside_its_inputs(tmp_path, monkeypatch):
    """Every registered extractor runs here - one that never ran would pass vacuously - and
    none opens a file it was not handed. (`spring-rest-calls` opens nothing itself: it reads
    the properties view another extractor leaves in the shared cache.)"""
    with recording(monkeypatch) as (seen, called):
        built = build(tmp_path, every_reader())
    assert built.code == 0
    assert sorted({r.name for r in B.discover_extractors()} - called) == []
    assert outside(seen) == []


def test_the_guard_catches_a_read_outside_the_inputs(tmp_path, monkeypatch):
    """The positive control (D15a-17): with maven's declaration taken away, the parent's
    maven reading is no longer handed the child's pom, and maven's `<modules>` still reads it
    by path - exactly that one read is flagged, so the guard is wired to the right seam."""
    with recording(monkeypatch, without_reach="maven-modules") as (seen, _called):
        build(tmp_path, every_reader())
    flagged = outside(seen)
    assert flagged == [("maven-modules", "pom.xml")], flagged
    child = (tmp_path / "ws" / "platform" / "svc-a" / "pom.xml").resolve()
    assert any(name == "maven-modules" and path == child and path not in inputs
               for name, path, inputs in seen)
