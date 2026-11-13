"""Actual Mercy inputs and separately bounded original node64 parameter reads."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_mercy_guardian as guardian
from test_game_combat import Run, aim
from test_game_mercy_staff import SCENE

from ow174.game import bodytrace, movelog, orisa_halt
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.regeneration import MERCY

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/behavior/body-position-provider-v1.json'


class BodyPositionProviderTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(FIXTURE.read_text())
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, 'ENABLED', False))

    def melee_fixture(self):
        run = Run(SCENE, ('Mercy', 'Recipient'))
        owner, target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(owner, heroes()[MERCY])
        for _ in range(35):
            run.step({})
        owner.team, target.team = 0, 1
        x, y, z = owner.mover.position
        target.mover.position[:] = (x, y, z + 1.5)
        target.mover.body = None
        yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 1, z + 1.5))
        return run, owner, target, yaw, pitch

    def test_real_melee_resolves_eye_expression_without_extra_character_force(self):
        run, owner, target, yaw, pitch = self.melee_fixture()
        rows = []
        original = orisa_halt.HaltPhysicsExplosion.on_enter

        def enter(state):
            if (state.instance.graph.index, state.node.pos) == (0x43, 43):
                point = state.evaluate(state.node.field('m_position'), watch=False)
                direction = state.evaluate(state.node.field('m_direction'), watch=False)
                eye = run.match.combat._eye(owner)
                expected = tuple(a + b * self.data['expected']['melee_forward_offset']
                                 for a, b in zip(eye, direction, strict=True))
                rows.append((point, expected))
            return original(state)

        with patch.object(orisa_halt.HaltPhysicsExplosion, 'on_enter', enter):
            run.step({owner: (yaw, pitch, 0x800)})
        self.assertEqual(len(rows), 1)
        point, expected = rows[0]
        self.assertIsInstance(point, expr.Vec3)
        for actual, wanted in zip(point, expected, strict=True):
            self.assertAlmostEqual(actual, wanted, places=5)
        self.assertEqual(run.match.combat.vitals[target.body].total, 170)
        # The shared ring producer is supplied by the integration branch.
        # This provider must never add another character impulse of its own.
        self.assertLessEqual(run.match.combat.knockbacks.serial, 1)

    def test_real_guardian_uses_shared_position_fallback(self):
        fixture = guardian.GuardianAngelTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.step(1, guardian.SHIFT)
        state = fixture.instance.states[43]
        actual = state.evaluate(state.node.field('m_position'), watch=False)
        position = fixture.mercy.mover.position
        direction = state.evaluate(state.node.field('m_direction'), watch=False)
        self.assertIsInstance(actual, expr.Vec3)
        for value, origin, forward in zip(actual, position, direction, strict=True):
            self.assertAlmostEqual(value, origin + forward * self.data['expected']['guardian_forward_offset'])

    def test_original_node64_typed_selector_is_bounded_to_known_body_pose(self):
        _run, owner, target, _, _ = self.melee_fixture()
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x43)
        config = instance.graph.node(64).field('m_position')
        slot = instance.find_var(0, 536)
        saved = slot.base
        try:
            target.mover.position[:] = self.data['input']['selected_body_position']
            slot.base = expr.Entity(target.body)
            actual = instance.evaluate(config)
            self.assertIsInstance(actual, expr.Vec3)
            self.assertEqual(list(actual), self.data['expected']['selected_body_position'])
            target.mover.position[:] = self.data['input']['valid_origin']
            self.assertEqual(list(instance.evaluate(config)), self.data['expected']['valid_origin'])
            slot.base = expr.Entity(self.data['input']['unknown_entity'])
            self.assertIsNone(instance.evaluate(config))
            slot.base = target.body
            self.assertIsNone(instance.evaluate(config))
        finally:
            slot.base = saved
