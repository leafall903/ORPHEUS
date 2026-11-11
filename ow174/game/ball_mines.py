"""Match host for the authored Minefield volley and mine lifecycle."""

import math
from dataclasses import dataclass

from ow174.game import sombra_control
from ow174.game.script import entity_queries, expr, graph, runtime
from ow174.game.script.driver import create_body_graphs

HERO = 0x02E00000000001CA
DEVICE = 0x0400000000002578
VOLLEY = 0x1517
LIFECYCLE = 0x151A
IMPACT = 0x02400000000005D4
NORMAL = 0x0A40000000000411
LIMIT = 15  # Owner pet capacity.
DEVICE_RADIUS = 0.22  # 151A/56, used as the reconstructed hit volume.
TAGS = frozenset((0x626,))  # 2578 entity tag.


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
        if instance is not None and instance.graph.index == LIFECYCLE:
            cls = cfg.get("$")
            if cls == "STU_D32169F6":
                return expr.Vec3(*(self.mine.normal or (0.0, 1.0, 0.0)))
            if (cls == "STU_2B2E2F6D" and cfg.get("m_7BDB02D1") == 0
                    and instance.evaluate(cfg.get("m_81A364DE")) == expr.Entity(self.mine.body)):
                return expr.Entity(self.mine.source)
        return super().native(cfg, instance)

    def entity_var(self, entity, var):
        script = self.mine.owner.body_script
        if entity == self.mine.source and script is not None and script.component.entity == entity:
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
            if elapsed_ms == 0:
                self.manager.explode(self.mine, state)
            return True
        return super().modify_health(state, elapsed_ms)


class Mines:
    def __init__(self, fight):
        self.fight = fight
        self.devices = {}
        self.volleys = {}

    def launch(self, owner, state, number):
        from ow174.game.combat import projectile_gravity

        node = state.node
        resource = state.evaluate(node.field("m_projectileEntity.m_entityDef"), watch=False)
        if resource != expr.Asset(DEVICE):
            return False
        record = graph.entity_body(DEVICE)
        if record is None or any(graph.graph(row["graph"]) is None for row in record["graphs"]):
            return False
        source = owner.body
        slot = source, state.node.pos
        report = state.instance.id, state.counter, number
        if self.volleys.get(slot) == report:
            return True

        def numeric(field):
            return expr.to_float(state.evaluate(node.field(field), watch=False))

        count = expr.to_int(state.evaluate(node.field("m_numProjectilesPerShot"), watch=False))
        offset = state.evaluate(node.field("m_5EC1EB38.m_E2FD6F04"), watch=False)
        speed = numeric("m_projectileSpeed")
        angle = math.radians(numeric("m_5EC1EB38.m_spread"))
        lifetime, reach = numeric("m_projectileLifetime"), numeric("m_FEC435C6")
        gravity = projectile_gravity(state)
        if (not 0 < count <= LIMIT or not isinstance(offset, expr.Vec3) or speed <= 0
                or not all(map(math.isfinite, (speed, angle, lifetime, reach, gravity, *offset)))):
            raise ValueError("unsupported Minefield volley")
        fight = self.fight
        if fight.next_bot + count > 0xD0000000:
            raise ValueError("mine entity allocator exhausted")
        health = expr.to_float(graph.graph(LIFECYCLE).node(0).field("m_amount.m_value"))
        position = tuple(expr.f32(p + o) for p, o in zip(owner.mover.position, offset, strict=True))
        yaw = owner.mover.yaw * math.tau / 65536
        self.volleys[slot] = report
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
            component = mine.component = runtime.Component(body, world=MineWorld(self, mine), seed=body)
            component.frame = fight.tick
            component.now = fight.tick * component.frame_ms
            instances = create_body_graphs(component, record)
            mine.primary, = (instance for instance in instances if instance.graph.index == LIFECYCLE)
            for instance in instances:
                instance.init()
            component.run_frame(fight.tick)
            self.devices[body] = mine
        return True

    def remove(self, mine):
        if self.devices.pop(mine.body, None) is None:
            return
        self.fight.sombra_controls.retire(mine.body)
        if mine.component.fault is None:
            for instance in tuple(mine.component.instances.values()):
                mine.component.destroy(instance)

    def retire(self, source):
        for mine in tuple(self.devices.values()):
            if mine.source == source:
                self.remove(mine)
        self.volleys = {key: value for key, value in self.volleys.items() if key[0] != source}

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
        from ow174.game.combat import TICK

        dt = min(TICK, max(0.0, mine.lifetime - mine.age)) if mine.lifetime > 0 else TICK
        vx, vy, vz = mine.velocity
        mine.velocity = vx, expr.f32(vy - expr.f32(mine.gravity * dt)), vz
        speed = math.hypot(*mine.velocity)
        travel = min(speed * dt, max(0.0, mine.reach - mine.travelled))
        if travel > 0:
            direction = tuple(value / speed for value in mine.velocity)
            hit = self.fight._collision().cast_ray(mine.position, direction, travel)
            if hit is not None:
                mine.position = hit.point
                mine.normal = hit.normal
                mine.velocity = (0.0, 0.0, 0.0)
                self.advance_script(mine, tick)
                runtime.send_message(mine.component, IMPACT, {NORMAL: expr.Vec3(*hit.normal)}, mine.body)
                mine.component.run_now()
                return
            mine.position = tuple(expr.f32(p + d * travel)
                                  for p, d in zip(mine.position, direction, strict=True))
        mine.age += dt
        mine.travelled += travel
        if (mine.lifetime > 0 and mine.age >= mine.lifetime) or mine.travelled >= mine.reach:
            mine.removing = True

    @staticmethod
    def advance_script(mine, tick):
        component = mine.component
        while not mine.removing and component.frame < tick:
            due = min((event.time for instance in component.instances.values() for event in instance.queue
                       if event.time > component.now), default=tick * component.frame_ms)
            frame = max(component.frame + 1, math.ceil(due / component.frame_ms))
            component.run_frame(min(tick, frame))

    def update(self, tick):
        for mine in tuple(self.devices.values()):
            if mine.owner.gone or mine.owner.body != mine.source or mine.component.fault is not None:
                self.remove(mine)
                continue
            for at in range(mine.last_tick + 1, tick + 1):
                if mine.normal is not None or mine.removing:
                    break
                self.advance(mine, at)
            mine.last_tick = tick
            self.advance_script(mine, tick)
            if mine.armed:
                self.trigger(mine)
            if mine.removing:
                self.remove(mine)

    def trigger(self, mine):
        from ow174.game.combat import capsule_visible, segment_distance

        fight, instance = self.fight, mine.primary
        tracker = instance.graph.node(52)
        centre = instance.evaluate(tracker.field("m_05BE62DD"))
        radius = expr.to_float(instance.evaluate(tracker.field("m_radius")))
        if not isinstance(centre, expr.Vec3) or radius <= 0:
            return
        targets = []
        for target in fight._targets():
            if (target.owner is mine.owner or target.entity == mine.source
                    or (not fight.match.game_map.free_for_all and target.team == mine.team)
                    or fight.vitals_of(target).dead or fight._protected(target.owner)):
                continue
            a, b = target.segment()
            if (segment_distance(tuple(centre), a, b) - target.radius <= radius
                    and capsule_visible(fight._collision(), tuple(centre), a, b, target.radius,
                                        pillars=fight.ice_wall.pieces.values())):
                targets.append(expr.Entity(target.entity))
        if targets:
            # Host the proximity-to-explosion transition for the authored tracker.
            mine.triggered = True
            instance.write((expr.INSTANCE, 375), tuple(targets))
            instance.node_state(instance.graph.node(8)).request(runtime.BEGIN)
            instance.component.run_now()

    def explode(self, mine, state):
        from ow174.game import bots, fortify

        fight = self.fight
        modify = state.node.field("m_modifyHealth")
        amount = expr.to_float(state.evaluate(modify["m_amount"], watch=False))
        loadout = state.evaluate(modify["m_loadout"], watch=False).guid
        push = expr.to_float(state.evaluate(modify["m_E33EFEE1"], watch=False))
        lift = expr.to_float(state.evaluate(modify["m_583C208B"], watch=False))
        selected = state.instance.find_var(expr.INSTANCE, 375).value() or ()
        for target in fight._targets():
            if expr.Entity(target.entity) not in selected:
                continue
            health = fight.vitals_of(target)
            before = health.total
            fight._damage(mine.owner, target, amount, False, loadout, fight.tick,
                          source_entity=mine.source, source_scaled=True)
            if ((push > 0 or lift > 0) and health.total < before and not health.dead
                    and not isinstance(target.owner, bots.Bot)
                    and not fortify.control_immune(target.owner)):
                dx, dz = target.feet[0] - mine.position[0], target.feet[2] - mine.position[2]
                distance = math.hypot(dx, dz)
                scale = push / distance if distance > 0 else 0.0
                fight.external_impulses[target.entity] = (dx * scale, lift, dz * scale)
