"""Soundwave contacts from the authored 104319 cast and live combat geometry."""

import math

from ow174.game.script import expr
from ow174.game.script.runtime import InstanceContext

HERO = 0x02E0000000000079
GRAPH = 0x0776


class ImpulseContext(InstanceContext):
    """Resolve the self-facing queries used by Soundwave's impulse expression."""

    def __init__(self, instance, yaw, pitch):
        super().__init__(instance, None)
        self.yaw, self.pitch = yaw, pitch

    def native(self, cfg):
        from ow174.game.combat import aim

        kind = cfg.get("$")
        if kind in ("STU_2200A66D", "STU_021D01CD"):
            if (cfg.get("m_entity") or {}).get("$") == "STU_B5A0CAF0":
                return expr.Vec3(*aim(self.yaw, self.pitch if kind == "STU_2200A66D" else 0))
        if kind == "STU_0762B33A":
            desired = expr.evaluate(cfg.get("m_32B00625"), self)
            basis = expr.evaluate(cfg.get("m_3661BC45"), self)
            if isinstance(desired, expr.Vec3) and isinstance(basis, expr.Vec3):
                yaw = math.atan2(basis.x, basis.z)
                pitch = math.atan2(basis.y, math.hypot(basis.x, basis.z))
                yaw_limit = math.radians(expr.to_float(expr.evaluate(cfg.get("m_5023B9B0"), self)))
                pitch_limit = math.radians(expr.to_float(expr.evaluate(cfg.get("m_E26E5E09"), self)))
                offset_yaw = (math.atan2(desired.x, desired.z) - yaw + math.pi) % math.tau - math.pi
                offset_pitch = math.atan2(desired.y, math.hypot(desired.x, desired.z)) - pitch
                yaw += max(-yaw_limit, min(yaw_limit, offset_yaw))
                pitch += max(-pitch_limit, min(pitch_limit, offset_pitch))
                return expr.Vec3(math.sin(yaw) * math.cos(pitch), math.sin(pitch),
                                 math.cos(yaw) * math.cos(pitch))
        return super().native(cfg)


def consume(fight, player, command, script):
    from ow174.game.combat import EPSILON, _dot, _number, _sub, aim, hit_def, ray_world, segment_distance

    for instance in script.component.instances.values():
        if instance.graph.index != GRAPH:
            continue
        state, search = instance.states.get(11), instance.states.get(10)
        if state is None or search is None:
            continue
        counters = fight.melee_seen.setdefault(script.entity, {})
        key = (instance.id, state.index)
        seen = counters.get(key, 0)
        counters[key] = state.counter
        if state.counter == seen or not state.active:
            continue
        modify = state.node.field("m_modifyHealth")
        hit = hit_def(state, {"m_modifyHealth": modify})
        radius = _number(search.evaluate(search.node.field("m_radius"), watch=False), 0.0)
        angle = _number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0.0)
        if hit.amount <= 0 or radius <= 0 or not 0 < angle <= 360:
            continue

        context = ImpulseContext(instance, *command.aim())
        impulse = expr.evaluate(modify.get("m_1376B4DF"), context)
        origin, direction = fight._eye(player), aim(*command.aim())
        threshold = math.cos(math.radians(angle / 2))
        view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
        for target in fight._enemies(player):
            shown = fight.seen(target, view_tick)
            if shown is None or segment_distance(origin, *shown.segment()) - shown.radius > radius:
                continue
            x, y, z = shown.feet
            centre = (x, y + shown.height / 2, z)
            offset = _sub(centre, origin)
            length = math.sqrt(_dot(offset, offset))
            if length > EPSILON and _dot(offset, direction) < length * threshold:
                continue
            # 104319's fallback visibility checks sample 30 and 60 percent of body height.
            for fraction in (0.3, 0.6):
                sample = (x, y + shown.height * fraction, z)
                delta = _sub(sample, origin)
                reach = math.sqrt(_dot(delta, delta))
                if reach <= EPSILON:
                    break
                wall = ray_world(fight._collision(), origin, tuple(axis / reach for axis in delta), reach)
                if (wall is None or wall >= reach) and not fight.walls.blocked(origin, sample):
                    break
            else:
                continue
            if fight._damage(player, target, hit.amount, False, hit.loadout, command.frame, tags=hit.tags):
                if isinstance(impulse, expr.Vec3):
                    fight.knockback(target, tuple(impulse), command.frame)
