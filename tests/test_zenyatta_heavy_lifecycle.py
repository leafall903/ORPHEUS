"""Actual heavy charge lifecycle and pre-command impulse observation."""
import unittest

import test_zenyatta_npcs as scenes
from test_game_combat import aim

from ow174.game import heavy_assault, npc_control
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.content import SOLDIER, heroes


class HeavyChargeLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scenes.ZenyattaNpcTests.setUpClass()
        cls.addClassCleanup(scenes.ZenyattaNpcTests.tearDownClass)

    def arena(self, *, ultimate=False, distance=10):
        run, zen, bot, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+distance)
        for _ in range(35):
            run.step({})
        if ultimate:
            script = zen.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
            run.step({zen: (0, 0, 0x20)})
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        return run, zen, bot

    def test_body_replacement_ends_active_charge_without_hitting_new_body(self):
        run, zen, bot = self.arena()
        start = bot.position[2]
        for _ in range(75):
            run.step({})
            if bot.position[2] < start-2:
                break
        self.assertLess(bot.position[2], start-2)
        old_body = zen.body
        self.assertTrue(run.match.switch_hero(zen, heroes()[SOLDIER]))
        self.assertNotEqual(zen.body, old_body)
        fight = run.match.combat
        hp = fight.vitals_of(next(t for t in fight._targets() if t.owner is zen)).total
        stopped = bot.position
        for _ in range(85):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertIsNone(bot.special_mover)
        self.assertIsNone(bot.special_target_body)
        self.assertEqual(fight.vitals[zen.body].total, hp)

    def test_charge_damage_node_has_no_authored_impulse(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                run, zen, bot = self.arena(ultimate=ultimate)
                phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
                for _ in range(105):
                    run.frame += 1
                    run.match.update(run.frame*.016, run.frame)
                    impact = phase.states.get(phase.graph.node(102).state)
                    if impact and impact.counter:
                        break
                self.assertIsNotNone(impact)
                self.assertGreater(impact.counter, 0)
                hp = run.match.combat.vitals[zen.body].total
                fields = impact.node.field("m_modifyHealth")
                print("impact", ultimate, hp, zen.mover.velocity,
                      {key: impact.evaluate(value, watch=False) for key, value in fields.items()
                       if value is not None and key.startswith("m_")}, flush=True)
                self.assertEqual(hp, 200 if ultimate else 25)
                self.assertEqual(zen.mover.velocity, (0.0, 0.0, 0.0))


    def test_close_attack_uses_authored_impulse_before_player_pose(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                run, zen, bot = self.arena(ultimate=ultimate, distance=2)
                phase = heavy_assault.ability(bot.body_script.component).instance
                for _ in range(60):
                    run.frame += 1
                    run.match.update(run.frame*.016, run.frame)
                    impact = phase.states.get(phase.graph.node(5).state)
                    if impact and impact.counter:
                        break
                self.assertIsNotNone(impact)
                self.assertGreater(impact.counter, 0)
                hp = run.match.combat.vitals[zen.body].total
                print("close impact", ultimate, hp, zen.mover.velocity, flush=True)
                self.assertEqual(hp, 200 if ultimate else 120)
                self.assertAlmostEqual(zen.mover.velocity[1], 8)
                self.assertAlmostEqual(zen.mover.velocity[2], -20)

    def test_projected_bubble_blocks_close_attack_damage_and_impulse(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, _ = fixture.arena(fixture.catalog["5"], names=("Zen", "Zarya"))
        zarya = run.players[1]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.team = zen.team
        x, y, z = zen.mover.position
        zarya.mover.position = [x+4, y, z]
        zarya.mover.body = None
        bot.position = (x, y, z+2)
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        direction = aim(fight._eye(zarya), (x, y+1, z))
        run.step({zarya: (*direction, 16)})
        bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
        self.assertEqual(bubble.target, zen.body)
        self.assertEqual(bubble.hp, 200)
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        phase = heavy_assault.ability(bot.body_script.component).instance
        before = tuple(zen.mover.velocity)
        for _ in range(60):
            run.frame += 1
            run.match.update(run.frame*.016, run.frame)
            impact = phase.states.get(phase.graph.node(5).state)
            if impact is not None and impact.counter:
                break
        self.assertTrue(impact and impact.counter)
        self.assertEqual(bubble.hp, 120)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertEqual(tuple(zen.mover.velocity), before)

    def test_actual_orb_kills_charging_heavy_and_keeps_corpse(self):
        from ow174.game.commands import FIRE

        run, zen, bot = self.arena()
        fight = run.match.combat
        vitals = fight.vitals[bot.entity]
        vitals.damage(vitals.total-1)
        start = bot.position[2]
        for _ in range(75):
            run.step({})
            if bot.position[2] < start-2:
                break
        self.assertIsNotNone(bot.special_mover)
        bx, by, bz = bot.position
        angles = aim(fight._eye(zen), (bx, by+1.2, bz))
        run.step({zen: (*angles, FIRE)})
        for _ in range(20):
            run.step({})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, zen.body)
        self.assertIsNone(bot.special_mover)
        self.assertIsNone(bot.special_target_body)
        self.assertIn(bot, run.match.bots)
        for _ in range(35):
            run.step({})
        self.assertEqual(fight.vitals[zen.body].total, 200)
