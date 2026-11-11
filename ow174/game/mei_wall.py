"""Mei Ice Wall server reconstruction, distinct from native placement/physics hosting.

0622 supplies five columns, width1.55, depth0.5, height4.4, duration5 and
cooldown12. 0636 supplies400 health; actual pillar definition is0932. The
oriented boxes, weapon contacts and swept player contacts here are reconstructed consumers.
They do not mutate shared map collision assets or claim native client parity.
"""

import math
from dataclasses import dataclass

from ow174.game import bots, collision, contacts, mei_frost
from ow174.game.collision import RayHit
from ow174.game.mover import IN_AIR, mover_data
from ow174.game.script import expr, nodes

DEFINITION = 0x0400000000000932
EPS = 1e-5


@dataclass
class Pillar:
    entity: int
    owner: int
    team: int
    feet: tuple
    axis: tuple
    health: float
    expires: int
    width: float = 1.55
    depth: float = 0.5
    height: float = 4.4


@dataclass
class Input:
    buttons: int = 0
    preview: bool = False
    rotated: bool = False


def local(pillar, vector):
    x, _y, z = pillar.axis
    return vector[0] * x + vector[2] * z, vector[1], -vector[0] * z + vector[2] * x


def global_vector(pillar, vector):
    x, _y, z = pillar.axis
    return vector[0] * x - vector[2] * z, vector[1], vector[0] * z + vector[2] * x


def ray(pillar, origin, direction, length, *, radius=0.0, height=0.0):
    point = local(pillar, tuple(a - b for a, b in zip(origin, pillar.feet, strict=True)))
    direction = local(pillar, direction)
    low = (-pillar.width / 2 - radius, -height, -pillar.depth - radius)
    high = (pillar.width / 2 + radius, pillar.height, pillar.depth + radius)
    enter, leave, normal = 0.0, length, (0.0, 0.0, 0.0)
    for axis in range(3):
        if abs(direction[axis]) < EPS:
            if point[axis] < low[axis] or point[axis] > high[axis]:
                return None
            continue
        near, far = (low[axis] - point[axis]) / direction[axis], (high[axis] - point[axis]) / direction[axis]
        sign = -1.0
        if near > far:
            near, far, sign = far, near, 1.0
        if near >= enter:
            enter = near
            normal = tuple(sign if index == axis else 0.0 for index in range(3))
        leave = min(leave, far)
        if enter > leave:
            return None
    if leave <= EPS or enter > length:
        return None
    return max(0.0, enter), global_vector(pillar, normal)


def closest(pillar, point):
    p = local(pillar, tuple(a - b for a, b in zip(point, pillar.feet, strict=True)))
    bounded = (max(-pillar.width / 2, min(pillar.width / 2, p[0])),
               max(0.0, min(pillar.height, p[1])), max(-pillar.depth, min(pillar.depth, p[2])))
    offset = global_vector(pillar, bounded)
    return tuple(a + b for a, b in zip(pillar.feet, offset, strict=True))


def overlap(a, b):
    """Strict OBB volume overlap; touching seams/top faces remain admissible."""
    if (a.feet[1] + a.height <= b.feet[1] + EPS
            or b.feet[1] + b.height <= a.feet[1] + EPS):
        return False
    delta = tuple(y - x for x, y in zip(a.feet, b.feet, strict=True))
    for axis in (a.axis, (-a.axis[2], 0., a.axis[0]), b.axis, (-b.axis[2], 0., b.axis[0])):
        separation = abs(sum(x * y for x, y in zip(delta, axis, strict=True)))
        radii = []
        for piece in (a, b):
            along = abs(piece.axis[0] * axis[0] + piece.axis[2] * axis[2])
            across = abs(-piece.axis[2] * axis[0] + piece.axis[0] * axis[2])
            radii.append(piece.width / 2 * along + piece.depth * across)
        if separation >= sum(radii) - EPS:
            return False
    return True


class Walls:
    def __init__(self, combat):
        self.combat = combat
        self.pieces: dict[int, Pillar] = {}
        self.inputs: dict[int, Input] = {}
        self.anchors = {}

    def instance(self, player):
        script = getattr(player, "body_script", None)
        if script is None or getattr(script, "hero", None) != mei_frost.HERO:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == 0x0622), None)

    def cast(self, origin, direction, length):
        best = None
        for piece in self.pieces.values():
            hit = ray(piece, origin, direction, length)
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit[0], piece, hit[1]
        return best

    def contact(self, origin, direction, reach, static_hit):
        wall = self.cast(origin, direction, static_hit.distance if static_hit is not None else reach)
        if wall is None or (static_hit is not None and wall[0] >= static_hit.distance):
            return static_hit, None
        distance, piece, normal = wall
        point = tuple(x + d * distance for x, d in zip(origin, direction, strict=True))
        return RayHit(distance, point, normal, 0, 0), piece

    def distance(self, origin, direction, reach, static_distance):
        hit = self.cast(origin, direction, reach if static_distance is None else static_distance)
        return hit[0] if hit is not None else static_distance

    def blocked(self, origin, destination):
        delta = tuple(b - a for a, b in zip(origin, destination, strict=True))
        length = math.sqrt(sum(axis * axis for axis in delta))
        if length < EPS:
            return False
        direction = tuple(axis / length for axis in delta)
        start = tuple(a + EPS * d for a, d in zip(origin, direction, strict=True))
        hit = self.cast(start, direction, max(0.0, length - 2 * EPS))
        return hit is not None

    def placement(self, player, command, rotated):
        from ow174.game.combat import aim

        origin, direction = self.combat._eye(player), aim(*command.aim())
        hit = self.combat.match.collision_world.cast_ray(origin, direction, 35.0)
        if hit is None or hit.normal[1] < 0.7:
            return None
        where = hit.point
        yaw = command.yaw * 2 * math.pi / 65536
        axis = (math.sin(yaw), 0.0, math.cos(yaw)) if rotated else (math.cos(yaw), 0.0, -math.sin(yaw))
        points = []
        for offset in range(-2, 3):
            x, z = where[0] + axis[0] * offset * 1.55, where[2] + axis[2] * offset * 1.55
            ground = self.combat.match.collision_world.ground(x, z, where[1] + 0.5, where[1] - 0.5)
            if ground is None or ground.normal[1] < 0.7:
                continue
            foot = (x, ground.y, z)
            ceiling = self.combat.match.collision_world.cast_ray(
                (x, ground.y + 0.01, z), (0.0, 1.0, 0.0), 4.39,
            )
            candidate = Pillar(0, player.body, player.team, foot, axis, 400., 0)
            if ceiling is None and self.placement_clear(candidate):
                points.append(foot)
        return (points, axis) if points else None

    def placement_clear(self, piece):
        """Reconstructed support/volume policy, using existing static triangle SAT.

        Nine footprint samples require walkable support within the existing
        +/-0.5m ground tolerance. Walkable terrain can enter that foundation
        band; other surfaces must clear the entire raised pillar interior.
        Invalid columns are omitted, preserving the existing partial-wall policy.
        """
        scene = self.combat.match.collision_world
        half = piece.width / 2
        for x in (-half + EPS, 0., half - EPS):
            for z in (-piece.depth + EPS, 0., piece.depth - EPS):
                offset = global_vector(piece, (x, 0., z))
                ground = scene.ground(piece.feet[0] + offset[0], piece.feet[2] + offset[2],
                                      piece.feet[1] + .5, piece.feet[1] - .5)
                if ground is None or ground.normal[1] < .7:
                    return False
        if any(overlap(piece, other) for other in self.pieces.values()):
            return False
        rx = abs(piece.axis[0]) * half + abs(piece.axis[2]) * piece.depth
        rz = abs(piece.axis[2]) * half + abs(piece.axis[0]) * piece.depth
        x, y, z = piece.feet
        for triangle in scene.nearby(x - rx, x + rx, y + EPS, y + piece.height, z - rz, z + rz):
            vertices = [local(piece, tuple(v - f for v, f in zip(triangle[k:k + 3], piece.feet,
                                                               strict=True)))
                        for k in (collision.AX, collision.BX, collision.CX)]
            base = .5 if triangle[collision.NY] >= .7 else EPS
            if contacts.triangle_box(*vertices, (-half, base, -piece.depth),
                                     (half, piece.height, piece.depth)):
                return False
        return True

    @staticmethod
    def stop_preview(instance):
        instance.state(33).stop()
        # The authored on-start plug creates this stack outside Ability's own
        # subgraph. Its normal .25s stop-wait also aborts state4 explicitly.
        stack = instance.states.get(4)
        if stack is not None and stack.active:
            stack.end(False)
        instance.component.run_now()
        Walls.clear_fire(instance.component)
        for button in (20, 29):
            holders = instance.component.disabled.get(button, [])
            holders[:] = [holder for holder in holders if holder is not stack]
            if not holders:
                instance.component.disabled.pop(button, None)

    @staticmethod
    def clear_fire(component):
        # UI confirmation/rotation presses must not survive as buffered weapon
        # requests after releasing the preview's character-action stack.
        from ow174.game.script.nodes import Ability, LogicalButton

        component.held[:] = [button for button in component.held if button not in (20, 29, 111, 112)]
        for instance in component.instances.values():
            if instance.graph.index == 0x05E6:
                for state in instance.states.values():
                    if isinstance(state, Ability):
                        state.presses[:] = [press for press in state.presses if press[1] not in (20, 29)]
                    elif isinstance(state, LogicalButton) and state.button_id() in (20, 29):
                        state.down, state.pressed_at = False, None

    @staticmethod
    def disable_fire(instance):
        # Wall UI LogicalButtons explicitly ignore disabled buttons; the weapon
        # buttons do not. Use the VM's existing disable list, with a real State
        # identity, to prevent ammo/volleys before the placement is consumed.
        stack = instance.state(4)
        for button in (20, 29):
            holders = instance.component.disabled.setdefault(button, [])
            if stack not in holders:
                holders.append(stack)

    def command(self, player, command):
        instance = self.instance(player)
        if instance is None or command is None:
            return False
        state = self.inputs.setdefault(player.body, Input())
        pressed = command.buttons & ~state.buttons
        state.buttons = command.buttons
        owned = [p for p in self.pieces.values() if p.owner == player.body]
        if pressed & 16:
            if owned:
                for piece in owned:
                    self.remove(piece)
                state.preview = False
                self.stop_preview(instance)
                return True
            if state.preview:
                state.preview = False
                self.stop_preview(instance)
                return True
            if (instance.state(33).remaining() <= 0.0 and not self.combat.invulnerable(player)
                    and instance.state(33).flags & 1):
                state.preview, state.rotated = True, False
        if not state.preview:
            return False
        if self.combat.invulnerable(player):
            state.preview = False
            self.stop_preview(instance)
            return True
        if pressed & 2:
            state.rotated = not state.rotated
        if pressed & 1:
            placement = self.placement(player, command, state.rotated)
            if placement is not None:
                self.create(player, *placement)
                instance.write((0, 61), expr.f32(12.0))
                self.stop_preview(instance)
                state.preview = False
        if state.preview:
            self.disable_fire(instance)
        self.clear_fire(instance.component)
        return True

    def create(self, player, points, axis):
        for point in points:
            entity = self.combat.next_bot
            if entity >= 0xD0000000:
                raise ValueError("wall entity allocator exhausted")
            self.combat.next_bot += 1
            piece = Pillar(entity, player.body, player.team, point, axis, 400.0, self.combat.match.tick + 313)
            self.pieces[entity] = piece

    def anchor(self, body, piece, instance):
        if piece is None:
            self.anchors.pop(body, None)
        else:
            ability = next((state for state in instance.states.values()
                            if isinstance(state, nodes.Ability)
                            and state.flags & nodes.Ability.RUNNING), None)
            if ability is not None:
                self.anchors[body] = (piece.entity, ability)

    def remove(self, piece):
        if self.pieces.pop(piece.entity, None) is not None:
            for body, (entity, ability) in tuple(self.anchors.items()):
                if entity == piece.entity:
                    self.anchors.pop(body)
                    ability.stop()
                    ability.instance.component.run_now()
            mines = self.combat.venom.mines
            mines[:] = [mine for mine in mines if mine.wall != piece.entity]

    def retire(self, body):
        self.inputs.pop(body, None)
        self.anchors.pop(body, None)
        for piece in tuple(self.pieces.values()):
            if piece.owner == body:
                self.remove(piece)

    def cancel_preview(self, player):
        state = self.inputs.get(player.body)
        instance = self.instance(player)
        if state is not None and state.preview:
            state.preview = False
            if instance is not None:
                self.stop_preview(instance)

    def update(self, tick):
        for body, (_, ability) in tuple(self.anchors.items()):
            if not ability.flags & nodes.Ability.RUNNING:
                self.anchors.pop(body)
        for piece in tuple(self.pieces.values()):
            if tick >= piece.expires:
                self.remove(piece)

    def damage(self, piece, shooter, amount):
        source = shooter.entity if isinstance(shooter, bots.Bot) else shooter.body
        allied = not self.combat.match.game_map.free_for_all and shooter.team == piece.team
        if source == piece.owner or allied or amount <= 0.0 or piece.entity not in self.pieces:
            return
        piece.health = max(0.0, piece.health - amount)
        if piece.health == 0.0:
            self.remove(piece)


    def splash(self, shooter, point, blast, visible_from):
        from ow174.game.combat import ray_world

        hits = []
        for piece in self.pieces.values():
            destination = closest(piece, point)
            scalar = blast.splash(math.dist(point, destination))
            if scalar <= 0.0:
                continue
            delta = tuple(b - a for a, b in zip(visible_from, destination, strict=True))
            length = math.hypot(*delta)
            if length > EPS:
                direction = tuple(d / length for d in delta)
                hit = self.cast(visible_from, direction, length)
                static = ray_world(self.combat._collision(), visible_from, direction, length)
                if ((hit is not None and hit[1] is not piece and hit[0] < length - EPS)
                        or (static is not None and static < length - EPS)):
                    continue
            hits.append((piece, blast.amount * scalar))
        for piece, amount in hits:
            self.damage(piece, shooter, amount)

    def constrain(self, before, after, radius, height):
        velocity_normals = []
        for _ in range(3):
            delta = tuple(b - a for a, b in zip(before, after, strict=True))
            length = math.sqrt(sum(axis * axis for axis in delta))
            if length < EPS:
                break
            direction = tuple(axis / length for axis in delta)
            best = None
            for piece in self.pieces.values():
                hit = ray(piece, before, direction, length, radius=radius, height=height)
                if hit is not None and hit[1] != (0.0, 0.0, 0.0) and (best is None or hit[0] < best[0]):
                    best = hit
            if best is None:
                break
            at, normal = best
            point = tuple(a + at * d for a, d in zip(before, direction, strict=True))
            overlap = sum((a - p) * n for a, p, n in zip(after, point, normal, strict=True))
            after = tuple(a + n * (EPS - overlap) for a, n in zip(after, normal, strict=True))
            velocity_normals.append(normal)
        return after, velocity_normals

    @staticmethod
    def place_player(player, position, normals=()):
        mover = player.mover
        mover.position = list(position)
        for normal in normals:
            into = sum(a * n for a, n in zip(mover.velocity, normal, strict=True))
            if into < 0.0:
                mover.velocity = tuple(a - into * n for a, n in zip(mover.velocity, normal, strict=True))
            if normal[1] > 0.7:
                mover.flags_word &= ~IN_AIR
                mover.fall, mover.air_ticks, mover.landed = 0.0, 0, True
        if mover.body is not None:
            mover.body.teleport((position[0], position[1] + mover._lift(), position[2]))
        if mover.history:
            mover.history[-1] = mover.snapshot(mover.history[-1].frame)

    def move_player(self, player, before):
        if not self.pieces:
            return
        data = player.mover.data
        height = data.crouch_height if player.mover.crouched else data.stand_height
        radius = data.crouch_radius if player.mover.crouched else data.stand_radius
        after, normals = self.constrain(before, tuple(player.mover.position), radius, height)
        if normals:
            self.place_player(player, after, normals)
        return bool(normals)

    def move_bot(self, bot, before):
        data = mover_data(bot.body)
        after, normals = self.constrain(before, bot.position, data.stand_radius, data.stand_height)
        if normals:
            bot.position = after
            bot.pace = 0.0
            bot.velocity = (0.0, 0.0, 0.0)
