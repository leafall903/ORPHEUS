"""Reconstructed Reinhardt barrier host using the pinned external model ray mesh.

Actual0095 Pet51/secondary input controls deployment. HP587, recovery Wait17,
Chase16 rate and break cooldown472 are authored. Model-to-eye attachment,
regeneration scheduling and damage routing are explicit reconstructions.
Generic Pet actors, native raycast filter policies and client transport are open.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from ow174.game import reinhardt_barrier
from ow174.game.script import expr, graph, runtime
from ow174.paths import DATA_DIR

MESH_SHA256 = 'dca55f2163d88355e718f842edc5b0e5ed8b0c79463cde5c64a6d564562aea10'
MODEL_SHA256 = '56d88e87a4e404264bbbbb2e0b2a42248abedfd4dea5358f1d96c826b0aec9fa'
ZARYA = 0x02E0000000000068
PERSONAL, PROJECTED = 0x0244, 0x025C
ENERGY_MESSAGE = 0x0240000000000146
# Manifest-verified 10D7.00C MSTU sphere. Attachment to body midpoint is reconstructed.
BUBBLE_RADIUS = 1.5


def load_mesh():
    override = os.environ.get('ORPHEUS_BARRIER_DATA')
    path = Path(override) if override else DATA_DIR/'barriers_174.json'
    if not path.is_file():
        if override:
            raise FileNotFoundError(path)
        return (), None
    data = json.loads(path.read_text(encoding='utf-8'))
    geometry = {key: data[key] for key in ('vertices', 'triangles')}
    encoded = json.dumps(geometry, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if (data.get('schema') != 'ow174.reinhardt-barrier-mesh.v1' or data.get('build') != 104319
            or data.get('source_sha256') != MODEL_SHA256
            or hashlib.sha256(encoded).hexdigest() != MESH_SHA256):
        raise ValueError('barrier geometry does not match the verified104319 ray mesh')
    vertices = geometry['vertices']
    triangles = tuple(tuple(vertices[index] for index in row) for row in geometry['triangles'])
    bounds = tuple(tuple(op(p[k] for p in vertices) for k in range(3)) for op in (min, max))
    return triangles, bounds


@dataclass
class Barrier:
    player: object
    body: int
    instance: int
    hp: float
    most: float
    last: float
    lowered_at: float
    damaged_at: float
    blocked_until: float = 0.0
    raised: bool = False


@dataclass
class Bubble:
    caster: object
    target: int
    graph: int
    instance: int
    activation: int
    expires: float
    hp: float


WINSTON = 0x02E0000000000009
DOME_RADIUS = 5.0  # Authored 01C3/D02 sphere, static centre reconstruction.


@dataclass
class Dome:
    player: object
    body: int
    entity: int
    source: runtime.Component
    component: runtime.Component
    centre: tuple
    start_frame: int
    world_ms: int
    hp: float = 0.0
    retired: bool = False


class Barriers:
    def __init__(self, combat):
        self.combat = combat
        self.triangles, self.bounds = load_mesh()
        self.owners = {}
        self.bubbles = {}
        self.domes = {}

    def retire(self, body):
        self.owners.pop(body, None)
        self.domes.pop(body, None)
        for key, bubble in list(self.bubbles.items()):
            if key[0] == body or bubble.target == body:
                del self.bubbles[key]

    def bubble(self, target):
        return next((bubble for bubble in self.bubbles.values()
                     if bubble.target == target.entity and bubble.hp > 0
                     and self.combat.now < bubble.expires
                     and bubble.caster.body_script is not None
                     and not self.combat.is_dead(bubble.caster)
                     and self.combat.zenyatta.allied(bubble.caster, target)), None)

    def select_projection(self, player, command):
        """Supply the authored #375 TrackTargets output for an actual cast edge."""
        from ow174.game.combat import _dot, _sub, aim
        from ow174.game.commands import FRAME_HALF, frame_distance

        script = player.body_script
        if (not command.buttons & 16 or script.buttons & 16
                or not 0 < frame_distance(command.frame, script.frame) < FRAME_HALF
                or command.frame < script.frame):
            return
        instance = next((i for i in script.component.instances.values() if i.graph.index == PROJECTED), None)
        if instance is None:
            return
        node = instance.graph.node(18)
        reach = expr.to_float(instance.evaluate(node.field('m_radius')))
        origin, direction = self.combat._eye(player), aim(*command.aim())
        best = None
        for target in self.combat._targets():
            if (target.owner is player or not self.combat.zenyatta.allied(player, target)
                    or self.combat.vitals_of(target).dead):
                continue
            x, y, z = target.feet
            delta = _sub((x, y + target.eye, z), origin)
            distance = math.sqrt(_dot(delta, delta))
            if not 0 < distance <= reach:
                continue
            alignment = _dot(delta, direction) / distance
            if alignment < math.cos(math.radians(10)) or not self.combat.zenyatta.visible(player, target):
                continue
            key = (-alignment, distance, target.entity)
            if best is None or key < best:
                best = key
        instance.write((0, 375), (expr.Entity(best[2]),) if best is not None else ())

    def sync_bubbles(self, player):
        from ow174.game.script import graph

        targets = {target.entity: target for target in self.combat._targets()}
        if player.body not in targets or self.combat.is_dead(player):
            self.retire(player.body)
            return
        for instance in player.body_script.component.instances.values():
            index = instance.graph.index
            if index not in (PERSONAL, PROJECTED):
                continue
            key = (player.body, index)
            personal = index == PERSONAL
            pet = instance.states.get(instance.graph.node(8 if personal else 26).state)
            slot = instance.vars.get(536)
            target_id = player.body if personal else expr.to_entity(slot.value() if slot else None)
            target = targets.get(target_id)
            if (pet is None or not pet.active or target is None or self.combat.vitals_of(target).dead
                    or not self.combat.zenyatta.allied(player, target)):
                self.bubbles.pop(key, None)
                continue
            found = self.bubbles.get(key)
            if found is not None and (found.instance, found.activation) == (instance.id, pet.start):
                continue
            pool = graph.graph(0x256)
            if pool is None:
                continue
            hp = expr.to_float(instance.evaluate(pool.node(0).field('m_amount')))
            chase = instance.graph.node(5 if personal else 28)
            duration = expr.to_float(instance.evaluate(chase.field('m_EB0FC261')))
            script = player.body_script
            elapsed = max(0, script.frame * script.frame_ms - pet.start) / 1000
            self.bubbles[key] = Bubble(player, target_id, index, instance.id, pet.start,
                                       self.combat.now + duration - elapsed, hp)
            self.combat.zenyatta.cleanse_discord(target_id)
            self.combat._clear_frost(target_id, target.owner)

    def owner(self, target):
        if not self.triangles or getattr(target.owner, "mover", None) is None:
            return None
        instance = reinhardt_barrier.instance(target.owner)
        if instance is None or self.combat.vitals_of(target).dead:
            return None
        now = self.combat.now
        found = self.owners.get(target.entity)
        if found is None or found.instance != instance.id:
            most = max(0.0, expr.to_float(instance.find_var(0, 587).value()))
            found = Barrier(target.owner, target.entity, instance.id, most, most, now, now, now)
            self.owners[target.entity] = found
        raised = reinhardt_barrier.active(target.owner) is instance
        if found.raised and not raised:
            found.lowered_at = now
        # The mesh host only projects the health and Pet lifetime owned by 0095.
        found.hp = expr.to_float(instance.vars[586].value())
        found.last, found.raised = now, raised
        return found

    def update(self):
        self.advance_domes()
        current = set()
        bodies = set()
        for target in self.combat._targets():
            bodies.add(target.entity)
            owner = self.owner(target)
            if owner is not None:
                current.add(owner.body)
        for body in set(self.owners)-current:
            del self.owners[body]
        for key, bubble in list(self.bubbles.items()):
            if key[0] not in bodies or bubble.target not in bodies:
                del self.bubbles[key]

    def damage(self, barrier, amount, source):
        taken = min(barrier.hp, max(0.0, amount))
        barrier.hp -= taken
        if isinstance(barrier, Dome):
            barrier.component.set(253, barrier.hp)
            if barrier.hp <= 0:
                barrier.retired = True
            return taken
        if isinstance(barrier, Bubble):
            if taken > 0:
                if source and source != barrier.target:
                    # Bridge the hosted Pet's absorbed damage to the authored owner graph.
                    # The protected body stands in for the absent Pet as message sender.
                    component = barrier.caster.body_script.component
                    params = {0x2A: taken, 0x12: expr.Entity(source)}
                    if runtime.send_message(component, ENERGY_MESSAGE, params, barrier.target):
                        component.run_now()
                if barrier.hp == 0:
                    self.break_bubble(barrier)
            return taken
        barrier.damaged_at = self.combat.now
        component = barrier.player.body_script.component
        instance = component.instances[barrier.instance]
        reinhardt_barrier.damage(instance, taken)
        barrier.hp = expr.to_float(instance.vars[586].value())
        if barrier.hp <= 0:
            delay = max(0.0, expr.to_float(instance.find_var(0, 472).value()))
            barrier.blocked_until = self.combat.now + delay
            barrier.lowered_at = self.combat.now
            barrier.raised = False
        return taken

    def break_bubble(self, bubble):
        """End the timed Pet scope so its authored cooldown outputs execute."""
        script = bubble.caster.body_script
        if script is None:
            return
        instance = script.component.instances.get(bubble.instance)
        if instance is None or instance.graph.index != bubble.graph:
            return
        personal = bubble.graph == PERSONAL
        pet = instance.states.get(instance.graph.node(8 if personal else 26).state)
        chase = instance.states.get(instance.graph.node(5 if personal else 28).state)
        if pet is not None and pet.active and pet.start == bubble.activation and chase and chase.active:
            chase.request(runtime.ABORT)
            script.component.run_now()

    def _distance(self, barrier, target, origin, direction, reach):
        from ow174.game.combat import _dot, _sub, aim, ray_box

        mover = barrier.player.mover
        forward = aim(mover.yaw, mover.pitch)
        yaw = mover.yaw*2*math.pi/65536
        right = (-math.cos(yaw), 0.0, math.sin(yaw))
        up = cross(right, forward)
        eye = (target.feet[0], target.feet[1]+target.eye, target.feet[2])
        delta = tuple(origin[k]-eye[k]-forward[k]*(target.radius+.2) for k in range(3))
        # Reconstructed attachment: front capsule surface plus authored0.2;
        # animated hardpoint29 is not evaluated. Model -Z points outward.
        # Mesh vertices/topology are exact external resource data.
        basis = (right, up, tuple(-v for v in forward))
        local = tuple(_dot(delta, axis) for axis in basis)
        ray = tuple(_dot(direction, axis) for axis in basis)
        if ray_box(local, ray, *self.bounds, reach) is None:
            return None
        best = None
        for a, b, c in self.triangles:
            ab, ac = _sub(b, a), _sub(c, a)
            p = cross(ray, ac)
            determinant = _dot(ab, p)
            if abs(determinant) < 1e-10:
                continue
            offset = _sub(local, a)
            u = _dot(offset, p)/determinant
            q = cross(offset, ab)
            v = _dot(ray, q)/determinant
            distance = _dot(ac, q)/determinant
            if 0 <= u <= 1 and v >= 0 and u+v <= 1 and 0 <= distance <= reach:
                best = distance if best is None else min(best, distance)
        return best

    def ray(self, shooter, origin, direction, reach, extra=None, *, include_bubbles=True):
        from ow174.game.combat import ray_capsule

        best = None
        for target in self.combat._targets():
            if target.owner is shooter:
                continue
            if not self.combat.match.game_map.free_for_all and target.team == shooter.team:
                continue
            bubble = self.bubble(target) if include_bubbles else None
            if isinstance(extra, Bubble) and extra.target == target.entity:
                bubble = extra
            if bubble is not None:
                x, y, z = target.feet
                centre = (x, y + target.height / 2, z)
                distance = ray_capsule(origin, direction, centre, centre, BUBBLE_RADIUS)
                if distance is not None and distance <= reach and (best is None or distance < best[0]):
                    best = (distance, bubble)
            barrier = self.owner(target)
            if barrier is None or (not barrier.raised and barrier is not extra):
                continue
            distance = self._distance(barrier, target, origin, direction, reach)
            if distance is not None and (best is None or distance < best[0]):
                best = (distance, barrier)
        for dome in self.domes.values():
            owner = dome.player
            if (owner is shooter or not self.dome_live(dome) or dome.hp <= 0
                    or (not self.combat.match.game_map.free_for_all and owner.team == shooter.team)):
                continue
            offset = tuple(a-b for a,b in zip(origin, dome.centre, strict=True))
            projection = sum(a*b for a,b in zip(offset, direction, strict=True))
            radial = sum(axis*axis for axis in offset)-DOME_RADIUS**2
            discriminant = projection*projection-radial
            if discriminant < 0:
                continue
            root = math.sqrt(discriminant)
            distance = -projection+root if radial < 0 else -projection-root
            if 0 <= distance <= reach and (best is None or distance < best[0]):
                best = distance, dome
        return best

    def dome_live(self, dome):
        owner = dome.player
        return (not dome.retired and owner.has_body and not owner.gone and owner.body == dome.body
                and owner.body_script is not None and owner.body_script.component is dome.source
                and not self.combat.is_dead(owner))

    def advance_domes(self):
        for body, dome in tuple(self.domes.items()):
            if self.dome_live(dome):
                elapsed = max(0, round(self.combat.now*1000)-dome.world_ms)
                frame = dome.start_frame+elapsed//dome.component.frame_ms
                if frame > dome.component.frame:
                    dome.component.run_frame(frame)
            if not self.dome_live(dome):
                del self.domes[body]


def create_dome(world, instance, node):
    if (world.hero, instance.graph.index, node.pos) != (WINSTON, 0xFA, 26):
        return NotImplemented
    if world.match is None or graph.graph(0xFC) is None:
        return None
    if expr.guid_of(node.field("m_entityDefinition.m_entityDef")) != 0x04000000000001C3:
        return NotImplemented
    from ow174.game.script.driver import BodyWorld

    fight = world.match.combat
    owner = next((p for p in world.match.players if p.body == world.body and p.has_body
                  and not p.gone and p.body_script is not None
                  and p.body_script.component is instance.component
                  and not fight.is_dead(p)), None)
    if owner is None:
        return None
    entity = fight.next_bot
    fight.next_bot += 1
    child_world = BodyWorld(world.hero, world.mode, entity)
    child_world.match = world.match
    source = instance.component
    child = runtime.Component(entity, source.frame_ms, child_world)
    child.frame, child.now = source.frame, source.now
    x, y, z = owner.mover.position
    fight.barriers.domes[owner.body] = Dome(
        owner, owner.body, entity, source, child, (x, y+.1, z), source.frame, round(fight.now*1000)
    )
    return expr.Entity(entity)


def play_dome(world, instance, node):
    from ow174.game.script import play, play_queue

    if world.match is None:
        return
    dome = world.match.combat.barriers.domes.get(world.body)
    definition = graph.graph(0xFC)
    resource = play.resolve_resource(instance, node)
    if (dome is None or dome.source is not instance.component or definition is None or resource is None
            or resource.guid != 0x05800000000000FC):
        return
    request = play.resolve_request(
        instance, node, resource,
        context_provider=lambda: play.InvocationContext(world.body, world.body, instance.source_entity()),
    )
    if request is None or request.targets != (dome.entity,):
        return
    child = dome.component
    queue = play_queue.Queue(child, lambda guid: play_queue.Load("ready", definition))
    queue.submit(request)
    child.run_now()
    pool = next((i.states.get(0) for i in child.instances.values() if i.graph.index == 0xFC), None)
    if pool is not None and pool.active:
        dome.hp = expr.to_float(pool.evaluate(pool.node.field("m_amount"), watch=False))
        child.set(253, dome.hp)


def destroy_dome(world, instance, node):
    if (world.hero != WINSTON or world.match is None or instance.graph.index != 0xFC
            or node.pos not in (12, 28)):
        return NotImplemented
    for dome in world.match.combat.barriers.domes.values():
        if dome.entity == world.body and dome.component is instance.component:
            dome.retired = True
            return True
    return NotImplemented


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
