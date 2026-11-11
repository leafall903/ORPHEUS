"""Scoped 104319 spray frost accumulation on Reinhardt."""

from ow174.game.script import expr

SPRAY = 0x0B900000000000D3
INITIAL = expr.f32(0.2)
STEP = expr.f32(0.0285)
GAP = 1.0
FREEZE = 1.3


def hit(fight, shooter, target, *, channel=0, initial=INITIAL, increment=STEP):
    from ow174.game import combat, defenses

    if defenses.control_immune(target.owner) or frozen(fight, target.entity):
        return
    source = combat._source_entity(shooter)
    key = (source, fight.replacement_ticks.get(source), channel)
    samples = fight.frost.setdefault(target.entity, {})
    previous, end = samples.get(key, (0.0, 0.0))
    progress = max(initial, expr.f32((previous if end > fight.now else 0.0) + increment))
    if progress >= 1.0:
        fight.frost.pop(target.entity, None)
        fight.control(target, source, "frost", FREEZE)
    else:
        samples[key] = (progress, fight.now + GAP)


def frozen(fight, entity):
    return any(victim == entity and kind == "frost" and end > fight.now
               for (victim, _, kind), end in fight.controls.items())


def expire(fight):
    for entity, samples in tuple(fight.frost.items()):
        for source, (_, end) in tuple(samples.items()):
            if end <= fight.now:
                del samples[source]
        if not samples:
            del fight.frost[entity]


def movement_mods(fight, entity, mods):
    samples = fight.frost.get(entity)
    if samples:
        progress = max((amount for amount, end in samples.values() if end > fight.now), default=0.0)
        if progress > 0:
            # 05E7 caps the reconstructed slowdown at 70 percent.
            mods[(-1, 0, 0)] = (0, 1, max(0.3, 1.0 - progress), 99.0)
    return mods
