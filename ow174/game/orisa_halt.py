"""Authored Halt input and recipient graphs with reconstructed world contact."""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots, defenses, reaper
from ow174.game.script import expr, graph, runtime

CAST = (0x0CCB, 8)
DETONATE = 0x024000000000003F
DEFLECT_GRAPH = 0x03A3
_bundle = os.environ.get("ORPHEUS_ORISA_HALT")
SUPPLEMENT_SHA256 = (
    "e7a53acc866bee6ebf3e554f7c409769f523509802dfa6e04c312a1d5385576f" if _bundle else None
)


@cache
def resources():
    raw = Path(_bundle).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SUPPLEMENT_SHA256:
        raise ValueError("Halt supplement checksum mismatch")
    data = json.loads(raw)
    graphs = {row["index"]: graph.Graph(row) for row in data["graphs"]}
    query = graphs[0xCCC]
    return graphs[0xCCE], query.node(31).field("m_value.m_value"), query.node(3).field("m_timeout.m_value")


def pull_ticks(definition, quantum):
    # Include the rounded authored waits and the guard for abandoned recipients.
    return 2 + sum(math.ceil(definition.node(pos).field("m_timeout.m_value") / quantum)
                   for pos in (13, 9, 30, 5))


@dataclass
class Orb:
    entity: int
    owner: object
    owner_body: int
    source: runtime.Instance
    definition: int
    position: tuple
    direction: tuple
    speed: float
    remaining: float
    tick: int
    until: int | None = None


@dataclass
class Pull:
    owner: object
    script: runtime.Instance
    start: int
    until: int


class Halts:
    def __init__(self, fight):
        self.fight = fight
        self.orbs = {}
        self.pulls = {}

    def launch(self, owner, state, origin, direction, speed, reach):
        resources()  # Validate the pinned inputs before admitting an orb.
        entity = self.fight.next_bot
        self.fight.next_bot += 1
        definition = state.evaluate(state.node.field("m_projectileEntity.m_entityDef"), watch=False)
        self.orbs[entity] = Orb(
            entity, owner, owner.body, state.instance, definition.guid, origin, direction, speed, reach,
            self.fight.tick,
        )
        state.instance.write(expr.lvalue(state.node.field("m_505472EF")), expr.Entity(entity))

    def receive(self, entity, message, sender):
        orb = self.orbs.get(entity)
        if (orb is None or message != DETONATE or sender != orb.source.component.entity
                or sender != orb.owner_body or sender != orb.owner.body or self.fight.is_dead(orb.owner)):
            return False
        self.detonate(orb, self.fight.tick)
        return True

    def detonate(self, orb, tick):
        from ow174.game.combat import TICK, capsule_visible, segment_distance

        if orb.until is not None:
            return
        definition, radius, window = resources()
        orb.until = tick + math.ceil(window / TICK)
        for target in self.fight._enemies(orb.owner):
            owner = target.owner
            if (owner.body_script is None
                    or (isinstance(owner, bots.Bot) and owner.spawner.get("manual_spawn") is not True)
                    or self.cryo_immune(owner) or self.fight.fortify_control_immune(target)):
                continue
            a, b = target.segment()
            if (segment_distance(orb.position, a, b) - target.radius > radius
                    or not capsule_visible(self.fight._collision(), orb.position, a, b, target.radius)):
                continue
            self.drop_pull(target.entity)
            component = owner.body_script.component
            if isinstance(owner, bots.Bot):
                component.world.owner_position = expr.Vec3(*target.feet)
            script = component.create(definition)
            script.start_entries(((0x0A40000000000078, expr.Vec3(*orb.position)),
                                  (0x0A4000000000007B, radius),
                                  (0x0A4000000000000A, expr.Entity(orb.owner_body))), orb.entity)
            component.run_now()
            if isinstance(owner, bots.Bot) and self.control_immune(script):
                component.destroy(script)
                continue
            self.pulls[target.entity] = Pull(owner, script, tick, tick + pull_ticks(definition, TICK))

    def retain_ticks(self, ticks, begin, end, quantum):
        """Keep a bounded flight plus recipient window before evaluating the endpoint."""
        for orb in self.orbs.values():
            deadline = orb.until
            if deadline is None:
                definition, _, window = resources()
                flight = math.ceil(orb.remaining / (orb.speed * quantum))
                aftermath = max(math.ceil(window / quantum), pull_ticks(definition, quantum))
                deadline = orb.tick + flight + aftermath + 2
            ticks.update(range(max(begin, orb.tick + 1), min(end, deadline) + 1))
        for pull in self.pulls.values():
            ticks.update(range(max(begin, pull.start), min(end, pull.until + 1) + 1))

    @staticmethod
    def control_immune(script):
        gate = script.states.get(script.graph.node(17).state)
        return gate is not None and gate.active and expr.truthy(gate.current)

    @staticmethod
    def cryo_immune(owner):
        # Cryo-Freeze publishes the authored control flag. The distinct damage
        # protection scope shared with Transcendence does not decide motion.
        script = owner.body_script
        if script is None or script.component.world.hero != defenses.MEI:
            return False
        flag = script.component.entity_var(6601, create=False)
        return flag is not None and expr.truthy(flag.value())

    def npc_move(self, entity, tick):
        pull = self.pulls.get(entity)
        if pull is None or tick < pull.start or tick >= pull.until or pull.script.stopped:
            return None
        state = pull.script.states.get(pull.script.graph.node(3).state)
        if state is None or tick * state.instance.component.frame_ms < state.start:
            return None
        request = state.move_request()
        return (state, request) if request is not None else None

    def npc_speed_scale(self, entity, tick):
        pull = self.pulls.get(entity)
        if pull is None or tick >= pull.until or pull.script.stopped:
            return 1.0
        linger = pull.script.states.get(pull.script.graph.node(5).state)
        if linger is None or not linger.active or tick * linger.instance.component.frame_ms < linger.start:
            return 1.0
        slow = pull.script.component.entity_var(23186, create=False)
        return 1.0 - expr.to_float(slow.value()) if slow is not None else 1.0

    def drop_pull(self, entity):
        pull = self.pulls.pop(entity, None)
        if pull is not None and not pull.script.stopped:
            body_script = pull.owner.body_script
            if (isinstance(pull.owner, bots.Bot) and body_script is not None
                    and body_script.component is pull.script.component):
                body_script.retire_live_instance(pull.script)
            else:
                pull.script.component.destroy(pull.script)

    def drop_orb(self, orb):
        del self.orbs[orb.entity]
        self.refresh_source(orb)

    @staticmethod
    def refresh_source(orb):
        if not orb.source.stopped:
            for pos in (40, 43, 47, 63):
                state = orb.source.states.get(orb.source.graph.node(pos).state)
                if state is not None and state.active:
                    state.dependency_changed()
            orb.source.component.run_now()

    def retire(self, entity):
        self.drop_pull(entity)
        for orb in tuple(self.orbs.values()):
            if orb.owner_body == entity:
                self.drop_orb(orb)

    def advance(self, orb, tick):
        from ow174.game.combat import EPSILON, TICK, _along, ray_world

        step = min(orb.remaining, (tick - orb.tick) * TICK * orb.speed)
        orb.tick = tick
        reflected = set()
        while step > EPSILON:
            wall = ray_world(self.fight._collision(), orb.position, orb.direction, step + EPSILON)
            reach = step if wall is None else min(wall, step)
            contact = self.fight._projectile_defense_contact(
                orb.owner, orb.position, orb.direction, reach, moving=True, definition=orb.definition,
                passed_defenders=reflected,
            )
            if contact is None or (wall is not None and contact[0] >= reach):
                # Keep the query on the incoming side of a wall contact.
                distance = reach if wall is None else max(0.0, reach - 0.0001)
                orb.position = _along(orb.position, orb.direction, distance)
                orb.remaining -= reach
                if wall is not None:
                    self.detonate(orb, tick)
                    return
                break
            distance, defender, state, forward = contact
            if state.instance.graph.index != DEFLECT_GRAPH:
                self.drop_orb(orb)  # This orb has no damage to convert into Grasp shields.
                return
            orb.position = _along(orb.position, orb.direction, distance)
            orb.remaining -= distance
            step -= distance
            orb.owner, orb.owner_body, orb.direction = defender, defender.body, forward
            # A body may reflect again on a later tick. Within this tick each
            # body can only reflect once, even when defense boxes overlap.
            reflected.add(defender.body)
            self.refresh_source(orb)
        if orb.remaining <= EPSILON:
            self.detonate(orb, tick)

    def update(self, tick):
        for orb in tuple(self.orbs.values()):
            if orb.owner.gone or orb.owner.body != orb.owner_body:
                self.drop_orb(orb)
                continue
            if orb.until is None and tick > orb.tick:
                self.advance(orb, tick)
            elif orb.until is not None and tick >= orb.until:
                self.drop_orb(orb)
        for entity, pull in tuple(self.pulls.items()):
            script = pull.script
            linger = script.states.get(script.graph.node(5).state)
            owner = pull.owner
            if isinstance(owner, bots.Bot):
                retired = owner.dead or owner not in self.fight.match.bots or self.control_immune(script)
            else:
                retired = (owner.body != entity or self.fight.is_dead(owner)
                           or reaper.intangible(owner) or self.cryo_immune(owner))
            if (script.stopped or retired or tick >= pull.until
                    or (linger is not None and linger.ended_at)):
                self.drop_pull(entity)


def native(world, cfg, instance):
    if not SUPPLEMENT_SHA256 or world.match is None or instance.component.strict:
        return NotImplemented
    if instance.graph.index == 0xCCE and cfg.get("$") == "STU_71A87E92":
        entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
        if entity == instance.component.entity:
            position = getattr(world, "owner_position", None)
            return world.position(entity) if position is None else position
    if instance.graph.index == CAST[0]:
        if cfg is instance.graph.node(31).field("m_condition"):
            entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
            return entity in world.match.combat.halts.orbs
        for pos in (40, 43, 47, 63):
            condition = instance.graph.node(pos).field("m_condition.m_configVars")
            if cfg is condition[0]:
                entity = expr.to_entity(instance.evaluate(cfg["m_entity"]))
                return entity in world.match.combat.halts.orbs
            if pos == 63 and cfg is condition[1]:
                entity = expr.to_entity(instance.evaluate(cfg["m_81A364DE"]))
                orb = world.match.combat.halts.orbs.get(entity)
                return expr.Entity(orb.owner_body) if orb is not None else None
    return NotImplemented
