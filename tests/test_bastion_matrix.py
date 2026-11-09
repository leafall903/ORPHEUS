"""Actual Bastion command input against the authored 104319 mech Matrix."""
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_bastion_combat as kit

from ow174.game import combat, dva_matrix, mover, pools
from ow174.game.script import graph


class BastionMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = Path(os.environ["ORPHEUS_DVA_RUNTIME"]) / "data"
        cls.addClassCleanup(cls.clear_inputs)
        for module, attr, filename in ((graph, "GRAPHS_PATH", "game_graphs_174.json.gz"),
                                       (mover, "MOVERS_PATH", "game_movers_174.json")):
            mounted = patch.object(module, attr, data / filename)
            mounted.start()
            cls.addClassCleanup(mounted.stop)
        cls.clear_inputs()

    @staticmethod
    def clear_inputs():
        for load in (graph._data, graph._records, graph.graph, graph.mode_variables,
                     pools.hero_pools):
            load.cache_clear()
        mover._clear_movers()

    def arena(self, form):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 6), bystander_position=(0, 1, 9),
                     enemy_hero=dva_matrix.HERO)
        mech = case.enemy
        position = list(mech.mover.position)
        self.assertTrue(case.run.match.switch_hero(mech, replace(mech.hero, body=dva_matrix.MECH)))
        mech.mover.position[:] = position
        mech.mover.body = None
        self.assertEqual(case.health(mech).total, 600)
        if form == "sentry":
            case.configure()
        elif form == "tank":
            case.tank()
        else:
            case.step(0, 85)
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)
        self.assertEqual(instance.vars[971].value(), 100)
        return case, mech, instance

    def test_three_weapons_stop_in_active_front_matrix(self):
        for form in ("recon", "sentry", "tank"):
            with self.subTest(form=form):
                case, mech, instance = self.arena(form)
                yaw, pitch = case.command_aim
                for _ in range(12):
                    case.run.step({mech: (-32768, 0, 2)})
                self.assertIs(dva_matrix.active(case.fight, mech), instance.states[3])
                self.assertLess(instance.vars[87].value(), 100)
                bystander = case.health(case.bystander)
                before = bystander.total
                for frame in range(24):
                    attack = int(frame < (1 if form == "tank" else 10))
                    case.run.step({mech: (-32768, 0, 2), case.player: (yaw, pitch, attack)})
                self.assertEqual(case.health(mech).total, 600)
                self.assertEqual(bystander.total, before)
                self.assertFalse(case.player.client.messages(combat.HIT))
                self.assertFalse(case.fight.rockets)


    def test_rear_exposure_release_depletion_and_resource_reuse(self):
        for scenario in ("rear", "release", "depleted"):
            with self.subTest(scenario=scenario):
                case, mech, instance = self.arena("recon")
                facing = 0 if scenario == "rear" else -32768
                hold = {"rear": 12, "release": 32, "depleted": 155}[scenario]
                for _ in range(hold):
                    case.run.step({mech: (facing, 0, 2)})
                self.assertEqual(dva_matrix.active(case.fight, mech) is not None, scenario != "depleted")
                if scenario == "release":
                    case.run.step({mech: (facing, 0, 0)})
                    self.assertIsNone(dva_matrix.active(case.fight, mech))
                health = case.health(mech)
                for _ in range(10):
                    case.run.step({mech: (facing, 0, 2 if scenario == "rear" else 0),
                                   case.player: (*case.command_aim, 1)})
                self.assertLess(health.total, 600)
                if scenario == "rear":
                    continue
                spent = instance.vars[87].value()
                for _ in range(125):
                    case.run.step({mech: (-32768, 0, 0)})
                self.assertGreater(instance.vars[87].value(), spent)
                for _ in range(12):
                    case.run.step({mech: (-32768, 0, 2)})
                self.assertIsNotNone(dva_matrix.active(case.fight, mech))
                before = health.total
                for _ in range(10):
                    case.run.step({mech: (-32768, 0, 2), case.player: (*case.command_aim, 1)})
                self.assertEqual(health.total, before)

    def test_friendly_matrix_allows_shots_through_to_an_enemy(self):
        case, mech, _ = self.arena("recon")
        case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
        mech.team = case.player.team
        case.bystander.team = 3 if case.player.team != 3 else 2
        for _ in range(12):
            case.run.step({mech: (-32768, 0, 2)})
        self.assertIsNotNone(dva_matrix.active(case.fight, mech))
        health = case.health(case.bystander)
        before = health.total
        for _ in range(10):
            case.run.step({mech: (-32768, 0, 2), case.player: (*case.command_aim, 1)})
        self.assertLess(health.total, before)
        self.assertEqual(case.health(mech).total, 600)


    def test_matrix_cannot_consume_a_tank_shell_through_a_wall(self):
        case, mech, _ = self.arena("tank")
        for _ in range(12):
            case.run.step({mech: (-32768, 0, 2)})
        self.assertIsNotNone(dva_matrix.active(case.fight, mech))
        case.wall(1.5)
        health = case.health(case.player)
        before = health.total
        for frame in range(24):
            case.run.step({mech: (-32768, 0, 2), case.player: (*case.command_aim, int(frame == 0))})
        self.assertLess(health.total, before)
        self.assertEqual(case.health(mech).total, 600)
        self.assertFalse(case.fight.rockets)


if __name__ == "__main__":
    unittest.main()
