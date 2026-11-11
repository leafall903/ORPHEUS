"""Server host for the static 1571 teleporter pair and 1647 transit."""

import math
from dataclasses import dataclass

from ow174.game import npc, pools
from ow174.game.mover import MoverData, quantise
from ow174.game.script import expr, graph
from ow174.game.script.placement_geometry import static_surface_allowed
from ow174.game.symmetra import HERO

BODIES = (0x040000000000272D, 0x04000000000026F2)
INTERACT = 0x04
ABILITY = 0x10


@dataclass
class PortalPair:
    owner: object
    endpoints: tuple
    instance_id: int
    owner_body: int
    ready_at: float


class Portals:
    def __init__(self, combat):
        self.combat = combat
        self.pairs: list[PortalPair] = []
        self.transits = {}

    def confirm(self, world, state):
        if (world.hero != HERO or (state.instance.graph.index, state.node.pos) != (0x1571, 69)
                or not state.active or not all(graph.entity_body(body) for body in BODIES)):
            return NotImplemented
        instance = state.instance
        owner = self.owner(world.body)
        if owner is None or self.combat.is_dead(owner) or self.combat.is_stunned(owner):
            return True
        if any(pair.owner is owner for pair in self.pairs):
            return True
        x, y, z = owner.mover.position
        collision = self.combat.match.collision_world
        if collision is None:
            return NotImplemented
        ground = collision.ground(x, z, y + 1, y - 2)
        if ground is None or not static_surface_allowed(expr.Vec3(*ground.normal), ground.surface, False):
            instance.follow(state.node, 'm_425337C6', state)
            return True
        result = world.calculate_placement(state, commit=True)
        if result is True and instance.find_var(0, 1002).value() is True:
            # 82 is the grounded producer of join83. The host resolves its
            # owner-position input from the static floor under the caster.
            instance.run_node(instance.graph.node(82), None)
            instance.write((0, 14877), expr.Vec3(x, ground.y, z))
            unused = (73, 69, 'm_F6955E81')
            if unused in instance.join_inputs:
                instance.join_inputs.remove(unused)
        return result

    def owner(self, body):
        return next((p for p in self.combat.match.players
                     if p.body == body and p.spawned and p.has_body and not p.gone), None)

    def has_pair(self, body):
        return any(pair.owner.body == body for pair in self.pairs)

    def factory(self, instance, node):
        owner = self.owner(instance.component.entity)
        if owner is None or owner.hero.guid != HERO:
            return NotImplemented
        if node.pos == 70:
            return any(pair.owner is owner and pair.instance_id == instance.id for pair in self.pairs)
        if not all(graph.entity_body(body) for body in BODIES):
            return NotImplemented
        if any(pair.owner is owner for pair in self.pairs):
            return False
        destination = instance.find_var(0, 1626).value()
        entrance = instance.find_var(0, 14877).value()
        if not isinstance(destination, expr.Vec3) or not isinstance(entrance, expr.Vec3):
            return False
        position = expr.Vec3(destination.x, destination.y + .05, destination.z)
        activation = graph.graph(0x157D).node(41).field('m_timeout')['m_value']
        endpoints = []
        try:
            for body, point in zip(BODIES, (position, entrance), strict=True):
                record = graph.entity_body(body)
                override = next(row for row in record['graphs'] if row['graph'] == pools.BODY_GRAPH)
                schema = graph.graph(pools.BODY_GRAPH).fields['m_publicSchema']['m_entries']
                values = pools._numbers(schema)
                values.update(pools._numbers(override.get('m_1EB5A024')))
                definition = npc.Definition(HERO, body, 'Teleporter', values[pools.HEALTH_VAR],
                                            values.get(pools.ARMOUR_VAR, 0),
                                            values.get(pools.SHIELDS_VAR, 0),
                                            MoverData(stand_height=1.5, stand_radius=.5))
                bot = npc.spawn(self.combat.match, definition, tuple(point), team=owner.team)
                bot.spawner.update(manual_spawn=False, summoned=True)
                bot.combat_owner = owner
                endpoints.append(bot)
        except Exception:
            for bot in endpoints:
                npc.remove_summoned(self.combat.match, bot)
            raise
        ready = self.combat.now + activation
        self.pairs.append(PortalPair(owner, tuple(endpoints), instance.id, owner.body, ready))
        for variable, bot in zip((1624, 1625), endpoints, strict=True):
            instance.component.set(variable, expr.Entity(bot.entity))
        return True

    def _retire(self, pair):
        self.pairs.remove(pair)
        for endpoint in pair.endpoints:
            npc.remove_summoned(self.combat.match, endpoint)
        script = pair.owner.body_script
        if script is None:
            return
        if script.entity == pair.owner_body:
            instance = script.component.instances.get(pair.instance_id)
        elif script.entity == pair.owner.body and pair.owner.hero.guid == HERO:
            # A surviving pair still owns its cooldown after same-hero respawn.
            instance = next((i for i in script.component.instances.values()
                             if i.graph.index == 0x1571), None)
        else:
            return
        if instance is None:
            return
        for variable in (1624, 1625):
            script.component.set(variable, expr.Entity(0))
        instance.write((0, 61), instance.find_var(0, 472).value())
        instance.write((0, 22860), 0)

    def retire_owner(self, owner):
        for pair in tuple(self.pairs):
            if pair.owner is owner:
                self._retire(pair)

    def update(self):
        for pair in tuple(self.pairs):
            if (pair.owner.gone or pair.owner.hero.guid != HERO
                    or any(bot.dead or bot not in self.combat.match.bots for bot in pair.endpoints)):
                self._retire(pair)
        for body, (instance_id, deadline) in tuple(self.transits.items()):
            player = self.owner(body)
            script = player.body_script if player is not None else None
            if script is None or self.combat.now >= deadline or self.combat.is_dead(player):
                del self.transits[body]
                if script is not None:
                    instance = script.component.instances.get(instance_id)
                    if instance is not None and instance.graph.index == 0x1647:
                        script.component.destroy(instance)

    def dismiss(self, player):
        if self.combat.is_dead(player) or self.combat.is_stunned(player):
            return
        for pair in self.pairs:
            if pair.owner is player and self.combat.now >= pair.ready_at:
                self._retire(pair)
                return

    def interact(self, player):
        if self.combat.match.ended or self.combat.is_dead(player) or self.combat.is_stunned(player):
            return
        self.update()
        match = self.combat.match
        if player.body in self.transits or match.collision_world is None:
            return
        nearest = None
        radius = graph.graph(0x157D).node(1).field('m_radius')['m_value']
        for pair in self.pairs:
            if self.combat.now < pair.ready_at:
                continue
            if player is not pair.owner and (match.game_map.free_for_all or player.team != pair.owner.team):
                continue
            for index, source in enumerate(pair.endpoints):
                distance = math.dist(player.mover.position, source.position)
                if (distance <= radius and (nearest is None or distance < nearest[0])
                        and self.visible(player, source)):
                    nearest = distance, pair, index
        if nearest is None:
            return
        _, pair, index = nearest
        source, destination = pair.endpoints[index], pair.endpoints[1 - index]
        component = player.body_script.component
        x, y, z = destination.position
        use_graph = graph.graph(0x15F5)
        lift = use_graph.node(3).field('m_value')['m_expression']['m_D99EF254'][1]
        duration = expr.evaluate(use_graph.node(12).field('m_value'), expr.Context())
        point = expr.Vec3(x, y + lift, z)
        ice = self.combat.ice_wall
        movement_world = ice.movement_world(match.collision_world)
        probe = player.mover.body_at(tuple(quantise(v) for v in point), movement_world)
        if not probe.fits(probe.radius, probe.top):
            return
        # A capsule wholly inside a pillar can miss the movement overlay's triangle shell.
        lower, _ = probe.points(probe.position)
        if ice.cast(lower, (0, 1, 0), probe.top - probe.radius) is not None:
            return
        params = ((0x0A40000000000078, point), (0x0A40000000000619, duration),
                  (0x0A4000000000055A, tuple(expr.Entity(bot.entity) for bot in pair.endpoints)))
        instance = component.play_loaded_graph(graph.graph(0x1647), source=source.entity,
                                               params=params, policy=1, instigator=source.entity)
        self.transits[player.body] = instance.id, self.combat.now + duration
        component.run_now()

    def visible(self, player, source):
        # Static LOS to the authored target-probe height over the endpoint.
        eye = self.combat._eye(player)
        x, y, z = source.position
        target = (x, y + 1, z)
        distance = math.dist(eye, target)
        if distance <= 1e-6:
            return True
        direction = tuple((b - a) / distance for a, b in zip(eye, target, strict=True))
        return self.combat.match.collision_world.cast_ray(eye, direction, distance) is None
