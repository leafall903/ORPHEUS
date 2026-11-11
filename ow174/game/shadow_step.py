"""Host Reaper's 00F2 placement and world-time teleport."""

import math
from dataclasses import dataclass

from ow174.game import contacts
from ow174.game.mover import quantise
from ow174.game.script import expr, runtime

HERO, GRAPH = 0x02E0000000000002, 0x00F2


def root(actor):
    script = getattr(actor, "body_script", None)
    if (getattr(script, "hero", None) != HERO or script.retired or script.component.strict
            or script.component.owner_dead or script.component.entity != actor.body):
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == GRAPH), None)


def immune(actor):
    instance = root(actor)
    if instance is None or instance.component.world.match is None:
        return False
    fight = instance.component.world.match.combat
    cast = fight.shadow.casts.get(actor.body)
    return cast is not None and cast.immune <= fight.tick < cast.ends and cast.active(instance)


def gate_delay(state):
    instance = state.instance
    if (instance.graph.index != GRAPH or state.node.pos != 61 or instance.component.strict):
        return NotImplemented
    delay = expr.to_float(state.evaluate(state.node.field("m_ED97A97C"), watch=False))
    return round(delay * 1000) if math.isfinite(delay) and delay > 0 else NotImplemented


@dataclass
class Preview:
    identity: tuple
    buttons: int


@dataclass
class Cast:
    point: tuple
    identity: tuple
    immune: int
    ends: int

    def active(self, instance):
        state = instance.states.get(32) if instance is not None else None
        return state is not None and state.active and self.identity == (
            instance.id, state.counter, state.start,
        )


class ShadowStep:
    def __init__(self, fight):
        self.fight = fight
        self.previews = {}
        self.casts = {}

    def retire(self, body):
        self.previews.pop(body, None)
        self.casts.pop(body, None)

    def interrupt(self, actor, body):
        self.retire(body)
        instance = root(actor)
        if instance is not None:
            for index in (6, 19):
                state = instance.states.get(index)
                if state is not None and state.active:
                    state.request(runtime.ABORT)
            instance.component.run_now()

    def landing(self, player, point):
        fight = self.fight
        mover = player.mover
        radius, segment = mover._capsule(False)
        collision = fight._collision()
        body = contacts.CharacterBody(radius, segment + radius, world=collision.engine,
                                      category=mover.category)
        body.teleport((point[0], point[1] + mover.data.stand_pogo, point[2]))
        if not body.fits(radius, segment + radius) or not fight.walls.fits(
            point, radius, mover.data.stand_height,
        ):
            return False
        ground = fight._line_collision().cast_ray((point[0], point[1] + .1, point[2]), (0, -1, 0), .2)
        if ground is None or ground.normal[1] < mover.slope_cos:
            return False
        for target in fight._targets():
            if target.owner is player or target.is_device or fight.vitals_of(target).dead:
                continue
            if (abs(point[1] - target.feet[1]) < max(mover.data.stand_height, target.height)
                    and math.hypot(point[0] - target.feet[0], point[2] - target.feet[2])
                    < radius + target.radius):
                return False
        return True

    def command(self, player, command):
        from ow174.game.combat import aim

        instance = root(player)
        if instance is None or command is None:
            return
        selector = instance.states.get(0)
        if selector is None or not selector.active:
            self.previews.pop(player.body, None)
            return
        if player.body in self.fight.hacks:
            self.interrupt(player, player.body)
            return
        identity = instance.id, selector.counter
        preview = self.previews.get(player.body)
        if preview is None or preview.identity != identity:
            preview = self.previews[player.body] = Preview(identity, command.buttons)
        reach = expr.to_float(selector.evaluate(selector.node.field("m_6386E7B4"), watch=False))
        origin = self.fight._eye(player)
        hit = self.fight._line_collision().cast_ray(origin, aim(*command.aim()), reach)
        valid = (hit is not None and hit.normal[1] >= player.mover.slope_cos
                 and self.landing(player, hit.point))
        instance.write((0, 983), valid)
        if hit is not None:
            point = expr.Vec3(*hit.point)
            instance.write((0, 432), point)
            instance.write((0, 985), point)
        down = command.buttons & ~preview.buttons
        preview.buttons = command.buttons
        if down & 2:
            self.interrupt(player, player.body)
        elif down & 1 and valid:
            instance.follow(selector.node, "m_F6955E81", selector)
            instance.component.run_now()
            state = instance.states.get(32)
            windup = instance.states.get(33)
            if state is not None and state.active and windup is not None and windup.active:
                frame_ms = instance.component.frame_ms
                now = self.fight.tick
                self.casts[player.body] = Cast(
                    tuple(hit.point), (instance.id, state.counter, state.start),
                    now + math.ceil(windup.ms / frame_ms), now + math.ceil(gate_delay(state) / frame_ms),
                )
            self.previews.pop(player.body, None)

    def replay_until(self, previous):
        return max((cast.ends for cast in self.casts.values()), default=previous)

    @staticmethod
    def recovery_frame(player):
        instance = root(player)
        scope = instance.states.get(19) if instance is not None else None
        if scope is None or not scope.active:
            return None
        cast = instance.states.get(32)
        if cast is None or not cast.counter or cast.start < scope.start:
            return None
        duration = gate_delay(cast)
        recovery = instance.graph.node(16)
        delay = expr.to_float(instance.evaluate(recovery.field("m_timeout")))
        if duration is NotImplemented or not math.isfinite(delay) or delay <= 0:
            return None
        return (cast.start + duration + round(delay * 1000)) // instance.component.frame_ms

    def update(self, tick):
        fight = self.fight
        for player in fight.match.players:
            cast = self.casts.get(player.body)
            if cast is None:
                continue
            if not cast.active(root(player)) or fight.is_dead(player):
                self.retire(player.body)
                continue
            if tick < cast.ends:
                continue
            self.retire(player.body)
            if not self.landing(player, cast.point):
                continue
            fight.impulses.retire(player.body)
            fight.halt.drop(player.body)
            player.mover.teleport_grounded(quantise(axis) for axis in cast.point)
