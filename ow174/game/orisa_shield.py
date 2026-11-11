"""Scoped Orisa 0BD7 deployment and 0BD8 physical barrier.

The original volley and child resources supply motion, deployment delay,
capacity and duration. Fixed-step swept static landing and a mesh at the
landing yaw are selected host behavior; there is no native Pet replication.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import flashbang
from ow174.game.collision import World
from ow174.game.script import expr, graph

HERO = 0x02E000000000013E
CAST_GRAPH = 0x0BD7
CAST_ENTITY = 0x0400000000001846
SHIELD_ENTITY = 0x04000000000014A1
GEOMETRY_SHA256 = "6a37fa71cb176191b11807f46d2feade340a22be6afc384969eeeb61df6fb1ef"
TICK = .016


def geometry_bytes():
    path = os.environ.get("ORPHEUS_ORISA_GEOMETRY")
    if not path:
        return None
    raw = Path(path).read_bytes()
    geometry(raw)
    return raw


@cache
def geometry(raw):
    if hashlib.sha256(raw).hexdigest() != GEOMETRY_SHA256:
        raise ValueError("unrecognized Orisa barrier geometry checksum")
    data = json.loads(raw)
    if (data.get("schema") != "orpheus-orisa-geometry-v1"
            or data.get("build") != "1.74.0.0.104319" or data.get("model") != "0x0D00000000004062"):
        raise ValueError("wrong Orisa barrier geometry")
    return World(data["triangles"], data["masks"], name="Orisa shield 4062")


@dataclass
class Flight:
    owner: object
    body: int
    team: int
    position: tuple
    velocity: tuple
    yaw: float
    gravity: float
    remaining: float
    deploy_delay: float
    health: float
    duration: float
    shot: object


@dataclass
class Shield:
    owner: object
    body: int
    team: int
    position: tuple
    yaw: float
    starts: int
    ends: int
    health: float

    def damage(self, amount):
        self.health = max(0.0, self.health - max(0.0, amount))


class Shields:
    def __init__(self, combat):
        self.combat = combat
        self.geometry = geometry_bytes()
        self.flights = []
        self.pending = []
        self.active = {}
        self.last_tick = None

    def retire(self, body):
        self.flights[:] = [f for f in self.flights if f.body != body]
        self.pending[:] = [s for s in self.pending if s.body != body]
        self.active.pop(body, None)

    def throw(self, shooter, state, origin, direction, definition, speed, lifetime, shot):
        if (getattr(getattr(shooter, "hero", None), "guid", None) != HERO
                or state.instance.graph.index != CAST_GRAPH or state.node.pos != 1
                or state.instance.component is not shooter.body_script.component
                or definition != CAST_ENTITY):
            return False
        if self.geometry is None:
            expr.warn_once("orisa_geometry", "missing Orisa barrier geometry; set ORPHEUS_ORISA_GEOMETRY")
            return False
        spawn = (state.node.field("m_49CBA3BC") or {}).get("m_entityDefinition")
        asset = state.evaluate(spawn, watch=False)
        motions = state.node.field("m_projectileMotions") or ()
        if (not isinstance(asset, expr.Asset) or asset.guid != SHIELD_ENTITY or len(motions) != 1
                or motions[0].get("$") != "STU_E1A5B579" or motions[0].get("m_D0C4CF49") is not None):
            raise ValueError("unexpected Orisa barrier launch resource")
        gravity = expr.to_float(state.evaluate(motions[0].get("m_599AB8D7"), watch=False))
        delay_cfg = (state.node.field("m_E365DB20") or {}).get("m_0D20DAFD")
        delay = expr.to_float(state.evaluate(delay_cfg, watch=False))
        child = graph.graph(0x0BD8)
        if child is None:
            raise ValueError("missing Orisa barrier child graph")

        def init_value(var):
            setters = [n for n in child.nodes if n.cls == "STUStatescriptActionSetVar"
                       and (n.field("m_out_Var") or {}).get("m_identifier") == var]
            if len(setters) != 1:
                raise ValueError(f"ambiguous Orisa barrier initialization: {var}")
            return expr.to_float(state.evaluate(setters[0].field("m_value"), watch=False))

        # 0BD8/node8 writes capacity586; node9 copies it to active pool maximum587.
        health, duration = init_value(586), init_value(567)
        if not all(math.isfinite(x) and x > 0 for x in (gravity, delay, health, duration)):
            raise ValueError("invalid Orisa barrier settings")
        self.flights.append(Flight(
            shooter, shooter.body, shooter.team, origin, tuple(expr.f32(v * speed) for v in direction),
            math.atan2(direction[0], direction[2]), gravity, lifetime, delay, health, duration, shot,
        ))
        return True

    def update(self, tick):
        if self.last_tick is not None and tick <= self.last_tick:
            return
        first = tick if self.last_tick is None else max(self.last_tick + 1, tick - 1874)
        self.last_tick = tick
        if not (self.flights or self.pending or self.active):
            return
        fight = self.combat
        for frame in range(first, tick + 1):
            for body, shield in tuple(self.active.items()):
                if shield.health <= 0 or shield.ends <= frame:
                    del self.active[body]
            for shield in tuple(self.pending):
                if shield.starts <= frame:
                    self.pending.remove(shield)
                    if frame < shield.ends and shield.owner.body == shield.body:
                        self.active[shield.body] = shield
            for flight in tuple(self.flights):
                if flight.owner.body != flight.body or not flight.owner.has_body:
                    self.flights.remove(flight)
                    continue
                duration = min(TICK, flight.remaining)
                flight.velocity = flashbang.gravity_velocity(flight.velocity, flight.gravity, duration)
                speed = math.sqrt(sum(v * v for v in flight.velocity))
                direction = tuple(v / speed for v in flight.velocity) if speed > 1e-9 else (0.0, 0.0, 0.0)
                length = speed * duration
                hit = fight._collision().cast_ray(flight.position, direction, length) if length else None
                if hit is not None:
                    due = frame + math.ceil(flight.deploy_delay / TICK)
                    self.pending.append(Shield(
                        flight.owner, flight.body, flight.team, hit.point, flight.yaw,
                        due, due + math.ceil(flight.duration / TICK), flight.health,
                    ))
                flight.position = tuple(
                    x + v * duration for x, v in zip(flight.position, flight.velocity, strict=True)
                )
                flight.remaining -= duration
                if hit is not None or flight.remaining <= 0:
                    self.flights.remove(flight)
                    if flight.shot is not None:
                        fight.projectiles._end(flight.shot, frame, explode=False)

    def first(self, shooter, origin, direction, length, *, team=None):
        if self.geometry is None or not self.active:
            return None
        world = geometry(self.geometry)
        side = shooter.team if team is None else team
        best = None
        for shield in self.active.values():
            if (shield.owner is shooter or shield.health <= 0
                    or not shield.starts <= self.combat.tick < shield.ends
                    or (not self.combat.match.game_map.free_for_all and shield.team == side)):
                continue
            cosine, sine = math.cos(shield.yaw), math.sin(shield.yaw)

            def local(vector, cosine=cosine, sine=sine):
                x, y, z = vector
                return cosine * x - sine * z, y, sine * x + cosine * z

            offset = tuple(a - b for a, b in zip(origin, shield.position, strict=True))
            hit = world.cast_ray(local(offset), local(direction), length, category=0xFFFF)
            if hit is not None and (best is None or hit.distance < best[0]):
                best = hit.distance, shield
        return best
