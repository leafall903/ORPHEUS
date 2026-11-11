"""Mei frost changes real combat/input/movement; reconstructed timing is explicit."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_mei_combat as fixtures

from ow174.game import combat, mei
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import heroes
from ow174.game.script import nodes


class FrostTests(unittest.TestCase):
    def duel(self):
        return fixtures.MeiCombatTests().duel()

    def commands(self, run, actor, frames, *, buttons=0, forward=0, yaw=0):
        for _ in range(frames):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            for player in run.players:
                player.client.queue_entities(run.match.remote_movements(player))
                command = Command(run.frame, yaw=yaw, buttons=buttons, forward=forward)
                player.take_commands([command if player is actor else Command(run.frame)])

    def one_spray(self, run, owner, target):
        yaw, pitch = fixtures.aim(run.match.combat._eye(owner),
                                  (target.feet[0], target.feet[1] + 0.9, target.feet[2]))
        run.step({owner: (yaw, pitch, FIRE)})
        for _ in range(20):
            run.step({owner: (yaw, pitch, 0)})

    def freeze(self, run, owner, target):
        yaw, pitch = fixtures.aim(run.match.combat._eye(owner),
                                  (target.feet[0], target.feet[1] + 0.9, target.feet[2]))
        for _ in range(110):
            run.step({owner: (yaw, pitch, FIRE)})
        self.assertTrue(run.match.combat.frozen(target.owner))
        return run.match.combat.frost(target.owner)

    def test_single_actual_spray_hit_damages_and_slows_real_movement(self):
        run, owner, enemy, target = self.duel()
        self.one_spray(run, owner, target)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(target).total, 197.25)
        self.assertAlmostEqual(fight.frost(enemy)[0], 0.2)
        self.assertFalse(fight.frozen(enemy))
        self.commands(run, enemy, 20, forward=127)
        self.assertAlmostEqual(enemy.mover.values.run_forward, enemy.mover.data.run_forward * 0.8, places=5)
        self.assertGreater(enemy.mover.position[2], target.feet[2])

    def test_freeze_blocks_movement_aim_attacks_and_abilities_then_restores(self):
        run, owner, enemy, target = self.duel()
        fight = run.match.combat
        sample = self.freeze(run, owner, target)
        self.assertEqual(sample[2] - sample[1], 1300)
        position = enemy.mover.position
        weapon = next(i for i in enemy.body_script.component.instances.values() if i.graph.index == 0x254)
        ammo = weapon.vars[53].value()
        owner_target = next(t for t in fight._targets() if t.owner is owner)
        health = fight.vitals_of(owner_target).total
        self.commands(run, enemy, 20, buttons=FIRE | JUMP | 8 | 16 | 32 | 0x800,
                      forward=127, yaw=12345)
        self.assertEqual(enemy.mover.position, position)
        self.assertEqual(enemy.mover.yaw, sample[3])
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(fight.vitals_of(owner_target).total, health)
        self.assertEqual(enemy.client.messages(combat.HIT), [])
        self.commands(run, enemy, 100)
        self.assertFalse(fight.frozen(enemy))
        self.commands(run, enemy, 20, forward=127)
        self.assertGreater(enemy.mover.position[2], position[2] + 0.5)
        self.assertEqual(enemy.mover.values.run_forward, enemy.mover.data.run_forward)

    def test_missing_hits_expire_slow_and_remove_body_variable(self):
        run, owner, enemy, target = self.duel()
        self.one_spray(run, owner, target)
        self.assertIn(enemy.body, run.match.combat.frost_hits)
        for _ in range(70):
            run.step({})
        self.assertIsNone(run.match.combat.frost(enemy))
        self.assertNotIn(enemy.body, run.match.combat.frost_hits)
        self.assertEqual(enemy.body_script.component.entity_var(2427).value(), 0.0)
        self.commands(run, enemy, 1, forward=127)
        self.assertEqual(enemy.mover.values.run_forward, enemy.mover.data.run_forward)

    def test_cryo_cleanses_slow_and_refuses_new_frost(self):
        run, owner, enemy, _target = self.duel()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[mei.HERO]))
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        self.one_spray(run, owner, target)
        self.assertIsNotNone(fight.frost(enemy))
        run.step({enemy: (0, 0, 8)})
        self.assertTrue(mei.protected(enemy))
        self.assertIsNone(fight.frost(enemy))
        before = fight.vitals_of(target).total
        fight._damage(owner, target, 2.75, False, mei.SPRAY_LOADOUT, run.frame)
        self.assertEqual(fight.vitals_of(target).total, before)
        self.assertIsNone(fight.frost(enemy))

    def test_death_and_body_replacement_remove_frost(self):
        run, owner, enemy, target = self.duel()
        self.one_spray(run, owner, target)
        fight = run.match.combat
        fight._damage(owner, target, 1000.0, False, None, run.frame)
        self.assertTrue(fight.vitals_of(target).dead)
        self.assertNotIn(target.entity, fight.frost_hits)
        # A separate live replacement also retires the old body's status.
        run, owner, enemy, target = self.duel()
        self.one_spray(run, owner, target)
        retired = enemy.body
        self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
        self.assertNotIn(retired, run.match.combat.frost_hits)
        self.assertIsNone(run.match.combat.frost(enemy))

    def test_frozen_shooter_keeps_an_already_fired_projectile(self):
        run, owner, enemy, target = self.duel()
        fight = run.match.combat
        fight.rockets.append(combat.Rocket(owner, owner.team, fight._eye(owner),
                                           (0.0, 0.0, 1.0), 40.0, 1.0, combat.HitDef(19.0), None))
        owner_target = next(t for t in fight._targets() if t.owner is owner)
        # Explicit frost-hit fixture isolates the persisted-flight interaction.
        for _ in range(30):
            fight._damage(enemy, owner_target, 2.75, False, mei.SPRAY_LOADOUT, run.frame)
        self.assertTrue(fight.frozen(owner))
        for _ in range(15):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 181.0)

    def test_full_ultimate_cannot_start_while_frozen_but_can_after_thaw(self):
        run, owner, enemy, target = self.duel()
        self.freeze(run, owner, target)
        body = enemy.body_script
        cost = body.component.entity_var(338).value()
        body.set_ult_charge(cost)
        self.commands(run, enemy, 10, buttons=32)
        self.assertTrue(run.match.combat.frozen(enemy))
        self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in body.ultimates))
        self.assertEqual(body.component.entity_var(342).value(), cost)
        self.commands(run, enemy, 100)
        self.commands(run, enemy, 2, buttons=32)
        self.assertTrue(any(state.flags & nodes.Ability.RUNNING for state in body.ultimates))
        self.assertEqual(body.component.entity_var(342).value(), 0.0)

    def test_existing_tactical_visor_buff_is_not_removed_by_freeze(self):
        run, owner, enemy, target = self.duel()
        body = enemy.body_script
        body.set_ult_charge(body.component.entity_var(338).value())
        self.commands(run, enemy, 1, buttons=32)
        self.assertTrue(any(state.flags & nodes.Ability.RUNNING for state in body.ultimates))
        self.freeze(run, owner, target)
        self.commands(run, enemy, 10)
        self.assertTrue(run.match.combat.frozen(enemy))
        self.assertTrue(any(state.flags & nodes.Ability.RUNNING for state in body.ultimates))

    def test_continued_spray_does_not_extend_active_freeze_deadline(self):
        run, owner, enemy, target = self.duel()
        sample = self.freeze(run, owner, target)
        yaw, pitch = fixtures.aim(run.match.combat._eye(owner),
                                  (target.feet[0], target.feet[1] + 0.9, target.feet[2]))
        for _ in range(35):
            run.step({owner: (yaw, pitch, FIRE)})
        self.assertEqual(run.match.combat.frost(enemy)[2], sample[2])
        while run.frame * 16 < sample[2]:
            run.step({})
        self.assertFalse(run.match.combat.frozen(enemy))
        self.assertFalse(run.match.combat.vitals_of(target).dead)


if __name__ == "__main__":
    unittest.main()
