"""Ana's authored Sleep Dart against Ashe through ordinary combat input."""

import os
import unittest

import test_ashe_bob as gun_fixtures
import test_ashe_bob_charge as charge_fixtures
import test_ashe_combat as fixtures
import test_ashe_controls as controls
import test_mccree_flashbang as flash_fixtures
from test_ashe_impulses import wall_scene
from test_game_combat import aim

from ow174.game import combat
from ow174.game.content import HEROES_PATH, SOLDIER, find_hero, heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_COST


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires build 104319 data")
class AsheSleepTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    step = flash_fixtures.FlashbangTests.step

    def arena(self, *, ally=False):
        run, ashe, ana = self.fight(distance=4, team_game=ally)
        self.assertTrue(run.match.switch_hero(ana, find_hero("ana")))
        ana.mover.position[:] = (0, ashe.mover.position[1], 4)
        if ally:
            ana.team = ashe.team
        for _ in range(50):
            run.step({})
        fight = run.match.combat
        for target in fight._targets():
            fight.vitals_of(target)
        return run, ana, ashe

    def cast(self, run, ana, target):
        self.step(run, ana, target, cast=8)
        fight = run.match.combat
        entity = combat._source_entity(target)
        for _ in range(60):
            if entity in fight.sleeps:
                return fight.sleeps[entity]
            self.step(run, ana, target)
        self.fail("actual Sleep Dart never applied its control effect")

    def test_dart_blocks_ashe_actions_and_movement_until_natural_expiry(self):
        run, ana, ashe = self.arena()
        fight = run.match.combat
        start, deadline = self.cast(run, ana, ashe)
        self.assertEqual(fight.vitals[ashe.body].total, 195)
        self.assertEqual(deadline - start, 313)
        ana_health = fight.vitals[ana.body].total
        position = tuple(ashe.mover.position)
        ashe.body_script.set_ult_charge(ashe.body_script.component.entity_var(ULT_COST).value())
        while run.frame < deadline - 1:
            self.step(run, ana, ashe, target_buttons=1 | 2 | 8 | 16 | 32, forward=127)
        self.assertEqual(tuple(ashe.mover.position), position)
        self.assertEqual(fight.vitals[ana.body].total, ana_health)
        self.assertFalse(ashe.body_script.component.held)
        self.assertFalse(fight.dynamite.grenades)
        self.assertFalse(fight.companions.active)
        self.assertTrue(fight.is_stunned(ashe))
        self.step(run, ana, ashe)
        self.assertNotIn(ashe.body, fight.sleeps)
        self.assertFalse(fight.is_stunned(ashe))
        for _ in range(12):
            self.step(run, ana, ashe, target_buttons=1, forward=127)
        self.assertNotEqual(tuple(ashe.mover.position), position)
        self.assertLess(fight.vitals[ana.body].total, ana_health)

    def test_early_rifle_damage_preserves_sleep_and_later_damage_shortens_it(self):
        run, ana, ashe = self.arena()
        fight = run.match.combat
        start, deadline = self.cast(run, ana, ashe)
        self.assertTrue(run.match.switch_hero(ana, heroes()[SOLDIER]))
        ana.mover.position[:] = (0, ashe.mover.position[1], 4)
        for _ in range(10):
            self.step(run, ana, ashe)
        self.step(run, ana, ashe, cast=1)
        self.assertEqual(fight.vitals[ashe.body].total, 175)
        self.assertEqual(fight.sleeps[ashe.body], (start, deadline))

        while run.frame < start + 50:
            self.step(run, ana, ashe)
        self.step(run, ana, ashe, cast=1)
        self.assertEqual(fight.vitals[ashe.body].total, 155)
        wake = run.frame + 32
        self.assertEqual(fight.sleeps[ashe.body], (start, wake))
        while run.frame < wake - 1:
            self.step(run, ana, ashe, target_buttons=8)
        self.assertTrue(fight.is_stunned(ashe))
        self.step(run, ana, ashe)
        self.assertFalse(fight.is_stunned(ashe))

    def test_untouched_dynamite_burn_wakes_ashe_at_the_first_eligible_pulse(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run = fixtures.Run(fixtures.KANEZAKA_DM, ("Thrower", "Ashe", "Ana"))
                thrower, ashe, ana = run.players
                run.match.bots = []
                for player, hero, position in (
                    (thrower, "ashe", (1.2, 1, 0)),
                    (ashe, "ashe", (0, 1, 15.5)),
                    (ana, "ana", (0, 1, 19.5)),
                ):
                    self.assertTrue(run.match.switch_hero(player, find_hero(hero)))
                    player.mover.position[:] = position
                    player.mover.body = None
                for _ in range(100):
                    run.step({})
                fight = run.match.combat
                fixtures.AsheCombatTests().press(run, thrower, 16)
                grenade, = fight.dynamite.grenades
                while run.frame < grenade.fuse:
                    run.step({})
                self.assertEqual(fight.vitals[ashe.body].total, 150)
                burn = fight.dynamite.burns[(thrower.body, ashe.body)]
                start, original = self.cast(run, ana, ashe)
                self.assertNotIn(combat.SLEEP_NO_WAKE_TAG, burn.tags)
                self.assertEqual(original - start, 313)

                early = 0
                while (burn.next_damage - start) * combat.TICK <= combat.SLEEP_DAMAGE_GRACE:
                    due = burn.next_damage
                    before = fight.vitals[ashe.body].total
                    while run.frame < due:
                        run.step({})
                    self.assertEqual(fight.vitals[ashe.body].total, before - 5)
                    self.assertEqual(fight.sleeps[ashe.body], (start, original))
                    early += 1
                self.assertGreater(early, 0)

                due = burn.next_damage
                before = fight.vitals[ashe.body].total
                if skipped:
                    run.frame = due + 2
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    while run.frame < due + 2:
                        run.step({})
                self.assertEqual(fight.vitals[ashe.body].total, before - 5)
                wake = due + 32
                self.assertEqual(fight.sleeps[ashe.body], (start, wake))
                while run.frame < wake - 1:
                    run.step({})
                self.assertTrue(fight.is_stunned(ashe))
                self.assertEqual(fight.sleeps[ashe.body], (start, wake))

                angle = aim(fight._eye(ashe), fight._eye(ana))
                run.step({ashe: (*angle, 1)})
                self.assertFalse(fight.is_stunned(ashe))
                self.assertEqual(len(ashe.client.messages(combat.HIT)), 1)
                self.assertIn((thrower.body, ashe.body), fight.dynamite.burns)

    def test_replacing_sleeping_ashe_retires_body_local_control(self):
        run, ana, ashe = self.arena()
        fight = run.match.combat
        started, _ = self.cast(run, ana, ashe)
        retired = ashe.body
        while run.frame < started + 10:
            run.step({})
        self.assertIsNotNone(fight.sleep_view_angles(retired))
        self.assertTrue(run.match.switch_hero(ashe, heroes()[SOLDIER]))
        self.assertNotEqual(ashe.body, retired)
        self.assertNotIn(retired, fight.sleeps)
        self.assertNotIn(retired, fight.sleep_views)
        self.assertFalse(fight.is_stunned(ashe))

    def test_wall_and_ally_block_sleep_dart_damage_and_control(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, ana, ashe = self.arena(ally=ally)
                fight = run.match.combat
                if not ally:
                    run.match.collision_world = wall_scene(2)
                self.step(run, ana, ashe, cast=8)
                for _ in range(60):
                    self.step(run, ana, ashe)
                self.assertEqual(fight.vitals[ashe.body].total, 200)
                self.assertNotIn(ashe.body, fight.sleeps)

    def defender_arena(self, name, distance=4):
        run, ana, defender = self.arena()
        self.assertTrue(run.match.switch_hero(defender, find_hero(name)))
        for player, z in ((defender, 0), (ana, distance)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        for target in fight._targets():
            fight.vitals_of(target)
        return run, ana, defender

    def test_projected_barrier_spends_five_health_and_keeps_ashe_awake(self):
        barrier_test = controls.AsheBarrierTests()
        run, ana, zarya, ashe = barrier_test.projected_arena()
        self.assertTrue(run.match.switch_hero(ana, find_hero("ana")))
        self.assertTrue(run.match.switch_hero(ashe, find_hero("ashe")))
        zarya.team = ashe.team = 1 - ana.team
        for player, x, z in ((ana, 0, 0), (zarya, 4, 6), (ashe, 0, 6)):
            player.mover.position[:] = (x, 1, z)
            player.mover.body = None
        for _ in range(35):
            run.step({})
        direction, projected = barrier_test.projected_input(run, zarya, ashe)
        run.step({zarya: (*direction, 16)})
        self.assertTrue(projected.states[10].active)
        self.step(run, ana, ashe, cast=8)
        for _ in range(60):
            self.step(run, ana, ashe)
        fight = run.match.combat
        self.assertEqual(fight.personal_barriers[ashe.body][2], 195)
        barrier_test.assert_energy_credit(zarya, 5, ana.body, 1)
        self.assertEqual(fight.vitals[ashe.body].total, 200)
        self.assertEqual(fight.sleeps, {})
        self.assertFalse(fight.rockets)
        self.assertEqual(ana.client.messages(combat.HIT), [])

    def test_winston_shell_stops_crossing_dart_but_allows_inside_dart(self):
        for distance in (2, 8):
            with self.subTest(distance=distance):
                run, ana, winston = self.defender_arena("winston", distance)
                barrier = controls.AsheBarrierTests().deploy_winston(run, winston, ana)
                fight = run.match.combat
                before = fight.vitals[winston.body].total
                self.step(run, ana, winston, cast=8)
                for _ in range(60):
                    self.step(run, ana, winston)
                self.assertFalse(fight.rockets)
                self.assertEqual(barrier.health, 695 if distance == 8 else 700)
                self.assertEqual(fight.vitals[winston.body].total, before - (2.5 if distance == 2 else 0))
                self.assertEqual(winston.body in fight.sleeps, distance == 2)

    def test_kinetic_grasp_consumes_the_dart_and_credits_its_authored_amount(self):
        run, ana, sigma = self.defender_arena("sigma")
        fight = run.match.combat
        before = fight.vitals[sigma.body].total
        self.step(run, sigma, ana, cast=8)
        script = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(script.node_state(script.graph.node(7)).active)
        self.step(run, ana, sigma, cast=8)
        for _ in range(60):
            self.step(run, ana, sigma)
        self.assertEqual(script.find_var(0, 21312).value(), 5)
        self.assertEqual(fight.vitals[sigma.body].total, before)
        self.assertEqual(fight.sleeps, {})
        self.assertFalse(fight.rockets)
        self.assertEqual(ana.client.messages(combat.HIT), [])

    def test_deflect_returns_sleep_dart_to_ashe_and_credits_genji(self):
        run, ana, genji, ashe = controls.AsheBarrierTests().projected_arena()
        for actor, name in ((ana, "ana"), (genji, "genji"), (ashe, "ashe")):
            self.assertTrue(run.match.switch_hero(actor, find_hero(name)))
        ashe.team = ana.team = 1 - genji.team
        for actor, x, z in ((genji, 0, 0), (ana, 0, 8), (ashe, 0.85, 5)):
            actor.mover.position[:] = (x, 1, z)
            actor.mover.body = None
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        for target in fight._targets():
            fight.vitals_of(target)
        reflect = aim(fight._eye(genji), (0.85, 2.2, 5))
        launch = aim(fight._eye(ana), (0, 2.2, 0))
        run.step({genji: (*reflect, 16)})
        run.step({genji: (*reflect, 0), ana: (*launch, 8)})
        for _ in range(60):
            run.step({genji: (*reflect, 0), ana: (*launch, 0)})
        self.assertEqual(fight.vitals[ashe.body].total, 195)
        self.assertEqual(fight.vitals[ana.body].total, 200)
        self.assertEqual(fight.vitals[genji.body].total, 200)
        self.assertEqual(set(fight.sleeps), {ashe.body})
        self.assertFalse(fight.rockets)
        hit, = genji.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], ashe.body)
        self.assertEqual(hit["+0x78"]["+0x4"], 5)
        self.assertEqual(ana.client.messages(combat.HIT), [])

    def test_defense_matrix_consumes_dart_without_damage_or_sleep(self):
        run, dva, ana, matrix = controls.AsheMatrixTests().matrix_arena()
        self.assertTrue(run.match.switch_hero(ana, find_hero("ana")))
        ana.mover.position[:] = (0, 1, 4)
        ana.mover.body = None
        fight = run.match.combat
        for _ in range(35):
            run.step({})
        for target in fight._targets():
            fight.vitals_of(target)
        before = fight.vitals[dva.body].total
        self.step(run, ana, dva, target_buttons=2)
        self.step(run, ana, dva, cast=8, target_buttons=2)
        for _ in range(50):
            self.step(run, ana, dva, target_buttons=2)
        self.assertEqual(matrix.vars[6584].value(), 5)
        self.assertEqual(matrix.vars[41].value().id, ana.body)
        self.assertEqual(fight.vitals[dva.body].total, before)
        self.assertEqual(fight.sleeps, {})
        self.assertFalse(fight.rockets)
        self.assertEqual(ana.client.messages(combat.HIT), [])

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_COLLISION"),
                         "requires the authored Sigma collision mesh")
    def test_experimental_barrier_stops_sleep_dart_before_sigma(self):
        run, ana, sigma = self.defender_arena("sigma", 13)
        fight = run.match.combat
        shield = fight.sigma_barriers.actors[sigma.body]
        for _ in range(12):
            self.step(run, sigma, ana, cast=2)
        for _ in range(12):
            self.step(run, sigma, ana)
        self.assertEqual(shield.mode, 3)
        self.assertEqual(shield.health, 700)
        before = fight.vitals[sigma.body].total
        self.step(run, ana, sigma, cast=8)
        for _ in range(60):
            self.step(run, ana, sigma)
        self.assertEqual(shield.health, 695)
        self.assertEqual(fight.vitals[sigma.body].total, before)
        self.assertFalse(fight.sleeps)
        self.assertFalse(fight.rockets)
        self.assertEqual(ana.client.messages(combat.HIT), [])

    def test_overlapping_sleep_and_flashbang_hold_empty_viper_reload_until_both_end(self):
        for late_flash in (False, True):
            with self.subTest(late_flash=late_flash):
                run, ana, ashe = self.arena()
                fight = run.match.combat
                component = ashe.body_script.component
                weapon = next(i for i in component.instances.values() if i.graph.index == 0x14FC)
                for shot in range(12):
                    for _ in range(5):
                        run.step({ashe: (16384, 0, 1)})
                    if shot < 11:
                        for _ in range(75):
                            run.step({})
                ammo = weapon.vars[53]
                self.assertEqual(ammo.value(), 0)
                started, natural_end = self.cast(run, ana, ashe)
                reload = weapon.node_state(weapon.graph.node(65))
                self.assertTrue(reload.under)
                self.assertFalse(weapon.states[75].active)
                self.assertEqual(ammo.value(), 0)

                self.assertTrue(run.match.switch_hero(ana, heroes()[controls.MCCREE]))
                ana.mover.position[:] = (0, 1, 3)
                ana.mover.body = None
                while run.frame < started + (50 if late_flash else 5):
                    self.step(run, ana, ashe)
                self.step(run, ana, ashe, cast=16)
                for _ in range(45):
                    if ashe.body in fight.stuns:
                        break
                    self.step(run, ana, ashe)
                flash_end = fight.stuns[ashe.body]
                sleep_end = fight.sleeps[ashe.body][1]
                self.assertEqual(fight.vitals[ashe.body].total, 170)
                self.assertEqual(sleep_end, run.frame + 32 if late_flash else natural_end)
                first, last = sorted((sleep_end, flash_end))
                self.assertNotEqual(first, last)

                while run.frame < last - 1:
                    if run.frame + 1 == first:
                        previous = (ashe.mover.yaw, ashe.mover.pitch)
                        requested = (8192, -1024)
                        run.step({ashe: (*requested, 1 | 8 | 16)})
                        self.assertEqual(ashe.body in fight.sleeps, not late_flash)
                        self.assertEqual(ashe.body in fight.stuns, late_flash)
                        expected = requested if late_flash else previous
                        self.assertEqual((ashe.mover.yaw, ashe.mover.pitch), expected)
                    else:
                        self.step(run, ana, ashe, target_buttons=1 | 8 | 16, forward=127)
                    self.assertTrue(fight.is_stunned(ashe))
                    self.assertTrue(reload.under)
                    self.assertEqual(ammo.value(), 0)
                self.step(run, ana, ashe)
                self.assertFalse(fight.is_stunned(ashe))
                self.assertTrue(reload.top)
                self.assertTrue(weapon.states[75].active)
                self.assertEqual(ammo.value(), 0)
                for _ in range(40):
                    self.step(run, ana, ashe)
                self.assertEqual(ammo.value(), 1)
                self.assertIsNone(component.fault)

    @unittest.skipUnless(gun_fixtures.EXTENSION.is_file(), "requires B.O.B. metadata")
    def test_sleep_pauses_bob_gun_and_keeps_expiry_cadence_across_skipped_ticks(self):
        results = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, ana, actor = gun_fixtures.BobTests().cast()
                self.assertTrue(run.match.switch_hero(ana, find_hero("ana")))
                ana.mover.position[:] = (0, 1, 7)
                ana.mover.body = None
                fight = run.match.combat
                while run.frame < actor.ready + 20:
                    run.step({})
                self.assertGreater(actor.shots, 0)
                original_expiry = actor.until
                _, deadline = self.cast(run, ana, actor.bot)
                self.assertLess(deadline + 18, original_expiry)
                self.assertEqual(fight.vitals[actor.bot.entity].total, 1195)
                health = fight.vitals[ana.body].total
                shots = actor.shots
                count = len(owner.client.messages(combat.HIT))
                while run.frame < deadline - 10:
                    self.step(run, ana, actor.bot)
                    self.assertEqual(actor.shots, shots)
                    self.assertFalse(actor.bot.firing)
                self.assertEqual(fight.vitals[ana.body].total, health)

                if skipped:
                    run.frame = deadline + 18
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    while run.frame < deadline + 18:
                        self.step(run, ana, actor.bot)
                hits = owner.client.messages(combat.HIT)[count:]
                results.append([(hit["+0x84"] - deadline, hit["+0x78"]["+0x4"]) for hit in hits])
                self.assertEqual(actor.shots, shots + 3)
                self.assertEqual(fight.vitals[ana.body].total, health - 42)
                self.assertEqual(actor.until, original_expiry)
                self.assertNotIn(actor.bot.entity, fight.sleeps)
        self.assertEqual(results, [[(0, 14), (7, 14), (15, 14)]] * 2)

    @unittest.skipUnless(charge_fixtures.EXTRA.is_file(), "requires B.O.B. movement profiles")
    def test_sleep_stops_bob_motion_and_charge_still_times_out(self):
        fixture = charge_fixtures.BobChargeTests()
        run, owner, ana = fixture.setup(distance=18)
        self.assertTrue(run.match.switch_hero(ana, find_hero("ana")))
        ana.mover.position[:] = (0, 1, 18)
        ana.mover.body = None
        for _ in range(40):
            run.step({})
        actor = fixture.begin(run, owner)
        fight = run.match.combat
        _, deadline = self.cast(run, ana, actor.bot)
        self.assertEqual(actor.phase, "charge")
        self.assertLess(actor.charge_until, deadline)
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1195)
        position = actor.bot.position
        while run.frame < actor.charge_until - 1:
            self.step(run, ana, actor.bot)
            self.assertEqual(actor.bot.position, position)
            self.assertIsNone(actor.stopped_reason)
        self.step(run, ana, actor.bot)
        self.assertEqual(actor.stopped_reason, "timeout")
        self.assertIsNone(actor.uppercut_target)
        self.assertEqual(actor.phase, "settle")
        while run.frame < deadline - 1:
            self.step(run, ana, actor.bot)
            self.assertEqual(actor.shots, 0)
            self.assertFalse(actor.bot.firing)
        self.assertEqual(fight.vitals[ana.body].total, 200)
        self.step(run, ana, actor.bot)
        self.assertEqual(actor.phase, "gun")
        self.assertEqual(actor.shots, 1)
        self.assertEqual(fight.vitals[ana.body].total, 186)

    @unittest.skipUnless(gun_fixtures.EXTENSION.is_file(), "requires B.O.B. metadata")
    def test_bob_lifetime_expires_before_sleep_and_retires_both(self):
        run, owner, ana, actor = gun_fixtures.BobTests().cast()
        self.assertTrue(run.match.switch_hero(ana, find_hero("ana")))
        ana.mover.position[:] = (0, 1, 7)
        ana.mover.body = None
        match = run.match
        fight = match.combat
        open_world = match.collision_world
        match.collision_world = wall_scene(4)
        while run.frame < actor.until - 100:
            run.step({})
        self.assertEqual(actor.shots, 0)
        match.collision_world = open_world
        _, deadline = self.cast(run, ana, actor.bot)
        self.assertGreater(deadline, actor.until)
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1195)
        shots = actor.shots
        health = fight.vitals[ana.body].total
        while run.frame < actor.until - 1:
            self.step(run, ana, actor.bot)
        self.assertIn(actor.bot, match.bots)
        self.assertTrue(fight.is_stunned(actor.bot))
        run.step({})
        self.assertNotIn(actor.bot, match.bots)
        self.assertFalse(fight.companions.active)
        self.assertNotIn(actor.bot.entity, fight.sleeps)
        self.assertNotIn(actor.bot.entity, fight.vitals)
        self.assertEqual(actor.shots, shots)
        self.assertEqual(fight.vitals[ana.body].total, health)
        self.assertEqual(owner.body_script.component.entity_var(512).value().id, 0)

    def test_sleep_view_samples_after_authored_wait_and_unlocks_on_wake(self):
        for rifle_wake in (False, True):
            with self.subTest(rifle_wake=rifle_wake):
                run, ana, ashe = self.arena()
                fight = run.match.combat
                started, deadline = self.cast(run, ana, ashe)
                if rifle_wake:
                    self.assertTrue(run.match.switch_hero(ana, heroes()[SOLDIER]))
                    ana.mover.position[:] = (0, 1, 4)
                self.assertIsNone(fight.sleep_view_angles(ashe.body))
                # Opposite signed yaw values exercise the native wrap branch.
                pose = (-32000, 8000)
                while run.frame < started + 9:
                    run.step({ashe: (*pose, 0)})
                    self.assertIsNone(fight.sleep_view_angles(ashe.body))
                self.assertEqual((ashe.mover.yaw, ashe.mover.pitch), pose)
                request = (31000, -7000)
                run.step({ashe: (*request, 0)})
                self.assertEqual(run.frame, started + 10)
                angles = fight.sleep_view_angles(ashe.body)
                self.assertIsNotNone(angles)
                self.assertEqual(angles[:2], angles[2:4])
                self.assertEqual(angles[2:4], angles[4:])
                self.assertEqual((ashe.mover.yaw, ashe.mover.pitch), pose)

                if rifle_wake:
                    while run.frame < started + 50:
                        run.step({ashe: (*request, 0)})
                    fire = aim(fight._eye(ana), (0, 2, 0))
                    run.step({ana: (*fire, 1), ashe: (*request, 0)})
                    self.assertEqual(fight.vitals[ashe.body].total, 175)
                    deadline = run.frame + 32
                    self.assertEqual(fight.sleeps[ashe.body], (started, deadline))

                while run.frame < deadline - 1:
                    run.step({ashe: (*request, 0)})
                self.assertEqual(fight.sleep_view_angles(ashe.body), angles)
                self.assertEqual((ashe.mover.yaw, ashe.mover.pitch), pose)
                run.step({ashe: (*request, 0)})
                self.assertNotIn(ashe.body, fight.sleep_views)
                self.assertEqual((ashe.mover.yaw, ashe.mover.pitch), request)
