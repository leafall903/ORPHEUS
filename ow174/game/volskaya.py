"""Match-owned reconstruction of the 104319 Volskaya lift (01DB/3CD8/2ABA)."""
import gzip
import json
import math
from dataclasses import dataclass, replace
from functools import lru_cache

from ow174.game import collision, contacts, content, mei_wall, props, world
from ow174.game.mover import STEP
from ow174.game.script import expr, graph, runtime, scene, scene_host
from ow174.paths import DATA_DIR

MAP = 0x08000000000001DB
ASSAULT = 0x0230000000000014
PLATFORM_DATA = DATA_DIR / 'volskaya-platform-104319.json.gz'
FLAT = 1931
ENTITY = 0x80000000 | FLAT
CONTROLLER = 0x2ABA
SCENE_FIELDS = frozenset((
    'hero', 'mode', 'map_guid', 'entity_start', 'entities', 'references', 'placements', 'definitions',
    'possessors', 'primary_possessors', 'modifier_components', 'scope_components', 'effect_contexts',
    'effect_targets', 'control_contexts', 'alias_components', 'look_contexts', 'data_flow_contexts',
    'script_components', 'play_queues', 'voice_contexts', 'query_contexts', 'hero_contexts',
    'team_contexts', 'message_contexts', 'lift',
))


@lru_cache(maxsize=4)
def _read_bundle(raw):
    data = json.loads(gzip.decompress(raw))
    if (data['version'], data['build'], int(data['map'], 16),
            data['platform']['flat_index'], data['graph']['index']) != (1, 104319, MAP, FLAT, CONTROLLER):
        raise ValueError('wrong Volskaya platform bundle identity')
    return data


def select(game, factory):
    """Enable the reconstructed lift for Assault when its verified external data is installed."""
    if (factory is not None or game.map_guid != MAP or game.mode_guid != ASSAULT
            or not PLATFORM_DATA.is_file()):
        return game, factory
    data = _read_bundle(PLATFORM_DATA.read_bytes())
    if FLAT not in game.placeables:
        game = replace(game, placeables=(*game.placeables, FLAT))
    return game, LiftFactory(data)


@dataclass(frozen=True)
class LiftFactory:
    data: dict

    def __call__(self, match):
        return LiftHost.from_bundle(match, self.data)


class LiftScene(scene.SceneWorld):
    def position(self, entity):
        if entity == ENTITY:
            return expr.Vec3(*self.lift.position)
        return super().position(entity)


class LiftHost(scene_host.SceneHost):
    @classmethod
    def from_bundle(cls, match, data):
        entry = content._map_entries()[MAP]
        identifiers = {int(entry['identifiers'][str(index)], 16) for index in match.game_map.placeables
                       if str(index) in entry['identifiers']}
        script_world = LiftScene.from_map(
            data['scene'], entity_start=data['first_entity'], server_placeables=match.game_map.placeables,
            kept_identifiers=identifiers, mode=match.game_map.mode_guid,
        )
        model = graph.Graph(data['graph'])
        entity = script_world.entities[ENTITY]
        definitions = script_world.definitions[entity.placement.definition]['components']['entries']
        definition, = (row['value'] for row in definitions if row['value']['$'] == 'STUStatescriptComponent')
        placed, = (row['fields'] for row in entity.placement.source['components']
                   if row['class'] == 'STUStatescriptComponentInstanceData')
        host = cls(script_world, graph_loader={CONTROLLER: model}.get)
        host.add(ENTITY, definition, placed)
        host.lift = Lift(match, data['platform'], entity.placement.source)
        script_world.lift = host.lift
        return host

    @property
    def client_ready(self):
        # Match sends this scoped scene's platform create and movement records.
        return self.prepared and not self.closed and self.error is None

    def advance(self, frame):
        if frame != self.frame:
            self.lift.velocity = 0.0
        return super().advance(frame)

    def close(self):
        if not self.closed:
            self.lift.restore_base()
        super().close()


class Lift:
    def __init__(self, match, platform, placement):
        self.match = match
        self.base = match.collision_world
        self.origin = tuple(placement['position'])
        self.position = self.origin
        self.scale = placement['scale']
        self.rotation = placement['rotation']
        self.models = [[platform['shape']]]
        points = [row['position'] for row in platform['path']]
        self.offsets = tuple(point[1] - points[0][1] for point in points)
        self.route_length = sum(abs(after - before) for before, after in zip(self.offsets, self.offsets[1:]))
        if self.route_length <= 0:
            raise ValueError('Volskaya lift route has no travel')
        self.segment = None
        self.velocity = 0.0
        self.blocked_riders = set()
        self.attached_walls = set()
        self._install_pose()

    def _set_world(self, scene):
        self.match.collision_world = scene
        for player in self.match.players:
            player.mover.use(world=scene)

    def restore_base(self):
        self._set_world(self.base)

    def _install_pose(self):
        placements = [(0, 0, self.position, self.scale, self.rotation)]
        triangles, masks, surfaces = collision.probe_triangles([], [], self.models, placements)
        engine = collision.engine_world([], [], self.models, placements)
        self.collider = collision.World(triangles, masks, surfaces, 'Volskaya lift', engine)
        self._set_world(collision.LayeredWorld(self.base, self.collider))

    def supports_wall(self, piece):
        y = piece.feet[1]
        for x, z in mei_wall.Walls.footprint(piece):
            moving = self.collider.ground(x, z, y + .5, y - .5)
            fixed = self.base.ground(x, z, y + .5, y - .5)
            if moving is None or moving.normal[1] < .7 or (fixed is not None and fixed.y >= moving.y):
                return False
        return True

    def wall_movement(self, piece, angle):
        from ow174.game.projectiles import KEEP_ROTATION

        dx, dy, dz = (a - b for a, b in zip(piece.feet, self.position, strict=True))
        qx, qy, qz, qw = self.rotation
        relative = props._rotate((-qx, -qy, -qz, qw), (dx, dy, dz))
        yaw = round(angle * 65536 / (2 * math.pi))
        return world.Movement(relative, yaw=yaw, flags=KEEP_ROTATION)

    def _walls_can_move(self, attached, step):
        pieces = self.match.combat.walls.pieces.values()
        for piece in attached:
            x, y, z = piece.feet
            swept = replace(piece, feet=(x, min(y, y + step), z), height=piece.height + abs(step))
            if not mei_wall.Walls.volume_clear(swept, self.base):
                return False
            if any(other.entity not in self.attached_walls and mei_wall.overlap(swept, other) for other in pieces):
                return False
        return True

    def _riders(self, attached):
        for player in self.match.players:
            if player.gone or not player.spawned or not player.has_body:
                continue
            mover = player.mover
            blocked = player.body in self.blocked_riders
            if mover.airborne and not blocked:
                continue
            x, y, z = mover.position
            # Static contact can push feet off the exact lift plane after the
            # sweep stops. Keep that obstruction while the rider remains aboard.
            reach = max(STEP, mover.values.step_down) if blocked else STEP
            support = self.collider.ground(x, z, y + reach, y - reach, mover.category)
            wall = self.match.combat.walls.ground(player, attached)
            if wall is not None and abs(wall.y - y) <= reach and (support is None or wall.y > support.y):
                support = wall
            if support is None or support.normal[1] <= .7:
                continue
            fixed = self.base.ground(x, z, y + STEP, y - STEP, mover.category)
            if fixed is None or fixed.y < support.y:
                yield player

    def _carry_step(self, step, riders):
        """Hold the lift short of static contact rather than move its rider into it."""
        self.blocked_riders.intersection_update(player.body for player in riders)
        if self.blocked_riders:
            return 0.0
        available = abs(step)
        for player in riders:
            mover = player.mover
            x, y, z = mover.position
            radius, segment = mover._capsule()
            lower = mover._lift() + radius
            points = ((0., lower, 0.), (0., lower + segment, 0.))
            hit = contacts.scene_sweep(self.base.engine, (x, y, z), (x, y + step, z),
                                       points, radius, mover.category)
            if hit is not None:
                self.blocked_riders.add(player.body)
                available = min(available, max(0., abs(step) * hit[0] - STEP))
        return math.copysign(available, step)

    def move(self, speed, seconds):
        if self.segment is None:
            return True
        x, y, z = self.position
        distance = self.origin[1] + self.offsets[self.segment] - y
        step = math.copysign(min(speed * seconds, abs(distance)), distance)
        walls = self.match.combat.walls
        attached = tuple(piece for piece in walls.pieces.values() if piece.entity in self.attached_walls)
        if step and not self._walls_can_move(attached, step):
            return False
        riders = tuple(self._riders(attached)) if step else ()
        if step:
            step = self._carry_step(step, riders)
        if step:
            for player in riders:
                px, py, pz = player.mover.position
                walls.place_player(player, (px, py + step, pz), ((0., 1., 0.),))
            for piece in attached:
                px, py, pz = piece.feet
                piece.feet = (px, py + step, pz)
            self.position = (x, y + step, z)
            self.velocity = step / seconds
            self._install_pose()
        if abs(step) >= abs(distance):
            self.segment += 1
            if self.segment == len(self.offsets):
                self.segment = None
                return True
        return False

    def progress(self):
        if self.segment is None:
            return 1.0
        travelled = sum(abs(self.offsets[i] - self.offsets[i - 1]) for i in range(1, self.segment))
        start = self.origin[1] + self.offsets[self.segment - 1]
        return min(1.0, (travelled + abs(self.position[1] - start)) / self.route_length)

    def movement(self, frame=None):
        yaw = round(math.atan2(self.rotation[1], self.rotation[3]) * 65536 / math.pi)
        yaw = (yaw + 32768) % 65536 - 32768
        return world.Movement(self.position, yaw=yaw, velocity=(0., self.velocity, 0.), frame=frame)


def tracks(state):
    return (isinstance(state.instance.component.world, LiftScene)
            and state.instance.graph.index == CONTROLLER and state.node.rid in (6, 21))


def targets(state):
    host = state.instance.component.world
    radius = state.evaluate(state.node.field('m_radius'))
    centre = state.evaluate(state.node.field('m_D59188D5'))
    nearby = tuple(expr.Entity(target.owner.body) for target in host.lift.match.combat._targets()
                   if getattr(target.owner, 'mover', None) is not None
                   and math.dist(tuple(centre), target.feet) <= radius)
    if state.node.rid == 6:
        previous = state.instance.find_var(0, 31823).value()
        return tuple(entity for entity in previous if entity in nearby) if isinstance(previous, tuple) else ()
    return nearby


@runtime.handler('STU_4A4BEDAA')
def reset_path(instance, node, caller):
    host = instance.component.world
    if not isinstance(host, LiftScene) or instance.graph.index != CONTROLLER or node.rid != 23:
        return NotImplemented
    if (instance.evaluate(node.field('m_targets')) != expr.Entity(ENTITY)
            or instance.evaluate(node.field('m_AEE4E389')) != 0):
        return NotImplemented
    host.lift.segment = 1
    instance.follow(node, 'm_outPlug', caller)
    return True


@runtime.state_class('STU_49FE0019')
class LiftMotion(runtime.State):
    def on_begin(self):
        component = self.instance.component
        if not isinstance(component.world, LiftScene) or self.instance.graph.index != CONTROLLER:
            if component.strict:
                component.unsupported(self.instance, 'state', self.node.cls, self.node)
            return
        self.last = self.now
        self.progress_slot = expr.lvalue(self.node.field('m_A3D27CCA'))
        self.instance.write(self.progress_slot, 0.0)
        self.ticks()

    def tick(self):
        elapsed = self.now - self.last
        self.last = self.now
        speed = self.evaluate(self.node.field('m_speed'))
        if elapsed > 0 and speed > 0:
            lift = self.instance.component.world.lift
            finished = lift.move(speed, elapsed * .001)
            # The authored controller compares the output against .5 and 1. Use
            # completed route distance as the reconstructed progress value.
            self.instance.write(self.progress_slot, expr.f32(lift.progress()))
            if finished:
                # Rearm the placed controller after the reconstructed round trip.
                # The native movement callback that resets its phase is unhosted.
                self.instance.write((0, 2034), 0)
                self.request_finish()

