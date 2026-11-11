"""Actual Mercy blaster shots against the original D.Va mech Matrix."""

import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import test_game_mercy_npc as npc_tests

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mercy_blaster_critical import equip

from ow174.game import defense_matrix, npc, npc_control, stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import find_hero
from ow174.game.script import graph

MECH = defense_matrix.MECH_BODY


class MercyMatrixTests(unittest.TestCase):
    def scenario(self):
        if graph.entity_body(MECH) is None or not defense_matrix.tags():
            self.skipTest("prepared 104319 mech graph and projectile tags required")
        run = Run(KANEZAKA_DM, ("Mercy", "DVa"))
        mercy, dva = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(mercy, find_hero("mercy")))
        self.assertTrue(run.match.switch_hero(dva, replace(find_hero("dva"), body=MECH)))
        mercy.mover.position[:] = [0, 1, 14]
        dva.mover.position[:] = [0, 1, 0]
        equip(run, mercy)
        instance = next(i for i in dva.body_script.component.instances.values() if i.graph.index == 0x02CC)
        target = next(t for t in fight._targets() if t.owner is dva)
        self.assertAlmostEqual(dva.mover.data.stand_radius, 0.7)
        self.assertEqual(fight.vitals_of(target).total, 600)
        return run, mercy, dva, instance, fight.vitals_of(target)

    def fire(self, run, mercy, dva, pose):
        fight = run.match.combat
        x, y, z = dva.mover.position
        yaw, pitch = aim(fight._eye(mercy), (x, y + 0.9, z))
        run.step({mercy: (yaw, pitch, FIRE), dva: pose})
        for _ in range(30):
            run.step({dva: pose})

    def test_front_matrix_absorbs_and_release_restores_body_damage(self):
        run, mercy, dva, instance, health = self.scenario()
        before = health.total
        for _ in range(4):
            run.step({dva: (0, 0, SECONDARY)})
        self.assertTrue(instance.state(3).active)
        self.fire(run, mercy, dva, (0, 0, SECONDARY))
        self.assertEqual(health.total, before)
        self.assertEqual(mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.projectiles.shots)
        self.assertLess(instance.vars[87].value(), 100)
        for _ in range(12):
            run.step({})
        self.assertFalse(instance.state(3).active)
        self.assertGreater(instance.vars[61].value(), 0)
        run.step({dva: (0, 0, SECONDARY)})
        self.assertFalse(instance.state(3).active)
        self.fire(run, mercy, dva, (0, 0, 0))
        self.assertLess(health.total, before)

    def test_matrix_stops_actual_trooper_hitscan_until_release(self):
        fixture = npc_tests.MercyNpcTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        run, mercy, dva, matrix, health = self.scenario()
        definition = next(d for d in fixture.catalog.values() if d.name == "Trooper")
        for player in (mercy, dva):
            player.client.bot_stream = Mock(return_value=None)
        bot = npc.spawn(run.match, definition, (0, 1, 8), team=1)
        pose = (0, 0, SECONDARY)
        for _ in range(4):
            run.step({dva: pose})
        self.assertTrue(matrix.state(3).active)

        def attack(buttons):
            self.assertEqual(npc_control.start(run.match, dva), bot.entity)
            fight = run.match.combat
            with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                for _ in range(90):
                    run.step({dva: (0, 0, buttons)})
                    if shoot.called:
                        break
            self.assertEqual(shoot.call_count, 1)
            state = shoot.call_args.args[2]
            self.assertEqual(state.instance.graph.index, 0x102E)
            self.assertFalse(fight.rockets)
            npc_control.stop(run.match)

        attack(SECONDARY)
        self.assertEqual(health.total, 600)
        for _ in range(50):
            run.step({})
            if not matrix.state(3).active:
                break
        self.assertFalse(matrix.state(3).active)
        attack(0)
        self.assertEqual(health.total, 585)

    def test_active_matrix_facing_away_leaves_body_hittable(self):
        run, mercy, dva, instance, health = self.scenario()
        before = health.total
        pose = (-32768, 0, SECONDARY)
        for _ in range(4):
            run.step({dva: pose})
        self.assertTrue(instance.state(3).active)
        self.fire(run, mercy, dva, pose)
        self.assertLess(health.total, before)


    def test_depletion_exposes_body_while_secondary_is_still_held(self):
        run, mercy, dva, instance, health = self.scenario()
        pose = (0, 0, SECONDARY)
        for _ in range(4):
            run.step({dva: pose})
        self.assertTrue(instance.state(3).active)
        for _ in range(145):
            run.step({dva: pose})
        self.assertFalse(instance.state(3).active)
        self.assertEqual(instance.vars[87].value(), 0)
        before = health.total
        self.fire(run, mercy, dva, pose)
        self.assertLess(health.total, before)
