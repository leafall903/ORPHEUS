"""104319 Minefield bridge using selected external mine graphs.

World-up cone formation, point collision/first-contact settling and server-owned
entity IDs are explicit reconstructions. Native bounce/muzzle/prediction and
complete target/status semantics are not accepted by this bridge.
"""

import math
from dataclasses import dataclass, field

from ow174.game import observers, scalar_math, world
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import ReplicatedBody, create_body_graphs

BODY = 0x0400000000002578
HERO = 0x02E00000000001CA
IMPACT = 0x02400000000005D4
NORMAL = 0x0A40000000000411
FIRST, LIMIT = 0xB1000000, 15  # pet component m_739DD5BF=15; replacement order is reconstructed
TICK = 0.016
FLIGHT_EPSILON = 2 ** -23


def flight_step(position, velocity, gravity, delta):
    """D71A70 + D70454: ungated gravity then normal-dt/unit-scale position.

    Each SSE multiply/subtract/add rounds separately. Clock/scale production,
    alternate motion flags and collision response are outside this bridge.
    """
    delta = expr.f32(delta)
    vx, vy, vz = map(expr.f32, velocity)
    velocity = vx, expr.f32(vy - expr.f32(expr.f32(gravity) * delta)), vz
    movement = tuple(expr.f32(v * delta) for v in velocity)
    endpoint = tuple(expr.f32(expr.f32(p) + d) for p, d in zip(position, movement, strict=True))
    return velocity, movement, endpoint


def flight_distance(previous, current):
    """5E2610 SSE order using the host RSQRTSS approximation before Newton refinement."""
    squared = [expr.f32(expr.f32(expr.f32(b) - expr.f32(a)) ** 2)
               for a, b in zip(previous, current, strict=True)]
    total = expr.f32(expr.f32(squared[1] + squared[0]) + squared[2])
    if not total > 0:
        return 0.0
    seed = scalar_math.reciprocal_sqrt(total)
    correction = expr.f32(3 - expr.f32(total * expr.f32(seed * seed)))
    return expr.f32(total * expr.f32(correction * expr.f32(seed * 0.5)))


def clip_flight(previous, old_velocity, position, velocity, lifetime, reach, age, travelled, delta):
    """D72C80 strict time/travel bounds and separate float32 interpolation."""
    lifetime, reach, age, travelled, delta = map(expr.f32, (lifetime, reach, age, travelled, delta))
    distance = flight_distance(previous, position)
    fraction = 1.0
    if lifetime >= FLIGHT_EPSILON:
        if age > lifetime:
            fraction = 0.0
        elif expr.f32(age + delta) > lifetime:
            fraction = expr.f32(expr.f32(lifetime - age) / delta)
    if travelled > reach:
        fraction = 0.0
    elif expr.f32(distance + travelled) > reach:
        fraction = min(fraction, expr.f32(expr.f32(reach - travelled) / distance))
    if fraction >= 1:
        return position, velocity, delta, distance, False

    def interpolate(before, after):
        return tuple(expr.f32(expr.f32(a) + expr.f32(expr.f32(expr.f32(b) - expr.f32(a)) * fraction))
                     for a, b in zip(before, after, strict=True))

    return (interpolate(previous, position), interpolate(old_velocity, velocity),
            expr.f32(delta * fraction), expr.f32(distance * fraction), True)


@dataclass
class Mine:
    entity: int
    owner: object
    source: int
    team: int
    position: tuple
    velocity: tuple
    health: float
    gravity: float
    reach: float
    flight_lifetime: float
    reported_frame: int
    born_tick: int
    last_tick: int
    travelled: float = 0.0
    landed: bool = False
    removing: bool = False
    triggered: bool = False
    normal: tuple = (0.0, 1.0, 0.0)
    body_script: object = None
    viewers: dict = field(default_factory=dict)

    @property
    def body(self):
        return self.entity

    @property
    def armed(self):
        if not self.landed:
            return False
        search = self.body_script.primary().states.get(28)
        return search is not None and search.active


class MineWorld(runtime.World):
    def __init__(self, mine, mode):
        super().__init__(HERO, mode)
        self.mine = mine

    @property
    def body(self):
        return self.mine.entity

    @property
    def match(self):
        return self.mine.owner.match

    def play_compatible_script(self, instance, node):
        from ow174.game import sombra_emp

        sombra_emp.route(self, instance, node)

    def destroy_entity(self, instance, node):
        if instance.graph.index != 0x151A or node.pos not in (9, 26, 43):
            return NotImplemented
        self.mine.removing = True
        return True

    def track_targets(self, state):
        instance = state.instance
        node = state.node
        if instance.graph.index != 0x151A or node.pos != 50:
            return super().track_targets(state)
        targets = self.match.combat.mines.visible_targets(self.mine, instance, node)
        output = expr.lvalue(node.field('m_8A2905A0'))
        instance.write(output, tuple(expr.Entity(target.entity) for target in targets))
        return False

    def source_script(self):
        script = self.mine.owner.body_script
        return script if script is not None and script.component.entity == self.mine.source else None

    def position(self, entity):
        if entity == self.mine.entity:
            return expr.Vec3(*self.mine.position)
        return super().position(entity)

    def native(self, cfg, instance):
        if instance is not None and instance.graph.index == 0x0B1F:
            if cfg.get('$') == 'STU_25ECB55F':
                return (cfg.get('m_88B0050A') == '0x0D60000000000626'
                        and instance.evaluate(cfg.get('m_entity')) == expr.Entity(self.mine.entity))
            if cfg is instance.graph.node(19).field('m_condition'):
                source = expr.to_entity(instance.evaluate(cfg['m_entity']))
                return any(p.body == source and p.has_body and not p.gone for p in self.match.players)
        if cfg.get('$') == 'STU_9B7648C3':
            return self.mine.health
        if cfg.get('$') == 'STU_2B2E2F6D':
            # The actual mode0/self query used by151A. Native F66C80 emits
            # an EntityID (tag0A), not a variable map or numeric rule key.
            entity = instance.evaluate(cfg.get('m_81A364DE'))
            if cfg.get('m_7BDB02D1') == 0 and entity == expr.Entity(self.mine.entity):
                # CCFCC0 resolves the selected entity before F66C80 emits its
                # EntityID. A retired source cannot alias its owner's new body.
                return expr.Entity(self.mine.source if self.source_script() is not None else 0)
            return super().native(cfg, instance)
        if cfg.get('$') == 'STU_D32169F6':
            return expr.Vec3(*self.mine.normal)  # reconstructed landed orientation
        return super().native(cfg, instance)

    def ruleset_for_entity(self, entity, key):
        script = self.source_script()
        if entity == self.mine.source and script is not None:
            return script.component.world.ruleset(key)
        return None

    def has_script_entity(self, entity):
        if entity == self.mine.source and self.source_script() is not None:
            return True
        return super().has_script_entity(entity)

    def entity_var(self, entity, var):
        script = self.source_script() if entity == self.mine.source else None
        if script is None and var == 9588:
            from ow174.game import combat

            # A frozen body VM can retain the stack past the world-clock deadline.
            fight = self.match.combat
            for target in fight._body_targets():
                if target.entity == entity and fight._has_protection(target, combat.CRYO_STATES):
                    script = target.owner.body_script
                    break
        if script is not None:
            found = script.component.entity_var(var, create=False)
            return found.value() if found is not None else None
        return super().entity_var(entity, var)


class MineScript(ReplicatedBody):
    def __init__(self, mine, mode, frame):
        self.frame = frame
        self.death_frame = None
        self.component = runtime.Component(mine.entity, world=MineWorld(mine, mode), seed=mine.entity)
        self.component.frame = frame
        self.component.now = frame * self.component.frame_ms
        self.set_health(mine.health, mine.health)
        record = graph.entity_body(BODY)
        if record is None or any(graph.graph(row['graph']) is None for row in record['graphs']):
            raise ValueError('Minefield needs explicitly selected mine body/151A/261E graph data')
        for instance in create_body_graphs(self.component, record):
            instance.init()
        self.component.run_frame(frame)
        self.server_only = self.initial_exclusions()
        self.latest_live = observers.snapshot(self)

    @property
    def initial(self):
        return observers.full_frame(self, self.latest_live), self.latest_live

    def primary(self):
        return next(i for i in self.component.instances.values() if i.graph.index == 0x151A)

    def advance(self, frame):
        from ow174.game import sombra_emp

        # Preserve deadline chains across scheduler gaps: running every overdue
        # timer at the endpoint would start each fade wait late at that endpoint.
        while self.frame < frame:
            times = [event.time for instance in self.component.instances.values()
                     for event in instance.queue if event.time > self.component.now]
            due = min(times, default=frame * self.component.frame_ms)
            at = min(frame, max(self.frame + 1, math.ceil(due / self.component.frame_ms)))
            self.frame = at
            self.component.run_frame(at)
            sombra_emp.expire_mine_effects(self.component)
            if self.component.world.mine.removing:
                break
        snap = observers.snapshot(self)
        old = self.latest_live
        changed = (snap.instances, snap.vars, snap.states) != (old.instances, old.vars, old.states)
        self.latest_live = snap
        return snap if changed else None

    def impact(self, normal):
        runtime.send_message(self.component, IMPACT, {NORMAL: expr.Vec3(*normal)}, self.component.entity)
        self.component.run_now()


class Minefield:
    def __init__(self, combat):
        self.combat = combat
        self.entities = {}
        self.next_entity = FIRST
        self.seen = set()

    def volley(self, player, state, number, frame):
        """Consume one actual Q volley report, expanding its authored ring count."""
        if player.hero.guid != HERO or state.instance.graph.index != 0x1517:
            return False
        definition = state.node.field('m_projectileEntity') or {}
        asset = state.evaluate(definition.get('m_entityDef'), watch=False)
        if not isinstance(asset, expr.Asset) or asset.guid != BODY:
            return False
        key = player.body, state.instance.id, state.index, state.counter, number
        if key in self.seen:
            return True
        # Fail visibly for incomplete data instead of silently consuming the cast.
        if graph.entity_body(BODY) is None or graph.graph(0x151A) is None:
            raise ValueError('Minefield requires the external mine graph bundle')
        self.seen.add(key)
        def evaluate(value):
            return state.evaluate(value, watch=False)
        count = expr.to_int(evaluate(state.node.field('m_numProjectilesPerShot')))
        speed = expr.to_float(evaluate(state.node.field('m_projectileSpeed')))
        muzzle = state.node.field('m_5EC1EB38') or {}
        offset = evaluate(muzzle.get('m_E2FD6F04'))
        angle = expr.to_float(evaluate(muzzle.get('m_spread')))
        motions = state.node.field('m_projectileMotions') or []
        gravity = expr.to_float(evaluate(motions[0].get('m_599AB8D7'))) if motions else 0.0
        reach = expr.to_float(evaluate(state.node.field('m_FEC435C6')))
        lifetime = expr.to_float(evaluate(state.node.field('m_projectileLifetime')))
        if (not 0 < count <= LIMIT or not isinstance(offset, expr.Vec3) or speed <= 0
                or not all(math.isfinite(v) for v in (gravity, reach, lifetime))):
            raise ValueError('unsupported Minefield formation/motion inputs')
        theta = math.radians(angle)
        yaw = player.mover.yaw * 2 * math.pi / 65536
        for index in range(count):
            old = [m for m in self.entities.values() if m.source == player.body]
            if len(old) >= LIMIT:
                self.remove(min(old, key=lambda m: m.entity))
            phi = yaw + 2 * math.pi * index / count
            velocity = tuple(map(expr.f32, (
                speed * math.sin(theta) * math.sin(phi), speed * math.cos(theta),
                speed * math.sin(theta) * math.cos(phi),
            )))
            position = tuple(expr.f32(expr.f32(p) + o)
                             for p, o in zip(player.mover.position, offset, strict=True))
            entity = self.next_entity
            self.next_entity += 1
            if self.next_entity >= FIRST + 0x01000000:
                raise ValueError('Minefield entity allocator exhausted')
            primary = graph.graph(0x151A)
            health = expr.to_float(primary.node(0).field('m_amount')['m_value'])
            # Reports use command time; these server-owned entities advance on
            # world time even when several future command poses arrive together.
            cast_at = self.combat.match.tick
            mine = Mine(entity, player, player.body, player.team, position, velocity, health,
                        gravity, reach, lifetime, frame, cast_at, cast_at)
            mine.body_script = MineScript(mine, self.combat.match.game_map.mode_guid, cast_at)
            self.entities[entity] = mine
            self.combat.record_replacement(entity)
            self.deliver(mine)
        return True

    def deliver(self, mine):
        for viewer in self.combat.match.players:
            if (viewer.client is None or not viewer.spawned
                    or mine.viewers.get(viewer.slot) is viewer.client):
                continue
            mine.viewers[viewer.slot] = viewer.client
            # Freeze the create baseline; retransmission cannot change its pose/pools.
            position = mine.position
            components = world.health(mine.health, 50)
            components[26] = [0x800000 << mine.team]
            viewer.client.queue_entities([world.EntityUpdate(
                mine.entity, world.OP_CREATE,
                lambda origin, position=position, components=components: world.create(
                    origin, BODY, components, position=position,
                ),
            )])

    def update(self, tick):
        collision = self.combat._collision()
        for mine in list(self.entities.values()):
            if tick < mine.last_tick:
                raise ValueError('Minefield world clock moved backwards')
            script = mine.body_script
            if not mine.landed:
                for at in range(mine.last_tick + 1, tick + 1):
                    previous, old_velocity = mine.position, mine.velocity
                    velocity, _, endpoint = flight_step(
                        mine.position, mine.velocity, mine.gravity, TICK,
                    )
                    # Default world clock/unit scale bridge. Native alternate
                    # age/clock sources remain unqualified; command report time
                    # is never the physics birth time.
                    age = expr.f32(expr.f32(at - mine.born_tick - 1) * expr.f32(TICK))
                    endpoint, mine.velocity, _, distance, expired = clip_flight(
                        previous, old_velocity, endpoint, velocity, mine.flight_lifetime,
                        mine.reach, age, mine.travelled, TICK,
                    )
                    delta = tuple(p - old for p, old in zip(endpoint, previous, strict=True))
                    # Geometric sweep/first-contact settling is reconstructed;
                    # its normalization is separate from native travel math.
                    length = math.sqrt(sum(v * v for v in delta))
                    hit = (collision.cast_ray(mine.position, tuple(v / length for v in delta), length)
                           if length > 0 else None)
                    if hit is not None:
                        mine.travelled = expr.f32(mine.travelled + flight_distance(previous, hit.point))
                        mine.position = tuple(hit.point)
                        mine.normal = tuple(hit.normal)
                        mine.velocity = (0.0, 0.0, 0.0)
                        mine.landed = True
                        script.advance(at)
                        script.impact(hit.normal)
                        break
                    mine.position = endpoint
                    mine.travelled = expr.f32(mine.travelled + distance)
                    if expired:
                        # D74EC0 zeroes velocity after terminal callbacks. Body
                        # removal/transport remains the bridge's retirement policy.
                        mine.velocity = (0.0, 0.0, 0.0)
                        mine.removing = True
                        break
            mine.last_tick = tick
            if mine.removing:
                self.remove(mine)
                continue
            snap = script.advance(tick)
            self.trigger(mine, tick)
            if mine.removing:
                self.remove(mine)
                continue
            self.deliver(mine)
            for viewer in self.combat.match.players:
                if viewer.client is not None and mine.viewers.get(viewer.slot) is viewer.client:
                    viewer.client.queue_entities([world.EntityUpdate(
                        mine.entity, movement=world.Movement(mine.position, velocity=mine.velocity),
                    )])
                    if snap is not None:
                        viewer.client.queue_bot_live(mine.entity, snap)

    def visible_targets(self, mine, instance, node, *, centre=None):
        from ow174.game import combat

        if centre is None:
            centre = instance.evaluate(node.field('m_05BE62DD'))
        if not isinstance(centre, expr.Vec3):
            return []
        radius = expr.to_float(instance.evaluate(node.field('m_radius')))
        condition = node.field('m_filter')['m_condition']
        collision = self.combat._collision()
        targets = []
        for target in self.combat._enemies(mine.owner):
            if isinstance(target.owner, Mine):
                continue  # mine-chain/filter interactions remain unverified
            a, b = target.segment()
            if combat.segment_distance(tuple(centre), a, b) - target.radius > radius:
                continue
            if not combat.capsule_visible(collision, tuple(centre), a, b, target.radius):
                continue
            with instance.component.parameter_scope({8: expr.Entity(target.entity)}):
                if expr.truthy(instance.evaluate(condition)):
                    targets.append(target)
        return targets

    def trigger(self, mine, tick):
        """Reconstructed capsule/LOS proximity -> actual explosion graph activation."""
        if mine.triggered or mine.health <= 0 or mine.removing or not mine.armed:
            return
        primary = mine.body_script.primary()
        from ow174.game import barriers, combat

        node = primary.graph.node(52)
        centre = primary.evaluate(node.field('m_05BE62DD'))
        count = len(self.visible_targets(mine, primary, node, centre=centre))
        if not count:
            return
        # Node 52 activates node 50, which publishes damage targets before node 8 begins.
        search = primary.node_state(node)
        blast = primary.state(5)
        before = blast.counter
        primary.write(expr.lvalue(node.field('m_1B89EA79')), count)
        search.try_complete()
        primary.component.run_now()
        if blast.counter == before:
            return
        mine.triggered = True
        modify = blast.node.field('m_modifyHealth')
        amount = expr.to_float(primary.evaluate(modify['m_amount']))
        loadout = primary.evaluate(modify['m_loadout'])
        horizontal = expr.to_float(primary.evaluate(modify['m_E33EFEE1']))
        vertical = expr.to_float(primary.evaluate(modify['m_583C208B']))
        targets = {target.entity: target for target in self.combat._enemies(mine.owner)}
        for entity in primary.evaluate(blast.node.field('m_FA3ECC43')) or ():
            target = targets.pop(expr.to_entity(entity), None)
            if target is None:
                continue
            if self.combat.barriers and barriers.protect_body(
                self.combat, target, amount, attacker=mine.source, source_scale=1.0,
            ):
                continue
            self.combat._damage(
                mine.owner, target, amount, False, loadout.guid, tick, world_tick=tick, source_scale=1.0,
            )
            if (not self.combat.vitals_of(target).dead
                    and not self.combat._has_protection(target, combat.CRYO_STATES)
                    and (horizontal > 0 or vertical > 0)):
                self.combat._knock_up(target, vertical)
                actor = target.owner
                from ow174.game import bots

                mover = (self.combat.npc_impulses[target.entity][0]
                         if isinstance(actor, bots.Bot) else actor.mover)
                dx, dz = target.feet[0] - centre.x, target.feet[2] - centre.z
                distance = math.hypot(dx, dz)
                if distance > 1e-9:
                    vx, vy, vz = mover.velocity
                    mover.velocity = vx + horizontal * dx / distance, vy, vz + horizontal * dz / distance
                    if isinstance(actor, bots.Bot):
                        actor.velocity = mover.velocity

    def remove(self, mine):
        if self.entities.pop(mine.entity, None) is None:
            return
        mine.removing = True
        self.combat.vitals.pop(mine.entity, None)
        self.combat.history.pop(mine.entity, None)
        self.combat.replacement_ticks.pop(mine.entity, None)
        for contacts in self.combat.ball_contacts.values():
            contacts.discard(mine.entity)
        for instance in list(mine.body_script.component.instances.values()):
            mine.body_script.component.destroy(instance)
        for viewer in self.combat.match.players:
            if viewer.client is not None and mine.viewers.get(viewer.slot) is viewer.client:
                viewer.client.queue_entities([world.EntityUpdate(mine.entity, world.OP_DESTROY)])

    def retire(self, body):
        for mine in list(self.entities.values()):
            if mine.source == body:
                self.remove(mine)
        self.seen = {key for key in self.seen if key[0] != body}
