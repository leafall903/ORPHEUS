"""Actual Ana projectile interception in a Mercy encounter."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_mercy_deflection as deflection_tests
import test_game_mercy_emp as emp_tests
import test_game_mercy_staff as staff_tests
from test_game_combat import aim

from ow174.game import defense_matrix
from ow174.game.commands import SECONDARY
from ow174.game.content import find_hero
from ow174.game.script import expr, graph, healing_status
from ow174.game.script.projectile_defense import SHIELD_TAG


class MercyDartAbsorptionTests(unittest.TestCase):
    def encounter(self, *, matrix):
        f = staff_tests.MercyStaffTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        hero = replace(find_hero("dva"), body=defense_matrix.MECH_BODY) if matrix else find_hero("sigma")
        self.assertTrue(f.match.switch_hero(f.ally, hero))
        self.assertTrue(f.match.switch_hero(f.enemy, find_hero("ana")))
        f.ally.mover.position[:] = (0, 1, 6)
        f.enemy.mover.position[:] = (0, 1, 0)
        f.mercy.mover.position[:] = (0, 1, 9)
        for player in f.run.players:
            player.mover.body = None
        for _ in range(35):
            f.run.step({})
        definition = 0x02CC if matrix else 0x1D02
        instances = f.ally.body_script.component.instances.values()
        instance = next(i for i in instances if i.graph.index == definition)
        targets = {t.owner: f.fight.vitals_of(t) for t in f.fight._targets()}
        return f, instance, targets

    def cast(self, f, defense_button, *, hold, attack_button=8, attack_frame=4):
        yaw, pitch = aim(f.fight._eye(f.enemy), (0, 2, 9))
        for frame in range(55):
            f.run.step({
                f.ally: (-32768, 0, defense_button if hold or frame == 0 else 0),
                f.enemy: (yaw, pitch, attack_button if frame == attack_frame else 0),
            })

    def test_matrix_absorbs_dart_without_damage_or_sleep(self):
        if graph.entity_body(defense_matrix.MECH_BODY) is None or not defense_matrix.tags():
            self.skipTest("prepared mech and projectile tag assets required")
        f, instance, targets = self.encounter(matrix=True)
        admitted = []
        matrix_distance = f.fight._matrix_distance

        def observe(*args):
            distance = matrix_distance(*args)
            if distance is not None:
                admitted.append(distance)
            return distance

        with patch.object(f.fight, "_matrix_distance", side_effect=observe):
            self.cast(f, SECONDARY, hold=True)
        self.assertTrue(instance.state(3).active)
        self.assertEqual(len(admitted), 1)
        self.assertFalse(f.fight.rockets)
        self.assertEqual(targets[f.ally].total, 600)
        self.assertEqual(targets[f.mercy].total, 200)
        self.assertFalse(f.fight.is_asleep(f.ally))
        self.assertFalse(f.fight.is_asleep(f.mercy))

    def test_matrix_absorbs_grenade_before_damage_or_antiheal(self):
        if graph.entity_body(defense_matrix.MECH_BODY) is None or not defense_matrix.tags():
            self.skipTest("prepared mech and projectile tag assets required")
        emp_tests.MercyEmpTests.mount_grenade_status(self)
        f, instance, targets = self.encounter(matrix=True)
        with patch.object(f.fight, "_absorb_in_matrix", wraps=f.fight._absorb_in_matrix) as absorb:
            self.cast(f, SECONDARY, hold=True, attack_button=16, attack_frame=30)
        self.assertTrue(instance.state(3).active)
        self.assertTrue(any(
            call.args[0].blast is not None and call.args[0].blast.effect == healing_status.GRENADE
            for call in absorb.call_args_list
        ))
        definitions = {call.args[2] for call in absorb.call_args_list}
        self.assertTrue(any(defense_matrix.absorbable(d) for d in definitions), definitions)
        self.assertFalse(f.fight.rockets)
        self.assertEqual(targets[f.ally].total, 600)
        self.assertEqual(targets[f.mercy].total, 200)
        self.assertEqual(f.fight.healing_status.scale(f.ally.body), 1)
        self.assertEqual(f.fight.healing_status.scale(f.mercy.body), 1)

        exposed, _, targets = self.encounter(matrix=True)
        exposed.ally.mover.position[0] = 8
        self.cast(exposed, 0, hold=False, attack_button=16)
        self.assertEqual(targets[exposed.mercy].total, 140)
        self.assertEqual(exposed.fight.healing_status.scale(exposed.mercy.body), 0)

    def test_grasp_absorbs_dart_and_awards_only_authored_shields(self):
        f, instance, targets = self.encounter(matrix=False)
        self.cast(f, 8, hold=False)
        self.assertTrue(instance.state(1).active)
        self.assertEqual(instance.vars[21312].value(), 5)
        self.assertEqual(instance.vars[41].value(), expr.Entity(f.enemy.body))
        self.assertFalse(f.fight.is_asleep(f.ally))
        self.assertFalse(f.fight.is_asleep(f.mercy))
        self.assertEqual(targets[f.mercy].total, 200)
        for _ in range(125):
            f.run.step({})
        self.assertEqual(targets[f.ally].temporary_shields[SHIELD_TAG].now, 3)

    def test_grasp_absorbs_grenade_without_detonation(self):
        emp_tests.MercyEmpTests.mount_grenade_status(self)
        f, instance, targets = self.encounter(matrix=False)
        self.cast(f, 8, hold=False, attack_button=16, attack_frame=30)
        self.assertEqual(instance.vars[21312].value(), 50)
        self.assertEqual(instance.vars[41].value(), expr.Entity(f.enemy.body))
        self.assertEqual(targets[f.mercy].total, 200)
        self.assertFalse(f.fight.healing_status.applied)
        self.assertFalse(f.fight.rockets)
        for _ in range(125):
            f.run.step({})
        self.assertAlmostEqual(targets[f.ally].temporary_shields[SHIELD_TAG].now, 30, places=5)

    def test_genji_returns_grenade_with_reflector_faction_and_status_source(self):
        emp_tests.MercyEmpTests.mount_grenade_status(self)
        helper = deflection_tests.MercyDeflectionTests()
        self.addCleanup(helper.doCleanups)
        f, deflect = helper.fixture()
        self.assertTrue(f.match.switch_hero(f.ally, find_hero("ana")))
        f.ally.mover.position[:] = (0, 1, 0)
        f.mercy.mover.position[:] = (1.5, 1, 4)
        f.enemy.mover.position[:] = (0, 1, 8)
        for player in f.run.players:
            player.mover.body = None
        yaw, pitch = aim(f.fight._eye(f.enemy), (1.5, 2, 4))
        f.run.step({f.enemy: (yaw, pitch, 16)})
        for _ in range(3):
            f.run.step({f.enemy: (yaw, pitch, 0)})
        self.assertTrue(deflect.node_state(deflect.graph.node(18)).active)
        throw_yaw, throw_pitch = aim(f.fight._eye(f.ally), (0, 2.3, 8))
        f.run.step({f.ally: (throw_yaw, throw_pitch, 16), f.enemy: (yaw, pitch, 0)})
        for _ in range(60):
            f.run.step({f.enemy: (yaw, pitch, 0)})
            if f.fight.healing_status.scale(f.mercy.body) == 0:
                break
        self.assertEqual(helper.health(f, f.mercy), 140)
        self.assertEqual(f.fight.healing_status.scale(f.mercy.body), 0)
        self.assertIn((f.enemy.body, f.mercy.body), f.fight.healing_status.applied)
        self.assertEqual(helper.health(f, f.enemy), 200)
