"""Bastion attacks against the authored 104319 Zarya protection scopes."""
import unittest
from dataclasses import replace

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import combat, zarya_barriers
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.script import expr


class BastionZaryaBarrierTests(unittest.TestCase):
    def arena(self, form, projected):
        case = kit.BastionCombatTests()
        z = 1.4 if form == "melee" else 5
        case.fixture(enemy_position=(3 if projected else 0, 1, z),
                     bystander_position=(0, 1, z) if projected else (5, 1, 9),
                     enemy_hero=zarya_barriers.HERO)
        case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
        case.player.team = 0
        case.enemy.team = case.bystander.team = 1
        victim = case.bystander if projected else case.enemy
        case.command_aim = aim(case.fight._eye(case.player), (0, 2, z))
        if form == "sentry":
            case.configure()
        elif form == "tank":
            case.tank()
        caster = case.enemy
        graph = 0x025C if projected else 0x0244
        instance = next(i for i in caster.body_script.component.instances.values() if i.graph.index == graph)
        cast = (-16384, 0, 16) if projected else (0, 0, 8)
        case.run.step({caster: (cast[0], cast[1], 0)})
        case.run.step({caster: cast})
        self.assertTrue(instance.state(10 if projected else 2).active)
        if projected:
            self.assertEqual(instance.vars[536].value(), expr.Entity(victim.body))
        return case, caster, victim, instance, cast

    def test_personal_and_projected_barriers_block_all_bastion_weapons_and_melee(self):
        for projected in (False, True):
            for form, button, frames, absorbed in (
                ("recon", 1, 10, 40), ("sentry", 1, 10, 75),
                ("tank", 1, 1, 200), ("melee", 0x800, 1, 30),
            ):
                with self.subTest(projected=projected, form=form):
                    case, caster, victim, instance, _ = self.arena(form, projected)
                    health = case.health(victim)
                    before = health.total
                    position = tuple(victim.mover.position)
                    script = case.player.body_script
                    script.ult_seconds = 0
                    charge = script.component.entity_var(342)
                    before_charge = charge.value()
                    case.step(button, frames)
                    case.step(0, 30)
                    self.assertEqual(health.total, before)
                    self.assertEqual(tuple(victim.mover.position), position)
                    self.assertEqual(charge.value(), before_charge)
                    hits = [message["+0x78"] for message in case.player.client.messages(combat.HIT)]
                    actual = [(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits]
                    expected = [(caster.body, 90)] if projected and form == "tank" else []
                    self.assertEqual(actual, expected)
                    self.assertFalse(case.fight.rockets)
                    host = case.fight.zarya_barriers
                    storage = host.projected if projected else host.active
                    self.assertEqual(storage[caster.body].remaining, 200 - absorbed)
                    energy = caster.body_script.component.entity_var(87).value()
                    self.assertAlmostEqual(energy, absorbed * 0.2)
                    if form == "tank":
                        self.assertFalse(instance.state(10 if projected else 2).active)

    def test_expiry_and_cooldown_then_reuse(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                case, caster, victim, instance, cast = self.arena("recon", projected)
                pet = instance.state(10 if projected else 2)
                for _ in range(160):
                    case.run.step({caster: (cast[0], cast[1], 0)})
                self.assertFalse(pet.active)
                case.run.step({caster: cast})
                self.assertFalse(pet.active)
                before = case.health(victim).total
                case.step(1, 10)
                self.assertEqual(before - case.health(victim).total, 40)
                for _ in range(78):
                    for _ in range(9):
                        case.run.step({caster: (cast[0], cast[1], 0)})
                    case.run.step({caster: cast})
                    if pet.active:
                        break
                self.assertTrue(pet.active)
                before = case.health(victim).total
                case.step(1, 10)
                self.assertEqual(case.health(victim).total, before)


    def test_projected_requires_visible_ally_and_snapshots_active_target(self):
        for scenario in ("opponent", "wall"):
            with self.subTest(scenario=scenario):
                case = kit.BastionCombatTests()
                case.fixture(enemy_position=(0, 1, 5), bystander_position=(0, 1, 8),
                             enemy_hero=zarya_barriers.HERO)
                case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
                caster, candidate = case.enemy, case.bystander
                case.player.team = 0
                caster.team = 1
                candidate.team = int(scenario == "wall")
                if scenario == "wall":
                    case.wall(6.5)
                case.run.step({caster: (0, 0, 16)})
                instances = caster.body_script.component.instances.values()
                instance = next(i for i in instances if i.graph.index == 0x025C)
                self.assertFalse(instance.state(10).active)
                self.assertIsNone(instance.vars.get(536))
        case, caster, victim, instance, _ = self.arena("recon", True)
        digest = snapshot_digest(export_body_vm(caster.body_script))
        self.assertEqual(digest, snapshot_digest(export_body_vm(caster.body_script)))
        case.step(1, 10)
        self.assertTrue(instance.state(10).active)
        self.assertEqual(instance.vars[536].value(), expr.Entity(victim.body))
        self.assertNotEqual(digest, snapshot_digest(export_body_vm(caster.body_script)))


if __name__ == "__main__":
    unittest.main()
