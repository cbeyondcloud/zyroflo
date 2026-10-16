"""Batch 22 (T633; design pass 22, section 4): a reader whose every shared-cache entry is keyed by
the component it ran for declares `CACHE_SCOPE = "component"`, and its cross-component dependence
is then confined to the producers it `REQUIRES`. So the dependents rule (claims 9 and 10) follows
each reader's CROSS-COMPONENT closure - its transitive `REQUIRES` closure minus its
component-scoped members - and every cache writer declares `CACHE_KEYS`, the namespaces it owns.

A wrong declaration can mis-judge a verdict, never change the graph: which producers re-run is
never narrowed (D22-33). Five guards defend it, generic over the registry, over ONE instrumented
reference build of an estate built to feed every declared (namespace, component) cell, its cache
recorded and replayed as a deep copy:

1. writes - a reader adds, changes or removes nothing outside its `CACHE_KEYS` at any depth (one
   declaring none writes nothing), and a component-scoped one no other component's entry;
2. reads - a consumer of a component-scoped producer reads no other component's entry of it;
3. the closure function keeps every shared member of a scoped producer's closure;
4. the declaration itself - a scoped producer's entry for a component does not move when another
   component's files are edited, added or removed;
5. an undeclared read - a scoped producer reads no namespace outside its own and its `REQUIRES`
   closure's declared ones, present or absent.

Their reach: every declared cell whose producer's parser is installed - all of them on the Linux
gate - each cell asserted non-empty by the reference build, or absent beside its
`grammar-unavailable` rows where an optional grammar is missing.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph import snapshot as SN  # noqa: E402
from tools.graph.core.model import ExtractResult  # noqa: E402
from tools.graph.core.spi import ExtractContext  # noqa: E402
from tools.graph.tests._estate import compose, lay_down, spawn, svc  # noqa: E402

HERE = Path(__file__).resolve().parent

PROPS = "reporting.host=http://reports:8080\nsearch.list.path=/reports/v1/list\n"
PATH_CFG = """\
@Getter
public class PathCfg {
    @Value("${search.list.path}")
    private String searchListPath;
}
"""
CLIENT = """\
public class Client {
    @Value("${reporting.host}")
    private String reportingHost;
    private PathCfg pathCfg;
    private RestTemplate restTemplate;
    public String fetch() {
        return restTemplate.getForObject(reportingHost + pathCfg.getSearchListPath(), String.class);
    }
}
"""
APP = 'import os\n\nMODE = os.environ.get("MODE", "fast")\nTOPIC = os.environ["TOPIC"]\n'
CONFIGMAP = """apiVersion: v1
kind: ConfigMap
metadata:
  name: hosts
data:
  ORDERS_API_HOST: http://orders-api:8080
"""


def service() -> dict[str, str]:
    """A component every component-scoped producer writes a non-empty entry for: properties,
    a Java call its host and path meet only in the code's concatenation, an env read with a
    default."""
    return {"src/main/resources/application.properties": PROPS,
            "src/main/java/x/PathCfg.java": PATH_CFG,
            "src/main/java/x/Client.java": CLIENT,
            "app.py": APP}


ESTATE = {"x": service(), "y": service(),
          "d": {"docker-compose.yml": compose(svc("x", "../x", "x-topic"),
                                              svc("y", "../y", "y-topic")),
                "templates/cm.yaml": CONFIGMAP}}


class PoisonRead(Exception):
    """A read of what a guard poisoned."""


class Poison:
    """Raises on any access but identity: what a guard puts where nothing may be read."""

    __slots__ = ("what",)

    def __init__(self, what: str):
        object.__setattr__(self, "what", what)

    def _refuse(self, *args, **kwargs):
        raise PoisonRead(object.__getattribute__(self, "what"))

    __getattr__ = __getitem__ = __setitem__ = __delitem__ = __iter__ = __len__ = _refuse
    __contains__ = __bool__ = __eq__ = __ne__ = __hash__ = __call__ = __str__ = _refuse
    __lt__ = __le__ = __gt__ = __ge__ = __format__ = _refuse

    def __repr__(self) -> str:
        return f"<poison {object.__getattribute__(self, 'what')}>"


def holds_something(entry) -> bool:
    """Whether an entry holds a value at any depth - `{"values": {}}` holds none."""
    if isinstance(entry, dict):
        return any(holds_something(v) for v in entry.values())
    if isinstance(entry, (list, tuple, set, frozenset)):
        return any(holds_something(v) for v in entry)
    return entry is not None


def facts(res: ExtractResult) -> tuple:
    return res.nodes, res.edges, res.unresolved


class World:
    """The estate laid down, activated with the real registry and built once with the shared
    cache recorded (`run_extractors_owned(cache=...)`), as a full build fills it."""

    def __init__(self, where: Path, estate: dict = ESTATE, extra: tuple = ()):
        self.toml = lay_down(where, estate)
        self.regs = B.order_extractors(B.discover_extractors() + list(extra))
        self.activate()
        self.ref: dict = {}
        owned, failures, violations = B.run_extractors_owned(
            self.regs, self.act, self.components, self.root, "guard", self.config,
            cache=self.ref)
        assert (failures, violations) == ({}, []), (failures, violations)
        self.owned = owned
        self.closure = B.requires_closure(self.regs)
        self.namespaces = {ns for reg in self.regs for ns in reg.cache_keys}

    def activate(self) -> None:
        si = B.load_installer()
        ws = B.resolve_workspace(self.toml)
        self.root = ws["root"]
        self.components = si.discover_components(ws)["components"]
        self.act = B.activate(self.regs, self.components, self.root,
                              root_exclusions=B.installer_root_exclusions(si),
                              all_regs=self.regs, workspace=self.components)
        config = {"workspace_name": ws.get("name", ""), "canonical": [], "generated": [],
                  "entrypoint": [], "alias": [], "apart": [], "components": self.components,
                  "profile": ""}
        self.config = {**config, "deployment_off_case": B.deployment_scope(
            self.act, self.components, self.root, self.regs)["deployment_off_case"]}

    def reg(self, name: str):
        return next(r for r in self.regs if r.name == name)

    def scoped(self) -> list:
        return [r for r in self.regs if r.cache_scope == "component"]

    def required(self, reg) -> set[str]:
        """The namespaces its `REQUIRES` closure declares - a control's from its own
        `REQUIRES` and theirs."""
        deps = self.closure.get(reg.name)
        if deps is None:
            deps = set(reg.requires).union(*(self.closure.get(r, ()) for r in reg.requires))
        return {ns for r in self.regs if r.name in deps for ns in r.cache_keys}

    def run(self, reg, comp: str, cache: dict) -> ExtractResult:
        ctx = ExtractContext(repos=B.repo_refs(self.components, self.root, [comp]),
                             run_id="guard", config=self.config, cache=cache,
                             files={comp: self.act.files.get((reg.name, comp), [])})
        return reg.extractor.extract(ctx)


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return World(tmp_path_factory.mktemp("guards"))


def without(cache: dict, keys) -> dict:
    copied = copy.deepcopy(cache)
    for ns in keys:
        copied.pop(ns, None)
    return copied


# ── the guards, each a function a positive control runs too ───────────────────────────────


def guard_writes(world: World, reg, comp: str) -> None:
    """Guard 1: the WHOLE cache deep-snapshotted before and after the run (D22-45)."""
    before = without(world.ref, reg.cache_keys)
    cache = copy.deepcopy(before)
    world.run(reg, comp, cache)
    assert ({k: v for k, v in cache.items() if k not in reg.cache_keys}
            == {k: v for k, v in before.items() if k not in reg.cache_keys}), (
        f"{reg.name} wrote outside its CACHE_KEYS {reg.cache_keys}")
    if reg.cache_scope == "component":
        for ns in reg.cache_keys:
            assert set(cache.get(ns, {})) <= {comp}, (
                f"{reg.name} wrote another component's entry under {ns}")


def guard_reads(world: World, consumer, producer, comp: str) -> None:
    """Guard 2: the producer's entries for other components raise on any access."""
    clean = world.run(consumer, comp, copy.deepcopy(world.ref))
    cache = copy.deepcopy(world.ref)
    for ns in producer.cache_keys:
        cache[ns] = {k: (v if k == comp else Poison(f"{ns}[{k}]"))
                     for k, v in cache.get(ns, {}).items()}
    try:
        poisoned = world.run(consumer, comp, cache)
    except PoisonRead as read:
        raise AssertionError(f"{consumer.name}, run for {comp}, read {read}") from None
    assert facts(poisoned) == facts(clean), f"{consumer.name} read another component's entry"


def guard_closure(regs) -> None:
    """Guard 3: every shared member of a scoped producer's closure stays in each consumer's
    cross-component closure."""
    full, cross = B.requires_closure(regs), B.cross_closure(regs)
    scoped = {r.name for r in regs if r.cache_scope == "component"}
    for producer in sorted(scoped):
        shared = full[producer] - scoped
        for reg in regs:
            if producer in full[reg.name]:
                assert shared <= cross[reg.name], (reg.name, producer, shared - cross[reg.name])


def entry_of(world: World, reg, comp: str) -> dict:
    """Guard 4's run: its own namespaces absent, its REQUIRES producers' entries replayed from
    the reference build, every other declared namespace poisoned (D22-66)."""
    required = world.required(reg)
    cache: dict = {ns: copy.deepcopy(world.ref[ns]) for ns in required if ns in world.ref}
    for ns in world.namespaces - required - set(reg.cache_keys):
        cache[ns] = Poison(ns)
    world.run(reg, comp, cache)
    return {ns: copy.deepcopy(cache.get(ns, {}).get(comp)) for ns in reg.cache_keys}


def guard_declaration(world: World, reg, comp: str, other: str) -> None:
    """Guard 4: files a registered reader matches in ANOTHER component edited, added and removed
    between two runs - a listing is a dependence too (D22-61) - and the entry compared by `==`:
    its keys and values, `properties`' tuple keys and a set as they are (D22-75)."""
    first = entry_of(world, reg, comp)
    base = world.root / next(c["path"] for c in world.components if c["name"] == other)
    props = base / "src/main/resources/application.properties"
    props.write_text(props.read_text(encoding="utf-8") + "extra.key=elsewhere\n",
                     encoding="utf-8")
    (base / "src/main/java/x/Extra.java").write_text("public class Extra {}\n", encoding="utf-8")
    (base / "app.py").unlink()
    try:
        world.activate()
        second = entry_of(world, reg, comp)
    finally:
        props.write_text(PROPS, encoding="utf-8")
        (base / "src/main/java/x/Extra.java").unlink()
        (base / "app.py").write_text(APP, encoding="utf-8")
        world.activate()
    assert first == second, f"{reg.name}'s entry for {comp} moved with {other}'s files"


def guard_undeclared(world: World, reg, comp: str) -> None:
    """Guard 5: every declared namespace outside its own and its REQUIRES closure's poisoned,
    present or absent - so a read of one no run wrote fails, never meets an empty cache
    (D22-56)."""
    base = without(world.ref, reg.cache_keys)
    clean = world.run(reg, comp, copy.deepcopy(base))
    cache = copy.deepcopy(base)
    for ns in world.namespaces - set(reg.cache_keys) - world.required(reg):
        cache[ns] = Poison(ns)
    try:
        poisoned = world.run(reg, comp, cache)
    except PoisonRead as read:
        raise AssertionError(f"{reg.name}, run for {comp}, read {read}, which it did not "
                             f"declare") from None
    assert facts(poisoned) == facts(clean)


# ── the declarations ───────────────────────────────────────────────────────────────────────


def test_the_declared_scopes_and_keys():
    regs = {r.name: r for r in B.discover_extractors()}
    assert {n for n, r in regs.items() if r.cache_scope == "component"} == {
        "properties", "spring-call-concats", "env-keys"}
    assert {n: r.cache_keys for n, r in regs.items() if r.cache_keys} == {
        "properties": ("properties",), "spring-call-concats": ("spring-call-concats.paired",),
        "env-keys": ("envkeys",), "compose-services": ("composeenv", "composeenv.origin"),
        "helm-configmaps": ("hostmap",)}


def reader(**attrs):
    body = {"name": "b22-reader", "INPUT_GLOBS": ("*.b22",),
            "extract": lambda self, ctx: ExtractResult(), **attrs}
    return type("Reader", (), body)()


def registered(obj):
    return B._cache_declarations(obj, obj.name, "pkg")


@pytest.mark.parametrize("attrs, refusal", [
    ({"CACHE_SCOPE": "repo", "CACHE_KEYS": ("x",)}, "`CACHE_SCOPE` on 'b22-reader' is 'repo'"),
    ({"CACHE_SCOPE": "component"}, "declares `CACHE_SCOPE = \"component\"` but no `CACHE_KEYS`"),
    ({"CACHE_KEYS": "x"}, "`CACHE_KEYS` on 'b22-reader' must be a TUPLE of namespace names"),
    ({"CACHE_KEYS": ("x", "")}, "`CACHE_KEYS` on 'b22-reader' must be a TUPLE of namespace names"),
], ids=["scope-not-component", "scoped-without-keys", "keys-a-string", "keys-an-empty-name"])
def test_a_wrong_cache_declaration_is_refused_at_registration(attrs, refusal):
    with pytest.raises(B.BuildError) as refused:
        registered(reader(**attrs))
    assert refusal in str(refused.value)


def test_a_right_cache_declaration_registers():
    assert registered(reader(CACHE_SCOPE="component", CACHE_KEYS=("x",))) == (
        "component", ("x",))
    assert registered(reader()) == ("", ())


# ── the cross-component closure (guard 3) ─────────────────────────────────────────────────


def test_the_cross_closure_drops_component_scoped_members_only():
    regs = B.discover_extractors()
    cross = B.cross_closure(regs)
    assert cross["spring-rest-calls"] == frozenset(), "properties, spring-call-concats: scoped"
    assert cross["kafka-consumers"] == frozenset()
    assert cross["http-calls"] == frozenset({"compose-services"}), "env-keys is scoped"
    guard_closure(regs)


def test_a_reader_requiring_a_scoped_producer_alone_keeps_its_shared_members():
    """Guard 3's positive control (round 1, measured): the transitive closure, then filtered -
    a walk of shared edges alone would drop `compose-services`, false-current."""
    x = B.Registration(name="b22-x", layer="stack", globs=("*.x",), extractor=None,
                       package=HERE, requires=("env-keys",))
    regs = B.discover_extractors() + [x]
    assert B.cross_closure(regs)["b22-x"] == frozenset({"compose-services"})
    guard_closure(regs)


def test_guard_3_catches_a_closure_that_drops_a_shared_member(monkeypatch):
    """The guard's own failing branch: a cross closure walking shared edges alone."""
    x = B.Registration(name="b22-x", layer="stack", globs=("*.x",), extractor=None,
                       package=HERE, requires=("env-keys",))
    regs = B.discover_extractors() + [x]
    full = B.requires_closure(regs)
    monkeypatch.setattr(B, "cross_closure", lambda regs: {
        r.name: frozenset(d for d in r.requires if d not in ("env-keys",))
        for r in regs if r.name in full})
    with pytest.raises(AssertionError):
        guard_closure(regs)


SCOPED_PROFILE = '''\
from tools.graph.core.model import ExtractResult


class Producer:
    name = "b22-scoped"
    INPUT_GLOBS = ("*.b22.yml",)
    PARSERS = ("yaml",)
    CACHE_SCOPE = "component"
    CACHE_KEYS = ("b22-ns",)

    def extract(self, ctx):
        for repo in ctx.repos:
            ctx.cache.setdefault("b22-ns", {})[repo.alias] = {"files": len(ctx.inputs(repo))}
        return ExtractResult()


class Consumer:
    name = "b22-consumer"
    INPUT_GLOBS = ("*.b22",)
    PARSERS = ()
    REQUIRES = ("b22-scoped",)

    def extract(self, ctx):
        return ExtractResult()


EXTRACTORS = (Producer(), Consumer())
'''


def test_the_publish_stamps_the_closure_the_verdict_judges(tmp_path):
    """One producer, one closure: `b`'s consumer requires a component-scoped producer whose
    YAML is read in `a` alone, so `b`'s parser stamp holds no closure half - the publish's
    stamp and the verdict's both by `cross_closure`, and `b` current straight after it."""
    toml = lay_down(tmp_path, {"a": {"x.b22.yml": "k: v\n"}, "b": {"y.b22": "z\n"}})
    toml.write_text(toml.read_text(encoding="utf-8").replace('profile = ""',
                                                             'profile = "./prof"'),
                    encoding="utf-8")
    (toml.parent / "prof").mkdir()
    (toml.parent / "prof" / "__init__.py").write_text(SCOPED_PROFILE, encoding="utf-8")
    published = spawn(tmp_path, "--publish-snapshot")
    assert published.returncode == 0, published.stdout + published.stderr
    stamped = json.loads((tmp_path / "ws" / "b" / SN.SHARD_DIR / SN.MANIFEST)
                         .read_text(encoding="utf-8"))["parsers"]
    assert stamped == {}, "b reads nothing of a's through a component-scoped producer"
    proc = spawn(tmp_path, "--check-fresh", out="check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_entry_holds_something_only_where_a_value_is():
    assert not holds_something({"values": {}, "unresolved": {}})
    assert not holds_something(set()) and not holds_something(None)
    assert holds_something({"values": {"mode": {"value": "fast"}}})
    assert holds_something({"paired": {"reporting.host"}})


# ── the reference build ────────────────────────────────────────────────────────────────────


def test_the_reference_build_feeds_every_declared_cell(world):
    """An empty entry compares empty against empty, as vacuous as none (D22-67, D22-76): every
    declared (namespace, component) cell a guard poisons or compares holds something - or,
    where a scoped producer's parser is missing here, is absent beside its rows (D22-83)."""
    probe = B.parser_probe(world.regs)
    cells = 0
    for reg in world.scoped():
        comps = world.act.by_extractor.get(reg.name) or []
        assert len(comps) >= 2, f"{reg.name} must be active in two components: {comps}"
        for comp in comps:
            missing = [p for p in reg.parsers if not probe(p)]
            for ns in reg.cache_keys:
                if missing:   # branch and assert, never skip
                    assert not holds_something(world.ref.get(ns, {}).get(comp))
                    assert any(row.get("kind") == "grammar-unavailable"
                               for row in world.owned[reg.name][comp].unresolved)
                else:
                    assert holds_something(world.ref[ns][comp]), (reg.name, ns, comp)
                    cells += 1
    assert cells >= 4, "the guards would compare nothing"
    for reg in world.regs:
        if reg.cache_keys and reg.cache_scope != "component":
            assert any(holds_something(world.ref.get(ns)) for ns in reg.cache_keys), reg.name


# ── guard 1: writes ────────────────────────────────────────────────────────────────────────


def test_guard_1_no_reader_writes_outside_its_declared_namespaces(world):
    """Every registered reader, run for each component it is active in - one declaring no
    `CACHE_KEYS` writes nothing (D22-71)."""
    for reg in world.regs:
        for comp in world.act.by_extractor.get(reg.name) or []:
            guard_writes(world, reg, comp)


class WritesAnother:
    name = "b22-writes-another"
    CACHE_SCOPE, CACHE_KEYS = "component", ("b22-ns",)

    def extract(self, ctx):
        ctx.cache.setdefault("b22-ns", {})["someone-else"] = {"x": 1}
        return ExtractResult()


class WritesOutside:
    name = "b22-writes-outside"
    CACHE_SCOPE, CACHE_KEYS = "component", ("b22-ns",)

    def extract(self, ctx):
        ctx.cache.setdefault("b22-undeclared", {})[ctx.repos[0].alias] = {"x": 1}
        return ExtractResult()


class MutatesForeign:
    name = "b22-mutates-foreign"
    CACHE_SCOPE, CACHE_KEYS = "component", ("b22-ns",)

    def extract(self, ctx):
        entries = ctx.cache.get("properties") or {}
        for entry in entries.values():
            if isinstance(entry, dict):
                entry["b22"] = "mutated in place"
        return ExtractResult()


def control(obj, requires: tuple = ()):
    return B.Registration(name=obj.name, layer="stack", globs=("*.b22",), extractor=obj,
                          package=HERE, requires=requires,
                          cache_scope=getattr(obj, "CACHE_SCOPE", ""),
                          cache_keys=getattr(obj, "CACHE_KEYS", ()))


@pytest.mark.parametrize("obj", [WritesAnother(), WritesOutside(), MutatesForeign()],
                         ids=["another-components-entry", "an-undeclared-namespace",
                              "a-foreign-entry-in-place"])
def test_guard_1_catches_each_wrong_write(world, obj):
    with pytest.raises(AssertionError):
        guard_writes(world, control(obj), "x")


# ── guard 2: reads ─────────────────────────────────────────────────────────────────────────


def test_guard_2_no_consumer_reads_another_components_entry(world):
    pairs = 0
    for producer in world.scoped():
        for consumer in world.regs:
            if producer.name in world.closure[consumer.name]:
                for comp in world.act.by_extractor.get(consumer.name) or []:
                    guard_reads(world, consumer, producer, comp)
                    pairs += 1
    assert pairs, "no consumer of a component-scoped producer was run"


class ReadsAnother:
    name = "b22-reads-another"

    def extract(self, ctx):
        for alias, entry in (ctx.cache.get("properties") or {}).items():
            if alias != ctx.repos[0].alias:
                bool(entry)
        return ExtractResult()


def test_guard_2_catches_a_consumer_reading_another_components_entry(world):
    with pytest.raises(AssertionError):
        guard_reads(world, control(ReadsAnother(), ("properties",)), world.reg("properties"), "x")


# ── guard 4: the declaration itself ────────────────────────────────────────────────────────


def test_guard_4_a_scoped_entry_does_not_move_with_another_components_files(world):
    for reg in world.scoped():
        guard_declaration(world, reg, "x", "y")


class ReadsAnotherFile:
    name = "b22-reads-another-file"
    CACHE_SCOPE, CACHE_KEYS = "component", ("b22-ns",)

    def extract(self, ctx):
        alias = ctx.repos[0].alias
        root = ctx.repos[0].path.parent
        seen = sorted(p.relative_to(root).as_posix() for comp in ctx.config["components"]
                      if comp["name"] != alias for p in (root / comp["path"]).rglob("*")
                      if p.is_file())
        ctx.cache.setdefault("b22-ns", {})[alias] = {"seen": seen}
        return ExtractResult()


def test_guard_4_catches_a_producer_reading_another_components_files(world):
    with pytest.raises(AssertionError):
        guard_declaration(world, control(ReadsAnotherFile()), "x", "y")


def test_guard_4_compares_tuple_keys_and_sets_as_they_are():
    """D22-75: `==` over the deep copies - `json.dumps` raises on both shapes."""
    one = {("src/main/resources", "application"): {"k": "v"}, "paired": {"a", "b"}}
    assert copy.deepcopy(one) == one
    assert copy.deepcopy(one) != {**one, "paired": {"a"}}
    with pytest.raises(TypeError):
        json.dumps(one)


# ── guard 5: an undeclared read ────────────────────────────────────────────────────────────


def test_guard_5_no_scoped_producer_reads_an_undeclared_namespace(world):
    for reg in world.scoped():
        for comp in world.act.by_extractor.get(reg.name) or []:
            guard_undeclared(world, reg, comp)


class ReadsUndeclared:
    name = "b22-reads-undeclared"
    CACHE_SCOPE, CACHE_KEYS = "component", ("b22-ns",)

    def extract(self, ctx):
        hosts = ctx.cache.get("hostmap")
        if hosts is not None:
            len(hosts)
        ctx.cache.setdefault("b22-ns", {})[ctx.repos[0].alias] = {"x": 1}
        return ExtractResult()


def test_guard_5_catches_a_read_of_a_namespace_it_did_not_declare(world):
    with pytest.raises(AssertionError):
        guard_undeclared(world, control(ReadsUndeclared()), "x")


# ── T633's cell, through the doors ─────────────────────────────────────────────────────────


def test_a_java_edit_in_one_component_leaves_the_other_current(tmp_path):
    """T633 (measured on `main`: `y` STALE, "depends on spring-call-concats, whose inputs
    changed in another component"): `spring-call-concats` is component-scoped, so `x`'s Java
    edit is no input `y` read - `y` current, and its re-published bytes identical."""
    lay_down(tmp_path, ESTATE)
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0
    shard = tmp_path / "ws" / "y" / SN.SHARD_DIR
    before = {p.name: p.read_bytes() for p in shard.iterdir() if p.is_file()}
    client = tmp_path / "ws" / "x" / "src/main/java/x/Client.java"
    client.write_text(client.read_text(encoding="utf-8") + "// edited\n", encoding="utf-8")
    proc = spawn(tmp_path, "--check-fresh", out="check")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    stale = [line for line in proc.stdout.splitlines() if line.startswith("freshness: STALE")]
    assert [line.split(" - ")[0] for line in stale] == ["freshness: STALE x"], proc.stdout
    assert spawn(tmp_path, "--publish-snapshot").returncode == 0
    assert {p.name: p.read_bytes() for p in shard.iterdir() if p.is_file()} == before


def test_the_cache_is_one_fresh_dict_per_call_unless_handed_one(tmp_path):
    """D22-14, D22-61: never a shared mutable default, which would carry one build's cache into
    the next in a long-lived process such as the MCP server."""
    import inspect
    assert inspect.signature(B.run_extractors_owned).parameters["cache"].default is None
    world = World(tmp_path)
    again: dict = {}
    B.run_extractors_owned(world.regs, world.act, world.components, world.root, "guard",
                           world.config, cache=again)
    assert again == world.ref and again is not world.ref
