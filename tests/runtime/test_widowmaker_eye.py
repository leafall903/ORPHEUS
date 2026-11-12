"""Authored static FirstPerson offsets through real stance, shot and cover paths."""

import unittest
from unittest.mock import patch

from test_game_combat import aim
from test_widowmaker_combat import _WidowmakerMatch

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes


class WidowmakerEyeTests(_WidowmakerMatch, unittest.TestCase):
    def mccree(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[0x02E0000000000042]))
        self.b.mover.position[:] = (0, 0, 10)
        self.b.mover.body = None
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)

    def test_real_stances_use_authored_shooter_and_target_offsets(self):
        self.mccree()
        for crouched in (False, True):
            buttons = CROUCH if crouched else 0
            for _ in range(4):
                self.run.step({self.a: (0, 0, buttons), self.b: (-32768, 0, buttons)})
            for actor, expected in ((self.a, 1.05 if crouched else 1.5),
                                    (self.b, 0.9975 if crouched else 1.425)):
                self.assertEqual(actor.mover.crouched, crouched)
                self.assertAlmostEqual(self.run.match.combat._eye(actor)[1] - actor.mover.position[1],
                                       expected)
                self.assertEqual(self.target(actor).eye, expected)

    def test_low_cover_distinguishes_each_actual_standing_and_crouched_ray(self):
        for source in ('widow', 'mccree'):
            for crouched in (False, True):
                for blocked in (False, True):
                    with self.subTest(source=source, crouched=crouched, blocked=blocked):
                        self.setUp()
                        self.mccree()
                        actor, target = (self.a, self.b) if source == 'widow' else (self.b, self.a)
                        yaw = 0 if source == 'widow' else -32768
                        buttons = (CROUCH if crouched else 0) | (2 if source == 'widow' else 0)
                        # Use Widow's authored zero-angle scoped launch to isolate the eye ray.
                        for _ in range(100 if source == 'widow' else 4):
                            self.run.step({actor: (yaw, 0, buttons)})
                        authored = (1.05 if crouched else 1.5) if source == 'widow' else (
                            0.9975 if crouched else 1.425)
                        top = actor.mover.position[1] + authored + (0.025 if blocked else -0.025)
                        self.run.match.collision_world = World([
                            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                            (-5, 0, 5, 5, 0, 5, 5, top, 5),
                            (-5, 0, 5, 5, top, 5, -5, top, 5),
                        ])
                        before = self.health(target)
                        for _ in range(12):
                            self.run.step({actor: (yaw, 0, buttons | 1)})
                        if blocked:
                            self.assertEqual(self.health(target), before)
                            self.assertEqual(actor.client.messages(combat.HIT), [])
                        else:
                            self.assertLess(self.health(target), before)
                            self.assertTrue(actor.client.messages(combat.HIT))

    def test_incoming_mccree_primary_head_hit_uses_selected_implicit_scalar(self):
        self.mccree()
        direction = aim(self.run.match.combat._eye(self.b), (0, 1.4, 0))
        self.run.step({self.b: (*direction, 1)})
        self.assertEqual(self.health(self.a), 35)
        hit = self.b.client.messages(combat.HIT)[-1]['+0x78']
        self.assertEqual(hit['+0x4'], 140)
        self.assertTrue(hit['+0x8'] & combat.CRITICAL)

    def test_explicit_mccree_primary_scalar_overrides_selected_fallback(self):
        self.mccree()
        instance = next(i for i in self.b.body_script.component.instances.values()
                        if i.graph.index == 0x1D2)
        definition = instance.graph.node(31).field('m_6396149F')
        with patch.dict(definition, m_A872C70E={'$': 'STUConfigVarInt', 'm_value': 1}):
            direction = aim(self.run.match.combat._eye(self.b), (0, 1.4, 0))
            self.run.step({self.b: (*direction, 1)})
        self.assertEqual(self.health(self.a), 105)
        self.assertFalse(self.b.client.messages(combat.HIT)[-1]['+0x78']['+0x8'] & combat.CRITICAL)

    def test_fan_head_hit_keeps_authored_noncritical_scalar(self):
        self.mccree()
        direction = aim(self.run.match.combat._eye(self.b), (0, 1.4, 0))
        instance = next(i for i in self.b.body_script.component.instances.values()
                        if i.graph.index == 0x1D2)
        launch = instance.graph.node(37).field('m_5EC1EB38')
        # This check isolates the explicit damage scalar, not Fan accuracy.
        with patch.dict(launch, m_7F0FED02={'$': 'STUConfigVarInt', 'm_value': 0},
                        m_429A6C85={'$': 'STUConfigVarInt', 'm_value': 0}):
            self.run.step({self.b: (*direction, 2)})
        self.assertEqual(self.health(self.a), 125)
        self.assertFalse(self.b.client.messages(combat.HIT)[-1]['+0x78']['+0x8'] & combat.CRITICAL)
