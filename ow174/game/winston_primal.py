"""Host combat for Winston's authored Primal Rage pool and fist states."""

import math
from dataclasses import dataclass

from ow174.game import assassin, interception, winston_jump
from ow174.game.script import expr, nodes
from ow174.game.world import HEALTH_POOL


def health_state(script):
    if getattr(script, "hero", None) != winston_jump.HERO:
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x0109), None)
    state = instance.states.get(4) if instance is not None else None
    return state if state is not None and state.active else None


def weapon(script):
    return next((i for i in script.component.instances.values() if i.graph.index == 0x00FE), None)


@dataclass
class Expiry:
    owner: object
    frame: int
    tick: int


class Primal:
    def __init__(self, fight):
        self.fight = fight
        self.seen = {}
        self.bonus = {}
        self.expiries = {}

    def retire(self, body):
        self.seen.pop((body, 35), None)
        self.seen.pop((body, 36), None)
        self.bonus.pop(body, None)
        self.expiries.pop(body, None)

    def schedule_expiry(self, player, script, health):
        from ow174.game.combat import TICK
        from ow174.game.projectiles import MAX_LEASE_FRAME

        instance = health.instance
        chase = instance.states.get(instance.graph.node(25).state)
        if not isinstance(chase, nodes.ChaseVar) or not chase.active:
            return
        rate = expr.to_float(chase.evaluate(chase.node.field("m_9B0385B4"), watch=False))
        if rate <= 0:
            return
        remaining = round(abs(expr.to_float(chase.cur) - expr.to_float(chase.destination())) * 1000 / rate)
        frame = (chase.last + remaining - 1) // script.frame_ms
        if script.frame < frame < MAX_LEASE_FRAME:
            elapsed = (frame - script.frame) * script.frame_ms
            tick = self.fight.tick + math.ceil(elapsed / (TICK * 1000))
            self.expiries[player.body] = Expiry(player, frame, tick)

    def clock_edges(self, tick):
        # Resolve in-flight impacts on both sides of the timer in sparse updates.
        return {edge for expiry in self.expiries.values()
                for edge in (expiry.tick, expiry.tick + 1, expiry.tick + 2)
                if self.fight.tick < edge <= tick}

    def advance_expiries(self):
        for body, expiry in tuple(self.expiries.items()):
            owner = expiry.owner
            script = owner.body_script
            if (self.fight.match.ended or owner.gone or not owner.has_body or owner.body != body
                    or script is None or script.retired or self.fight.is_dead(owner)):
                self.expiries.pop(body, None)
            elif self.fight.tick > expiry.tick:
                # Give real input the expiry frame, then use the normal neutral gap path.
                # The next frame also covers float rounding in the authored chase.
                frame = expiry.frame + self.fight.tick - expiry.tick - 1
                owner._advance_body_gap(frame + 1)

    def sync(self, player, script):
        if script is None or script.hero != winston_jump.HERO:
            return
        target = next((t for t in self.fight._targets() if t.owner is player), None)
        if target is None:
            return
        vitals = self.fight.vitals_of(target)
        state = health_state(script)
        extra = (expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
                 if state is not None and not vitals.dead else 0.0)
        prior = self.bonus.get(player.body, 0.0)
        if prior == extra:
            return
        vitals.most[HEALTH_POOL] += extra - prior
        vitals.now[HEALTH_POOL] = min(vitals.now[HEALTH_POOL], vitals.most[HEALTH_POOL])
        vitals.changed.add(HEALTH_POOL)
        if extra > prior:
            vitals.heal(vitals.maximum)
        if extra:
            self.bonus[player.body] = extra
            self.schedule_expiry(player, script, state)
        else:
            self.bonus.pop(player.body, None)
            self.expiries.pop(player.body, None)
        self.fight._sync_body_health(player, vitals)

    def interrupt(self, script):
        if health_state(script) is None:
            return
        instance = weapon(script)
        if instance is None:
            return
        for index in (20, 21):
            state = instance.states.get(index)
            if state is not None and state.active:
                state.end(False)
        instance.component.run_now()
        # The shared windup exit can queue a damage state even on abort.
        for index in (35, 36):
            state = instance.states.get(index)
            if state is not None and state.active:
                state.end(False)

    def command(self, player, command, script):
        if health_state(script) is None or command.frame != script.frame:
            return
        instance = weapon(script)
        if instance is None:
            return
        for index in (20, 21):
            state = instance.states.get(index)
            if state is None or not state.active:
                continue
            duration = expr.to_float(state.evaluate(state.node.field("m_ED97A97C"), watch=False))
            if script.component.now - state.start >= round(duration * 1000):
                state.end(True)
                script.component.run_now()
        for damage_index, search_index in ((35, 18), (36, 17)):
            damage = instance.states.get(damage_index)
            search = instance.states.get(search_index)
            if damage is None or search is None or not (damage.active and search.active):
                continue
            key = player.body, damage_index
            activation = instance.id, damage.counter, damage.start
            if self.seen.get(key) != activation:
                self.seen[key] = activation
                self.hit(player, command, damage, search)

    def hit(self, player, command, damage, search):
        from ow174.game import combat

        yaw, pitch = command.aim()
        forward = combat.aim(yaw, pitch)
        heading = yaw * math.tau / 65536
        right = (math.cos(heading), 0, -math.sin(heading))
        feet = player.mover.position
        points = []
        for name in ("m_05BE62DD", "m_B3A77CD0"):
            value = search.evaluate(search.node.field(name), watch=False)
            if not isinstance(value, expr.Vec3):
                return
            points.append(tuple(feet[k] + right[k] * value.x + forward[k] * value.z
                                + (value.y if k == 1 else 0) for k in range(3)))
        a, b = points
        radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
        modify = damage.node.field("m_modifyHealth")
        hit = combat.hit_def(damage, {"m_modifyHealth": modify})
        if hit is None or hit.amount <= 0 or radius <= 0:
            return
        horizontal = expr.to_float(damage.evaluate(modify.get("m_E33EFEE1"), watch=False))
        vertical = expr.to_float(damage.evaluate(modify.get("m_583C208B"), watch=False))
        fight = self.fight
        eye = fight._eye(player)
        volumes = ((a, b, radius),)
        scaled = hit.amount * fight.offense_scale(player.body)
        fight.reinhardt_barriers.melee(player, volumes, scaled)
        for target in fight._enemies(player):
            c, d = target.segment()
            body = (combat.segment_pair_distance(a, b, c, d) <= radius + target.radius
                    and fight.combat_visible(eye, c, d, target.radius, exclude=player))
            if not body:
                if scaled > 0 and fight._melee_bubble(player, eye, target, volumes):
                    fight.zarya_barriers.intercept(target.owner, scaled, player.body)
                continue
            if fight.zarya_barriers.intercept(target.owner, scaled, player.body):
                continue
            if interception.blocks_melee(target, forward):
                continue
            if (fight._damage(player, target, hit.amount, False, hit.loadout, command.frame)
                    and not fight.vitals_of(target).dead):
                fight.radial_impulse(target, feet, horizontal, vertical)
                assassin.interrupt_pin(fight, target)
