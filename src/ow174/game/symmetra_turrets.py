"""Authored sentry launch/health/beam/slow inputs with a reconstructed world host.

This owns forward flight, attachment, acquisition and effect lifetimes. Client
Pet/projectile reconciliation and surface-oriented hit geometry remain separate
acceptance items. External graph/entity shards supply the missing actor inputs.
"""

import math
from copy import copy
from dataclasses import dataclass, replace

from ow174.game import bots, deflection, fortify, pools
from ow174.game.mover import MoverData
from ow174.game.script import expr, graph, runtime
from ow174.game.symmetra import HERO

BODY = 0x0400000000002658
HELIX_PROJECTILE = 0x040000000000072C
PHARAH_HERO = 0x02E0000000000008
LAUNCH_GRAPH, ATTACH_GRAPH, BEAM_GRAPH, SLOW_GRAPH = 0x157E, 0x157F, 0x1590, 0x01E7


@dataclass
class Sentry:
    owner: object
    bot: object
    direction: tuple
    speed: float
    last_tick: int
    attached_at: float | None = None
    target: int | None = None
    slow: object = None  # (target component, actual aura graph instance)
    support_pillar: int | None = None


class Turrets:
    def __init__(self, combat):
        self.combat = combat
        self.sentries: list[Sentry] = []

    def fired(self, player, command, state) -> bool:
        if (getattr(player.hero, "guid", None) != HERO
                or state.instance.graph.index != LAUNCH_GRAPH or state.index != 12):
            return False
        from ow174.game import npc
        from ow174.game.combat import aim

        record = graph.entity_body(BODY)
        if record is None or graph.graph(BEAM_GRAPH) is None or graph.graph(SLOW_GRAPH) is None:
            raise ValueError("sentry deployment needs its external actor and aura graph definitions")
        health = next(row for row in record["graphs"] if row["graph"] == pools.BODY_GRAPH)
        values = pools._numbers(graph.graph(pools.BODY_GRAPH).fields["m_publicSchema"]["m_entries"])
        values.update(pools._numbers(health.get("m_1EB5A024")))
        definition = npc.Definition(HERO, BODY, "Sentry Turret", values[pools.HEALTH_VAR],
                                    values.get(pools.ARMOUR_VAR, 0), values.get(pools.SHIELDS_VAR, 0))
        movement = MoverData(stand_height=1.0, stand_radius=.2,
                             crouch_height=1.0, crouch_radius=.2,
                             run_forward=0, run_backward=0, run_strafe=0)
        bot = npc.spawn(self.combat.match, definition, self.combat._eye(player), team=player.team)
        bot.spawner.update(manual_spawn=False, summoned=True)
        bot.combat_owner = player
        bot.combat_geometry = (movement.stand_height, movement.stand_radius, .75, False)
        speed = expr.to_float(state.evaluate(state.node.field("m_projectileSpeed"), watch=False))
        sentry = Sentry(player, bot, aim(*command.aim()), speed, self.combat.tick)
        self.sentries.append(sentry)
        # Reconstructed ownership limit includes in-flight sentries, so a missed
        # infinite-lifetime launch cannot leave an unbounded actor collection.
        maximum = expr.to_int(state.instance.find_var(graph.INSTANCE, 181).value())
        owned = [item for item in self.sentries if item.owner is player]
        for old in owned[:-max(1, maximum)]:
            self._retire(old)
        return True

    def _clear_slow(self, sentry):
        if sentry.slow is not None:
            component, instance = sentry.slow
            if component.instances.get(instance.id) is instance:
                component.destroy(instance)
                component.run_now()
            sentry.slow = None
        sentry.target = None

    def release_target(self, entity):
        for sentry in self.sentries:
            if sentry.target == entity:
                self._clear_slow(sentry)

    def _retire(self, sentry):
        from ow174.game import npc

        self._clear_slow(sentry)
        npc.remove_body(self.combat.match, sentry.bot)
        self.sentries.remove(sentry)

    def retire_owner(self, owner):
        for sentry in tuple(self.sentries):
            if sentry.owner is owner:
                self._retire(sentry)

    def retire_attached_to(self, pillar):
        for sentry in tuple(self.sentries):
            if sentry.support_pillar == pillar:
                self._retire(sentry)

    def _target(self, sentry, origin, reach):
        from ow174.game import actors, cryo, ice_wall
        from ow174.game.combat import segment_distance

        best = None
        for target in _unit_targets(self.combat, sentry.owner):
            # The delivered acquisition searches units, not the separate solid
            # Ice Wall object that may also back this same sentry's support.
            if (isinstance(target.owner, actors.Deployable)
                    and target.owner.definition == ice_wall.PILLAR):
                continue
            # Original0016 acquisition excludes Cryo itself as well as using
            # its solid capsule to occlude a body behind it.
            if cryo.active(target.owner):
                continue
            a, b = target.segment()
            distance = max(0.0, segment_distance(origin, a, b) - target.radius)
            if (distance <= reach and (best is None or distance < best[0])
                    and _visible(self.combat, sentry.bot, origin, target)
                    ):
                best = distance, target
        return None if best is None else best[1]

    def _advance_flight(self, sentry, elapsed):
        from ow174.game.combat import TICK, _along

        bot = sentry.bot
        if bot.moved_tick is None:
            elapsed = 1  # newly admitted player input joins at the endpoint
        distance = sentry.speed * elapsed * TICK
        hit = self.combat._collision().cast_ray(bot.position, sentry.direction, distance)
        ice = self.combat.walls.cast(bot.position, sentry.direction, distance)
        contact = None
        if ice is not None and (hit is None or ice[0] < hit.distance):
            hit_distance, pillar, normal = ice
            point = _along(bot.position, sentry.direction, hit_distance)
            contact = hit_distance, point, normal
            sentry.support_pillar = pillar.entity
        elif hit is not None:
            contact = hit.distance, hit.point, hit.normal
        if contact is None:
            bot.position = _along(bot.position, sentry.direction, distance)
            bot.velocity = tuple(v * sentry.speed for v in sentry.direction)
        else:
            hit_distance, point, normal = contact
            # Small normal offset avoids casting future beams from inside the wall.
            dot = sum(a * b for a, b in zip(normal, sentry.direction, strict=True))
            facing = -1 if dot > 0 else 1
            bot.position = tuple(p + facing * n * .01 for p, n in zip(point, normal, strict=True))
            bot.velocity = (0, 0, 0)
            sentry.attached_at = self.combat.now - elapsed * TICK + hit_distance / sentry.speed
            bot.body_script.component.set(478, True)  # reconstructed deployed flag
        bot.moved_tick = self.combat.tick

    def frozen_history_candidates(self, end_tick, *, support_impacts=True):
        if self.combat.match.game_map.free_for_all or not self.sentries:
            return ()
        first = self.sentries[0]
        targets = _unit_targets(self.combat, first.owner)
        target = next((candidate for candidate in targets if candidate.entity == first.target), None)
        if target is None or not self._held_frozen_pose(target, first.last_tick):
            return ()
        # Replay the whole group only when every beam starts from the
        # same proven lock. Keep deployment and unrelated locks on the
        # ordinary retained window.
        if any(
            sentry.owner.team != first.owner.team or sentry.owner.gone or sentry.owner.hero.guid != HERO
            or sentry.bot.dead or sentry.attached_at is None
            or sentry.target != target.entity or sentry.last_tick != first.last_tick
            for sentry in self.sentries
        ):
            return ()
        others = [candidate for candidate in targets if candidate is not target]
        ice = self.combat.walls.pieces
        support = None
        if ice:
            # A recorded lock on an ice-supported sentry proves that its
            # wall already existed. Exclude other deployments in the gap.
            support = next((ice[sentry.support_pillar] for sentry in self.sentries
                            if sentry.support_pillar in ice), None)
            if support is None or any(
                (piece.owner, piece.expires) != (support.owner, support.expires) for piece in ice.values()
            ):
                return ()
        hostile_support = support is not None and support.team != first.owner.team
        if _projected_barriers(self.combat) and not hostile_support:
            return ()
        if hostile_support or (others and self.combat.rockets):
            if (not support_impacts
                    or not self._ice_support_rocket(support, target, first.last_tick, end_tick)):
                return ()
        else:
            if others and not self._out_of_range_history(others, first.last_tick):
                return ()
            # A piercing allied shot that crossed the only enemy cannot
            # change the target or interrupt the beam during this gap.
            if any(
                rocket.team != first.owner.team or rocket.blast is not None or (rocket.source_graph == 0x007B)
                or target.entity not in rocket.hit_entities
                for rocket in self.combat.rockets
            ):
                return ()
        return tuple(sentry.bot.entity for sentry in self.sentries)

    def _ice_support_rocket(self, support, frozen, previous_tick, end_tick):
        from ow174.game import heavy_charge, immunity
        from ow174.game.combat import TICK, _along, ray_capsule, respawn_seconds

        fight = self.combat
        if support is None or len(fight.rockets) != 1:
            return False
        [rocket] = fight.rockets
        if (rocket.last_tick != previous_tick
                or rocket.blast is None or rocket.motion or (rocket.source_graph == 0x007B)
                or rocket.shooter not in fight.match.players or rocket.team != rocket.shooter.team):
            return False
        # The authored Pharah launch marker survives Deflect.
        pharah_primary = rocket.source_graph == 0x0076
        shooter_hero = rocket.shooter.hero.guid
        reflected_pharah = (
            pharah_primary and rocket.reflected and shooter_hero == deflection.GENJI
            and rocket.team == self.sentries[0].owner.team
        )
        if rocket.definition != HELIX_PROJECTILE and not (
            reflected_pharah or (pharah_primary and shooter_hero == PHARAH_HERO)
        ):
            return False
        projected = None
        if _projected_barriers(fight):
            projected = _single_replay_projection(fight)
            if (projected is None or rocket.definition != HELIX_PROJECTILE
                    or rocket.team != self.sentries[0].owner.team):
                return False
        projected_player = projected is not None and not isinstance(projected, bots.Bot)
        # Resolve the known flight with stable poses until contact. The original
        # route proof assumes no owned physical mover; never mutate one through
        # a shallow preview copy, including an already settled manual mover.
        if any(bot.impulse_mover is not None or bot.control_mover is not None for bot in fight.match.bots):
            return False
        if any(
            bot is not frozen.owner and heavy_charge.active(bot) is not None
            for bot in fight.match.bots
        ):
            return False
        for target in fight._targets():
            if not self._recorded_pose(target, previous_tick):
                return False
            if not isinstance(target.owner, bots.Bot) and (
                immunity.phased_out(target.owner)
                or (_projectile_cover(target.owner)
                    and not (reflected_pharah and target.owner is rocket.shooter))
            ):
                return False
        hit = fight.walls.cast(rocket.position, rocket.direction, rocket.speed * rocket.left)
        if hit is None or hit[1] is not support:
            return False
        distance = hit[0]
        thaw = math.ceil(fight.frost(frozen.owner, tick=previous_tick)[2] / round(TICK * 1000))
        impact_tick = previous_tick + math.ceil(distance / (rocket.speed * TICK))
        if impact_tick >= support.expires:
            return False
        moving_frozen = impact_tick >= thaw and not self._route_holds_until(frozen, thaw, impact_tick)
        if moving_frozen and not projected_player:
            return False
        if fight.cover_ray(rocket.position, rocket.direction, distance) != distance:
            return False
        barrier = fight.trace_barrier(rocket.shooter, rocket.position, rocket.direction, distance)
        if barrier is None or barrier[1] is not support:
            return False
        if _impact_damage(rocket) + rocket.blast.amount * rocket.blast.splash(0) < support.health:
            return False
        fixed_body = None
        for target in _unit_targets(fight, rocket.shooter):
            contact = ray_capsule(rocket.position, rocket.direction, *target.segment(), target.radius)
            if contact is not None and contact <= distance:
                if not projected_player or target.owner is not projected or contact == distance:
                    return False
                fixed_body, distance = target, contact
                impact_tick = previous_tick + math.ceil(distance / (rocket.speed * TICK))
        contact = self._support_rocket_contact(
            frozen, rocket, distance, previous_tick, impact_tick, fixed_body, moving_frozen=moving_frozen,
        )
        if contact is None:
            return not projected_player and self._support_retarget_isolated(rocket, distance, previous_tick)
        impact_tick, distance, body, impact_targets = contact
        late_projected = projected_player and impact_tick >= thaw
        if projected_player and len(self.sentries) != 1:
            return False
        # The moving original target can receive splash at its previewed pose.
        if late_projected and body is not None and body.entity == frozen.entity:
            return False
        impact = _along(rocket.position, rocket.direction, distance)
        visible_from = impact if body is not None else _along(impact, rocket.direction, -.0001)
        respawn_tick = impact_tick + math.ceil(respawn_seconds(fight.match) / TICK)
        splash_targets = tuple(fight._splash_targets(rocket, impact, visible_from, targets=impact_targets))
        frozen_in_splash = any(target.entity == frozen.entity for target, _ in splash_targets)
        if late_projected and body is None:
            recipient = next((target for target, _ in splash_targets if target.owner is projected), None)
            if recipient is None or not frozen.owner.retain_corpse:
                return False
            cover = fight._splash_barriers(rocket, impact, visible_from, (support, fight.walls))
            if any(service is not fight.walls for _, service, _ in cover):
                return False
            # The hit destroys the support before that tick's beam update.
            if not self._isolated_player_lock_history(
                frozen, recipient, previous_tick, impact_tick, min(end_tick, impact_tick - 1),
                allow_later_lock=False, allow_recorded_players=True,
            ) and not self._isolated_wall_retarget(frozen, previous_tick, end_tick):
                return False
        if body is not None:
            if isinstance(body.owner, bots.Bot) and not body.owner.retain_corpse:
                return False
            splash_cover = tuple(fight._splash_barriers(rocket, impact, visible_from, None))
            if late_projected and (splash_cover or not frozen_in_splash):
                return False
            if any(service is not fight.walls for _, service, _ in splash_cover):
                return False
            if projected_player and not any(
                shield is support and amount >= support.health for shield, _, amount in splash_cover
            ):
                players = [
                    target for target in _unit_targets(fight, self.sentries[0].owner)
                    if not isinstance(target.owner, bots.Bot)
                ]
                tracked_player_lock = not self._out_of_range_history(players, previous_tick)
                if late_projected and not tracked_player_lock:
                    return False
                if tracked_player_lock:
                    last_tick = min(end_tick, support.expires - 1)
                    recipient = next((target for target in players if target.owner is projected), None)
                    npc_interceptor = body if body.owner is not projected else None
                    if frozen_in_splash:
                        if (
                            (npc_interceptor is None and not late_projected)
                            or not frozen.owner.retain_corpse or respawn_tick <= end_tick
                        ):
                            return False
                        # Splash can remove the recorded lock before the beam update.
                        # The replay resolves the surviving poses after contact.
                        last_tick = min(last_tick, impact_tick)
                    if splash_cover or recipient is None or not self._isolated_player_lock_history(
                        frozen, recipient, previous_tick, impact_tick, last_tick,
                        allow_later_lock=respawn_tick > end_tick,
                        npc_interceptor=npc_interceptor,
                    ):
                        return False
        # A death can resolve here if the NPC is retained or the player's
        # replacement belongs to a later world update.
        projected_in_splash = False
        for target, amount in splash_targets:
            projected_in_splash |= target.owner is projected
            if isinstance(target.owner, bots.Bot):
                if target.entity != frozen.entity and not target.owner.retain_corpse:
                    return False
            elif respawn_tick <= end_tick and fight.vitals_of(target).total <= amount:
                return False
        # Cover on the recorded lock still affects beam damage outside the blast.
        return projected is None or projected is frozen.owner or projected_in_splash

    def _isolated_player_lock_history(
        self, frozen, recipient, previous_tick, impact_tick, last_tick, *,
        allow_later_lock, npc_interceptor=None, allow_recorded_players=False,
    ):
        from ow174.game.combat import HEALTH_POOL, TICK, segment_distance

        fight = self.combat
        [sentry] = self.sentries
        tracked = {frozen.entity, recipient.entity}
        if npc_interceptor is not None:
            tracked.add(npc_interceptor.entity)
        others = [target for target in _unit_targets(fight, sentry.owner) if target.entity not in tracked]
        if sentry.slow is None:
            return False
        if allow_recorded_players:
            if any(
                isinstance(target.owner, bots.Bot) or not self._recorded_pose(target, previous_tick)
                for target in others
            ):
                return False
            players = (recipient, *others)
        else:
            if not self._out_of_range_history(others, previous_tick):
                return False
            players = (recipient,)
        beam = self._beam(sentry)
        reach = expr.to_float(beam.find_var(graph.INSTANCE, 1648).value())
        modify = beam.graph.state(1).field("m_modifyHealth")
        rate = expr.to_float(beam.evaluate(modify["m_amount"]))
        health = fight.vitals_of(frozen).now[HEALTH_POOL]
        # A death after impact can leave a corpse without introducing a replacement.
        survives_impact = health > rate * (impact_tick - previous_tick) * TICK
        can_retain_death = allow_later_lock and frozen.owner.retain_corpse and survives_impact
        if health <= rate * (last_tick - previous_tick) * TICK and not can_retain_death:
            return False
        origin = sentry.bot.position
        player_distance = min(
            max(0.0, segment_distance(origin, *player.segment()) - player.radius) for player in players
        )
        preview = copy(frozen.owner)
        npc_preview = copy(npc_interceptor.owner) if npc_interceptor is not None else None
        for tick in range(previous_tick + 1, last_tick + 1):
            if not _step_route_preview(fight, preview, tick):
                return False
            target = replace(frozen, feet=preview.position)
            distance = max(0.0, segment_distance(origin, *target.segment()) - target.radius)
            if (distance >= player_distance or distance > reach or not fight.visible_target(origin, target)
                    or _barrier_occludes(fight, sentry.bot, origin, target)):
                # After thaw the recorded player can take the lock if a respawn
                # cannot enter the replay window.
                return allow_later_lock and tick > impact_tick and not fight.is_stunned(preview, tick=tick)
            if npc_preview is not None:
                # A lethal hit can only remove this competing capsule.
                if not _step_route_preview(fight, npc_preview, tick):
                    return False
                segment = npc_interceptor.segment(npc_preview.position)
                npc_distance = max(0.0, segment_distance(origin, *segment) - npc_interceptor.radius)
                if npc_distance <= distance:
                    return False
        return True

    def _isolated_wall_retarget(self, frozen, previous_tick, end_tick):
        from ow174.game.combat import TICK, respawn_seconds

        fight = self.combat
        [sentry] = self.sentries
        # Replaying the actual lock change also replays loss of the route slow.
        # Admit it only when that route is the only moving enemy geometry
        # and no player replacement can enter the replay window.
        if (sentry.slow is None or fight.reinhardt_barriers.states
                or end_tick - previous_tick >= math.ceil(respawn_seconds(fight.match) / TICK)):
            return False
        return all(
            not isinstance(target.owner, bots.Bot) and self._recorded_pose(target, previous_tick)
            for target in _unit_targets(fight, sentry.owner) if target.entity != frozen.entity
        )

    def _support_retarget_isolated(self, rocket, distance, previous_tick):
        from ow174.game.combat import ray_capsule

        fight = self.combat
        if rocket.team != self.sentries[0].owner.team or fight.barriers.reinhardt.actors:
            return False
        targets = _unit_targets(fight, rocket.shooter)
        if any(isinstance(target.owner, bots.Bot) and not target.owner.retain_corpse for target in targets):
            return False
        players = [target for target in targets if not isinstance(target.owner, bots.Bot)]
        if not self._out_of_range_history(players, previous_tick):
            return False
        # A new slow changes the route preview. Replay the real poses when
        # only retained NPCs and this ice deployment can be affected, even
        # if the rocket hits an NPC anywhere before the predicted wall hit.
        radius = rocket.blast.rings[-1][0] if rocket.blast.rings else 0
        for target in players:
            contact = ray_capsule(
                rocket.position, rocket.direction, *target.segment(), target.radius + radius,
            )
            if contact is not None and contact <= distance:
                return False
        return True

    def _support_rocket_contact(
        self, frozen, rocket, distance, previous_tick, impact_tick, fixed_body=None, *, moving_frozen=False,
    ):
        from ow174.game.combat import TICK, _along, ray_capsule, segment_distance

        fight = self.combat
        targets = _unit_targets(fight, rocket.shooter)
        previews = [
            (target, copy(target.owner)) for target in fight._targets()
            if (moving_frozen or target.entity != frozen.entity)
            and isinstance(target.owner, bots.Bot) and target.owner.route
        ]
        if not previews:
            return impact_tick, distance, fixed_body, targets
        locked = [
            (sentry.bot.position, sentry.owner.team,
             max(0.0, segment_distance(sentry.bot.position, *frozen.segment()) - frozen.radius))
            for sentry in self.sentries
        ]
        enemy_ids = {target.entity for target in targets}
        step = rocket.speed * TICK
        body = None
        # Step poses before each flight segment, as the world update does.
        # A closer beam target could change its route speed before contact.
        for tick in range(previous_tick + 1, impact_tick + 1):
            travelled = (tick - previous_tick - 1) * step
            origin = _along(rocket.position, rocket.direction, travelled)
            nearest = step
            for target, preview in previews:
                if not _step_route_preview(fight, preview, tick):
                    return None
                segment = target.segment(preview.position)
                if target.entity != frozen.entity and any(
                    target.team != team and segment_distance(beam_origin, *segment) - target.radius <= reach
                    for beam_origin, team, reach in locked
                ):
                    return None
                if target.entity in enemy_ids:
                    hit = ray_capsule(origin, rocket.direction, *segment, target.radius)
                    if hit is not None and hit < distance - travelled and (
                        hit < nearest or (hit == nearest and body is None)
                    ):
                        nearest, body = hit, target
            if body is not None:
                impact_tick, distance = tick, travelled + nearest
                break
        poses = {target.entity: preview.position for target, preview in previews}
        impact_targets = [
            replace(target, feet=poses[target.entity]) if target.entity in poses else target
            for target in targets
        ]
        return impact_tick, distance, body if body is not None else fixed_body, impact_targets

    def _route_holds_until(self, frozen, thaw, contact):
        from ow174.game.combat import TICK

        bot = frozen.owner
        if not bot.route:
            return True
        # Movement precedes impact. Preview on a copy while the current
        # wall is still intact, including the impact tick.
        preview = copy(bot)
        preview.last_tick = thaw - 1
        preview._pause(thaw - 1)
        for tick in range(thaw, contact + 1):
            if not _step_route_preview(self.combat, preview, tick):
                return False
            if preview.position != frozen.feet:
                return False
        return True

    def _out_of_range_history(self, targets, previous_tick):
        from ow174.game.combat import segment_distance

        # Additional players can coexist with the frozen lock only if
        # they have a recorded unchanged pose beyond every attached beam.
        # Do not extrapolate other NPC routes.
        if any(
            isinstance(target.owner, bots.Bot) or not self._recorded_pose(target, previous_tick)
            for target in targets
        ):
            return False
        for sentry in self.sentries:
            beam = self._beam(sentry)
            reach = expr.to_float(beam.find_var(graph.INSTANCE, 1648).value())
            if any(segment_distance(sentry.bot.position, *target.segment()) - target.radius <= reach
                   for target in targets):
                return False
        return True

    @staticmethod
    def _beam(sentry):
        instances = sentry.bot.body_script.component.instances
        return next(instance for instance in instances.values() if instance.graph.index == BEAM_GRAPH)

    def update(
        self, damage_starts, cryo_expiries, barrier_cover_ends, *,
        replay_ice=False, replay_owners=(), replay_frozen=(),
    ):
        from ow174.game.combat import COMBAT_CATCH_UP, TICK

        retained = replay_ice or bool(replay_owners) or bool(replay_frozen)
        for sentry in tuple(self.sentries):
            bot, owner = sentry.bot, sentry.owner
            if retained:
                moved = bot.moved_tick is not None
                ice_eligible = replay_ice and (
                    sentry.support_pillar is not None or (sentry.attached_at is None and moved)
                )
                if (
                    not ice_eligible and not (moved and owner in replay_owners)
                    and bot.entity not in replay_frozen
                ):
                    continue
            if (bot not in self.combat.match.bots or bot.dead or owner.gone or owner.hero.guid != HERO):
                self._retire(sentry)
                continue
            elapsed = self.combat.tick - sentry.last_tick
            if elapsed <= 0:
                continue
            sentry.last_tick = self.combat.tick
            start = self.combat.now - (1 if retained else elapsed) * TICK
            if sentry.attached_at is None:
                self._advance_flight(sentry, elapsed)
            script = bot.body_script
            if script is None:
                raise ValueError("deployed sentry lost its authored body script")
            snapshot = script.advance_live(self.combat.tick, bot.position)
            if snapshot is not None:
                for viewer in self.combat.match.players:
                    if viewer.client is not None and viewer.spawned:
                        viewer.client.queue_bot_live(bot.entity, snapshot)
            if sentry.attached_at is None:
                continue
            if bot.entity in self.combat.sombra_controls.locks:
                self._clear_slow(sentry)
                continue
            attach = next(i for i in script.component.instances.values() if i.graph.index == ATTACH_GRAPH)
            charge = attach.state(4)
            fixed = charge.node.field("m_5BEFF040")
            if fixed is not None:
                delay = expr.to_float(charge.evaluate(fixed, watch=False))
            else:
                rate_cfg = charge.node.field("m_9B0385B4")
                rate = expr.to_float(charge.evaluate(rate_cfg, watch=False)) if rate_cfg is not None else 1.0
                destination = expr.to_float(charge.evaluate(charge.node.field("m_destination"), watch=False))
                delay = destination / rate if rate > 0 else math.inf
            ready = sentry.attached_at + delay
            if self.combat.now <= ready:
                continue
            beam = self._beam(sentry)
            reach = expr.to_float(beam.find_var(graph.INSTANCE, 1648).value())
            origin = bot.position
            target = self._target(sentry, origin, reach)
            if target is None:
                self._clear_slow(sentry)
                continue
            target_script = getattr(target.owner, "body_script", None)
            protected = (_attached_barrier(self.combat, target.owner) is not None
                         or fortify.active_filter(target.owner) is not None)
            if protected:
                self._clear_slow(sentry)
                sentry.target = target.entity
            elif sentry.target != target.entity or (target_script is not None and sentry.slow is None):
                self._clear_slow(sentry)
                sentry.target = target.entity
                if target_script is not None:
                    component = target_script.component
                    aura = component.create(graph.graph(SLOW_GRAPH))
                    percent_cfg = beam.graph.state(9).field("m_params")[0]["m_value"]
                    percent = beam.evaluate(percent_cfg)
                    if runtime.send_instance_message(aura, 0x024000000000000F,
                                                     {0x002A: percent}, bot.entity) != 1:
                        component.destroy(aura)
                        raise ValueError("sentry slow aura has no authored activation entry")
                    component.run_now()
                    sentry.slow = component, aura
            # Normally only the retained combat window has usable target poses.
            cryo_expiry = _cryo_cover_expiry(self.combat, origin, target, cryo_expiries, start)
            barrier_start = start
            for service, ended in barrier_cover_ends:
                cover_end = service.cover_end(bot, origin, target, ended)
                if cover_end is not None:
                    # Expose the whole interval of the first tick at or after
                    # cover ends. Ignore float noise at grid boundaries.
                    ticks_since_end = math.floor((self.combat.now - cover_end) / TICK + 1e-9)
                    barrier_start = max(barrier_start, self.combat.now - (ticks_since_end + 1) * TICK)
            modify = beam.graph.state(1).field("m_modifyHealth")
            rate = expr.to_float(beam.evaluate(modify["m_amount"]))
            retained_start = self.combat.now - COMBAT_CATCH_UP * TICK
            beam_start = max(
                start, ready, cryo_expiry, barrier_start, damage_starts.get(target.entity, start),
            )
            amount = rate * max(0.0, self.combat.now - max(beam_start, retained_start))
            loadout = beam.evaluate(modify["m_loadout"])
            self.combat._damage(owner, target, amount, False,
                                loadout.guid if isinstance(loadout, expr.Asset) else None,
                                self.combat.tick, world_tick=self.combat.tick, beam=True)
            if self.combat.vitals_of(target).dead:
                self._clear_slow(sentry)

    def _held_frozen_pose(self, target, previous_tick):
        from ow174.game.combat import TICK

        actor = target.owner
        if not isinstance(actor, bots.Bot) or _external_motion(self.combat, actor):
            return False
        frost = self.combat.frost(actor, tick=previous_tick)
        # A live full freeze records its activation time in milliseconds.
        if frost is None or not frost[2] or frost[1] > previous_tick * round(TICK * 1000):
            return False
        return self._recorded_pose(target, previous_tick)

    def _recorded_pose(self, target, previous_tick):
        history = self.combat.history.get(target.entity, ())
        return any(sample[:4] == (previous_tick, target.feet, target.height, target.eye)
                   for sample in history)


def _barrier_occludes(fight, shooter, origin, target):
    direction, reach = _capsule_ray(origin, target)
    return bool(reach and fight.trace_barrier(shooter, origin, direction, reach) is not None)


def _impact_damage(rocket):
    if rocket.direct is None:
        return 0.
    scalar = rocket.direct.scale(rocket.flown * rocket.speed) if rocket.distance_falloff else 1.
    return rocket.direct.amount * scalar


def _projectile_cover(actor):
    from ow174.game import defenses

    script = getattr(actor, "body_script", None)
    if defenses.deflect_state(actor) is not None or defenses.grasp_state(actor) is not None:
        return True
    if (script is None or getattr(script, "retired", False) or script.component.owner_dead
            or script.component.fault is not None):
        return False
    return any(instance.graph.index == 0x02CC
               and (state := instance.states.get(3)) is not None and state.active
               and state.node.cls == "STUStatescriptStateDeflectProjectiles"
               for instance in script.component.instances.values())


def _single_replay_projection(fight):
    from ow174.game.combat import _source_entity

    projected = _projected_barriers(fight)
    if len(projected) != 1:
        return None
    actor = projected[0]
    if actor.vitals is None or actor.vitals.total <= 0:
        return None
    active = []
    for player in fight.match.players:
        if player.hero.guid != 0x02E0000000000068:
            continue
        script = player.body_script
        if script is None or script.retired or script.component.owner_dead:
            continue
        for instance in script.component.instances.values():
            index = {0x0244: 2, 0x025C: 10}.get(instance.graph.index)
            pet = instance.states.get(index) if index is not None else None
            if pet is not None and pet.active and not pet.ending:
                active.append((player, pet))
    if len(active) != 1 or active[0][0] is not actor.parent:
        return None
    _, pet = active[0]
    if pet.instance.graph.index != 0x025C or pet.instance.id != actor.instance or pet.counter != actor.activation:
        return None
    slot = pet.instance.vars.get(536)
    recipient = actor.recipient
    if slot is None or recipient is None or expr.to_entity(slot.value()) != _source_entity(recipient):
        return None
    if isinstance(recipient, bots.Bot) and not recipient.retain_corpse:
        return None
    return recipient


def _step_route_preview(fight, preview, tick):
    from ow174.game import ice_wall
    from ow174.game.combat import TICK
    from ow174.game.mover import statescript_mods

    if preview.impulse_mover is not None or preview.control_mover is not None:
        return False
    script = preview.body_script
    mods = statescript_mods(script.component) if script is not None and not preview.dead else {}
    mods.update(fight.orisa.npc_movement_mods(preview.entity, tick) or {})
    mods = fight.movement_mods(preview, mods, tick=tick)
    preview.step(tick, TICK, fight.match.collision_world,
                 paused_until=fight.movement_pause_until(preview), movement_mods=mods,
                 pillars=ice_wall.pillar_actors(fight))
    return True


def _unit_targets(fight, shooter):
    """Use the same unit search in acquisition and its historical lock proof."""
    from ow174.game import actors, ice_wall

    return [target for target in fight._enemies(shooter)
            if not (isinstance(target.owner, actors.Deployable)
                    and target.owner.definition == ice_wall.PILLAR)]


def _external_motion(fight, actor):
    """An owned manual mover can remain after its external displacement ends."""
    if fight.knockbacks.affecting(actor.entity) or fight.knockbacks.has_pending(actor.entity):
        return True
    hooks = getattr(fight, "hooks", None)
    if hooks is not None and hooks.controlled(actor.entity):
        return True
    if fight.orisa.controls(actor.entity, fight.tick):
        return True
    mover = actor.impulse_mover
    if mover is None:
        return False
    command = mover.last_command
    return (mover.airborne or any(mover.velocity)
            or (command is not None and bool(command.forward or command.right)))


def _projected_barriers(fight):
    from ow174.game import barriers

    return tuple(actor for actor in fight.barriers.scripted.values()
                 if actor.component.world.hero == barriers.ZARYA and actor.recipient is not None
                 and barriers._live(fight, actor))


def _attached_barrier(fight, owner):
    from ow174.game.symmetra import _attached_barrier as contact
    return contact(fight, owner)


def _visible(fight, shooter, origin, target):
    from ow174.game.combat import capsule_visible
    from ow174.game.symmetra import _trace_barrier
    a, b = target.segment()
    return capsule_visible(
        fight._collision(), origin, a, b, target.radius, target_entity=target.entity,
        walls=fight.walls,
        cover_contact=lambda start, direction, reach: fight._cryo_contact(shooter, start, direction, reach),
        mesh_blocks=lambda start, direction, reach: _trace_barrier(fight, shooter, start, direction, reach) is not None,
    )


def _cryo_cover_expiry(fight, origin, target, expiries, start):
    if not expiries:
        return start
    from ow174.game.combat import EPSILON, ray_capsule
    direction, reach = _capsule_ray(origin, target)
    contacts=[]
    for covered in fight._targets():
        if covered.entity not in expiries:
            continue
        a,b=covered.segment()
        distance=ray_capsule(origin,direction,a,b,covered.radius)
        if distance is not None and distance < reach-EPSILON:
            contacts.append(expiries[covered.entity])
    return max(contacts,default=start)


def _capsule_ray(origin, target):
    from ow174.game.combat import EPSILON, _sub, _dot, _along
    a,b=target.segment()
    ab=_sub(b,a)
    length=_dot(ab,ab)
    t=0.0 if length<=EPSILON else max(0.0,min(1.0,_dot(_sub(origin,a),ab)/length))
    delta=_sub(_along(a,ab,t),origin)
    distance=math.sqrt(_dot(delta,delta))
    reach=max(0.0,distance-target.radius)
    if reach<=EPSILON:
        return (0.0,0.0,1.0),0.0
    return tuple(axis/distance for axis in delta),reach
