"""104319 unkeyed insertion/merge and fresh typed-array assignment."""

import math

from ow174.game.script import expr, play

_FAILED, _SELF = object(), object()


def _unsupported(instance, node, feature):
    instance.component.unsupported(instance, feature, node.cls, node)


def _alive(instance, node):
    instance.component.ensure_valid()
    if instance.stopped or instance.component.instances.get(instance.id) is not instance:
        _unsupported(instance, node, 'array action owner retired')


def collection_index(value, default=-1):
    """F65260 conversion, including native cvttss2si's indefinite result."""
    while isinstance(value, tuple):
        if not value:
            return default
        value = value[0]
    if value is None:
        return 0
    if type(value) is bool:
        return int(value)
    if type(value) is int:
        return expr.i32(value)
    if type(value) is float:
        value = expr.f32(value)
        value = expr.f32(value-0.5 if value < 0 else value+0.5)
        return (math.trunc(value) if math.isfinite(value) and -2147483648 <= value < 2147483648
                else -2147483648)
    return default


def _key(value):
    """F663A0 rejects bool/int/float/array/map/null as keyed array identifiers."""
    return value is not None and type(value) not in (bool, int, float, tuple, expr.Map)


def _items(instance, node, cfg, target, *, depth=0):
    instance.component.consume_work('array insertion source config')
    if depth >= play.MAX_CONFIG_DEPTH:
        instance.component.budget_exceeded('array insertion config depth exhausted')
    if cfg is None:
        return
    if isinstance(cfg, dict) and cfg.get('$') == 'STU_06610EBC':
        for child in cfg.get('m_configVars') or ():
            yield from _items(instance, node, child, target, depth=depth+1)
        return
    # Dynamic iterator providers expose their actual array storage. Encountering
    # its first row at the destination start terminates the entire native loop.
    if expr.dynamic(cfg) == target and instance.find_var(*target).base:
        yield _SELF
        return
    value = instance.evaluate(cfg, initial=_FAILED)
    _alive(instance, node)
    if value is _FAILED or value is None:
        return

    def flatten(item, level):
        instance.component.consume_work('array insertion source row')
        if level >= play.MAX_CONFIG_DEPTH:
            instance.component.budget_exceeded('array insertion value depth exhausted')
        if isinstance(item, tuple):
            for child in item:
                yield from flatten(child, level+1)
        else:
            yield item

    yield from flatten(value, depth)


def _contains(instance, items, value):
    for item in items:
        instance.component.consume_work('array insertion equality')
        if expr.equal(item, value):
            return True
    return False


def insert(instance, node, caller):
    """1B6B720/+48 and1B6BA40/+78; no keyed row or linked-variable approximation."""
    _alive(instance, node)
    output, source = node.field('m_26E3B366'), node.field('m_value')
    if output is None or source is None:
        return
    target = expr.dynamic(output)
    if target is None:
        if isinstance(output, dict) and output.get('$') in expr.CONSTANTS:
            return  # A known non-Dynamic cannot pass the native output cast.
        _unsupported(instance, node, 'array insertion output target')
    index_cfg = node.field('m_index')
    value = (play.evaluate_scalar(instance, index_cfg, initial=_FAILED, watcher=caller)
             if index_cfg is not None else _FAILED)
    _alive(instance, node)
    index = -1 if value is _FAILED else collection_index(value)
    if index > 200:
        _unsupported(instance, node, 'array insertion native limit diagnostic')
    key_cfg = node.field('m_key')
    if key_cfg is not None:
        key = play.evaluate_scalar(instance, key_cfg, initial=0, watcher=caller)
        _alive(instance, node)
        if not _key(key) or not target[1]:
            return
        _unsupported(instance, node, 'array insertion keyed row storage')
    if not target[1]:
        return
    var = instance.find_var(*target)
    if var.links:
        _unsupported(instance, node, 'array insertion linked variable checkpoint')
    before = var.value()
    # F672D0 changes every other type to a fresh empty array, not a singleton.
    items = list(var.base) if type(var.base) is tuple else []
    original_count = len(items)
    if index > original_count:
        items.extend([0]*(index-original_count))  # Native gaps have int-zero values.
    var.base = tuple(items)
    inserting = 0 <= index < original_count
    unique = bool(node.field('m_8AD67B20'))
    for item in _items(instance, node, source, target):
        _alive(instance, node)
        if item is _SELF:
            break
        if unique and _contains(instance, items, item):
            continue
        if inserting:
            items.insert(index, item)
            index += 1
        else:
            items.append(item)
        # Later configs observe earlier rows; watcher notification occurs only
        # when the native writable-variable handle is released, after iteration.
        var.base = tuple(items)
    instance.changed(var, before)
    _alive(instance, node)
    if len(items) > 200:
        _unsupported(instance, node, 'array insertion native limit diagnostic')


def clear(instance, node):
    """1B6C130: build a fresh empty type12 value, assign it, then follow output."""
    _alive(instance, node)
    cfg = node.field('m_26E3B366')
    if cfg is None:
        return
    target = expr.dynamic(cfg)
    if target is None:
        if isinstance(cfg, dict) and cfg.get('$') in expr.CONSTANTS:
            return
        _unsupported(instance, node, 'array clear output target')
    if target[1]:
        instance.write(target, ())
        _alive(instance, node)
