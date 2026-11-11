"""Moira F76 spray and 10E4 Biotic Orb world services."""

import math
from dataclasses import dataclass

from ow174.game import mei, moira, phase_defenses, reinhardt_barrier, shot_guards
from ow174.game.script import expr

SPRAY = moira.GRASP, 19
ORB_GRAPH = 0x10E4
HEAL_ORB, DAMAGE_ORB = 0x0400000000001E60, 0x0400000000001E61
ORB_SLOW = .275
ORB_LOADOUT = 0x0B9000000000016D
TAIL_DELAY_MS, TAIL_DURATION_MS = 200, 2000
TAIL_SECONDS = (TAIL_DELAY_MS + TAIL_DURATION_MS) / 1000
SPRAY_TAIL_TAGS = (0x0D80000000002F9F,)
HEAL_ORB_TAGS = (0x0D80000000002DFD, 0x0D800000000031AC)
DAMAGE_ORB_TAGS = (0x0D80000000002DFD, 0x0D80000000002F99, 0x0D80000000004D29, 0x0D80000000004910)
REFLECTED_TAG = 0x0D800000000019C4


def cancel_selection(player):
    script = getattr(player, "body_script", None)
    if getattr(script, "hero", None) != moira.HERO or script.component.owner_dead:
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == ORB_GRAPH), None)
    if instance is not None:
        ability = instance.state(0)
        if ability.flags & ability.RUNNING:
            ability.stop()
            instance.component.run_now()


@dataclass
class Tail:
    shooter: object
    body: int
    team: int
    start: int
    end: int
    last: int


@dataclass
class Orb:
    shooter: object
    body: int
    launch_body: int
    team: int
    position: tuple
    direction: tuple
    speed: float
    left: float
    energy: float
    healing: bool
    absorption: float
    scale: float
    shot: object
    tick: int
    flown: float = 0.
    bounces: int = 0
    returns: int = 0


class Biotics:
    def __init__(self, fight):
        self.fight = fight
        self.tails = {}
        self.orbs = []

    def heal(self, shooter, body, target, amount, tags, *, budget=None):
        from ow174.game import hero_charge

        given = self.fight.heal_target(target, amount, healer=shooter, source_body=body, maximum=budget)
        script = getattr(shooter, "body_script", None)
        if (shooter is not target.owner and shooter.body == body and not shooter.gone
                and getattr(script, "hero", None) == moira.HERO):
            hero_charge.healing(script, target.entity, given, tags)
        return given

    def fly_spray(self, rocket, duration):
        from ow174.game.combat import EPSILON, _along, _sub, ray_capsule, ray_world

        fight = self.fight
        origin, direction = rocket.position, rocket.direction
        reach = rocket.speed * duration
        geometry = fight._line_collision()
        wall = ray_world(geometry, origin, direction, reach)
        limit = reach if wall is None else wall
        barrier = reinhardt_barrier.ray_hit(fight, rocket.shooter, origin, direction, limit, team=rocket.team)
        if barrier is not None:
            limit = barrier[0]
        if not fight.match.game_map.free_for_all:
            contacts = []
            for target in fight._targets():
                if (target.owner is rocket.shooter or target.team != rocket.team or target.is_device
                        or target.entity in rocket.spray_hits or fight.vitals_of(target).dead):
                    continue
                a, b = target.segment()
                hit = ray_capsule(origin, direction, a, b, target.radius + rocket.spray_width)
                if hit is None or hit > limit or (barrier is not None and hit >= limit):
                    continue
                point = _along(origin, direction, hit)
                centre = a[0], max(a[1], min(point[1], b[1])), a[2]
                delta = _sub(centre, point)
                distance = math.hypot(*delta)
                if distance > target.radius:
                    heading = tuple(axis / distance for axis in delta)
                    surface = distance - target.radius
                    obstruction = ray_world(geometry, point, heading, surface)
                    if obstruction is not None and obstruction + EPSILON < surface:
                        continue
                    if reinhardt_barrier.ray_hit(
                        fight, rocket.shooter, point, heading, surface, team=rocket.team,
                    ) is not None:
                        continue
                contacts.append((hit, target.entity, target))
            direct = rocket.direct
            for _, entity, target in sorted(contacts):
                rocket.spray_hits += (entity,)
                self.heal(rocket.shooter, rocket.source_body, target, direct.amount, direct.tags)
                starts = fight.tick * mei.FRAME_MS + TAIL_DELAY_MS
                self.tails[entity] = Tail(rocket.shooter, rocket.source_body, rocket.team,
                                          starts, starts + TAIL_DURATION_MS, starts)
        rocket.position = _along(origin, direction, limit)
        rocket.flown += limit / rocket.speed
        rocket.left -= duration
        if wall is not None or barrier is not None or rocket.left <= EPSILON:
            fight._landed(rocket)

    def launch(self, player, state, definition, speed, lifetime, origin, direction, direct, shot):
        if (state.instance.graph.index != ORB_GRAPH or definition not in (HEAL_ORB, DAMAGE_ORB)
                or player.hero.guid != moira.HERO):
            return False
        healing = definition == HEAL_ORB
        key, base = (12618, 300.) if healing else (12619, 200.)
        component = state.instance.component
        slot = component.entity_var(key, create=False)
        scalar = slot.value() if slot is not None else None
        if scalar is None:
            scalar = component.world.ruleset(key)
        energy = expr.f32(base * expr.to_float(scalar))
        if not math.isfinite(energy):
            energy = 0.
        self.orbs.append(Orb(
            player, player.body, player.body, player.team, origin, direction, speed, lifetime,
            max(0., energy), healing, max(0., direct.amount) if direct else 0.,
            self.fight.outgoing_scale(player.body), shot, self.fight.tick,
        ))
        return True

    def replay_until(self, previous, seconds):
        return max(
            max((math.ceil(tail.end / mei.FRAME_MS) for tail in self.tails.values()), default=previous),
            max((orb.tick + math.ceil(orb.left / seconds) for orb in self.orbs), default=previous),
        )

    def finish(self, orb):
        self.orbs.remove(orb)
        if orb.shot is not None:
            self.fight.projectiles.flown(orb.shot, orb.flown)

    def move(self, orb, elapsed, *, slow):
        from ow174.game.combat import EPSILON, _along, _dot

        fight = self.fight
        speed = orb.speed * (ORB_SLOW if slow else 1)
        remaining = speed * elapsed
        geometry = fight._line_collision()
        for _ in range(8):
            if remaining <= EPSILON:
                break
            hit = geometry.cast_ray(orb.position, orb.direction, remaining)
            limit = remaining if hit is None else hit.distance
            guard = fight._projectile_guard(orb, limit, orb.returns < shot_guards.MAX_RETURNS)
            if guard is not None:
                distance, state, target, heading = guard
                orb.position = _along(orb.position, orb.direction, distance)
                orb.flown += distance / speed
                hero = shot_guards.guard_hero(target.owner)
                if hero != shot_guards.GENJI:
                    if hero == shot_guards.SIGMA:
                        shot_guards.absorb(state, orb.absorption, orb.body)
                    self.finish(orb)
                    return False
                if orb.shot is not None:
                    fight.projectiles.flown(orb.shot, orb.flown)
                    orb.shot = None
                orb.shooter, orb.body, orb.team = target.owner, target.entity, target.team
                orb.scale = fight.outgoing_scale(target.entity)
                orb.direction = heading
                orb.returns += 1
                limit = distance
            else:
                orb.position = _along(orb.position, orb.direction, limit)
                orb.flown += limit / speed
                if hit is None:
                    return True
                projection = _dot(orb.direction, hit.normal)
                reflected = tuple(axis - 2 * projection * normal
                                  for axis, normal in zip(orb.direction, hit.normal, strict=True))
                length = math.hypot(*reflected)
                if length <= EPSILON:
                    self.finish(orb)
                    return False
                orb.direction = tuple(axis / length for axis in reflected)
                orb.bounces += 1
            remaining -= limit
            separation = min(remaining, .0001)
            orb.position = _along(orb.position, orb.direction, separation)
            orb.flown += separation / speed
            remaining -= separation
        return True

    def targets(self, orb):
        from ow174.game.combat import capsule_visible, segment_distance

        fight = self.fight
        radius = 5. if orb.healing else 4.
        geometry = fight._line_collision()
        for target in fight._targets():
            if target.is_device:
                continue
            vitals = fight.vitals_of(target)
            if vitals.dead:
                continue
            own = target.owner is orb.shooter
            friendly = own or (not fight.match.game_map.free_for_all and target.team == orb.team)
            if orb.healing != friendly:
                continue
            if orb.healing:
                if vitals.missing_permanent <= 0 or vitals.healing_block_until > fight.tick:
                    continue
            elif mei.protected(target.owner) or phase_defenses.active(target.owner):
                continue
            if segment_distance(orb.position, *target.segment()) - target.radius > radius:
                continue
            x, y, z = target.feet
            point = x, y + target.height / 2, z
            if capsule_visible(geometry, orb.position, point, point, 0):
                yield target

    def pulse(self, orb, elapsed):
        fight = self.fight
        for target in self.targets(orb):
            if orb.energy <= 1e-7:
                break
            if orb.healing:
                spent = self.heal(
                    orb.shooter, orb.body, target, 65. * elapsed, HEAL_ORB_TAGS, budget=orb.energy,
                )
            else:
                amount = 50. * elapsed * orb.scale
                bubble = reinhardt_barrier.body_bubble(fight, target.owner)
                if bubble is not None:
                    before = bubble.vitals.total
                    reinhardt_barrier.damage(bubble, min(orb.energy, amount), attacker=orb.body)
                    spent = before - bubble.vitals.total
                else:
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    tags = DAMAGE_ORB_TAGS
                    if orb.body != orb.launch_body:
                        tags = (*tags, REFLECTED_TAG)
                    fight._damage(orb.shooter, target, amount, False, ORB_LOADOUT, fight.tick,
                                  world_tick=fight.tick, tags=tags, source_body=orb.body, source_scaled=True,
                                  maximum=orb.energy)
                    spent = before - vitals.total
            orb.energy = max(0., orb.energy - spent)

    def update(self, tick, seconds):
        now = tick * mei.FRAME_MS
        if self.tails:
            targets = {target.entity: target for target in self.fight._targets()}
            for entity, tail in tuple(self.tails.items()):
                target = targets.get(entity)
                if (target is None or self.fight.match.game_map.free_for_all or target.team != tail.team
                        or self.fight.vitals_of(target).dead):
                    del self.tails[entity]
                    continue
                end = min(now, tail.end)
                if end > tail.last:
                    amount = (end - tail.last) * 35. / TAIL_DURATION_MS
                    self.heal(tail.shooter, tail.body, target, amount, SPRAY_TAIL_TAGS)
                    tail.last = end
                if now >= tail.end:
                    del self.tails[entity]
        for orb in tuple(self.orbs):
            elapsed = min(orb.left, (tick - orb.tick) * seconds)
            if elapsed <= 0:
                continue
            orb.tick = tick
            if orb.energy > 1e-7:
                if not self.move(orb, elapsed, slow=next(self.targets(orb), None) is not None):
                    continue
                self.pulse(orb, elapsed)
            orb.left -= elapsed
            if orb.energy <= 1e-7 or orb.left <= 1e-7:
                self.finish(orb)
