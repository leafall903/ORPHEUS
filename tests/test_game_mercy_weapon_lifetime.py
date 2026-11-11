"""Actual Q/held-fire reentry after notification lifecycle restoration."""

import unittest
from unittest.mock import patch

import test_game_mercy_valkyrie as valkyrie
from test_game_mercy_blaster_critical import equip

from ow174.game import combat
from ow174.game.commands import FIRE, Command


class MercyWeaponLifetimeTests(unittest.TestCase):
    def held_across_expiry(self, *, delay_from=None):
        fixture = valkyrie.ValkyrieTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        run = fixture.fixture.run
        fixture.start()
        start = run.frame
        equip(run, fixture.mercy)
        fixture.step(start + 925 - run.frame)
        weapon = next(i for i in fixture.script.component.instances.values() if i.graph.index == 0x6D)
        ammo = weapon.find_var(0, 53)
        fight = fixture.fixture.fight
        trace = []
        late = False
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            for frame in range(start + 926, start + 968):
                if frame - start == delay_from:
                    run.frame = start + 1000
                    run.match.update(run.frame * combat.TICK, run.frame)
                    late = True
                    self.assertFalse(fixture.running())
                if not late:
                    run.match.update(frame * combat.TICK, frame)
                fixture.mercy.take_commands([Command(frame, buttons=FIRE)])
                trace.append(ammo.value())
                if late:
                    self.assertFalse(fixture.script.component.entity_var(842).value())
        self.assertFalse(fixture.running())
        self.assertFalse(weapon.state(27).current)
        return trace, [call.args[4] for call in shots.call_args_list]

    def test_late_held_fire_keeps_the_authored_ammo_and_shot_boundaries(self):
        timely = self.held_across_expiry()
        for delay_from in (926, 927):
            with self.subTest(delay_from=delay_from):
                self.assertEqual(self.held_across_expiry(delay_from=delay_from), timely)
        ammo, shots = timely
        self.assertEqual(ammo[0], 20)
        self.assertLess(ammo[-1], 20)
        self.assertGreater(len(shots), 2)

    def test_two_actual_valkyrie_cycles_restore_ammo_consumption_with_primary_held_across_expiry(self):
        fixture = valkyrie.ValkyrieTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        run = fixture.fixture.run
        fixture.start()
        fixture.step(35)  # respect the actual post-activation weapon switch gate
        run.frame += 1
        fixture.fixture.match.update(run.frame * combat.TICK, run.frame)
        fixture.mercy.take_commands([Command(run.frame, action=2)])
        fixture.step(35)
        weapon = next(i for i in fixture.script.component.instances.values() if i.graph.index == 0x6D)
        fight = fixture.fixture.fight
        for cycle in range(2):
            with self.subTest(cycle=cycle):
                if cycle:
                    fixture.start()  # explicit full-charge setup,actual Q activation
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    fixture.step(850, FIRE)
                    self.assertTrue(fixture.running())
                    self.assertGreater(shots.call_count, 20)
                    self.assertEqual(weapon.find_var(0, 53).value(), 20)
                    # Keep primary down while original15s scope expires.
                    fixture.step(120, FIRE)
                    self.assertFalse(fixture.running())
                    self.assertLess(weapon.find_var(0, 53).value(), 20)
                    before = shots.call_count
                    fixture.step(25, FIRE)
                    self.assertGreater(shots.call_count, before)
                    # The original842 BooleanSwitch has observed expiry again.
                    self.assertFalse(weapon.state(27).current)
