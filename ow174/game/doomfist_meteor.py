"""Authored Meteor stages/damage with explicitly reconstructed spatial services."""

import math
from dataclasses import dataclass, replace

from ow174.game.deflection import state_deadline
from ow174.game.script import expr, runtime

HERO = 0x02E000000000012F
LOADOUT = 0x0B90000000000108


@dataclass
class Cast:
    activation: tuple
    target: tuple
    confirm_down: bool = False
    committed: bool = False
    impacted: bool = False
    finished: bool = False
    hit: tuple = ()
    damage: tuple = ()
    impact_at: float | None = None
    world_landed_frame: int | None = None


class Meteors:
    def __init__(self, combat):
        self.combat = combat
        self.casts: dict[int, Cast] = {}

    @staticmethod
    def instance(player):
        if (player.hero.guid != HERO or player.body_script is None
                or player.body_script.component.strict):
            return None
        return next((i for i in player.body_script.component.instances.values()
                     if i.graph.index == 0xC51), None)

    def protected(self, player):
        if not getattr(player, "has_body", False):
            return False
        instance = self.instance(player)
        phase = instance.states.get(24) if instance is not None else None
        cast = self.casts.get(player.body)
        return (phase is not None and phase.active
                and (cast is None or (not cast.impacted and not cast.finished)))

    def retire(self, body):
        self.casts.pop(body, None)

    def prepare_command(self, player, command):
        instance = self.instance(player)
        phase = instance.states.get(24) if instance is not None else None
        if phase is None or not phase.active:
            return command
        aim = instance.states.get(18)
        cast = self.casts.get(player.body)
        aiming = aim is not None and aim.active and (cast is None or not cast.impacted)
        down = bool(command.buttons & 1)
        if cast is not None:
            if aiming and down and not cast.confirm_down:
                delay = max(0, command.frame * instance.component.frame_ms - instance.component.now)
                aim.request(runtime.FINISH, delay)
            cast.confirm_down = down
        # Primary confirms through the host rather than firing Hand Cannon;
        # native7DEB522D's frame predicate and early-confirm buffering are open.
        return replace(command, buttons=command.buttons & 2 if aiming else 0, action=0,
                       forward=command.forward if aiming else 0, right=command.right if aiming else 0)

    def reconcile(self, player, command):
        cast = self.casts.get(player.body)
        if cast is None or cast.finished or cast.world_landed_frame is None:
            return
        instance = self.instance(player)
        if instance is None:
            return
        command_ms = command.frame * instance.component.frame_ms
        if command.frame < cast.world_landed_frame:
            return
        delay = max(0, command_ms - instance.component.now)
        # Let the resumed VM traverse its original links without replaying
        # the selection and descent delays after a world-clock landing.
        for index in (6, 18, 16):
            phase = instance.states.get(index)
            if phase is not None and phase.active:
                phase.request(runtime.FINISH, delay)
                return
        recovery = instance.graph.node(25).field("m_timeout")
        elapsed = (command.frame - cast.world_landed_frame) * instance.component.frame_ms / 1000
        if elapsed + 1e-9 >= float(instance.evaluate(recovery)):
            for index in (86, 13):
                phase = instance.states.get(index)
                if phase is not None and phase.active:
                    phase.request(runtime.FINISH, delay)

    def _ground(self, player, instance):
        from ow174.game.collision import NOT_GROUND

        feet = player.mover.position
        distance = float(instance.graph.node(127).field('m_F5732403.m_6A79BD98.m_value'))
        ground = self.combat._collision().ground(feet[0], feet[2], feet[1] + .2,
                                                 feet[1] - distance, player.mover.category)
        if ground is None or ground.normal[1] <= player.mover.slope_cos or ground.surface & NOT_GROUND:
            return None
        return feet[0], ground.y, feet[2]

    @staticmethod
    def _warp(player, target):
        player.mover.position[:] = target
        player.mover.stop_drive()
        x, y, z = target
        player.mover._engine_body().teleport((x, y + player.mover._lift(), z))

    def command(self, player, command):
        instance = self.instance(player)
        if instance is None or command is None:
            return
        phase = instance.states.get(24)
        cast = self.casts.get(player.body)
        if phase is None or not phase.active:
            if cast is not None:
                cast.finished = True
            return
        activation = (instance.id, phase.counter, phase.start)
        if cast is None or cast.activation != activation:
            cast = Cast(activation, tuple(player.mover.position))
            cast.finished = phase.start < instance.component.now - instance.component.frame_ms
            self.casts[player.body] = cast
            player.mover.stop_drive()
            startup_end = state_deadline(instance.states.get(6))
            if startup_end is not None:
                # Node31 counts its initial selection seconds down to zero at
                # the default scalar ChaseVar rate of one unit per second.
                selection = instance.graph.node(31).field("m_EB0FC261")
                selection_time = float(instance.evaluate(selection))
                cast.impact_at = startup_end + selection_time + self._descent_time(instance)
        if cast.finished:
            return
        aiming, descending, impact = (instance.states.get(n) for n in (18, 16, 10))
        if not cast.committed:
            if aiming is not None and aiming.active:
                target = self._ground(player, instance)
                if target is not None:
                    cast.target = target
                selection_end = state_deadline(aiming)
                if selection_end is not None:
                    cast.impact_at = selection_end + self._descent_time(instance)
            if descending is not None and descending.active:
                cast.committed = True
                cast.impact_at = state_deadline(descending)
                instance.write((0, 431), expr.Vec3(*cast.target))
                player.mover.stop_drive()
        # TrackTargets' transition completes synchronously, so its fresh
        # activation counter/start, rather than its final active bit, carries
        # this frame's impact. Never replay an old unposed impact at an endpoint.
        if (impact is None or not impact.counter or impact.start < phase.start or cast.impacted):
            return
        if instance.component.now - impact.start <= instance.component.frame_ms:
            self._impact(player, instance, cast, command.frame)
        else:
            cast.impacted = True

    @staticmethod
    def _descent_time(instance):
        return float(instance.evaluate(instance.graph.node(28).field("m_timeout")))

    def update(self):
        if not self.casts:
            return
        from ow174.game.combat import TICK

        for player in self.combat.match.players:
            cast = self.casts.get(player.body)
            if cast is None or cast.finished or cast.impacted or cast.impact_at is None:
                continue
            instance = self.instance(player)
            phase = instance.states.get(24) if instance is not None else None
            if phase is None or not phase.active:
                cast.finished = True
                continue
            # Give the authored query its normal command frame. If poses stop,
            # resolve the same committed landing at the frozen selection point.
            grace = instance.component.frame_ms / 1000
            if self.combat.tick * TICK + 1e-9 >= cast.impact_at + grace:
                cast.committed = True
                cast.world_landed_frame = self.combat.tick
                self._impact(player, instance, cast, self.combat.tick)

    def _impact(self, player, instance, cast, frame):
        from ow174.game.combat import capsule_visible

        cast.impacted = True
        self._warp(player, cast.target)
        radius = float(instance.vars[6252].value())
        origin = (cast.target[0], cast.target[1] + .6, cast.target[2])
        found, distances, amounts = [], [], []
        for target in self.combat._enemies(player):
            distance = math.dist(target.feet, cast.target)
            a, b = target.segment()
            midpoint = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
            if (distance > radius
                    or not capsule_visible(self.combat._collision(), origin, a, b, target.radius)
                    or self.combat.walls.blocked(origin, midpoint)):
                continue
            instance.write((0, 680), distance)
            amount = float(instance.evaluate(instance.graph.node(6).field('m_value')))
            vitals = self.combat.vitals_of(target)
            before = vitals.total
            self.combat._damage(player, target, amount, False, LOADOUT, frame)
            dealt = before - vitals.total
            if dealt > 0:
                found.append(target.entity)
                distances.append(distance)
                amounts.append((target.entity, amount, dealt))
                self.combat.punches.grant(player, ultimate=True)
                mover = getattr(target.owner, "mover", None)
                if (mover is not None and not vitals.dead and not self.combat.control_immune(target.owner)
                        and not self.combat.captured(target.entity)):
                    instance.write((0, 9275), instance.evaluate(instance.graph.node(139).field('m_value')))
                    definition = instance.graph.node(7)
                    horizontal = float(instance.evaluate(definition.field('m_modifyHealth.m_E33EFEE1')))
                    up = float(instance.evaluate(definition.field('m_modifyHealth.m_583C208B')))
                    dx, dz = target.feet[0] - cast.target[0], target.feet[2] - cast.target[2]
                    length = math.hypot(dx, dz) or 1
                    velocity = (dx / length * horizontal, up, dz / length * horizontal)
                    mover.add_external_impulse(velocity)
        cast.hit, cast.damage = tuple(found), tuple(amounts)
        # The authored recovery spends charge when the command VM runs.
        # The world-clock landing shares the same one-shot host result.
        instance.write((0, 375), tuple(expr.Entity(e) for e in found))
        instance.write((0, 9326), tuple(distances))
