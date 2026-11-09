"""Actual Genji inputs against Bastion; reconstructed interception contract."""

import unittest
from dataclasses import replace

import test_bastion_combat as kit

from ow174.game import combat
from ow174.game.content import heroes


class BastionDeflectTests(unittest.TestCase):
    def fixture(self, mode='recon', *, facing=-32768, activate=True, distance=10):
        case = kit.BastionCombatTests()
        genji = next(hero for hero in heroes().values() if hero.name == 'Genji')
        case.fixture(enemy_hero=genji.guid, enemy_position=(0, 1, distance))
        if mode == 'sentry':
            case.configure()
        elif mode == 'tank':
            case.tank()
        for frame in range(21):
            case.run.step({case.enemy: (facing, 0, 16 if activate and frame == 0 else 0)})
        self.case = case
        self.facing = facing
        self.deflect = next(instance for instance in case.enemy.body_script.component.instances.values()
                            if instance.graph.index == 0x3a3).states.get(10)
        self.assertEqual(bool(self.deflect and self.deflect.active), activate)
        return case

    def step(self, buttons=0, frames=1):
        for _ in range(frames):
            self.case.run.step({self.case.player: (*self.case.command_aim, buttons),
                                self.case.enemy: (self.facing, 0, 0)})

    def test_front_reflects_both_guns_and_tank_with_returned_damage(self):
        # Returned20-point Recon hits lose5 to armour; Sentry/Tank also meet
        # the shooter's active Ironclad before armour. No critical is invented.
        for mode, expected in (('recon', 30), ('sentry', 35), ('tank', 154)):
            with self.subTest(mode=mode):
                case = self.fixture(mode)
                before, own = case.health(case.enemy).total, case.health(case.player).total
                self.step(1, 1 if mode == 'tank' else 10)
                self.step(0, 30)
                self.assertEqual(case.health(case.enemy).total, before)
                self.assertAlmostEqual(own - case.health(case.player).total, expected, places=5)
                self.assertFalse(case.player.client.messages(combat.HIT))
                self.assertTrue(case.enemy.client.messages(combat.HIT))
                self.assertFalse(case.fight.rockets)

    def test_back_and_inactive_deflect_take_original_recon_damage(self):
        for facing, activate in ((0, True), (-32768, False)):
            with self.subTest(facing=facing, activate=activate):
                case = self.fixture(facing=facing, activate=activate)
                before, own = case.health(case.enemy).total, case.health(case.player).total
                self.step(1, 10)
                self.assertEqual(before - case.health(case.enemy).total, 40)
                self.assertEqual(case.health(case.player).total, own)

    def test_friendly_deflect_does_not_intercept_bullets_to_enemy_behind_it(self):
        case = self.fixture()
        case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
        case.player.team = case.enemy.team = 0
        case.bystander.team = 1
        case.enemy.mover.position[:] = (0, 1, 5)
        case.bystander.mover.position[:] = (0, 1, 10)
        for actor in (case.enemy, case.bystander):
            x, y, z = actor.mover.position
            actor.mover.body.teleport((x, y + actor.mover._lift(), z))
        self.step(0, 10)  # publish the moved target poses before the shooter's view-time query
        self.step(1, 10)
        self.assertEqual(case.health(case.bystander).total, 160)
        self.assertEqual(case.health(case.enemy).total, 200)
        self.assertEqual(case.health(case.player).total, 300)

    def test_wall_before_deflect_prevents_interception_and_return_damage(self):
        for mode in ('recon', 'sentry', 'tank'):
            with self.subTest(mode=mode):
                case = self.fixture(mode)
                case.wall(5)
                self.step(1, 1 if mode == 'tank' else 10)
                self.step(0, 30)
                self.assertEqual(case.health(case.enemy).total, 200)
                self.assertEqual(case.health(case.player).total, 300)
                self.assertFalse(case.enemy.client.messages(combat.HIT))
                self.assertFalse(case.fight.rockets)

    def test_expiry_takes_damage_and_actual_cooldown_reuse_reflects_again(self):
        case = self.fixture()
        for _ in range(200):
            if not self.deflect.active:
                break
            self.step()
        self.assertFalse(self.deflect.active)
        self.step(1, 10)
        self.assertEqual(case.health(case.enemy).total, 160)
        self.step(0, 625)
        for frame in range(21):
            case.run.step({case.enemy: (-32768, 0, 16 if frame == 0 else 0)})
        self.assertTrue(self.deflect.active)
        before = case.health(case.player).total
        self.step(1, 10)
        self.assertEqual(case.health(case.enemy).total, 160)
        self.assertEqual(before - case.health(case.player).total, 30)

    def test_reflected_tank_keeps_payload_and_credits_defender_for_third_party_kill(self):
        case = self.fixture('tank')
        for actor, position in ((case.player, (5, 1, 0)), (case.bystander, (0, 1, -10))):
            actor.mover.position[:] = position
            x, y, z = position
            actor.mover.body.teleport((x, y + actor.mover._lift(), z))
        # Restore the source to the firing line just for emission, then move
        # aside before the reflected projectile returns. The third party is real.
        case.player.mover.position[:] = (0, 1, 0)
        case.player.mover.body.teleport((0, 1 + case.player.mover._lift(), 0))
        self.step(1)
        self.assertEqual(len(case.fight.rockets), 1)
        rocket = case.fight.rockets[0]
        case.player.mover.position[:] = (5, 1, 0)
        case.player.mover.body.teleport((5, 1 + case.player.mover._lift(), 0))
        for _ in range(20):
            self.step()
            if rocket.shooter is case.enemy:
                break
        self.assertIs(rocket.shooter, case.enemy)
        self.assertEqual(rocket.team, case.enemy.team)
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (65, 140))
        self.assertTrue(rocket.deflectable)
        self.assertFalse(rocket.charge_eligible)
        self.assertIsNone(rocket.shot)  # native reflected presentation remains separate
        self.step(0, 50)
        victim = case.health(case.bystander)
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, case.enemy.body)
        self.assertEqual(case.health(case.enemy).total, 200)
        self.assertEqual(case.health(case.player).total, 300)
        self.assertTrue(case.enemy.client.messages(combat.KILL_NOTICE))
        self.assertFalse(case.player.client.messages(combat.KILL_NOTICE))
        self.assertFalse(case.fight.rockets)

    def test_actual_melee_front_blocks_without_reflection_back_and_inactive_still_damage(self):
        for facing, active, expected in ((-32768, True, 0), (0, True, 30),
                                         (-16384, True, 30), (16384, True, 30), (-32768, False, 30)):
            with self.subTest(facing=facing, active=active):
                case = self.fixture(facing=facing, activate=active, distance=1.4)
                script = case.player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                if active:
                    instance = self.deflect.instance
                    self.assertTrue(instance.states[59].active)
                    self.assertEqual(instance.states[59].node.cls, 'STUStatescriptStateCombatModFilter')
                self.step(0x800, 10)
                self.assertEqual(case.health(case.enemy).total, 200 - expected)
                self.assertEqual(case.health(case.player).total, 300)
                self.assertEqual(script.component.entity_var(342).value(), expected)
                self.assertEqual(bool(case.player.client.messages(combat.HIT)), bool(expected))
                self.assertFalse(case.enemy.client.messages(combat.HIT))

    def test_actual_melee_after_expiry_damages_then_cooldown_reuse_blocks(self):
        case = self.fixture(distance=1.4)
        for _ in range(200):
            if not self.deflect.active:
                break
            self.step()
        self.assertFalse(self.deflect.active)
        self.step(0x800, 10)
        self.assertEqual(case.health(case.enemy).total, 170)
        self.step(0, 625)
        for frame in range(21):
            case.run.step({case.enemy: (-32768, 0, 16 if frame == 0 else 0)})
        self.assertTrue(self.deflect.active)
        self.step(0x800, 10)
        self.assertEqual(case.health(case.enemy).total, 170)
        self.assertEqual(case.health(case.player).total, 300)

    def test_melee_wall_and_friendly_deflector_preserve_target_filtering(self):
        for profile in ('wall', 'friendly'):
            with self.subTest(profile=profile):
                case = self.fixture(distance=1.4, facing=0 if profile == 'wall' else -32768)
                if profile == 'wall':
                    case.wall(0.7)
                else:
                    case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
                    case.player.team = case.enemy.team = 0
                    case.bystander.team = 1
                    actor = case.bystander
                    actor.mover.position[:] = (0, 1, 2.2)
                    actor.mover.body.teleport((0, 1 + actor.mover._lift(), 2.2))
                    self.step(0, 10)
                self.step(0x800, 10)
                self.assertEqual(case.health(case.enemy).total, 200)
                self.assertEqual(case.health(case.player).total, 300)
                self.assertEqual(case.health(case.bystander).total, 170 if profile == 'friendly' else 200)


if __name__ == '__main__':
    unittest.main()
