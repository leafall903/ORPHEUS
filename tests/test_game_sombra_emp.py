"""Actual EMP graph input/cast/effect behavior; native filters/presentation remain open."""

import json
import unittest
from unittest import mock

import test_game_sombra_combat as fixture

from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

EMP = 0x20


class SombraEmpTests(unittest.TestCase):
    def make(self, hero=None, position=(0, 0, 1.5)):
        f = fixture.SombraCombatTests()
        f.setUp()
        if hero is not None:
            f.run.match.switch_hero(f.b, heroes()[hero])
        f.b.mover.position[:] = position
        f.b.mover.body = None
        f.press(0, 50)
        f.a.body_script.set_ult_charge(1400)
        return f

    @staticmethod
    def applications(f):
        return [i for i in f.b.body_script.component.instances.values() if i.graph.index == 0x0B32]

    def test_emp_spends_full_charge_observes_cast_delay_and_hacks_without_health_damage(self):
        f = self.make()
        f.press(EMP)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        f.press(EMP, 19)
        self.assertEqual(self.applications(f), [])
        self.assertNotIn(f.b.body, f.run.match.combat.hacks)
        f.press(EMP, 10)
        self.assertEqual(len(self.applications(f)), 1)
        self.assertEqual(f.health(f.b), 200)
        application = self.applications(f)[0]
        self.assertEqual(application.source_entity(), f.a.body)
        self.assertEqual(application.vars[567].value(), (5,))
        self.assertEqual(f.b.body_script.component.entity_var(6396).value().id, f.a.body)
        self.assertIn(f.b.body, f.run.match.combat.hacks)
        emp = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0B31)
        self.assertTrue(emp.state(1).flags & nodes.Ability.RUNNING)
        f.press(EMP, 50)
        self.assertFalse(emp.state(1).flags & nodes.Ability.RUNNING)
        self.assertEqual(self.applications(f), [application])  # holding never recasts for free
        f.press(0, 400)
        self.assertEqual(self.applications(f), [])
        self.assertFalse(f.b.body_script.component.entity_var(888).value())
        self.assertFalse(f.b.body_script.component.entity_var(6396).value())

    def test_emp_with_insufficient_charge_cannot_cast_or_apply(self):
        f = self.make()
        f.a.body_script.ult_seconds = 0
        f.a.body_script.set_ult_charge(1399)
        f.press(EMP, 80)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 1399)
        self.assertEqual(self.applications(f), [])
        self.assertNotIn(f.b.body, f.run.match.combat.hacks)

    def test_emp_whiff_spends_charge_and_releasing_button_does_not_cancel_pulse(self):
        f = self.make(position=(0, 0, 30))
        f.press(EMP)
        f.press(0, 50)
        self.assertEqual(self.applications(f), [])
        self.assertLess(f.a.body_script.component.entity_var(342).value(), 100)
        g = self.make()
        g.press(EMP)
        g.press(0, 30)
        self.assertEqual(len(self.applications(g)), 1)

    def test_emp_hits_behind_caster_but_rejects_outside_radius_and_static_wall(self):
        for position, wall, hit in (((0, 0, -3), False, True),
                                    ((0, 0, 17), False, False),
                                    ((0, 0, 1.5), True, False)):
            with self.subTest(position=position, wall=wall):
                f = self.make(position=position)
                if wall:
                    f.wall()
                # Fixed aim away from the target: radial selection must not use the aim ray.
                for _ in range(30):
                    f.run.step({f.a: (16384, 0, EMP)})
                self.assertEqual(bool(self.applications(f)), hit)
                self.assertEqual(f.health(f.b), 200)

    def test_emp_ignores_allies_and_caster(self):
        f = self.make()
        f.run.match.game_map = fixture.KANEZAKA_DM.__class__(**{
            **fixture.KANEZAKA_DM.__dict__, "free_for_all": False,
        })
        f.b.team = f.a.team
        f.press(EMP, 30)
        self.assertEqual(self.applications(f), [])
        self.assertNotIn(f.a.body, f.run.match.combat.hacks)
        self.assertNotIn(f.b.body, f.run.match.combat.hacks)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(f.health(f.b), 200)

    def test_emp_removes_character_shields_preserves_health_and_armour(self):
        for hero in (0x02E0000000000020, 0x02E0000000000006):  # Zenyatta, Torbjorn
            with self.subTest(hero=hero):
                f = self.make(hero)
                f.health(f.b)  # materialize the switched body's authoritative pools
                vitals = f.run.match.combat.vitals[f.b.body]
                before = dict(vitals.now)
                f.press(EMP, 30)
                self.assertIsNotNone(f.b.body_script)
                self.assertEqual(vitals.now[SHIELDS_POOL], 0)
                self.assertEqual(vitals.now[HEALTH_POOL], before[HEALTH_POOL])
                self.assertEqual(vitals.now[ARMOUR_POOL], before[ARMOUR_POOL])
                self.assertIn(f.b.body, f.run.match.combat.hacks)

    def test_emp_actual_silence_blocks_enemy_helix_then_expires(self):
        f = self.make()
        f.press(EMP, 30)
        f.press(2, 20, shooter=f.b, target=f.a)
        f.press(0, 10, shooter=f.b, target=f.a)
        self.assertEqual(f.health(f.a), 200)
        f.press(0, 330)
        f.press(2, 20, shooter=f.b, target=f.a)
        f.press(0, 10, shooter=f.b, target=f.a)
        self.assertEqual(f.health(f.a), 80)

    def test_second_paid_emp_extends_silence_without_damaging_unshielded_health(self):
        f = self.make(0x02E0000000000020)
        f.press(EMP, 30)
        first_deadline = f.run.match.combat.hacks[f.b.body][0].until_tick
        f.press(0, 80)
        f.a.body_script.set_ult_charge(1400)
        f.press(EMP, 30)
        effects = f.run.match.combat.hacks[f.b.body]
        second_deadline = max(effect.until_tick for effect in effects)
        self.assertGreater(second_deadline, first_deadline)
        self.assertEqual(f.health(f.b), 50)
        f.press(0, first_deadline - f.run.frame + 1)
        self.assertTrue(f.b.body_script.component.entity_var(888).value())
        self.assertEqual(f.health(f.b), 50)
        f.press(0, second_deadline - f.run.frame)
        self.assertNotIn(f.b.body, f.run.match.combat.hacks)
        self.assertFalse(f.b.body_script.component.entity_var(888).value())

    def test_actual_enemy_kill_during_cast_prevents_postmortem_pulse(self):
        f = self.make()
        f.press(2, 20, shooter=f.b, target=f.a)
        f.press(0, 3, shooter=f.b, target=f.a)
        self.assertEqual(f.health(f.a), 80)
        f.press(FIRE, 20, shooter=f.b, target=f.a)
        f.press(0, 3, shooter=f.b, target=f.a)
        self.assertEqual(f.health(f.a), 40)
        f.press(EMP)
        f.press(FIRE, 20, shooter=f.b, target=f.a)
        self.assertEqual(f.health(f.a), 0)
        f.press(0, 20)
        self.assertEqual(self.applications(f), [])
        self.assertNotIn(f.b.body, f.run.match.combat.hacks)

    def test_active_emp_body_vm_export_is_passive_and_records_health_state(self):
        f = self.make(0x02E0000000000020)
        f.press(EMP, 30)
        script = f.b.body_script
        frame, now = script.frame, script.component.now
        first = export_body_vm(script)
        self.assertIn("ModifyBodyHealth", json.dumps(first))
        self.assertEqual(snapshot_digest(first), snapshot_digest(export_body_vm(script)))
        self.assertEqual((script.frame, script.component.now), (frame, now))
        application = self.applications(f)[0]
        application.node_state(application.graph._by_pos[9]).counter += 1
        self.assertNotEqual(snapshot_digest(first), snapshot_digest(export_body_vm(script)))

    def test_unrelated_legacy_play_script_does_not_even_resolve_a_resource(self):
        f = self.make()
        world = f.a.body_script.component.world
        unrelated = next(i for i in f.a.body_script.component.instances.values()
                         if i.graph.index not in (0x0B1B, 0x0B31, 0x0B32))
        with mock.patch(
            "ow174.game.script.play.resolve_resource", side_effect=AssertionError("out of scope"),
        ):
            world.play_body_script(unrelated, None)


if __name__ == "__main__":
    unittest.main()
