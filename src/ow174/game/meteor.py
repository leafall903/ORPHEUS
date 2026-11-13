"""Authored Meteor stages/damage with explicitly reconstructed spatial services."""

import math
from dataclasses import dataclass, replace

from ow174.game import defenses
from ow174.game.mover import f32
from ow174.game.script import expr, runtime

HERO = 0x02E000000000012F
LOADOUT = 0x0B90000000000108


def _impact_distance(origin, target):
    x, y, z = target.feet
    radius = target.radius
    mover = getattr(target.owner, 'mover', None)
    if mover is not None and mover.crouched:
        radius = mover.data.crouch_radius
    midpoint = target.height * .5
    bottom = y + min(radius, midpoint)
    top = y + max(target.height - radius, midpoint)
    closest_y = max(bottom, min(top, origin[1]))
    return max(0., math.dist(origin, (x, closest_y, z)) - radius)


def _impact_visible(collision, landing, target, blockers):
    from ow174.game.combat import EPSILON, ray_cover

    x, y, z = landing
    tx, ty, tz = target.feet
    # Resolve the authored ray and the two body-height fallbacks using the
    # host landing anchor, static world, and active Cryo. Native sockets are separate.
    rays = (
        ((x, y + .5, z), (tx, ty + .5, tz)),
        ((x, y + .6, z), (tx, ty + .3 * target.height, tz)),
        ((x, y + .6, z), (tx, ty + .6 * target.height, tz)),
    )
    for start, end in rays:
        delta = tuple(b - a for a, b in zip(start, end, strict=True))
        length = math.hypot(*delta)
        if length <= EPSILON:
            return True
        hit = ray_cover(collision, start, tuple(axis / length for axis in delta), length, blockers)
        if hit is None:
            return True
    return False


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


class Meteors:
    def __init__(self, combat):
        self.combat = combat
        self.casts: dict[int, Cast] = {}

    @staticmethod
    def instance(player):
        if player.hero.guid != HERO or player.body_script is None or player.body_script.component.strict:
            return None
        return next((i for i in player.body_script.component.instances.values()
                     if i.graph.index == 0xC51), None)

    def protected(self, entity):
        player = next((p for p in self.combat.match.players if p.has_body and p.body == entity), None)
        if player is None:
            return False
        instance = self.instance(player)
        phase = instance.states.get(24) if instance is not None else None
        cast = self.casts.get(entity)
        return (phase is not None and phase.active
                and (cast is None or (not cast.impacted and not cast.finished)))

    def prepare_command(self, player, command):
        instance = self.instance(player)
        phase = instance.states.get(24) if instance is not None else None
        if phase is None or not phase.active:
            return command
        aim = instance.states.get(18)
        aiming = aim is not None and aim.active
        cast = self.casts.get(player.body)
        down = bool(command.buttons & 1)
        if cast is not None:
            if aiming and down and not cast.confirm_down:
                delay = max(0, command.frame * instance.component.frame_ms - instance.component.now)
                # Native confirmation executes node22's SetVar/out chain before
                # the queued selection exit, including countdown215 = 0.
                instance.run_node(instance.graph.node(22), aim)
                aim.request(runtime.FINISH, delay)
            cast.confirm_down = down
        # Primary confirms through the host rather than firing Hand Cannon;
        # Native7DEB522D's frame predicate and early-confirm buffering remain open.
        return replace(command, buttons=command.buttons & 2 if aiming else 0, action=0,
                       forward=command.forward if aiming else 0, right=command.right if aiming else 0)

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
        if cast.finished:
            return
        aiming, descending, impact = (instance.states.get(n) for n in (18, 16, 10))
        if not cast.committed:
            if aiming is not None and aiming.active:
                target = self._ground(player, instance)
                if target is not None:
                    cast.target = target
            if descending is not None and descending.active:
                cast.committed = True
                instance.write((0, 431), expr.Vec3(*cast.target))
                player.mover.stop_drive()
        # TrackTargets' transition completes synchronously, so its fresh
        # activation counter/start, rather than its final active bit, carries
        # this frame's impact. Never replay an old unposed impact at an endpoint.
        if (impact is None or not impact.counter or impact.start < phase.start or cast.impacted):
            return
        cast.impacted = True
        if instance.component.now - impact.start > instance.component.frame_ms:
            return
        self._warp(player, cast.target)
        radius = float(instance.vars[6252].value())
        collision = self.combat._collision()
        cryo_cover = defenses.cryo_blockers(self.combat.match.players, exclude=player)
        x, y, z = cast.target
        # Apply the authored local +0.1 m offset to the upright landing anchor.
        origin = (x, y + .1, z)
        candidates = {}
        for target in self.combat._enemies(player):
            distance = _impact_distance(origin, target)
            if distance > radius or not _impact_visible(collision, cast.target, target, cryo_cover):
                continue
            candidates[target.entity] = (target, f32(distance))

        previous = instance.vars.get(375)
        previous = previous.value() if previous is not None else None
        ordered = []
        if isinstance(previous, tuple):
            for entity in previous:
                pair = candidates.pop(expr.to_entity(entity), None)
                if pair is not None:
                    ordered.append(pair)
        ordered.extend(candidates.values())

        # TrackTargets publishes the ID list and an independently deduplicated
        # float list. Node 5 reads null when the ID index exceeds that list.
        instance.write((0, 375), tuple(expr.Entity(target.entity) for target, _ in ordered))
        instance.write((0, 9326), tuple(dict.fromkeys(distance for _, distance in ordered)))
        metric_expr = instance.graph.node(5).field('m_value')
        amount_expr = instance.graph.node(6).field('m_value')
        found, amounts = [], []
        for index, (target, _) in enumerate(ordered):
            instance.write((0, 39), index)
            instance.write((0, 680), instance.evaluate(metric_expr))
            amount = float(instance.evaluate(amount_expr))
            instance.write((0, 476), amount)
            vitals = self.combat.vitals_of(target)
            before = vitals.total
            self.combat._damage(player, target, amount, False, LOADOUT, command.frame)
            dealt = before - vitals.total
            if dealt > 0:
                found.append(target.entity)
                amounts.append((target.entity, amount, dealt))
                self.combat._doomfist_shields(player, LOADOUT)
                if not self.combat.vitals_of(target).dead:
                    instance.write((0, 9275), instance.evaluate(instance.graph.node(139).field('m_value')))
                    definition = instance.graph.node(7)
                    horizontal = float(instance.evaluate(definition.field('m_modifyHealth.m_E33EFEE1')))
                    up = float(instance.evaluate(definition.field('m_modifyHealth.m_583C208B')))
                    dx, dz = target.feet[0] - cast.target[0], target.feet[2] - cast.target[2]
                    length = math.hypot(dx, dz) or 1
                    velocity = (dx / length * horizontal, up, dz / length * horizontal)
                    if any(velocity):
                        from ow174.game.doomfist import Slams

                        scale = Slams._target_impulse_scale(target)
                        impulse = expr.Vec3(*(axis * scale for axis in velocity))
                        self.combat.knockbacks.apply(
                            target, impulse, impact_tick=self.combat.match.tick, source_entity=player.body)
        instance.write((0, 39), len(ordered))
        cast.hit, cast.damage = tuple(found), tuple(amounts)
