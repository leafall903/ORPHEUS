"""Actual force contacts retain separate source-command and physical clocks."""

import unittest
from unittest.mock import patch

import test_lucio_combat as fixtures

from ow174.game import combat, lucio
from ow174.game.collision import flat_world
from ow174.game.commands import SECONDARY, Command
from ow174.game.content import heroes


class LucioForceClockTests(unittest.TestCase):
    def contact(self, offset, button):
        run, source, enemy, ally = fixtures.LucioCombatTests().scenario()
        match, fight = run.match, run.match.combat
        match.collision_world = flat_world(1)
        match.switch_hero(source, heroes()[lucio.HERO], start_frame=run.frame + offset)
        source.mover.position[:] = (0, 1, 0)
        enemy.mover.position[:] = (0, 1, 2)
        ally.mover.position[:] = (20, 1, 4)
        calls = []
        damage = fight._damage

        def observe(shooter, target, amount, critical, loadout, frame, **kwargs):
            if shooter is source and target.owner is enemy:
                calls.append((frame, kwargs.get("world_tick")))
            return damage(shooter, target, amount, critical, loadout, frame, **kwargs)

        with patch.object(fight, "_damage", observe):
            for index in range(36):
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                source.take_commands([Command(run.frame + offset, buttons=button if index == 12 else 0)])
                enemy.take_commands([Command(run.frame)])
                ally.take_commands([Command(run.frame)])
        return {
            "health": fixtures.LucioCombatTests().health(run, enemy),
            "impulses": fight.knockbacks.serial,
            "position": tuple(enemy.mover.position),
            "calls": [(owner - offset, world) for owner, world in calls],
        }

    def test_soundwave_unequal_source_epoch_keeps_damage_and_force_contact(self):
        normal = self.contact(0, SECONDARY)
        shifted = self.contact(4000, SECONDARY)
        self.assertEqual(normal["health"], 175)
        self.assertEqual(normal["impulses"], 1)
        self.assertEqual(shifted, normal)
        self.assertEqual(normal["calls"][0][0], normal["calls"][0][1])

    def test_quick_melee_unequal_source_epoch_keeps_damage_and_force_contact(self):
        normal = self.contact(0, 0x800)
        shifted = self.contact(4000, 0x800)
        self.assertEqual(normal["health"], 170)
        self.assertEqual(normal["impulses"], 1)
        self.assertEqual(shifted, normal)
        self.assertEqual(normal["calls"][0][0], normal["calls"][0][1])
