"""Body-bound protection from actual Zarya Pet and effect activations."""

import math
from dataclasses import dataclass

from ow174.game.script import expr, runtime

HERO = 0x02E0000000000068
PERSONAL, PROJECTED = 0x0244, 0x025C
STATES = {PERSONAL: (2, 5), PROJECTED: (10, 18)}
ENERGY_GAIN = 0x0240000000000146


@dataclass
class Bubble:
    source: int
    until: float
    health: float = 200.0
    source_retired: bool = False


def active(fight, entity):
    bubble = fight.zarya_bubbles.get(entity)
    return bubble if bubble is not None and bubble.health > 0 and fight.now < bubble.until else None


def sync(fight, player):
    if (player.hero.guid != HERO or player.gone or not player.has_body or fight.is_dead(player)
            or player.body_script is None):
        return
    for instance in player.body_script.component.instances.values():
        pair = STATES.get(instance.graph.index)
        if pair is None:
            continue
        pet, effect = (instance.states.get(index) for index in pair)
        if pet is None or not pet.active or effect is None or not effect.active:
            continue
        key = player.body, instance.id
        if fight.zarya_casts.get(key) == pet.counter:
            continue
        fight.zarya_casts[key] = pet.counter
        duration = expr.to_float(instance.find_var(0, 215).value())
        if not math.isfinite(duration) or duration <= 0:
            continue
        recipients = instance.evaluate(effect.node.field("m_targets"))
        if isinstance(recipients, expr.Entity):
            recipients = (recipients,)
        if not isinstance(recipients, tuple):
            continue
        for recipient in recipients:
            if not isinstance(recipient, expr.Entity):
                continue
            target = fight._target_entity(recipient.id)
            if target is None or active(fight, recipient.id) is not None:
                continue
            if instance.graph.index == PERSONAL:
                if target.owner is not player:
                    continue
            elif target.owner is player or fight.match.game_map.free_for_all or target.team != player.team:
                continue
            fight.zarya_bubbles[target.entity] = Bubble(player.body, fight.now + duration)
            fight.sleeps.pop(target.entity, None)
            fight.vitals_of(target).healing_block.clear()


def targets(fight, body, state):
    from ow174.game.combat import aim, capsule_visible

    caster = next((p for p in fight.match.players if p.has_body and not p.gone and p.body == body), None)
    if (caster is None or fight.match.collision_world is None or fight.match.game_map.free_for_all
            or fight.is_dead(caster) or fight.is_stunned(caster)):
        return ()
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    scale = state.instance.component.entity_var(9595, create=False)
    factor = scale.value() if scale is not None else None
    angle = 10 * (expr.to_float(factor) if factor is not None else 1)
    if not math.isfinite(radius) or radius <= 0 or not 0 <= angle <= 180:
        return ()
    origin = fight._eye(caster)
    direction = aim(caster.mover.yaw, caster.mover.pitch)
    candidates = []
    for target in fight._targets():
        if (target.owner is caster or target.team != caster.team or fight.vitals_of(target).dead
                or active(fight, target.entity) is not None):
            continue
        script = target.owner.body_script
        if script is not None and any(
            (slot := script.component.entity_var(var, create=False)) is not None and expr.truthy(slot.value())
            for var in (6600, 11264)
        ):
            continue
        centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        delta = tuple(b - a for a, b in zip(origin, centre, strict=True))
        distance = math.hypot(*delta)
        if not 0 < distance <= radius:
            continue
        alignment = sum(a * b for a, b in zip(direction, delta, strict=True)) / distance
        if (alignment >= math.cos(math.radians(angle)) and not fight.walls.blocked(origin, centre)
                and capsule_visible(fight.match.collision_world, origin, *target.segment(), target.radius)):
            candidates.append((-alignment, distance, target.entity))
    return (expr.Entity(min(candidates)[2]),) if candidates else ()


def absorb(fight, entity, amount, attacker):
    bubble = active(fight, entity)
    if bubble is None:
        return False
    absorbed = min(amount, bubble.health)
    bubble.health -= absorbed
    if bubble.source_retired:
        return True
    caster = fight._target_entity(bubble.source)
    if caster is not None and caster.owner.body_script is not None:
        component = caster.owner.body_script.component
        params = {0x2A: absorbed, 0x12: expr.Entity(attacker)}
        if runtime.send_message(component, ENERGY_GAIN, params, bubble.source):
            component.run_now()
    return True


def retire(fight, entity):
    fight.zarya_bubbles.pop(entity, None)
    for bubble in fight.zarya_bubbles.values():
        if bubble.source == entity:
            bubble.source_retired = True
    for key in [key for key in fight.zarya_casts if key[0] == entity]:
        del fight.zarya_casts[key]
