"""Reconstructed Zarya combat services driven by the target build's authored States.

Target selection, damage interception and shield recovery use host services. Pet entity
hosting, control immunity and original-client presentation remain open.
"""

import math
from dataclasses import dataclass

from ow174.game import bots
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E0000000000068
PARTICLE_GRENADE = 0x04000000000003DB
SECONDARY_RECOVERY_TICKS = 62  # Default paced 0241 grenade cadence in build 104319.
BUBBLE_RADIUS = 1.5  # Target-build 10D7.00C MSTU sphere, shared by both barrier pets.
ULT_PASSIVE_PER_SECOND = 5.0  # Reconstructed combat charge rate.


def ultimate_charge_ready_frame(script) -> int | None:
    """Use the cast's authored end wait even when input stalls the body VM."""
    ready = 0
    for ability in script.ultimates:
        if not (ability.flags & nodes.Ability.RUNNING):
            continue
        instance = ability.instance
        if instance.graph.index != 0x025F or ability.node.pos != 7:
            return None
        wait = instance.states.get(instance.graph.node(4).state)
        if not isinstance(wait, nodes.Wait) or not wait.active:
            return None
        ready = max(ready, (wait.start + wait.ms) // script.frame_ms + 1)
    return ready


def advance_energy(script, tick: int) -> None:
    from ow174.game.combat import TICK

    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == 0x0242), None)
    if instance is None:
        return
    wait = instance.states.get(instance.graph.node(7).state)
    deadline = getattr(wait, "world_deadline", None)
    if deadline is None or tick < deadline:
        return
    tick_ms = round(TICK * 1000)
    if wait.active:
        wait.request_finish()
        component.run_now()
        chase = instance.states.get(instance.graph.node(12).state)
        if chase is not None and chase.active:
            # The first elapsed step starts at the exact authored timeout.
            chase.world_used = deadline * tick_ms - (-wait.ms % tick_ms)
    chase = instance.states.get(instance.graph.node(12).state)
    if chase is not None and chase.active and not chase.reached:
        elapsed = tick * tick_ms - chase.world_used
        if elapsed > 0:
            chase.world_used += elapsed
            chase.advance(elapsed)
            component.run_now()


@dataclass
class Barrier:
    activation: tuple
    remaining: float
    recipient: int | None = None


@dataclass
class BarrierExpiry:
    owner: object
    duration: nodes.ChaseVar
    generation: int
    frame: int
    tick: int
    started: int
    ended_ahead: bool = False


@dataclass
class Recovery:
    damaged_tick: int
    processed_tick: int


class ShieldRecovery:
    """0033 shield regeneration bridged to authoritative pools for Zarya.

    Node66 supplies delay3 times21868e, node5 supplies rate30 times21869e.
    Nodes68/69 supply the authored default scalars1. Undeclared scalars do not
    regenerate. Reconstruction: actual body pool loss resets delay, a fully
    intercepted barrier hit does not, and only shields regenerate. Ana's healing
    block suppresses recovery and her healing boost raises its rate within
    their world deadlines.
    """

    def __init__(self, combat):
        self.combat = combat
        self.bodies: dict[int, Recovery] = {}

    def retire(self, body: int) -> None:
        self.bodies.pop(body, None)

    def before_pool_change(self, body: int, tick: int) -> None:
        """Settle recovery through the tick before the new health event."""
        recovery = self.bodies.get(body)
        if recovery is not None and recovery.processed_tick < tick - 1:
            self.update(tick - 1, only_body=body)

    def damaged(self, owner, tick: int) -> None:
        if getattr(getattr(owner, "hero", None), "guid", None) != HERO:
            return
        previous = self.bodies.get(owner.body)
        if previous is not None:
            tick = max(tick, previous.damaged_tick, previous.processed_tick)
        self.bodies[owner.body] = Recovery(tick, tick)

    @staticmethod
    def scalar(component, identifier):
        var = component.entity_var(identifier, create=False)
        value = None if var is None else var.value()
        return None if value is None else max(0.0, expr.to_float(value))

    def update(self, tick: int, *, only_body: int | None = None) -> None:
        current = {p.body: p for p in self.combat.match.players if p.has_body and not p.gone}
        for body, recovery in list(self.bodies.items()):
            if only_body is not None and body != only_body:
                continue
            player = current.get(body)
            vitals = self.combat.vitals.get(body)
            if (self.combat.match.ended or player is None or vitals is None or vitals.dead
                    or player.hero.guid != HERO):
                self.retire(body)
                continue
            previous = recovery.processed_tick
            recovery.processed_tick = max(previous, tick)
            script = player.body_script
            if script is None or tick <= previous:
                continue
            base = graph.graph(0x0033)
            delay = base.node(66).field("m_7130456B.m_expression.m_D99EF254")[0]
            rate = base.node(5).field("m_modifyHealth.m_amount.m_expression.m_D99EF254")[1]
            delay_scalar = self.scalar(script.component, 21868)
            rate_scalar = self.scalar(script.component, 21869)
            if delay_scalar is None or rate_scalar is None:
                continue
            delay *= delay_scalar
            rate *= rate_scalar
            since = max(previous, recovery.damaged_tick + delay / 0.016, vitals.healing_block_until - 1)
            elapsed = max(0.0, tick - since)
            boosted = max(0.0, min(tick, vitals.healing_boost_until - 1) - since)
            amount = rate * (elapsed + 0.5 * boosted) * 0.016
            amount = min(amount, vitals.most[SHIELDS_POOL] - vitals.now[SHIELDS_POOL])
            if amount > 0:
                vitals.now[SHIELDS_POOL] += amount
                vitals.changed.add(SHIELDS_POOL)
                self.combat._sync_body_health(player, vitals)


class Barriers:
    def __init__(self, combat):
        self.combat = combat
        self.active: dict[int, Barrier] = {}
        self.projected: dict[int, Barrier] = {}
        self.expiries: dict[tuple[int, bool], BarrierExpiry] = {}
        self.pending_cleanses: dict[tuple[int, int], set[int]] = {}
        self.cooldown_deadlines: dict[tuple[int, bool], int] = {}

    def retire(self, body: int) -> None:
        self.active.pop(body, None)
        self.projected.pop(body, None)
        self.expiries.pop((body, False), None)
        self.expiries.pop((body, True), None)
        for bodies in tuple(self.pending_cleanses):
            if body in bodies:
                self.pending_cleanses.pop(bodies)
        self.cooldown_deadlines.pop((body, False), None)
        self.cooldown_deadlines.pop((body, True), None)
        for caster, barrier in tuple(self.projected.items()):
            if barrier.recipient == body:
                self.projected.pop(caster)

    def ability_start_allowed(self, state):
        index = state.instance.graph.index
        if (index, state.node.pos) not in ((0x0244, 3), (0x025C, 21)):
            return True
        key = state.instance.component.entity, index == 0x025C
        return key not in self.expiries and self.combat.match.tick >= self.cooldown_deadlines.get(key, 0)

    def track_targets(self, state, angles):
        """Reconstructed ally selection for the two authored projected searches.

        Radius and base cone come from the resource. Missing cone modifier uses1.
        Rank by angular alignment then distance; require a live ally and static LOS.
        Other target States remain undeclared, rather than sharing this filter policy.
        """
        if state.instance.graph.index != 0x025C or state.node.pos not in (11, 18):
            return NotImplemented
        owner = next((p for p in self.combat.match.players
                      if p.body == state.instance.component.entity), None)
        if (owner is None or getattr(owner.hero, "guid", None) != HERO
                or self.combat.is_dead(owner) or self.combat.match.game_map.free_for_all):
            return ()
        # Import geometry lazily: combat owns this service and its geometric types.
        from ow174.game.combat import aim

        origin, direction = self.combat._eye(owner), aim(*angles)
        reach = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        cone = expr.to_float(state.evaluate(state.node.field("m_A30B383E"), watch=False))
        modifier = state.instance.component.entity_var(9595, create=False)
        if modifier is None or modifier.value() is None:
            cone = state.node.field("m_A30B383E")["m_expression"]["m_D99EF254"][0]
        best = None
        for target in self.combat._targets():
            if target.owner is owner or target.team != owner.team or self.combat.vitals_of(target).dead:
                continue
            point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
            delta = tuple(point[k] - origin[k] for k in range(3))
            distance = math.sqrt(sum(v * v for v in delta))
            if distance <= 0 or distance > reach:
                continue
            alignment = sum(delta[k] * direction[k] for k in range(3)) / distance
            if alignment < math.cos(math.radians(cone)):
                continue
            # Cryo itself remains a valid ally recipient but covers bodies behind it.
            wall = self.combat.cover_ray(origin, tuple(v / distance for v in delta), distance,
                                        exclude=target.owner)
            if wall is not None and wall < distance:
                continue
            score = (1 - alignment, distance, target.entity)
            if best is None or score < best[0]:
                best = score, target.entity
        return () if best is None else (best[1],)

    def observe_command(self, caster, previous_time):
        """Observe authored barrier starts and finishes after a body command."""
        if getattr(caster.hero, "guid", None) != HERO:
            return
        for projected in (False, True):
            expiry = self.expiries.get((caster.body, projected))
            if (expiry is not None and self.combat.tick < expiry.tick
                    and self._duration_ended_on_deadline(expiry, caster.body_script.frame_ms)):
                expiry.ended_ahead = True
        for instance in caster.body_script.component.instances.values():
            if instance.graph.index == 0x0244:
                pet = instance.states.get(2)
                body = caster.body
            elif instance.graph.index == 0x025C:
                pet = instance.states.get(10)
                selected = instance.vars.get(536)
                target = selected.value() if selected is not None else None
                if not isinstance(target, expr.Entity):
                    continue
                body = target.id
            else:
                continue
            if pet is not None and pet.active and pet.start > previous_time:
                projected = instance.graph.index == 0x025C
                duration = instance.states.get(11 if projected else 1)
                self._schedule_expiry(caster, duration, projected)
                if caster.match.tick > self.combat.tick:
                    self.pending_cleanses.setdefault((caster.body, body), set()).add(caster.match.tick)
                else:
                    vitals = self.combat.vitals.get(body)
                    if vitals is not None:
                        vitals.healing_block_until = 0

    def _schedule_expiry(self, caster, duration, projected):
        from ow174.game.combat import TICK
        from ow174.game.projectiles import MAX_LEASE_FRAME

        if not isinstance(duration, nodes.ChaseVar) or not duration.active:
            return
        script = caster.body_script
        remaining = duration.remaining
        if remaining is None:
            rate = expr.to_float(duration.evaluate(duration.node.field("m_9B0385B4"), watch=False))
            if rate <= 0:
                return
            remaining = round(abs(expr.to_float(duration.cur) - expr.to_float(duration.destination())) * 1000
                              / rate)
        frame = (duration.last + remaining - 1) // script.frame_ms
        if script.frame < frame < MAX_LEASE_FRAME:
            elapsed = (frame - script.frame) * script.frame_ms
            started = caster.match.tick
            tick = started + math.ceil(elapsed / (TICK * 1000))
            expiry = BarrierExpiry(caster, duration, duration.counter, frame, tick, started)
            self.expiries[caster.body, projected] = expiry

    def clock_edges(self, tick):
        return {edge for expiry in self.expiries.values()
                for edge in (expiry.tick, expiry.tick + 1, expiry.tick + 2)
                if self.combat.tick < edge <= tick}

    def start_edges(self, tick):
        edges = {edge for expiry in self.expiries.values()
                 for edge in (expiry.started - 1, expiry.started)
                 if self.combat.tick < edge <= tick}
        edges.update(edge for pending in self.pending_cleanses.values()
                     for start in pending for edge in (start - 1, start)
                     if self.combat.tick < edge <= tick)
        return edges

    def apply_cleanses(self):
        if not self.pending_cleanses:
            return
        if self.combat.match.ended:
            self.pending_cleanses.clear()
            return
        tick = self.combat.tick
        for bodies, pending in tuple(self.pending_cleanses.items()):
            if tick not in pending:
                continue
            pending.remove(tick)
            if not pending:
                self.pending_cleanses.pop(bodies)
            caster, recipient = bodies
            vitals = self.combat.vitals.get(recipient)
            if vitals is None or vitals.healing_block_until <= tick:
                continue
            if caster != recipient:
                teams = {target.entity: target.team for target in self.combat._targets()}
                if caster not in teams or recipient not in teams or teams[caster] != teams[recipient]:
                    continue
            # Keep the shortened deadline until shield recovery has integrated it.
            vitals.healing_block_until = tick

    @staticmethod
    def _duration_ended_on_deadline(expiry, frame_ms):
        # An accepted input frame can finish the duration before the world replays
        # an earlier contact. Keep that activation until its mapped expiry tick.
        duration = expiry.duration
        return (not duration.active and duration.counter == expiry.generation
                and duration.ended_at // frame_ms == expiry.frame)

    def advance_expiries(self):
        for (body, projected), expiry in tuple(self.expiries.items()):
            owner = expiry.owner
            script = owner.body_script
            if (not self.combat.match.ended and not owner.gone and owner.has_body and owner.body == body
                    and script is not None and not script.retired
                    and script.component is expiry.duration.instance.component
                    and expiry.duration.counter == expiry.generation
                    and not self.combat.is_dead(owner)):
                if expiry.ended_ahead and self.combat.tick <= expiry.tick:
                    continue
                if expiry.duration.active and self.combat.tick > expiry.tick:
                    # Leave the expiry frame available for real input before running a neutral gap.
                    frame = expiry.frame + min(1, self.combat.tick - expiry.tick - 1)
                    owner._advance_body_gap(frame + 1)
                if expiry.duration.active:
                    continue
                self._record_cooldown(body, projected, expiry, script.frame_ms)
            self.expiries.pop((body, projected), None)
            (self.projected if projected else self.active).pop(body, None)

    @staticmethod
    def _ability(instance, projected):
        return instance.states.get(instance.graph.node(21 if projected else 3).state)

    def _record_cooldown(self, body, projected, expiry, frame_ms):
        instance = expiry.duration.instance
        ability = self._ability(instance, projected)
        if ability is None or ability.cur <= 0 or ability.cooldown_rate <= 0:
            return
        self.cooldown_deadlines[body, projected] = self._mapped_cooldown_ready_tick(ability, expiry, frame_ms)

    @staticmethod
    def _mapped_cooldown_ready_tick(ability, expiry, frame_ms):
        from ow174.game.combat import TICK

        # The authored exit writes the cooldown. Map its ready frame back to the
        # world clock even if a command already advanced the linked variable to zero.
        ready_frame = (ability.last + ability.cooldown_ms()) // frame_ms
        elapsed = (ready_frame - expiry.frame) * frame_ms
        return expiry.tick + math.ceil(elapsed / (TICK * 1000))

    def cooldown_ready_tick(self, ability):
        index = ability.instance.graph.index
        if (index, ability.node.pos) not in ((0x0244, 3), (0x025C, 21)):
            return 0
        key = ability.instance.component.entity, index == 0x025C
        expiry = self.expiries.get(key)
        if (expiry is not None and expiry.duration.instance is ability.instance
                and not expiry.duration.active and expiry.duration.counter == expiry.generation):
            script = expiry.owner.body_script
            if script is not None and script.component is ability.instance.component:
                return self._mapped_cooldown_ready_tick(ability, expiry, script.frame_ms)
        return self.cooldown_deadlines.get(key, 0)

    def protects(self, owner, *, tick=None) -> bool:
        return any(self._current_barrier(caster, recipient, projected, tick=tick) is not None
                   for caster, recipient, projected in self._candidates(owner))

    def intercept(self, owner, amount: float, source: int, *, tick=None) -> bool:
        """Consume one hostile damage event while the authored personal pet is active.

        Reconstructed policy: the event that breaks the barrier is blocked in full;
        only remaining barrier HP contributes to absorbed-damage energy. A broken
        activation cannot refill until a different authored pet activation starts.
        """
        return any(
            self._intercept(caster, amount, source, recipient=recipient, projected=projected, tick=tick)
            for caster, recipient, projected in self._candidates(owner)
        )

    def _candidates(self, owner):
        if getattr(getattr(owner, "hero", None), "guid", None) == HERO:
            yield owner, owner.body, False
        target = owner.entity if isinstance(owner, bots.Bot) else owner.body
        for caster in self.combat.match.players:
            if (not caster.has_body or caster.team != owner.team
                    or self.combat.match.game_map.free_for_all
                    or getattr(caster.hero, "guid", None) != HERO):
                continue
            script = caster.body_script
            if script is None:
                continue
            instance = next((i for i in script.component.instances.values()
                             if i.graph.index == 0x025C), None)
            if instance is None:
                continue
            selected = instance.vars.get(536)
            if selected is not None and selected.value() == expr.Entity(target):
                yield caster, target, True

    def _current_barrier(self, owner, recipient, projected, *, tick=None):
        script = owner.body_script
        if script is None or script.component.owner_dead:
            return None
        expiry = self.expiries.get((owner.body, projected))
        contact_tick = self._contact_tick(tick)
        if expiry is not None and not expiry.started <= contact_tick <= expiry.tick:
            return None
        instance = next((i for i in script.component.instances.values()
                         if i.graph.index == (0x025C if projected else 0x0244)), None)
        if instance is None:
            return None
        pet = instance.state(10 if projected else 2)
        storage = self.projected if projected else self.active
        if not pet.active:
            if (expiry is None or expiry.duration.instance is not instance
                    or pet.ended_at != expiry.duration.ended_at
                    or not expiry.ended_ahead or self.combat.tick > expiry.tick):
                storage.pop(owner.body, None)
                return None
            # The world processes the expiry frame before commands for that frame.
            if tick is None and contact_tick == expiry.tick:
                return None
        activation = (instance.id, pet.counter, pet.start)
        barrier = storage.get(owner.body)
        if barrier is None or barrier.activation != activation:
            pool = graph.graph(0x0256).node(0).field("m_amount")
            capacity = expr.to_float(pet.evaluate(pool, watch=False))
            barrier = storage[owner.body] = Barrier(activation, max(0.0, capacity), recipient)
        return (instance, barrier) if barrier.remaining > 0 else None

    def _contact_tick(self, tick):
        return max(self.combat.tick, self.combat.match.tick) if tick is None else tick

    def _intercept(
        self, owner, amount: float, source: int, *, recipient: int, projected: bool, tick=None,
    ) -> bool:
        current = self._current_barrier(owner, recipient, projected, tick=tick)
        if current is None or amount <= 0:
            return False
        instance, barrier = current
        script = owner.body_script
        absorbed = min(amount, barrier.remaining)
        barrier.remaining -= absorbed
        expiry = self.expiries.get((owner.body, projected))
        now = script.component.now
        hit_tick = self._contact_tick(tick)
        hit_frame = expiry.frame + hit_tick - expiry.tick if expiry is not None else script.frame
        at = max(now, hit_frame * script.frame_ms)
        advance_energy(script, hit_tick)
        # Deliver the authored cap and decay restart when the barrier absorbs the hit,
        # even when the owner's body has stopped sending input.
        entry = graph.graph(0x0242).node(5)
        message = graph.guid(entry.field("m_gameMessage.m_gameMessage"))
        runtime.send_message(script.component, message,
                             {0x2A: absorbed, 0x12: expr.Entity(source)}, owner.body, at=now)
        script.component.run_now()
        energy = next(i for i in script.component.instances.values() if i.graph.index == 0x0242)
        wait = energy.state(energy.graph.node(7).state)
        from ow174.game.combat import TICK

        tick_ms = round(TICK * 1000)
        wait.world_deadline = hit_tick + (wait.ms + tick_ms - 1) // tick_ms
        duration = instance.state(11 if projected else 1)
        if not duration.active:
            if barrier.remaining <= 0 and expiry is not None and expiry.ended_ahead:
                ability = self._ability(instance, projected)
                if ability is not None and ability.last >= duration.ended_at:
                    # Keep the authored cooldown value, but start it at the earlier breaking contact.
                    ability.set_cooldown_start(hit_frame * script.frame_ms + ability.last - duration.ended_at)
        elif barrier.remaining <= 0:
            # Reconstruction: exhaustion ends the parent duration State, following
            # its authored StopAbility/cooldown branch at the hit's mapped frame.
            duration.request_finish(at - now)
            if expiry is not None:
                frame = min(expiry.frame, max(script.frame + 1, hit_frame))
                expiry.tick += frame - expiry.frame
                expiry.frame = frame
        return True
