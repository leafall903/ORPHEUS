"""Actual Match registry bindings; no successful target/query-state injection."""

import os
import unittest
from unittest.mock import patch

import test_dva_factory as factory

from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_body_vm, snapshot_digest
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import statescript_mods
from ow174.game.script import entity_queries, expr, runtime


@unittest.skipUnless(os.environ.get('ORPHEUS_DVA_RUNTIME'), 'requires actual mech metadata')
class DvaQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        factory.DvaFactoryTests.setUpClass()

    def arena(self):
        helper = factory.DvaFactoryTests()
        run, pilot, zen, actor, _, _ = helper.arena()
        instance = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x026A)
        return run, pilot, zen, actor, instance

    def query(self, instance, kind, entity):
        return instance.evaluate({'$': kind, 'm_entity': expr.Entity(entity)})

    @staticmethod
    def move(run, pilot, *, forward=0, right=0, buttons=0, yaw=0):
        run.frame += 1
        run.match.update(run.frame*.016, run.frame)
        for player in run.players:
            player.client.queue_entities(run.match.remote_movements(player))
        for player in run.players:
            command = (Command(run.frame, yaw=yaw, forward=forward, right=right, buttons=buttons)
                       if player is pilot else Command(run.frame))
            player.take_commands([command])

    def test_actual_summon_recognizes_created_mech_and_reaches_authored_seat_state(self):
        run, pilot, _, actor, instance = self.arena()
        branch = next(s for s in instance.states.values() if s.node.pos == 28)
        self.assertIs(branch.current, True)
        self.assertTrue(branch.polled)
        self.assertTrue(self.query(instance, entity_queries.EXISTS, actor.entity))
        for _ in range(130):
            run.step({})
        self.assertTrue(next(s for s in instance.states.values() if s.node.pos == 20).active)
        self.assertFalse(any(s.active for s in instance.states.values() if s.node.pos == 65))
        self.assertNotEqual(pilot.body, actor.entity)  # seat carrier still has no possession service
        self.assertFalse(pilot.body_script_failed)

    def test_native_backend_uses_actual_controller_body_and_missing_identity_records(self):
        _, pilot, _, actor, instance = self.arena()
        for entity in (pilot.entity, pilot.body, actor.entity, runtime.GAME_MODE_ENTITY):
            self.assertTrue(self.query(instance, entity_queries.EXISTS, entity))
        self.assertFalse(self.query(instance, entity_queries.EXISTS, 0))
        self.assertFalse(self.query(instance, entity_queries.EXISTS, 0xC000FFFF))
        current = self.query(instance, entity_queries.POSSESSED, pilot.entity)
        self.assertEqual(current, expr.Entity(pilot.body))
        self.assertEqual(self.query(instance, entity_queries.POSSESSED, actor.entity), expr.Entity(0))

    def test_foreign_member_reads_real_received_health_without_creating_or_aliasing_slots(self):
        run, pilot, zen, actor, instance = self.arena()
        context = runtime.InstanceContext(instance, None)
        component = actor.body_script.component
        self.assertEqual(context.member(actor.entity, 253), 600)
        self.assertEqual(context.member(pilot.body, 253), 150)
        before = set(component.vars)
        self.assertIsNone(context.member(actor.entity, 987654))
        self.assertEqual(set(component.vars), before)
        self.assertIsNone(context.member(0xC000FFFF, 253))
        angles = factory.aim(run.match.combat._eye(zen),
                             (actor.position[0], actor.position[1]+1, actor.position[2]))
        run.step({zen: (*angles, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(context.member(actor.entity, 253), 557)

    def test_member_dependency_polls_without_linking_foreign_var_watchers(self):
        _, _, _, actor, instance = self.arena()
        watcher = next(s for s in instance.states.values() if s.node.pos == 28)
        slot = actor.body_script.component.entity_var(253)
        before = set(slot.watchers)
        self.assertEqual(runtime.InstanceContext(instance, watcher).member(actor.entity, 253), 600)
        self.assertTrue(watcher.polled)
        self.assertEqual(set(slot.watchers), before)

    def test_registry_snapshot_is_read_only_causal_and_rejects_unknown_callbacks(self):
        _, pilot, _, _, _ = self.arena()
        script = pilot.body_script
        context = script.world.dva_queries.context
        with patch.object(runtime.Var, 'value', side_effect=AssertionError('no evaluation')):
            before = snapshot_digest(export_body_vm(script))
            context.environment_present = False
            self.assertNotEqual(before, snapshot_digest(export_body_vm(script)))
        context.environment_present = True
        context.lookup = lambda entity: None
        with self.assertRaises(BodyVMSnapshotError):
            export_body_vm(script)

    def test_source_retirement_retires_registry_and_does_not_bind_replacement_body(self):
        run, pilot, _, actor, _ = self.arena()
        old = pilot.body_script
        registry = old.world.dva_queries
        run.match.switch_hero(pilot, heroes()[SOLDIER])
        self.assertTrue(old.retired)
        self.assertTrue(registry.context.retired)
        self.assertIsNone(registry.lookup(actor.entity))
        self.assertIs(old.world.entity_query_context(old.entity), NotImplemented)
        self.assertIsNone(pilot.body_script.world.dva_queries)

    def test_actual_summon_suppresses_all_directional_inputs_but_keeps_raw_bytes_and_look(self):
        for forward, right in ((127, 0), (-127, 0), (0, 127), (0, -127)):
            with self.subTest(forward=forward, right=right):
                run, pilot, _, _, _ = self.arena()
                before = tuple(pilot.mover.position)
                for _ in range(20):
                    self.move(run, pilot, forward=forward, right=right, yaw=4096)
                self.assertEqual((pilot.mover.position[0], pilot.mover.position[2]), (before[0], before[2]))
                self.assertEqual(pilot.mover.throttles, (right, forward))
                self.assertEqual(pilot.mover.input_throttles, (right, forward))
                self.assertEqual(pilot.mover.yaw, 4096)

    def test_summon_input_modifiers_begin_on_next_movement_frame_without_erasing_inertia(self):
        fixtures = [factory.DvaFactoryTests().arena(cast=False) for _ in range(2)]
        for _, run, pilot, _ in fixtures:
            before = tuple(pilot.mover.position)
            pilot.body_script.set_ult_charge(pilot.body_script.component.entity_var(338).value())
            self.move(run, pilot, forward=127, buttons=0x20)
            self.assertGreater(pilot.mover.position[2], before[2])  # Q's own movement precedes its scopes
            rows = statescript_mods(pilot.body_script.component).values()
            self.assertTrue({17, 18, 19} <= {row[0] for row in rows})
        for _ in range(10):
            for held, (_, run, pilot, _) in zip((True, False), fixtures, strict=True):
                self.move(run, pilot, forward=127 if held else 0)
            first, second = (item[2].mover for item in fixtures)
            self.assertEqual(tuple(first.position), tuple(second.position))
            self.assertEqual(tuple(first.velocity), tuple(second.velocity))
            self.assertEqual(first.throttles, (0, 127))
            self.assertEqual(second.throttles, (0, 0))

    def test_hero_replacement_releases_summon_input_modifiers(self):
        run, pilot, _, _, _ = self.arena()
        run.match.switch_hero(pilot, heroes()[SOLDIER])
        before = tuple(pilot.mover.position)
        for _ in range(15):
            self.move(run, pilot, forward=127)
        self.assertGreater(pilot.mover.position[2]-before[2], .2)
