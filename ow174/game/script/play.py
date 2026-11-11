"""Native PlayScript resource selection, before world routing/loading.

104319 RVAs: 906DC4, D05510, D055D0 and D058D0. The inline graph
configuration constructs a fresh identifier map; it is consumed here without
extending the VM's legacy general-purpose Map mutation implementation.
"""

from dataclasses import dataclass

from ow174.game.script import controller_heroes, expr
from ow174.game.script.graph import GRAPH_TYPE
from ow174.game.script.graph import guid as parse_guid

GRAPH_KEY = 0x4065
IDENTIFIER_TYPE = 0x0D80000000000000
_FAILED = object()
MAX_CONFIG_DEPTH = 64
MODE_SCORE_THRESHOLDS = "STU_0C37A184"
SESSION_ENTITIES = "STU_BD2D717B"
AUTHORED_ARRAY = "STU_45B0D2B2"
AUTHORED_PAIRS = "STU_7757D403"


@dataclass(frozen=True)
class Resource:
    guid: int
    overrides: tuple = ()  # evaluated (identifier GUID, value), native bucket order


@dataclass(frozen=True)
class InvocationContext:
    """Explicit native context inputs; caller (+98) and instigator (+9C) differ."""

    caller: int
    owner: int
    instigator: int


@dataclass(frozen=True)
class Request:
    resource: Resource
    targets: tuple[int, ...]
    context: InvocationContext
    params: tuple
    policy: int
    wire_flag: int


def evaluate_value(instance, cfg, *, initial=0, watcher=None):
    """The +28 value interface for assignments and request/schema initializers.

    Config groups and controller-hero queries collect all iterator items here;
    their scalar interface is selected separately by evaluate_scalar/evaluate.
    """
    with instance.component.operation():
        if isinstance(cfg, dict) and cfg.get("$") == "STU_06610EBC":
            return tuple(_iter_values(instance, cfg, watcher=watcher))
        if isinstance(cfg, dict) and cfg.get("$") == MODE_SCORE_THRESHOLDS:
            return mode_score_thresholds(instance, cfg, watcher=watcher)
        if isinstance(cfg, dict) and cfg.get("$") == SESSION_ENTITIES:
            return session_entities(instance, cfg, watcher=watcher)
        if isinstance(cfg, dict) and cfg.get("$") == AUTHORED_ARRAY:
            values = []
            for child in cfg.get('m_configVars') or ():
                instance.component.consume_work('authored array item')
                values.append(evaluate_value(instance, child, watcher=watcher) if child is not None else 0)
            return tuple(values)
        if isinstance(cfg, dict) and cfg.get("$") == AUTHORED_PAIRS:
            return _authored_pairs(instance, cfg, watcher=watcher)
        result = controller_heroes.collect(instance.context(watcher), cfg)
        if result is not NotImplemented:
            return result
        return instance.evaluate(cfg, watcher, initial=initial)


def _authored_pairs(instance, cfg, *, watcher):
    """104319 1B78280/1B77090 collect each value, then its typed key, in authored order."""
    pairs = []
    for row in cfg.get("m_configVars") or ():
        instance.component.consume_work("authored pair")
        if not isinstance(row, dict) or row.get("$") != "STU_98DD1872":
            instance.component.unsupported(instance, "authored pair row", AUTHORED_PAIRS)
        value = evaluate_scalar(instance, row.get("m_value"), initial=None, watcher=watcher)
        key = evaluate_scalar(instance, row.get("m_key"), initial=None, watcher=watcher)
        if not isinstance(key, expr.Asset):
            instance.component.unsupported(instance, "authored resource pair key", AUTHORED_PAIRS)
        pairs.append((key, value))
    return expr.PairArray(tuple(pairs))


def array_item(instance, cfg, *, watcher=None):
    """104319 1B79A60: scalar index, collecting +28 container, array-only lookup.

    Failed/missing index evaluation selects zero. Missing/non-array containers
    and out-of-range indices successfully write null, rather than preserving the
    caller's destination. Unlike expression MEMBER_BY_INDEX, scalars never match.
    """
    index = expr.to_int(evaluate_scalar(instance, cfg.get("m_index"), initial=0, watcher=watcher))
    values = evaluate_value(instance, cfg.get("m_814F8EC3"), initial=None, watcher=watcher)
    return values[index] if isinstance(values, tuple) and 0 <= index < len(values) else None


def _iter_values(instance, cfg, *, _depth=0, watcher=None):
    """1B803D0/1B7F700: ordered config groups and recursive value arrays.

    A missing/failed config or a scalar null yields no item. Null members of an
    existing array are retained. Recognized native iterators retain their own
    traversal; unknown native iterator configs remain strict world queries.
    """
    iterator = _iter_pairs(instance, cfg, _depth=_depth, watcher=watcher)
    try:
        for _, value in iterator:
            yield value
    finally:
        iterator.close()


def fold_array(instance, cfg, *, watcher=None):
    """1B77D80 and 1B76A60: fold a materialized iterator in its parameter scope."""
    component = instance.component
    values = tuple(_iter_values(instance, cfg.get("m_814F8EC3"), watcher=watcher))
    params = {12: 0, 1050: len(values), 1293: None}
    with component.parameter_scope(params):
        if values:
            params[1294] = values[0]
            params[1293] = evaluate_scalar(instance, cfg.get("m_418D9D9D"), watcher=watcher)
        for index, item in enumerate(values):
            component.consume_work("array fold item")
            params[12], params[1294] = index, item
            params[1293] = evaluate_scalar(
                instance, cfg.get("m_C239F989"), initial=params[1293], watcher=watcher,
            )
        params[12] = len(values)
        params.pop(1294, None)
        result = cfg.get("m_8054375D")
        if result is not None:
            return evaluate_scalar(instance, result, watcher=watcher)
    return params[1293]


def _iter_pairs(instance, cfg, *, _depth=0, watcher=None):
    """The supported generic iterators' key/value rows, before value projection."""
    instance.component.consume_work("played graph config iteration")
    if _depth >= MAX_CONFIG_DEPTH:
        instance.component.budget_exceeded("played graph config iterator depth exhausted")
    if cfg is None:
        return
    if isinstance(cfg, dict) and cfg.get("$") == "STU_06610EBC":
        for child in cfg.get("m_configVars") or ():
            yield from _iter_pairs(instance, child, _depth=_depth + 1, watcher=watcher)
        return
    if isinstance(cfg, dict) and cfg.get("$") == MODE_SCORE_THRESHOLDS:
        for value in mode_score_thresholds(instance, cfg, watcher=watcher):
            yield 0, value
        return
    if isinstance(cfg, dict) and cfg.get("$") == SESSION_ENTITIES:
        for value in session_entities(instance, cfg, watcher=watcher):
            yield 0, value
        return
    iterator = controller_heroes.values(instance.context(watcher), cfg)
    if iterator is not NotImplemented:
        try:
            for value in iterator:
                yield 0, value
        finally:
            iterator.close()
        return
    value = instance.evaluate(cfg, watcher, initial=_FAILED)
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
            yield 0, item

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
        cls = node.cls if node is not None else "STU_7042F028"
        instance.component.unsupported(instance, "played graph negative map key", cls, node)
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


def authored_map(instance, cfg, *, watcher=None):
    """104319 1B78C00: create a map from authored identifier/value pairs."""
    component = instance.component
    with component.operation():
        buckets = []
        for row in cfg.get('m_92829780') or ():
            component.consume_work('authored map row')
            if not isinstance(row, dict):
                component.unsupported(instance, 'authored map row', cfg['$'])
            child = row.get('m_value')
            value = evaluate_value(instance, child, watcher=watcher) if child is not None else 0
            key = _key(instance, None, expr.guid_of(row.get('m_identifier')))
            buckets = _insert(buckets, key, value, component.consume_work)
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


def _scalar(instance, cfg, *, initial, depth=0, watcher=None):
    """The +20 path, including 06610EBC's first config and no-output failure."""
    instance.component.consume_work("played graph scalar evaluation")
    if depth >= MAX_CONFIG_DEPTH:
        instance.component.budget_exceeded("played graph scalar config depth exhausted")
    if cfg is None:
        return initial
    if isinstance(cfg, dict) and cfg.get("$") == "STU_06610EBC":
        configs = cfg.get("m_configVars") or ()
        return (_scalar(instance, configs[0], initial=initial, depth=depth + 1, watcher=watcher)
                if configs else initial)
    return instance.evaluate(cfg, watcher, initial=initial)


def evaluate_scalar(instance, cfg, *, initial=None, watcher=None):
    """A native +20 caller's destination, including first-config array failure."""
    with instance.component.operation():
        return _scalar(instance, cfg, initial=initial, watcher=watcher)


def evaluate_scalar_result(instance, cfg, *, initial=None, watcher=None, _depth=0):
    """The +20 destination and success flag, which need not change together."""
    with instance.component.operation():
        instance.component.consume_work('native scalar result evaluation')
        if _depth >= MAX_CONFIG_DEPTH:
            instance.component.budget_exceeded('native scalar result depth exhausted')
        if cfg is None:
            return initial, False
        if isinstance(cfg, dict) and cfg.get('$') == 'STU_06610EBC':
            configs = cfg.get('m_configVars') or ()
            return (evaluate_scalar_result(instance, configs[0], initial=initial, watcher=watcher,
                                           _depth=_depth+1) if configs else (initial, False))
        if isinstance(cfg, dict) and cfg.get('$') == MODE_SCORE_THRESHOLDS:
            values = mode_score_thresholds(instance, cfg, watcher=watcher)
            return (values[0], True) if values else (0, False)
        if isinstance(cfg, dict) and cfg.get('$') == AUTHORED_ARRAY:
            configs = cfg.get('m_configVars') or ()
            if not configs:
                return 0, False
            child = configs[0]
            return (evaluate_value(instance, child, watcher=watcher) if child is not None else 0), True
        result = controller_heroes.scalar_result(instance.context(watcher), cfg)
        if result is not NotImplemented:
            return result
        value = instance.evaluate(cfg, watcher, initial=_FAILED)
        return (initial, False) if value is _FAILED else (value, True)


def session_entities(instance, cfg, *, watcher=None) -> tuple[expr.Entity, ...]:
    """CE2E40 selector zero collects the resolved session entities in order."""
    component = instance.component
    with component.operation():
        instance.context(watcher).poll_native(cfg)
        entities = (component.world.session_script_entities(instance)
                    if cfg.get('m_B257A3A4', 0) == 0 else NotImplemented)
        if type(entities) is not tuple:
            component.unsupported(instance, 'session entity enumeration', SESSION_ENTITIES)
        for entity in entities:
            component.consume_work('session entity enumeration')
            if type(entity) is not expr.Entity or not expr.valid_entity(entity.id):
                component.unsupported(instance, 'session entity identity', SESSION_ENTITIES)
        return entities


def mode_score_thresholds(instance, cfg, *, watcher=None) -> tuple[float, ...]:
    """104319 CD5740/D9BF20: mode score targets, masked by difficulty."""
    with instance.component.operation():
        selector, success = evaluate_scalar_result(instance, cfg.get('m_5B9DD0CB'), watcher=watcher)
        row = instance.component.world.mode_score_threshold_row()
        if row is NotImplemented:
            instance.component.unsupported(instance, 'mode score threshold table', MODE_SCORE_THRESHOLDS)
        thresholds, limits = row
        limit = limits.get(_asset(selector) if success else 0, len(thresholds))
        values = []
        for index, threshold in enumerate(thresholds):
            instance.component.consume_work('mode score threshold iteration')
            values.append(expr.f32(threshold) if index < limit else -1.0)
        return tuple(values)


def minimum_value(instance, cfg, *, watcher=None) -> float:
    """104319 1B76C90: evaluate the default, then reduce the flattened candidates."""
    with instance.component.operation():
        value, success = evaluate_scalar_result(instance, cfg.get('m_A53100D5'), watcher=watcher)
        default = expr.f32(expr.to_float(value)) if success else 0.0
        minimum = 3.4028234663852886e38
        found = False
        for value in _iter_values(instance, cfg.get('m_814F8EC3'), watcher=watcher):
            found = True
            candidate = expr.f32(expr.to_float(value, default))
            if candidate < minimum:
                minimum = candidate
        return minimum if found else default


def resolve_request(instance, node, resource: Resource, *, context_provider) -> Request | None:
    """906E49..9070D0: targets, explicit context, then ordered array parameters.

    No usable target means no request and no context/parameter evaluation.
    The world supplies the three native entity inputs without an owner/source
    guess. Resource readiness, per-target component lookup and transport are
    the submitter's responsibility; construction does not execute a callee.
    """
    with instance.component.operation():
        targets = []
        for value in _iter_values(instance, node.field("m_targets")):
            entity = expr.to_entity(value)
            if expr.valid_entity(entity):
                targets.append(entity)
        if not targets:
            return None
        context = context_provider()
        if (not isinstance(context, InvocationContext)
                or any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF
                       for value in (context.caller, context.owner, context.instigator))):
            instance.component.unsupported(instance, "played graph invocation context", node.cls, node)
        candidate = context.instigator
        config = node.field("m_6D7701B7")
        if config is not None:
            value = _scalar(instance, config, initial=_FAILED)
            if value is not _FAILED:
                candidate = expr.to_entity(value)
        # 1BA7E60 first seeds +9C from +98; 1BA7E30 accepts only valid overrides.
        instigator = candidate if expr.valid_entity(candidate) else context.caller
        context = InvocationContext(context.caller, context.owner, instigator)
        params = []
        for row in node.field("m_params") or ():
            instance.component.consume_work("played graph parameter collection")
            if row is None:
                continue
            if not isinstance(row, dict):
                instance.component.unsupported(instance, "played graph parameter row", node.cls, node)
            identifier = row.get("m_B5051BCE")
            if identifier is None:
                continue  # Native 9070A9 ignores an absent identifier pointer.
            key = parse_guid(identifier)
            if key is None:
                instance.component.unsupported(instance, "played graph parameter identifier", node.cls, node)
            # 1BA7C60 always allocates an array, even for one item or no items.
            params.append((key, tuple(_iter_values(instance, row.get("m_value")))))
        policy = node.field("m_AA4DE426")
        wire_flag = node.field("m_7ABA409C")
        policy = 0 if policy is None else policy
        wire_flag = 1 if wire_flag is None else wire_flag
        if type(policy) is not int or policy not in range(4):
            instance.component.unsupported(instance, "played graph instance policy", node.cls, node)
        if type(wire_flag) is not int or not 0 <= wire_flag <= 0xFF:
            instance.component.unsupported(instance, "played graph wire flag", node.cls, node)
        return Request(resource, tuple(targets), context, tuple(params), policy, wire_flag)
