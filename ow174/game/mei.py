"""Mei's authored spray frost and 05FE Cryo control on the match clock."""

import math
from dataclasses import dataclass

from ow174.game import bots, fortify, phase_defenses, reinhardt_barrier
from ow174.game.mover import MULTIPLY, SPEED
from ow174.game.script import expr

HERO = 0x02E00000000000DD
CRYO = 0x05FE
SPRAY = 0x05E6, 35
ICICLE = 0x05E6, 40
SPRAY_LOADOUT = 0x0B900000000000D3
FROST_STEP, FROST_INITIAL = expr.f32(0.0285), expr.f32(0.2)  # 0B77/31, 05E7/28
FROST_GAP_MS, FREEZE_MS = 1000, 1300
FRAME_MS = 16
SLOW_MOD = (-2, 0, SPEED)


def enter(state):
    if (not state.instance.component.strict and state.instance.graph.index == CRYO
            and state.node.pos == 64 and state.node.cls == "STU_54FEB21C"):
        state.request_finish()


def cryo_instance(actor):
    body = getattr(actor, "body_script", None)
    if getattr(body, "hero", None) != HERO or body.component.owner_dead:
        return None
    return next((i for i in body.component.instances.values() if i.graph.index == CRYO), None)


def protected(actor):
    instance = cryo_instance(actor)
    clock = instance.states.get(6) if instance is not None else None
    return clock is not None and clock.active and not clock.ending


def frost_view(sample, now_ms):
    if sample is None:
        return None
    _, touched, frozen_until, _, _ = sample
    return sample if now_ms < (frozen_until or touched + FROST_GAP_MS) else None


def frost_hit(sample, now_ms, yaw, pitch, *, initial=FROST_INITIAL, step=FROST_STEP, freeze_ms=FREEZE_MS):
    sample = frost_view(sample, now_ms)
    if sample is not None and sample[2]:
        return sample
    progress = min(1.0, max(initial, expr.f32((sample[0] if sample is not None else 0) + step)))
    return progress, now_ms, now_ms + freeze_ms if progress >= 1 else 0, yaw, pitch


def command_edges(body, edges):
    if body.hero != HERO or body.component.owner_dead:
        return edges
    held = set(body.component.held)
    for button, pressed in edges:
        held.add(button) if pressed else held.discard(button)
    if any(pressed and button in (14, 35) for button, pressed in edges):
        return edges
    frost = body.component.entity_var(2427, create=False)
    if frost is not None and expr.to_float(frost.value()) >= 1:
        return edges
    for instance in body.component.instances.values():
        if instance.graph.index == CRYO:
            duration = instance.states.get(6)
            if duration is not None and duration.active:
                return edges
    weapon = next((i for i in body.component.instances.values() if i.graph.index == SPRAY[0]), None)
    if weapon is None:
        return edges
    windup = weapon.states.get(31)
    if windup is not None and windup.active:
        return edges
    ammo = expr.to_int(weapon.find_var(expr.INSTANCE, 53).value())
    needed = 10 if 29 in held and 20 not in held else 1
    reloading = weapon.states.get(7)
    if ammo < needed and (reloading is None or not reloading.active):
        return [*edges, (14, True), (14, False)]
    return edges


@dataclass
class CryoCycle:
    owner: object
    clock: object
    activation: int
    deadline: int
    start: int
    duration: float
    amount: float
    tags: tuple[int, ...]
    accounted: float = 0.0


class MeiCombat:
    def __init__(self, fight):
        self.fight = fight
        self.hits = {}
        self.sources = {}
        self.cryos = {}

    def frost(self, entity, tick=None):
        return frost_view(self.hits.get(entity), (self.fight.tick if tick is None else tick) * FRAME_MS)

    def freeze_deadline(self, entity):
        sample = self.hits.get(entity)
        return math.ceil(sample[2] / FRAME_MS) if sample is not None else 0

    def movement_mods(self, entity, mods, tick):
        sample = self.frost(entity, tick)
        if sample is not None:
            mods[SLOW_MOD] = (SPEED, MULTIPLY, max(0.3, 1.0 - sample[0]), 99.0)
        return mods

    def immune(self, target):
        actor = target.owner
        return (target.is_device or protected(actor) or phase_defenses.active(actor)
                or fortify.active(self.fight, actor)
                or reinhardt_barrier.body_bubble(self.fight, actor) is not None)

    def add_frost(self, target, source, source_entity, *, channel=0, now_ms=None,
                  initial=FROST_INITIAL, step=FROST_STEP):
        from ow174.game import mei_frost

        if self.immune(target):
            return
        actor = target.owner
        yaw, pitch = ((round(actor.yaw), 0) if isinstance(actor, bots.Bot)
                      else (actor.mover.yaw, actor.mover.pitch))
        at = self.fight.tick * FRAME_MS if now_ms is None else now_ms
        entity = target.entity
        old = self.hits.get(entity)
        if old is not None:
            at = max(at, old[1])
        if old is not None and old[2]:
            if at < old[2]:
                return
            self.sources.pop(entity, None)
        entries = self.sources.setdefault(entity, {})
        mei_frost.prune(entries, at)
        minimum, scale, duration, stacking = mei_frost.settings(source, weapon=channel == 0)
        key = source_entity, channel
        previous = entries.get(key, (None, False))[0]
        entries[key] = (frost_hit(previous, at, yaw, pitch,
                                  initial=initial if minimum is None else minimum,
                                  step=expr.f32(step * scale), freeze_ms=duration), stacking)
        sample = mei_frost.combine(entries, at, yaw, pitch, freeze_ms=duration)
        self.write_frost(target, sample)
        if sample[2]:
            self.fight._interrupt(target)

    def write_frost(self, target, sample):
        self.hits[target.entity] = sample
        script = getattr(target.owner, "body_script", None)
        if script is not None:
            script.component.set(2427, sample[0])

    def clear_frost(self, entity, owner=None):
        self.sources.pop(entity, None)
        if self.hits.pop(entity, None) is None:
            return
        script = getattr(owner, "body_script", None)
        if script is not None:
            script.component.set(2427, 0.0)

    def retire(self, entity):
        self.clear_frost(entity)
        self.cryos.pop(entity, None)

    def observe(self, player):
        if player.hero.guid != HERO:
            return
        instance = cryo_instance(player)
        if not protected(player):
            self.cryos.pop(player.body, None)
            return
        self.clear_frost(player.body, player)
        clock = instance.states[6]
        existing = self.cryos.get(player.body)
        if existing is not None and existing.clock is clock and existing.activation == clock.counter:
            return
        healing = instance.states.get(13)
        duration = expr.to_float(instance.find_var(expr.INSTANCE, 567).value()) * 1000
        deadline = clock.expiry_frame()
        if healing is not None and healing.active and deadline is not None and duration > 0:
            from ow174.game.combat import damage_tags

            cfg = healing.node.field("m_modifyHealth")
            amount = expr.to_float(healing.evaluate(cfg["m_amount"], watch=False))
            self.cryos[player.body] = CryoCycle(
                player, clock, clock.counter, deadline, healing.start, duration, amount,
                damage_tags(healing, cfg),
            )

    def replay_until(self, previous):
        return max((cycle.deadline for cycle in self.cryos.values()), default=previous)

    def update(self, tick):
        from ow174.game import mei_frost

        if not self.cryos and not self.hits:
            return
        targets = {target.entity: target for target in self.fight._targets()}
        for entity, cycle in tuple(self.cryos.items()):
            self.heal(entity, cycle, targets.get(entity), tick)
        at = tick * FRAME_MS
        for entity, sample in tuple(self.hits.items()):
            target = targets.get(entity)
            if (target is None or self.fight.vitals_of(target).dead or self.immune(target)
                    or frost_view(sample, at) is None):
                self.clear_frost(entity, target.owner if target is not None else None)
            elif not sample[2]:
                entries = self.sources[entity]
                mei_frost.prune(entries, at)
                remaining = mei_frost.combine(entries, at, *sample[3:])
                if remaining is None:
                    self.clear_frost(entity, target.owner)
                elif remaining[0] != sample[0]:
                    self.write_frost(target, remaining)

    def heal(self, entity, cycle, target, tick):
        clock = cycle.clock
        if (target is None or not clock.active or clock.counter != cycle.activation
                or self.fight.vitals_of(target).dead):
            self.cryos.pop(entity, None)
            return
        component = clock.instance.component
        frame_end = (tick + 1) * component.frame_ms - 1
        elapsed = cycle.duration if tick >= cycle.deadline else max(0, frame_end - cycle.start)
        due = cycle.amount * min(1, elapsed / cycle.duration)
        from ow174.game import hero_charge

        given = self.fight.heal_target(target, due - cycle.accounted, healer=cycle.owner)
        hero_charge.healing(cycle.owner.body_script, target.entity, given, cycle.tags)
        cycle.accounted = due
        if tick < cycle.deadline:
            return
        self.cryos.pop(entity, None)
        saved = component.now
        with component.operation():
            try:
                component.now = max(saved, frame_end)
                clock.cur = clock.destination()
                clock.reached = True
                clock.end(True)
                clock.instance.update()
            finally:
                component.now = saved
