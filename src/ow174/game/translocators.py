"""Sombra's server-owned projectile entities; physics/placement are reconstructed.

The external additive record retains original0C1A and its HealthPool amount.
This is not an owner lease, an autonomous NPC, or native projectile parity.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ow174.game import actors, contacts, world
from ow174.game.script import expr, graph, runtime
from ow174.game.script.bot import BotScript
from ow174.game.script.driver import BodyWorld
from ow174.paths import DATA_DIR

DATA_PATH = Path(os.environ.get("ORPHEUS_TRANSLOCATOR_DATA", DATA_DIR / "translocator_174.json"))
DEFINITION = 0x0400000000001575
OWNER_GRAPH = 0x0C19
DEVICE_GRAPH = 0x0C1A
RECYCLE_END = 14  # device node 34
THROW_ADMISSION = 18  # node 35
RECALL_MOVE = (8, 61)  # nodes 18 and 131
ABILITY = 2  # node 5
RECYCLE_MESSAGE = 0x024000000000003F  # 0C1A recycle entry
RADIUS = 0.2  # entity1575's authored50BA775C radius
BOUNCE, TANGENT = 0.5, 0.6  # explicit host reconstruction, not decoded native coefficients


@lru_cache(maxsize=4)
def data(path):
    raw = Path(path).read_bytes()
    source = json.loads(raw)
    return source["entity1575"], source["extra_graphs"], hashlib.sha256(raw).hexdigest()


def accepted_volley(state):
    return (state.instance.graph.index == OWNER_GRAPH and state.node.pos == 7
            and not state.node.field("m_45271E8C"))


def _owner_graph(script):
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == OWNER_GRAPH), None)


def _action_pending(instance):
    return any((state := instance.states.get(index)) is not None and state.active and not state.ending
               for index in (THROW_ADMISSION, *RECALL_MOVE))


def replay_pending(script):
    instance = _owner_graph(script)
    if instance is None:
        return False
    ability = instance.states.get(ABILITY)
    return _action_pending(instance) or (ability is not None and ability.remaining() > 0)


def _recycle_pending(actor):
    if actor.body_script is None:
        return False
    for instance in actor.body_script.component.instances.values():
        if instance.graph.index != DEVICE_GRAPH:
            continue
        end = instance.states.get(RECYCLE_END)
        if end is not None and end.active:
            return True
        if any(event.kind == runtime.MESSAGE and event.message[0] == RECYCLE_MESSAGE
               for event in instance.queue):
            return True
    return False


def replay_frames(fight):
    if fight.translocators.recall_poses:
        return 480
    if any(_recycle_pending(actor) for actor in fight.translocators.entries.values()):
        # Resolve device retirement before jumping a concurrent owner cooldown.
        return 64
    for player in fight.match.players:
        script = player.body_script
        if script is None or script.retired or not fight.match._shown(player) or fight.is_dead(player):
            continue
        instance = _owner_graph(script)
        if instance is not None and _action_pending(instance):
            return 64
    return 0


@dataclass(eq=False, init=False)
class Translocator(actors.Deployable):
    cast: object
    output: tuple
    activation: tuple
    velocity: tuple
    gravity: float
    last_tick: int
    data_digest: str
    settled: bool = False
    distance: float = 0.

    @property
    def until_tick(self):
        return self.expires_tick

    @until_tick.setter
    def until_tick(self, tick):
        self.expires_tick = tick

    def __init__(self, entity, owner, source_body, cast, output, activation,
                 position, velocity, gravity, until_tick, last_tick, data_digest,
                 body_script=None, settled=False, distance=0., *, health=5.):
        super().__init__(entity, DEFINITION, 'Translocator', owner.team, position,
                         health, RADIUS, 2*RADIUS, owner, source_body, last_tick,
                         until_tick, body_script)
        self.cast, self.output, self.activation = cast, output, activation
        self.velocity, self.gravity = velocity, gravity
        self.until_tick, self.last_tick = until_tick, last_tick
        self.data_digest, self.settled, self.distance = data_digest, settled, distance


class Translocators:
    def __init__(self, combat):
        self.combat = combat
        self.entries = {}
        self.recall_poses: set[int] = set()
        self.data_path = str(DATA_PATH)
        self.data_record = self.data_graphs = self.data_digest = None

    def _owner(self, body):
        return next((p for p in self.combat.match.players
                     if p.body == body and p.has_body and p.spawned and p.client is not None), None)

    def fits_destination(self, owner, point):
        if not isinstance(point, expr.Vec3) or not all(math.isfinite(v) for v in point):
            return False
        mover = owner.mover
        radius, segment = mover._capsule()
        probe = contacts.CharacterBody(radius, segment + radius, world=mover.world.engine,
                                       category=mover.category)
        # Recall clears the spring; test the destination capsule at that same lift.
        probe.teleport((point.x, point.y + mover._pogo(), point.z))
        return (probe.fits(radius, segment + radius)
                and self.combat.walls.fits(tuple(point), radius, segment + 2 * radius, tick=self.combat.tick))

    def recall_blocked_by_graviton(self, entity):
        owner = self._owner(entity)
        if owner is None or owner.body not in self.combat.zarya_gravitons.pulled:
            return False
        return any(actor.owner is owner and actor.source_body == entity for actor in self.entries.values())

    def select_recall(self, state):
        if state.instance.graph.index != 0x0C19 or state.node.pos != 17:
            return NotImplemented
        owner = self._owner(state.instance.component.entity)
        entity = expr.to_entity(state.instance.find_var(graph.INSTANCE, 5875).value())
        actor = self.entries.get(entity)
        if (owner is None or actor is None or actor.source_body != owner.body
                or self.combat.is_dead(owner)):
            return None, False
        point = state.evaluate(state.node.field("m_69232622"), watch=False)
        return point, self.fits_destination(owner, point)

    def recall(self, state):
        if state.instance.graph.index != 0x0C19 or state.node.pos not in (18, 131):
            return NotImplemented
        owner = self._owner(state.instance.component.entity)
        point = state.evaluate(state.node.field("m_AF774F7D"), watch=False)
        selector = state.instance.states.get(state.instance.graph._by_pos[17].state)
        selected = selector.result[0] if selector is not None else None
        valid = (owner is not None and not self.combat.is_dead(owner)
                 and not self.combat.is_stunned(owner) and owner.body not in self.combat.sombra_controls.locks
                 and owner.body not in self.combat.zarya_gravitons.pulled
                 and getattr(selector, "ever_valid", False) and isinstance(point, expr.Vec3)
                 and isinstance(selected, expr.Vec3)
                 and math.dist(tuple(point), tuple(selected)) < 0.01 and self.fits_destination(owner, point))
        if not valid:
            return False, point
        # Explicit server teleport interpretation of this authored movement state.
        # Native curve/timing/momentum/prediction semantics are not claimed.
        self.combat.knockbacks.retire(owner.body)
        owner.mover.retire_impulses()
        self.combat.orisa._drop(owner.body)
        owner.mover.teleport_grounded(point)
        self.recall_poses.add(owner.body)
        return True, point

    def fire(self, owner, state, definition, origin, direction, activation, *, launch_frame=None):
        """Handle only the authored non-predicted Sombra volley, without a lease."""
        if definition != DEFINITION or not accepted_volley(state):
            return False
        if self.data_record is None and not Path(self.data_path).is_file():
            return True  # missing external entity services must not become a leased rocket
        for actor in self.entries.values():
            if actor.source_body == owner.body:
                return True  # an active beacon is recalled, never replaced by a second throw
        if self.data_record is None:
            self.data_record, self.data_graphs, self.data_digest = data(self.data_path)
        record, digest = self.data_record, self.data_digest
        definitions = {row["index"]: graph.Graph(row) for row in self.data_graphs}
        definition_graph = definitions[0x0C1A]
        pool = definition_graph._by_pos[23].field("m_amount")
        health = float(pool["m_value"])
        speed = expr.to_float(state.evaluate(state.node.field("m_projectileSpeed"), watch=False))
        motions = state.node.field("m_projectileMotions") or ()
        if any(m.get("$") != "STU_E1A5B579" for m in motions):
            return True
        gravity = sum(expr.to_float(state.evaluate(m.get("m_599AB8D7"), watch=False)) for m in motions)
        seconds = expr.to_float(state.evaluate(state.node.field("m_projectileLifetime"), watch=False))
        output = expr.lvalue(state.node.field("m_505472EF"))
        if output is None or speed <= 0 or seconds <= 0:
            return True
        match = self.combat.match
        launch_tick = match.tick
        received_frame = owner.commands.last_frame
        if launch_frame is not None and received_frame is not None and launch_frame <= received_frame:
            # The receive window ends at match.tick even while earlier commands are simulated.
            elapsed_ms = (received_frame - launch_frame) * state.instance.component.frame_ms
            launch_tick -= math.ceil(elapsed_ms / 16)
        entity = self.combat.allocate_combat_entity()
        actor = Translocator(entity, owner, owner.body, state.instance, output, activation,
                             tuple(origin), tuple(speed * axis for axis in direction), gravity,
                             launch_tick + math.ceil(seconds / 0.016), launch_tick, digest, health=health)
        actor.filter_bits = owner.team_bit
        actor.pet_owner = actor.source_body
        self.entries[entity] = actor
        self.combat.deployables[entity] = actor
        script = actor.body_script = BotScript.__new__(BotScript)
        actor_world = BodyWorld(owner.hero.guid, match.game_map.mode_guid, entity, DEFINITION)
        actor_world.match = match
        try:
            BotScript.__init__(script, DEFINITION, owner.hero.guid, entity, launch_tick,
                               match.game_map.mode_guid, health, record=record, world=actor_world,
                               resolve_graph=lambda index: definitions.get(index) or graph.graph(index),
                               source_entity=owner.body, match=match)
            state.instance.write(output, expr.Entity(entity))
            for viewer in match.players:
                if viewer.spawned and viewer.client is not None:
                    viewer.client.queue_entities([self.combat.deployable_create(actor)])
        except Exception:
            self.destroy(entity)
            raise
        return True

    def health_pool(self, state):
        if state.instance.graph.index != 0x0C1A or state.node.pos != 23:
            return
        actor = self.entries.get(state.instance.component.entity)
        if actor is None:
            return
        from ow174.game.combat import Vitals

        amount = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
        if actor.entity not in self.combat.vitals:
            self.combat.vitals[actor.entity] = Vitals(actor.entity, amount)
        actor.body_script.set_health(amount, amount)

    def destroy(self, entity):
        actor = self.entries.pop(entity, None)
        if actor is None:
            return
        if (actor.cast.component.instances.get(actor.cast.id) is actor.cast
                and expr.to_entity(actor.cast.find_var(*actor.output).value()) == entity):
            actor.cast.write(actor.output, None)
        owner_script = getattr(actor.owner, "body_script", None)
        if (owner_script is not None and actor.owner.body == actor.source_body
                and owner_script.component is actor.cast.component):
            # Original0C1A nodes31/40 notify the possessor with0B75; the body
            # receives it at0C19/entry105 and owns its stop/reset transitions.
            runtime.send_message(owner_script.component, 0x0240000000000B75, {}, entity)
            owner_script.component.run_now()
        self.combat.deployables.pop(entity, None)
        self.combat.sombra_controls.retire(entity)
        self.combat.stuns.pop(entity, None)
        self.combat._retire_source_controls(entity)
        script = actor.body_script
        component = getattr(script, "component", None)
        if component is not None:
            self.combat.match.unregister_body_component(component)
            component.disconnect_foreign_watchers()
            for instance in list(component.instances.values()):
                component.destroy(instance)
        for viewer in self.combat.match.players:
            if viewer.client is not None and viewer.spawned:
                viewer.client.queue_entities([world.EntityUpdate(entity, world.OP_DESTROY)])
        for table in (self.combat.vitals, self.combat.history, self.combat.replacement_ticks):
            table.pop(entity, None)

    def retire_body(self, body):
        self.recall_poses.discard(body)
        for actor in list(self.entries.values()):
            if actor.source_body == body:
                self.destroy(actor.entity)

    def destroy_at(self, entity, tick):
        """Retire a beacon at a world boundary before its owner's input."""
        actor = self.entries.get(entity)
        if actor is None:
            return
        match = self.combat.match
        scheduled = match.tick
        try:
            match.tick = tick
            owner = actor.owner
            script = owner.body_script
            if (owner.body == actor.source_body and script is not None and not script.retired
                    and script.component is actor.cast.component and not self.combat.is_dead(owner)):
                # Keep the graph's cooldown write on the same clock as a contiguous run.
                from ow174.game.sombra_replay import body_frame

                frame = body_frame(script, tick)
                if frame is not None and frame > script.frame:
                    owner._advance_body_gap(frame)
            self.destroy(entity)
        finally:
            match.tick = scheduled

    def destroy_authored(self, entity):
        actor = self.entries.get(entity)
        if actor is not None:
            self.destroy_at(entity, actor.last_tick)

    def expire_before(self, tick):
        for actor in list(self.entries.values()):
            if actor.until_tick < tick:
                self.destroy_at(actor.entity, actor.until_tick)

    def update(self, tick):
        """Advance beacons and report retirement inside an omitted world interval."""
        retired_earlier = False
        for actor in list(self.entries.values()):
            owner = actor.owner
            if (not owner.has_body or owner.body != actor.source_body or owner.client is None
                    or self.combat.is_dead(owner)):
                self.destroy(actor.entity)
                continue
            if tick >= actor.until_tick:
                self.destroy_at(actor.entity, actor.until_tick)
                continue
            while actor.last_tick < tick:
                actor.last_tick += 1
                if not actor.settled:
                    self._move(actor, 0.016)
                actor.body_script.advance_live(actor.last_tick, actor.position)
                if actor.entity not in self.entries:
                    retired_earlier |= actor.last_tick < tick
                    break
            if actor.entity in self.entries:
                for viewer in self.combat.match.players:
                    client = viewer.client
                    if client is None or not viewer.spawned:
                        continue
                    if client.bot_create_acked(actor.entity):
                        update = world.EntityUpdate(
                            actor.entity, movement=world.Movement(actor.position, velocity=actor.velocity),
                        )
                        update.resend = False
                        client.queue_entities([update])
                    client.queue_bot_live(actor.entity, actor.body_script.latest_live)
        return retired_earlier

    def _move(self, actor, seconds):
        velocity = actor.velocity
        delta = (velocity[0] * seconds,
                 velocity[1] * seconds - 0.5 * actor.gravity * seconds * seconds,
                 velocity[2] * seconds)
        length = math.sqrt(sum(axis * axis for axis in delta))
        direction = tuple(axis / length for axis in delta) if length else (0, -1, 0)
        # Reconstructed finite-radius sweep: center and six axial samples.
        # Full native convex contact and surface material rules remain open.
        offsets = ((0, 0, 0), (RADIUS, 0, 0), (-RADIUS, 0, 0),
                   (0, RADIUS, 0), (0, -RADIUS, 0), (0, 0, RADIUS), (0, 0, -RADIUS))
        hits = [(self.combat.walls if self.combat.walls.pieces else self.combat._collision()).cast_ray(
            tuple(a + b for a, b in zip(actor.position, offset, strict=True)), direction, length,
        ) for offset in offsets]
        hit = min((h for h in hits if h is not None), key=lambda h: h.distance, default=None)
        velocity = (velocity[0], velocity[1] - actor.gravity * seconds, velocity[2])
        if hit is None:
            actor.position = tuple(a + b for a, b in zip(actor.position, delta, strict=True))
            actor.distance += length
        else:
            actor.position = tuple(a + d * hit.distance + n * 0.00001
                                   for a, d, n in zip(actor.position, direction, hit.normal, strict=True))
            actor.distance += hit.distance
            normal_speed = sum(a * b for a, b in zip(velocity, hit.normal, strict=True))
            velocity = tuple(TANGENT * (a - normal_speed * n) - BOUNCE * normal_speed * n
                             for a, n in zip(velocity, hit.normal, strict=True))
            if hit.normal[1] > 0.5 and abs(normal_speed) < 2:
                actor.settled = True
                velocity = (0.0, 0.0, 0.0)
        actor.velocity = velocity


def native(world, cfg, instance):
    fight = getattr(getattr(world, 'match', None), 'combat', None)
    if fight is None or instance.graph.index not in (OWNER_GRAPH, DEVICE_GRAPH):
        return NotImplemented
    cls = cfg.get('$')
    if cls in ('STU_2B2E2F6D', 'STU_0F9E2904'):
        entity = expr.to_entity(instance.evaluate(cfg.get('m_81A364DE')))
        actor = fight.translocators.entries.get(entity)
        if actor is not None:
            if cls == 'STU_0F9E2904':
                return expr.Vec3(*actor.position)
            if cfg.get('m_7BDB02D1') == 0:
                return expr.Entity(actor.source_body)
    if cls == 'STU_E72F6348':
        entity = expr.to_entity(instance.evaluate(cfg.get('m_entity')))
        return entity in fight.translocators.entries or entity in fight.match.body_components
    return NotImplemented
