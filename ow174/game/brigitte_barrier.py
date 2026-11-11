"""Graph-local pool for Brigitte's reconstructed held shield."""

from dataclasses import dataclass
import math

from ow174.game import barriers, defenses
from ow174.game.script import expr, play

HERO = 0x02E0000000000195
HALF_WIDTH = 1.0
HALF_HEIGHT = 1.1
CORNERS = ((-HALF_WIDTH, -HALF_HEIGHT, 0.0), (HALF_WIDTH, -HALF_HEIGHT, 0.0),
           (HALF_WIDTH, HALF_HEIGHT, 0.0), (-HALF_WIDTH, HALF_HEIGHT, 0.0))
TRIANGLES = (CORNERS[0] + CORNERS[1] + CORNERS[2], CORNERS[0] + CORNERS[2] + CORNERS[3])


@dataclass
class Shield:
    instance: object
    current: float
    maximum: float
    last: float
    regen_after: float

    def raised(self):
        state = self.instance.states.get(30)
        return state is not None and state.active and bool(state.flags & 1)

    def refresh_graph(self):
        state = self.instance.states.get(32)
        if state is not None and state.active:
            state.dependency_changed()
            self.instance.component.run_now()


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.shields = {}

    def current_health(self, cfg, instance):
        # Pet8 and Stack32 share actor variable 13214 in Brigitte's 0EE3 graph.
        entity = cfg.get("m_entity")
        if (instance.graph.index != 0x0EE3 or instance.component.strict
                or not isinstance(entity, dict) or entity.get("$") != "STUConfigVarDynamic"
                or entity.get("m_identifier") != 13214 or entity.get("m_60DB8F99") != 1):
            return NotImplemented
        body = instance.component.entity
        shield = self.shields.get(body)
        if shield is None or shield.instance is not instance:
            maximum = expr.to_float(instance.find_var(expr.INSTANCE, 587).value())
            if maximum <= 1:
                return NotImplemented
            shield = Shield(instance, maximum, maximum, self.fight.now, self.fight.now)
            self.shields[body] = shield
        return expr.f32(shield.current)

    def sync(self, player):
        shield = self.shields.get(player.body)
        if shield is None or not player.spawned or player.gone or self.fight.is_dead(player):
            return
        now = self.fight.now
        if shield.raised():
            shield.regen_after = now + 2
        else:
            # 0EE4/Wait18 delays recharge by 2s and ModifyHealth20 supplies 85 HP/s.
            elapsed = max(0, now - max(shield.last, shield.regen_after))
            current = min(shield.maximum, shield.current + elapsed * 85)
            if current > shield.current:
                shield.current = current
                shield.refresh_graph()
        shield.last = max(shield.last, now)

    def damage(self, shield, amount):
        if amount <= 0 or shield.current <= 1:
            return
        # 0EE3 treats 1 HP as a broken shield and owns the five-second cooldown.
        shield.current = max(1, shield.current - amount)
        shield.regen_after = self.fight.now + 2
        shield.refresh_graph()
        if shield.current <= 1 and shield.raised():
            shield.instance.states[30].stop()
            shield.instance.component.run_now()

    def _faces(self, shooter, team):
        from ow174.game.combat import _along, aim

        for owner in self.fight.match.players:
            if owner is shooter or owner.gone or not owner.spawned or self.fight.is_dead(owner):
                continue
            if not self.fight.match.game_map.free_for_all and owner.team == team:
                continue
            shield = self.shields.get(owner.body)
            if shield is None or shield.current <= 1 or not shield.raised():
                continue
            yaw = owner.mover.yaw
            normal = aim(yaw, owner.mover.pitch)
            x, y, z = owner.mover.position
            centre = _along((x, y + 1, z), normal, 0.5)
            yaw *= math.tau / 65536
            right = (math.cos(yaw), 0.0, -math.sin(yaw))
            up = (normal[1] * right[2], normal[2] * right[0] - normal[0] * right[2],
                  -normal[1] * right[0])
            yield owner, shield, centre, (right, up, normal)

    def cryo_contact(self, origin, direction, reach):
        from ow174.game.combat import ray_capsule

        nearest = None
        for target in self.fight._targets():
            if defenses._cryo(target.owner) is None:
                continue
            a, b = target.segment()
            distance = ray_capsule(origin, direction, a, b, target.radius)
            if distance is not None and distance < reach and (nearest is None or distance < nearest):
                nearest = distance
        return nearest

    def bash_movement_limit(self, player):
        shield = self.shields.get(player.body)
        dash = shield.instance.states.get(14) if shield is not None else None
        if dash is None or not dash.active or dash.ending:
            return None
        x, y, z = player.mover.position
        origin = (x, y + 1, z)

        def movement_limit(direction, reach):
            barrier = self.fight._barrier_contact(player, origin, direction, reach)
            ice = self.fight.walls.cast(origin, direction, reach)
            cryo = self.cryo_contact(origin, direction, reach)
            distances = (barrier[0] if barrier else None, ice[0] if ice else None, cryo)
            nearest = min((value for value in distances if value is not None), default=reach)
            if nearest < reach:
                dash.instance.write((expr.INSTANCE, 488), True)
                dash.try_complete()
                return nearest
            return None

        return movement_limit

    def bash(self, player, command):
        from ow174.game.combat import aim, segment_distance

        shield = self.shields.get(player.body)
        if shield is None:
            return
        instance = shield.instance
        tracker = instance.states.get(12)
        dash = instance.states.get(14)
        if (tracker is None or not tracker.active or dash is None or not dash.active
                or tracker.start + instance.component.frame_ms >= instance.component.now):
            return
        offset, radius, cone = (
            tracker.evaluate(tracker.node.field(field), watch=False)
            for field in ("m_05BE62DD", "m_radius", "m_A30B383E")
        )
        radius = expr.to_float(radius)
        if not isinstance(offset, expr.Vec3) or radius <= 0:
            return
        yaw, pitch = command.aim()
        angle = yaw * math.tau / 65536
        x, y, z = player.mover.position
        centre = (x + math.cos(angle) * offset.x + math.sin(angle) * offset.z, y + offset.y,
                  z - math.sin(angle) * offset.x + math.cos(angle) * offset.z)
        direction = aim(yaw, pitch)
        half_angle = math.radians(expr.to_float(cone)) / 2
        found = []
        for target in self.fight._enemies(player):
            if defenses._cryo(target.owner) is not None:
                continue
            a, b = target.segment()
            distance = segment_distance(centre, a, b)
            if distance <= radius + target.radius and self.fight._aim_contact_visible(
                player, centre, direction, half_angle, target, a, b, solid_cryo=True,
            ):
                found.append((distance, target.entity, target))
        if not found:
            return
        _, _, target = min(found)
        instance.write((expr.INSTANCE, 375), (expr.Entity(target.entity),))
        instance.component.run_now()
        state = instance.states.get(10)
        if state is None or not state.active:
            return
        selected = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
        modify = state.node.field("m_modifyHealth")
        loadout = state.evaluate(modify.get("m_loadout"), watch=False)
        amount, horizontal, vertical = (
            expr.to_float(state.evaluate(modify.get(field), watch=False))
            for field in ("m_amount", "m_E33EFEE1", "m_583C208B")
        )
        resource = play.resolve_resource(instance, instance.graph.node(13))
        state.request_finish()
        # Contact sets 488 after the movement state's normal transition check.
        dash.try_complete()
        instance.component.run_now()
        if (not isinstance(selected, tuple) or expr.Entity(target.entity) not in selected
                or not isinstance(loadout, expr.Asset)):
            return
        if not self.fight._damage(player, target, amount, False, loadout.guid, command.frame):
            return
        if resource is not None and resource.guid == 0x058000000000224E:
            duration = expr.to_float(dict(resource.overrides).get(0x0D800000000000D7))
            self.fight.control(target, player.body, "shield_bash", duration)
            if (target.entity, player.body, "shield_bash") in self.fight.controls:
                dx, _, dz = aim(yaw, 0)
                self.fight.knockback(target, (dx * horizontal, vertical, dz * horizontal), command.frame)

    def contacts(self, shooter, team, origin, direction, length, radius):
        from ow174.game.combat import _along, _dot, _sub

        for owner, shield, centre, (right, up, normal) in self._faces(shooter, team):
            denominator = _dot(direction, normal)
            if denominator >= -1e-9:
                continue
            centre = _along(centre, normal, radius)
            distance = _dot(_sub(centre, origin), normal) / denominator
            if distance < -1e-6 or distance > length + 1e-9:
                continue
            distance = min(length, max(0, distance))
            offset = _sub(_along(origin, direction, distance), centre)
            if (abs(_dot(offset, right)) <= HALF_WIDTH + radius
                    and abs(_dot(offset, up)) <= HALF_HEIGHT + radius):
                yield distance, owner, shield

    def melee_contacts(self, shooter, volumes, seen, visible):
        for owner, shield, centre, axes in self._faces(shooter, shooter.team):
            if ("barrier", owner.body) in seen:
                continue
            if any(barriers.overlaps_frame(TRIANGLES, centre, axes, a, b, radius, visible=visible)
                   for a, b, radius in volumes):
                yield owner, shield
