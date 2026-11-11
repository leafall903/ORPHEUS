"""Selected 0CCB Halt launch and authored 0CCE player pull.

The orb local handle accepts the authored second-press message from its
launching body. Static capsule visibility and world-clock retirement are
declared host policy; native configured ray fallbacks remain separate.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots
from ow174.game.script import expr, graph, nodes

HERO = 0x02E000000000013E
CAST, TARGETS, PULL = 0x0CCB, 0x0CCC, 0x0CCE
ORB = 0x040000000000172C
DETONATE = 0x024000000000003F
SHA256 = "e7a53acc866bee6ebf3e554f7c409769f523509802dfa6e04c312a1d5385576f"
TICK = 0.016


def input_bytes():
    path = os.environ.get("ORPHEUS_ORISA_HALT")
    if not path:
        return None
    raw = Path(path).read_bytes()
    definitions(raw)
    return raw


@cache
def definitions(raw):
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError("Halt metadata checksum mismatch")
    source = json.loads(raw.decode("utf-8-sig"))
    if source.get("schema") != "orpheus-orisa-halt-v1" or source.get("build") != "1.74.0.0.104319":
        raise ValueError("Halt metadata schema/build mismatch")
    result = {row["index"]: graph.Graph(row) for row in source["graphs"]}
    if set(result) != {TARGETS, PULL}:
        raise ValueError("Halt metadata lacks authored target and control graphs")
    return result


@dataclass
class Orb:
    entity: int
    owner: object
    body: int
    team: int
    position: tuple
    direction: tuple
    speed: float
    remaining: float
    radius: float
    retirement_ticks: int
    shot: object
    requested: int | None = None
    pulled: bool = False


@dataclass
class Pull:
    owner: object
    instance: object
    until: int


class Halt:
    def __init__(self, combat):
        self.combat = combat
        self.bundle = input_bytes()
        self.orbs = {}
        self.pulls = {}
        self.last_tick = None

    def launch(self, shooter, state, origin, direction, definition, speed, lifetime, shot):
        if (getattr(getattr(shooter, "hero", None), "guid", None) != HERO
                or state.instance.graph.index != CAST or state.node.pos != 8 or definition != ORB
                or state.instance.component is not shooter.body_script.component):
            return False
        if self.bundle is None:
            expr.warn_once("orisa_halt", "missing authored Halt inputs; set ORPHEUS_ORISA_HALT")
            return False
        cfg = definitions(self.bundle)[TARGETS]
        radius = expr.to_float(state.evaluate(cfg.node(31).field("m_value"), watch=False))
        end = expr.to_float(state.evaluate(cfg.node(3).field("m_timeout"), watch=False))
        if not all(math.isfinite(n) and n > 0 for n in (radius, end)):
            raise ValueError("invalid Halt target settings")
        fight = self.combat
        entity = fight.next_bot
        if not bots.BOT_ENTITY <= entity < 0xD0000000:
            raise ValueError("Halt local handle allocator exhausted")
        fight.next_bot += 1
        self.orbs[entity] = Orb(
            entity, shooter, shooter.body, shooter.team, origin, direction, speed, lifetime,
            radius, math.ceil(end / TICK), shot,
        )
        state.instance.write(expr.lvalue(state.node.field("m_505472EF")), expr.Entity(entity))
        return True

    def receive(self, target, message, sender):
        orb = self.orbs.get(target)
        if orb is None or message != DETONATE or orb.body != sender or orb.owner.body != sender:
            return False
        if self.combat.is_dead(orb.owner):
            return False
        if orb.requested is None:
            orb.requested = self.combat.tick
        return True

    def native(self, world, cfg, instance):
        cls = cfg.get("$")
        if instance.graph.index == PULL and cls == "STU_71A87E92":
            entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
            return world.position(entity) if entity == world.body else NotImplemented
        if instance.graph.index not in (CAST, PULL) or cls != "STU_2B2E2F6D":
            return NotImplemented
        entity = expr.to_entity(instance.evaluate(cfg.get("m_81A364DE")))
        orb = self.orbs.get(entity)
        if orb is None:
            return NotImplemented
        return expr.Entity(orb.body) if cfg.get("m_7BDB02D1") == 0 else NotImplemented

    def _drop(self, body):
        pull = self.pulls.pop(body, None)
        if pull is not None and not pull.instance.stopped:
            pull.instance.component.destroy(pull.instance)

    def retire(self, body):
        for entity, orb in tuple(self.orbs.items()):
            if orb.body == body:
                del self.orbs[entity]
        self._drop(body)

    def _apply_pull(self, orb, tick):
        from ow174.game.combat import ray_world, segment_distance

        fight = self.combat
        for target in fight._enemies(orb.owner):
            actor = target.owner
            script = getattr(actor, "body_script", None)
            if (isinstance(actor, bots.Bot) or script is None or script.retired or fight.control_immune(actor)
                    or fight.captured(target.entity)
                    or (not fight.match.game_map.free_for_all and target.team == orb.team)):
                continue
            a, b = target.segment()
            if segment_distance(orb.position, a, b) - target.radius > orb.radius:
                continue
            point = (a[0], max(a[1], min(b[1], orb.position[1])), a[2])
            length = math.dist(orb.position, point)
            if length > 1e-9:
                direction = tuple((y - x) / length for x, y in zip(orb.position, point, strict=True))
                reach = max(0.0, length - target.radius)
                wall = ray_world(fight._collision(), orb.position, direction, reach)
                wall = fight.walls.distance(orb.position, direction, reach, wall)
                if wall is not None and wall < reach:
                    continue
                if fight._shield_hit(orb.owner, orb.position, direction, reach, team=orb.team) is not None:
                    continue
            self._drop(target.entity)
            # Stop only Widow's competing authored grapple movement scope.
            for owned in tuple(script.component.instances.values()):
                if owned.graph.index == 0x005A:
                    for ability in tuple(owned.states.values()):
                        if isinstance(ability, nodes.Ability) and ability.flags & ability.RUNNING:
                            ability.stop()
            instance = script.component.create(definitions(self.bundle)[PULL])
            instance.start_entries((
                (0x0A40000000000078, expr.Vec3(*orb.position)),
                (0x0A4000000000007B, orb.radius),
                (0x0A4000000000000A, expr.Entity(orb.owner.entity)),
            ), orb.entity)
            script.component.run_now()
            seconds = sum(expr.to_float(instance.evaluate(instance.graph.node(n).field("m_timeout")))
                          for n in (13, 9, 30, 5))
            self.pulls[target.entity] = Pull(actor, instance, tick + math.ceil(seconds / TICK))

    def update(self, tick):
        if self.last_tick is not None and tick <= self.last_tick:
            return
        first = tick if self.last_tick is None else max(self.last_tick + 1, tick - 2499)
        self.last_tick = tick
        if not self.orbs and not self.pulls:
            return
        fight = self.combat
        for frame in range(first, tick + 1):
            for entity, orb in tuple(self.orbs.items()):
                if orb.owner.body != orb.body or not orb.owner.has_body:
                    del self.orbs[entity]
                    continue
                if orb.requested is None:
                    seconds = min(TICK, orb.remaining)
                    length = orb.speed * seconds
                    static = fight._collision().cast_ray(orb.position, orb.direction, length)
                    hit, _ = fight.walls.contact(orb.position, orb.direction, length, static)
                    if hit is not None:
                        orb.position = hit.point
                        orb.requested = frame
                    else:
                        orb.position = tuple(
                            x + v * length for x, v in zip(orb.position, orb.direction, strict=True)
                        )
                    orb.remaining -= seconds
                    if orb.remaining <= 0:
                        orb.requested = frame
                if orb.requested is None or frame < orb.requested:
                    continue
                if not orb.pulled:
                    self._apply_pull(orb, frame)
                    orb.pulled = True
                if frame >= orb.requested + orb.retirement_ticks:
                    del self.orbs[entity]
                    if orb.shot is not None:
                        fight.projectiles._end(orb.shot, frame, explode=False)
            for body, pull in tuple(self.pulls.items()):
                if (frame >= pull.until or pull.instance.stopped or pull.owner.body != body
                        or fight.is_dead(pull.owner) or fight.control_immune(pull.owner)):
                    self._drop(body)
