"""The accepted Q flight supplies the original typed projectile output."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_zarya_owner_clock_delivery as owner_clock
import test_zarya_reported_presentation as presentation

from ow174.game import combat, graviton, world
from ow174.game.commands import Command
from ow174.game.script import expr


class ZaryaGravitonProjectileOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads((Path(__file__).resolve().parents[2]
                               / "fixtures/behavior/zarya-graviton-projectile-output-v1.json").read_text())

    def test_actual_q_publishes_entity_later_created_by_owner_confirmation(self):
        run, caster, _, _ = presentation.ZaryaPresentationTests().scene()
        fight = run.match.combat
        caster.body_script.set_ult_charge(2100)
        run.step({caster: (0, 0, 32)})
        rocket, = fight.rockets
        shot = rocket.shot
        allocated = fight.projectiles.lease_id(caster, rocket.definition, shot.frame)
        output = caster.body_script.component.entity_var(self.spec["output"], create=False)
        self.assertIsNotNone(output)
        self.assertEqual(output.value(), expr.Entity(allocated))
        self.assertEqual(rocket.entity, allocated)
        self.assertNotEqual(allocated, rocket.source_body)
        # The original output must resolve to this actual flight on every peer.
        self.assertTrue(all(item.record.ids[0] == allocated for item in shot.observers))
        for _ in range(36):
            run.step({})
        self.assertTrue(any(item.entity == allocated and item.op == world.OP_CREATE
                            for item in caster.client.entities))
        self.assertIn(allocated, shot.created)
        field, = fight.zarya_gravitons.fields
        self.assertEqual(field.source_body, rocket.source_body)

    def test_real_owner_epoch_output_cannot_rebind_a_replaced_body(self):
        run, caster, _, _ = presentation.ZaryaPresentationTests().scene()
        match, fight = run.match, run.match.combat
        match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=9000)
        caster.commands.reset(last_frame=9000)
        birth = run.frame
        for offset in range(1, 21):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            caster.take_commands([Command(9000 + offset)])
        caster.body_script.set_ult_charge(2100)
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        caster.take_commands([Command(9000 + run.frame - birth, buttons=32)])
        rocket, = fight.rockets
        old = caster.body_script.component
        identity = fight.projectiles.lease_id(caster, rocket.definition, rocket.shot.frame)
        self.assertEqual(old.entity_var(self.spec["output"]).value(), expr.Entity(identity))
        self.assertGreater(rocket.shot.frame, 9000)
        match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=18000)
        self.assertNotEqual(caster.body, rocket.source_body)
        graviton.publish_projectile_output(fight, rocket)
        current = caster.body_script.component.entity_var(self.spec["output"], create=False)
        self.assertTrue(current is None or current.value() is None)
        self.assertEqual(old.entity_var(self.spec["output"]).value(), expr.Entity(identity))
        end = run.frame + 100
        match.update(end * combat.TICK, end, frames=False)
        self.assertNotIn(rocket.shot, fight.projectiles.shots)

    def test_encrypted_q_keeps_one_exported_identity_through_late_ack(self):
        accepted = []
        producer = graviton.publish_projectile_output

        def capture(fight, rocket):
            producer(fight, rocket)
            if rocket.definition == 0x04000000000008CE:
                accepted.append(rocket)

        with patch.object(graviton, "publish_projectile_output", side_effect=capture):
            owner_clock.ZaryaOwnerClockTests().exercise(32)
        rocket, = accepted
        self.assertIn(rocket.entity, rocket.shot.created)
        self.assertTrue(all(item.record.ids == (rocket.entity,) for item in rocket.shot.observers))
        self.assertEqual(rocket.source_state.instance.component.entity_var(self.spec["output"]).value(),
                         expr.Entity(rocket.entity))
