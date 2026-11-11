"""Photon Barrier server host driven by156B's actual placement/Pet lifecycle.

The resource owns cost, cast delay, actor health and duration. This reconstruction
uses a map-spanning vertical plane (no character collision), current world poses
and team ownership. Native mesh bounds, prediction and presentation remain open.
Projectile impact charges the barrier at the surface. The combat host traces
body splash from the approach side before the barrier can retire.
"""

import math
from dataclasses import dataclass

from ow174.game import pools
from ow174.game.mover import MoverData
from ow174.game.script import expr, graph, runtime
from ow174.game.symmetra import HERO

BODY = 0x04000000000026B0


@dataclass
class Barrier:
    owner: object
    bot: object
    normal: tuple
    expires_at: float
    key: tuple


class Barriers:
    def __init__(self, combat):
        self.combat = combat
        self.walls: list[Barrier] = []
        self.seen: set[tuple] = set()

    def available(self):
        record = graph.entity_body(BODY)
        actor = graph.graph(0x156C)
        return (record is not None and actor is not None
                and any(row['graph'] == pools.BODY_GRAPH for row in record['graphs']))

    def confirm(self, world, state):
        if (world.hero != HERO or world.match is not self.combat.match
                or (state.instance.graph.index, state.node.pos) != (0x156B, 57)
                or not state.active or not self.available()):
            return NotImplemented
        owner = next((p for p in world.match.players if p.body == world.body), None)
        if (owner is None or owner.gone or not owner.spawned or not owner.has_body
                or self.combat.is_dead(owner) or self.combat.is_stunned(owner)):
            return True  # A stale input cannot acquire an authoritative placement.
        # Explicit server ownership of this authored confirmation input. This
        # does not assert native Priority1/2 property/prediction transport parity.
        return world.calculate_placement(state, commit=True)

    def _spawn(self, owner, instance, state, key):
        from ow174.game import npc

        position = state.evaluate(state.node.field('m_position'), watch=False)
        axis = state.evaluate(state.node.field('m_AD8E614B'), watch=False)
        if type(position) is not expr.Vec3 or type(axis) is not expr.Vec3:
            raise ValueError('Photon Barrier requires resolved position and axis')
        horizontal = math.hypot(axis.x, axis.z)
        if not horizontal or not all(math.isfinite(v) for v in (*position, *axis)):
            raise ValueError('Photon Barrier requires finite geometry and a horizontal normal')
        normal = (axis.x / horizontal, 0.0, axis.z / horizontal)
        health = next(row for row in graph.entity_body(BODY)['graphs'] if row['graph'] == pools.BODY_GRAPH)
        values = pools._numbers(graph.graph(pools.BODY_GRAPH).fields['m_publicSchema']['m_entries'])
        values.update(pools._numbers(health.get('m_1EB5A024')))
        # The caster's Chase18 starts from567 (12s here); actor42's30s is a
        # watchdog, not the ordinary wall duration. Do not substitute30 for12.
        chase = instance.graph.node(18)
        duration = expr.to_float(instance.evaluate(chase.field('m_EB0FC261')))
        watchdog = expr.to_float(graph.graph(0x156C).node(42).field('m_timeout')['m_value'])
        if not math.isfinite(duration) or duration <= 0 or watchdog <= 0:
            raise ValueError('Photon Barrier requires a positive authored lifetime')
        definition = npc.Definition(HERO, BODY, 'Photon Barrier', values[pools.HEALTH_VAR], 0, 0,
                                    MoverData(stand_height=1, stand_radius=.01,
                                              crouch_height=1, crouch_radius=.01,
                                              run_forward=0, run_backward=0, run_strafe=0))
        self.retire_owner(owner)  # One independently surviving wall per owner.
        bot = npc.spawn(self.combat.match, definition, tuple(position), team=owner.team)
        bot.spawner.update(manual_spawn=False, summoned=True, photon_barrier=True)
        bot.combat_owner = owner
        # VM command time and world time may have different origins. The
        # reconstructed host starts the authored active lifetime at creation,
        # at most one normal server update after the Pet state begins.
        self.walls.append(Barrier(owner, bot, normal, self.combat.now + min(duration, watchdog), key))
        output = state.node.field('m_B3019338')
        instance.write((output.get('m_60DB8F99', graph.INSTANCE), output['m_identifier']),
                       expr.Entity(bot.entity))

    def _retire(self, wall):
        from ow174.game import npc

        npc.remove_summoned(self.combat.match, wall.bot)
        self.walls.remove(wall)
        script = wall.owner.body_script
        if script is not None and script.entity == wall.key[0]:
            instance = script.component.instances.get(wall.key[1])
            if instance is not None and instance.graph.index == 0x156B:
                state = instance.states.get(instance.graph.node(17).state)
                if state is not None and state.counter == wall.key[2]:
                    if instance.find_var(graph.INSTANCE, 38).value() == expr.Entity(wall.bot.entity):
                        instance.write((graph.INSTANCE, 38), expr.Entity(0))
                    if state.active:
                        state.request(runtime.ABORT)
                        script.component.run_now()

    def retire_owner(self, owner):
        for wall in tuple(self.walls):
            if wall.owner is owner:
                self._retire(wall)

    def update(self):
        live = set()
        for owner in self.combat.match.players:
            script = owner.body_script
            if (owner.gone or not owner.spawned or owner.hero.guid != HERO or script is None
                    or self.combat.is_dead(owner)):
                continue
            for instance in tuple(script.component.instances.values()):
                if instance.graph.index != 0x156B:
                    continue
                state = instance.states.get(instance.graph.node(17).state)
                if state is None or not state.active:
                    continue
                key = (script.entity, instance.id, state.counter)
                live.add(key)
                if key not in self.seen and self.available():
                    self._spawn(owner, instance, state, key)
                    self.seen.add(key)
        self.seen.intersection_update(live)
        return self.retire_ended()

    def retire_ended(self):
        expired = []
        for wall in tuple(self.walls):
            if (wall.bot not in self.combat.match.bots or wall.bot.dead
                    or wall.owner.gone or wall.owner.hero.guid != HERO):
                self._retire(wall)
            elif self.combat.now >= wall.expires_at:
                expired.append(wall)
                self._retire(wall)
        return expired

    @staticmethod
    def _plane_contact(wall, origin, direction, reach):
        denominator = sum(n * d for n, d in zip(wall.normal, direction, strict=True))
        if abs(denominator) < 1e-8:
            return None
        distance = sum((p - o) * n for p, o, n in zip(
            wall.bot.position, origin, wall.normal, strict=True)) / denominator
        return distance if 0 <= distance <= reach else None

    def intersection(self, shooter, origin, direction, reach):
        best = None
        for wall in self.hostile(shooter):
            distance = self._plane_contact(wall, origin, direction, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = distance, wall
        return best

    def _opposes(self, wall, shooter):
        return (getattr(shooter, 'combat_owner', shooter) is not wall.owner
                and (self.combat.match.game_map.free_for_all or shooter.team != wall.bot.team))

    def hostile(self, shooter):
        for wall in self.walls:
            if self.combat.now < wall.expires_at and not wall.bot.dead and self._opposes(wall, shooter):
                yield wall

    def cover_end(self, shooter, origin, target, ended):
        from ow174.game.combat import capsule_ray

        if not ended:
            return None
        direction, reach = capsule_ray(origin, target)
        if not reach:
            return None
        return max(
            (ended_at for wall, ended_at in ended if self._opposes(wall, shooter)
             and self._plane_contact(wall, origin, direction, reach) is not None),
            default=None,
        )

    def splash_contacts(self, shooter, point, reach):
        for wall in self.hostile(shooter):
            offset = sum((p - o) * n for p, o, n in zip(wall.bot.position, point, wall.normal, strict=True))
            if abs(offset) <= reach:
                surface = tuple(o + offset * n for o, n in zip(point, wall.normal, strict=True))
                yield wall, surface

    def damage(self, wall, amount, *, beam=False):
        vitals = self.combat.vitals.get(wall.bot.entity)
        if vitals is None or wall not in self.walls or amount <= 0:
            return
        vitals.damage(amount, beam=beam)
        self.combat._sync_body_health(wall.bot, vitals)
        if vitals.total <= 0:
            self._retire(wall)

    @staticmethod
    def beam_identity(wall):
        return BODY, wall.bot.entity

    def occludes(self, shooter, origin, target):
        """Same nearest-capsule LOS profile as the static sentry query."""
        from ow174.game.combat import capsule_ray

        direction, reach = capsule_ray(origin, target)
        return reach > 0 and self.combat.trace_barrier(shooter, origin, direction, reach) is not None
