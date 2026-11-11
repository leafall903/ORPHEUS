"""Static Ice Wall actor host for the authored 0622 creation and 0636 pillar graphs."""

import math
from dataclasses import dataclass
from functools import partial

from ow174.game import collision, contacts, immunity
from ow174.game.script import expr, graph, play, play_queue, runtime

CAST_GRAPH, PILLAR_GRAPH = 0x0622, 0x0636
PILLAR_BODY = 0x0400000000000932
CREATES = (133, 136, 139, 142, 145)
PLAYS = (66, 137, 138, 143, 144)
HALF_DEPTH = 0.5
CONTACT_MARGIN = 0.001
PLACEMENT_MARGIN = 0.0001


@dataclass
class Pillar:
    entity: int
    owner: int
    team: int
    feet: tuple
    axis: tuple
    width: float
    height: float
    born: int
    last_tick: int
    component: runtime.Component | None = None
    health: float = 0.0
    expires: int = 0


@dataclass
class Input:
    buttons: int = 0
    preview: bool = False
    rotated: bool = False


def _local(piece, vector):
    x, _, z = piece.axis
    return vector[0] * x + vector[2] * z, vector[1], -vector[0] * z + vector[2] * x


def _overlap(a, b):
    if (a.feet[1] + a.height <= b.feet[1] + PLACEMENT_MARGIN
            or b.feet[1] + b.height <= a.feet[1] + PLACEMENT_MARGIN):
        return False
    delta = tuple(b.feet[k] - a.feet[k] for k in range(3))
    for axis in (a.axis, (-a.axis[2], 0, a.axis[0]), b.axis, (-b.axis[2], 0, b.axis[0])):
        separation = abs(delta[0] * axis[0] + delta[2] * axis[2])
        ax, _, az = _local(a, axis)
        bx, _, bz = _local(b, axis)
        span = (abs(ax) * a.width / 2 + abs(bx) * b.width / 2
                + (abs(az) + abs(bz)) * HALF_DEPTH)
        if separation >= span - PLACEMENT_MARGIN:
            return False
    return True


def _hit(piece, origin, direction, reach, radius=0.0, below=0.0, above=0.0):
    """Ray against a reconstructed pillar box expanded for moving capsules."""
    at = _local(piece, tuple(a - b for a, b in zip(origin, piece.feet, strict=True)))
    direction = _local(piece, direction)
    lows = (-piece.width / 2 - radius, -below, -HALF_DEPTH - radius)
    highs = (piece.width / 2 + radius, piece.height + above, HALF_DEPTH + radius)
    enter, leave = 0.0, reach
    normal = (0.0, 0.0, 0.0)
    for axis, delta in enumerate(direction):
        if abs(delta) < 1e-8:
            if not lows[axis] <= at[axis] <= highs[axis]:
                return None
            continue
        a = (lows[axis] - at[axis]) / delta
        b = (highs[axis] - at[axis]) / delta
        near = min(a, b)
        if near >= enter:
            enter = near
            normal = tuple((-1.0 if delta > 0 else 1.0) if k == axis else 0.0 for k in range(3))
        leave = min(leave, max(a, b))
        if enter > leave:
            return None
    x, _, z = piece.axis
    nx, ny, nz = normal
    return (enter, (nx * x - nz * z, ny, nx * z + nz * x)) if leave >= 0 else None


def _value(instance, key):
    return expr.to_float(instance.find_var(expr.INSTANCE, key).value())


def clip_mover(mover, point):
    mover.velocity = (0.0, 0.0, 0.0)
    _place_mover(mover, point)


def _place_mover(mover, point):
    mover.position[:] = point
    if mover.body is not None:
        mover.body.teleport((point[0], point[1] + mover._lift(), point[2]))
    if mover.history:
        mover.history[-1] = mover.snapshot(mover.history[-1].frame)


class Walls:
    def __init__(self, fight):
        self.fight = fight
        self.pieces = {}
        self.inputs = {}
        self.building = None

    def _instance(self, player):
        if player.hero.guid != immunity.MEI or player.body_script is None:
            return None
        component = player.body_script.component
        if component.strict:
            return None
        return next((item for item in component.instances.values() if item.graph.index == CAST_GRAPH), None)

    def command(self, player, command):
        instance = self._instance(player)
        if instance is None or command is None:
            return False
        state = self.inputs.setdefault(player.body, Input())
        pressed = command.buttons & ~state.buttons
        state.buttons = command.buttons
        ability = instance.states.get(instance.graph.node(56).state)
        if ability is None:
            return False
        if pressed & 16:
            owned = [piece for piece in self.pieces.values() if piece.owner == player.body]
            if owned or state.preview:
                for piece in owned:
                    self.drop(piece)
                self._end_preview(instance, ability, state)
                return True
            if ability.flags & ability.RUNNING and ability.remaining() <= 0:
                state.preview, state.rotated = True, False
                self._hold_weapon(instance, True)
        if not state.preview:
            return False
        if immunity.cryo_active(player, player.body):
            self._end_preview(instance, ability, state)
            return True
        if pressed & 2:
            # The permissive VM drops its preview stack on this input. Keep
            # the static placement preview until confirmation or cancellation.
            state.rotated = not state.rotated
        if pressed & 1 and self._place(instance, player, command, state.rotated):
            instance.write((expr.INSTANCE, 61), _value(instance, 472))
            self._end_preview(instance, ability, state)
        elif state.preview:
            self._hold_weapon(instance, True)
        return True

    def cancel_preview(self, player):
        state = self.inputs.get(player.body)
        if state is None or not state.preview:
            return
        instance = self._instance(player)
        if instance is not None:
            ability = instance.states.get(instance.graph.node(56).state)
            if ability is not None:
                self._end_preview(instance, ability, state)

    def _place(self, instance, player, command, rotated):
        from ow174.game.combat import aim

        scene = self.fight._collision()
        yaw, pitch = command.aim()
        hit = scene.cast_ray(self.fight._eye(player), aim(yaw, pitch), 35)
        if hit is None or hit.normal[1] < 0.7 or graph.graph(PILLAR_GRAPH) is None:
            return False
        axis = aim(yaw if rotated else yaw + 16384, 0)
        for slot, value in ((2478, expr.Vec3(*hit.point)), (6503, expr.Vec3(*axis))):
            instance.write((expr.INSTANCE, slot), value)
        before = set(self.pieces)
        self.building = (instance, player, axis)
        complete = False
        try:
            for position in CREATES:
                instance.run_node(instance.graph.node(position), None)
            instance.component.run_now()
            complete = True
        finally:
            self.building = None
            for entity in self.pieces.keys() - before:
                piece = self.pieces[entity]
                if not complete or piece.component is None:
                    self.drop(piece)
        placed = [piece for entity, piece in self.pieces.items() if entity not in before]
        self._lift_occupants(placed)
        return bool(placed)

    @staticmethod
    def _hold_weapon(instance, enabled):
        holder = instance.states.get(instance.graph.node(4).state)
        if holder is None:
            return
        for button in (20, 29):
            holders = instance.component.disabled.setdefault(button, [])
            if enabled and holder not in holders:
                holders.append(holder)
            elif not enabled and holder in holders:
                holders.remove(holder)
            if not holders:
                instance.component.disabled.pop(button, None)

    def _end_preview(self, instance, ability, state):
        from ow174.game.script.nodes import Ability, LogicalButton

        state.preview = False
        ability.stop()
        holder = instance.states.get(instance.graph.node(4).state)
        if holder is not None and holder.active:
            holder.end(False)
        self._hold_weapon(instance, False)
        component = instance.component
        for other in component.instances.values():
            if other.graph.index != 0x05E6:
                continue
            for node_state in other.states.values():
                if isinstance(node_state, Ability):
                    node_state.presses[:] = [
                        press for press in node_state.presses if press[1] not in (20, 29)
                    ]
                elif isinstance(node_state, LogicalButton) and node_state.button_id() in (20, 29):
                    node_state.down, node_state.pressed_at = False, None
        component.run_now()

    def create(self, instance, node):
        if self.building is None or self.building[0] is not instance or node.pos not in CREATES:
            return NotImplemented
        definition = node.field("m_entityDefinition")
        if expr.guid_of(definition["m_entityDef"]) != PILLAR_BODY:
            return NotImplemented
        _, player, axis = self.building
        point = instance.evaluate(node.field("m_position"))
        output = expr.lvalue(node.field("m_B3019338"))
        if not isinstance(point, expr.Vec3) or output is None:
            return False
        scene = self.fight._collision()
        support = scene.ground(point.x, point.z, point.y + 0.5, point.y - 0.5)
        height = _value(instance, 1165)
        if support is None or support.normal[1] < 0.7:
            instance.write(output, expr.Entity(0))
            return False
        piece = Pillar(0, player.body, player.team, (point.x, support.y, point.z),
                       axis, _value(instance, 6274), height, self.fight.tick, self.fight.tick)
        if not self._placement_clear(piece, scene):
            instance.write(output, expr.Entity(0))
            return False
        entity = self.fight.next_bot
        if entity >= 0xD0000000:
            raise ValueError("wall entity allocator exhausted")
        self.fight.next_bot += 1
        piece.entity = entity
        self.pieces[entity] = piece
        instance.write(output, expr.Entity(entity))
        return True

    def _placement_clear(self, piece, scene):
        """Require supported footprint samples and a clear raised pillar volume."""
        if any(_overlap(piece, other) for other in self.pieces.values()):
            return False
        x, y, z = piece.feet
        ax, _, az = piece.axis
        half = piece.width / 2
        for along in (-half + PLACEMENT_MARGIN, 0, half - PLACEMENT_MARGIN):
            for across in (-HALF_DEPTH + PLACEMENT_MARGIN, 0, HALF_DEPTH - PLACEMENT_MARGIN):
                ground = scene.ground(x + along * ax - across * az, z + along * az + across * ax,
                                      y + 0.5, y - 0.5)
                if ground is None or ground.normal[1] < 0.7:
                    return False
        rx = abs(ax) * half + abs(az) * HALF_DEPTH
        rz = abs(az) * half + abs(ax) * HALF_DEPTH
        bounds = (x - rx, x + rx, y + PLACEMENT_MARGIN, y + piece.height, z - rz, z + rz)
        for triangle in scene.nearby(*bounds):
            vertices = [_local(piece, (triangle[k] - x, triangle[k + 1] - y, triangle[k + 2] - z))
                        for k in (collision.AX, collision.BX, collision.CX)]
            # Walkable terrain may occupy the half-metre support band.
            low_y = 0.5 if triangle[collision.NY] >= 0.7 else PLACEMENT_MARGIN
            if contacts.triangle_box(*vertices, (-half, low_y, -HALF_DEPTH),
                                     (half, piece.height, HALF_DEPTH)):
                return False
        for _, mover in self._occupants(piece):
            px, py, pz = mover.position
            radius, segment = mover._capsule()
            lift = mover._lift() + radius
            start = (px, py + lift, pz)
            end = (px, y + piece.height + lift, pz)
            if contacts.scene_sweep(scene.engine, start, end, ((0, 0, 0), (0, segment, 0)),
                                    radius, mover.category, hit_touching=True) is not None:
                return False
        return True

    @staticmethod
    def _covers(piece, x, z, radius):
        along, _, across = _local(piece, (x - piece.feet[0], 0.0, z - piece.feet[2]))
        return abs(along) <= piece.width / 2 + radius and abs(across) <= HALF_DEPTH + radius

    def _occupants(self, piece):
        from ow174.game.mover import mover_data

        for player in self.fight.match.players:
            if not player.spawned or not player.has_body or player.gone or self.fight.is_dead(player):
                continue
            mover = player.mover
            x, y, z = mover.position
            radius, _ = mover._capsule()
            if (piece.feet[1] - CONTACT_MARGIN <= y < piece.feet[1] + piece.height
                    and self._covers(piece, x, z, radius)):
                yield player, mover
        for bot in self.fight.match.bots:
            x, y, z = bot.position
            if bot.dead or not piece.feet[1] - CONTACT_MARGIN <= y < piece.feet[1] + piece.height:
                continue
            if self._covers(piece, x, z, mover_data(bot.body).stand_radius):
                yield bot, self.fight.impulses.npc_mover(bot)

    def _lift_occupants(self, pieces):
        """Resolve the rise as a static lift after all valid pillars have been hosted."""
        from ow174.game import bots
        from ow174.game.mover import IN_AIR

        heights = {}
        for piece in pieces:
            top = piece.feet[1] + piece.height
            for owner, mover in self._occupants(piece):
                previous = heights.get(owner)
                if previous is None or top > previous[1]:
                    heights[owner] = mover, top
        for owner, (mover, top) in heights.items():
            x, _, z = mover.position
            vx, _, vz = mover.velocity
            mover.velocity = (vx, 0.0, vz)
            mover.flags_word &= ~IN_AIR
            mover.fall, mover.air_ticks, mover.landed = 0.0, 0, True
            _place_mover(mover, (x, top, z))
            if isinstance(owner, bots.Bot):
                impulses = self.fight.impulses
                impulses.track_npc(owner, mover)
                impulses.publish_npc(owner, mover, self.fight.tick)

    def ground(self, x, z, top, bottom, radius):
        height = None
        for piece in self.pieces.values():
            y = piece.feet[1] + piece.height
            if (self._active(piece) and bottom <= y <= top and self._covers(piece, x, z, radius)
                    and (height is None or y > height)):
                height = y
        return None if height is None else collision.Ground.at((x, height, z), (0, 1, 0))

    def supports(self, mover):
        if not self.pieces:
            return False
        x, y, z = mover.position
        radius, _ = mover._capsule()
        return self.ground(x, z, y + CONTACT_MARGIN, y - CONTACT_MARGIN, radius) is not None

    def step(self, mover, command, seconds, tick, *, top_only=False):
        pose = partial(self.step_pose, mover, top_only=top_only) if self.pieces else None
        mover.step(command, seconds, tick, simulate_pose=pose)

    def step_pose(self, mover, command, seconds, tick, *, move_to=None, top_only=False):
        before = tuple(mover.position)
        result = mover.step_pose(command, seconds, tick, move_to=move_to,
                                 ground_at=self.ground if self.pieces else None)
        blocked = self.constrain(mover, before, top_only=top_only)
        return False if move_to is not None and blocked else result

    def host(self, instance, node):
        if self.building is None or self.building[0] is not instance or node.pos not in PLAYS:
            return False
        resource = play.resolve_resource(instance, node)
        if resource is None or resource.guid != 0x0580000000000636:
            return False
        owner = instance.component.entity
        request = play.resolve_request(instance, node, resource,
                                       context_provider=lambda: play.InvocationContext(owner, owner, owner))
        if request is None:
            return True
        from ow174.game.combat import TICK

        definition = graph.graph(PILLAR_GRAPH)
        for entity in request.targets:
            piece = self.pieces.get(entity)
            if piece is None or piece.owner != owner or piece.component is not None:
                continue
            component = runtime.Component(entity)
            component.frame, component.now = instance.component.frame, instance.component.now
            queue = play_queue.Queue(component, lambda _guid: play_queue.Load("ready", definition))
            queue.submit(request)
            component.run_now()
            hosted = next(item for item in component.instances.values() if item.graph.index == PILLAR_GRAPH)
            pool = hosted.states[hosted.graph.node(1).state]
            piece.health = expr.to_float(pool.evaluate(pool.node.field("m_amount"), watch=False))
            piece.expires = piece.born + math.ceil(_value(hosted, 215) / TICK)
            piece.component = component
        return True

    def drop(self, piece):
        self.pieces.pop(piece.entity, None)
        if piece.component is not None:
            for instance in tuple(piece.component.instances.values()):
                piece.component.destroy(instance)

    def retire(self, body):
        self.inputs.pop(body, None)
        for piece in tuple(self.pieces.values()):
            if piece.owner == body:
                self.drop(piece)

    def update(self, tick):
        for piece in tuple(self.pieces.values()):
            elapsed = min(tick, piece.expires) - piece.last_tick
            if piece.component is not None and elapsed > 0:
                component = piece.component
                component.frame += elapsed
                component.now += elapsed * component.frame_ms
                component.update()
                component.run_now()
                piece.last_tick += elapsed
            if tick >= piece.expires:
                self.drop(piece)

    def hostile(self, shooter, piece):
        owner = getattr(shooter, "combat_owner", shooter)
        return (getattr(owner, "body", None) != piece.owner
                and (self.fight.match.game_map.free_for_all or shooter.team != piece.team))

    @staticmethod
    def marked(piece):
        if piece.component is None:
            return False
        slot = piece.component.entity_var(961, create=False)
        return slot is not None and slot.value() is True

    def _active(self, piece):
        return piece.component is not None and piece.born <= self.fight.tick < piece.expires

    def cast(self, origin, direction, reach, *, radius=0.0, vertical=0.0):
        best = None
        for piece in self.pieces.values():
            if not self._active(piece):
                continue
            hit = _hit(piece, origin, direction, reach, radius, vertical, vertical)
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit[0], piece, hit[1]
        return best

    def sweep(self, start, end, *, radius, y_offset):
        delta = tuple(b - a for a, b in zip(start, end, strict=True))
        length = math.hypot(*delta)
        if length <= 1e-8:
            return None
        direction = tuple(value / length for value in delta)
        origin = (start[0], start[1] + y_offset, start[2])
        hit = self.cast(origin, direction, length, radius=radius)
        if hit is None:
            return None
        distance, piece, _ = hit
        return tuple(a + value * distance for a, value in zip(start, direction, strict=True)), piece

    def constrain(self, mover, before, *, top_only=False):
        """Project a moved capsule onto pillar faces and preserve tangential momentum."""
        from ow174.game.mover import IN_AIR, TOUCHING

        if not self.pieces:
            return False
        radius, segment = mover._capsule()
        height = mover._lift() + segment + 2 * radius
        after = tuple(mover.position)
        touched = False
        for _ in range(3):
            delta = tuple(b - a for a, b in zip(before, after, strict=True))
            length = math.hypot(*delta)
            if length <= 1e-8:
                break
            direction = tuple(value / length for value in delta)
            best = None
            for piece in self.pieces.values():
                if not self._active(piece):
                    continue
                if min(before[1], after[1]) >= piece.feet[1] + piece.height - CONTACT_MARGIN:
                    continue
                hit = _hit(piece, before, direction, length, radius, below=height)
                if top_only and hit is not None and hit[1][1] <= 0.7:
                    continue  # Charge keeps its own horizontal contact and damage query.
                if hit is not None and any(hit[1]) and (best is None or hit[0] < best[0]):
                    best = hit
            if best is None:
                break
            distance, normal = best
            remaining = length - distance
            into = sum(a * b for a, b in zip(direction, normal, strict=True))
            offset = CONTACT_MARGIN - remaining * into
            after = tuple(a + b * offset for a, b in zip(after, normal, strict=True))
            speed = sum(a * b for a, b in zip(mover.velocity, normal, strict=True))
            if speed < 0:
                mover.velocity = tuple(a - b * speed for a, b in zip(mover.velocity, normal, strict=True))
            if normal[1] > 0.7:
                mover.flags_word &= ~IN_AIR
                mover.fall, mover.air_ticks, mover.landed = 0.0, 0, True
            touched = True
        if touched:
            mover.flags_word |= TOUCHING
            _place_mover(mover, after)
        return touched

    def blast(self, shooter, origin, amount_at):
        """Damage visible pillar surfaces using the existing blast falloff."""
        self._area(shooter, origin, origin, amount_at)

    def melee(self, shooter, origin, centre, radius, amount):
        self._area(shooter, origin, centre, lambda distance: amount if distance <= radius else 0)

    def _area(self, shooter, origin, centre, amount_at):
        from ow174.game.combat import ray_world

        damage = []
        for piece in self.pieces.values():
            if not self._active(piece) or not self.hostile(shooter, piece):
                continue
            x, y, z = _local(piece, tuple(a - b for a, b in zip(centre, piece.feet, strict=True)))
            x = max(-piece.width / 2, min(piece.width / 2, x))
            y = max(0.0, min(piece.height, y))
            z = max(-HALF_DEPTH, min(HALF_DEPTH, z))
            fx, fy, fz = piece.feet
            ax, _, az = piece.axis
            point = (fx + x * ax - z * az, fy + y, fz + x * az + z * ax)
            amount = amount_at(math.dist(point, centre))
            if amount <= 0:
                continue
            delta = tuple(a - b for a, b in zip(point, origin, strict=True))
            distance = math.hypot(*delta)
            if distance > 1e-6:
                direction = tuple(value / distance for value in delta)
                wall = ray_world(self.fight._collision(), origin, direction, distance)
                ice = self.cast(origin, direction, distance)
                if ((wall is not None and wall < distance - 1e-6)
                        or (ice is not None and ice[1] is not piece and ice[0] < distance - 1e-6)):
                    continue
            damage.append((piece, amount))
        for piece, amount in damage:
            self.damage(piece, shooter, amount)

    def blocks(self, origin, point):
        if not self.pieces:
            return False
        delta = tuple(b - a for a, b in zip(origin, point, strict=True))
        distance = math.hypot(*delta)
        if distance <= 1e-6:
            return False
        direction = tuple(value / distance for value in delta)
        hit = self.cast(origin, direction, distance)
        return hit is not None and hit[0] < distance - 1e-6

    def damage(self, piece, shooter, amount):
        if (self.pieces.get(piece.entity) is not piece or not self._active(piece)
                or not self.hostile(shooter, piece) or amount <= 0):
            return 0.0
        dealt = min(piece.health, amount)
        piece.health -= dealt
        if piece.health <= 0:
            self.drop(piece)
        return dealt
