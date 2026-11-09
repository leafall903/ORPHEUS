"""Actual Bastion inputs against the authored Sigma Grasp state."""
import unittest

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import combat, world

SIGMA = 0x02E000000000023B


class BastionAbsorptionTests(unittest.TestCase):
    def test_sigma_grasp_absorbs_actual_bastion_weapons_only_from_front(self):
        for form, damage in (("recon", 40), ("sentry", 75), ("tank", 205)):
            for scenario in ("front", "back", "inactive", "wall"):
                with self.subTest(form=form, scenario=scenario):
                    case = kit.BastionCombatTests()
                    case.fixture(enemy_position=(0, 1, 5), bystander_position=(0, 1, 7), enemy_hero=SIGMA)
                    if form == "sentry":
                        case.configure()
                    if form == "tank":
                        case.tank()
                    enemy = case.enemy
                    heading = 0 if scenario == "back" else -32768
                    for frame in range(12):
                        buttons = 8 if frame == 0 and scenario != "inactive" else 0
                        case.run.step({enemy: (heading, 0, buttons)})
                    grasp = next((i for i in enemy.body_script.component.instances.values()
                                  if i.graph.index == 0x1D02), None)
                    state = grasp.states.get(1) if grasp is not None else None
                    self.assertEqual(bool(state and state.active), scenario != "inactive")
                    if scenario == "wall":
                        case.wall(1.5)
                    health = case.health(enemy)
                    before = health.total
                    bystander = case.health(case.bystander).total
                    yaw, pitch = aim(case.fight._eye(case.player), (0, 2, 5))
                    for frame in range(16):
                        pressed = frame < (1 if form == "tank" else 10)
                        case.run.step({case.player: (yaw, pitch, int(pressed)), enemy: (heading, 0, 0)})
                    absorbed = grasp.vars[21312].value() if state is not None else 0
                    self.assertEqual(absorbed, damage if scenario == "front" else 0)
                    self.assertEqual(before - health.total, damage if scenario in ("back", "inactive") else 0)
                    if scenario in ("front", "wall"):
                        self.assertEqual(case.health(case.bystander).total, bystander)
                    self.assertFalse(case.fight.rockets)


    def test_grasp_expiry_releases_the_same_firing_lane(self):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 5), enemy_hero=SIGMA)
        enemy = case.enemy
        for frame in range(12):
            case.run.step({enemy: (-32768, 0, 8 if frame == 0 else 0)})
        grasp = next(i for i in enemy.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.states[1].active)
        health = case.health(enemy)
        before = health.total
        yaw, pitch = aim(case.fight._eye(case.player), (0, 2, 5))
        for _ in range(10):
            case.run.step({case.player: (yaw, pitch, 1), enemy: (-32768, 0, 0)})
        self.assertEqual(health.total, before)
        self.assertEqual(grasp.vars[21312].value(), 40)
        for _ in range(180):
            case.run.step({enemy: (-32768, 0, 0)})
            if enemy.body in case.fight.grasp_shields:
                break
        active = grasp.states.get(1)
        self.assertFalse(active and active.active)
        pool = health.temporary_shields[0x5847]
        self.assertAlmostEqual(pool.now, 24)
        self.assertAlmostEqual(health.total, before + 24)
        decay = case.fight.grasp_shields[enemy.body]
        self.assertAlmostEqual(decay.rate, 5)
        for _ in range(210):
            if case.fight.now >= decay.starts:
                break
            case.run.step({enemy: (-32768, 0, 0)})
        self.assertGreaterEqual(case.fight.now, decay.starts)
        for _ in range(25):
            case.run.step({enemy: (-32768, 0, 0)})
        self.assertAlmostEqual(pool.now, 22, delta=0.09)
        before_fire = health.total
        base_shields = health.now[world.SHIELDS_POOL]
        bonus = pool.now
        for _ in range(10):
            case.run.step({case.player: (yaw, pitch, 1), enemy: (-32768, 0, 0)})
        self.assertEqual(pool.now, 0)
        hits = case.player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [20, 20])
        decayed = before_fire - health.total - 40
        self.assertGreaterEqual(decayed, 0)
        self.assertLessEqual(decayed, 10 * combat.TICK * decay.rate + 0.001)
        self.assertAlmostEqual(base_shields - health.now[world.SHIELDS_POOL], 40 - bonus + decayed)


if __name__ == "__main__":
    unittest.main()
