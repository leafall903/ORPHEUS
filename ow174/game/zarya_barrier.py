"""Personal 0244 and projected 025C barriers using the authored pool and 10D7 sphere.

Capsule-centre attachment and whole-hit exhaustion are host reconstructions.
"""

import math

from ow174.game import bots, defenses
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, runtime

HERO = defenses.ZARYA
GRAPH = 0x0244
PROJECTED = 0x025C
RADIUS = 1.5


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.pools = {}
        self.projected = {}

    def retire(self, body):
        self.pools.pop(body, None)
        self.projected.pop(body, None)
        for caster in self.fight.match.players:
            entry = self.projected.get(caster.body)
            if entry is not None and entry[2] == body:
                self.projected.pop(caster.body)
                phase = self.projected_phase(caster)
                if phase is not None:
                    self.finish(phase)

    @staticmethod
    def phase(owner):
        return defenses.active_state(owner, HERO, GRAPH, 2)

    @staticmethod
    def projected_phase(owner):
        return defenses.active_state(owner, HERO, PROJECTED, 10)

    def has_phase(self, owner):
        return self.phase(owner) is not None or self.projected_phase(owner) is not None

    @staticmethod
    def finish(phase):
        instance = phase.instance
        instance.state(1 if instance.graph.index == GRAPH else 11).request_finish()
        instance.component.run_now()

    @staticmethod
    def fresh_pool(phase, previous):
        stamp = phase.instance.id, phase.counter, phase.start
        if previous is not None and previous[0] == stamp:
            return stamp, previous[1]
        pool = graph.graph(0x0256).node(0).field("m_amount")
        return stamp, max(0.0, expr.to_float(phase.evaluate(pool, watch=False)))

    def sync(self, owner):
        self.sync_projected(owner)
        phase = self.phase(owner)
        if phase is None:
            self.pools.pop(owner.body, None)
            return None
        self.pools[owner.body] = self.fresh_pool(phase, self.pools.get(owner.body))
        return phase if self.pools[owner.body][1] > 0 else None

    def sync_projected(self, caster):
        phase = self.projected_phase(caster)
        if phase is None:
            self.projected.pop(caster.body, None)
            return
        selected = phase.instance.vars.get(536)
        entity = expr.to_entity(selected.value()) if selected is not None else None
        target = next((item for item in self.fight._targets() if item.entity == entity), None)
        if (target is None or target.team != caster.team or self.fight.vitals_of(target).dead
                or self.fight.match.game_map.free_for_all):
            self.projected.pop(caster.body, None)
            self.finish(phase)
            return
        pool = self.fresh_pool(phase, self.projected.get(caster.body))
        self.projected[caster.body] = (*pool, entity)

    def query_targets(self, state):
        if (state.instance.graph.index, state.index) not in ((PROJECTED, 4), (PROJECTED, 7)):
            return NotImplemented
        from ow174.game.combat import aim, capsule_visible

        component = state.instance.component
        caster = next((p for p in self.fight.match.players if p.body == component.entity), None)
        if (caster is None or caster.hero.guid != HERO or component.owner_dead
                or self.fight.match.collision_world is None or self.fight.match.game_map.free_for_all):
            return ()
        origin = self.fight._eye(caster)
        direction = aim(caster.mover.yaw, caster.mover.pitch)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        modifier = component.entity_var(9595, create=False)
        angle = (10.0 if modifier is None or modifier.value() is None
                 else expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False)))
        threshold = math.cos(math.radians(angle))
        protected = {entry[2] for player in self.fight.match.players
                     if self.projected_phase(player) is not None
                     and (entry := self.projected.get(player.body)) is not None and entry[1] > 0}
        best = None
        for target in self.fight._targets():
            if (target.owner is caster or target.team != caster.team or target.entity in protected
                    or self.fight.vitals_of(target).dead):
                continue
            a, b = target.segment()
            centre = tuple((x + y) * .5 for x, y in zip(a, b, strict=True))
            distance = math.dist(origin, centre)
            if not 0 < distance <= reach:
                continue
            alignment = sum((y - x) * d for x, y, d in zip(origin, centre, direction, strict=True)) / distance
            if (alignment < threshold
                    or not capsule_visible(self.fight._collision(), origin, a, b, target.radius)):
                continue
            rank = -alignment, distance, target.entity
            if best is None or rank < best:
                best = rank
        return () if best is None else (expr.Entity(best[2]),)

    def _enemy_targets(self, shooter):
        for owner in self.fight.match.players:
            self.sync(owner)
        active = {body for body, (_, hp) in self.pools.items() if hp > 0}
        active.update(entity for _, hp, entity in self.projected.values() if hp > 0)
        if not active:
            return []
        return [
            target for target in self.fight._targets()
            if target.entity in active and target.owner is not shooter
            and (self.fight.match.game_map.free_for_all or target.team != shooter.team)
        ]

    def enemies(self, shooter):
        return [target.owner for target in self._enemy_targets(shooter)]

    @staticmethod
    def body(owner):
        return owner.entity if isinstance(owner, bots.Bot) else owner.body

    @staticmethod
    def centre(owner, *, shown=None):
        if shown is not None:
            x, y, z = shown.feet
            return x, y + shown.height * .5, z
        if isinstance(owner, bots.Bot):
            x, y, z = owner.position
            height = mover_data(owner.body).stand_height
        else:
            x, y, z = owner.mover.position
            data = owner.mover.values
            height = data.crouch_height if owner.mover.crouched else data.stand_height
        return x, y + height * .5, z

    @staticmethod
    def intersect(origin, direction, centre):
        from ow174.game.combat import ray_capsule

        return ray_capsule(origin, direction, centre, centre, RADIUS)

    def trace(self, shooter, origin, direction, reach, *, view_tick=None):
        best = None
        for target in self._enemy_targets(shooter):
            shown = None
            if view_tick is not None:
                shown = self.fight.seen(target, view_tick)
                if shown is None:
                    continue
            distance = self.intersect(origin, direction, self.centre(target.owner, shown=shown))
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = distance, target.owner
        return best

    def damage(self, owner, amount, source):
        body = self.body(owner)
        phase = self.phase(owner)
        storage = self.pools
        caster = owner
        if phase is None or self.pools.get(body, (None, 0))[1] <= 0:
            caster = next((p for p in self.fight.match.players
                           if (entry := self.projected.get(p.body)) is not None
                           and entry[2] == body and entry[1] > 0), None)
            if caster is None:
                return
            phase = self.projected_phase(caster)
            storage = self.projected
        if phase is None or amount <= 0:
            return
        stamp, remaining, *target = storage[caster.body]
        loss = min(remaining, amount)
        storage[caster.body] = (stamp, remaining - loss, *target)
        instance = phase.instance
        entry = graph.graph(0x0242).node(5)
        message = graph.guid(entry.field("m_gameMessage.m_gameMessage"))
        runtime.send_message(instance.component, message, {0x2A: expr.f32(loss), 0x12: expr.Entity(source)},
                             caster.body)
        if loss == remaining:
            self.finish(phase)
        else:
            instance.component.run_now()

    def intercept_target(self, shooter, owner, amount):
        """Consume a validated hostile contact on the recipient's barrier."""
        if owner not in self.enemies(shooter):
            return False
        source = shooter.entity if isinstance(shooter, bots.Bot) else shooter.body
        self.damage(owner, amount, source)
        return True

    def melee(self, shooter, centre, radius, amount, source, *, view_tick):
        from ow174.game.combat import capsule_visible

        protected = set()
        origin = self.fight._eye(shooter)
        for target in self._enemy_targets(shooter):
            shown = self.fight.seen(target, view_tick)
            if shown is None:
                continue
            shell = self.centre(target.owner, shown=shown)
            if (math.dist(centre, shell) <= radius + RADIUS
                    and capsule_visible(self.fight._collision(), origin, shell, shell, RADIUS)):
                protected.add(target.entity)
                self.damage(target.owner, amount, source)
        return protected

    def splash(self, shooter, point, origin, blast, cover, impact_owner, blocked):
        from ow174.game.combat import _source_entity, capsule_visible, sphere_surface

        for owner, centre in cover.items():
            if owner is impact_owner:
                continue
            distance = max(0.0, math.dist(point, centre) - RADIUS)
            scalar = blast.splash(distance)
            witness = sphere_surface(point, centre, RADIUS) if distance > 0 else point
            if (scalar > 0 and capsule_visible(self.fight._collision(), origin, centre, centre, RADIUS)
                    and not blocked(self, owner, witness)):
                self.damage(owner, blast.amount * scalar, _source_entity(shooter))

    def covers(self, cover, origin, a, b, body):
        if not cover:
            return False
        if body in cover:
            return True
        centre = tuple((x + y) * .5 for x, y in zip(a, b, strict=True))
        reach = math.dist(origin, centre)
        if reach <= 0:
            return False
        direction = tuple((y - x) / reach for x, y in zip(origin, centre, strict=True))
        return any((distance := self.intersect(origin, direction, shell)) is not None and distance < reach
                   for shell in cover.values())
