"""World-clock healing for the authored 017B Transcendence lifetime."""

import math

from ow174.game import ball_mines, barriers
from ow174.game.script import expr

HERO = 0x02E0000000000020


def _window(instance):
    lifetime = instance.states.get(3)
    if lifetime is None or not lifetime.active:
        return None
    health = instance.state(6)
    duration = expr.to_float(health.evaluate(health.node.field("m_95F1FB0B"), watch=False))
    if not math.isfinite(duration) or duration <= 0:
        return None
    return lifetime.start, lifetime.start + round(duration * 1000)


def immunity_live(instance, tick):
    window = _window(instance)
    return window is not None and tick * instance.component.frame_ms < window[1]


def heal_transcendence(fight, previous_tick, *, expiring=False):
    from ow174.game.combat import TICK, ray_world

    for player in fight.match.players:
        script = player.body_script
        if (player.hero.guid != HERO or script is None or script.retired
                or not barriers._recipient_ready(fight, player)):
            continue
        instance = next((i for i in script.component.instances.values() if i.graph.index == 0x017B), None)
        if instance is None:
            continue
        search = instance.states.get(0)
        window = _window(instance)
        if window is None or search is None or not search.active:
            continue
        start_ms, end_ms = window
        if expiring != (fight.tick * script.frame_ms >= end_ms):
            continue
        since = max(previous_tick * TICK, start_ms / 1000)
        until = min(fight.tick * TICK, end_ms / 1000)
        elapsed = until - since
        if elapsed <= 0:
            continue
        health = instance.state(6)
        duration = (end_ms - start_ms) / 1000
        budget = expr.to_float(health.evaluate(health.node.field("m_modifyHealth.m_amount"), watch=False))
        radius = expr.to_float(search.evaluate(search.node.field("m_radius"), watch=False))
        origin = fight._eye(player)
        for target in fight._targets():
            if isinstance(target.owner, ball_mines.Mine):
                continue
            allied = not fight.match.game_map.free_for_all and target.team == player.team
            if target.owner is not player and not allied:
                continue
            vitals = fight.vitals_of(target)
            if vitals.dead or getattr(target.owner, "gone", False):
                continue
            point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
            if math.dist(origin, point) > radius:
                continue
            centre = (point[0], target.feet[1] + target.eye * 0.5, point[2])
            delta = tuple(b - a for a, b in zip(origin, centre, strict=True))
            distance = math.sqrt(sum(axis * axis for axis in delta))
            if distance > 0:
                direction = tuple(axis / distance for axis in delta)
                wall = ray_world(fight._collision(), origin, direction, distance)
                if ((wall is not None and wall < distance)
                        or barriers.ray_hit(fight, player, origin, direction, distance) is not None):
                    continue
            fight.heal_target(player, target, budget * elapsed / duration, interval=(since, until))
