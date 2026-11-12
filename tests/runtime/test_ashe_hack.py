"""Build 104319 Sombra Hack against Ashe through real commands."""

import unittest

import test_ashe_combat as fixtures
import test_mccree_flashbang as flash_fixtures
from test_ashe_impulses import wall_scene

from ow174.game import sombra_hack
from ow174.game.content import HEROES_PATH, heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires build 104319 data")
class AsheHackTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    step = flash_fixtures.FlashbangTests.step

    def arena(self, *, distance=5, ally=False):
        run, ashe, sombra = self.fight(distance=distance, team_game=ally)
        self.assertTrue(run.match.switch_hero(sombra, heroes()[sombra_hack.SOMBRA]))
        sombra.mover.position[:] = (0, ashe.mover.position[1], distance)
        if ally:
            sombra.team = ashe.team
        for _ in range(50):
            run.step({})
        return run, sombra, ashe

    def cast(self, run, sombra, ashe):
        for _ in range(65):
            self.step(run, sombra, ashe, cast=2)
            if sombra_hack.locked(ashe):
                return sombra_hack.instance_for(ashe, 0x0B1F).states[58]
        self.fail("actual Hack did not apply")

    def test_hack_locks_ashe_abilities_but_preserves_ads_reload_and_movement(self):
        run, sombra, ashe = self.arena()
        fight = run.match.combat
        receiver = self.cast(run, sombra, ashe)
        self.assertEqual(receiver.ms, 5000)
        self.assertFalse(fight.is_stunned(ashe))
        self.assertEqual(fight.vitals[ashe.body].total, 200)
        script = ashe.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        weapon = sombra_hack.instance_for(ashe, 0x14FC)
        position = tuple(ashe.mover.position)

        for i in range(48):
            buttons = 2 | 8 | 16 | 32 | (1 if i >= 24 else 0)
            self.step(run, sombra, ashe, target_buttons=buttons, forward=127 if i < 4 else 0)
        self.assertNotEqual(tuple(ashe.mover.position), position)
        self.assertEqual(fight.vitals[sombra.body].total, 50)
        self.assertEqual(weapon.vars[53].value(), 11)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertFalse(fight.companions.active)
        self.assertFalse(fight.dynamite.grenades)

        for _ in range(60):
            self.step(run, sombra, ashe, target_buttons=0x400)
        self.assertEqual(weapon.vars[53].value(), 12)
        deadline = (receiver.start + receiver.ms) // script.frame_ms
        while run.frame < deadline - 1:
            self.step(run, sombra, ashe)
        self.assertTrue(sombra_hack.locked(ashe))
        self.step(run, sombra, ashe, target_buttons=32)
        self.assertFalse(sombra_hack.locked(ashe))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        self.step(run, sombra, ashe)
        self.step(run, sombra, ashe, target_buttons=32)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_viper_damage_interrupts_live_hack_channel(self):
        run, sombra, ashe = self.arena()
        for _ in range(20):
            self.step(run, sombra, ashe, cast=2)
        channel = sombra_hack.instance_for(sombra, 0x0B1B).states[54]
        self.assertTrue(channel.active)
        self.step(run, sombra, ashe, cast=2, target_buttons=1)
        self.assertEqual(run.match.combat.vitals[sombra.body].total, 120)
        self.assertFalse(channel.active)
        for _ in range(50):
            self.step(run, sombra, ashe)
        self.assertIsNone(sombra_hack.instance_for(ashe, 0x0B1F))

    def test_hack_rejects_allies_and_targets_beyond_range_or_behind_terrain(self):
        for case in ("ally", "range", "terrain"):
            with self.subTest(case=case):
                run, sombra, ashe = self.arena(distance=30 if case == "range" else 5, ally=case == "ally")
                if case == "terrain":
                    run.match.collision_world = wall_scene(2.5)
                for _ in range(65):
                    self.step(run, sombra, ashe, cast=2)
                self.assertIsNone(sombra_hack.instance_for(ashe, 0x0B1F))

    def test_live_channel_aborts_when_terrain_breaks_line_of_sight(self):
        run, sombra, ashe = self.arena()
        for _ in range(20):
            self.step(run, sombra, ashe, cast=2)
        channel = sombra_hack.instance_for(sombra, 0x0B1B).states[54]
        self.assertTrue(channel.active)
        run.match.collision_world = wall_scene(2.5)
        self.step(run, sombra, ashe, cast=2)
        self.assertFalse(channel.active)
        for _ in range(45):
            self.step(run, sombra, ashe, cast=2)
        self.assertIsNone(sombra_hack.instance_for(ashe, 0x0B1F))
