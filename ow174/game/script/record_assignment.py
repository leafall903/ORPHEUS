"""Reconstructed record lvalues used by224E's pending stun array.

Only explicit Dynamic -> numeric array index -> identifier member chains are
supported. This is not the native general address evaluator: foreign entity,
proxy, linked-root, computed-address and non-record writes remain unsupported.
"""

from ow174.game.script import expr

_FAILED = object()
MAX_ROWS = 200
MAX_PATH = 32


def path(instance, cfg):
    if not isinstance(cfg, dict) or cfg.get("$") not in expr.EXPRESSIONS:
        return None
    data = cfg.get("m_expression") or {}
    code = data.get("m_opcodes") or []
    dynamics = data.get("m_dynamicVars") or []
    floats = data.get("m_D99EF254") or []
    try:
        if any(type(byte) is not int or not 0 <= byte <= 255 for byte in code):
            return None
        if len(code) < 3 or code[0] not in (8, 9):
            return None
        target = expr.dynamic(dynamics[code[1]])
        if target is None or not target[1]:
            return None
        steps, pc = [], 2
        while pc < len(code) and len(steps) < MAX_PATH:
            instance.component.consume_work("record assignment path")
            if code[pc] == 0:
                return (target, tuple(steps)) if steps and pc == len(code) - 1 else None
            op, argument, accessor = code[pc:pc + 3]
            if accessor == 17 and op in (6, 8, 9):
                value = (floats[argument] if op == 6 else instance.evaluate(dynamics[argument]))
                if value is not None and not isinstance(value, (bool, int, float)):
                    return None
                index = expr.to_int(value)
                if not 0 <= index < MAX_ROWS:
                    return None
                steps.append(("index", index))
            elif accessor in (15, 16) and op == 7:
                key = int(dynamics[argument].get("m_identifier") or 0)
                if not key:
                    return None
                steps.append(("member", key))
            else:
                return None
            pc += 3
    except (IndexError, TypeError, ValueError):
        return None
    return None


def _replace(instance, current, steps, value):
    instance.component.consume_work("record assignment copy")
    if not steps:
        return value
    (kind, key), *tail = steps
    if kind == "index":
        if current is not None and not isinstance(current, tuple):
            return _FAILED
        if current is not None and len(current) > MAX_ROWS:
            return _FAILED
        items = list(current or ())
        if key > len(items):
            return _FAILED  # no inferred sparse-array allocation
        if key == len(items):
            items.append(None)
        replacement = _replace(instance, items[key], tail, value)
        if replacement is _FAILED:
            return _FAILED
        items[key] = replacement
        return tuple(items)
    if current is not None and not isinstance(current, expr.Map):
        return _FAILED
    items = list(current.items) if current is not None else []
    old = current.get(key) if current is not None else None
    replacement = _replace(instance, old, tail, value)
    if replacement is _FAILED:
        return _FAILED
    kept = tuple((k, v) for k, v in items if not expr.equal(k, key))
    return expr.Map((*kept, (key, replacement)))


def store(instance, cfg, value):
    resolved = path(instance, cfg)
    if resolved is None:
        return False
    target, steps = resolved
    variable = instance.find_var(*target, create=False)
    if variable is not None and variable.links:
        return False  # native writable handles for linked storage are not reconstructed
    current = variable.base if variable is not None else None
    replacement = _replace(instance, current, steps, value)
    if replacement is _FAILED:
        return False
    instance.write(target, replacement)
    return True
