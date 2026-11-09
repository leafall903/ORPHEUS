"""Actual Bastion reload/repair/reconfigure inputs retire notification markers."""

import unittest
from unittest.mock import patch

import test_bastion_combat as fixtures

from ow174.game.script.runtime import State


class BastionNotificationLifetimeTests(unittest.TestCase):
    def test_repeated_reload_repair_and_reconfigure_retire_pending_markers(self):
        stale = []
        original = State.end

        def observe(state, finished):
            active = state.active
            original(state, finished)
            if active and not state.active and (state.flags_changed or state.flags_check):
                stale.append((state.instance.graph.index, state.node.state))

        with patch.object(State, 'end', observe):
            for sentry, clip, damage in ((False, 35, 20), (True, 300, 15)):
                with self.subTest(sentry=sentry):
                    c = fixtures.BastionCombatTests()
                    script = c.fixture()
                    if sentry:
                        c.configure()
                    weapon = next(i for i in script.component.instances.values()
                                  if i.graph.index == (0x13A if sentry else 0x12E))
                    target_aim = c.command_aim
                    for _ in range(3):
                        own = c.health(c.player)
                        own.damage(50)
                        c.fight._sync_body_health(c.player, own)
                        c.command_aim = (16384, 0)
                        c.step(1, 10)
                        c.step(0, 2)
                        c.step(0x400)
                        c.step(0, 19)
                        c.step(2, 50)
                        c.step(0, 150)
                        c.step(0x400)
                        c.step(0, 200)
                        self.assertEqual(weapon.vars[53].value(), clip)
                        before = c.health(c.enemy).total
                        c.command_aim = target_aim
                        c.step(1)
                        self.assertEqual(weapon.vars[53].value(), clip - 1)
                        self.assertEqual(before - c.health(c.enemy).total, damage)
                        c.step(0, 10)
                    c.configure()
                    c.configure()
        self.assertEqual(stale, [])
