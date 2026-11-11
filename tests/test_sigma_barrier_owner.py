"""A scoped Barrier factory prerequisite, explicitly not Barrier combat acceptance."""

import os
import unittest
from unittest.mock import patch

import test_sigma_combat as fixtures

from ow174.game import sigma
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, runtime


class BarrierOwnerTests(unittest.TestCase):
    def owner(self):
        # This prerequisite fixture deliberately declares no model input. Full
        # actor/protection scenarios use the separately verified external model.
        with patch.dict(os.environ):
            os.environ.pop("ORPHEUS_SIGMA_BARRIER_MODEL", None)
            run, player, _enemy, _vitals = fixtures.SigmaCombatTests().arena()
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1B55)
        gate = instance.graph.node(64).field("m_condition")
        return run, player, instance, gate

    def test_the_actual_owner_predicate_uses_the_current_player_possession(self):
        _run, player, instance, gate = self.owner()
        self.assertEqual(instance.evaluate(gate["m_entity"]), expr.Entity(player.entity))
        self.assertTrue(instance.evaluate(gate))

    def test_actual_input_reaches_pet_state_without_claiming_an_entity_was_created(self):
        run, player, instance, _gate = self.owner()
        run.step({player: (0, 0, 2)})
        self.assertTrue(instance.states[4].active)
        # The next missing service remains visible; no placeholder entity or
        # successful combat assertion is manufactured for the Pet State.
        self.assertIsNone(instance.component.entity_var(512).value())

    def test_ended_match_does_not_fabricate_a_live_owner(self):
        run, player, instance, gate = self.owner()
        run.match.ended = True
        self.assertFalse(instance.evaluate(gate))
        self.assertIs(sigma.barrier_owner_query(player.body_script.world, gate["m_entity"], instance),
                      expr.EVALUATION_FAILED)

    def test_old_body_binding_does_not_follow_a_replacement_body(self):
        run, player, instance, gate = self.owner()
        old_world = player.body_script.world
        run.match.switch_hero(player, heroes()[SOLDIER])
        self.assertIsNone(old_world.match)  # normal retirement detaches the old world
        self.assertIs(sigma.barrier_owner_query(old_world, gate["m_entity"], instance), sigma.NOT_HANDLED)
        self.assertIs(sigma.barrier_owner_query(old_world, gate, instance), sigma.NOT_HANDLED)

    def test_unrelated_selectors_and_graphs_remain_unhandled(self):
        _run, player, instance, _gate = self.owner()
        world = player.body_script.world
        foreign = {"$": "STU_F7262B36", "m_801B298F": {"$": "STUConfigVarDynamic", "m_identifier": 99}}
        self.assertIs(sigma.barrier_owner_query(world, foreign, instance), sigma.NOT_HANDLED)
        other = next(i for i in player.body_script.component.instances.values()
                     if i.graph.index == sigma.GRASP)
        self.assertIs(sigma.barrier_owner_query(world, {"$": "STU_F7262B36",
                      "m_801B298F": {"$": "STU_B5A0CAF0"}}, other), sigma.NOT_HANDLED)

    def test_strict_native_execution_does_not_accept_the_reconstructed_binding(self):
        _run, player, instance, gate = self.owner()
        instance.component.strict = True
        self.assertIs(sigma.barrier_owner_query(player.body_script.world, gate, instance), sigma.NOT_HANDLED)
        with self.assertRaises(runtime.UnsupportedScriptError):
            player.body_script.world.native(gate["m_entity"], instance)


if __name__ == "__main__":
    unittest.main()
