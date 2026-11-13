"""Actual accepted flights own their source until existing side effects finish."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ashe_combat as combat_fixture
import test_ashe_dynamite as flight_fixture
import test_ashe_dynamite_clock as clock_fixture
import test_server_snapshot as snapshot_fixture

from ow174.game import combat, managed_projectiles
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, runtime
from ow174.game.server_snapshot import ServerSnapshotError, snapshot_digest


class ManagedProjectileSourceTests(unittest.TestCase):
    def contract(self):
        path = Path(__file__).parents[2] / 'fixtures/behavior/managed-projectile-source-v1.json'
        return json.loads(path.read_text())

    def cast(self):
        return flight_fixture.DynamiteTests().cast(distance=100)

    def assert_live(self, run, flight):
        source = getattr(flight, 'managed_source', None)
        self.assertIsNotNone(source)
        self.assertTrue(source.current())
        self.assertIs(managed_projectiles.lookup(run.match, source.entity), source)
        self.assertIs(run.match.body_components[source.entity], source.component)
        self.assertIs(source.flight, flight)
        self.assertEqual(source.position(), expr.Vec3(*flight.position))
        return source

    def test_actual_e_allocates_pending_source_and_follows_the_same_moving_flight(self):
        spec = self.contract()
        run, owner, _enemy, manager, flight = self.cast()
        source = self.assert_live(run, flight)
        self.assertEqual(source.definition, int(spec['definition'], 16))
        self.assertEqual(source.born_world_tick, flight.born)
        self.assertNotEqual(source.entity, owner.body)
        self.assertNotEqual(source.entity, flight.shot.entity)
        self.assertEqual([i.graph.index for i in source.component.instances.values()], spec['pendingGraphs'])
        self.assertTrue(all(i.initialization_pending for i in source.component.instances.values()))
        self.assertFalse(any(i.states for i in source.component.instances.values()))
        self.assertEqual((source.component.frame, source.component.now), (0, 0))
        self.assertFalse(source.association_known)
        self.assertIsNone(source.associated_component)
        self.assertNotIn(source.entity, run.match.combat.vitals)
        self.assertNotIn(source.entity, {target.entity for target in run.match.combat._targets()})
        original = source.position()
        for _ in range(3):
            run.step({})
        self.assert_live(run, flight)
        self.assertNotEqual(source.position(), original)
        self.assertEqual(owner.body_script.world.position(source.entity), source.position())
        self.assertTrue(owner.body_script.world.script_component_present(source.entity))
        original1502 = next(i for i in source.component.instances.values() if i.graph.index == 0x1502)
        self_entity = original1502.evaluate(original1502.graph.node(50).field('m_position'))
        self.assertEqual(self_entity, expr.Entity(source.entity))
        self.assertEqual(source.component.world.position(self_entity.id), source.position())
        self.assertIs(source.component.world.physics_explosion(None), NotImplemented)
        self.assertEqual(len(manager.grenades), 1)
        generation = source.generation
        run.match.switch_hero(_enemy, heroes()[0x02E0000000000200])
        for _ in range(30):
            run.step({})
        combat_fixture.AsheCombatTests().press(run, _enemy, 16)
        other, = [item for item in manager.grenades if item is not flight]
        second = self.assert_live(run, other)
        self.assertGreater(second.generation, generation)
        self.assertNotEqual(second.entity, source.entity)
        self.assert_live(run, flight)

    def test_actual_fuse_and_owner_shot_keep_source_live_through_one_dispatch(self):
        for trigger in ('fuse', 'owner-shot'):
            with self.subTest(trigger=trigger):
                run, owner, _enemy, manager, flight = self.cast()
                source = self.assert_live(run, flight)
                fight = run.match.combat
                for _ in range(40):
                    run.step({})
                calls = []
                explode = fight._explode

                def observed(*args, calls=calls, source=source, run=run, flight=flight,
                             manager=manager, owner=owner, explode=explode, **kwargs):
                    calls.append(source.entity)
                    self.assert_live(run, flight)
                    self.assertTrue(flight.detonating)
                    self.assertFalse(manager.detonate(flight))
                    manager.retire(owner.body)  # retirement reentry must not destroy a live dispatch source
                    self.assert_live(run, flight)
                    result = explode(*args, **kwargs)
                    self.assert_live(run, flight)
                    return result

                with patch.object(fight, '_explode', side_effect=observed):
                    if trigger == 'fuse':
                        while run.frame <= flight.fuse and not source.retired:
                            run.step({})
                    else:
                        # Aim the real Viper input at the existing flight's next physical pose.
                        at = tuple(p + v * combat.TICK
                                   for p, v in zip(flight.position, flight.velocity, strict=True))
                        yaw, pitch = combat_fixture.aim(fight._eye(owner), at)
                        run.step({owner: (yaw, pitch, 1)})
                self.assertEqual(calls, [source.entity])
                self.assertTrue(source.retired)
                self.assertFalse(source.current())
                self.assertIsNone(managed_projectiles.lookup(run.match, source.entity))
                self.assertNotIn(source.entity, run.match.body_components)
                self.assertFalse(manager.detonate(flight))
                with self.assertRaises(managed_projectiles.SourceUnavailable):
                    source.position()

    def test_exception_and_replaced_component_cleanup_cannot_revive_or_unregister_another_lifetime(self):
        run, _owner, _enemy, manager, flight = self.cast()
        source = self.assert_live(run, flight)

        class AbortDispatch(RuntimeError):
            pass

        def failed(*args, **kwargs):
            self.assert_live(run, flight)
            raise AbortDispatch('fixture failure in existing explosion callback')

        with patch.object(run.match.combat, '_explode', side_effect=failed), self.assertRaises(AbortDispatch):
            manager.detonate(flight)
        self.assertTrue(source.retired)
        self.assertFalse(source.retire())
        self.assertFalse(any(item is flight for item in manager.grenades))
        replacement = runtime.Component(source.entity)
        run.match.body_components[source.entity] = replacement
        self.assertFalse(source.retire())
        self.assertIs(run.match.body_components[source.entity], replacement)
        self.assertIsNone(managed_projectiles.lookup(run.match, source.entity))
        del run.match.body_components[source.entity]
        with self.assertRaises(managed_projectiles.SourceUnavailable):
            managed_projectiles.register(run.match.combat, manager, flight,
                definition=source.definition, born_world_tick=flight.born)
        run, _owner, _enemy, _manager, flight = self.cast()
        source = self.assert_live(run, flight)
        replacement = runtime.Component(source.entity)
        run.match.body_components[source.entity] = replacement
        self.assertFalse(source.current())
        self.assertTrue(source.retire())
        self.assertIs(run.match.body_components[source.entity], replacement)
        del run.match.body_components[source.entity]

    def test_shifted_owner_clock_and_actual_body_replacement_keep_lifetimes_separate(self):
        run, owner, flight = clock_fixture.DynamiteClockTests().throw(6, epoch_offset=3000)
        source = self.assert_live(run, flight)
        self.assertEqual(source.born_world_tick, flight.born)
        self.assertNotEqual(source.born_world_tick, flight.shot.frame)
        self.assertEqual(source.component.frame, 0)
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertTrue(source.retired)
        self.assertIsNone(managed_projectiles.lookup(run.match, source.entity))
        for _ in range(12):
            run.step({})
        self.assertFalse(source.current())
        self.assertFalse(source.retire())

    def test_actual_e_snapshot_records_shared_source_identity_without_querying_pose(self):
        fixture = snapshot_fixture.ServerSnapshotTests()
        session, _peers, players = fixture.scene()
        owner = players[0]
        self.assertTrue(session.apply('switch_hero',
                                     {'account': owner.account_lo, 'hero': 0x02E0000000000200}))
        match = owner.match

        def step(buttons):
            frame = max(owner.body_script.frame, owner.commands.last_frame or 0) + 1
            match.update(match.combat.now + combat.TICK, match.tick + 1)
            owner.take_commands([Command(frame, buttons=buttons)])

        for _ in range(35):
            step(0)
        for index in range(40):
            step(16 if index < 5 else 0)
            if owner.match.combat.dynamite.grenades:
                break
        self.assertTrue(owner.match.combat.dynamite.grenades, {
            'hero': hex(owner.hero.guid), 'spawned': owner.spawned, 'has_body': owner.has_body,
            'world': match.tick, 'owner': owner.body_script.frame, 'last': owner.commands.last_frame,
            'dead': match.combat.is_dead(owner), 'held': owner.body_script.component.held,
            'fault': str(owner.body_script.component.fault),
            'throw': [(s.index, type(s).__name__, s.active)
                      for i in owner.body_script.component.instances.values() if i.graph.index == 0x14FD
                      for s in i.states.values()],
        })
        flight, = owner.match.combat.dynamite.grenades
        source = flight.managed_source
        self.assertIsNotNone(source)
        with patch.object(managed_projectiles.Source, 'position',
                          side_effect=AssertionError('no live pose getter')):
            before = snapshot_digest(fixture.capture(session))
            source.generation += 1
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
            source.generation -= 1
            self.assertEqual(snapshot_digest(fixture.capture(session)), before)
            source.unregistered_probe = True
            with self.assertRaises(ServerSnapshotError):
                fixture.capture(session)
            del source.unregistered_probe
            self.assertEqual(snapshot_digest(fixture.capture(session)), before)
        fight = owner.match.combat
        saved = fight.next_bot
        fight.next_bot = source.entity
        with self.assertRaises(ValueError):
            fight.allocate_combat_entity()
        fight.next_bot = saved

    def test_missing_definition_keeps_the_real_flight_explicitly_unbound(self):
        run, owner, _enemy = combat_fixture.AsheCombatTests().fight(distance=100)
        original = graph.entity_body
        fight = run.match.combat
        allocation = fight.next_bot

        def without_source(definition):
            return None if definition == managed_projectiles.DYNAMITE else original(definition)

        with patch.object(graph, 'entity_body', side_effect=without_source):
            combat_fixture.AsheCombatTests().press(run, owner, 16)
        flight, = fight.dynamite.grenades
        self.assertIsNone(flight.managed_source)
        self.assertEqual(fight.next_bot, allocation)
        self.assertEqual(fight.managed_source_generation, 0)


if __name__ == '__main__':
    unittest.main()
