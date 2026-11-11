"""104319 Minefield bridge using selected external mine graphs.

World-up cone formation, point collision, wall sliding, and server-owned
entity IDs are explicit reconstructions. Native bounce/muzzle/prediction and
complete target/status semantics are not accepted by this bridge.
"""

import math
from dataclasses import dataclass, field

from ow174.game import defenses, observers, world
from ow174.game.collision import MOVER, RayHit
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.driver import ReplicatedBody, create_body_graphs

BODY = 0x0400000000002578
HERO = 0x02E00000000001CA
IMPACT = 0x02400000000005D4
NORMAL = 0x0A40000000000411
FIRST, LIMIT = 0xB1000000, 15  # pet component m_739DD5BF=15; replacement order is reconstructed
TICK = 0.016
FLIGHT_EPSILON = 2 ** -23
HIT_RADIUS = 0.35
WALL_NORMAL_Y = 0.7
WALL_SKIN = 0.001


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
    """Reconstructed chord length rounded to float32."""
    return expr.f32(math.dist(previous, current))


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


def slide_wall(velocity, hit):
    # The retail Minefield change prevents wall attachment. Removing the
    # inward velocity and adding a separation skin is the server's response.
    inward = min(0.0, sum(v * n for v, n in zip(velocity, hit.normal, strict=True)))
    position = tuple(expr.f32(p + WALL_SKIN * n) for p, n in zip(hit.point, hit.normal, strict=True))
    tangent = tuple(expr.f32(v - inward * n) for v, n in zip(velocity, hit.normal, strict=True))
    return position, tangent


@dataclass
class Mine:
    entity: int
    owner: object
    source: int
    team: int
    position: tuple
    velocity: tuple
    health: float
    maximum: float
    gravity: float
    reach: float
    flight_lifetime: float
    reported_frame: int
    born_tick: int
    last_tick: int
    travelled: float = 0.0
    landed: bool = False
    # Retain the old support ID during a drop so re-landing keeps the same VM lifetime.
    wall_support: int | None = None
    removing: bool = False
    triggered: bool = False
    normal: tuple = (0.0, 1.0, 0.0)
    body_script: object = None
    viewers: dict = field(default_factory=dict)

    @property
    def body(self):
        return self.entity

    def centre(self):
        return tuple(p + n * HIT_RADIUS for p, n in zip(self.position, self.normal, strict=True))

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

    def destroy_entity(self, instance, node):
        if instance.graph.index != 0x151A or node.pos not in (9, 26, 43):
            return NotImplemented
        self.mine.removing = True
        return True

    def play_script_selected(self, instance, node):
        if (instance.graph.index, node.pos) != (0xB32, 6):
            return NotImplemented
        from ow174.game import sombra

        return sombra.invoke(self.mine.owner.match.combat, instance, node)

    def source_script(self):
        script = self.mine.owner.body_script
        return script if script is not None and script.component.entity == self.mine.source else None

    def position(self, entity):
        if entity == self.mine.entity:
            return expr.Vec3(*self.mine.position)
        return super().position(entity)

    def native(self, cfg, instance):
        if instance is not None and instance.graph.index == 0xB1F:
            # 2578's authored tag 0626 selects 0B1F/89's deployable branch.
            condition = instance.graph.node(89).field("m_condition")
            if cfg is condition and graph.guid(cfg.get("m_88B0050A")) == 0x0D60000000000626:
                return instance.evaluate(cfg["m_entity"]) == expr.Entity(self.mine.entity)
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

    def entity_ruleset(self, entity, key):
        script = self.source_script()
        if entity == self.mine.source and script is not None:
            return script.component.world.ruleset(key)
        return super().entity_ruleset(entity, key)

    def script_component_present(self, entity):
        if entity == self.mine.source and self.source_script() is not None:
            return True
        return super().script_component_present(entity)

    def entity_var_slot(self, entity, var, *, create=False):
        script = self.source_script()
        if entity == self.mine.source and script is not None:
            return script.component.entity_var(var, create=create)
        return super().entity_var_slot(entity, var, create=create)


class MineScript(ReplicatedBody):
    def __init__(self, mine, mode, frame):
        self.frame = frame
        self.death_frame = None
        self.component = runtime.Component(mine.entity, world=MineWorld(mine, mode), seed=mine.entity)
        self.component.frame = frame
        self.component.now = frame * self.component.frame_ms
        self.set_health(mine.health, mine.maximum)
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

    def emp_scope(self):
        receiver = next((i for i in self.component.instances.values() if i.graph.index == 0xB1F), None)
        return receiver.states.get(receiver.graph.node(92).state) if receiver is not None else None

    def schedule_emp_end(self):
        # 0B1F/90 writes the deployable duration. The compatibility VM
        # supplies the native scope's missing expiry using its event queue.
        scope = self.emp_scope()
        if scope is not None and scope.active:
            seconds = scope.instance.find_var(expr.INSTANCE, 215).value()
            scope.request_finish(nodes.seconds_to_ms(seconds))

    def advance(self, frame):
        # Preserve deadline chains across scheduler gaps: running every overdue
        # timer at the endpoint would start each fade wait late at that endpoint.
        while self.frame < frame:
            times = [event.time for instance in self.component.instances.values()
                     for event in instance.queue if event.time > self.component.now]
            due = min(times, default=frame * self.component.frame_ms)
            at = min(frame, max(self.frame + 1, math.ceil(due / self.component.frame_ms)))
            self.frame = at
            self.component.run_frame(at)
            scope = self.emp_scope()
            if scope is not None and not scope.active:
                for instance in tuple(self.component.instances.values()):
                    if instance.graph.index in (0xB1F, 0xB32):
                        self.component.destroy(instance)
                self.component.run_now()
            if self.component.world.mine.removing:
                break

    def capture(self):
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

    def volley(self, player, state, number, frame, activation, yaw):
        """Consume one actual Q volley report, expanding its authored ring count."""
        if player.hero.guid != HERO or state.instance.graph.index != 0x1517:
            return False
        definition = state.node.field('m_projectileEntity') or {}
        asset = state.evaluate(definition.get('m_entityDef'), watch=False)
        if not isinstance(asset, expr.Asset) or asset.guid != BODY:
            return False
        key = player.body, state.instance.id, state.index, activation, number
        if key in self.seen:
            return True
        # Fail visibly for incomplete data instead of silently consuming the cast.
        if graph.entity_body(BODY) is None or graph.graph(0x151A) is None:
            raise ValueError('Minefield requires the external mine graph bundle')
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
        yaw *= 2 * math.pi / 65536
        self.seen.add(key)
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
            mine = Mine(entity, player, player.body, player.team, position, velocity, health, health,
                        gravity, reach, lifetime, frame, cast_at, cast_at)
            mine.body_script = MineScript(mine, self.combat.match.game_map.mode_guid, cast_at)
            self.entities[entity] = mine
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
            components = world.health(mine.health, mine.maximum)
            components[26] = [0x800000 << mine.team]
            movement = self.movement(mine)
            viewer.client.queue_entities([world.EntityUpdate(
                mine.entity, world.OP_CREATE,
                lambda origin, position=position, components=components, movement=movement: world.create(
                    origin, BODY, components, position=position, movement=movement,
                ),
            )])

    @staticmethod
    def movement(mine):
        return world.Movement(mine.position, velocity=mine.velocity)

    def movements(self, viewer):
        """Sample current flight poses at send time after the viewer's create ACK."""
        client = viewer.client
        return [
            world.EntityUpdate(mine.entity, movement=self.movement(mine))
            for mine in self.entities.values()
            if not mine.landed and mine.viewers.get(viewer.slot) is client
            and client.bot_create_acked(mine.entity)
        ]

    def flight_contact(self, collision, origin, direction, length):
        hit = collision.cast_ray(origin, direction, length)
        wall = self.combat.walls.cast(origin, direction, length)
        if wall is not None and (hit is None or wall[0] < hit.distance):
            distance, pillar, normal = wall
            # A ray starting inside a newly placed pillar has no entry normal.
            if any(normal):
                point = tuple(p + v * distance for p, v in zip(origin, direction, strict=True))
                return RayHit(distance, point, normal, MOVER, 0), pillar.entity
        return hit, None

    def update(self, tick):
        collision = self.combat._collision()
        pillars = self.combat.walls.pieces
        for mine in list(self.entities.values()):
            if tick < mine.last_tick:
                raise ValueError('Minefield world clock moved backwards')
            script = mine.body_script
            detached = (mine.landed and mine.wall_support is not None and mine.wall_support not in pillars
                        and not mine.triggered and mine.health > 0)
            if detached:
                mine.landed = False
            was_flying = not mine.landed
            if was_flying:
                first_tick = max(mine.last_tick + 1, tick) if detached else mine.last_tick + 1
                for at in range(first_tick, tick + 1):
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
                    # Normalize the geometric sweep independently from the
                    # authored float32 travel calculation.
                    length = math.sqrt(sum(v * v for v in delta))
                    hit, support = None, None
                    if length > 0:
                        direction = tuple(v / length for v in delta)
                        hit, support = self.flight_contact(collision, previous, direction, length)
                    if hit is not None:
                        mine.travelled = expr.f32(mine.travelled + flight_distance(previous, hit.point))
                        if abs(hit.normal[1]) < WALL_NORMAL_Y:
                            mine.position, mine.velocity = slide_wall(mine.velocity, hit)
                            continue
                        mine.position = tuple(hit.point)
                        mine.normal = tuple(hit.normal)
                        mine.velocity = (0.0, 0.0, 0.0)
                        mine.landed = True
                        if mine.wall_support is None:
                            script.advance(at)
                            script.impact(hit.normal)
                        mine.wall_support = support
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
            script.advance(tick)
            self.trigger(mine, tick)
            if mine.removing:
                self.remove(mine)
                continue
            snap = script.capture()
            self.deliver(mine)
            for viewer in self.combat.match.players:
                if viewer.client is not None and mine.viewers.get(viewer.slot) is viewer.client:
                    if detached or (was_flying and mine.landed):
                        self.publish_health(viewer, mine)
                    if snap is not None:
                        viewer.client.queue_bot_live(mine.entity, snap)

    def trigger(self, mine, tick):
        """Reconstructed capsule/LOS and shield contact trigger the authored blast."""
        if mine.triggered or mine.health <= 0 or mine.removing or not mine.armed:
            return
        primary = mine.body_script.primary()
        from ow174.game import combat

        node = primary.graph.node(52)
        centre = primary.evaluate(node.field('m_05BE62DD'))
        if not isinstance(centre, expr.Vec3):
            return
        radius = expr.to_float(primary.evaluate(node.field('m_radius')))
        fight = self.combat
        targets = []
        for target in fight._enemies(mine.owner):
            if defenses.protection(target.owner)[0]:
                continue
            a, b = target.segment()
            if combat.segment_distance(tuple(centre), a, b) - target.radius > radius:
                continue
            if combat.capsule_visible(
                fight._collision(), tuple(centre), a, b, target.radius, walls=fight.walls,
            ):
                midpoint = tuple((p + q) / 2 for p, q in zip(a, b, strict=True))
                delta = tuple(p - q for p, q in zip(midpoint, centre, strict=True))
                distance = math.hypot(*delta)
                cover = (fight._barrier_contact(mine.owner, tuple(centre), tuple(v / distance for v in delta),
                                                distance) if distance else None)
                targets.append((target, cover))
        touching = tuple(fight.touching_energy_barriers(mine.owner, mine.centre(), HIT_RADIUS))
        if not targets and not touching:
            return
        mine.triggered = True
        primary.write((expr.INSTANCE, 375), tuple(expr.Entity(t.entity) for t, _ in targets))
        modify = primary.graph.node(8).field('m_modifyHealth')
        amount = expr.to_float(primary.evaluate(modify['m_amount']))
        loadout = primary.evaluate(modify['m_loadout'])
        horizontal = expr.to_float(primary.evaluate(modify['m_E33EFEE1']))
        vertical = expr.to_float(primary.evaluate(modify['m_583C208B']))
        primary.state(5).request(runtime.BEGIN)
        primary.component.run_now()
        covered = set()
        for target, cover in targets:
            if cover is not None:
                barrier = id(cover[1])
                if barrier not in covered:
                    fight._damage_barrier(cover, amount, shooter=mine.owner, source_body=mine.source)
                    covered.add(barrier)
                continue
            bubble = fight.zarya_bubbles.for_target(target.owner)
            fight._damage(mine.owner, target, amount, False, loadout.guid, tick, world_tick=tick,
                          source_body=mine.source)
            if bubble is None:
                dx, dz = target.feet[0] - centre.x, target.feet[2] - centre.z
                distance = math.hypot(dx, dz)
                scale = horizontal / distance if distance > 1e-9 else 0.0
                fight.impulses.vector(target, expr.Vec3(dx * scale, vertical, dz * scale))
            else:
                covered.add(id(bubble))
        for contact in touching:
            if id(contact[1]) not in covered:
                fight._damage_barrier(contact, amount, shooter=mine.owner, source_body=mine.source)

    def enemies(self, shooter):
        for mine in self.entities.values():
            if (mine.owner is not shooter and not mine.removing and not mine.triggered and mine.health > 0
                    and (self.combat.match.game_map.free_for_all or mine.team != shooter.team)):
                yield mine

    def damage(self, mine, amount):
        if amount <= 0.0 or mine.removing:
            return
        mine.health = expr.f32(max(0.0, mine.health - amount))
        script = mine.body_script
        script.set_health(mine.health, mine.maximum)
        script.component.run_now()
        if mine.removing:
            self.remove(mine)
            return
        for viewer in self.combat.match.players:
            if viewer.client is not None and mine.viewers.get(viewer.slot) is viewer.client:
                self.publish_health(viewer, mine)

    def publish_health(self, viewer, mine):
        # Preserve the latest landing or drop pose when a later pool record
        # supersedes it. The create ACK gates the record.
        viewer.client.queue_body_health(
            mine.entity, world.health(mine.health, mine.maximum),
            movement=self.movement(mine) if mine.landed or mine.wall_support is not None else None,
        )

    def remove(self, mine):
        if self.entities.pop(mine.entity, None) is None:
            return
        mine.removing = True
        for instance in list(mine.body_script.component.instances.values()):
            mine.body_script.component.destroy(instance)
        for viewer in self.combat.match.players:
            if viewer.client is not None and mine.viewers.get(viewer.slot) is viewer.client:
                viewer.client.queue_entities([world.EntityUpdate(mine.entity, world.OP_DESTROY)])

    def forget_viewer(self, slot):
        for mine in self.entities.values():
            mine.viewers.pop(slot, None)

    def retire(self, body):
        for mine in list(self.entities.values()):
            if mine.source == body:
                self.remove(mine)
        self.seen = {key for key in self.seen if key[0] != body}
