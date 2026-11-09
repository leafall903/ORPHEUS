"""Authored Reaper 00F2 cast with reconstructed static selection and movement."""

import math

from ow174.game import contacts
from ow174.game.mover import quantise
from ow174.game.script import expr, runtime

HERO = 0x02E0000000000002
GRAPH = 0x00F2


def is_reaper(actor):
    return getattr(getattr(actor, "hero", None), "guid", None) == HERO


def _instance(actor):
    script = getattr(actor, "body_script", None)
    if not is_reaper(actor) or script is None or script.retired or script.component.owner_dead:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)


def protection_state(actor):
    instance = _instance(actor)
    return instance.states.get(34) if instance is not None else None


def protected(actor):
    state = protection_state(actor)
    return state is not None and state.active


def needs_clock(actor):
    instance = _instance(actor)
    if instance is None:
        return False
    casting = instance.states.get(19)
    ability = instance.states.get(26)
    return bool((casting is not None and casting.active) or (ability is not None and ability.remaining() > 0))


class ShadowSteps:
    def __init__(self, combat):
        self.combat = combat
        self.casts = {}

    def retire(self, entity):
        self.casts.pop(entity, None)

    def interrupt(self, actor):
        instance = _instance(actor)
        if instance is None:
            return
        for index in (6, 19):
            state = instance.states.get(index)
            if state is not None and state.active:
                state.request(runtime.ABORT)
                instance.component.run_now()
                self.retire(actor.body)
                break

    def _landing(self, player, point) -> bool:
        """Reconstructed standing-capsule clearance and live-actor exclusion."""
        mover = player.mover
        radius, segment = mover._capsule(False)
        body = contacts.CharacterBody(radius, segment + radius, world=self.combat._collision().engine,
                                      category=mover.category)
        body.teleport((point[0], point[1] + mover.data.stand_pogo, point[2]))
        if not body.fits(radius, segment + radius):
            return False
        ground = self.combat._collision().ground(point[0], point[2], point[1] + 0.1, point[1] - 0.1)
        if ground is None or ground.normal[1] < mover.slope_cos:
            return False
        for target in self.combat._targets():
            if target.owner is player or self.combat.vitals_of(target).dead:
                continue
            if (abs(point[1] - target.feet[1]) < max(mover.data.stand_height, target.height)
                    and math.hypot(point[0] - target.feet[0], point[2] - target.feet[2])
                    < radius + target.radius):
                return False
        return True

    def command(self, player, command) -> None:
        """Bridge00F2's selector and teleport with declared server geometry policy.

        The native landing adjustment solver and random/context callbacks remain
        unimplemented; require a directly visible walkable surface within the
        authored35m ray and a free standing capsule. Keep authored cast phases,
        recovery and cooldown; lock a confirmed destination until completion.
        """
        if not is_reaper(player):
            return
        from ow174.game.combat import EPSILON, _number, aim

        script = player.body_script
        if script.component.owner_dead or self.combat.is_dead(player):
            self.casts.pop(player.body, None)
            return
        instance = _instance(player)
        if instance is None:
            return
        cast = instance.states.get(32)
        if cast is not None and cast.active and cast.node.cls == "STU_54FEB21C" and not any(
            event.state is cast and event.kind == runtime.STATE_OP and event.op == runtime.FINISH
            for event in instance.queue
        ):
            duration = _number(cast.evaluate(cast.node.field("m_ED97A97C"), watch=False), 0.0)
            cast.request_finish(max(0, round(duration * 1000) - (cast.now - cast.start)))
        idle = instance.states.get(1)
        if command is not None and command.buttons & 16 and idle is not None and idle.active:
            # The compiled logical-button condition names this waiting state.
            # Recheck only this authored transition after the real input edge.
            idle.try_complete()
            script.component.run_now()
        selector = instance.states.get(0)
        record = self.casts.get(player.body)
        if command is not None and not command.buttons & 16:
            idle, targeting, casting = (instance.states.get(index) for index in (1, 6, 19))
            if (idle is not None and not idle.active and not (targeting and targeting.active)
                    and not (casting and casting.active)):
                # The unsupported native reset action on a refused cooldown
                # attempt must not permanently retire the input wait. Re-arm
                # only after release, avoiding an immediate held-button cycle.
                idle.request(runtime.BEGIN)
                script.component.run_now()
        if selector is not None and selector.active and command is not None:
            identity = (instance.id, selector.counter)
            if record is None or record["selector"] != identity:
                record = self.casts[player.body] = {
                    "selector": identity, "buttons": command.buttons, "target": None,
                    "cast": None, "moved": False,
                }
            origin = self.combat._eye(player)
            reach = _number(selector.evaluate(selector.node.field("m_6386E7B4"), watch=False), 0.0)
            hit = self.combat._collision().cast_ray(origin, aim(*command.aim()), reach)
            point = hit.point if hit is not None else None
            valid = (point is not None and hit.normal[1] >= player.mover.slope_cos
                     and self._landing(player, point))
            record["target"] = tuple(point) if valid else None
            instance.write((0, 983), bool(valid))
            if point is not None:
                instance.write((0, 432), expr.Vec3(*point))
                instance.write((0, 985), expr.Vec3(*point))
            down = command.buttons & ~record["buttons"]
            record["buttons"] = command.buttons
            if down & 2:
                instance.state(6).request(runtime.ABORT)
                script.component.run_now()
                record["target"] = None
            elif down & 1 and valid:
                instance.follow(selector.node, "m_F6955E81", selector)
                script.component.run_now()
                cast = instance.states.get(32)
                if cast is not None and cast.active:
                    record["cast"] = (cast.counter, cast.start)
        if record is None or record["cast"] is None or record["moved"]:
            return
        cast = instance.states.get(32)
        if (cast is None or (cast.counter, cast.start) != record["cast"] or cast.active
                or cast.ended_at <= cast.start):
            return
        # A parent/control abort before the authored completion cannot teleport.
        delay = _number(cast.evaluate(cast.node.field("m_ED97A97C"), watch=False), 0.0) * 1000
        record["moved"] = True
        if cast.ended_at - cast.start + EPSILON < delay:
            return
        point = record["target"]
        if point is None or not self._landing(player, point):
            return
        mover = player.mover
        mover.position[:] = [quantise(axis) for axis in point]
        mover.velocity = (0.0, 0.0, 0.0)
        mover.spring = mover.spring2 = mover.spring_speed = mover.fall = 0.0
        mover.spring_steps = (0, 0)
        mover.body = None
        if mover.history and mover.history[-1].frame == script.frame:
            mover.history[-1] = mover.snapshot(script.frame)
        poses = self.combat.history.get(player.body)
        if poses and poses[-1][0] == self.combat.tick:
            poses[-1] = (self.combat.tick, tuple(mover.position), *poses[-1][2:])
