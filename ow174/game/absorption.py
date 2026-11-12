"""Selected Sigma1D02/1D03 host; pose/filter/pool/clock replication stays reconstructed.

Native D5C2ED..D5C376 selects only operation1 payloads for the amount event.
The actual authored accumulator, conversion/cap and played HealthPool expression
execute here. This is not the general native interception or pool service.
"""

import math

from ow174.game import bots
from ow174.game.script import expr

SIGMA = 0x02E000000000023B
GRASP = 0x1D02
SHIELD_TAG = 0x5847
TICK = .016


def window(instance, at_tick):
    state, chase = instance.states.get(1), instance.states.get(20)
    if (state is None or not state.active or state.node.field('m_8B251D53') != 1
            or chase is None or not chase.active or chase.node.pos != 45):
        return None
    if chase.remaining is not None:
        remaining = max(0, chase.remaining) / 1000
    else:
        destination = expr.to_float(chase.evaluate(chase.node.field('m_destination'), watch=False))
        rate = expr.to_float(chase.evaluate(chase.node.field('m_9B0385B4'), watch=False))
        remaining = abs(chase.cur - destination) / rate if rate > 0 else math.inf
    now = at_tick * TICK
    if (now + (instance.component.frame_ms - 1) / 1000 < state.start / 1000
            or now >= chase.last / 1000 + remaining):
        return None
    return state


def amount(direct, blast):
    """Typed nominal payload; falloff/Matrix placement before native caller remains open."""
    selected = [hit.amount for hit in (direct, blast)
                if hit is not None and hit.health_operation == 1 and not hit.healing]
    return expr.f32(sum(selected)) if selected else None


class Grasp:
    def __init__(self, combat):
        self.combat = combat
        self.converted = {}
        self.decay_clocks = {}
        self.last_tick = None

    def retire(self, entity):
        self.converted.pop(entity, None)
        self.decay_clocks.pop(entity, None)

    def absorbed(self, state, direct, blast, source):
        value = amount(direct, blast)
        if value is None:
            return  # healing interception has no damage-amount row, as in the native selector
        instance = state.instance
        instance.write(expr.lvalue(state.node.field('m_F6CDC81C')), value)
        instance.write(expr.lvalue(state.node.field('m_0A2CABA3')), expr.Entity(source))
        instance.follow(state.node, 'm_160B5DBE', state)
        instance.component.run_now()

    def pool_amount(self, instance, cfg):
        if (instance.graph.index != 0x1D03 or cfg.get('$') != 'STU_783B4CF0'
                or instance.evaluate(cfg.get('m_entity')) != expr.Entity(instance.component.entity)
                or (cfg.get('m_7E8B666C') or {}).get('m_7E8B666C') != 2
                or expr.guid_of((cfg.get('m_7BE02F89') or {}).get('m_identifier')) & 0xffff != SHIELD_TAG):
            return NotImplemented
        vitals = self.combat.vitals.get(instance.component.entity)
        return expr.f32(sum(pool.now for pool in vitals.extra.values()
                            if pool.identifier == SHIELD_TAG)) if vitals is not None else 0.0

    def play(self, instance, node):
        from ow174.game.interception import play_shield_script

        return bool(play_shield_script(instance.component.world, instance, node))

    def update(self):
        from ow174.game.interception import update_pools

        fight = self.combat
        self.last_tick = fight.tick
        update_pools(fight, fight.tick)
        live = {target.entity for target in fight._targets()
                if getattr(getattr(target.owner, "hero", None), "guid", None) == SIGMA}
        for entity in self.converted.keys() - live:
            del self.converted[entity]
            self.decay_clocks.pop(entity, None)


def grasp_intersection(combat, shooter, origin, direction, reach):
    """Nearest live enemy Grasp box before the caller's nearer body/wall bound.

    The local box is reconstructed as view-oriented and eye-relative. Bounds
    themselves are evaluated from the actual active State, including body scale.
    """
    from ow174.game.interception import ray_box

    best = None
    for target in combat._enemies(shooter):
        actor = target.owner
        npc = isinstance(actor, bots.Bot)
        hero = actor.hero if npc else getattr(getattr(actor, "hero", None), "guid", None)
        script = getattr(actor, "body_script", None)
        if hero != SIGMA or script is None or script.component.owner_dead:
            continue
        yaw, pitch = (actor.yaw, 0) if npc else (actor.mover.yaw, actor.mover.pitch)
        yaw, pitch = yaw * math.tau / 65536, pitch * math.tau / 65536
        sy, cy, sp, cp = math.sin(yaw), math.cos(yaw), math.sin(pitch), math.cos(pitch)
        basis = ((cy, 0, -sy), (sy * sp, cp, cy * sp), (sy * cp, -sp, cy * cp))
        eye = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        delta = tuple(a - b for a, b in zip(origin, eye, strict=True))
        local_origin = tuple(sum(a * b for a, b in zip(delta, axis, strict=True)) for axis in basis)
        local_direction = tuple(sum(a * b for a, b in zip(direction, axis, strict=True)) for axis in basis)
        for instance in script.component.instances.values():
            if instance.graph.index != GRASP:
                continue
            state = instance.states.get(1)
            if state is None or not state.active:
                continue
            for box in state.node.field("m_boxes") or []:
                low = state.evaluate(box.get("m_CBA118B2"), watch=False)
                high = state.evaluate(box.get("m_AEF67365"), watch=False)
                if not isinstance(low, expr.Vec3) or not isinstance(high, expr.Vec3):
                    continue
                hit = ray_box(local_origin, local_direction, low, high, reach)
                if hit is not None and (best is None or hit < best[0]):
                    best = hit, state
    return best


from ow174.game.interception import absorbed
