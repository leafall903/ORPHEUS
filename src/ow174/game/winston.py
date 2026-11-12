"""Winston's narrow production bridges for missing body engine services.

104319 uses a LogicalButton subtree in 0109, rather than an Ability state.
Its active HealthPool st4 is the lifetime of the additional 500 health.
This is a narrow production bridge, not a replacement for engine services.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr, nodes, runtime

HERO = 0x02E0000000000009
PRIMAL_GRAPH = 0x0109
PRIMAL_HEALTH = 4
CHARGE_GRAPH = 0x0B8B
ROADHOG_BODY = 0x040000000000028F  # Original STUTargetTagComponent includes 011E.06C.
PVE_CHARGE_TAG = 0x0D600000000003C3
ROADHOG_CHARGE_TAG = 0x0D6000000000011E


class _DamageChargeContext(runtime.InstanceContext):
    def __init__(self, instance, amount, target, tags):
        super().__init__(instance, None)
        self.amount = amount
        self.target = expr.Entity(target)
        self.tags = tags

    def var(self, scope, var):
        if scope == expr.INSTANCE:
            if var == 336:
                return self.amount
            if var == 393:
                return self.target
        return super().var(scope, var)

    def native(self, cfg):
        if cfg.get('$') == 'STU_25ECB55F':
            subject = expr.evaluate(cfg.get('m_entity'), self)
            return subject == self.target and expr.guid_of(cfg.get('m_88B0050A')) in self.tags
        return super().native(cfg)


def charge_instance(script):
    """Normal sandbox eligibility bridge; original mode/filter services remain open.

    Missing2426 uses a live sandbox body's eligibility. Explicit false is never
    replaced. Other scalar gates and the arithmetic are read from original0B8B.
    This does not implement012's native damage-target/tag/difficulty pipeline.
    """
    if (script is None or script.hero != HERO or script.retired or script.component.owner_dead
            or primal_active(script.component)):
        return None
    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == CHARGE_GRAPH), None)
    if instance is None:
        return None
    eligibility = component.entity_var(2426, create=False)
    channel = component.entity_var(842, create=False)
    if ((eligibility is not None and eligibility.value() is not None
         and not expr.truthy(eligibility.value())) or (channel is not None and expr.truthy(channel.value()))):
        return None
    if not all(expr.truthy(instance.evaluate(instance.graph.node(n).field('m_condition')))
               for n in (56, 61)):
        return None
    return instance


def _pve_charge_tag(target_script, target):
    """Project only the authored 0E62 tag state onto its declared target."""
    if target_script is None:
        return False
    for instance in target_script.component.instances.values():
        state = instance.states.get(7) if instance.graph.index == 0x0E62 else None
        if state is None or not state.active or state.node.cls != 'STU_E56BA8D8':
            continue
        tag = state.evaluate(state.node.field('m_targetTags'), watch=False)
        subject = state.evaluate(state.node.field('m_31714AFC'), watch=False)
        if tag == expr.Asset(PVE_CHARGE_TAG) and expr.to_entity(subject) == target:
            return True
    return False


def charge_damage(script, amount, target, body_definition, target_script):
    """Execute0B8B/node23 with actual admitted enemy-body health loss."""
    if not math.isfinite(amount) or amount <= 0:
        return
    instance = charge_instance(script)
    if instance is None:
        return
    blocked = script.component.entity_var(12321, create=False)
    if blocked is not None and expr.truthy(blocked.value()):
        return
    tags = set()
    if _pve_charge_tag(target_script, target):
        tags.add(PVE_CHARGE_TAG)
    if body_definition == ROADHOG_BODY:
        tags.add(ROADHOG_CHARGE_TAG)
    context = _DamageChargeContext(instance, amount, target, tags)
    if any(expr.truthy(expr.evaluate(instance.graph.node(position).field('m_condition'), context))
           for position in (5, 17)):
        return  # These original branches have no charge path for a marked target.
    for condition, action in ((59, 60), (97, 98)):
        if expr.truthy(expr.evaluate(instance.graph.node(condition).field('m_condition'), context)):
            context.amount = expr.evaluate(instance.graph.node(action).field('m_value'), context)
            break
    value = expr.evaluate(instance.graph.node(23).field('m_value'), context)
    script.set_ult_charge(expr.to_float(value))


def charge_passive(script, elapsed_ms):
    """Original one-second/5-point arithmetic on an explicit server cadence.

    The remainder is reset on ineligibility/cap. Positive ult_seconds enables
    authored charging; it is no longer a Winston fill-time setting. Native mode
    eligibility is reconstructed as documented in charge_instance.
    """
    if type(elapsed_ms) is not int or elapsed_ms < 0:
        raise ValueError('invalid Winston passive-charge elapsed time')
    instance = charge_instance(script)
    component = script.component
    blocked = component.entity_var(11942, create=False)
    charge = expr.to_float(component.entity_var(342).value())
    cost = expr.to_float(component.entity_var(338).value())
    if (instance is None or script.ult_seconds <= 0 or (blocked and expr.truthy(blocked.value()))
            or cost <= 0 or charge >= cost):
        script.winston_charge_ms = 0
        return
    if expr.truthy(instance.evaluate(instance.graph.node(63).field('m_condition'))):
        script.winston_charge_ms = 0  # explicit original mode11002 suppression
        return
    authored = instance.states.get(instance.graph.node(3).state)
    external = authored is not None and component.world.external_wait(authored)
    if authored is not None and authored.active and not external:
        script.winston_charge_ms = 0  # an active original timer must not receive a second award
        return
    interval = round(expr.to_float(instance.evaluate(instance.graph.node(3).field('m_timeout'))) * 1000)
    if interval <= 0:
        raise ValueError('invalid authored Winston passive-charge interval')
    periods, script.winston_charge_ms = divmod(script.winston_charge_ms + elapsed_ms, interval)
    for _ in range(periods):
        before = component.entity_var(342).value()
        if authored is not None and authored.active and external:
            authored.end(True)  # original node4 writes342 and restarts original Wait3
            component.run_now()
        else:
            script.set_ult_charge(expr.to_float(instance.evaluate(instance.graph.node(4).field('m_value'))))
        after = component.entity_var(342).value()
        if expr.to_float(after) >= cost:
            script.winston_charge_ms = 0
            break
        if after == before:
            break  # zero rate discards elapsed pulses, retaining only the subsecond phase


def advance_primal_world(player, fight):
    """Tick only0109/node25 on the world clock, retaining its original exit path.

    Owner command time stays unchanged. The authored ChaseVar, its variable215
    and onEnd/finished actions remove the original health/modifier scopes. The
    cached activation/deadline clips passive credit across a skipped world span.
    This is a scoped server-clock reconstruction, not native clock equivalence.
    """
    script = player.body_script
    if script.retired or script.component.owner_dead:
        return None
    health = primal_health(script.component)
    if health is None:
        return None
    instance = health.instance
    chase = instance.states.get(instance.graph.node(25).state)
    if chase is None or not chase.active:
        return None
    activation = (instance.id, health.counter, health.start)
    clock = script.winston_primal_clock
    if clock is None or clock[:3] != activation:
        rate = expr.to_float(chase.evaluate(chase.node.field('m_9B0385B4'), watch=False))
        if rate <= 0:
            return None
        # The server observes completed states on16ms boundaries. Cache from
        # the untouched original start/current value before the first world tick.
        remaining = max(0.0, expr.to_float(chase.cur) - expr.to_float(chase.destination())) / rate
        deadline = chase.last + math.ceil(remaining * 1000 / 16) * 16
        clock = script.winston_primal_clock = (*activation, deadline)
    chase.tick()
    script.component.run_now()
    fight._winston_health(player, script)
    return clock[3] if not primal_active(script.component) else None


def charge_world(player, fight):
    """Advance bound Winston cadence once per world tick, including input silence."""
    script = player.body_script
    if script is None or script.hero != HERO or script.world.match is not fight.match:
        return
    previous = script.winston_charge_tick
    if previous is not None and fight.tick <= previous:
        return
    script.winston_charge_tick = fight.tick
    ended = advance_primal_world(player, fight)
    if previous is None or not player.spawned or not player.has_body:
        script.winston_charge_ms = 0
        return
    start_ms = previous * 16
    if ended is not None:
        start_ms = max(start_ms, ended)
    charge_passive(script, max(0, fight.tick * 16 - start_ms))


def primal_health(component):
    for instance in component.instances.values():
        if instance.graph.index == PRIMAL_GRAPH:
            state = instance.states.get(PRIMAL_HEALTH)
            if state is not None and state.active:
                return state
    return None


def reload_pending(script):
    """Allow idle command catch-up only while Tesla's authored reload gate is active."""
    if script.hero != HERO or script.component.owner_dead:
        return False
    weapon = next((i for i in script.component.instances.values() if i.graph.index == 0x00FE), None)
    gate = weapon.vars.get(51) if weapon is not None else None
    return gate is not None and expr.truthy(gate.value()) and not primal_active(script.component)


def primal_active(component) -> bool:
    return primal_health(component) is not None


@dataclass
class Bubble:
    owner: object
    body: int
    centre: tuple
    health: float
    expires: float
    radius: float = 5.0  # DATA: D02.00C MSTU sphere ED61D926, referenced by 01C3.003.

    def ray(self, origin, direction):
        offset = tuple(a - b for a, b in zip(origin, self.centre, strict=True))
        projection = sum(a * b for a, b in zip(offset, direction, strict=True))
        c = sum(value * value for value in offset) - self.radius * self.radius
        discriminant = projection * projection - c
        if discriminant < 0:
            return None
        root = math.sqrt(discriminant)
        if c < -1e-8:
            return -projection + root  # from inside: only crossing the exit blocks
        entry = -projection - root
        if entry < -1e-8 or (entry <= 1e-8 and projection >= 0):
            return None  # outward/tangent at a surface is not a crossing
        return max(0.0, entry)

    def damage(self, amount):
        lost = min(self.health, max(0.0, amount))
        self.health -= lost
        return lost


class Barriers:
    """Combat-side sphere actors; native entity/graph replication remains open.

    DATA: radius5 in D02's dynamic sphere (wrapper motion1), graph00FA HP700,
    duration9 in entity var567, and 50ms deployment -> cooldown13. Ordinary
    static-map collision does not include this dynamic shape. Sphere crossings,
    no damage spill, owner-team pass-through and placement at feet+.1 are
    reconstructed services awaiting native comparison.
    """

    def __init__(self, combat):
        self.combat = combat
        self.actors = {}
        self.seen = {}

    def retire(self, body):
        self.actors.pop(body, None)
        self.seen.pop(body, None)

    def update(self):
        for body, actor in tuple(self.actors.items()):
            if actor.health <= 0 or self.combat.now >= actor.expires:
                self.actors.pop(body, None)

    def pending_release(self, player, tick_ms):
        script = player.body_script
        if (player.gone or not player.spawned or player.hero.guid != HERO or script is None
                or script.world_frame_anchor is None or self.combat.is_dead(player)):
            return
        for instance in script.component.instances.values():
            deployment = instance.states.get(3) if instance.graph.index == 0x00FA else None
            if isinstance(deployment, nodes.Wait) and deployment.active:
                # The VM processes timer events throughout the command frame.
                frame = (deployment.start + deployment.ms) // script.frame_ms
                at, anchor = script.world_frame_anchor
                return at + math.ceil((frame - anchor) * script.frame_ms / tick_ms), frame

    def release(self, player, now):
        if player.hero.guid != HERO or player.body_script is None or self.combat.is_dead(player):
            return
        for instance in player.body_script.component.instances.values():
            if instance.graph.index != 0x00FA:
                continue
            ability = instance.states.get(2)
            if not isinstance(ability, nodes.Ability) or ability.cur <= 0:
                continue
            deployment = instance.states.get(3)
            # State 1 starts through the effect action on state 3's finish branch.
            # An aborted deployment also has an end time but never enters that branch.
            effect_wait = instance.states.get(1)
            if (deployment is None or deployment.active or effect_wait is None
                    or effect_wait.start != deployment.ended_at):
                continue
            token = (instance.id, deployment.counter)
            if self.seen.get(player.body) == token:
                continue
            self.seen[player.body] = token
            health = expr.to_float(instance.find_var(0, 587).value())
            duration = expr.to_float(instance.component.entity_var(567).value())
            x, y, z = player.mover.position
            if health > 0 and duration > 0:
                self.actors[player.body] = Bubble(player, player.body, (x, y + .1, z), health,
                                                  now + duration)

    def snapshot(self):
        self.update()
        return tuple(self.actors.values())

    def first(self, shooter, origin, direction, length, *, snapshot=None):
        actors = self.snapshot() if snapshot is None else snapshot
        found = None
        for actor in actors:
            if actor.owner is shooter:
                continue
            if not self.combat.match.game_map.free_for_all and actor.owner.team == shooter.team:
                continue
            distance = actor.ray(origin, direction)
            if distance is not None and distance <= length and (found is None or distance < found[0]):
                found = distance, actor
        return found

    def hit_capsule(self, player, a, b, radius, amount, origin):
        """Reconstruct melee volume contact, independently of body penetration."""
        from ow174.game import combat

        for barrier in self.snapshot():
            if barrier.owner is player or (not self.combat.match.game_map.free_for_all
                                           and barrier.owner.team == player.team):
                continue
            near = combat.segment_distance(barrier.centre, a, b)
            far = max(math.dist(barrier.centre, a), math.dist(barrier.centre, b))
            if near > barrier.radius + radius or far < barrier.radius - radius:
                continue
            ab = combat._sub(b, a)
            length2 = combat._dot(ab, ab)
            along = (max(0.0, min(1.0, combat._dot(combat._sub(barrier.centre, a), ab) / length2))
                     if length2 > combat.EPSILON else 0.0)
            query = combat._along(a, ab, along)
            normal = combat._sub(query, barrier.centre)
            normal_length = math.sqrt(combat._dot(normal, normal))
            if normal_length > combat.EPSILON:
                contact = combat._along(barrier.centre, normal, barrier.radius / normal_length)
                sight = combat._sub(contact, origin)
                distance = math.sqrt(combat._dot(sight, sight))
                if distance > combat.EPSILON:
                    wall = combat.ray_world(self.combat._collision(), origin,
                                            tuple(v / distance for v in sight), distance)
                    if wall is not None and wall < distance - combat.EPSILON:
                        continue
            barrier.damage(amount * _outgoing(self.combat, player))


class HostedBubble:
    """Read-through view of an existing canonical world actor; no second pool."""

    def __init__(self, fight, actor):
        self.fight, self.actor = fight, actor

    owner = property(lambda self: self.actor.owner)
    body = property(lambda self: self.actor.body)
    centre = property(lambda self: self.actor.point)
    radius = property(lambda self: self.actor.radius)
    health = property(lambda self: self.actor.health)
    ray = Bubble.ray

    def damage(self, amount):
        return self.fight.barriers.world.damage(self.actor, amount)


class HostedBarriers(Barriers):
    """Use the delivered queries on already mounted canonical bubble actors."""

    def snapshot(self):
        manager = self.combat.barriers.world
        return tuple(HostedBubble(self.combat, actor) for actor in manager.actors.values()
                     if not actor.retired and actor.component is not None and actor.health > 0
                     and manager.live_source(actor))


def _outgoing(fight, player):
    from ow174.game import nano

    return nano.offense(fight, player.body)


def _wall_blocks(fight, origin, point, radius):
    delta = tuple(b - a for a, b in zip(origin, point, strict=True))
    length = math.hypot(*delta)
    if length <= radius:
        return False
    return fight.walls.cast(origin, tuple(v / length for v in delta), length - radius) is not None


def _hit_walls(fight, player, origin, a, b, radius, amount, *, falloff=False, excluded=()):
    from ow174.game import combat, mei_wall

    centre = tuple((x + y) * .5 for x, y in zip(a, b, strict=True))
    for piece in tuple(fight.walls.pieces.values()):
        if piece.entity in excluded:
            continue
        point = mei_wall.closest(piece, centre)
        distance = combat.segment_distance(point, a, b)
        if distance > radius:
            continue
        delta = combat._sub(point, origin)
        length = math.hypot(*delta)
        if length > combat.EPSILON:
            ray = tuple(v / length for v in delta)
            obstruction = combat.ray_world(fight._collision(), origin, ray, length)
            if obstruction is not None and obstruction < length - combat.EPSILON:
                continue
        scalar = max(0., 1 - distance / radius) if falloff and radius > 0 else 1.
        fight.walls.damage(piece, player, amount * scalar)


def tesla_walls(fight, shooter, origin, direction, reach, angle, amount, barriers, snapshot, *, excluded=()):
    from ow174.game import combat, mei_wall

    admitted = []
    slope = math.tan(math.radians(angle))
    for piece in fight.walls.pieces.values():
        if piece.entity in excluded:
            continue
        offset = combat._sub(mei_wall.closest(piece, origin), origin)
        length = math.hypot(*offset)
        if length > reach:
            continue
        if length > combat.EPSILON:
            forward = combat._dot(offset, direction)
            lateral = math.sqrt(max(0., length**2 - forward**2))
            if forward < 0 or lateral > forward * slope:
                continue
            ray = tuple(v / length for v in offset)
            wall = combat.ray_world(fight._collision(), origin, ray, length)
            if wall is not None and wall < length - combat.EPSILON:
                continue
            nearer = fight.walls.cast(origin, ray, max(0., length - combat.EPSILON))
            if nearer is not None and nearer[1] is not piece:
                continue
            if barriers.first(shooter, origin, ray, length, snapshot=snapshot):
                continue
        admitted.append(piece)
    for piece in admitted:
        fight.walls.damage(piece, shooter, amount)


class JumpPack:
    """Reconstructed physics driven by the authored cast branch.

    010A's accepted Ability starts Wait st26 alongside the movement state
    and the six/two-second cooldown write. Its start identifies the cast
    even when the existing movement state is still active in the air.
    Native launch-angle interpretation, impact falloff and impulses remain
    inferred. No strict native VM behavior is changed by this bridge.
    """

    def __init__(self, combat):
        self.combat = combat
        self.seen = {}
        self.flights = {}

    def retire(self, body):
        self.seen.pop(body, None)
        self.flights.pop(body, None)

    def cancel(self, body):
        flight = self.flights.pop(body, None)
        if flight is not None:
            self.stop(flight[0])

    @staticmethod
    def stop(instance):
        state = instance.states.get(2)
        if state is not None and state.active:
            state.end(False)
            instance.component.run_now()

    def command(self, player, command, *, dead=False):
        if player.hero.guid != HERO:
            return
        if dead:
            self.cancel(player.body)
            return
        if command is None:
            return
        component = player.body_script.component
        flight = self.flights.get(player.body)
        if flight is not None and command.frame > flight[1] and player.mover.landed:
            self.impact(player, flight[0], command.frame, landing=True)
            self.stop(flight[0])
            self.flights.pop(player.body, None)
        for instance in component.instances.values():
            if instance.graph.index != 0x010A:
                continue
            cast = instance.states.get(26)
            if not isinstance(cast, nodes.Wait) or cast.counter == 0:
                continue
            token = (instance.id, cast.start)
            if self.seen.get(player.body) == token:
                continue
            self.seen[player.body] = token
            old = self.flights.pop(player.body, None)
            if old is not None:
                self.stop(old[0])
            self.launch(player, instance, command)
            self.flights[player.body] = (instance, command.frame)

    def launch(self, player, instance, command):
        from ow174.game.mover import FELL, JUMPED, REAL_JUMP

        state = instance.state(2)
        if not state.active:
            # An accepted air recast can have reentered the old active subtree.
            # After retiring that flight, give this cast a fresh effect lifetime.
            state.begin()
            instance.component.run_now()
        # Resource node85 supplies 90/25 degree bounds; legacy initialization
        # retains speed30 in var1262. Interpret bounds as elevation above ground.
        config = instance.graph.node(85).field('m_value')['m_configVars'][0]
        lower = expr.to_float(instance.evaluate(config['m_E26E5E09']))
        upper = expr.to_float(instance.evaluate(config['m_5023B9B0']))
        speed = expr.to_float(instance.find_var(0, 1262).value())
        yaw, pitch = command.aim()
        elevation = math.radians(max(lower, min(upper, -pitch * 360 / 65536)))
        heading = yaw * 2 * math.pi / 65536
        horizontal = speed * math.cos(elevation)
        mover = player.mover
        mover.velocity = (math.sin(heading) * horizontal, speed * math.sin(elevation),
                          math.cos(heading) * horizontal)
        mover.flags_word = mover.flags_word & ~FELL | JUMPED | REAL_JUMP
        mover.air_ticks = 0
        mover.landed = False
        self.impact(player, instance, command.frame, landing=False)

    def impact(self, player, instance, frame, *, landing):
        # Local import avoids the Combat -> Winston -> Combat import cycle.
        from ow174.game import combat

        search = instance.state(4 if landing else 6)
        definition = instance.state(3 if landing else 7)
        radius = expr.to_float(search.evaluate(search.node.field('m_radius'), watch=False))
        modify = definition.node.field('m_modifyHealth')
        amount = (expr.to_float(instance.find_var(0, 476).value()) if landing
                  else expr.to_float(definition.evaluate(modify['m_amount'], watch=False)))
        loadout = definition.evaluate(modify['m_loadout'], watch=False)
        centre = tuple(player.mover.position)
        origin = (centre[0], centre[1] + .1, centre[2])
        barriers = HostedBarriers(self.combat)
        barrier_snapshot = barriers.snapshot()
        damaged_barriers = set()
        damaged_bodies = set()
        for target in self.combat._enemies(player):
            a, b = target.segment()
            distance = max(0.0, combat.segment_distance(centre, a, b) - target.radius)
            if distance > radius:
                continue
            point = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            delta = combat._sub(point, origin)
            length = math.sqrt(combat._dot(delta, delta))
            if length > combat.EPSILON:
                ray = tuple(value / length for value in delta)
                wall = combat.ray_world(self.combat._collision(), origin, ray, length)
                if wall is not None and wall < max(0.0, length - target.radius):
                    continue
                if _wall_blocks(self.combat, origin, point, target.radius):
                    continue
            scalar = max(0.0, 1.0 - distance / radius) if landing else 1.0
            if length > combat.EPSILON:
                blocked = barriers.first(player, origin, ray, length, snapshot=barrier_snapshot)
                if blocked is not None:
                    _, barrier = blocked
                    if barrier.body not in damaged_barriers:
                        barrier.damage(amount * scalar * _outgoing(self.combat, player))
                        damaged_barriers.add(barrier.body)
                    continue
            reaction_direction = None
            if landing:
                reaction_direction = delta if delta[0] or delta[2] else combat.aim(player.mover.yaw, 0)
            affected = self.combat._damage(player, target, amount * scalar, False,
                                          loadout.guid if isinstance(loadout, expr.Asset) else None, frame,
                                          hit_reaction=landing)
            if affected:
                damaged_bodies.add(target.entity)
            if affected and not self.combat.vitals_of(target).dead:
                self.knockback(player, target, definition, scalar)
        _hit_walls(self.combat, player, origin, centre, centre, radius,
                   amount * _outgoing(self.combat, player), falloff=landing, excluded=damaged_bodies)

    def knockback(self, player, target, definition, scalar):
        from ow174.game import fortify

        if fortify.control_immune(target.owner):
            return
        horizontal = expr.to_float(definition.evaluate(
            definition.node.field('m_modifyHealth.m_E33EFEE1'), watch=False)) * scalar
        vertical = expr.to_float(definition.evaluate(
            definition.node.field('m_modifyHealth.m_583C208B'), watch=False)) * scalar
        dx, dz = (target.feet[0] - player.mover.position[0],
                  target.feet[2] - player.mover.position[2])
        distance = math.hypot(dx, dz)
        x, z = ((dx / distance, dz / distance) if distance else (0., 0.))
        self.combat.knockbacks.apply(target, expr.Vec3(x * horizontal, vertical, z * horizontal))


class PrimalPunches:
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

    def retire(self, body):
        self.seen.pop(body, None)
        self.frames.pop(body, None)

    def interrupt(self, script):
        component = script.component
        if script.hero != HERO or not (component.owner_dead or primal_active(component)):
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

    def command(self, player, command):
        if player.hero.guid != HERO or command is None:
            return
        if command.frame <= self.frames.get(player.body, -1):
            return
        self.frames[player.body] = command.frame
        component = player.body_script.component
        if component.owner_dead or not primal_active(component):
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
        from ow174.game import combat

        direction = combat.aim(*command.aim())
        heading = command.aim()[0] * 2 * math.pi / 65536
        right = (math.cos(heading), 0, -math.sin(heading))
        feet = tuple(player.mover.position)

        def point(field):
            value = search.evaluate(search.node.field(field), watch=False)
            return tuple(feet[k] + right[k] * value.x + direction[k] * value.z
                         + (value.y if k == 1 else 0) for k in range(3))

        a, b = point('m_05BE62DD'), point('m_B3A77CD0')
        radius = expr.to_float(search.evaluate(search.node.field('m_radius'), watch=False))
        definition = combat.hit_def(health, {'m_modifyHealth': health.node.field('m_modifyHealth')})
        origin = self.combat._eye(player)
        damaged_bodies = set()
        for target in self.combat._enemies(player):
            bottom, top = target.segment()
            middle = (a[1] + b[1]) / 2
            closest = (target.feet[0], max(bottom[1], min(top[1], middle)), target.feet[2])
            if combat.segment_distance(closest, a, b) > radius + target.radius:
                continue
            delta = combat._sub(closest, origin)
            distance = math.sqrt(combat._dot(delta, delta))
            if distance > combat.EPSILON:
                ray = tuple(v / distance for v in delta)
                wall = combat.ray_world(self.combat._collision(), origin, ray, distance)
                if wall is not None and wall < max(0, distance - target.radius):
                    continue
                if _wall_blocks(self.combat, origin, closest, target.radius):
                    continue
            # Reconstructed melee semantics: body strikes may pass barriers;
            # shield contact/damage is evaluated separately below.
            from ow174.game import deflections

            if deflections.blocks_melee(self.combat, player, target, health, health.node.field('m_modifyHealth')):
                continue
            affected = self.combat._damage(player, target, definition.amount, False, definition.loadout,
                                          command.frame, hit_reaction=True)
            if affected:
                damaged_bodies.add(target.entity)
            if affected and not self.combat.vitals_of(target).dead:
                self.combat.winston_jump.knockback(player, target, health, 1.0)
        HostedBarriers(self.combat).hit_capsule(player, a, b, radius, definition.amount, origin)
        _hit_walls(self.combat, player, origin, a, b, radius,
                   definition.amount * _outgoing(self.combat, player), excluded=damaged_bodies)
