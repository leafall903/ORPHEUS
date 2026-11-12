"""Retain0004's passive timing expectations through the combined grenade host."""

import unittest
from unittest.mock import patch

from test_game_combat import aim
import test_game_mercy_staff as staff_tests

from ow174.game import combat, stats
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import runtime


class MercyRegenerationTimingTests(unittest.TestCase):
    def scene(self, *, friendly=False):
        fixture = staff_tests.MercyStaffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.match.switch_hero(fixture.enemy, heroes()[0x02E000000000013B])
        fixture.enemy.team = fixture.mercy.team if friendly else 1 - fixture.mercy.team
        fixture.step(35, buttons=0)
        x, y, z = fixture.mercy.mover.position
        fixture.enemy.mover.position[:] = (x + 4, y, z)
        fixture.enemy.mover.body = None
        yaw, pitch = aim(fixture.fight._eye(fixture.enemy), (x, y + 0.8, z))
        fixture.run.step({fixture.enemy: (yaw, pitch, 0x10)})
        target = next(t for t in fixture.fight._targets() if t.owner is fixture.mercy)
        for _ in range(60):
            fixture.run.step({})
            factor = fixture.fight.healing.factor(target)
            if (factor > 1 if friendly else factor == 0):
                break
        self.assertEqual(fixture.fight.healing.factor(target), 1.5 if friendly else 0)
        return fixture, target, fixture.fight.vitals_of(target)

    def test_late_passive_healing_respects_antiheal_that_expired_during_gap(self):
        f, target, health = self.scene()
        self.assertEqual(health.total, 140)
        impact = f.run.frame
        script = f.mercy.body_script
        for _ in range(260):
            f.run.frame += 1
            f.match.update(f.run.frame * combat.TICK, f.run.frame)
        self.assertEqual(script.frame, impact)
        self.assertEqual(health.total, 140)
        self.assertEqual(f.fight.healing.factor(target), 1)
        self.assertEqual(script.world.passive_healing_scales, ((impact, 0.0), (impact + 250, 1.0)))
        with patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated VM")):
            stored = snapshot_digest(export_body_vm(script))
            self.assertEqual(snapshot_digest(export_body_vm(script)), stored)
            script.world.passive_healing_scales = ((impact, 0.5), (impact + 250, 1.0))
            self.assertNotEqual(snapshot_digest(export_body_vm(script)), stored)
            script.world.passive_healing_scales = ((impact, 0.0), (impact + 250, 1.0))
        f.mercy.take_commands([Command(f.run.frame)])
        self.assertAlmostEqual(health.total, 143.52)
        self.assertFalse(hasattr(script.world, "passive_healing_scales"))
        f.mercy.take_commands([Command(f.run.frame - 1), Command(f.run.frame)])
        self.assertAlmostEqual(health.total, 143.52)
        self.assertEqual(f.mercy.stats.value(stats.HEALING), 0)

    def test_amp_applies_to_actual_passive_without_granting_team_healing_credit(self):
        f, target, health = self.scene(friendly=True)
        f.step(5, buttons=0)
        f.ally.team = 1 - f.mercy.team
        f.fight._damage(f.ally, target, 50, False, None, f.run.frame)
        f.step(125, buttons=0)
        self.assertAlmostEqual(health.total, 180.24, places=4)
        self.assertEqual(f.mercy.stats.value(stats.HEALING), 0)
