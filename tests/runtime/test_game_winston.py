"""Actual Winston weapon graph -> server health; reconstructed geometry, no client acceptance."""
import unittest
from unittest.mock import patch

from ow174.game import combat
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import Run

WINSTON = 0x02E0000000000009


class TeslaTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Winston", "Enemy", "Ally"))
        self.match = self.run.match
        self.npc = self.match.bots[0]
        self.match.bots.clear()
        self.winston, self.enemy, self.ally = self.run.players
        self.match.switch_hero(self.winston, heroes()[WINSTON])
        self.winston.team = self.ally.team = 0
        self.enemy.team = 1
        self.winston.mover.position = [0, 0, 0]
        self.enemy.mover.position = [0, 0, 4]
        self.ally.mover.position = [0, 0, 3]
        self.fight = self.match.combat
        self.fight.now = self.run.frame * combat.TICK
        self.targets = {t.owner: t for t in self.fight._targets()}

    def health(self, who):
        return self.fight.vitals_of(self.targets[who]).total

    def step(self, buttons=FIRE):
        self.run.step({self.winston: (0, 0, buttons)})

    def test_actual_fire_state_damages_enemy_and_preserves_ally(self):
        before = self.health(self.enemy)
        self.step()
        self.assertAlmostEqual(before - self.health(self.enemy), 60 * combat.TICK)
        self.assertEqual(self.health(self.ally), 200)
        self.assertEqual(self.health(self.winston), 500)
        self.assertTrue(self.winston.client.messages(combat.HIT))

    def test_continuous_damage_and_release(self):
        for _ in range(20):
            self.step()
        self.assertAlmostEqual(self.health(self.enemy), 200 - 20 * 60 * combat.TICK)
        self.step(0)
        stopped = self.health(self.enemy)
        for _ in range(5):
            self.step(0)
        self.assertEqual(self.health(self.enemy), stopped)

    def test_one_command_cannot_damage_twice_or_replay_backwards(self):
        self.step()
        before = self.health(self.enemy)
        for frame in (self.run.frame, self.run.frame - 1):
            self.fight.command(self.winston, Command(frame, buttons=FIRE))
        self.assertEqual(self.health(self.enemy), before)

    def test_beam_hits_multiple_enemies(self):
        self.ally.team = 1
        self.step()
        self.assertLess(self.health(self.enemy), 200)
        self.assertLess(self.health(self.ally), 200)

    def test_range_and_rear_targets_are_rejected(self):
        self.enemy.mover.position = [0, 0, 12]
        self.ally.team = 1
        self.ally.mover.position = [0, 0, -3]
        self.step()
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.ally), 200)

    def test_world_occlusion(self):
        with patch.object(combat, 'ray_world', return_value=1.0):
            self.step()
        self.assertEqual(self.health(self.enemy), 200)

    def test_beam_uses_reconstructed_continuous_armour_rule(self):
        self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, 100, 100)
        self.step()
        self.assertAlmostEqual(self.health(self.enemy), 200 - 60 * combat.TICK * .8)

    def test_retained_npc_is_damaged_killed_and_not_replaced(self):
        npc = self.npc
        npc.position, npc.team, npc.route = (0, 0, 4), 1, []
        npc.retain_corpse = True
        self.match.bots.append(npc)
        self.fight.vitals[npc.entity] = combat.Vitals(npc.entity, 1.5)
        self.step()
        self.assertAlmostEqual(self.fight.vitals[npc.entity].total, .54)
        self.step()
        self.assertTrue(npc.dead)
        self.assertEqual(self.fight.vitals[npc.entity].total, 0)
        for _ in range(200):
            self.step(0)
        self.assertIn(npc, self.match.bots)
        self.assertFalse(any(who is npc for _, who in self.fight.respawns))

    def test_player_attack_reaches_winston_armour(self):
        self.fight._damage(self.enemy, self.targets[self.winston], 100,
                           False, None, self.run.frame)
        self.assertEqual(self.health(self.winston), 405)

    def test_retiring_body_discards_beam_frame_guard(self):
        self.step()
        old = self.winston.body
        self.assertIn(old, self.fight.tesla_frames)
        self.fight.retire_player_body(self.winston)
        self.assertNotIn(old, self.fight.tesla_frames)

    def test_npc_attacks_damage_winston_armour_and_death_stops_fire(self):
        source = self.npc
        target = self.targets[self.winston]
        self.fight._damage(source, target, 100, False, None, self.run.frame)
        self.assertEqual(self.health(self.winston), 405)
        self.fight._damage(source, target, 1000, False, None, self.run.frame)
        self.step()
        self.assertTrue(self.fight.is_dead(self.winston))
        self.assertEqual(self.health(self.enemy), 200)

    def test_enemy_death_records_winston_killer_and_stops_further_damage(self):
        self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, .5)
        self.step()
        vitals = self.fight.vitals[self.enemy.body]
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.winston.body)
        self.step()
        self.assertEqual(vitals.total, 0)


if __name__ == '__main__':
    unittest.main()
