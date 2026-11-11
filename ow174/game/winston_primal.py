"""Server health and melee contact for Winston's authored Primal Rage scopes."""

import math

from ow174.game import contacts
from ow174.game.script import expr
from ow174.game.winston_barrier import HERO


def health_state(script):
    if script.hero != HERO:
        return None
    for instance in script.component.instances.values():
        if instance.graph.index == 0x0109:
            state = instance.states.get(4)
            if state is not None and state.active:
                return state
    return None


def active(script):
    return health_state(script) is not None


class PrimalRage:
    """Scoped reconstruction of 00FE's missing 54FEB21C windup/target service.

    DATA: alternating Wait st15/16 has 0.65s cycles; windup st20/21 names .25s,
    each feeds original ModifyHealth st36/35 with40 damage and25/7 knockback.
    We end only these Winston windups after their authored time, then consume
    the real damage activation once. Local swept capsule, one damage sample and
    the interpretation of windup/impulse semantics require native comparison.
    """

    def __init__(self, combat):
        self.combat = combat
        self.seen = {}
        self.frames = {}
        self.bonus = {}
        self.expiries = {}

    def retire(self, body):
        self.seen.pop(body, None)
        self.frames.pop(body, None)
        self.bonus.pop(body, None)
        self.expiries.pop(body, None)

    def update(self, tick_ms):
        """Run the authored exit when an older command window expires idle."""
        from ow174.game.projectiles import MAX_LEASE_FRAME

        for player in self.combat.match.players:
            clock = self.expiries.get(player.body)
            if clock is None or player.body_script is None:
                continue
            at, frame, deadline = clock
            script = player.body_script
            # Leave the current command window open for real input.
            available = frame + (self.combat.tick - at) * tick_ms // script.frame_ms - 1
            target = max(deadline, script.frame + 1)
            if target <= available and target < MAX_LEASE_FRAME:
                player._advance_body_gap(target + 1)

    def interrupt(self, script):
        component = script.component
        if script.hero != HERO or component.strict:
            return
        for instance in tuple(component.instances.values()):
            if instance.graph.index != 0x00FE:
                continue
            for index in (20, 21):
                state = instance.states.get(index)
                if state is not None and state.active:
                    state.end(False)
            # The windup's shared end plug queues its damage phase even on abort.
            component.run_now()
            for index in (35, 36):
                state = instance.states.get(index)
                if state is not None and state.active:
                    state.end(False)

    def sync_health(self, player, script, *, ending=False):
        if script.hero != HERO or script.component.strict:
            return
        from ow174.game.combat import HEALTH_POOL

        state = None if ending else health_state(script)
        extra = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False)) if state else 0.0
        old = self.bonus.get(player.body, 0.0)
        if old == extra:
            return
        fight = self.combat
        target = next((t for t in fight._targets() if t.owner is player), None)
        if target is None:
            return
        vitals = fight.vitals_of(target)
        if vitals.dead:
            extra = 0.0
        vitals.most[HEALTH_POOL] += extra - old
        vitals.now[HEALTH_POOL] = min(vitals.now[HEALTH_POOL], vitals.most[HEALTH_POOL])
        vitals.changed.add(HEALTH_POOL)
        if extra > old:
            vitals.heal(sum(vitals.most.values()))
        if extra:
            self.bonus[player.body] = extra
            instance = state.instance
            chase = instance.states.get(instance.graph.node(25).state)
            if chase is not None and chase.active:
                rate = expr.to_float(chase.evaluate(chase.node.field("m_9B0385B4"), watch=False))
                if rate > 0:
                    remaining = abs(expr.to_float(chase.cur) - expr.to_float(chase.destination()))
                    # The VM runs through the end of each command frame.
                    deadline = int((chase.last + remaining * 1000 / rate) // script.frame_ms)
                    self.expiries[player.body] = fight.tick, script.frame, deadline
        else:
            self.bonus.pop(player.body, None)
            self.expiries.pop(player.body, None)
            self.interrupt(script)
        fight._sync_body_health(player, vitals)

    def command(self, player, command):
        if player.hero.guid != HERO or command is None or self.combat.match.ended:
            return
        if command.frame <= self.frames.get(player.body, -1):
            return
        self.frames[player.body] = command.frame
        script = player.body_script
        component = script.component
        if component.owner_dead or component.strict or not active(script):
            return
        for instance in tuple(component.instances.values()):
            if instance.graph.index != 0x00FE:
                continue
            for index in (20, 21):
                state = instance.states.get(index)
                if state is not None and state.active:
                    duration = expr.to_float(state.evaluate(state.node.field('m_ED97A97C'), watch=False))
                    if component.now - state.start >= round(duration * 1000):
                        state.end(True)
                        component.run_now()
            for health_index, search_index in ((35, 18), (36, 17)):
                health = instance.states.get(health_index)
                search = instance.states.get(search_index)
                if health is None or search is None or not (health.active and search.active):
                    continue
                key = (instance.id, health_index, health.counter)
                seen = self.seen.setdefault(player.body, set())
                if key in seen:
                    continue
                # Keep only the current activation of each of the two fists.
                seen.difference_update(item for item in tuple(seen) if item[:2] == key[:2])
                seen.add(key)
                self.hit(player, command, health, search)

    def hit(self, player, command, health, search):
        from ow174.game import combat, sigma

        direction = combat.aim(*command.aim())
        heading = command.aim()[0] * math.tau / 65536
        right = (math.cos(heading), 0, -math.sin(heading))
        feet = tuple(player.mover.position)

        def point(field):
            value = search.evaluate(search.node.field(field), watch=False)
            return tuple(feet[k] + right[k] * value.x + direction[k] * value.z
                         + (value.y if k == 1 else 0) for k in range(3))

        a, b = point("m_05BE62DD"), point("m_B3A77CD0")
        radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
        definition = combat.hit_def(health, {"m_modifyHealth": health.node.field("m_modifyHealth")})
        if definition is None or definition.amount <= 0 or radius <= 0:
            return
        fight = self.combat
        origin, collision = fight._eye(player), fight._collision()
        for target in fight._enemies(player):
            bottom, top = target.segment()
            reach = radius + target.radius
            if (contacts.gjk((a, b), (bottom, top), reach=reach).distance > reach
                    or not combat.capsule_visible(collision, origin, bottom, top, target.radius)):
                continue
            vitals = fight.vitals_of(target)
            previous = vitals.total
            fight._damage(player, target, definition.amount, False, definition.loadout, command.frame)
            if vitals.total < previous:
                sigma.apply_impact_impulse(fight, direction, target, definition.knockback, feet)

        for field in sigma.hostile_barriers(fight, player):
            if isinstance(field, sigma.Barrier) and mesh_contact(field, a, b, radius, collision, origin):
                sigma.damage_barrier(fight, field, definition.amount)


def mesh_contact(field, a, b, radius, collision, origin):
    """Query the authored strike capsule against the barrier's loaded triangles."""
    from ow174.game.combat import capsule_visible

    local = tuple(
        tuple(sum((point[k] - field.position[k]) * axis[k] for k in range(3)) for axis in field.basis)
        for point in (a, b)
    )
    data = field.geometry.data
    low = tuple(min(p[k] for p in local) - radius for k in range(3))
    high = tuple(max(p[k] for p in local) + radius for k in range(3))
    for i in range(0, len(data), 13):
        corners = tuple(tuple(data[j:j + 3]) for j in range(i, i + 9, 3))
        if any(max(v[k] for v in corners) < low[k] or min(v[k] for v in corners) > high[k]
               for k in range(3)):
            continue
        hit = contacts.gjk(corners, local, reach=radius)
        if hit.distance <= radius:
            point = tuple(field.position[k] + sum(axis[k] * v for axis, v in
                          zip(field.basis, hit.point_a, strict=True)) for k in range(3))
            if capsule_visible(collision, origin, point, point, 0):
                return True
    return False
