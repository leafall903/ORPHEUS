"""Actual Sniper MoveTo and weapon lifecycle against a live Zen."""
import math
import unittest

import test_zenyatta_npcs as scenes
from test_game_combat import aim

from ow174.game import npc_control, sniper_reposition
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.content import heroes


class SniperReposition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scenes.ZenyattaNpcTests.setUpClass()
        cls.addClassCleanup(scenes.ZenyattaNpcTests.tearDownClass)

    def arena(self, *, with_zarya=False):
        fixture = scenes.ZenyattaNpcTests()
        names = ("Zen", "Zarya") if with_zarya else ("Zen",)
        run, zen, bot, _ = fixture.arena(fixture.catalog["3"], names=names)
        x, y, z = zen.mover.position
        bot.position = (x, y, z+2)
        if with_zarya:
            zarya = run.players[1]
            self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
            zarya.team = zen.team
            zarya.mover.position = [x+4, y, z]
            zarya.mover.body = None
        for _ in range(35):
            run.step({})
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        return run, zen, bot

    def test_actual_move_to_defers_shot_and_resumes_authored_fire(self):
        run, zen, bot = self.arena()
        start = bot.position
        state = sniper_reposition.state_of(bot)
        health = run.match.combat.vitals[zen.body]
        activated = False
        for _ in range(155):
            run.step({})
            if state.active and not activated:
                node = state.instance.graph.node(120)
                explosion = state.instance.states[node.state]
                self.assertEqual(explosion.counter, 1)
                self.assertAlmostEqual(explosion.evaluate(node.field("m_radius"), watch=False), .6)
            activated |= state.active
            self.assertEqual(health.total, 200)
            if activated and not state.active:
                break
        self.assertTrue(activated)
        self.assertFalse(state.active)
        self.assertIsNone(bot.special_mover)
        self.assertAlmostEqual(math.dist(start, bot.position), 4, places=2)
        for _ in range(360):
            run.step({})
            if health.total < 200:
                break
        self.assertLess(health.total, 200)
        self.assertEqual(state.counter, 1)

    def test_projected_bubble_intercepts_actual_post_reposition_shot(self):
        impact_step = damage = None
        for protected in (False, True):
            with self.subTest(protected=protected):
                if protected:
                    self.assertIsNotNone(impact_step)
                run, zen, bot = self.arena(with_zarya=True)
                zarya = run.players[1]
                x, y, z = zen.mover.position
                fight = run.match.combat
                move = sniper_reposition.state_of(bot)
                hp = fight.vitals[zen.body]
                bubble = None
                for step in range(500):
                    if protected and step == impact_step-12:
                        direction = aim(fight._eye(zarya), (x, y+1, z))
                        run.step({zarya: (*direction, 16)})
                        bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                        self.assertEqual(bubble.target, zen.body)
                        self.assertEqual(bubble.hp, 200)
                    else:
                        run.step({})
                    if not protected and hp.total < 200:
                        impact_step, damage = step, 200-hp.total
                        break
                    if protected and step == impact_step:
                        break
                self.assertEqual(move.counter, 1)
                self.assertFalse(move.active)
                self.assertIsNotNone(impact_step)
                self.assertEqual(damage, 100)
                if protected:
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.hp, 200-damage)
                    self.assertEqual(hp.total, 200)

    def test_real_dart_stops_active_move_and_weapon_recovers(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, _ = fixture.arena(fixture.catalog["3"], names=("Zen", "Ana"))
        ana = run.players[1]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = zen.team
        x, y, z = zen.mover.position
        ana.mover.position = [x, y, z+9]
        ana.mover.body = None
        bot.position = (x, y, z+2)
        origin = bot.position
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        direction = aim(fight._eye(ana), (x, y+1, z+2))
        run.step({ana: (*direction, 8)})
        for _ in range(13):
            run.step({ana: (*direction, 0)})
        self.assertNotIn(bot.entity, fight.sleeps)
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        move = sniper_reposition.state_of(bot)
        moved_before_hit = False
        for _ in range(40):
            moved_before_hit |= move.active
            run.step({ana: (*direction, 0)})
            if bot.entity in fight.sleeps:
                break
        self.assertIn(bot.entity, fight.sleeps)
        self.assertTrue(moved_before_hit)
        self.assertEqual(move.counter, 1)
        run.step({})
        self.assertFalse(move.active)
        self.assertIsNone(bot.special_mover)
        stopped = bot.position
        self.assertGreater(math.dist(stopped, origin), 0)
        self.assertLess(math.dist(stopped, origin), 4)
        hp = fight.vitals[zen.body]
        end = fight.sleeps[bot.entity][1]
        while run.frame < end-1:
            run.step({})
            self.assertEqual(bot.position, stopped)
            self.assertEqual(hp.total, 200)
            self.assertFalse(bot.attack_input)
        for _ in range(400):
            run.step({})
            if hp.total < 200:
                break
        self.assertNotIn(bot.entity, fight.sleeps)
        self.assertEqual(hp.total, 100)

    def test_stop_during_move_retires_pose_and_prevents_late_shot(self):
        run, zen, bot = self.arena()
        state = sniper_reposition.state_of(bot)
        start = bot.position
        for _ in range(90):
            run.step({})
            if math.dist(start, bot.position) > 1:
                break
        self.assertTrue(state.active)
        self.assertGreater(math.dist(start, bot.position), 1)
        self.assertEqual(npc_control.stop(run.match), 1)
        stopped = bot.position
        for _ in range(180):
            run.step({})
        self.assertFalse(state.active)
        self.assertIsNone(bot.special_mover)
        self.assertEqual(bot.position, stopped)
        self.assertEqual(run.match.combat.vitals[zen.body].total, 200)

    def test_reposition_shot_is_rejected_by_active_transcendence(self):
        run, zen, bot = self.arena()
        start = bot.position
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20)})
        fight = run.match.combat
        self.assertTrue(fight.zenyatta.ultimate(zen))
        for _ in range(330):
            run.step({})
            self.assertEqual(fight.vitals[zen.body].total, 200)
            if any(rocket.shooter is bot for rocket in fight.rockets):
                break
        else:
            self.fail("Sniper did not fire during Transcendence")
        for _ in range(20):
            run.step({})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertAlmostEqual(math.dist(start, bot.position), 4, places=2)

    def test_retreat_wall_blocks_destination_without_moving(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World

        run, _, bot = self.arena()
        x, y, z = bot.position
        run.match.collision_world = World(
            box(x-20, x+20, y-2, y, z-20, z+20) + wall(x-6, z+1, x+6, z+1, y, y+5)
        )
        state = sniper_reposition.state_of(bot)
        for _ in range(65):
            run.step({})
        self.assertFalse(state.active)
        self.assertEqual(state.counter, 0)
        self.assertEqual(bot.position, (x, y, z))
        self.assertIsNone(bot.special_mover)

    def test_replacement_body_retires_active_reposition(self):
        from ow174.game.content import SOLDIER, heroes

        run, zen, bot = self.arena()
        state = sniper_reposition.state_of(bot)
        start = bot.position
        for _ in range(90):
            run.step({})
            if math.dist(start, bot.position) > .8:
                break
        self.assertTrue(state.active)
        old_body = zen.body
        self.assertTrue(run.match.switch_hero(zen, heroes()[SOLDIER]))
        self.assertNotEqual(zen.body, old_body)
        stopped = bot.position
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is zen))
        initial = health.total
        for _ in range(200):
            run.step({})
        self.assertFalse(state.active)
        self.assertIsNone(bot.special_mover)
        self.assertIsNone(bot.special_target_body)
        self.assertEqual(bot.position, stopped)
        self.assertEqual(health.total, initial)

    def test_reapproach_does_not_reposition_during_cooldown(self):
        run, zen, bot = self.arena()
        state = sniper_reposition.state_of(bot)
        for _ in range(150):
            run.step({})
            if state.ended_at and not state.active:
                break
        self.assertEqual(state.counter, 1)
        self.assertFalse(state.active)
        x, y, z = bot.position
        zen.mover.position = [x, y, z-2]
        zen.mover.body = None
        stopped = bot.position
        for _ in range(100):
            run.step({})
            self.assertEqual(state.counter, 1)
        self.assertEqual(bot.position, stopped)
