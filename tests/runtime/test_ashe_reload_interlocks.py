"""Actual Viper shell grants, held-fire interruption and fresh-input recovery."""

import unittest
from unittest.mock import patch

import test_ashe_combat as fixtures

from ow174.game import combat
from ow174.game.content import HEROES_PATH
from ow174.game.script import expr, graph, runtime


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class AsheReloadInterlockTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    press = fixtures.AsheCombatTests.press

    def test_held_ads_exits_empty_reload_after_one_shell_and_reenters_scope(self):
        cases = (
            (None, None, [14], [75], 12),
            (0, None, [14], [75], 1),
            (0, 15, [14], [75], 1),
            (0, 60, [14, 220], [75, 40], 1),
            (0, 90, [14, 14], [75, 75], 1),
            (85, None, [14], [75], 3),
        )
        for hold_from, refire, expected_nodes, expected_damage, expected_ammo in cases:
            with self.subTest(hold_from=hold_from, refire=refire):
                run, player, enemy = self.fight(distance=8)
                fight = run.match.combat
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x14FC)
                ammo = weapon.vars[53]
                # Spend eleven rounds away from the enemy, preserving full health for the reload check.
                for frame in range(60):
                    run.step({player: (16384, 0, 1 if frame < 5 else 0)})
                for _ in range(40):
                    run.step({player: (16384, 0, 2)})
                for _ in range(10):
                    for frame in range(60):
                        run.step({player: (16384, 0, 3 if frame < 5 else 2)})
                self.assertEqual(ammo.value(), 1)
                self.assertEqual(player.client.messages(combat.HIT), [])

                vitals = fight.vitals[enemy.body]
                _, pitch = fixtures.aim(fight._eye(player), (0, enemy.mover.position[1] + 1, 8))
                grants = []
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    previous_ammo = ammo.value()
                    for frame in range(240):
                        firing = frame < 5 or (refire is not None and refire <= frame < refire + 5)
                        aiming = hold_from is not None and frame >= hold_from
                        buttons = 3 if firing else 2 if aiming else 0
                        run.step({player: (0, pitch, buttons)})
                        current_ammo = ammo.value()
                        if current_ammo > previous_ammo:
                            self.assertEqual(current_ammo, previous_ammo + 1)
                            grants.append(frame)
                        if frame == 0:
                            self.assertEqual(current_ammo, 0)
                        previous_ammo = current_ammo

                self.assertEqual([call.args[2].node.pos for call in shots.call_args_list], expected_nodes)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in player.client.messages(combat.HIT)],
                                 expected_damage)
                self.assertEqual(vitals.total, 200 - sum(expected_damage))
                self.assertEqual(ammo.value(), expected_ammo)
                self.assertEqual(len(grants), expected_ammo + len(expected_nodes) - 1)
                self.assertGreater(grants[0], 5)

    def test_shell_grants_and_held_primary_preserve_ammo_and_fresh_input_recovers(self):
        for used in (6, 12):
            with self.subTest(used=used):
                run, player, _enemy = self.fight(distance=100)
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x14FC)
                grants = []
                write = runtime.Instance.write

                def observe(instance, target, value, *, target_weapon=weapon, target_grants=grants,
                            clock=run, write_value=write):
                    if instance is target_weapon and target == (expr.INSTANCE, 53):
                        target_grants.append((clock.frame, value, instance.vars[53].value()))
                    return write_value(instance, target, value)

                with (patch.object(runtime.Instance, "write", observe),
                      patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot):
                    for _ in range(used - 1):
                        self.press(run, player, 1, frames=80)
                    self.press(run, player, 1, frames=5)
                    run.step({})
                    self.assertEqual(weapon.vars[53].value(), 12 - used)
                    self.assertEqual(shoot.call_count, used)
                    self.press(run, player, 0x400, frames=20)
                    for _ in range(240):
                        run.step({player: (0, 0, 1)})
                    self.assertTrue(grants)
                    self.assertTrue(all(value == before + 1 for _frame, value, before in grants))
                    reported = shoot.call_count - used
                    self.assertGreaterEqual(reported, 1)
                    self.assertEqual(weapon.vars[53].value(), 12 - used + len(grants) - reported)
                    self.assertEqual(shoot.call_args.args[2].instance.graph.index, 0x14FC)
                    # Account for every report rather than assuming the native
                    # held-fire cadence, which still needs independent evidence.
                    run.step({})
                    if used == 12:
                        self.press(run, player, 0x400, frames=50)
                        for _ in range(300):
                            run.step({})
                        self.assertEqual(weapon.vars[53].value(), 12)
                    before = weapon.vars[53].value()
                    before_shots = shoot.call_count
                    self.press(run, player, 1, frames=40)
                    self.assertEqual(shoot.call_count, before_shots + 1)
                    self.assertEqual(weapon.vars[53].value(), before - 1)
                    self.assertTrue(all(value == old + 1 for _frame, value, old in grants))
                    self.assertEqual(weapon.vars[53].value(), 12 + len(grants) - shoot.call_count)


if __name__ == "__main__":
    unittest.main()
