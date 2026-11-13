"""Match host for the authored Minefield volley and mine lifecycle."""

import math
from dataclasses import dataclass, field

from ow174.game import ball, observers, scalar_math, sombra_control, world
from ow174.game.script import entity_queries, expr, graph, runtime
from ow174.game.script.driver import ReplicatedBody, create_body_graphs

HERO = 0x02E00000000001CA
BODY = DEVICE = 0x0400000000002578
VOLLEY = 0x1517
LIFECYCLE = 0x151A
IMPACT = 0x02400000000005D4
NORMAL = 0x0A40000000000411
LIMIT = 15  # Owner pet capacity.
DEVICE_RADIUS = 0.22  # 151A/56, used as the reconstructed hit volume.
TAGS = frozenset((0x626,))  # 2578 entity tag.


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
    owner: object
    source: int
    team: int
    body: int
    position: tuple
    velocity: tuple
    gravity: float
    lifetime: float
    reach: float
    health: float
    last_tick: int
    age: float = 0.0
    travelled: float = 0.0
    normal: tuple | None = None
    component: runtime.Component | None = None
    primary: runtime.Instance | None = None
    triggered: bool = False
    removing: bool = False

    filter_bits: int = 0
    born_tick: int = 0
    reported_frame: int = 0
    body_script: object = None
    viewers: dict = field(default_factory=dict)

    @property
    def entity(self):
        return self.body

    @property
    def landed(self):
        return self.normal is not None

    @property
    def flight_lifetime(self):
        return self.lifetime

    @flight_lifetime.setter
    def flight_lifetime(self, value):
        self.lifetime = value

    def centre(self):
        if self.normal is None:
            return self.position
        return tuple(p + DEVICE_RADIUS * n for p, n in zip(self.position, self.normal, strict=True))

    @property
    def armed(self):
        search = self.primary.states.get(28)
        return (self.normal is not None and search is not None and search.active
                and not self.triggered and not self.removing)


class MineWorld(runtime.World):
    def __init__(self, manager, mine):
        super().__init__(HERO, manager.fight.match.game_map.mode_guid)
        self.manager = manager
        self.mine = mine

    @property
    def match(self):
        return self.manager.fight.match

    def source_script(self):
        script = self.mine.owner.body_script
        return script if script is not None and script.component.entity == self.mine.source else None

    def script_component_present(self, entity):
        if entity == self.mine.source:
            return self.source_script() is not None
        return super().script_component_present(entity)

    def entity_ruleset(self, entity, key):
        if entity == self.mine.source:
            script = self.source_script()
            return script.component.world.ruleset(key) if script is not None else None
        return super().entity_ruleset(entity, key)

    def track_targets(self, state):
        if state.instance.graph.index != LIFECYCLE or state.node.pos != 50:
            return super().track_targets(state)
        targets = self.manager.visible_targets(self.mine, state.instance, state.node)
        state.instance.write(expr.lvalue(state.node.field("m_8A2905A0")),
                             tuple(expr.Entity(target.entity) for target in targets))
        return False

    def position(self, entity):
        if entity == self.mine.body:
            return expr.Vec3(*self.mine.position)
        return super().position(entity)

    def body_health(self, entity):
        if entity == self.mine.body:
            return self.mine.health, 50.0
        return self.manager.fight.body_health(entity)

    def component76_keys(self, entity):
        if entity == self.mine.body:
            return entity_queries.Component76Keys(TAGS)
        return super().component76_keys(entity)

    def play_script_selected(self, instance, node):
        return self.manager.fight.sombra_controls.play(instance, node)

    def native(self, cfg, instance):
        if (instance is not None and instance.graph.index == sombra_control.EFFECT
                and cfg is instance.graph.node(19).field("m_condition")):
            source = expr.to_entity(instance.evaluate(cfg["m_entity"]))
            return any(p.body == source and p.has_body and not p.gone for p in self.match.players)
        if instance is not None and instance.graph.index == LIFECYCLE:
            cls = cfg.get("$")
            if cls == "STU_D32169F6":
                return expr.Vec3(*(self.mine.normal or (0.0, 1.0, 0.0)))
            if (cls == "STU_2B2E2F6D" and cfg.get("m_7BDB02D1") == 0
                    and instance.evaluate(cfg.get("m_81A364DE")) == expr.Entity(self.mine.body)):
                return expr.Entity(self.mine.source if self.source_script() is not None else 0)
        return super().native(cfg, instance)

    def entity_var(self, entity, var):
        script = self.source_script() if entity == self.mine.source else None
        if script is None and var == 9588:
            # Original second-search filter reads the live candidate Cryo stack.
            # Keep its same owner VM; no copied or fabricated status flag.
            for target in self.manager.fight._targets():
                if (target.entity == entity and self.manager.fight._has_protection(
                        target, {0x02E00000000000DD: (0x05FE, 35)})):
                    script = target.owner.body_script
                    break
        if script is not None:
            slot = script.component.entity_var(var, create=False)
            return slot.value() if slot is not None else None
        return super().entity_var(entity, var)

    def ruleset_expression(self, evaluation, config):
        instance = evaluation.instance
        if instance.graph.index != LIFECYCLE:
            return NotImplemented
        modify = instance.graph.node(8).field("m_modifyHealth")
        if not any(config is modify[field]["m_configVars"][0]["m_value"]
                   for field in ("m_E33EFEE1", "m_583C208B")):
            return NotImplemented
        value = expr.evaluate(config, evaluation)
        if value is not None:
            return value
        script = self.mine.owner.body_script
        if script is not None and script.component.entity == self.mine.source:
            return script.component.world.ruleset(16349)
        return 0.0

    def destroy_entity(self, instance, node):
        if instance.graph.index == LIFECYCLE and node.pos in (9, 26, 43):
            self.mine.removing = True
            return True
        return super().destroy_entity(instance, node)

    def modify_health(self, state, elapsed_ms):
        scope = state.instance.graph.index, state.node.pos
        if scope == (sombra_control.PULSE, 9):
            if elapsed_ms == 0:
                state.request_finish()
            return True
        if scope == (LIFECYCLE, 8):
            return True  # trigger resolves the actual second-search output once
        return super().modify_health(state, elapsed_ms)


class MineScript(ReplicatedBody):
    def __init__(self, manager, mine, frame):
        self.frame = frame
        self.death_frame = None
        self.component = runtime.Component(mine.entity, world=MineWorld(manager, mine), seed=mine.entity)
        self.component.frame = frame
        self.component.now = frame * self.component.frame_ms
        self.set_health(mine.health, mine.health)
        record = graph.entity_body(BODY)
        if record is None or any(graph.graph(row['graph']) is None for row in record['graphs']):
            raise ValueError('Minefield needs explicitly selected mine body/151A/261E graph data')
        for instance in create_body_graphs(self.component, record):
            instance.init()
        mine.component = self.component
        mine.primary = self.primary()
        self.component.run_frame(frame)
        self.server_only = self.initial_exclusions()
        self.latest_live = observers.snapshot(self)

    @property
    def initial(self):
        return observers.full_frame(self, self.latest_live), self.latest_live

    def primary(self):
        return next(i for i in self.component.instances.values() if i.graph.index == 0x151A)

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


class Mines:
    def __init__(self, fight):
        self.fight = fight
        self.devices = {}
        self.volleys = {}
        self.seen = set()

    @property
    def entities(self):
        return self.devices

    def volley(self, owner, state, number, frame):
        return self.launch(owner, state, number, frame=frame)

    def launch(self, owner, state, number, *, frame=None):
        if owner.hero.guid != HERO or state.instance.graph.index != VOLLEY:
            return False
        node = state.node
        resource = state.evaluate(node.field("m_projectileEntity.m_entityDef"), watch=False)
        if resource != expr.Asset(DEVICE):
            return False
        record = graph.entity_body(DEVICE)
        if record is None or any(graph.graph(row["graph"]) is None for row in record["graphs"]):
            raise ValueError("Minefield needs selected 2578/151A/261E graphs")
        source = owner.body
        slot = source, state.node.pos
        report = state.instance.id, state.counter, number
        key = source, state.instance.id, state.index, state.counter, number
        if key in self.seen:
            return True

        def numeric(field):
            return expr.to_float(state.evaluate(node.field(field), watch=False))

        count = expr.to_int(state.evaluate(node.field("m_numProjectilesPerShot"), watch=False))
        offset = state.evaluate(node.field("m_5EC1EB38.m_E2FD6F04"), watch=False)
        speed = numeric("m_projectileSpeed")
        angle = math.radians(numeric("m_5EC1EB38.m_spread"))
        lifetime, reach = numeric("m_projectileLifetime"), numeric("m_FEC435C6")
        motions = node.field("m_projectileMotions") or []
        gravity = expr.to_float(state.evaluate(motions[0].get("m_599AB8D7"), watch=False)) if motions else 0.0
        if (not 0 < count <= LIMIT or not isinstance(offset, expr.Vec3) or speed <= 0
                or not all(map(math.isfinite, (speed, angle, lifetime, reach, gravity, *offset)))):
            raise ValueError("unsupported Minefield volley")
        fight = self.fight
        if fight.next_bot + count > 0xD0000000:
            raise ValueError("mine entity allocator exhausted")
        health = expr.to_float(graph.graph(LIFECYCLE).node(0).field("m_amount.m_value"))
        reported_frame = state.instance.component.frame if frame is None else frame
        # Delayed reports keep their event's world time. Accepted commands may
        # lead the world clock; preserve receipt-time launch for those reports.
        born_tick = min(fight.match.tick, ball.world_tick_at_body(owner.body_script, reported_frame))
        position = tuple(expr.f32(p + o) for p, o in zip(owner.mover.position, offset, strict=True))
        yaw = owner.mover.yaw * math.tau / 65536
        self.volleys[slot] = report
        self.seen.add(key)
        for index in range(count):
            owned = [mine for mine in self.devices.values() if mine.source == source]
            if len(owned) >= LIMIT:
                self.remove(min(owned, key=lambda mine: mine.body))
            azimuth = yaw + math.tau * index / count
            flat = speed * math.sin(angle)
            velocity = tuple(map(expr.f32, (flat * math.sin(azimuth), speed * math.cos(angle),
                                            flat * math.cos(azimuth))))
            body = fight.next_bot
            fight.next_bot += 1
            mine = Mine(owner, source, owner.team, body, position, velocity, gravity, lifetime,
                        reach, health, fight.tick)
            mine.filter_bits = owner.team_bit
            mine.born_tick = born_tick
            mine.last_tick = mine.born_tick
            mine.reported_frame = reported_frame
            mine.body_script = MineScript(self, mine, mine.born_tick)
            self.devices[body] = mine
            fight.record_replacement(body)
            fight.replacement_ticks[body] = mine.born_tick
            self.deliver(mine)
            self._update_mine(mine, fight.match.tick)
        return True

    def deliver(self, mine):
        for viewer in self.fight.match.players:
            if (viewer.client is None or not viewer.spawned
                    or mine.viewers.get(viewer.slot) is viewer.client):
                continue
            mine.viewers[viewer.slot] = viewer.client
            # Freeze the create baseline; retransmission cannot change its pose/pools.
            position = mine.position
            components = world.health(mine.health, 50)
            components[26] = [mine.filter_bits]
            components[72] = [mine.source]
            viewer.client.queue_entities([world.EntityUpdate(
                mine.entity, world.OP_CREATE,
                lambda origin, position=position, components=components: world.create(
                    origin, BODY, components, position=position,
                ),
            )])

    def remove(self, mine):
        if self.devices.pop(mine.body, None) is None:
            return
        mine.removing = True
        self.fight.vitals.pop(mine.body, None)
        self.fight.history.pop(mine.body, None)
        self.fight.replacement_ticks.pop(mine.body, None)
        for contacts in self.fight.ball_contacts.values():
            contacts.discard(mine.body)
        for viewer in self.fight.match.players:
            if viewer.client is not None and mine.viewers.get(viewer.slot) is viewer.client:
                viewer.client.queue_entities([world.EntityUpdate(mine.body, world.OP_DESTROY)])
        self.fight.sombra_controls.retire(mine.body)
        if mine.component.fault is None:
            for instance in tuple(mine.component.instances.values()):
                mine.component.destroy(instance)

    def retire(self, source):
        for mine in tuple(self.devices.values()):
            if mine.source == source:
                self.remove(mine)
        self.volleys = {key: value for key, value in self.volleys.items() if key[0] != source}
        self.seen = {key for key in self.seen if key[0] != source}

    def enemies(self, shooter, *, team=None, include_flights=False):
        team = shooter.team if team is None else team
        for mine in self.devices.values():
            if ((mine.normal is not None or include_flights)
                    and not mine.triggered and not mine.removing and mine.owner is not shooter
                    and (self.fight.match.game_map.free_for_all or mine.team != team)):
                yield mine

    def trace(self, shooter, origin, direction, reach, *, team=None):
        from ow174.game.combat import ray_capsule

        best = None
        for mine in self.enemies(shooter, team=team):
            centre = mine.centre()
            distance = ray_capsule(origin, direction, centre, centre, DEVICE_RADIUS)
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = distance, mine
        return best

    def damage(self, mine, amount):
        if self.devices.get(mine.body) is mine and amount > 0:
            mine.health = expr.f32(max(0.0, mine.health - amount))
            if not mine.health:
                self.remove(mine)

    def advance(self, mine, tick):
        previous, old_velocity = mine.position, mine.velocity
        velocity, _, endpoint = flight_step(previous, old_velocity, mine.gravity, TICK)
        age = expr.f32(expr.f32(tick - mine.born_tick - 1) * expr.f32(TICK))
        endpoint, mine.velocity, _, distance, expired = clip_flight(
            previous, old_velocity, endpoint, velocity, mine.lifetime, mine.reach,
            age, mine.travelled, TICK)
        delta = tuple(p - old for p, old in zip(endpoint, previous, strict=True))
        length = math.sqrt(sum(v*v for v in delta))
        hit = (self.fight._collision().cast_ray(previous, tuple(v/length for v in delta), length)
               if length > 0 else None)
        if hit is not None:
            mine.travelled = expr.f32(mine.travelled + flight_distance(previous, hit.point))
            mine.position, mine.normal = tuple(hit.point), tuple(hit.normal)
            mine.velocity = (0.0, 0.0, 0.0)
            mine.body_script.advance(tick)
            mine.body_script.impact(hit.normal)
            return
        mine.position = endpoint
        mine.travelled = expr.f32(mine.travelled + distance)
        mine.age = expr.f32(age + TICK)
        if expired:
            mine.velocity = (0.0, 0.0, 0.0)
            mine.removing = True

    def update(self, tick):
        for mine in tuple(self.devices.values()):
            self._update_mine(mine, tick)

    def _update_mine(self, mine, tick):
        if tick < mine.last_tick:
            raise ValueError("Minefield world clock moved backwards")
        if (mine.owner.gone or mine.owner.body != mine.source or mine.component.fault is not None
                or mine.health <= 0):
            self.remove(mine)
            return
        for at in range(mine.last_tick + 1, tick + 1):
            if mine.normal is not None or mine.removing:
                break
            self.advance(mine, at)
        mine.last_tick = tick
        snap = mine.body_script.advance(tick)
        if mine.armed:
            self.trigger(mine)
        if mine.removing:
            self.remove(mine)
            return
        self.deliver(mine)
        for viewer in self.fight.match.players:
            if viewer.client is not None and mine.viewers.get(viewer.slot) is viewer.client:
                viewer.client.queue_entities([world.EntityUpdate(
                    mine.body, movement=world.Movement(mine.position, velocity=mine.velocity))])
                if snap is not None:
                    viewer.client.queue_bot_live(mine.body, snap)

    def visible_targets(self, mine, instance, node, *, centre=None):
        from ow174.game import combat

        if centre is None:
            centre = instance.evaluate(node.field('m_05BE62DD'))
        if not isinstance(centre, expr.Vec3):
            return []
        radius = expr.to_float(instance.evaluate(node.field('m_radius')))
        condition = node.field('m_filter')['m_condition']
        collision = self.fight._collision()
        targets = []
        for target in self.fight._enemies(mine.owner, team=mine.team):
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

    def trigger(self, mine):
        tick = self.fight.tick
        """Reconstructed capsule/LOS proximity -> actual explosion graph activation."""
        if mine.triggered or mine.health <= 0 or mine.removing or not mine.armed:
            return
        primary = mine.body_script.primary()
        from ow174.game import barriers, defenses

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
        targets = {target.entity: target for target in self.fight._enemies(mine.owner, team=mine.team)}
        for entity in primary.evaluate(blast.node.field('m_FA3ECC43')) or ():
            target = targets.pop(expr.to_entity(entity), None)
            if target is None:
                continue
            # The original mine consumes a live particle barrier before body
            # damage and impulse. A breaking shield still owns the whole hit.
            if barriers.protect_body(self.fight, target, amount, attacker=mine.source) is not None:
                continue
            self.fight._damage(
                mine.owner, target, amount, False, loadout.guid, tick, world_tick=tick,
                source_scaled=True, source_entity=mine.source, award_ult=False,
            )
            if (not self.fight.vitals_of(target).dead
                    and not self.fight._has_protection(target, {0x02E00000000000DD: (0x05FE, 35)})
                    and (horizontal > 0 or vertical > 0)):
                response = defenses.knockback_scale(target.owner)
                if response <= 0:
                    continue
                dx, dz = target.feet[0] - centre.x, target.feet[2] - centre.z
                distance = math.hypot(dx, dz)
                vy = (target.owner.velocity[1] if hasattr(target.owner, "velocity")
                      else target.owner.mover.velocity[1])
                impulse = (horizontal * dx/distance if distance > 1e-9 else 0.0,
                           max(0.0, vertical-vy),
                           horizontal * dz/distance if distance > 1e-9 else 0.0)
                self.fight.knockbacks.apply(
                    target, expr.Vec3(*(axis * response for axis in impulse)),
                    impact_tick=tick, source_entity=mine.source,
                )
