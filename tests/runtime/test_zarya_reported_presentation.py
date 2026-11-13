"""Reported Zarya transport and hit registration regressions, build 104319."""

import math
import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import combat, graviton, world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr


class ZaryaPresentationTests(unittest.TestCase):
    def scene(self):
        run = Run(names=("Zarya", "Enemy", "Observer"))
        caster, enemy, observer = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(caster, heroes()[0x02E0000000000068])
        caster.team = observer.team = 0
        enemy.team = 1
        for player, point in ((caster, (0, 1, 0)), (enemy, (0, 1, 5)), (observer, (5, 1, 0))):
            player.mover.position[:] = point
            player.mover.body = None
        for _ in range(20):
            run.step({})
        return run, caster, enemy, observer

    def test_packet_batching_preserves_projectile_clock_position_and_impact(self):
        for button in (2, 32):
            for pitch in (-8192, 0):
                outcomes = []
                for batch in (1, 8, 32):
                    with self.subTest(button=button, pitch=pitch, batch=batch):
                        run, caster, enemy, _ = self.scene()
                        match, fight = run.match, run.match.combat
                        caster.body_script.ult_seconds = 0
                        caster.body_script.set_ult_charge(2100)
                        start = run.frame
                        rocket = None
                        for end in range(batch, 97, batch):
                            at = start + end
                            match.update(at * combat.TICK, at, frames=False)
                            commands = [
                                Command(start + offset, pitch=pitch, buttons=button if offset == 10 else 0)
                                for offset in range(end - batch + 1, end + 1)
                            ]
                            with patch.object(fight, "_submit_attack", wraps=fight._submit_attack) as submits:
                                caster.take_commands(commands)
                            for call in submits.call_args_list:
                                if isinstance(call.args[2], combat.Rocket):
                                    self.assertIsNone(rocket)
                                    rocket = call.args[2]
                        self.assertIsNotNone(rocket)
                        query = start + 100
                        match.update(query * combat.TICK, query, frames=False)
                        field = next(iter(fight.zarya_gravitons.fields), None)
                        outcomes.append((rocket.flown, rocket.position, fight.vitals[enemy.body].total,
                                         field.end_tick - start if field is not None else None,
                                         rocket.ended, rocket in fight.rockets,
                                         rocket.shot in fight.projectiles.shots))
                reference = outcomes[0]
                for outcome in outcomes[1:]:
                    self.assertAlmostEqual(outcome[0], reference[0], places=6)
                    for actual, expected in zip(outcome[1], reference[1], strict=True):
                        self.assertAlmostEqual(actual, expected, places=5)
                    self.assertAlmostEqual(outcome[2], reference[2], places=4)
                    self.assertEqual(outcome[3:], reference[3:])

    def test_beam_publishes_original_contact_outputs_and_damage(self):
        run, caster, enemy, _ = self.scene()
        fight = run.match.combat
        for _ in range(51):
            run.step({caster: (0, 0, 1)})
        weapon = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x0241)
        self.assertEqual(weapon.vars[2815].value(), expr.Entity(enemy.body))
        self.assertTrue(weapon.vars[23531].value())
        self.assertEqual(weapon.vars[16627].value(), expr.Entity(enemy.body))
        self.assertTrue(weapon.vars[23532].value())
        self.assertAlmostEqual(fight.vitals[enemy.body].total, 140)
        self.assertTrue(caster.client.messages(combat.HIT))
        for _ in range(3):
            run.step({caster: (16384, 0, 1)})
        self.assertFalse(weapon.vars[23531].value())
        self.assertFalse(weapon.vars[23532].value())

    def test_graviton_has_observer_flight_and_original_region_lifecycle(self):
        run, caster, enemy, _ = self.scene()
        fight = run.match.combat
        caster.body_script.set_ult_charge(2100)
        run.step({caster: (0, 0, 32)})
        rocket, = fight.rockets
        self.assertEqual(rocket.definition, 0x04000000000008CE)
        self.assertEqual(rocket.spawn, graviton.FIELD_ENTITY)
        self.assertIsNotNone(rocket.shot)
        self.assertTrue(rocket.shot.observers)
        for _ in range(90):
            run.step({})
            if fight.zarya_gravitons.fields:
                break
        field, = fight.zarya_gravitons.fields
        actor = next(a for a in fight.deployables.values() if a.definition == graviton.FIELD_ENTITY)
        self.assertEqual(actor.position, field.position)
        self.assertEqual(actor.expires_tick, field.end_tick)
        self.assertTrue(fight.deployable_create(actor).build(0).getvalue())
        self.assertIsNotNone(rocket.shot.release_at)
        self.assertAlmostEqual(fight.vitals[enemy.body].total, 195)
        for _ in range(field.end_tick - run.frame + 1):
            run.step({})
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertNotIn(actor.entity, fight.deployables)
        self.assertAlmostEqual(fight.vitals[enemy.body].total, 175)
        self.assertTrue(caster.client.messages(combat.HIT))
        self.assertNotIn(rocket.shot, fight.projectiles.shots)
        for viewer in run.players:
            self.assertTrue(any(item.entity == actor.entity and item.op == world.OP_CREATE
                                for item in viewer.client.entities))
            self.assertTrue(any(item.entity == actor.entity and item.op == world.OP_DESTROY
                                for item in viewer.client.entities))

    def test_secondary_and_graviton_each_launch_one_round_and_expire(self):
        for button, definition in ((2, 0x04000000000003DB), (32, 0x04000000000008CE)):
            with self.subTest(button=button):
                run, caster, enemy, _ = self.scene()
                fight = run.match.combat
                if button == 32:
                    caster.body_script.set_ult_charge(2100)
                run.step({caster: (0, -16384, button)})
                rocket, = fight.rockets
                self.assertEqual(rocket.definition, definition)
                self.assertAlmostEqual(rocket.direction[1], 1)
                self.assertAlmostEqual(rocket.direction[0], 0)
                self.assertAlmostEqual(rocket.direction[2], 0)
                self.assertTrue(rocket.shot.observers)
                end = run.frame + math.ceil(rocket.left / combat.TICK) + 2
                run.match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(fight.vitals[enemy.body].total, 200)
                self.assertFalse(caster.client.messages(combat.HIT))
                self.assertEqual(len(fight.zarya_gravitons.fields), int(button == 32))
                if button == 32:
                    field, = fight.zarya_gravitons.fields
                    run.match.update((field.end_tick + 1) * combat.TICK, field.end_tick + 1, frames=False)
                    self.assertFalse(fight.zarya_gravitons.fields)
                    self.assertFalse(any(a.definition == graviton.FIELD_ENTITY
                                         for a in fight.deployables.values()))
