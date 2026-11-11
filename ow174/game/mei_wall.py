"""Ice Wall placement and static combat geometry for the authored 0622 ability."""

import math
from dataclasses import dataclass
from itertools import pairwise

from ow174.game import collision, contacts
from ow174.game.collision import Ground
from ow174.game.commands import Command
from ow174.game.mover import IN_AIR, Mover, mover_data
from ow174.game.script import expr, graph, runtime

MEI = 0x02E00000000000DD
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
    impact_marked: bool = False


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


def sphere_contact(pillar, origin, direction, length, radius):
    """First swept-sphere contact with the rounded corners of an oriented box."""
    p = local(pillar, tuple(a - b for a, b in zip(origin, pillar.feet, strict=True)))
    d = local(pillar, direction)
    low = (-pillar.width / 2, 0.0, -pillar.depth)
    high = (pillar.width / 2, pillar.height, pillar.depth)
    cuts = {0.0, length}
    for axis, speed in enumerate(d):
        if abs(speed) > EPS:
            for edge in (low[axis], high[axis]):
                at = (edge - p[axis]) / speed
                if 0 < at < length:
                    cuts.add(at)
    for start, end in pairwise(sorted(cuts)):
        quadratic = linear = 0.0
        constant = -radius * radius
        midpoint = (start + end) / 2
        for value, speed, lo, hi in zip(p, d, low, high, strict=True):
            probe = value + midpoint * speed
            if lo <= probe <= hi:
                continue
            gap = value + start * speed - (lo if probe < lo else hi)
            quadratic += speed * speed
            linear += gap * speed
            constant += gap * gap
        if constant <= 0:
            return start
        discriminant = linear * linear - quadratic * constant
        if quadratic > 0 and linear < 0 and discriminant >= 0:
            offset = constant / (-linear + math.sqrt(discriminant))
            if offset <= end - start:
                return start + offset
    return None


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

    def create(self, player, points, axis):
        from ow174.game.combat import TICK

        definition = graph.graph(0x0636)
        if definition is None:
            return False
        from ow174.game.bots import Bot

        players = []
        for target in self.combat._targets():
            owner = target.owner
            if isinstance(owner, Bot) and owner.spawner.get("manual_spawn") and not owner.dead:
                mover = getattr(owner, "wall_mover", None) or Mover(
                    owner.position, round(owner.yaw), self.combat.match.collision_world, mover_data(owner.body),
                )
            else:
                mover = getattr(owner, "mover", None)
            if mover is not None and not self.combat.is_dead(owner):
                players.append((owner, mover))
        created = False
        for point in points:
            piece = Pillar(0, player.body, player.team, point, axis, 0., 0)
            lifts = self.deployment_lifts(piece, players)
            if lifts is None:
                continue
            entity = self.combat.next_bot
            if entity >= 0xD0000000:
                raise ValueError("wall entity allocator exhausted")
            self.combat.next_bot += 1
            component = runtime.Component(entity)
            instance = component.create(definition, init=False)
            # Consume the original pillar marker entry. Health and retirement
            # are hosted here while the rest of the pillar VM remains open.
            instance.run_node(definition.node(27), None)
            marked = expr.truthy(component.entity_var(961).value())
            health = expr.to_float(instance.evaluate(definition.node(1).field("m_amount")))
            expires = self.combat.tick + math.ceil(5 / TICK)
            piece.entity, piece.health, piece.expires, piece.impact_marked = entity, health, expires, marked
            self.pieces[entity] = piece
            created = True
            for owner, mover, position in lifts:
                self.place_mover(mover, position, ((0., 1., 0.),))
                if isinstance(owner, Bot):
                    owner.wall_mover = mover
                    owner.position, owner.velocity = tuple(mover.position), mover.velocity
                    owner.moved_tick = self.combat.tick
        return created


    def deployment_lifts(self, piece, players):
        """Plan vertical clearance before committing a column and moving any player."""
        scene = self.combat.match.collision_world
        top = piece.feet[1] + piece.height
        up = (0., 1., 0.)
        lifts = []
        for owner, mover in players:
            x, y, z = mover.position
            dx, dy, dz = local(piece, tuple(a - b for a, b in zip(mover.position, piece.feet, strict=True)))
            radius, segment = mover._capsule()
            lift = mover._lift()
            height = lift + segment + 2 * radius
            if (abs(dx) >= piece.width / 2 + radius or abs(dz) >= piece.depth + radius
                    or dy >= piece.height or dy + height <= 0):
                continue
            destination = (x, top + EPS, z)
            start, end = (x, y + lift, z), (x, destination[1] + lift, z)
            centres = ((0., radius, 0.), (0., radius + segment, 0.))
            if contacts.scene_sweep(scene.engine, start, end, centres, radius, mover.category) is not None:
                return None
            for other in self.pieces.values():
                hit = ray(other, mover.position, up, destination[1] - y, radius=radius, height=height)
                if hit is not None and any(hit[1]):
                    return None
            lifts.append((owner, mover, destination))
        return lifts


    @staticmethod
    def place_mover(mover, position, normals):
        mover.position = list(position)
        for normal in normals:
            into = sum(a * b for a, b in zip(mover.velocity, normal, strict=True))
            if into < 0:
                mover.velocity = tuple(a - into * b for a, b in zip(mover.velocity, normal, strict=True))
            if normal[1] > .7:
                mover.flags_word &= ~IN_AIR
                mover.fall, mover.air_ticks, mover.landed = 0., 0, True
        if mover.body is not None:
            mover.body.teleport((position[0], position[1] + mover._lift(), position[2]))


    def ground(self, mover):
        radius, _ = mover._capsule()
        x, y, z = mover.position
        top = y + mover._pogo() + mover.spring + radius
        bottom = y - (0. if mover.airborne else mover.values.step_down)
        best = None
        for piece in self.pieces.values():
            dx, _, dz = local(piece, (x - piece.feet[0], y - piece.feet[1], z - piece.feet[2]))
            height = piece.feet[1] + piece.height
            if (abs(dx) <= piece.width / 2 and abs(dz) <= piece.depth and bottom <= height <= top
                    and (best is None or height > best.y)):
                best = Ground.at((x, height, z), (0., 1., 0.))
        return best


    def move_mover(self, mover, before):
        after = tuple(mover.position)
        radius, segment = mover._capsule()
        height = 2 * radius + segment + mover._pogo()
        after, normals = self.constrain(before, after, radius, height)
        if normals:
            self.place_mover(mover, after, normals)
            mover.history[-1] = mover.snapshot(mover.history[-1].frame)
        return bool(normals)


    def constrain(self, before, after, radius, height):
        normals = []
        for _ in range(3):
            delta = tuple(b - a for a, b in zip(before, after, strict=True))
            length = math.hypot(*delta)
            if length < EPS:
                break
            direction = tuple(v / length for v in delta)
            best = None
            for piece in self.pieces.values():
                hit = ray(piece, before, direction, length, radius=radius, height=height)
                if hit is not None and any(hit[1]) and (best is None or hit[0] < best[0]):
                    best = hit
            if best is None:
                break
            at, normal = best
            overlap = sum((length - at) * d * n for d, n in zip(direction, normal, strict=True))
            after = tuple(a + n * (EPS - overlap) for a, n in zip(after, normal, strict=True))
            normals.append(normal)
        return after, normals

    def step_npc(self, bot, tick, seconds):
        mover = getattr(bot, "wall_mover", None)
        if mover is None or bot.dead:
            return False
        if bot.last_tick == tick:
            return True
        bot.last_tick = tick
        before = tuple(mover.position)
        mover.step_pose(Command(tick, yaw=round(bot.yaw)), seconds, tick, ground_override=self.ground(mover))
        self.move_mover(mover, before)
        position = tuple(mover.position)
        if bot.position != position:
            bot.position, bot.moved_tick = position, tick
        bot.velocity = mover.velocity
        if not mover.airborne and self.ground(mover) is None:
            del bot.wall_mover
        return True

    def remove(self, piece):
        if self.pieces.pop(piece.entity, None) is not None:
            sentries = getattr(self.combat, "symmetra_sentries", None)
            if sentries is not None:
                sentries.retire_supported_by(piece.entity)

    def retire(self, body):
        self.inputs.pop(body, None)
        for piece in tuple(self.pieces.values()):
            if piece.owner == body:
                self.remove(piece)

    def update(self, tick):
        for entity, piece in tuple(self.pieces.items()):
            if piece.expires <= tick:
                self.remove(piece)

    def cancel_preview(self, player):
        state = self.inputs.get(getattr(player, "body", None))
        if state is not None and state.preview:
            state.preview = False
            instance = self.instance(player)
            if instance is not None:
                self.stop_preview(instance)

    def damage(self, piece, shooter, amount, *, team=None):
        from ow174.game.combat import _source_entity

        team = shooter.team if team is None else team
        allied = not self.combat.match.game_map.free_for_all and team == piece.team
        if _source_entity(shooter) == piece.owner or allied or amount <= 0:
            return
        piece.health = max(0.0, piece.health - amount)
        if piece.health == 0:
            self.remove(piece)

    def instance(self, player):
        script = getattr(player, "body_script", None)
        if script is None or player.hero.guid != MEI:
            return None
        return next((i for i in script.component.instances.values() if i.graph.index == 0x0622), None)

    def cast(self, origin, direction, length, *, radius=0.0):
        best = None
        for piece in self.pieces.values():
            if radius:
                distance = sphere_contact(piece, origin, direction, length, radius)
                hit = None if distance is None else (distance, None)
            else:
                hit = ray(piece, origin, direction, length)
            if hit is not None and (best is None or hit[0] < best[0]):
                best = hit[0], piece, hit[1]
        return best

    def flail_contacts(self, origin, direction, reach, minimum_alignment):
        from ow174.game.combat import _dot, _sub

        for piece in tuple(self.pieces.values()):
            point = closest(piece, origin)
            toward = _sub(point, origin)
            length = math.sqrt(_dot(toward, toward))
            if length > reach or _dot(direction, toward) < length * minimum_alignment:
                continue
            if length > EPS:
                ray = tuple(axis / length for axis in toward)
                obstruction = self.combat.ray_obstruction(origin, ray, length)
                if obstruction is not None and obstruction < length - EPS:
                    continue
            yield piece

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
            if (instance.state(33).remaining() <= 0.0 and not self.combat._invulnerable(player)
                    and instance.state(33).flags & 1):
                state.preview, state.rotated = True, False
        if not state.preview:
            return False
        if self.combat._invulnerable(player):
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
