"""Persistent effects keep launch-body identity through replacement and death."""

import unittest
from unittest.mock import patch

import test_game_moira as fixtures
import test_game_moira_orbs as orb_fixtures

from ow174.game import combat
from ow174.game.content import heroes
from ow174.game.script import expr, nodes, runtime
from ow174.game.world import HEALTH_POOL


class MoiraEffectIdentityTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight
    launch_orb = orb_fixtures.MoiraOrbTests.launch

    def test_lethal_rifle_retires_fade_buffer_and_pending_orb_before_respawn(self):
        for skill, index, state_index, initial, pending in (
            ("Fade", 0x0F7C, 9, 8, 8),
            ("healing orb", 0x10E4, 0, 16, 1),
            ("damage orb", 0x10E4, 0, 16, 2),
        ):
            with self.subTest(skill=skill):
                run, source, target, _ = self.setup_fight(ally=False)
                fight = run.match.combat
                old_body = source.body
                script = source.body_script
                ability = next(i.states[state_index] for i in script.component.instances.values()
                               if i.graph.index == index)
                own = next(t for t in fight._targets() if t.owner is source)
                health = fight.vitals_of(own)
                run.step({source: (0, 0, initial)})
                if skill == "Fade":
                    for _ in range(460):
                        if 0 < ability.remaining() <= 0.12:
                            break
                        run.step({})
                    self.assertGreater(ability.remaining(), 0)
                    self.assertLessEqual(ability.remaining(), 0.12)
                    run.step({source: (0, 0, pending)})
                    self.assertTrue(ability.presses)
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                else:
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                health.damage(health.total - 1)
                stale = source.mover.last_command
                run.step({source: (0, 0, 0 if skill == "Fade" else pending), target.owner: (-32768, 0, 1)})
                if skill != "Fade":
                    stale = source.mover.last_command
                self.assertTrue(fight.is_dead(source))
                self.assertEqual(health.killer, target.entity)
                self.assertFalse(fight.moira_orbs)
                for _ in range(230):
                    run.step({})
                    if source.body != old_body:
                        break
                self.assertNotEqual(source.body, old_body)
                self.assertFalse(fight.is_dead(source))
                fresh = next(i.states[state_index] for i in source.body_script.component.instances.values()
                             if i.graph.index == index)
                self.assertIsNot(fresh, ability)
                source.take_commands([stale])
                for _ in range(20):
                    run.step({})
                self.assertEqual(fresh.remaining(), 0)
                self.assertFalse(fresh.flags & nodes.Ability.RUNNING)
                self.assertFalse(fight.moira_orbs)
                if skill == "Fade":
                    run.step({source: (0, 0, 8)})
                    self.assertTrue(fresh.flags & nodes.Ability.RUNNING)
                else:
                    orb = self.launch_orb(run, source, healing=pending == 1)
                    self.assertEqual(orb.body, source.body)

    def test_surviving_orb_kill_keeps_launch_body_in_health_and_death_notices(self):
        run, source, target, health = self.setup_fight(ally=False)
        fight = run.match.combat
        orb = self.launch_orb(run, source)
        old = source.body
        self.assertTrue(run.match.switch_hero(source, heroes()[fixtures.MOIRA]))
        self.assertNotEqual(old, source.body)
        self.assertIn(orb, fight.moira_orbs)
        # A surviving launched orb's near-lethal suffix, without changing its
        # captured identity, amount, allegiance, operation or energy budget.
        orb.position = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        orb.speed = 0
        health.now[HEALTH_POOL] = 0.4
        with patch.object(runtime, 'send_message', wraps=runtime.send_message) as messages:
            run.step({})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, old)
        notice = target.owner.client.messages(combat.DEATH_NOTICE)[-1]
        self.assertEqual(notice['+0x7C']['+0x0'], old)
        elimination = source.client.messages(combat.KILL_NOTICE)[-1]
        self.assertEqual(elimination['+0x80']['+0x0'], old)
        deaths = [call for call in messages.call_args_list if call.args[1] == combat.DIED]
        self.assertEqual(len(deaths), 1)
        self.assertEqual(deaths[0].args[2][combat.KILLER], expr.Entity(old))
        self.assertEqual(deaths[0].args[3], old)
