"""Mei Ice Wall server reconstruction, distinct from native placement/physics hosting.

0622 supplies five columns, width1.55, depth0.5, height4.4, duration5 and
cooldown12. 0636 supplies400 health; actual pillar definition is0932. The
oriented boxes and post-mover swept contacts here are reconstructed consumers.
They do not mutate shared map collision assets or claim native client parity.
"""

import math
from dataclasses import dataclass

from ow174.game import collision, contacts, mei, world
from ow174.game.collision import Ground, RayHit
from ow174.game.mover import IN_AIR
from ow174.game.script import expr, graph

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

    def instance(self, player):
        script = getattr(player, "body_script", None)
        if script is None or getattr(script, "hero", None) != mei.HERO:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == 0x0622), None)

    def cast(self, origin, direction, length, *, radius=0.):
        best = None
        for piece in self.pieces.values():
            hit = ray(piece, origin, direction, length, radius=radius)
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit[0], piece, hit[1]
        return best

    def cast_ray(self, origin, direction, length, category=collision.MOVER):
        static = self.combat.match.collision_world.cast_ray(origin, direction, length, category)
        hit = self.cast(origin, direction, length if static is None else static.distance)
        if hit is None or (static is not None and static.distance <= hit[0]):
            return static
        at, _, normal = hit
        point = tuple(p + d * at for p, d in zip(origin, direction, strict=True))
        return RayHit(at, point, normal, category, 0)

    def replay_until(self, previous):
        return max((piece.expires for piece in self.pieces.values()), default=previous)

    def fits(self, point, radius, height):
        for piece in self.pieces.values():
            x, y, z = local(piece, tuple(a - b for a, b in zip(point, piece.feet, strict=True)))
            dx = max(0, abs(x) - piece.width / 2)
            dz = max(0, abs(z) - piece.depth)
            if y < piece.height - EPS and y + height > EPS and dx * dx + dz * dz < radius * radius:
                return False
        return True

    def reaches(self, piece, origin, destination):
        delta = tuple(b - a for a, b in zip(origin, destination, strict=True))
        length = math.hypot(*delta)
        if length < EPS:
            return True
        direction = tuple(axis / length for axis in delta)
        static = self.combat.match.collision_world.cast_ray(origin, direction, length)
        if static is not None and static.distance < length - EPS:
            return False
        hit = self.cast(origin, direction, length)
        return hit is None or hit[1] is piece or hit[0] >= length - EPS

    def placement(self, player, command, rotated):
        from ow174.game.combat import aim

        origin, direction = self.combat._eye(player), aim(*command.aim())
        instance = self.instance(player)
        reach = expr.to_float(instance.evaluate(instance.graph.node(3).field("m_6386E7B4")))
        hit = self.combat.match.collision_world.cast_ray(origin, direction, reach)
        if hit is None or hit.normal[1] < 0.7:
            return None
        where = hit.point
        yaw = command.yaw * 2 * math.pi / 65536
        axis = (math.sin(yaw), 0.0, math.cos(yaw)) if rotated else (math.cos(yaw), 0.0, -math.sin(yaw))
        points = []
        targets = self.combat._targets()
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
            if ceiling is None and self.placement_clear(candidate) and self.lift_clear(candidate, targets):
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

    def lift_clear(self, piece, targets):
        """Reject a column whose rise would sweep an occupant through static geometry."""
        engine = self.combat.match.collision_world.engine
        for target, end in self.lift_positions(piece, targets):
            mover = getattr(target.owner, "mover", None)
            if mover is not None:
                radius, segment = mover._capsule()
                lower = mover._lift() + radius
                upper = lower + segment
            else:
                radius = target.radius
                lower, upper = radius, max(radius, target.height - radius)
            points = ((0., lower, 0.), (0., upper, 0.))
            if contacts.scene_sweep(engine, target.feet, end, points, radius, collision.MOVER) is not None:
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
            if (instance.state(33).remaining() <= 0.0 and not mei.protected(player)
                    and instance.state(33).flags & 1):
                state.preview, state.rotated = True, False
        if not state.preview:
            return False
        if mei.protected(player):
            state.preview = False
            self.stop_preview(instance)
            return True
        if pressed & 2:
            state.rotated = not state.rotated
        if pressed & 1:
            placement = self.placement(player, command, state.rotated)
            if placement is not None and self.create(player, *placement):
                instance.write((0, 61), expr.f32(12.0))
                self.stop_preview(instance)
                state.preview = False
        if state.preview:
            self.disable_fire(instance)
        self.clear_fire(instance.component)
        return True

    def create(self, player, points, axis):
        definition = graph.graph(0x0636)
        if definition is None:
            return False
        amount = definition.node(1).field("m_amount")
        health = expr.to_float(expr.evaluate(amount, expr.Context()))
        if health <= 0:
            return False
        expiry = self.combat.match.tick + 313
        for point in points:
            entity = self.combat.next_bot
            if entity >= 0xD0000000:
                raise ValueError("wall entity allocator exhausted")
            self.combat.next_bot += 1
            piece = Pillar(entity, player.body, player.team, point, axis, health, expiry)
            self.pieces[entity] = piece
            self.lift(piece)
        self.show()
        return True

    def show(self):
        if not self.pieces:
            return
        for viewer in self.combat.match.players:
            if not viewer.spawned or viewer.client is None:
                continue
            for piece in self.pieces.values():
                if piece.entity in viewer.seen:
                    continue
                angle = math.atan2(-piece.axis[2], piece.axis[0])
                rotation = (0.0, math.sin(angle / 2), 0.0, math.cos(angle / 2))
                viewer.client.queue_entities([world.EntityUpdate(
                    piece.entity, world.OP_CREATE,
                    lambda origin, h=piece.health, t=piece.team, p=piece.feet, r=rotation: world.create(
                        origin, DEFINITION, {**world.health(h, 400.0), 26: [0x800000 << t]},
                        flags=world.WITH_TRANSFORM | world.WITH_SCALE, position=p, rotation=r,
                    ),
                )])
                viewer.seen[piece.entity] = True

    def remove(self, piece):
        if self.pieces.pop(piece.entity, None) is None:
            return
        for viewer in self.combat.match.players:
            if piece.entity in viewer.seen:
                del viewer.seen[piece.entity]
                if viewer.client is not None:
                    viewer.client.queue_entities([world.EntityUpdate(piece.entity, world.OP_DESTROY)])

    def retire(self, body):
        self.inputs.pop(body, None)
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
        for piece in tuple(self.pieces.values()):
            if tick >= piece.expires:
                self.remove(piece)
        self.show()

    def damage(self, piece, shooter, amount, frame):
        from ow174.game import bots

        source = shooter.entity if isinstance(shooter, bots.Bot) else shooter.body
        allied = not self.combat.match.game_map.free_for_all and shooter.team == piece.team
        if source == piece.owner or allied or amount <= 0.0 or piece.entity not in self.pieces:
            return 0.0
        dealt = min(piece.health, amount)
        piece.health -= dealt
        if not isinstance(shooter, bots.Bot) and shooter.client is not None:
            shooter.client.queue_combat_notice(piece.entity, 25001, {
                "+0x78": {"+0x0": {"+0x0": piece.entity}, "+0x4": max(1, round(dealt)), "+0x8": 0},
                "+0x84": frame & 0xFFFFFFFF,
            })
        if piece.health <= 0.0:
            self.remove(piece)
        else:
            for viewer in self.combat.match.players:
                if viewer.client is not None and piece.entity in viewer.seen:
                    viewer.client.queue_body_health(piece.entity, world.health(piece.health, 400.0))
        return dealt

    def splash(self, shooter, point, origin, blast, scale, frame, *, impact=None):
        # Resolve visibility before applying any damage: breaking the front
        # column must not expose another column to this same explosion.
        hits = []
        for piece in self.pieces.values():
            if piece is impact:
                continue
            destination = closest(piece, point)
            length = math.dist(point, destination)
            scalar = blast.splash(length)
            if scalar > 0 and self.reaches(piece, origin, destination):
                hits.append((piece, blast.amount * scalar * scale))
        for piece, amount in hits:
            self.damage(piece, shooter, amount, frame)

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
        after, normals = self.constrain(before, tuple(player.mover.position), data.stand_radius, height)
        if normals:
            self.place_player(player, after, normals)

    def ground(self, player):
        mover = player.mover
        radius, _segment = mover._capsule()
        top = mover.position[1] + mover._pogo() + mover.spring + radius
        bottom = mover.position[1] - (0.0 if mover.airborne else mover.values.step_down)
        best = None
        for piece in self.pieces.values():
            p = local(piece, tuple(a - b for a, b in zip(mover.position, piece.feet, strict=True)))
            height = piece.feet[1] + piece.height
            if (abs(p[0]) <= piece.width / 2 and abs(p[2]) <= piece.depth and bottom <= height <= top
                    and (best is None or height > best.y)):
                best = Ground.at((mover.position[0], height, mover.position[2]), (0.0, 1.0, 0.0))
        return best

    def move_bot(self, bot, before):
        if self.pieces:
            from ow174.game.mover import mover_data

            data = mover_data(bot.body)
            after, normals = self.constrain(before, bot.position, data.stand_radius, data.stand_height)
            if normals:
                bot.position, bot.velocity, bot.pace = after, (0.0, 0.0, 0.0), 0.0

    @staticmethod
    def lift_positions(piece, targets):
        for target in targets:
            if target.is_device:
                continue
            point = local(piece, tuple(a - b for a, b in zip(target.feet, piece.feet, strict=True)))
            if (abs(point[0]) < piece.width / 2 + target.radius
                    and abs(point[2]) < piece.depth + target.radius and -EPS <= point[1] < piece.height):
                yield target, (target.feet[0], piece.feet[1] + piece.height + EPS, target.feet[2])

    def lift(self, piece):
        for target, position in self.lift_positions(piece, self.combat._targets()):
            if hasattr(target.owner, "mover"):
                self.place_player(target.owner, position, ((0.0, 1.0, 0.0),))
            else:
                target.owner.position = position
