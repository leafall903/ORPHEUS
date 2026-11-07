"""Native PlayScript resource selection, before world routing/loading.

104319 RVAs: 906DC4, D05510, D055D0 and D058D0. The inline graph
configuration constructs a fresh identifier map; it is consumed here without
extending the VM's legacy general-purpose Map mutation implementation.
"""

from dataclasses import dataclass

from ow174.game.script import expr
from ow174.game.script.graph import GRAPH_TYPE

GRAPH_KEY = 0x4065
IDENTIFIER_TYPE = 0x0D80000000000000
_FAILED = object()
MAX_CONFIG_DEPTH = 64


@dataclass(frozen=True)
class Resource:
    guid: int
    overrides: tuple = ()  # evaluated (identifier GUID, value), native bucket order


def evaluate_value(instance, cfg, *, initial=0):
    """The value-producing +28 path used by request/schema initializers.

    06610EBC's +28 (1B779E0) collects its config iterator into an array; its
    different +20 first-item behavior is deliberately not added to expr.evaluate.
    """
    with instance.component.operation():
        if isinstance(cfg, dict) and cfg.get("$") == "STU_06610EBC":
            return tuple(_iter_values(instance, cfg))
        return instance.evaluate(cfg, initial=initial)


def _iter_values(instance, cfg, *, _depth=0):
    """1B803D0/1B7F700: ordered config groups and recursive value arrays.

    A missing/failed config or a scalar null yields no item. Null members of an
    existing array are retained. Unknown native iterator configs remain strict
    world queries; no alternate native iterator interface is invented here.
    """
    instance.component.consume_work("played graph config iteration")
    if _depth >= MAX_CONFIG_DEPTH:
        instance.component.budget_exceeded("played graph config iterator depth exhausted")
    if cfg is None:
        return
    if isinstance(cfg, dict) and cfg.get("$") == "STU_06610EBC":
        for child in cfg.get("m_configVars") or ():
            yield from _iter_values(instance, child, _depth=_depth + 1)
        return
    value = instance.evaluate(cfg, initial=_FAILED)
    if value is _FAILED or value is None:
        return

    def flatten(item, depth):
        instance.component.consume_work("played graph value array iteration")
        if depth >= MAX_CONFIG_DEPTH:
            instance.component.budget_exceeded("played graph value array depth exhausted")
        if isinstance(item, tuple):
            for child in item:
                yield from flatten(child, depth + 1)
        else:
            yield item

    yield from flatten(value, _depth)


def _asset(value) -> int:
    """F65540: only an Asset, or recursively the first array element."""
    while isinstance(value, tuple) and value:
        value = value[0]
    return value.guid if isinstance(value, expr.Asset) else 0


def _insert(buckets, key, value, work):
    """F66E90/F683E0/F68480 for nonnegative identifier keys.

    Zero marks an empty bucket. Growth reinserts in old physical bucket order,
    not declaration order. The signed negative-key memory path is not modeled.
    """
    if not buckets:
        buckets = [None] * 8
    for offset in range(len(buckets)):
        work("played graph map insertion")
        position = (key + offset) % len(buckets)
        if buckets[position] is None or buckets[position][0] == key:
            buckets[position] = (key, value) if key else None
            return buckets
    grown = [None] * (len(buckets) * 2)
    for old_key, old_value in buckets:
        grown = _insert(grown, old_key, old_value, work)
    return _insert(grown, key, value, work)


def _key(instance, node, key) -> int:
    if isinstance(key, expr.Asset):
        key = key.guid
    if type(key) is not int:
        instance.component.unsupported(instance, "played graph map key", str(type(key)), node)
    key &= 0xFFFFFFFF
    if key & 0x80000000:
        instance.component.unsupported(instance, "played graph negative map key", node.cls, node)
    return key


def _inline(instance, node, cfg):
    definition = cfg.get("m_6525E98B") or {}
    guid = expr.guid_of(definition.get("m_graph"))
    if not guid:
        return 0
    work = instance.component.consume_work
    buckets = _insert([], GRAPH_KEY, expr.Asset(guid), work)
    for row in definition.get("m_1EB5A024") or ():
        instance.component.consume_work("played graph override evaluation")
        if not isinstance(row, dict):
            instance.component.unsupported(instance, "played graph override row", node.cls, node)
        config = row.get("m_value")
        value = evaluate_value(instance, config) if config is not None else 0
        key = _key(instance, node, expr.guid_of(row.get("m_0D09D2D9")))
        buckets = _insert(buckets, key, value, work)
    return expr.Map(tuple(row for row in buckets if row is not None))


def _mapped(instance, node, rows):
    entries = []
    keys = set()
    guid = 0
    for key, value in rows:
        instance.component.consume_work("played graph map selection")
        key = _key(instance, node, key)
        if not key:
            continue
        if key in keys:
            # Legacy VM maps may contain duplicate low keys, unlike a native map.
            instance.component.unsupported(instance, "played graph duplicate map key", node.cls, node)
        keys.add(key)
        if key == GRAPH_KEY:
            guid = _asset(value)
        else:
            entries.append((IDENTIFIER_TYPE | key, value))
    return Resource(guid, tuple(entries)) if guid else None


def _evaluate(instance, node, cfg, *, collecting=True, depth=0):
    instance.component.consume_work("played graph resource evaluation")
    if depth >= MAX_CONFIG_DEPTH:
        instance.component.budget_exceeded("played graph resource config depth exhausted")
    if isinstance(cfg, dict) and cfg.get("$") == "STU_9DB46B98":
        # D058D0 writes nothing and returns false for an empty inline graph.
        return _inline(instance, node, cfg)
    if (isinstance(cfg, dict) and cfg.get("$") == "STU_105E1BCC"
            and cfg.get("m_resourceKey") is not None):
        instance.component.unsupported(instance, "played graph keyed resource", cfg["$"], node)
    if collecting:
        return evaluate_value(instance, cfg)
    if isinstance(cfg, dict) and cfg.get("$") == "STU_06610EBC":
        # 1B77980's +20 delegates only to the first config, including its
        # failure. It does not collect/skip forward to a later config.
        configs = cfg.get("m_configVars") or ()
        if not configs or configs[0] is None:
            return 0
        return _evaluate(instance, node, configs[0], collecting=False, depth=depth + 1)
    return instance.evaluate(cfg, initial=0)


def _select(instance, node, cfg):
    if cfg is None:
        return None
    value = _evaluate(instance, node, cfg)
    if isinstance(value, expr.Map):
        selected = _mapped(instance, node, value.items)
        if selected is not None:
            # The map branch accepts any nonzero asset; only the ordinary
            # resource branch below performs the native .01B type check.
            return selected
    # D05510 evaluates again through 8F6A90 when no usable map graph was found.
    guid = _asset(_evaluate(instance, node, cfg, collecting=False))
    return Resource(guid) if guid and guid >> 48 == GRAPH_TYPE else None


def resolve_resource(instance, node) -> Resource | None:
    """Try graph-with-overrides first, then m_script; no entity or loader effects.

    Values are evaluated in the caller without a state watcher. Unknown configs
    still fail through the world's strict evaluator. Native instance context,
    target iteration, resource readiness and transport flags belong to the
    world's play_resolved_script service.
    """
    with instance.component.operation():
        return (_select(instance, node, node.field("m_69F4B52C"))
                or _select(instance, node, node.field("m_script")))
