"""Real Echo input qualifies historical emissions for a newly joining human viewer."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_echo_combat import EchoPrimaryTests, EchoStickyTests
from test_game_combat import in_world

from ow174.game import echo_projectiles, tracer_pistol
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.world import OP_DESTROY

FIXTURE = json.loads((Path(__file__).resolve().parents[2]
                      / 'fixtures/behavior/echo-emission-observer-v1.json').read_text())


class EchoEmissionObserverTests(unittest.TestCase):
    def test_actual_pending_report_capture_participates_in_strict_vm_storage(self):
        run, shooter, _, _, angles = EchoPrimaryTests().session(
            distance=FIXTURE['input']['target_distance'])
        report_volley = nodes.WeaponVolley.report
        stored = []

        def report(state, until):
            report_volley(state, until)
            component = state.instance.component
            if component is not shooter.body_script.component or not component.shots:
                return
            pending = component.shots[-1]
            if len(pending) != 6:
                return
            self.assertIs(pending[0], state)
            self.assertIsNone(pending[4])  # Preserve the reserved Ashe launch slot.
            before = snapshot_digest(export_component_vm(component))
            component.shots[-1] = (*pending[:5], None)
            try:
                without_capture = snapshot_digest(export_component_vm(component))
            finally:
                component.shots[-1] = pending
            self.assertNotEqual(before, without_capture)
            self.assertEqual(before, snapshot_digest(export_component_vm(component)))
            stored.append(pending)

        with patch.object(nodes.WeaponVolley, 'report', report):
            run.step({shooter: (*angles, 1)})
        self.assertTrue(stored)
        capture = stored[0][5]
        self.assertTrue(all(rocket.shot.observer_activation is capture
                            for rocket in run.match.combat.rockets))

    def test_real_report_capture_precedes_mutable_state_consumption(self):
        for sticky in (False, True):
            with self.subTest(sticky=sticky):
                run, shooter, _, _, angles = EchoPrimaryTests().session(
                    distance=FIXTURE['input']['target_distance'])
                captures = []
                original_remember = tracer_pistol.remember
                shown = []
                show_others = run.match.combat.projectiles._show_others

                def remember(state, number, time, activation, *,
                             original_remember=original_remember, captures=captures):
                    original_remember(state, number, time, activation)
                    captured = echo_projectiles.volley_activation(state, activation)
                    if captured is not None:
                        captures.append((state, number, activation, captured))

                def show(*args, shown=shown, show_others=show_others, **kwargs):
                    show_others(*args, **kwargs)
                    members = kwargs.get('shots', (args[0],))
                    for member in members:
                        self.assertIs(member.observer_activation, kwargs['original_activation'])
                        self.assertTrue(member.observers)
                        self.assertTrue(all(d.activation is member.observer_activation
                                            for d in member.observers))
                    shown.append(members)

                with (patch.object(tracer_pistol, 'remember', side_effect=remember),
                      patch.object(run.match.combat.projectiles, '_show_others', side_effect=show)):
                    if sticky:
                        EchoStickyTests().launch(run, shooter, angles)
                    else:
                        run.step({shooter: (*angles, 1)})
                self.assertEqual(len(captures), 6 if sticky else 1)
                self.assertEqual(len(shown), 6 if sticky else 1)
                self.assertEqual([len(group) for group in shown], [1] * 6 if sticky else [3])
                frozen = tuple(item[3] for item in captures)
                self.assertTrue(all(item[1] is True for item in frozen))
                state = captures[0][0]
                self.assertFalse(state.active)
                for _, _number, activation, captured in captures:
                    self.assertEqual([key for key, _ in captured[2]],
                                     FIXTURE['expected']['capture_payload_keys'])
                    self.assertEqual(dict(captured[2])['counter'], activation[0])
                    self.assertEqual(dict(captured[2])['start'], activation[1])
                    self.assertIsNone(echo_projectiles.volley_activation(
                        state, (activation[0] + 1, activation[1])))
                for _ in range(10):
                    run.step({})
                self.assertEqual(tuple(item[3] for item in captures), frozen)

    def join(self, run, shooter, frames=FIXTURE['input']['join_settle_frames']):
        viewer = run.match.add_player(3, 'Emission viewer', shooter.hero.guid, shooter.team, False)
        in_world(run.match, viewer)
        run.match.switch_hero(viewer, heroes()[shooter.hero.guid])
        viewer.mover.position[0] += 30
        run.players.append(viewer)
        for _ in range(frames):
            run.step({})
        return viewer

    def test_partial_primary_missing_source_and_expired_launch_stay_stopped(self):
        run, shooter, _, _, angles = EchoPrimaryTests().session(
            distance=FIXTURE['input']['target_distance'])
        run.step({shooter: (*angles, 1)})
        fight = run.match.combat
        rockets = tuple(fight.rockets)
        self.join(run, shooter, frames=0)
        with patch.object(fight.projectiles, '_show_others') as show:
            echo_projectiles.observe_live(fight, rockets[0])
            self.assertEqual(show.call_count, 1)
            show.reset_mock()
            fight.rockets.remove(rockets[2])
            echo_projectiles.observe_live(fight, rockets[0])
            show.assert_not_called()
            fight.rockets.append(rockets[2])
            captured = rockets[2].shot.observer_activation
            rockets[2].shot.observer_activation = None
            echo_projectiles.observe_live(fight, rockets[0])
            show.assert_not_called()
            rockets[2].shot.observer_activation = captured
            script = shooter.body_script
            instance = script.component.instances.pop(rockets[0].shot.instance)
            echo_projectiles.observe_live(fight, rockets[0])
            show.assert_not_called()
            script.component.instances[instance.id] = instance
            rejected = replace(rockets[0].shot, echo_launch=None, observer_record=None,
                               observer_activation=None, observer_deadline=None)
            state = instance.states[rejected.state]
            wrong = dict(captured[2])
            wrong['counter'] += 1
            echo_projectiles.remember_launch(rejected, state, (0, 0, 0), (0, 0, 1), fight.tick + 100,
                                            (captured[0], captured[1], tuple(sorted(wrong.items()))))
            self.assertIsNone(rejected.echo_launch)
            self.assertIsNone(rejected.observer_activation)
            echo_projectiles.remember_launch(rejected, state, (0, 0, 0), (0, 0, 1), fight.tick + 100, None)
            self.assertIsNone(rejected.observer_activation)
            for rocket in rockets:
                for delivery in rocket.shot.observers:
                    delivery.deadline = fight.tick
            echo_projectiles.observe_live(fight, rockets[0])
            show.assert_not_called()

    def test_complete_old_and_current_primary_emissions_reach_joining_viewer(self):
        run, shooter, _, _, _ = EchoPrimaryTests().session(
            distance=FIXTURE['input']['target_distance'])
        fight = run.match.combat
        for _ in range(70):
            run.step({shooter: (0, 0, 1)})
            if len(fight.rockets) == 6:
                break
        rockets = tuple(fight.rockets)
        self.assertEqual(len(rockets), 6)
        self.assertEqual({r.shot.activation[0] for r in rockets}, {1, 2})
        originals = [r.shot.echo_launch for r in rockets]
        viewer = self.join(run, shooter, frames=0)
        queued = []
        queue_entities = viewer.client.queue_entities

        def queue(updates):
            for update in updates:
                deliveries = [d for r in rockets for d in r.shot.observers
                              if d.viewer is viewer and d.launch is update]
                if deliveries:
                    first = deliveries[0]
                    self.assertEqual(len(deliveries), 3)
                    self.assertTrue(all(d.creation.received for d in deliveries))
                    self.assertEqual(first.stream.base.states[first.volley_key], first.activation)
                    self.assertEqual(first.activation, next(
                        r.shot.observer_activation for r in rockets
                        if r.shot.activation[0] == first.record.activation))
                    queued.append(first.record.activation)
            return queue_entities(updates)

        with patch.object(viewer.client, 'queue_entities', side_effect=queue):
            for _ in range(FIXTURE['input']['join_settle_frames']):
                run.step({})
        self.assertEqual(queued, FIXTURE['input']['primary_activations'])
        self.assertTrue(all(r in fight.rockets for r in rockets))
        by_activation = {
            activation: [d for r in rockets if r.shot.activation[0] == activation
                         for d in r.shot.observers if d.viewer is viewer]
            for activation in (1, 2)
        }
        self.assertEqual({k: len(v) for k, v in by_activation.items()}, {1: 3, 2: 3})
        for rocket, original in zip(rockets, originals, strict=True):
            delivery, = [d for d in rocket.shot.observers if d.viewer is viewer]
            self.assertEqual(delivery.record.frame, original.frame)
            self.assertEqual(delivery.record.origin, original.origin)
            self.assertEqual(delivery.record.direction, original.direction)
            self.assertEqual(delivery.record.activation, original.activation)
            self.assertEqual(delivery.deadline, min(
                d.deadline for d in rocket.shot.observers if d.viewer is not viewer))
            self.assertTrue(delivery.creation.received)
            self.assertTrue(delivery.launch_arrival.received)
        self.assertEqual(len({id(ds[0].launch) for ds in by_activation.values()}), 2)
        self.assertEqual(len(fight.rockets), 6)
        ids = {d.entity for deliveries in by_activation.values() for d in deliveries}
        self.assertFalse(fight.observers.watches[(viewer.slot, shooter.body)].volleys)
        for _ in range(240):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertTrue(ids <= {item.entity for item in viewer.client.entities if item.op == OP_DESTROY})
        self.assertTrue(all(sum(d.viewer is viewer for d in r.shot.observers) == 1 for r in rockets))

    def test_six_actual_sticky_emissions_keep_independent_launches(self):
        run, shooter, _, _, angles = EchoPrimaryTests().session(
            distance=FIXTURE['input']['target_distance'])
        EchoStickyTests().launch(run, shooter, angles)
        rockets = tuple(run.match.combat.rockets)
        self.assertEqual(len(rockets), 6)
        records = [r.shot.echo_launch for r in rockets]
        self.assertEqual(len({record.frame for record in records}), 6)
        self.assertEqual([record.shot for record in records], list(range(6)))
        viewer = self.join(run, shooter)
        deliveries = [d for r in rockets for d in r.shot.observers if d.viewer is viewer]
        self.assertEqual(len(deliveries), 6)
        self.assertEqual(len({id(d.launch) for d in deliveries}), 6)
        self.assertTrue(all(d.creation.received and d.launch_arrival.received for d in deliveries))
