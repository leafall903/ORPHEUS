"""Selected 0EE3 Brigitte shield health and current-pose front face.

The stored Pet-health bridge, break sentinel and recovery follow reviewed
0EE3/0EE4 fields. The face dimensions are a declared host proxy.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr

HERO = 0x02E0000000000195
GRAPH = 0x0EE3
HALF_WIDTH, HALF_HEIGHT = 1.0, 1.1
REGEN_DELAY, REGEN_RATE = 2.0, 85.0


@dataclass
class Shield:
    instance: object
    health: float
    maximum: float
    last: float
    damaged_at: float | None = None
    raised_at: float | None = None


@dataclass(frozen=True)
class Contact:
    manager: object
    shield: Shield

    def damage(self, amount):
        self.manager.damage(self.shield, amount)


class Shields:
    def __init__(self, combat):
        self.combat = combat
        self.by_body = {}

    def retire(self, body):
        self.by_body.pop(body, None)

    def native(self, world, cfg, instance):
        key = cfg.get("m_entity")
        if (instance is None or world.hero != HERO or instance.graph.index != GRAPH
                or instance.component.entity != world.body or cfg.get("$") != "STU_9B7648C3"
                or not isinstance(key, dict) or key.get("m_identifier") != 13214):
            return NotImplemented
        shield = self.by_body.get(world.body)
        if shield is None:
            maximum = expr.to_float(instance.find_var(expr.INSTANCE, 587).value())
            if maximum <= 1:
                raise ValueError("authored Brigitte shield capacity is unavailable")
            shield = Shield(instance, maximum, maximum, self.combat.now)
            self.by_body[world.body] = shield
        return expr.f32(shield.health)

    def active(self, player, shield):
        state = shield.instance.states.get(30)
        return (player.spawned and player.has_body and not player.gone and player.client is not None
                and player.body_script is not None
                and player.body_script.component is shield.instance.component
                and not self.combat.is_dead(player) and shield.health > 1
                and state is not None and state.active and state.flags & state.RUNNING
                and self.combat.now + (state.instance.component.frame_ms - 1) / 1000 >= state.start / 1000)

    @staticmethod
    def sync(shield):
        state = shield.instance.states.get(32)
        if state is not None and state.active:
            state.dependency_changed()
            shield.instance.component.run_now()

    def update(self, now):
        for player in self.combat.match.players:
            shield = self.by_body.get(player.body)
            if shield is None or self.combat.is_dead(player):
                continue
            if self.active(player, shield):
                shield.raised_at = now
            else:
                deadlines = [shield.last]
                if shield.damaged_at is not None:
                    deadlines.append(shield.damaged_at + REGEN_DELAY)
                if shield.raised_at is not None:
                    deadlines.append(shield.raised_at + REGEN_DELAY)
                start = max(deadlines)
                if now > start and shield.health < shield.maximum:
                    shield.health = min(shield.maximum, shield.health + (now - start) * REGEN_RATE)
                    self.sync(shield)
            shield.last = max(shield.last, now)

    def damage(self, shield, amount):
        if amount <= 0 or shield.health <= 1:
            return
        shield.health = max(1, shield.health - amount)
        shield.damaged_at = self.combat.now
        self.sync(shield)
        if shield.health <= 1:
            ability = shield.instance.states.get(30)
            if ability is not None:
                ability.stop()
                shield.instance.component.run_now()

    def faces(self, shooter, *, team=None):
        from ow174.game.combat import _along, aim

        side = shooter.team if team is None else team
        for player in self.combat.match.players:
            if (player is shooter or (not self.combat.match.game_map.free_for_all and player.team == side)):
                continue
            shield = self.by_body.get(player.body)
            if shield is None or not self.active(player, shield):
                continue
            normal = aim(player.mover.yaw, player.mover.pitch)
            x, y, z = player.mover.position
            centre = _along((x, y + 1, z), normal, 0.5)
            yaw = player.mover.yaw * math.tau / 65536
            right = (math.cos(yaw), 0, -math.sin(yaw))
            up = (normal[1] * right[2], normal[2] * right[0] - normal[0] * right[2], -normal[1] * right[0])
            yield shield, centre, normal, right, up

    def first(self, shooter, origin, direction, length, *, team=None, radius=0.0):
        from ow174.game.combat import _along, _dot, _sub

        best = None
        for shield, centre, normal, right, up in self.faces(shooter, team=team):
            denominator = _dot(direction, normal)
            if denominator >= -1e-9:
                continue
            plane_offset = _dot(_sub(centre, origin), normal)
            if plane_offset > 0:
                continue
            distance = max(0.0, (plane_offset + radius) / denominator)
            if distance > length + 1e-9 or (best is not None and distance >= best[0]):
                continue
            offset = _sub(_along(origin, direction, distance), centre)
            if abs(_dot(offset, right)) <= HALF_WIDTH and abs(_dot(offset, up)) <= HALF_HEIGHT:
                best = distance, Contact(self, shield)
        return best
