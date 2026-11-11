"""Manual NPC controller policy is causal state in the enclosing server export."""

import unittest
from unittest.mock import patch

import test_server_snapshot as fixtures

from ow174.game import npc
from ow174.game.mover import Mover, mover_data
from ow174.game.script import runtime
from ow174.game.server_snapshot import ServerSnapshotError, snapshot_digest


class NpcContextServerSnapshotTests(unittest.TestCase):
    def test_controller_component_policy_changes_full_server_hash_without_vm_execution(self):
        case = fixtures.ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        definition = npc.Definition(player.hero.guid, player.hero.body, "Manual snapshot actor", 200)
        # This controlled server's asset registry has no NPC body graph. An
        # explicit empty body isolates stored controller policy from VM behavior.
        with patch("ow174.game.match.graph.entity_body", return_value={"graphs": []}):
            bot = npc.spawn(player.match, definition, (0, 0, 8), team=1)
        self.assertIsNotNone(bot.body_script)
        world = bot.body_script.component.world
        with (patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated a variable")),
              patch.object(runtime.Component, "run_now", side_effect=AssertionError("snapshot ran the VM"))):
            before = snapshot_digest(case.capture(session))
            world.controller_components = (75,)
            permitted = snapshot_digest(case.capture(session))
            self.assertNotEqual(before, permitted)
            world.controller_components = (75, 92)
            self.assertNotEqual(permitted, snapshot_digest(case.capture(session)))
            world.controller_components = None
            self.assertEqual(before, snapshot_digest(case.capture(session)))
            world.manual_target = player.body
            self.assertNotEqual(before, snapshot_digest(case.capture(session)))
            world.manual_target = None
            player.match.combat.pins[bot.entity] = player.body
            self.assertNotEqual(before, snapshot_digest(case.capture(session)))
            player.match.combat.pins.clear()
            self.assertEqual(before, snapshot_digest(case.capture(session)))
            bot.effect_mover = Mover(
                bot.position, world=player.match.collision_world, data=mover_data(bot.body),
            )
            airborne = snapshot_digest(case.capture(session))
            self.assertNotEqual(before, airborne)
            bot.effect_mover.velocity = (0.0, 18.0, 0.0)
            self.assertNotEqual(airborne, snapshot_digest(case.capture(session)))
            bot.effect_mover = None
            self.assertEqual(before, snapshot_digest(case.capture(session)))
            world.unregistered_controller_cache = 1
            with self.assertRaises(ServerSnapshotError):
                case.capture(session)


if __name__ == "__main__":
    unittest.main()
