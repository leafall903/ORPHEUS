"""104319 component51 total: 5BBC80's48 active slots, in native float32 order."""

from ow174.game.script import expr, play

CLASS = 'STU_9B7648C3'


def total(slots):
    if type(slots) is not tuple or len(slots) != 48:
        raise ValueError('health total requires48 explicit native slots')
    result = 0.0
    for active, current in slots:
        if type(active) is not int or not 0 <= active <= 0xFFFFFFFF:
            raise ValueError('health slot marker must be a native uint32')
        if active:
            result = expr.f32(result + expr.f32(current))
    return result


def evaluate(match, cfg, instance):
    """Match backend for the base pools and server-owned temporary shields.

    InstanceContext already polls this native query. Exact native component+20
    subscription notification timing is not represented by that frame polling.
    """
    if cfg.get('$') != CLASS:
        return NotImplemented
    component = instance.component
    value = play.evaluate_scalar(instance, cfg.get('m_entity'), initial=expr.Entity(0))
    component.ensure_valid()
    identifier = expr.to_entity(value) & 0xFFFFFFFF
    if not expr.valid_entity(identifier):
        return 0.0  # D177C0 invalid-ID path succeeds zero before lookup.
    target = next((target for target in match.combat._targets() if target.entity == identifier), None)
    if target is None:
        return expr.EVALUATION_FAILED
    vitals = match.combat.vitals_of(target)
    # Component51 has sixteen slots for each of its three pool kinds. This
    # Temporary shield slots follow the same ordering as the pool record.
    slots = [(0, 0.0)] * 48
    for kind in (0, 1, 2):
        slots[kind * 16] = (int(kind in vitals.ids), vitals.now[kind])
    first = 33 if 2 in vitals.ids else 32
    for index, pool in enumerate(vitals.temporary_shields.values(), first):
        slots[index] = (1, pool.now)
    return total(tuple(slots))
