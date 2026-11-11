"""Selected 0CCB Halt launch and authored 0CCE pull for players and manual NPCs.

The orb local handle accepts the authored second-press message from its
launching body. Reflected handles, static capsule visibility and world-clock
retirement are host policy. Native configured ray fallbacks remain separate.
"""

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ow174.game import bots, fortify, phase_defenses, projectile_defenses, sigma_grasp
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, graph, runtime

HERO = 0x02E000000000013E
CAST, TARGETS, PULL = 0x0CCB, 0x0CCC, 0x0CCE
ORB = 0x040000000000172C
ORB_HANDLE_VAR = 5875
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


def recipient_position(world, cfg, instance):
    if instance is None or instance.graph.index != PULL or cfg.get("$") != "STU_71A87E92":
        return NotImplemented
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    if entity != instance.component.entity:
        return NotImplemented
    position = getattr(world, "owner_position", None)
    return world.position(entity) if position is None else position


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
    manual: bool = True


@dataclass
class Pull:
    owner: object
    component: object
    identifier: int
    until: int
    mover: Mover | None = None

    @property
    def instance(self):
        return self.component.instances.get(self.identifier)


class Halt:
    def __init__(self, combat):
        self.combat = combat
        self.bundle = input_bytes()
        self.orbs = {}
        self.pulls = {}

    def _allocate(self):
        entity = self.combat.next_bot
        if not bots.BOT_ENTITY <= entity < 0xD0000000:
            raise ValueError("Halt local handle allocator exhausted")
        self.combat.next_bot += 1
        return entity

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
        entity = self._allocate()
        self.orbs[entity] = Orb(
            entity, shooter, shooter.body, shooter.team, origin, direction, speed, lifetime,
            radius, math.ceil(end / TICK), shot,
        )
        state.instance.write(expr.lvalue(state.node.field("m_505472EF")), expr.Entity(entity))
        return True

    def receive(self, target, message, sender):
        orb = self.orbs.get(target)
        if (orb is None or not orb.manual or message != DETONATE
                or orb.body != sender or orb.owner.body != sender):
            return False
        if self.combat.is_dead(orb.owner):
            return False
        if orb.requested is None:
            orb.requested = self.combat.tick
        return True

    def native(self, world, cfg, instance):
        position = recipient_position(world, cfg, instance)
        if position is not NotImplemented:
            return position
        cls = cfg.get("$")
        if instance.graph.index not in (CAST, PULL) or cls != "STU_2B2E2F6D":
            return NotImplemented
        entity = expr.to_entity(instance.evaluate(cfg.get("m_81A364DE")))
        orb = self.orbs.get(entity)
        if orb is None:
            return NotImplemented
        return expr.Entity(orb.body) if cfg.get("m_7BDB02D1") == 0 else NotImplemented

    def release(self, body):
        pull = self.pulls.pop(body, None)
        if pull is None:
            return
        if pull.instance is not None:
            pull.component.destroy(pull.instance)
        if pull.mover is not None and body not in self.combat.gravitons.pulled:
            pull.owner.velocity = (0.0, 0.0, 0.0)

    @staticmethod
    def _ability_for(owner):
        if owner.hero.guid != HERO:
            return None
        script = owner.body_script
        if script is None or script.retired or script.component.strict or script.component.owner_dead:
            return None
        instance = next((item for item in script.component.instances.values()
                         if item.graph.index == CAST), None)
        return instance.state(2) if instance is not None else None

    def cooldown_remaining(self, owner):
        ability = self._ability_for(owner)
        if ability is None:
            return 0.0
        if any(orb.owner is owner and orb.body == owner.body for orb in self.orbs.values()):
            # Retiring the committed orb starts the same cooldown as node 41.
            cooldown = expr.to_float(ability.instance.find_var(expr.INSTANCE, 472).value())
            rate = ability.rate()
            return cooldown / (rate if rate > 0 else 1.0)
        rate = ability.cooldown_rate
        return ability.remaining() / (rate if rate > 0 else 1.0)

    def set_cooldown(self, owner, seconds):
        ability = self._ability_for(owner)
        if ability is not None:
            target = expr.lvalue(ability.node.field("m_out_CooldownVar"))
            if target is not None:
                rate = ability.rate()
                ability.instance.write(target, seconds * (rate if rate > 0 else 1.0))


    def retire(self, body):
        for entity, orb in tuple(self.orbs.items()):
            if orb.body == body:
                self._remove_orb(entity, orb, self.combat.tick)
        self.release(body)

    def _apply_pull(self, orb, tick):
        from ow174.game.combat import ray_world, segment_distance

        fight = self.combat
        for target in fight._enemies(orb.owner):
            actor = target.owner
            script = getattr(actor, "body_script", None)
            if (script is None or getattr(script, "retired", False) or script.component.strict
                    or phase_defenses.phase(actor)[0] or fortify.control_flag_active(actor)
                    or (not fight.match.game_map.free_for_all and target.team == orb.team)):
                continue
            if isinstance(actor, bots.Bot):
                if actor.combat_owner is not None or not actor.spawner.get("manual_spawn"):
                    continue
                immunity = script.component.entity_var(6601, create=False)
                if immunity is not None and expr.truthy(immunity.value()):
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
                if wall is not None and wall < reach:
                    continue
                # The scoped Halt visibility rule admits Orisa and Zarya barrier crossings.
                if fight._shield_trace(orb.owner, orb.position, direction, reach,
                                       include_orisa=False, include_zarya=False) is not None:
                    continue
            if isinstance(actor, bots.Bot):
                script.component.world.owner_position = expr.Vec3(*target.feet)
                # An idle NPC may have last advanced long before this detonation.
                script.advance_live(tick - 1, actor.position)
            self.release(target.entity)
            instance = script.component.create(definitions(self.bundle)[PULL])
            instance.start_entries((
                (0x0A40000000000078, expr.Vec3(*orb.position)),
                (0x0A4000000000007B, orb.radius),
                (0x0A4000000000000A, expr.Entity(orb.owner.entity)),
            ), orb.entity)
            script.component.run_now()
            seconds = sum(expr.to_float(instance.evaluate(instance.graph.node(n).field("m_timeout")))
                          for n in (13, 9, 30, 5))
            deadline = tick + math.ceil(seconds / TICK)
            self.pulls[target.entity] = Pull(actor, script.component, instance.id, deadline)

    def _remove_orb(self, entity, orb, tick):
        del self.orbs[entity]
        if orb.shot is not None:
            self.combat.projectiles._end(orb.shot, tick, explode=False)

    def _fly(self, orb, tick):
        from ow174.game.combat import EPSILON, _along

        fight = self.combat
        collision = fight._collision()
        seconds = min(TICK, orb.remaining)
        for reflections in range(projectile_defenses.MAX_REFLECTIONS_PER_STEP + 1):
            length = orb.speed * seconds
            # Include a contact at the tick boundary despite floating-point rounding.
            hit = collision.cast_ray(orb.position, orb.direction, length + EPSILON)
            limit = min(hit.distance, length) if hit is not None else length
            gate = projectile_defenses.projectile_gate(
                orb.position, orb.direction, limit, fight._enemies(orb.owner), ORB, collision=collision,
            )
            if gate is None or gate[0] >= limit - EPSILON:
                orb.position = hit.point if hit is not None else _along(orb.position, orb.direction, length)
                orb.remaining -= seconds
                if hit is not None or orb.remaining <= 0:
                    orb.requested = tick
                return True

            distance, defender, mode, direction, state = gate
            self._remove_orb(orb.entity, orb, tick)
            if mode == 1:
                # 0CCB's orb has no direct or splash health payload.
                sigma_grasp.absorbed(state, 0)
            if mode != 0 or reflections == projectile_defenses.MAX_REFLECTIONS_PER_STEP:
                return False
            # The reflected projectile gets a new owner and handle. The launch
            # graph still holds the old handle, so only automatic detonation remains.
            orb.position = _along(orb.position, orb.direction, distance)
            spent = distance / orb.speed
            seconds -= spent
            orb.remaining -= spent
            orb.entity = self._allocate()
            orb.owner, orb.body, orb.team = defender.owner, defender.entity, defender.team
            orb.direction = direction
            orb.shot = None
            orb.manual = False
            self.orbs[orb.entity] = orb

    def update(self, tick):
        fight = self.combat
        for entity, orb in tuple(self.orbs.items()):
            if orb.owner.body != orb.body or not orb.owner.has_body or orb.owner.gone:
                self._remove_orb(entity, orb, tick)
                continue
            if orb.requested is None and not self._fly(orb, tick):
                continue
            if orb.requested is None or tick < orb.requested:
                continue
            if not orb.pulled:
                self._apply_pull(orb, tick)
                orb.pulled = True
            if tick >= orb.requested + orb.retirement_ticks:
                self._remove_orb(orb.entity, orb, tick)
        for body, pull in tuple(self.pulls.items()):
            owner = pull.owner
            entity = owner.entity if isinstance(owner, bots.Bot) else owner.body
            dead = owner.dead if isinstance(owner, bots.Bot) else fight.is_dead(owner)
            if (tick >= pull.until or pull.instance is None or entity != body
                    or dead or phase_defenses.phase(owner)[0]
                    or fortify.control_flag_active(pull.owner)):
                self.release(body)

    def move_npcs(self, tick):
        fight = self.combat
        for body, pull in self.pulls.items():
            bot = pull.owner
            if not isinstance(bot, bots.Bot):
                continue
            instance = pull.instance
            if instance is None:
                continue
            instance.component.world.owner_position = expr.Vec3(*bot.position)
            if body in fight.gravitons.pulled:
                continue
            state = instance.states.get(2)
            request = state.move_request() if state is not None else None
            if request is None and pull.mover is None:
                continue
            world = fight._collision()
            if pull.mover is None:
                pull.mover = Mover(bot.position, round(bot.yaw), world, mover_data(bot.body))
            mover = pull.mover
            if tuple(mover.position) != bot.position:
                mover.position[:] = bot.position
                mover.body = None
            mover.use(world=world)
            move_to = (tuple(request[0]), request[1]) if request is not None else None
            outcome = mover.step_pose(Command(tick, yaw=round(bot.yaw)), TICK, tick, move_to=move_to)
            bot.position, bot.velocity = tuple(mover.position), mover.velocity
            bot.moved_tick = tick
            instance.component.world.owner_position = expr.Vec3(*bot.position)
            if outcome is not None:
                delay = tick * instance.component.frame_ms - instance.component.now
                state.request(runtime.FINISH if outcome else runtime.ABORT, max(0, delay))
            history = fight.history.get(body)
            if history:
                frame, _, height, eye = history[-1]
                history[-1] = frame, bot.position, height, eye
