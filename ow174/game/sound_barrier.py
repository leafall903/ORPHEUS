"""Combat host for 073A Sound Barrier and its 0191/073B receiver."""

import math
from dataclasses import dataclass, field

from ow174.game import actors
from ow174.game.mover import IN_AIR
from ow174.game.script import expr, graph, runtime
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E0000000000079
GRAPH = 0x073A
RECEIVER = 0x073B
POOL_KEY = ("sound_barrier", RECEIVER)
CAPACITY = 750.0  # 073B node1 sets #499, consumed by the receiver HealthPool.


@dataclass
class Shield:
    application: runtime.Instance
    at: float
    rate: float


@dataclass
class Landing:
    ready: float
    grounded_at: float | None


@dataclass
class Wave:
    until: float
    radius: float
    duration: float
    recipients: set[int] = field(default_factory=set)


def landing_state(player):
    if getattr(getattr(player, "hero", None), "guid", None) != HERO:
        return None
    script = player.body_script
    if script is None or script.retired or script.component.strict or script.component.owner_dead:
        return None
    if script.component.fault is not None:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == GRAPH:
            state = instance.states.get(14)  # Node30 owns the landing transition.
            if state is not None and state.active and not state.ending:
                return state
    return None


class SoundBarriers:
    def __init__(self, fight):
        self.fight = fight
        self.landings = {}
        self.waves = {}
        self.shielded = {}

    def observe(self, player):
        state = landing_state(player)
        if state is None:
            return
        landing = self.landings.get(player.body)
        grounded = not player.mover.flags_word & IN_AIR
        if landing is None:
            windup = expr.to_float(state.instance.find_var(0, 1322).value())
            if math.isfinite(windup) and windup > 0:
                now = self.fight.now
                self.landings[player.body] = Landing(now + windup, now if grounded else None)
        elif not grounded:
            landing.grounded_at = None
        elif landing.grounded_at is None:
            landing.grounded_at = self.fight.now

    def interrupt(self, entity):
        self.landings.pop(entity, None)

    def retire(self, entity):
        self.interrupt(entity)
        self.waves.pop(entity, None)
        self.remove_shield(entity)

    def remove_shield(self, entity):
        shield = self.shielded.pop(entity, None)
        if shield is None:
            return
        vitals = self.fight.vitals.get(entity)
        if vitals is not None and POOL_KEY in vitals.extra:
            vitals.remove_temporary(POOL_KEY)
        application = shield.application
        if application.component.instances.get(application.id) is application:
            application.component.destroy(application)

    def update(self):
        from ow174.game.combat import TICK

        if not (self.landings or self.waves or self.shielded):
            return
        fight = self.fight
        targets = {target.entity: target for target in fight._targets()}
        for entity, shield in tuple(self.shielded.items()):
            vitals = fight.vitals.get(entity)
            target = targets.get(entity)
            pool = vitals.extra.get(POOL_KEY) if vitals is not None else None
            if vitals is None or vitals.dead or target is None or pool is None or pool.removed:
                self.remove_shield(entity)
                continue
            elapsed = max(0.0, fight.now - shield.at)
            if elapsed or pool.now == 0:
                pool.now = max(0.0, pool.now - elapsed * shield.rate)
                shield.at = fight.now
                vitals.changed.add(SHIELDS_POOL)
                if pool.now < 1e-9:
                    self.remove_shield(entity)
                else:
                    shield.application.write((graph.INSTANCE, 199), pool.now)
                fight._sync_body_health(target.owner, vitals)
        for entity, landing in tuple(self.landings.items()):
            player = self.player(entity)
            state = landing_state(player)
            if (state is None or fight.is_dead(player) or fight.is_asleep(player)
                    or fight.is_stunned(player) or entity in fight.sombra_controls.locks):
                del self.landings[entity]
            elif landing.grounded_at is not None:
                gate = max(landing.ready, landing.grounded_at + TICK)
                if fight.now >= gate:
                    del self.landings[entity]
                    # Snap the missed landing to its original world tick so
                    # a scheduler jump cannot extend the temporary pool.
                    skipped = math.floor((fight.now - gate + 1e-9) / TICK)
                    at = fight.now - skipped * TICK
                    self.release(player, state, at, targets.values())
        for entity, wave in tuple(self.waves.items()):
            player = self.player(entity)
            if player is None or fight.is_dead(player) or fight.now >= wave.until:
                del self.waves[entity]
            else:
                self.grant(player, wave, targets.values(), fight.now)

    def player(self, entity):
        return next((p for p in self.fight.match.players if p.has_body and p.body == entity and not p.gone), None)

    def release(self, player, state, at, targets):
        instance = state.instance
        graph = instance.graph
        radius = expr.to_float(instance.evaluate(graph.node(16).field("m_radius")))
        window = graph.node(1)
        initial = expr.to_float(instance.evaluate(window.field("m_EB0FC261")))
        rate = expr.to_float(instance.evaluate(window.field("m_9B0385B4")))
        if not all(math.isfinite(value) and value > 0 for value in (radius, initial, rate)):
            return
        # The server mover supplies the selected landing predicate. Keep the
        # authored success links and effect phase intact.
        instance.follow(graph.node(32), "m_truePlug", state)
        state.request_finish()
        instance.component.run_now()
        pulse = instance.states.get(0)
        duration = expr.to_float(instance.component.entity_var(1212).value())
        if pulse is not None and pulse.active and math.isfinite(duration) and duration > 0:
            wave = Wave(at + initial / rate, radius, duration)
            self.grant(player, wave, targets, at)
            if self.fight.now < wave.until:
                self.waves[player.body] = wave

    def tracked_targets(self, state):
        player = self.player(state.instance.component.entity)
        if player is None or self.fight.match.collision_world is None:
            return ()
        radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
        return tuple(expr.Entity(target.entity)
                     for target in self.eligible(player, radius, self.fight._targets()))

    def eligible(self, player, radius, targets):
        from ow174.game.combat import capsule_visible, segment_distance

        if not math.isfinite(radius) or radius <= 0:
            return
        fight = self.fight
        centre = fight._eye(player)
        for target in targets:
            if isinstance(target.owner, actors.Deployable) or target.owner.body_script is None:
                continue
            if target.owner is not player and (fight.match.game_map.free_for_all or target.team != player.team):
                continue
            vitals = fight.vitals_of(target)
            if vitals.dead or fight.replacement_ticks.get(target.entity, fight.tick) > fight.tick:
                continue
            if target.owner is not player:
                a, b = target.segment()
                if (segment_distance(centre, a, b) - target.radius > radius
                        or not capsule_visible(fight._collision(), centre, a, b, target.radius,
                                               walls=fight.walls)):
                    continue
            yield target

    def grant(self, player, wave, targets, at):
        fight = self.fight
        definition = graph.graph(RECEIVER)
        if definition is None:
            return
        for target in self.eligible(player, wave.radius, targets):
            if target.entity in wave.recipients:
                continue
            wave.recipients.add(target.entity)
            vitals = fight.vitals_of(target)
            component = target.owner.body_script.component
            receiver = component.create(
                definition, values={(graph.INSTANCE, 27): expr.Entity(player.body)},
            )
            runtime.send_instance_message(receiver, 0x024000000000000F,
                                          {0x2B: wave.duration}, player.body)
            receiver.update()
            state = receiver.states.get(2)
            if state is None or not state.active:
                component.destroy(receiver)
                continue
            capacity = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
            rate = capacity / wave.duration
            amount = capacity - max(0.0, fight.now - at) * rate
            if not math.isfinite(amount) or amount <= 0:
                component.destroy(receiver)
                continue
            self.remove_shield(target.entity)
            pool = vitals.add_temporary(POOL_KEY, RECEIVER, SHIELDS_POOL, capacity, fight.tick)
            if pool is None:
                component.destroy(receiver)
                continue
            pool.now = amount
            receiver.write((graph.INSTANCE, 199), amount)
            self.shielded[target.entity] = Shield(receiver, fight.now, rate)
            fight._sync_body_health(target.owner, vitals)
