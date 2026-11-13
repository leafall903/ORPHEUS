"""Real secondary volleys opt into the single candidate receiver API."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_zarya_force_rules_174 as fixtures

from ow174.game import combat, correction, zarya
from ow174.game.commands import Command
from ow174.game.content import heroes


class ZaryaR2ForceProducerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/zarya-r2-force-producer-v1.json"
        cls.spec = json.loads(path.read_text())

    def test_actual_secondary_registers_one_world_force_for_enemy_and_self(self):
        for own in (False, True):
            with self.subTest(own=own):
                run, caster, enemy, _ = fixtures.ZaryaForceRuleTests().scene()
                match, fight = run.match, run.match.combat
                birth = run.frame
                match.switch_hero(caster, heroes()[zarya.HERO], preserve_pose=True, start_frame=9000)
                caster.commands.reset(last_frame=9000)
                if not own:
                    match.switch_hero(enemy, heroes()[zarya.HERO], preserve_pose=True, start_frame=birth)
                victim = caster if own else enemy
                if own:
                    enemy.mover.position[:] = [10, 1, 5]

                def step(button=0, *, scenario=run, game=match, shooter=caster, epoch=birth, self_hit=own):
                    scenario.frame += 1
                    game.update(scenario.frame * combat.TICK, scenario.frame)
                    shooter.take_commands([Command(
                        9000 + scenario.frame - epoch, pitch=16384 if self_hit else 0, buttons=button,
                    )])
                    for player in scenario.players[1:]:
                        player.take_commands([Command(scenario.frame)])

                for _ in range(20):
                    step()
                initial = tuple(victim.mover.position)
                before = fight.vitals[victim.body].total
                with patch.object(fight.knockbacks, "apply", wraps=fight.knockbacks.apply) as apply:
                    step(2)
                    rocket, = fight.rockets
                    for _ in range(30):
                        step()
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertLess(fight.vitals[victim.body].total, before)
                    self.assertEqual(apply.call_count, 1)
                    call, = apply.call_args_list
                    self.assertEqual(call.args[0].entity, victim.body)
                    self.assertLess(call.kwargs["impact_tick"], 2000)
                    self.assertLessEqual(call.kwargs["impact_tick"], run.frame)
                    self.assertEqual(call.kwargs["source_entity"], rocket.source_entity)
                    self.assertNotIn("impact_frame", call.kwargs)
                    self.assertAlmostEqual(
                        call.args[1].y,
                        self.spec["secondary"]["vertical"]
                        * (self.spec["secondary"]["self_scale"] if own else 1),
                    )
                self.assertEqual(fight.knockbacks.serial, 1)
                self.assertIsNotNone(victim.impulse_correction)
                for _ in range(8):
                    step()
                axis = 1 if own else 2
                self.assertGreater(victim.mover.position[axis], initial[axis] + 0.05)
                if not own:
                    self.assertTrue(caster.client.messages(combat.HIT))

    def test_actual_graviton_publishes_existing_pull_and_release_without_impulses(self):
        run, caster, victim, _ = fixtures.ZaryaForceRuleTests().scene()
        match, fight = run.match, run.match.combat
        match.switch_hero(victim, heroes()[zarya.HERO], preserve_pose=True, start_frame=run.frame)
        run.step({})
        initial = tuple(victim.mover.position)
        health = fight.vitals[victim.body].total
        caster.body_script.set_ult_charge(2100)
        run.step({caster: (0, 0, 32)})
        for _ in range(60):
            run.step({})
            if fight.zarya_gravitons.fields:
                break
        field, = fight.zarya_gravitons.fields
        for _ in range(12):
            run.step({})
        self.assertIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertLess(victim.mover.position[2], initial[2])
        self.assertLess(fight.vitals[victim.body].total, health)
        self.assertIsNotNone(correction.own_record(victim, run.frame))
        self.assertEqual(fight.knockbacks.serial, 0)
        while run.frame <= field.end_tick + 2:
            run.step({})
        self.assertAlmostEqual(
            fight.vitals[victim.body].total, health - self.spec["graviton"]["total_damage"], places=4,
        )
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertNotIn(victim.body, fight.zarya_gravitons.pulled)
        self.assertFalse(victim.mover.driven)
        self.assertEqual(tuple(victim.mover.velocity), (0, 0, 0))
        released = tuple(victim.mover.position)
        for _ in range(6):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            caster.take_commands([Command(run.frame)])
            victim.take_commands([Command(run.frame, forward=127)])
        self.assertGreater(victim.mover.position[2], released[2])
        self.assertIsNotNone(correction.own_record(victim, run.frame))
        self.assertEqual(fight.knockbacks.serial, 0)
