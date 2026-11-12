"""Actual defensive ability input, scoped to proven104319 control predicates.

Fortify/Particle Barrier damage routing, numeric ordering and native hit shapes
are separate open requirements; these checks do not accept their damage policy.
"""

import unittest
from unittest.mock import patch

import test_game_brigitte as brigitte_tests
import test_game_brigitte_shield as shield_tests

from ow174.game import mccree, world
from ow174.game.commands import SECONDARY
from ow174.game.content import heroes
from ow174.game.script import nodes


class DefensiveControlTests(unittest.TestCase):
    def test_actual_personal_barrier_absorbs_enemy_brigitte_attacks_and_expires(self):
        for kind, remaining in (('flail', 165), ('quick_melee', 165), ('whip', 130), ('bash', 195)):
            with self.subTest(attack=kind):
                run, caster, target, _ = self.fixture('Zarya')
                fight = run.match.combat
                body = next(t for t in fight._targets() if t.owner is target)
                vitals = fight.vitals_of(body)
                caster.body_script.ult_seconds = 0
                caster.body_script.set_ult_charge(0)
                before, position = vitals.total, tuple(target.mover.position)
                self.attack(run, caster, kind)
                self.assertEqual(vitals.total, before)
                self.assertEqual(tuple(target.mover.position), position)
                self.assertFalse(fight.is_stunned(target))
                self.assertEqual(fight.personal_barriers[target.body][2], remaining)
                self.assertEqual(caster.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(fight.brigitte.inspire)
                for _ in range(140):
                    run.step({})
                self.assertNotIn(target.body, fight.personal_barriers)
                self.assertFalse(target.body_script.component.entity_var(2547).value())
                self.attack(run, caster, 'quick_melee' if kind == 'quick_melee' else 'flail')
                self.assertEqual(vitals.total, before - 35)

    def test_actual_whip_observes_personal_barrier_start_and_expiry_during_flight(self):
        for expires in (False, True):
            with self.subTest(expires=expires):
                run, caster, target, (x, y, z) = self.fixture("Zarya", activate=expires)
                target.mover.position, target.mover.body = [x, y, z + 18], None
                fight = run.match.combat
                vitals = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, target))
                before, start = vitals.total, tuple(target.mover.position)
                if expires:
                    for _ in range(95):
                        run.step({})
                brigitte_tests.BrigitteTests().whip(run, caster, 14)
                self.assertEqual(len(fight.brigitte.whips), 1)
                self.assertEqual(vitals.total, before)
                self.assertEqual(fight._personal_barrier_pet(target) is not None, expires)
                if expires:
                    for _ in range(10):
                        if fight._personal_barrier_pet(target) is None:
                            break
                        run.step({})
                else:
                    run.step({target: (-32768, 0, 8)})
                self.assertEqual(fight._personal_barrier_pet(target) is not None, not expires)
                self.assertEqual(len(fight.brigitte.whips), 1)
                self.assertEqual(vitals.total, before)
                for _ in range(40):
                    run.step({})
                self.assertFalse(fight.brigitte.whips)
                if expires:
                    self.assertEqual(vitals.total, before - 70)
                    self.assertGreater(target.mover.position[2], start[2] + 2)
                    self.assertNotIn(target.body, fight.personal_barriers)
                    self.assertIn((caster.body, caster.body), fight.brigitte.inspire)
                else:
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(tuple(target.mover.position), start)
                    self.assertEqual(fight.personal_barriers[target.body][2], 130)
                    self.assertFalse(fight.brigitte.inspire)

    def test_actual_personal_barrier_break_uses_graph_exit_and_absorbs_overkill(self):
        run, caster, target, _ = self.fixture('Zarya')
        fight = run.match.combat
        body = next(t for t in fight._targets() if t.owner is target)
        vitals = fight.vitals_of(body)
        before = vitals.total
        self.attack(run, caster, 'flail')
        self.assertEqual(fight.personal_barriers[target.body][2], 165)
        self.assertTrue(run.match.switch_hero(caster, heroes()[mccree.HERO]))
        x, y, z = target.mover.position
        caster.mover.position, caster.mover.body = [x, y, z - 3], None
        shoot = type(fight)._shoot
        shots = []

        def observe(combat, shooter, command, state, number, time, **kwargs):
            shoot(combat, shooter, command, state, number, time, **kwargs)
            if shooter is caster and state.node.pos == 37:
                pool = combat.personal_barriers.get(target.body)
                shots.append((pool[2] if pool else None, vitals.total))

        with patch.object(type(fight), '_shoot', observe):
            run.step({caster: (0, 0, SECONDARY)})
            for _ in range(50):
                run.step({})
        self.assertEqual(shots[:4], [(115, before), (65, before), (15, before), (None, before)])
        self.assertLess(vitals.total, before)
        instance = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x244)
        self.assertFalse(instance.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(target.body_script.component.entity_var(2547).value())
        self.assertGreater(instance.vars[61].value(), 0)

    def attack(self, run, caster, kind):
        if kind == 'bash':
            shield_tests.BarrierTests().bash(run, caster, frames=2)
        else:
            for _ in range(20):
                run.step({caster: (0, 0, 0)})
            case = brigitte_tests.BrigitteTests()
            if kind == 'flail':
                case.swing(run, caster, 20)
            elif kind == 'quick_melee':
                run.step({caster: (0, 0, 0x800)})
                for _ in range(40):
                    run.step({})
            else:
                case.whip(run, caster)

    def test_actual_fortify_reduces_brigitte_attacks_before_reconstructed_armor(self):
        attacks = (('flail', 21, 16), ('quick_melee', 21, 16), ('whip', 42, 37), ('bash', 3, 1.5))
        for kind, unarmored, armored in attacks:
            for armor in (False, True):
                with self.subTest(attack=kind, armor=armor):
                    run, caster, target, _ = self.fixture('Orisa')
                    caster.body_script.ult_seconds = 0
                    caster.body_script.set_ult_charge(0)
                    fight = run.match.combat
                    body = next(t for t in fight._targets() if t.owner is target)
                    vitals = fight.vitals_of(body)
                    if not armor:
                        vitals.damage(vitals.now[world.ARMOUR_POOL] + 5)
                        self.assertEqual(vitals.now[world.ARMOUR_POOL], 0)
                    before = vitals.total
                    self.attack(run, caster, kind)
                    self.assertAlmostEqual(before - vitals.total, armored if armor else unarmored, places=4)
                    self.assertAlmostEqual(caster.body_script.component.entity_var(342).value(),
                                           armored if armor else unarmored, places=4)
                    self.assertFalse(fight.is_stunned(target))

    def test_actual_fortify_blocks_whip_displacement_until_expiry(self):
        run, caster, target, _ = self.fixture("Orisa")
        fight = run.match.combat
        vitals = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, target))
        component = target.body_script.component
        fortify = next(item for item in component.instances.values() if item.graph.index == 0x0CF4)
        self.assertTrue(fortify.states[8].active)
        before = vitals.total
        start = tuple(target.mover.position)
        self.attack(run, caster, "whip")
        self.assertAlmostEqual(vitals.total, before - 37, places=4)
        self.assertEqual(tuple(target.mover.position), start)
        self.assertTrue(fortify.states[8].active)
        for _ in range(270):
            run.step({})
        self.assertFalse(fortify.states[8].active)
        before = vitals.total
        self.attack(run, caster, "whip")
        self.assertAlmostEqual(vitals.total, before - 65, places=4)
        self.assertGreater(target.mover.position[2], start[2] + 2)

    def test_whip_samples_fortify_at_contact_instead_of_launch(self):
        for expires in (False, True):
            with self.subTest(expires=expires):
                run, caster, target, (x, y, z) = self.fixture("Orisa", activate=expires)
                target.mover.position, target.mover.body = [x, y, z + 18], None
                fight = run.match.combat
                vitals = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, target))
                before = vitals.total
                if expires:
                    for _ in range(220):
                        run.step({})
                brigitte_tests.BrigitteTests().whip(run, caster, 14)
                self.assertEqual(len(fight.brigitte.whips), 1)
                self.assertEqual(vitals.total, before)
                self.assertEqual(fight.brigitte._fortify_blocks_whip(target), expires)
                if expires:
                    for _ in range(8):
                        if not fight.brigitte._fortify_blocks_whip(target):
                            break
                        run.step({})
                else:
                    run.step({target: (-32768, 0, 8)})
                self.assertEqual(fight.brigitte._fortify_blocks_whip(target), not expires)
                self.assertEqual(len(fight.brigitte.whips), 1)
                self.assertEqual(vitals.total, before)
                for _ in range(40):
                    run.step({})
                    if vitals.total < before:
                        break
                self.assertFalse(fight.brigitte.whips)
                self.assertEqual(fight.brigitte._fortify_blocks_whip(target), not expires)
                self.assertAlmostEqual(before - vitals.total, 65 if expires else 37, places=4)
                for _ in range(30):
                    run.step({})
                if expires:
                    self.assertGreater(target.mover.position[2], z + 20)
                else:
                    self.assertEqual(tuple(target.mover.position), (x, y, z + 18))

    def test_actual_fortify_expiry_restores_full_flail_damage_without_recast(self):
        run, caster, target, _ = self.fixture('Orisa')
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
        for _ in range(270):
            run.step({})
        self.assertEqual(target.body_script.component.entity_var(8243).value(), 1)
        before = vitals.total
        self.attack(run, caster, 'flail')
        self.assertEqual(before - vitals.total, 30)

    def fixture(self, name, *, activate=True):
        run, caster, target, _ = shield_tests.BarrierTests().fixture()
        run.match.switch_hero(target, next(hero for hero in heroes().values() if hero.name == name))
        x, y, z = caster.mover.position
        target.mover.position, target.mover.body = [x, y, z + 3], None
        run.step({caster: (0, 0, SECONDARY), target: (-32768, 0, 8 if activate else 0)})
        for _ in range(10):
            run.step({caster: (0, 0, SECONDARY)})
        return run, caster, target, (x, y, z)

    def test_actual_fortify_and_personal_barrier_reject_bash_control_without_interrupting(self):
        for name, index in (("Orisa", 0xCF4), ("Zarya", 0x244)):
            with self.subTest(hero=name):
                run, caster, target, _ = self.fixture(name)
                component = target.body_script.component
                ability = next(state for instance in component.instances.values()
                               if instance.graph.index == index for state in instance.states.values()
                               if isinstance(state, nodes.Ability))
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertTrue(component.entity_var(2547).value())
                position = tuple(target.mover.position)
                shield_tests.BarrierTests().bash(run, caster, frames=2)
                bash = run.match.combat.brigitte.barriers[caster.body].instance
                self.assertEqual(bash.states[10].counter, 1)  # contact occurred; not a geometry miss
                self.assertFalse(run.match.combat.is_stunned(target))
                self.assertTrue(component.entity_var(2547).value())
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                run.step({})
                self.assertFalse(run.match.combat.brigitte.stuns)
                self.assertEqual(tuple(target.mover.position), position)

    def test_control_reopens_after_real_defense_duration_and_bash_cooldown(self):
        for name in ("Orisa", "Zarya"):
            with self.subTest(hero=name):
                run, caster, target, start = self.fixture(name)
                shield_tests.BarrierTests().bash(run, caster, frames=2)
                for _ in range(500):
                    run.step({caster: (0, 0, SECONDARY)})
                self.assertFalse(target.body_script.component.entity_var(2547).value())
                caster.mover.position, caster.mover.body = list(start), None
                caster.mover.velocity = (0.0, 0.0, 0.0)
                shield_tests.BarrierTests().bash(run, caster, frames=2)
                self.assertTrue(run.match.combat.is_stunned(target))
                self.assertTrue(target.body_script.component.entity_var(888).value())

    def test_actual_fortify_exposes_resource_damage_scalar_and_retires_it(self):
        run, caster, target, _ = self.fixture("Orisa")
        component = target.body_script.component
        self.assertAlmostEqual(component.entity_var(8243).value(), 0.6)
        for _ in range(270):
            run.step({caster: (0, 0, SECONDARY)})
        self.assertEqual(component.entity_var(8243).value(), 1)


if __name__ == "__main__":
    unittest.main()
