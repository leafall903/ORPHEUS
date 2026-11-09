"""Manual heavy engagement executes authored motion and impact against Zen."""
import math
import unittest

import test_zenyatta_npcs as scenes
from test_game_combat import aim

from ow174.game import heavy_assault, npc_control
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.content import heroes


class HeavyCharge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scenes.ZenyattaNpcTests.setUpClass()
        cls.addClassCleanup(scenes.ZenyattaNpcTests.tearDownClass)

    def test_actual_charge_moves_and_damages_zen(self):
        run, zen, bot, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+10)
        start = bot.position
        for _ in range(35):
            run.step({})
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        fight = run.match.combat
        saw_phase = False
        for _ in range(140):
            run.step({})
            phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
            moving = phase.states.get(phase.graph.node(71).state)
            if moving is not None and moving.active and not saw_phase:
                for logical, radius in ((117, 3), (145, 2)):
                    node = phase.graph.node(logical)
                    explosion = phase.states[node.state]
                    self.assertEqual(explosion.counter, 1)
                    self.assertEqual(explosion.evaluate(node.field("m_radius"), watch=False), radius)
                self.assertEqual(fight.vitals[zen.body].total, 200)
            saw_phase |= bool(moving and moving.active)
            if fight.vitals[zen.body].total < 200:
                break
        hp = fight.vitals[zen.body].total
        self.assertTrue(saw_phase)
        self.assertGreater(math.dist(bot.position, start), 8)
        self.assertEqual(hp, 25)

    def test_projected_bubble_absorbs_charge_impact_before_body_damage(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, _ = fixture.arena(fixture.catalog["5"], names=("Zen", "Zarya"))
        zarya = run.players[1]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.team = zen.team
        x, y, z = zen.mover.position
        zarya.mover.position = [x+4, y, z]
        zarya.mover.body = None
        bot.position = (x, y, z+10)
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        for _ in range(75):
            run.step({})
            if bot.special_mover is not None:
                break
        self.assertIsNotNone(bot.special_mover)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        direction = aim(fight._eye(zarya), (x, y+1, z))
        run.step({zarya: (*direction, 16)})
        bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
        self.assertEqual(bubble.target, zen.body)
        self.assertEqual(bubble.hp, 200)
        phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
        for _ in range(65):
            run.step({})
            impact = phase.states.get(phase.graph.node(102).state)
            if impact is not None and impact.counter:
                break
        self.assertTrue(impact and impact.counter)
        self.assertEqual(bubble.hp, 25)
        self.assertEqual(fight.vitals[zen.body].total, 200)

    def test_actual_dart_stops_charge_and_later_weapon_recovers(self):
        fixture = scenes.ZenyattaNpcTests()
        run, zen, bot, _ = fixture.arena(fixture.catalog["5"], names=("Zen", "Ana"))
        ana = run.players[1]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = zen.team
        x, y, z = zen.mover.position
        ana.mover.position = [x, y, z-4]
        ana.mover.body = None
        bot.position = (x, y, z+14)
        origin = bot.position
        for _ in range(35):
            run.step({})
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
        for _ in range(90):
            run.step({})
            if math.dist(bot.position, origin) > 1:
                break
        self.assertGreater(math.dist(bot.position, origin), 1)
        self.assertIsNotNone(bot.special_mover)
        bx, by, bz = bot.position
        direction = aim(fight._eye(ana), (bx, by+1, bz))
        run.step({ana: (*direction, 8)})
        for _ in range(55):
            run.step({ana: (*direction, 0)})
            if bot.entity in fight.sleeps:
                break
        self.assertIn(bot.entity, fight.sleeps)
        run.step({})
        self.assertIsNone(bot.special_mover)
        stopped = bot.position
        hp = fight.vitals[zen.body]
        self.assertEqual(hp.total, 200)
        end = fight.sleeps[bot.entity][1]
        while run.frame < end-1:
            run.step({})
            self.assertEqual(bot.position, stopped)
            self.assertEqual(hp.total, 200)
            self.assertFalse(bot.attack_input)
        impact = phase.states.get(phase.graph.node(102).state)
        self.assertFalse(impact and impact.counter)
        for _ in range(400):
            run.step({})
            if hp.total < 200:
                break
        self.assertNotIn(bot.entity, fight.sleeps)
        self.assertLess(hp.total, 200)
        self.assertGreater(hp.total, 0)

    def test_stop_midcharge_cancels_movement_damage_and_starts_cooldown(self):
        run, zen, bot, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+10)
        start = bot.position
        for _ in range(35):
            run.step({})
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        for _ in range(75):
            run.step({})
            if math.dist(bot.position, start) > 2:
                break
        self.assertGreater(math.dist(bot.position, start), 2)
        self.assertEqual(npc_control.stop(run.match), 1)
        stopped = bot.position
        ability = heavy_assault.ability(bot.body_script.component, 0x1408, 82)
        self.assertAlmostEqual(ability.remaining(), 15, delta=.1)
        for _ in range(90):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(run.match.combat.vitals[zen.body].total, 200)

    def test_charge_impact_does_not_damage_transcendent_zen(self):
        run, zen, bot, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+10)
        for _ in range(35):
            run.step({})
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20)})
        fight = run.match.combat
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        phase = heavy_assault.ability(bot.body_script.component, 0x1408, 82).instance
        impact = None
        for _ in range(100):
            run.step({})
            impact = phase.states.get(phase.graph.node(102).state)
            if impact is not None and impact.counter:
                break
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertIsNotNone(impact)
        self.assertGreater(impact.counter, 0)


    def test_wall_appearing_during_charge_cancels_impact(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World

        run, zen, bot, _ = scenes.ZenyattaNpcTests().arena(scenes.ZenyattaNpcTests.catalog["5"])
        x, y, z = zen.mover.position
        bot.position = (x, y, z+10)
        for _ in range(35):
            run.step({})
        self.assertEqual(npc_control.start(run.match, zen, special_attack=True), bot.entity)
        for _ in range(75):
            run.step({})
            if bot.position[2] < z+8:
                break
        self.assertLess(bot.position[2], z+8)
        boundary = z+4
        run.match.collision_world = World(
            box(x-25, x+25, y-2, y, z-25, z+25) + wall(x-8, boundary, x+8, boundary, y, y+5)
        )
        stopped = bot.position
        for _ in range(65):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(run.match.combat.vitals[zen.body].total, 200)
