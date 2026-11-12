"""Original live target controllers participate in the stored server graph."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_server_snapshot as snapshot_fixture
from ow174.game import enforcer_controller, npc
from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_component_vm
from ow174.game.server_snapshot import ServerSnapshotError, snapshot_digest


@unittest.skipUnless(os.environ.get("ORPHEUS_PVE_NPC_DATA")
                     and os.environ.get(enforcer_controller.SUPPLEMENT),
                     "requires original NPC catalog and controller supplement")
class NpcControllerSnapshotTests(unittest.TestCase):
    def scene(self):
        helper = snapshot_fixture.ServerSnapshotTests()
        session, _, players = helper.scene()
        match = players[0].match
        match.bots.clear()
        players[0].team = 0
        players[0].mover.position[:] = (0, 1, 0)
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_PVE_NPC_DATA"]) / "pve_npcs_174.json")
        actors = [npc.spawn(match, definition, (0, 1, 8), team=1, acquire=True)
                  for definition in catalog.values() if definition.hero in enforcer_controller.HOSTS]
        for _ in range(40):
            tick = match.tick + 1
            match.update(tick * .016, tick)
        controllers = [getattr(actor, enforcer_controller.HOSTS[actor.hero][0]) for actor in actors]
        self.assertEqual(len(controllers), 4)
        self.assertTrue(all(component.fault is None for component in controllers))
        return helper, session, players[0], controllers

    def test_live_controller_selection_is_causal_without_evaluating_getters(self):
        helper, session, target, controllers = self.scene()
        with patch.object(enforcer_controller.TargetWorld, "native", side_effect=AssertionError("evaluated native")), \
             patch.object(enforcer_controller.TargetWorld, "filtered_body_health", side_effect=AssertionError("read health")):
            before = snapshot_digest(helper.capture(session))
            self.assertEqual(snapshot_digest(helper.capture(session)), before)
            for component in controllers:
                with self.subTest(graph=next(iter(component.instances.values())).graph.index):
                    original = export_component_vm(component)
                    self.assertEqual(export_component_vm(component), original)
                    selected = component.world.selected
                    component.world.selected = target.body if selected is None else None
                    try:
                        self.assertNotEqual(export_component_vm(component), original)
                        self.assertNotEqual(snapshot_digest(helper.capture(session)), before)
                    finally:
                        component.world.selected = selected
                    self.assertEqual(snapshot_digest(helper.capture(session)), before)

    def test_unreviewed_controller_storage_is_rejected_by_both_exporters(self):
        helper, session, _, controllers = self.scene()
        component = controllers[0]
        component.world.unreviewed = True
        try:
            with self.assertRaises(BodyVMSnapshotError):
                export_component_vm(component)
            with self.assertRaises(ServerSnapshotError):
                helper.capture(session)
        finally:
            del component.world.unreviewed
