"""Store the actual authored Assassin scene without invoking its native services."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_server_snapshot as fixture
from ow174.game import assassin_controller, enforcer_controller, npc
from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_component_vm
from ow174.game.server_snapshot import ServerSnapshotError, snapshot_digest
from ow174.game.script import assassin_movement


@unittest.skipUnless(os.environ.get("ORPHEUS_PVE_NPC_DATA")
                     and os.environ.get(enforcer_controller.SUPPLEMENT),
                     "requires original NPC catalog and controller supplement")
class AssassinSceneSnapshotTests(unittest.TestCase):
    def scene(self):
        helper = fixture.ServerSnapshotTests()
        session, _, players = helper.scene()
        match = players[0].match
        match.bots.clear()
        players[0].team = 0
        players[0].mover.position[:] = (0, 1, 0)
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_PVE_NPC_DATA"]) / "pve_npcs_174.json")
        definition = next(row for row in catalog.values() if row.name == "Assassin")
        bot = npc.spawn(match, definition, (0, 1, 8), team=1, pursue=True, authored_assassin=True)
        for _ in range(40):
            tick = match.tick + 1
            match.update(tick * .016, tick)
        component = bot.assassin_controller
        self.assertIsNone(component.fault)
        return helper, session, component

    def test_scene_revision_and_bound_lookup_storage_are_causal_and_read_only(self):
        helper, session, component = self.scene()
        world = component.world
        with patch.object(assassin_controller.Phase2World, "native", side_effect=AssertionError("native getter")):
            before = export_component_vm(component)
            server_before = snapshot_digest(helper.capture(session))
            self.assertEqual(export_component_vm(component), before)
            self.assertEqual(snapshot_digest(helper.capture(session)), server_before)
            context = world.query_contexts[component.entity]
            self.assertIs(context.lookup.__self__[component.entity].possession,
                          context.lookup(component.entity).possession)
            possession = context.lookup.__self__[component.entity].possession
            body = possession.current
            possession.current = 0
            try:
                self.assertNotEqual(export_component_vm(component), before)
                self.assertNotEqual(snapshot_digest(helper.capture(session)), server_before)
            finally:
                possession.current = body
            self.assertEqual(export_component_vm(component), before)
            self.assertEqual(snapshot_digest(helper.capture(session)), server_before)
            revision = world._ai_link_revision
            previous = revision.base
            revision.base = 1
            try:
                self.assertNotEqual(export_component_vm(component), before)
                self.assertNotEqual(snapshot_digest(helper.capture(session)), server_before)
            finally:
                revision.base = previous
            self.assertEqual(snapshot_digest(helper.capture(session)), server_before)

    def test_actual_movement_sample_override_has_exact_causal_storage(self):
        helper, session, component = self.scene()
        world = component.world
        sample = world.assassin_movement_sample(world.body)
        self.assertIs(type(sample), assassin_movement.Sample)
        original = world.sample
        world.sample = sample
        try:
            with patch.object(assassin_controller.Phase2World, "assassin_movement_sample",
                              side_effect=AssertionError("movement getter executed")):
                before = export_component_vm(component)
                server_before = snapshot_digest(helper.capture(session))
                world.sample = assassin_movement.Sample(sample.flags ^ assassin_movement.FLAGS, sample.axis)
                self.assertNotEqual(export_component_vm(component), before)
                self.assertNotEqual(snapshot_digest(helper.capture(session)), server_before)
                world.sample = sample
                self.assertEqual(export_component_vm(component), before)
                self.assertEqual(snapshot_digest(helper.capture(session)), server_before)
                object.__setattr__(sample, "unreviewed", True)
                try:
                    with self.assertRaises(BodyVMSnapshotError):
                        export_component_vm(component)
                    with self.assertRaises(ServerSnapshotError):
                        helper.capture(session)
                finally:
                    object.__delattr__(sample, "unreviewed")
        finally:
            world.sample = original

    def test_unreviewed_scene_fields_are_rejected(self):
        helper, session, component = self.scene()
        component.world.unreviewed = True
        try:
            with self.assertRaises(BodyVMSnapshotError):
                export_component_vm(component)
            with self.assertRaises(ServerSnapshotError):
                helper.capture(session)
        finally:
            del component.world.unreviewed
