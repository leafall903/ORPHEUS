"""Real Moira input batching: event epoch, selection and orb world effects."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_moira_absorption as absorption_fixtures
import test_game_moira_orbs as orb_fixtures

from ow174.game import combat, moira, world
from ow174.game.commands import MAX_COMMANDS, Command


class MoiraOrbBatchClockTests(unittest.TestCase):
    contract = json.loads((Path(__file__).resolve().parents[2]
                           / "fixtures/behavior/moira-orb-batch-clock-v1.json").read_text())

    def sample(self, healing, batch, scenario):
        if scenario == 'absorb':
            helper = absorption_fixtures.MoiraAbsorptionTests()
            run, owner, _sigma, grasp, health = helper.fight()
            target_health = health[1]
        else:
            run, owner, _, target_health = orb_fixtures.MoiraOrbTests().setup_fight(
                ally=healing, distance=1000 if scenario == 'flight' else 3)
            grasp = None
        if scenario == 'budget':
            owner.body_script.component.set(
                12618 if healing else 12619, self.contract['input']['budget_scalar'])
        start = run.frame
        epoch = owner.body_script.moira_birth
        fight = run.match.combat
        retained = []
        shots = []
        events = []
        original = moira.orb_fired

        def fired(*args, **kwargs):
            accepted = original(*args, **kwargs)
            if accepted:
                orb = fight.moira_orbs[-1]
                retained.append(orb)
                shots.append(orb.shot)
                events.append((orb.shot.frame, orb.shot.activation, orb.last_tick, orb.direction))
            return accepted

        with patch.object(moira, 'orb_fired', side_effect=fired):
            for end in range(start + batch, start + self.contract['input']['commands_until'] + 1, batch):
                run.match.update(end * combat.TICK, end)
                commands = [
                    Command(frame, yaw=0, pitch=0,
                            buttons=(16 if frame == start + 1 else
                                     (1 if healing else 2)
                                     if frame <= start + self.contract['input']['choice_until'] else 0))
                    for frame in range(end - batch + 1, end + 1)
                ]
                owner.take_commands(commands)
                owner.take_commands(commands)  # network retransmission cannot repeat the cast
            run.match.update((start + 112) * combat.TICK, start + 112)
        self.assertEqual(owner.body_script.moira_birth, epoch)
        self.assertEqual(len(retained), self.contract['expected']['casts'])
        self.assertEqual(events[0][0], start + self.contract['expected']['event_frame_offset'])
        orb = retained[0]
        self.assertEqual(len(events), 1)
        self.assertIsNone(owner.body_script.component.fault)
        result = {'event': events[0], 'position': orb.position, 'flown': orb.flown,
                  'left': orb.left, 'energy': orb.energy, 'health': target_health.total,
                  'hits': bool(owner.client.messages(combat.HIT)),
                  'absorbed': grasp.vars[21312].value() if grasp is not None else None}
        if scenario == 'flight':
            for tick in range(start + 113, start + 500):
                run.match.update(tick * combat.TICK, tick)
                if orb not in fight.moira_orbs:
                    result['retired'] = tick
                    break
            self.assertIn('retired', result)
        self.assertNotIn(orb, fight.moira_orbs)
        for tick in range(fight.tick + 1, fight.tick + 4):
            run.match.update(tick * combat.TICK, tick)
        for delivery in shots[0].observers:
            self.assertTrue(delivery.finished)
            self.assertEqual(sum(u.entity == delivery.record.ids[0] and u.op == world.OP_DESTROY
                                 for u in delivery.client.entities), 1)
        return result

    def compare(self, scenario):
        for healing in self.contract['input']['healing']:
            expected = None
            for batch in self.contract['input']['batch_sizes']:
                with self.subTest(scenario=scenario, healing=healing, batch=batch):
                    actual = self.sample(healing, batch, scenario)
                    if expected is None:
                        expected = actual
                    self.assertEqual(actual['event'], expected['event'])
                    for key in ('flown', 'left', 'energy', 'health'):
                        self.assertAlmostEqual(actual[key], expected[key], places=6)
                    for actual_axis, expected_axis in zip(
                            actual['position'], expected['position'], strict=True):
                        self.assertAlmostEqual(actual_axis, expected_axis, places=6)
                    self.assertEqual(actual.get('retired'), expected.get('retired'))
                    self.assertEqual(actual['absorbed'], expected['absorbed'])
                    self.assertEqual(actual['hits'], expected['hits'])
                    if scenario == 'budget':
                        self.assertEqual(actual['energy'], 0)
                        budget = (300 if healing else 200) * self.contract['input']['budget_scalar']
                        self.assertAlmostEqual(actual['health'], 100 + budget * (1 if healing else -1))
                        self.assertEqual(actual['hits'], not healing)
                    if scenario == 'absorb':
                        self.assertEqual(actual['absorbed'], 0 if healing else 100)

    def test_same_event_position_and_lifetime_for_1_8_32_commands(self):
        self.compare('flight')

    def test_same_health_budget_and_retirement_for_1_8_32_commands(self):
        self.compare('budget')

    def test_same_grasp_absorption_for_1_8_32_commands(self):
        self.compare('absorb')

    def test_old_orb_press_outside_neutral_replay_window_is_not_reopened(self):
        run, owner, _, _ = orb_fixtures.MoiraOrbTests().setup_fight(ally=False, distance=1000)
        start = run.frame
        run.match.update((start + MAX_COMMANDS * 2) * combat.TICK, start + MAX_COMMANDS * 2)
        owner.take_commands([Command(start + 1, buttons=16)])
        self.assertFalse(run.match.combat.moira_orbs)
        self.assertFalse(moira._orb_running(owner.body_script.component))
