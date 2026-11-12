"""Delivered Pulse Bomb inputs on the combined accelerated/contact-fuse host."""

import math
from dataclasses import dataclass, replace

from ow174.game.combat import Rocket
from ow174.game.script import expr

HERO = 0x02E0000000000003
GRAPH = 0x0034
ATTRIBUTE = 0x0F4800000000015D
DEFAULT_DAMAGE = 350.0


def resource_float(world, cfg, instance):
    """Resolve only the verified default resource on this live Tracer body."""
    resource = cfg.get("m_DBF61E9A")
    if (world.hero == HERO and instance.component.entity == world.body
            and isinstance(resource, dict) and resource.get("$") == "STU_D9BDAA46"
            and expr.guid_of(resource.get("m_DBF61E9A")) == ATTRIBUTE
            and instance.evaluate(cfg.get("m_entity")) == expr.Entity(world.body)):
        return DEFAULT_DAMAGE
    return NotImplemented


def is_pulse(shooter, state):
    return (getattr(getattr(shooter, "hero", None), "guid", None) == HERO
            and state.instance.graph.index == GRAPH and state.node.pos == 1)


def launch_direction(state, forward):
    """Apply the authored vertical launch bias before projectile speed."""
    launch = state.node.field("m_5EC1EB38") or {}
    config = launch.get("m_22CE7F30")
    if config is None:
        return forward
    # D5ECB4 through D5ECEA, as verified by the shared projectile launch probe.
    lift = expr.f32(expr.to_float(state.evaluate(config, watch=False)))
    x, y, z = (expr.f32(value) for value in forward)
    y = expr.f32(y + lift)
    squared = expr.f32(expr.f32(expr.f32(x * x) + expr.f32(y * y)) + expr.f32(z * z))
    if squared < 1.1754943508222875e-36:
        return (-0.0, -1.0, -0.0)
    inverse = 1 / math.sqrt(squared)
    return tuple(expr.f32(value * inverse) for value in (x, y, z))


class Bomb(Rocket):
    pulse_bomb = True

    @property
    def stuck(self):
        return self.fuse_left is not None

    @property
    def attached(self):
        return self.attached_entity

    @property
    def offset(self):
        return self.attached_offset

    @property
    def source(self):
        return self.source_body

    @property
    def radius(self):
        return self.direct.projectile_extent if self.direct is not None else 0.0

    @property
    def life_left(self):
        return self.left

    @property
    def fuse(self):
        return self.sticky_delay

    @property
    def age(self):
        return self.flown + (max(0.0, self.sticky_delay - self.fuse_left) if self.stuck else 0.0)


@dataclass(frozen=True)
class CommandPoseAnchor:
    world: int
    first: int
    trusted: bool
    latest: int | None
    recall_frames: tuple[int, ...]
    deflect_frames: tuple[tuple[int, tuple], ...] = ()


class PulseBombs:
    def __init__(self, combat):
        self.combat = combat
        self.anchors = {}
        self.poses = {}
        self.context = None

    def observe_command_pose(self, player, frame, exact):
        """Retain the delivered command/world anchor and per-command Recall state."""
        if not player.mover.history or player.mover.history[-1].frame != frame:
            self.anchors.pop(player.body, None)
            return
        world = self.combat.match.tick
        previous = self.anchors.get(player.body)
        latest = frame if exact else None
        protected = (frame,) if self.combat.recalls.locked(player) else ()
        from ow174.game.combat import aim

        facing = ((frame, aim(player.mover.yaw, player.mover.pitch)),) if self._deflecting(player) else ()
        if previous is None:
            anchor = CommandPoseAnchor(world, frame, False, latest, protected, facing)
        elif world == previous.world:
            continued = (previous.trusted and exact and previous.latest is not None
                         and frame == previous.latest + 1
                         and frame - previous.first < player.mover.history.maxlen)
            anchor = CommandPoseAnchor(world, previous.first, continued, latest,
                                       previous.recall_frames + protected if continued else (),
                                       previous.deflect_frames + facing if continued else ())
        else:
            aligned = (exact and world == previous.world + 1
                       and previous.latest == previous.first and frame == previous.first + 1)
            anchor = CommandPoseAnchor(world, frame, aligned, latest, protected, facing)
        self.anchors[player.body] = anchor

    def prepare_gap(self, tick, targets):
        """Use only complete, explicitly received pose batches, as in the delivery."""
        from ow174.game.combat import eye_height, mover_data
        from ow174.game.mover import CROUCHED

        self.poses = {}
        if tick <= self.combat.tick + 1 or not self.bombs:
            return
        for target in targets:
            anchor = self.anchors.get(target.entity)
            if (anchor is None or not anchor.trusted or anchor.world != self.combat.tick
                    or anchor.latest != anchor.first + tick - anchor.world):
                continue
            history = {sample.frame: sample for sample in target.owner.mover.history}
            frames = range(anchor.first, anchor.latest)
            if (anchor.latest not in history or history[anchor.latest].position != target.feet
                    or any(number not in history for number in frames)):
                continue
            body = target.owner.hero.body
            data = mover_data(body)
            deflect_frames = dict(anchor.deflect_frames)
            for step, number in enumerate(frames, 1):
                sample = history[number]
                crouched = bool(sample.flags & CROUCHED)
                pose = replace(target, feet=sample.position,
                               height=data.crouch_height if crouched else data.stand_height,
                               eye=eye_height(body, crouched))
                self.poses[target.entity, anchor.world + step] = (
                    pose, number in anchor.recall_frames, deflect_frames.get(number))

    def sampled(self, target):
        if self.context is None:
            return target
        sample = self.poses.get((target.entity, self.context))
        return sample[0] if sample is not None else target

    def recalling(self, target):
        sample = self.poses.get((target.entity, self.context)) if self.context is not None else None
        return sample[1] if sample is not None else self.combat.recalls.active(target.owner)

    def _deflecting(self, owner):
        if (getattr(getattr(owner, "hero", None), "guid", None) != 0x02E0000000000029
                or self.combat.is_stunned(owner)):
            return False
        script = getattr(owner, "body_script", None)
        if script is None or script.component.owner_dead:
            return False
        return any(instance.graph.index == 0x03A3 and (state := instance.states.get(10)) is not None
                   and state.active for instance in script.component.instances.values())

    def deflect_aim(self, target, incoming):
        from ow174.game.combat import aim

        if target is None:
            return None
        sample = self.poses.get((target.entity, self.context)) if self.context is not None else None
        facing = sample[2] if sample is not None else (
            aim(target.owner.mover.yaw, target.owner.mover.pitch) if self._deflecting(target.owner) else None)
        return facing if facing is not None and sum(a * b for a, b in zip(facing, incoming)) < -1e-7 else None

    def update(self, tick):
        """Keep the delivered idempotent world-clock entry point."""
        if tick != self.combat.tick:
            raise ValueError("Pulse update requires the current authoritative combat tick")
        for bomb in self.bombs:
            self.combat._fly(bomb)

    @property
    def bombs(self):
        return [rocket for rocket in self.combat.rockets if isinstance(rocket, Bomb)]
