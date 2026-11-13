"""The actual landed trap is visible to the existing cross-entity VM service."""

import unittest
from unittest.mock import patch

import test_junkrat_traps as traps

from ow174.game import junkrat_actor, server_snapshot
from ow174.game.link import LinkCipher
from ow174.game.script import runtime
from ow174.game.server import Client, GameServer


class TrapRegistrationTests(unittest.TestCase):
    def test_actual_landing_foreign_reads_and_owner_replacement_unregister_same_vm(self):
        scene = traps.JunkratTrapTests()
        scene.setUp()
        server = GameServer()
        server.matches.append(scene.run.match)
        for name in ("environment", "skin_of", "emote_aliases_of", "emote_blend_trees_of",
                     "wheel_cosmetics_of"):
            setattr(server, name, getattr(scene.run.match, name))
        for number, viewer in enumerate(scene.run.players, 1):
            client = Client(server, ("memory.test", 45000 + number), number,
                            LinkCipher(bytes([number]) * 32, bytes([number + 10]) * 32), viewer)
            server.clients[number] = client
            viewer.client = client
            client.open = True
        trap = scene.throw()
        match = scene.run.match
        component = trap.actor_component
        owner_world = scene.player.body_script.component.world
        self.assertTrue(trap.parked)
        self.assertTrue(owner_world.script_component_present(trap.entity))
        self.assertIs(match.body_components[trap.entity], component)
        # This is the existing authored entity slot from the actual0033 VM,
        # not a second variable store or a compatibility actor clone.
        slot = component.entity_var(232, create=False)
        self.assertIsNotNone(slot)
        self.assertIs(owner_world.entity_var_slot(trap.entity, 232), slot)
        self.assertEqual(owner_world.entity_var(trap.entity, 232), slot.value())
        self.assertIsNone(trap.pet_owner)

        with patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            document = server_snapshot.export_server_state(
                server, collision_provider=match.collision_provider)
        self.assertTrue(document["nodes"])

        self.assertTrue(match.switch_hero(scene.player, scene.enemy.hero))
        scene.step()
        self.assertFalse(component.instances)
        self.assertNotIn(trap.entity, match.body_components)
        reader_world = scene.enemy.body_script.component.world
        self.assertFalse(reader_world.script_component_present(trap.entity))
        self.assertIsNone(reader_world.entity_var_slot(trap.entity, 232))

        # A delayed callback from the retired trap may not remove a component
        # which has since acquired the same identity through the canonical API.
        replacement = runtime.Component(trap.entity, world=component.world)
        match.register_body_component(replacement)
        junkrat_actor.retire(trap)
        self.assertIs(match.body_components[trap.entity], replacement)
        match.unregister_body_component(replacement)
