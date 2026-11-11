"""Actual Adaptive Shield inputs and Ana contacts in the isolated match."""

import unittest

import test_game_ana as fixture
import test_game_ana_orisa_barrier as placement
import test_game_ana_sound_barrier as sound_fixture
from test_game_combat import Run, aim

from ow174.game import adaptive_shield, sound_barrier, stats
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.world import HEALTH_POOL


class AnaAdaptiveShieldTests(unittest.TestCase):
    def arena(self, *, distance=3, enemy=True):
        run, ana, ball, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy, distance=distance)
        self.assertTrue(run.match.switch_hero(ball, heroes()[adaptive_shield.HERO]))
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(ball, (x, y, z + distance))
        for _ in range(30):
            run.step({})
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        return run, ana, ball

    @staticmethod
    def entry(run, ball):
        return next(target for target in run.match.combat._targets() if target.owner is ball)

    @staticmethod
    def instance(ball):
        return next(i for i in ball.body_script.component.instances.values()
                    if i.graph.index == adaptive_shield.GRAPH)

    def cast(self, run, ball, expected):
        run.step({ball: (0, 0, 16)})
        instance = self.instance(ball)
        pool = run.match.combat.vitals_of(self.entry(run, ball)).adaptive_shield
        self.assertIsNotNone(pool)
        self.assertEqual(pool.capacity, expected)
        self.assertEqual(pool.amount, expected)
        self.assertGreater(instance.find_var(0, 61).value(), 14.9)
        return pool

    def test_actual_cast_counts_only_nearby_enemies_including_behind_cover(self):
        for distance, enemy, covered, expected in (
            (3, True, False, 175), (8.25, True, False, 175), (9, True, False, 100),
            (3, False, False, 100), (6, True, True, 175),
        ):
            with self.subTest(distance=distance, enemy=enemy, covered=covered):
                run, ana, ball = self.arena(distance=distance, enemy=enemy)
                if covered:
                    fixture.set_static_wall(run, ana, 3)
                self.cast(run, ball, expected)
                self.assertEqual(self.instance(ball).find_var(0, 11398).value(), (expected - 100) // 75)

    def test_cast_uses_eye_origin_at_the_vertical_range_boundary(self):
        for enemy_above, expected in ((True, 175), (False, 100)):
            with self.subTest(enemy_above=enemy_above):
                run, ana, ball = self.arena(distance=.5)
                raised = ana if enemy_above else ball
                x, y, z = raised.mover.position
                placement.AnaOrisaBarrierTests.place(raised, (x, y + 9, z))
                self.cast(run, ball, expected)
                self.assertGreater(raised.mover.position[1], y + 8)

    def test_cast_snapshots_multiple_enemies_and_never_refills_on_target_changes(self):
        run = Run(fixture.TEAM_MAP, ("Ball", "Enemy1", "Enemy2", "Ally"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        ball, first, second, ally = run.players
        self.assertTrue(run.match.switch_hero(ball, heroes()[adaptive_shield.HERO]))
        x, y, z = ball.mover.position
        for player, team, point in (
            (ball, 0, (x, y, z)), (first, 1, (x, y, z + 3)), (second, 1, (x, y, z - 3)),
            (ally, 0, (x + 2, y, z)),
        ):
            player.team = team
            placement.AnaOrisaBarrierTests.place(player, point)
        for _ in range(30):
            run.step({})
        pool = self.cast(run, ball, 250)
        placement.AnaOrisaBarrierTests.place(second, (x, y, z - 20))
        run.step({})
        run.step({ball: (0, 0, 16)})
        self.assertIs(run.match.combat.vitals_of(self.entry(run, ball)).adaptive_shield, pool)
        self.assertEqual(pool.amount, 250)

    def test_actual_ana_contacts_drain_pool_without_charge_and_keep_status(self):
        for button, held, damage in ((1, 0, 70), (1, 2, 70), (8, 0, 5), (16, 0, 50), (0x800, 0, 30)):
            with self.subTest(button=button, scoped=bool(held)):
                run, ana, ball = self.arena(distance=1.5 if button == 0x800 else 3)
                pool = self.cast(run, ball, 175)
                fight = run.match.combat
                entry = self.entry(run, ball)
                x, y, z = entry.feet
                direction = aim(fight._eye(ana), (x, y + 1, z))
                for _ in range(35 if held else 0):
                    run.step({ana: (*direction, held)})
                run.step({ana: (*direction, held | button)})
                for _ in range(65):
                    run.step({ana: (*direction, held)})
                self.assertAlmostEqual(pool.amount, 175 - damage)
                vitals = fight.vitals_of(entry)
                self.assertEqual(sum(vitals.now.values()), 600)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(ana.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(fight.is_asleep(ball), button == 8)
                self.assertEqual(vitals.healing_block_until > fight.now, button == 16)

    def test_rifle_damage_overflow_credits_only_ordinary_pools(self):
        run, ana, ball = self.arena()
        pool = self.cast(run, ball, 175)
        entry = self.entry(run, ball)
        for _ in range(3):
            fixture.AnaRifleTests().shoot(run, ana, entry, False)
        self.assertEqual(pool.amount, 0)
        self.assertEqual(run.match.combat.vitals_of(entry).total, 565)
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 210)
        self.assertEqual(ana.body_script.component.entity_var(ULT_CHARGE).value(), 35)

    def test_friendly_rifle_heals_health_without_refilling_the_temporary_pool(self):
        run, ana, ball = self.arena(enemy=False)
        pool = self.cast(run, ball, 100)
        entry = self.entry(run, ball)
        vitals = run.match.combat.vitals_of(entry)
        health = vitals.now[HEALTH_POOL]
        vitals.now[HEALTH_POOL] = health - 100
        pool.amount = 50
        fixture.AnaRifleTests().shoot(run, ana, entry, False)
        self.assertEqual(vitals.now[HEALTH_POOL], health - 30)
        self.assertEqual(pool.amount, 50)
        self.assertEqual(ana.body_script.component.entity_var(ULT_CHARGE).value(), 70)

    def test_no_input_expiry_and_reentry_respect_authored_cooldown(self):
        run, _, ball = self.arena()
        pool = self.cast(run, ball, 175)
        fight = run.match.combat
        cast_frame = ball.body_script.frame
        start = run.frame
        run.frame = start + 562
        run.match.update(run.frame * .016, run.frame, frames=False)
        self.assertEqual(pool.amount, 175)
        run.frame = start + 564
        run.match.update(run.frame * .016, run.frame, frames=False)
        self.assertEqual(pool.amount, 0)
        self.assertEqual(ball.body_script.frame, cast_frame)
        run.step({ball: (0, 0, 16)})
        self.assertIs(fight.vitals_of(self.entry(run, ball)).adaptive_shield, pool)
        run.frame = start + 1000
        run.match.update(run.frame * .016, run.frame, frames=False)
        run.step({})
        self.cast(run, ball, 175)

    def test_sleep_gates_cast_and_replacement_removes_an_active_pool(self):
        run, ana, ball = self.arena()
        fight = run.match.combat
        entry = self.entry(run, ball)
        x, y, z = entry.feet
        direction = aim(fight._eye(ana), (x, y + 1, z))
        run.step({ana: (*direction, 8)})
        for _ in range(45):
            run.step({})
        self.assertTrue(fight.is_asleep(ball))
        run.step({ball: (0, 0, 16)})
        self.assertIsNone(fight.vitals_of(entry).adaptive_shield)
        self.assertEqual(self.instance(ball).find_var(0, 61).value(), 0)
        for _ in range(380):
            run.step({})
        self.cast(run, ball, 175)
        old = ball.body
        self.assertTrue(run.match.switch_hero(ball, heroes()[fixture.ANA]))
        self.assertNotIn(old, fight.vitals)
        self.assertIsNone(fight.vitals_of(self.entry(run, ball)).adaptive_shield)

    def test_sound_barrier_and_adaptive_shield_keep_independent_amounts_and_credit(self):
        run = Run(fixture.TEAM_MAP, ("Ana", "Ball", "Lucio"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        ana, ball, lucio = run.players
        for player, hero in ((ana, fixture.ANA), (ball, adaptive_shield.HERO), (lucio, sound_barrier.HERO)):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
        ana.team, ball.team, lucio.team = 0, 1, 1
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(ball, (x, y, z + 3))
        placement.AnaOrisaBarrierTests.place(lucio, (x + 3, y, z + 3))
        for _ in range(30):
            run.step({})
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        sound = sound_fixture.AnaSoundBarrierTests()
        sound.cast(run, lucio)
        sound.land(run, lucio)
        pool = self.cast(run, ball, 175)
        entry = self.entry(run, ball)
        fight = run.match.combat
        vitals = fight.vitals_of(entry)
        before = vitals.sound_shield.amount
        at = fight.now
        for _ in range(3):
            fixture.AnaRifleTests().shoot(run, ana, entry, False)
        self.assertEqual(pool.amount, 0)
        self.assertAlmostEqual(before - vitals.sound_shield.amount - (fight.now - at) * 125, 35)
        self.assertEqual(sum(vitals.now.values()), 600)
        self.assertEqual(ana.body_script.component.entity_var(ULT_CHARGE).value(), 35)

    def test_active_adaptive_pool_is_causal_in_the_server_snapshot(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene()
        ball, = players
        peer, = peers
        peer.message(21616, stored.pick(ball.entity, adaptive_shield.HERO), now=101.0)
        session.tick(101.0)
        for index in range(12):
            now = 101.016 + index * .016
            session.tick(now)
            peer.commands(ball.body_script.frame + 1, 4, 0, now=now)
        peer.commands(ball.body_script.frame + 1, 4, 16, now=now)
        pool = ball.match.combat.vitals[ball.body].adaptive_shield
        self.assertIsNotNone(pool)
        active = stored.snapshot_digest(case.capture(session))
        pool.amount -= 5
        self.assertNotEqual(active, stored.snapshot_digest(case.capture(session)))
        pool.amount += 5
        self.assertEqual(active, stored.snapshot_digest(case.capture(session)))
        pool.until += .016
        self.assertNotEqual(active, stored.snapshot_digest(case.capture(session)))
