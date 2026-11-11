"""Personal 0244 and projected 025C barriers using the authored pool and 10D7 sphere.

Capsule-centre attachment and whole-hit exhaustion are host reconstructions.
"""

import math

from ow174.game import bots
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph, runtime

HERO = 0x02E0000000000068
GRAPH = 0x0244
PROJECTED = 0x025C
RADIUS = 1.5


class Barriers:
    def __init__(self, fight):
        self.fight = fight
        self.pools = {}
        self.projected = {}
        self.frame_anchors = {}
        self.new_activations = set()

    def retire(self, body):
        self.pools.pop(body, None)
        self.projected.pop(body, None)
        self.frame_anchors.pop(body, None)
        self.new_activations.discard((body, GRAPH))
        self.new_activations.discard((body, PROJECTED))
        for caster in self.fight.match.players:
            entry = self.projected.get(caster.body)
            if entry is not None and entry[2] == body:
                self.projected.pop(caster.body)
                phase = self.projected_phase(caster)
                if phase is not None:
                    self.finish(phase)

    def observe_pose(self, owner, tick):
        if owner.hero.guid == HERO:
            self.frame_anchors[owner.body] = (tick, owner.body_script.frame)

    def rebase_overlapping_casts(self, tick):
        from ow174.game.combat import TICK
        from ow174.game.projectiles import MAX_LEASE_FRAME
        from ow174.game.script.nodes import ChaseVar

        rebased = set()
        for owner in self.fight.match.players:
            body = owner.body
            personal_new = (body, GRAPH) in self.new_activations
            projected_new = (body, PROJECTED) in self.new_activations
            if personal_new == projected_new or body not in self.pools or body not in self.projected:
                continue
            anchor = self.frame_anchors.get(body)
            script = owner.body_script
            phase = self.phase(owner) if personal_new else self.projected_phase(owner)
            if anchor is None or script is None or phase is None:
                continue
            anchor_tick, anchor_frame = anchor
            elapsed_ms = max(0, tick - anchor_tick) * round(TICK * 1000)
            target = min(MAX_LEASE_FRAME - 1, anchor_frame + elapsed_ms // script.frame_ms - 1)
            shift = (target - script.frame) * script.frame_ms
            if shift <= 0:
                continue
            instance = phase.instance
            # Both ability graphs own their cast timers. Move the new activation
            # to admission without moving the established ability's deadlines.
            states = {state for state in instance.states.values()
                      if state.active and state.start >= phase.start}
            for state in states:
                state.start += shift
                if isinstance(state, ChaseVar):
                    state.last += shift
            for event in instance.queue:
                if event.state in states:
                    event.time += shift
            instance.queue.sort(key=runtime.Event.key)
            storage = self.pools if personal_new else self.projected
            storage[body] = ((instance.id, phase.counter, phase.start), *storage[body][1:])
            rebased.add(body)
        return rebased

    def advance_idle(self, tick, rebased):
        from ow174.game.combat import TICK
        from ow174.game.projectiles import MAX_LEASE_FRAME
        from ow174.game.script.driver import MAX_GAP

        if not self.frame_anchors:
            return
        for owner in self.fight.match.players:
            anchor = self.frame_anchors.get(owner.body)
            script = owner.body_script
            if anchor is None or script is None or script.retired or script.component.owner_dead:
                continue
            anchor_tick, anchor_frame = anchor
            personal_new = (owner.body, GRAPH) in self.new_activations
            projected_new = (owner.body, PROJECTED) in self.new_activations
            pending = (personal_new or projected_new) and owner.body not in rebased
            elapsed_ms = max(0, tick - anchor_tick) * round(TICK * 1000)
            target = min(MAX_LEASE_FRAME - 1, anchor_frame + elapsed_ms // script.frame_ms - 1)
            while script.frame < target and owner.body_script is script:
                if pending:
                    established = ((owner.body in self.pools and not personal_new)
                                   or (owner.body in self.projected and not projected_new))
                    if not established:
                        # The new input's clock joins at the same endpoint as its collision.
                        if tick == self.fight.match.tick:
                            self.frame_anchors[owner.body] = (tick, anchor_frame)
                        break
                # During overlap, stop at the established shield's expiry even
                # when no projectile caused per-tick combat catch-up.
                previous = script.frame
                owner._advance_body_gap(min(target, previous + (1 if pending else MAX_GAP)) + 1)
                if script.frame <= previous:
                    break

    @staticmethod
    def active_phase(owner, index, number):
        if isinstance(owner, bots.Bot):
            return None
        script = owner.body_script
        if (owner.hero.guid != HERO or script is None or script.retired or script.component.owner_dead):
            return None
        for instance in script.component.instances.values():
            if instance.graph.index == index:
                phase = instance.states.get(number)
                if phase is not None and phase.active:
                    return phase
        return None

    def phase(self, owner):
        return self.active_phase(owner, GRAPH, 2)

    def projected_phase(self, owner):
        return self.active_phase(owner, PROJECTED, 10)

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
        previous = self.pools.get(owner.body)
        current = self.fresh_pool(phase, previous)
        if previous is None or previous[0] != current[0]:
            self.new_activations.add((owner.body, GRAPH))
        self.pools[owner.body] = current
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
        previous = self.projected.get(caster.body)
        pool = self.fresh_pool(phase, previous)
        if previous is None or previous[0] != pool[0]:
            self.new_activations.add((caster.body, PROJECTED))
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

    def visible_this_tick(self, body, graph_index):
        # Newly reported input joins a skipped update at its endpoint, like player shots.
        return (self.fight.tick >= self.fight.match.tick
                or (body, graph_index) not in self.new_activations)

    def enemies(self, shooter):
        for owner in self.fight.match.players:
            self.sync(owner)
        active = {body for body, (_, hp) in self.pools.items()
                  if hp > 0 and self.visible_this_tick(body, GRAPH)}
        active.update(entity for body, (_, hp, entity) in self.projected.items()
                      if hp > 0 and self.visible_this_tick(body, PROJECTED))
        if not active:
            return []
        return [
            target.owner for target in self.fight._targets()
            if target.entity in active and target.owner is not shooter
            and (self.fight.match.game_map.free_for_all or target.team != shooter.team)
        ]

    @staticmethod
    def body(owner):
        return owner.entity if isinstance(owner, bots.Bot) else owner.body

    @staticmethod
    def centre(owner):
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

    def trace(self, shooter, origin, direction, reach):
        best = None
        for owner in self.enemies(shooter):
            distance = self.intersect(origin, direction, self.centre(owner))
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = distance, owner
        return best

    def damage(self, owner, amount, source):
        body = self.body(owner)
        phase = self.phase(owner)
        storage = self.pools
        caster = owner
        if phase is None or self.pools.get(body, (None, 0))[1] <= 0 or not self.visible_this_tick(body, GRAPH):
            caster = next((p for p in self.fight.match.players
                           if (entry := self.projected.get(p.body)) is not None
                           and entry[2] == body and entry[1] > 0
                           and self.visible_this_tick(p.body, PROJECTED)), None)
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

    def absorb_contact(self, source, owner, amount):
        if owner not in self.enemies(source):
            return False
        self.damage(owner, amount, self.body(source))
        return True

    def melee(self, shooter, centre, radius, amount, source, *, origin=None):
        from ow174.game.combat import capsule_visible

        protected = set()
        if amount <= 0:
            return protected
        if origin is None:
            origin = self.fight._eye(shooter)
        for owner in self.enemies(shooter):
            shell = self.centre(owner)
            if (math.dist(centre, shell) <= radius + RADIUS
                    and capsule_visible(self.fight._collision(), origin, shell, shell, RADIUS)):
                protected.add(self.body(owner))
                self.damage(owner, amount, source)
        return protected
