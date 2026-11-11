"""Server host for the 104319 Symmetra teleporter's static placement and transit."""

import math
from dataclasses import dataclass

from ow174.game.collision import NOT_GROUND
from ow174.game.script import expr, graph
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E0000000000016
PLACEMENT = 0x1571
TRANSIT = 0x1647
ENDPOINTS = {3: "0x040000000000272D", 70: "0x04000000000026F2"}
ENDPOINT_TAGS = (frozenset((0x417, 0x60A)), frozenset((0x417, 0x2EC)))
ENDPOINT_SLOTS = (1624, 1625)
DEVICE_RADIUS = 0.5  # Reconstructed endpoint hit volume.
INTERACT = 4
ABILITY = 16
SHIELD_DELAY = 3.0  # 0033/state27.
SHIELD_RATE = 30.0  # 0033/state2 with default endpoint overrides.


@dataclass
class Endpoint:
    body: int
    position: tuple
    vitals: object
    recover_at: float | None = None

    def centre(self):
        x, y, z = self.position
        return x, y + .75, z


@dataclass
class Pair:
    owner: object
    source: int
    instance: object | None
    endpoints: tuple
    ready: float
    cooldown: float


class Portals:
    def __init__(self, fight):
        self.fight = fight
        self.pairs = {}
        self.held = {}
        self.transits = {}
        self.cooldowns = {}

    def player(self, body):
        return next((p for p in self.fight.match.players
                     if p.body == body and p.spawned and p.has_body and not p.gone), None)

    def confirm(self, state):
        from ow174.game.combat import _along, aim

        instance = state.instance
        owner = self.player(instance.component.entity)
        if (owner is None or owner.hero.guid != HERO or self.fight.match.ended or self.fight.is_dead(owner)
                or self.fight.is_asleep(owner) or self.fight.is_stunned(owner)):
            return
        if owner.body in self.pairs or self.fight.now < self.cooldowns.get(owner.body, 0):
            return
        world = self.fight._collision()
        x, y, z = owner.mover.position
        origin = self.fight._eye(owner)
        direction = aim(owner.mover.yaw, owner.mover.pitch)
        minimum = expr.to_float(state.evaluate(state.node.field("m_00A23E4B"), watch=False))
        maximum = expr.to_float(state.evaluate(state.node.field("m_6386E7B4"), watch=False))
        hit = world.cast_ray(origin, direction, maximum)
        point = _along(origin, direction, maximum) if hit is None else hit.point
        source_ground = world.ground(x, z, y + .5, y - 1)
        tx, ty, tz = point
        destination = world.ground(tx, tz, ty + .5, ty - 3)
        if (source_ground is None or destination is None or hit is not None and hit.normal[1] < .7
                or math.hypot(tx - x, tz - z) < minimum
                or source_ground.normal[1] < .7 or destination.normal[1] < .7
                or source_ground.surface & NOT_GROUND or destination.surface & NOT_GROUND):
            return
        entrance, exit_point = (x, source_ground.y, z), (tx, destination.y, tz)
        for floor in (entrance, exit_point):
            if not self.clearance(owner, floor, radius=DEVICE_RADIUS):
                return
        instance.write((expr.INSTANCE, 1626), expr.Vec3(*exit_point))
        instance.write((expr.INSTANCE, 14877), expr.Vec3(*entrance))
        instance.write((expr.INSTANCE, 1002), True)
        instance.follow(state.node, "m_F6955E81", state)
        instance.follow(instance.graph.node(82), "m_outPlug")
        unused = (73, 69, "m_F6955E81")
        if unused in instance.join_inputs:
            instance.join_inputs.remove(unused)

    def create(self, instance, node):
        from ow174.game.combat import Vitals

        if instance.graph.index != PLACEMENT or node.pos not in ENDPOINTS or instance.component.strict:
            return NotImplemented
        source = instance.component.entity
        if node.pos == 70:
            return source in self.pairs
        owner = self.player(source)
        if (owner is None or owner.hero.guid != HERO or self.fight.match.ended or source in self.pairs
                or self.fight.now < self.cooldowns.get(source, 0)):
            return False
        destination = instance.find_var(expr.INSTANCE, 1626).value()
        entrance = instance.find_var(expr.INSTANCE, 14877).value()
        if not isinstance(destination, expr.Vec3) or not isinstance(entrance, expr.Vec3):
            return False
        first = self.fight.next_bot
        if first + 1 >= 0xD0000000:
            raise ValueError("teleporter entity allocator exhausted")
        endpoints = []
        for pos, floor, entity in zip(ENDPOINTS, (destination, entrance), (first, first + 1), strict=True):
            factory = instance.graph.node(pos)
            definition = factory.field("m_entityDefinition")["m_entityDef"]
            if definition != ENDPOINTS[pos]:
                raise ValueError("Symmetra teleporter endpoint resource changed")
            # Both endpoint resources override 0033 to 50 health and 250 shields.
            endpoints.append(Endpoint(entity, tuple(floor), Vitals(entity, 50, shields=250)))
        self.fight.next_bot += 2
        delay = graph.graph(0x157D).node(41).field("m_timeout")["m_value"]
        cooldown = expr.to_float(instance.find_var(expr.INSTANCE, 472).value())
        pair = Pair(owner, source, instance, tuple(endpoints), self.fight.now + delay, cooldown)
        self.pairs[source] = pair
        self.write_endpoints(pair)
        return True

    @staticmethod
    def write_endpoints(pair, *, clear=False):
        instance = pair.instance
        for pos, endpoint in zip(ENDPOINTS, pair.endpoints, strict=True):
            output = expr.dynamic(instance.graph.node(pos).field("m_B3019338"))
            instance.write(output, expr.Entity(0 if clear else endpoint.body))

    def transfer_on_respawn(self, player, new_body):
        if player.hero.guid != HERO:
            return
        pair = self.pairs.pop(player.body, None)
        if pair is not None:
            pair.source, pair.instance = new_body, None
            self.pairs[new_body] = pair
        deadline = self.cooldowns.pop(player.body, None)
        if deadline is not None:
            self.cooldowns[new_body] = deadline

    def bind_body_script(self, player):
        if player.hero.guid != HERO:
            return
        script = player.body_script
        instance = next((i for i in script.component.instances.values() if i.graph.index == PLACEMENT), None)
        if instance is None:
            return
        pair = self.pairs.get(player.body)
        if pair is not None:
            pair.instance = instance
            self.write_endpoints(pair)
            instance.write((expr.INSTANCE, 22860), 1)
        deadline = self.cooldowns.get(player.body, 0)
        if deadline > self.fight.now:
            instance.write((expr.INSTANCE, 61), deadline - self.fight.now)

    def remove_pair(self, source):
        pair = self.pairs.pop(source, None)
        if pair is None:
            return
        for endpoint in pair.endpoints:
            self.fight.sombra_controls.retire(endpoint.body)
        self.cooldowns[source] = self.fight.now + pair.cooldown
        instance = pair.instance
        if instance is None or instance.stopped or instance.component.fault is not None:
            return
        self.write_endpoints(pair, clear=True)
        instance.write((expr.INSTANCE, 61), pair.cooldown)
        instance.write((expr.INSTANCE, 22860), 0)

    def retire(self, body):
        self.remove_pair(body)
        self.held.pop(body, None)
        self.cooldowns.pop(body, None)
        self.end_transit(body)

    def end_transit(self, body):
        row = self.transits.pop(body, None)
        if row is not None:
            instance, _ = row
            if not instance.stopped and instance.component.fault is None:
                instance.component.destroy(instance)

    def update(self):
        for body, pair in tuple(self.pairs.items()):
            if pair.owner.gone or pair.owner.body != body or pair.owner.hero.guid != HERO:
                self.remove_pair(body)
                continue
            for endpoint in pair.endpoints:
                self.recover_shields(endpoint)
        for body, (_, deadline) in tuple(self.transits.items()):
            if self.fight.now >= deadline:
                self.end_transit(body)
        for body, deadline in tuple(self.cooldowns.items()):
            if self.fight.now >= deadline:
                del self.cooldowns[body]

    def enemies(self, shooter, *, team=None):
        team = shooter.team if team is None else team
        for pair in self.pairs.values():
            if pair.owner is not shooter and (self.fight.match.game_map.free_for_all or pair.owner.team != team):
                yield from pair.endpoints

    def damage(self, endpoint, amount):
        for pair in self.pairs.values():
            if any(device is endpoint for device in pair.endpoints):
                self.recover_shields(endpoint)
                if endpoint.vitals.damage(amount) > 0:
                    endpoint.recover_at = self.fight.now + SHIELD_DELAY
                if endpoint.vitals.total == 0:
                    endpoint.vitals.died_at = self.fight.now
                    endpoint.vitals.death_tick = self.fight.tick
                    self.remove_pair(pair.source)
                return

    def endpoint_pair(self, entity):
        for pair in self.pairs.values():
            for index, endpoint in enumerate(pair.endpoints):
                if endpoint.body == entity:
                    return pair, index
        return None, None

    def endpoint_owner(self, entity):
        pair, _ = self.endpoint_pair(entity)
        if pair is None or pair.instance is None or pair.owner.gone or not pair.owner.has_body:
            return None, None
        instance = pair.instance
        component = instance.component
        if (instance.stopped or component.fault is not None or component.entity != pair.source
                or pair.owner.body != pair.source or component.instances.get(instance.id) is not instance):
            return None, None
        return pair, component

    def drain_shields(self, entity, amount):
        pair, index = self.endpoint_pair(entity)
        if pair is None:
            return
        endpoint = pair.endpoints[index]
        self.recover_shields(endpoint)
        pools = endpoint.vitals
        before = pools.now[SHIELDS_POOL]
        pools.now[SHIELDS_POOL] = max(0.0, before - amount)
        if pools.now[SHIELDS_POOL] < before:
            pools.changed.add(SHIELDS_POOL)
            endpoint.recover_at = self.fight.now + SHIELD_DELAY

    def recover_shields(self, endpoint):
        start, now = endpoint.recover_at, self.fight.now
        if start is None or start >= now:
            return
        pools = endpoint.vitals
        maximum = pools.most[SHIELDS_POOL]
        shields = pools.now[SHIELDS_POOL]
        recovered = min(maximum, shields + (now - start) * SHIELD_RATE)
        if recovered > shields:
            pools.now[SHIELDS_POOL] = recovered
            pools.changed.add(SHIELDS_POOL)
        endpoint.recover_at = None if recovered == maximum else now

    def trace(self, shooter, origin, direction, reach, *, team=None):
        from ow174.game.combat import ray_capsule

        best = None
        for device in self.enemies(shooter, team=team):
            x, y, z = device.centre()
            distance = ray_capsule(origin, direction, (x, y - .25, z), (x, y + .25, z), DEVICE_RADIUS)
            if distance is not None and distance <= reach and (best is None or distance < best[0]):
                best = distance, device
        return best

    def clearance(self, player, point, *, radius=None):
        return self.fight.body_clearance(player, point, radius=radius)

    def command(self, player, command, *, blocked=False):
        if command is None:
            return
        mask = INTERACT | ABILITY if player.hero.guid == HERO else INTERACT
        held = command.buttons & mask
        pressed = held & ~self.held.get(player.body, 0)
        if held:
            self.held[player.body] = held
        else:
            self.held.pop(player.body, None)
        if not pressed or blocked or self.fight.match.ended:
            return
        pair = self.pairs.get(player.body)
        if pressed & ABILITY and pair is not None and self.fight.now >= pair.ready:
            self.remove_pair(player.body)
        if pressed & INTERACT:
            self.interact(player)

    def interact(self, player):
        from ow174.game.combat import capsule_visible

        if player.body in self.transits:
            return
        fight = self.fight
        radius = graph.graph(0x157D).node(1).field("m_radius")["m_value"]
        origin = fight._eye(player)
        nearest = None
        for pair in self.pairs.values():
            if any(endpoint.body in fight.sombra_controls.locks for endpoint in pair.endpoints):
                continue
            if fight.now < pair.ready or player is not pair.owner and (
                fight.match.game_map.free_for_all or player.team != pair.owner.team
            ):
                continue
            for index, endpoint in enumerate(pair.endpoints):
                distance = math.dist(player.mover.position, endpoint.position)
                centre = endpoint.centre()
                if (distance <= radius and (nearest is None or distance < nearest[0])
                        and capsule_visible(fight._collision(), origin, centre, centre, 0,
                                            pillars=fight.ice_wall.pieces.values())):
                    nearest = distance, pair, index
        if nearest is None:
            return
        _, pair, index = nearest
        source, destination = pair.endpoints[index], pair.endpoints[1 - index]
        use = graph.graph(0x15F5)
        lift = use.node(3).field("m_value")["m_expression"]["m_D99EF254"][1]
        duration = expr.to_float(expr.evaluate(use.node(12).field("m_value"), expr.Context()))
        x, y, z = destination.position
        point = x, y + lift, z
        if not self.clearance(player, point):
            return
        params = ((0x0A40000000000078, expr.Vec3(*point)), (0x0A40000000000619, duration),
                  (0x0A4000000000055A, tuple(expr.Entity(device.body) for device in pair.endpoints)))
        component = player.body_script.component
        instance = component.play_loaded_graph(graph.graph(TRANSIT), source=source.body,
                                               params=params, policy=1, instigator=source.body)
        self.transits[player.body] = instance, fight.now + duration
        component.run_now()

    def relocate(self, instance, node):
        if (instance.graph.index, node.pos) != (TRANSIT, 1) or instance.component.strict:
            return NotImplemented
        player = self.player(instance.component.entity)
        if player is None or self.fight.is_dead(player):
            return False
        admission = self.transits.get(player.body)
        if admission is None or admission[0] is not instance:
            return False
        destination = instance.evaluate(node.field("m_AF774F7D"))
        if not isinstance(destination, expr.Vec3) or not all(math.isfinite(value) for value in destination):
            return False
        target = next(item for item in self.fight._targets() if item.entity == player.body)
        self.fight.teleport_body(target, destination)
        return True
