"""Actual Tank input across death/body replacement; declared transport profiles."""

import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

import test_bastion_combat as bastion_tests

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import SOLDIER


class BastionProjectileLifecycleTests(unittest.TestCase):
    def test_tank_command_batches_preserve_flight_hit_and_timeout(self):
        contract = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "behavior"
                               / "bastion-tank-command-clock-v1.json").read_text())
        inputs, expected = contract["input"], contract["expected"]
        for hit in (False, True):
            observations = []
            for batch in inputs["batch_sizes"]:
                with self.subTest(hit=hit, batch=batch):
                    case = bastion_tests.BastionCombatTests()
                    case.fixture(enemy_position=inputs["target_positions"]["hit" if hit else "miss"],
                                 enemy_hero=int(inputs["target_hero"], 16))
                    case.tank()
                    origin = case.fight._eye(case.player)
                    start = case.run.frame
                    event = start + inputs["event_offset"]
                    before = case.health(case.enemy).total
                    rockets = []
                    impacts = []
                    commit = case.fight._commit_attack
                    damage = case.fight._damage

                    def record_damage(*args, damage=damage, impacts=impacts, fight=case.fight,
                                      enemy=case.enemy, **kwargs):
                        if args[1].owner is enemy:
                            impacts.append((kwargs.get("world_tick"), fight.tick))
                        return damage(*args, **kwargs)

                    def record(shooter, frame, payload, boost, commit=commit, rockets=rockets):
                        commit(shooter, frame, payload, boost)
                        if isinstance(payload, combat.Rocket):
                            rockets.append(payload)

                    case.fight._commit_attack = record
                    case.fight._damage = record_damage
                    sampled = None
                    for end in range(batch, inputs["end_offset"] + 1, batch):
                        world = start + end
                        case.run.frame = world
                        case.run.match.update(world * inputs["world_frame_seconds"], world)
                        yaw, pitch = case.command_aim if hit else (0, 0)
                        case.player.take_commands([
                            Command(start + offset, yaw=yaw, pitch=pitch,
                                    buttons=int(start + offset == event), extra=0)
                            for offset in range(end - batch + 1, end + 1)
                        ])
                        if end == inputs["sample_offset"]:
                            self.assertEqual(len(rockets), expected["launch_count"])
                            rocket = rockets[0]
                            self.assertAlmostEqual(math.dist(origin, rocket.position),
                                                   expected["sample_distance_meters"])
                            self.assertAlmostEqual(rocket.flown, expected["sample_flown_seconds"])
                            self.assertEqual(rocket.started_tick, start + expected["launch_offset"])
                            self.assertEqual(rocket.last_tick, start + expected["last_tick_offset"])
                            self.assertAlmostEqual(rocket.left, expected["remaining_lifetime_seconds"])
                            sampled = (rocket.position, rocket.flown, rocket.started_tick,
                                       rocket.last_tick, rocket.left)
                    self.assertFalse(case.fight.rockets)
                    dealt = before - case.health(case.enemy).total
                    outcome = expected["hit" if hit else "miss"]
                    self.assertAlmostEqual(dealt, outcome["damage"])
                    self.assertEqual(len(case.player.client.messages(combat.HIT)), outcome["owner_hit_count"])
                    self.assertTrue(all(impact == processed for impact, processed in impacts))
                    observations.append((sampled, dealt, impacts))
            reference, reference_damage, reference_impacts = observations[0]
            for sampled, dealt, impacts in observations[1:]:
                for actual, expected_value in zip(sampled[0], reference[0], strict=True):
                    self.assertAlmostEqual(actual, expected_value, places=6)
                for actual, expected_value in zip(sampled[1:], reference[1:], strict=True):
                    self.assertAlmostEqual(actual, expected_value, places=6)
                self.assertEqual(dealt, reference_damage)
                self.assertEqual(impacts, reference_impacts)

    def test_body_replacement_retires_flight_with_and_without_network_lease(self):
        for leased in (True, False):
            with self.subTest(leased=leased):
                case = bastion_tests.BastionCombatTests()
                case.fixture(enemy_position=(0, 1, 60))
                case.tank()
                if leased:
                    case.step(1)
                else:
                    # Only suppress network lease allocation; the real volley,
                    # projectile definition and damage payload still execute.
                    with patch.object(case.fight.projectiles, "lease_id", return_value=None):
                        case.step(1)
                self.assertEqual(len(case.fight.rockets), 1)
                rocket = case.fight.rockets[0]
                self.assertEqual(rocket.shot is not None, leased)
                old_body = case.player.body
                self.assertTrue(case.run.match.switch_hero(case.player, heroes()[SOLDIER]))
                self.assertNotEqual(case.player.body, old_body)
                case.player.mover.position[:] = (10, 1, 0)
                case.step(0, 90)
                self.assertEqual(case.health(case.enemy).total, 200)
                self.assertFalse(case.health(case.enemy).dead)
                self.assertNotIn(rocket, case.fight.rockets)
                self.assertFalse(case.player.client.messages(combat.KILL_NOTICE))

    def test_death_keeps_already_flying_tank_damage_and_original_attribution(self):
        for leased in (True, False):
            with self.subTest(leased=leased):
                case = bastion_tests.BastionCombatTests()
                case.fixture(enemy_position=(0, 1, 60))
                case.tank()
                if leased:
                    case.step(1)
                else:
                    with patch.object(case.fight.projectiles, "lease_id", return_value=None):
                        case.step(1)
                self.assertEqual(len(case.fight.rockets), 1)
                original_body = case.player.body
                case.fight._damage(case.enemy, case.target(case.player), 10000,
                                   False, None, case.run.frame)
                self.assertTrue(case.health(case.player).dead)
                self.assertEqual(len(case.fight.rockets), 1)
                case.step(1 | 8 | 32, 90)
                victim = case.health(case.enemy)
                self.assertTrue(victim.dead)
                self.assertEqual(victim.killer, original_body)
                self.assertEqual(case.enemy.client.messages(combat.DEATH_NOTICE)[-1],
                                 {"+0x78": combat.KILLED_BY,
                                  "+0x7C": {"+0x0": original_body}})
                self.assertEqual([hit["+0x78"]["+0x4"]
                                  for hit in case.player.client.messages(combat.HIT)], [65, 135])
                self.assertEqual(len(case.player.client.messages(combat.KILL_NOTICE)), 1)
                self.assertFalse(case.fight.rockets)


if __name__ == "__main__":
    unittest.main()
