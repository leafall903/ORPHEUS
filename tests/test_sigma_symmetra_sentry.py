"""Actual 157E summon commands and Sigma's two defensive channels."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_sigma_barrier as barrier_fixtures
import test_sigma_combat as combat_fixtures
import test_sigma_flux_placement as flux_fixtures
from test_game_combat import FakeClient, aim

from ow174.game import combat, sigma, symmetra_beam, symmetra_sentry
from ow174.game.commands import SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph


class SentryClient(FakeClient):
    def queue_bot_death(self, entity, event):
        pass

    def bot_stream(self, entity):
        return None


@unittest.skipUnless(
    os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the external summon graph bundle",
)
class SigmaSentryTests(unittest.TestCase):
    def prepare(self, run, owner):
        position = list(owner.mover.position)
        self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra_beam.HERO]))
        owner.mover.position = position
        for player in run.players:
            player.client = SentryClient(player)
        run.step({})
        weapon = next(i for i in owner.body_script.component.instances.values()
                      if i.graph.index == symmetra_sentry.LAUNCH)
        self.assertEqual(weapon.find_var(graph.INSTANCE, 11).value(), 3)
        return weapon

    def launch(self, run, owner, yaw):
        fight = run.match.combat
        for _ in range(40):
            run.step({owner: (yaw, 0, 8)})
            if fight.sentries:
                break
        sentry, = fight.sentries
        self.assertIsNone(sentry.attached_at)
        self.assertEqual(sentry.bot.body, symmetra_sentry.BODY)
        self.assertIs(sentry.owner, owner)
        return sentry

    def attach(self, run, sentry):
        for _ in range(80):
            run.step({})
            if sentry.attached_at is not None:
                break
        self.assertIsNotNone(sentry.attached_at)
        self.assertEqual(sentry.bot.velocity, (0, 0, 0))

    def test_sentry_crosses_live_grasp_then_damages_and_slows_sigma(self):
        fixture = combat_fixtures.SigmaCombatTests()
        run, player, owner, _ = fixture.arena(offset=0, distance=6)
        fixture.wall(run, sideways=False, offset=-4)
        weapon = self.prepare(run, owner)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        run.step({player: (0, 0, 8)})
        grasp = fixture.ability(player, sigma.GRASP).instance
        sentry = self.launch(run, owner, -32768)
        crossing = sigma.grasp_intersection(fight, owner, sentry.bot.position, sentry.direction, 8)
        self.assertIsNotNone(crossing)
        self.assertIs(crossing[1].instance, grasp)
        self.assertEqual(weapon.find_var(graph.INSTANCE, 11).value(), 2)
        self.attach(run, sentry)
        self.assertEqual(fight.vitals[sentry.bot.entity].total, 30)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(sentry.bot.team, owner.team)
        self.assertTrue(grasp.states[1].active)
        self.assertAlmostEqual(sentry.bot.position[2], player.mover.position[2] - 3.99)
        for _ in range(37):
            run.step({})
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertFalse(vitals.temporary_shields)
        self.assertAlmostEqual(400 - vitals.total, 40 * (fight.now - sentry.attached_at - .5))
        self.assertEqual(sentry.target, player.body)
        self.assertAlmostEqual(player.body_script.component.entity_var(23186).value(), .2)
        before_gap = vitals.total
        owner_frame = player.body_script.frame
        tick = run.frame + 12
        run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertAlmostEqual(before_gap - vitals.total, 40 * 12 * combat.TICK)
        self.assertEqual(player.body_script.frame, owner_frame)
        player.take_commands([Command(tick + 1, forward=127)])
        self.assertAlmostEqual(player.mover.values.run_forward, 4.4, places=5)

    @unittest.skipUnless(barrier_fixtures.MODEL, "requires the external Experimental Barrier mesh")
    def test_sentry_flight_crosses_experimental_barrier_without_damage(self):
        fixture = barrier_fixtures.SigmaBarrierTests()
        run, player, (owner,), actor, vitals = fixture.arena()
        combat_fixtures.SigmaCombatTests().wall(run, sideways=False, offset=-3)
        self.prepare(run, owner)
        sentry = self.launch(run, owner, -32768)
        self.assertIsNotNone(actor.intersection(sentry.bot.position, sentry.direction, 10))
        self.attach(run, sentry)
        self.assertLess(sentry.bot.position[2], player.mover.position[2])
        self.assertEqual(actor.vitals.total, 700)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(run.match.combat.vitals[sentry.bot.entity].total, 30)

    @unittest.skipUnless(barrier_fixtures.MODEL, "requires the external Experimental Barrier mesh")
    def test_barrier_blocks_deployed_beam_until_actual_recall(self):
        fixture = barrier_fixtures.SigmaBarrierTests()
        run, player, (owner,), actor, vitals = fixture.arena(hold=8)
        owner.mover.position[2] -= 3
        combat_fixtures.SigmaCombatTests().wall(run, sideways=False, offset=8)
        self.prepare(run, owner)
        sentry = self.launch(run, owner, 0)
        self.attach(run, sentry)
        for _ in range(50):
            run.step({})
        self.assertGreater(run.match.combat.now - sentry.attached_at, .5)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(actor.vitals.total, 700)
        self.assertIsNone(sentry.target)
        self.assertIsNone(sentry.slow)
        run.step({player: (0, 0, SECONDARY)})
        for _ in range(8):
            run.step({})
        self.assertFalse(actor.active)
        self.assertLess(vitals.total, 400)
        self.assertEqual(sentry.target, player.body)
        self.assertAlmostEqual(player.body_script.component.entity_var(23186).value(), .2)

    def test_accretion_destroys_sentry_and_retires_its_slow(self):
        fixture = combat_fixtures.SigmaCombatTests()
        run, player, owner, _ = fixture.arena(offset=0, distance=6)
        fixture.wall(run, sideways=False, offset=-4)
        self.prepare(run, owner)
        sentry = self.launch(run, owner, -32768)
        self.attach(run, sentry)
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        self.assertEqual(sentry.target, player.body)
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        x, y, z = sentry.bot.position
        yaw, pitch = aim(fight._eye(player), (x, y + .5, z))
        run.step({player: (yaw, pitch, 0x10)})
        for _ in range(80):
            if not fight.sentries:
                break
            run.step({player: (yaw, pitch, 0)})
        self.assertFalse(fight.sentries)
        self.assertNotIn(sentry.bot, run.match.bots)
        self.assertNotIn(sentry.bot.entity, fight.vitals)
        self.assertFalse(player.body_script.component.entity_var(23186).value())
        self.assertEqual(script.component.entity_var(342).value(), 0)
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        after = vitals.total
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, after)

    def test_sentry_does_not_acquire_an_ally_in_a_team_match(self):
        fixture = combat_fixtures.SigmaCombatTests()
        run, player, owner, _ = fixture.arena(offset=0, distance=6)
        fixture.wall(run, sideways=False, offset=-3)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = player.team
        self.prepare(run, owner)
        sentry = self.launch(run, owner, -32768)
        self.attach(run, sentry)
        for _ in range(45):
            run.step({})
        self.assertIsNone(sentry.target)
        self.assertIsNone(sentry.slow)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        self.assertEqual(vitals.total, 400)

    def test_confirmed_flux_lifts_the_owner_and_leaves_the_in_range_sentry_firing(self):
        fixture = combat_fixtures.SigmaCombatTests()
        run, player, owner, _ = fixture.arena(offset=0, distance=4)
        fixture.wall(run, sideways=False, offset=-3)
        self.prepare(run, owner)
        sentry = self.launch(run, owner, -32768)
        self.attach(run, sentry)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        caster_health = fight.vitals_of(target)
        owner_health = fight.vitals_of(next(target for target in fight._targets() if target.owner is owner))
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        flux = fixture.ability(player, sigma.FLUX).instance
        candidates_without_summon_gate = []
        original = sigma.flux_targets

        def observe(world, state):
            selected = original(world, state)
            if state is flux.node_state(flux.graph.node(37)):
                # Remove only the scope marker. 2658's capsule and team stay the same.
                with patch.dict(sentry.bot.spawner, {"summoned": False}):
                    candidates_without_summon_gate.append(original(world, state))
            return selected

        x, y, z = player.mover.position
        attached = sentry.bot.position
        with patch.object(sigma, "flux_targets", side_effect=observe):
            flux_fixtures.SigmaFluxPlacementTests().select(run, player, (x, y, z + 1))
            for _ in range(150):
                run.step({})
                if owner.mover.airborne:
                    break
        self.assertEqual(flux.vars[375].value(), (expr.Entity(owner.body),))
        self.assertTrue(any(sentry.bot.entity in candidates for candidates in candidates_without_summon_gate))
        self.assertTrue(owner.mover.airborne)
        self.assertEqual(owner_health.total, 175)
        before = caster_health.total
        for _ in range(12):
            run.step({})
        self.assertTrue(owner.mover.airborne)
        self.assertAlmostEqual(before - caster_health.total, 40 * 12 * combat.TICK)
        for _ in range(350):
            if owner_health.total == 62.5:
                break
            run.step({})
        self.assertEqual(owner_health.total, 62.5)
        self.assertFalse(owner.mover.airborne)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(fight.sentries, [sentry])
        self.assertEqual(fight.vitals[sentry.bot.entity].total, 30)
        self.assertEqual(sentry.bot.position, attached)
        self.assertIsNone(sentry.bot.effect_mover)
        self.assertFalse(any(i.graph.index == sigma.FLUX_EFFECT
                             for i in sentry.bot.body_script.component.instances.values()))

    def test_static_cover_and_authored_range_prevent_sentry_acquisition(self):
        for reason, wall in (("cover", 3), ("range", -12)):
            with self.subTest(reason=reason):
                fixture = combat_fixtures.SigmaCombatTests()
                run, player, owner, _ = fixture.arena(offset=0, distance=6)
                fixture.wall(run, sideways=False, offset=wall)
                self.prepare(run, owner)
                sentry = self.launch(run, owner, -32768)
                self.attach(run, sentry)
                for _ in range(50):
                    run.step({})
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is player)
                self.assertEqual(fight.vitals_of(target).total, 400)
                self.assertIsNone(sentry.target)
                self.assertIsNone(sentry.slow)

    def test_three_charges_deny_fourth_until_recharge_and_cap_active_sentries(self):
        fixture = combat_fixtures.SigmaCombatTests()
        run, _, owner, _ = fixture.arena(offset=0, distance=30)
        fixture.wall(run, sideways=False, offset=24)
        weapon = self.prepare(run, owner)
        fight = run.match.combat

        def press():
            for _ in range(40):
                run.step({owner: (-32768, 0, 8)})
            run.step({})

        for _ in range(3):
            press()
        first = [sentry.bot for sentry in fight.sentries]
        self.assertEqual(len(first), 3)
        self.assertEqual(weapon.find_var(graph.INSTANCE, 11).value(), 0)
        press()
        self.assertEqual([sentry.bot for sentry in fight.sentries], first)
        for _ in range(650):
            run.step({})
        self.assertEqual([sentry.bot for sentry in fight.sentries], first)
        self.assertGreater(weapon.find_var(graph.INSTANCE, 11).value(), 0)
        press()
        active = [sentry.bot for sentry in fight.sentries]
        self.assertEqual(len(active), 3)
        self.assertEqual(active[:2], first[1:])
        self.assertNotIn(first[0], run.match.bots)

    def test_hero_switch_retires_summon_and_clears_its_active_slow(self):
        fixture = combat_fixtures.SigmaCombatTests()
        run, player, owner, _ = fixture.arena(offset=0, distance=6)
        fixture.wall(run, sideways=False, offset=-4)
        self.prepare(run, owner)
        sentry = self.launch(run, owner, -32768)
        self.attach(run, sentry)
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        self.assertEqual(sentry.target, player.body)
        self.assertTrue(player.body_script.component.entity_var(23186).value())
        vitals = fight.vitals[player.body]
        before = vitals.total
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        run.step({})
        self.assertFalse(fight.sentries)
        self.assertNotIn(sentry.bot, run.match.bots)
        self.assertFalse(player.body_script.component.entity_var(23186).value())
        self.assertEqual(vitals.total, before)

    def kill_owner_while_sentry_fires(self, *, keep_target_alive=False):
        fixture = combat_fixtures.SigmaCombatTests()
        run, player, owner, _ = fixture.arena(offset=0, distance=6)
        fixture.wall(run, sideways=False, offset=-4)
        self.prepare(run, owner)
        sentry = self.launch(run, owner, -32768)
        self.attach(run, sentry)
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        self.assertEqual(sentry.target, player.body)
        slow = sentry.slow
        if keep_target_alive:
            # Keep Sigma alive through ten seconds of uninterrupted counterfire.
            fight.vitals[player.body].grant_temporary_shields(sigma.SHIELD_TAG, 100, 400)
        owner_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))
        owner_health.now[combat.HEALTH_POOL] = 60
        owner_health.now[combat.SHIELDS_POOL] = 0
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(13)
        yaw, pitch = aim(fight._eye(player), fight._eye(owner))
        run.step({player: (yaw, pitch, 0x10)})
        for _ in range(80):
            if owner_health.dead:
                break
            run.step({player: (yaw, pitch, 0)})
        self.assertTrue(owner_health.dead)
        self.assertEqual(owner_health.killer, player.body)
        self.assertEqual(script.component.entity_var(342).value(), 73)
        self.assertIs(sentry.slow, slow)
        return run, player, owner, sentry

    def test_live_sentry_keeps_its_slow_and_uses_the_replacement_owner_after_respawn(self):
        cases = ((False, 0, 5), (True, 0, 5), (True, 1, 21),
                 (True, 0, 70), (True, 1, 70), (True, 2, 100))
        for skipped, failures, tail in cases:
            with self.subTest(skipped=skipped, failures=failures, tail=tail):
                run, player, owner, sentry = self.kill_owner_while_sentry_fires(keep_target_alive=True)
                fight = run.match.combat
                slow = sentry.slow
                health = fight.vitals[player.body]
                old_body = owner.body
                deadline, = (when for when, actor in fight.respawns if actor is owner)
                respawn_tick = round(deadline / combat.TICK)

                def update(tick, match=run.match):
                    match.update(tick * combat.TICK, tick, frames=False)

                gap_start = respawn_tick - 5
                for tick in range(run.frame + 1, gap_start + 1):
                    update(tick)
                self.assertEqual(owner.body, old_body)
                before = health.total
                contacts = []
                damage = fight._damage

                def observe(shooter, target, *args, owner=owner, player=player, contacts=contacts,
                            damage=damage, **kwargs):
                    if shooter is owner and target.owner is player:
                        contacts.append((kwargs["world_tick"], shooter.body))
                    return damage(shooter, target, *args, **kwargs)

                attempts = []
                allocate = run.match._next_body_id

                def next_id(actor, failures=failures, owner=owner, match=run.match,
                            attempts=attempts, allocate=allocate):
                    if actor is owner:
                        attempts.append(match.tick)
                        if len(attempts) <= failures:
                            return None
                    return allocate(actor)

                replaced = respawn_tick + 16 * failures
                end = respawn_tick + tail
                first = max(gap_start + 1, end - combat.COMBAT_CATCH_UP + 1)
                with (patch.object(fight, "_damage", side_effect=observe),
                      patch.object(run.match, "_next_body_id", side_effect=next_id)):
                    for tick in (end,) if skipped else range(gap_start + 1, end + 1):
                        update(tick)
                self.assertNotEqual(owner.body, old_body)
                self.assertFalse(fight.is_dead(owner))
                self.assertEqual(attempts, list(range(respawn_tick, replaced + 1, 16)))
                self.assertEqual(fight.replacement_ticks[owner.body], replaced)
                self.assertEqual(contacts, [
                    (tick, old_body if tick <= replaced else owner.body)
                    for tick in range(first, end + 1)
                ])
                self.assertAlmostEqual(before - health.total, 40 * (end - first + 1) * combat.TICK)
                self.assertEqual(fight.sentries, [sentry])
                self.assertIs(sentry.slow, slow)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 73)

    def test_posthumous_sentry_kills_sigma_and_reacquires_the_replacement_body(self):
        for tail in (5, 70):
            with self.subTest(tail=tail):
                self.assert_sentry_reacquisition(tail)

    def assert_sentry_reacquisition(self, tail):
        run, player, owner, sentry = self.kill_owner_while_sentry_fires()
        fight = run.match.combat
        old_owner, old_target = owner.body, player.body
        old_component, old_aura = sentry.slow
        health = fight.vitals[old_target]
        health.now[combat.HEALTH_POOL] = 2
        health.now[combat.SHIELDS_POOL] = 0
        for _ in range(4):
            run.step({})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, old_owner)
        self.assertIsNone(sentry.slow)
        self.assertNotIn(old_aura.id, old_component.instances)
        self.assertFalse(old_component.entity_var(23186).value())

        due = {actor: round(when / combat.TICK) for when, actor in fight.respawns}
        end = due[player] + tail
        run.match.update(end * combat.TICK, end, frames=False)
        self.assertNotEqual(owner.body, old_owner)
        self.assertNotEqual(player.body, old_target)
        self.assertEqual(fight.replacement_ticks[owner.body], due[owner])
        self.assertEqual(fight.replacement_ticks[player.body], due[player])
        self.assertEqual(fight.sentries, [sentry])
        self.assertEqual(sentry.target, player.body)
        self.assertIs(sentry.slow[0], player.body_script.component)
        self.assertIsNot(sentry.slow[1], old_aura)
        expected_hits = min(tail, combat.COMBAT_CATCH_UP)
        self.assertAlmostEqual(fight.vitals[player.body].total, 400 - 40 * expected_hits * combat.TICK)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 73)
