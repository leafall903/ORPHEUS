"""Actual Uppercut activates Slam's preview; requires the canonical priority2 byte."""

import json
import logging
import unittest
from pathlib import Path

from test_doomfist_combat import DoomfistFixture

from ow174.game import combat
from ow174.game.script.decode import ClientBody


class ErrorLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class SlamPreviewWireTests(DoomfistFixture, unittest.TestCase):
    def test_actual_uppercut_rebegins_slam_preview_and_keeps_both_observer_streams(self):
        contract = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                               / 'doomfist-slam-preview-wire-v1.json').read_text(encoding='utf-8'))
        self.prepare(1.5)
        self.match.switch_hero(self.b, self.a.hero)
        identities = [(p.body, p.body_script, p.body_script.component, p.mover)
                      for p in (self.a, self.b)]
        previews = []
        for player in (self.a, self.b):
            instance = next(i for i in player.body_script.component.instances.values()
                            if i.graph.index == 0x0B27)
            previews.append(instance.node_state(instance.graph.node(15)))
        before, serial = self.vitals().total, self.fight.knockbacks.serial
        logger = logging.getLogger('ow174.game')
        errors = ErrorLog()
        logger.addHandler(errors)
        observed = [set(), set()]
        try:
            for activation in (1, 2):
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + .25, y, z + 1.25))
                self.settle(1)
                self.run.step({self.a: (0, 0, contract['input']['uppercut_button'])})
                for _ in range(160):
                    self.run.step({})
                    for index, state in enumerate(previews):
                        if state.active:
                            self.assertEqual(state.payload(),
                                             {'m_B4802CE3': contract['expected']['begin_byte']})
                            self.assertIs(type(state.payload()['m_B4802CE3']), int)
                            observed[index].add(state.counter)
                for state in previews:
                    self.assertFalse(state.active)
                    self.assertEqual(state.counter, activation)
                if activation == 1:
                    self.settle(contract['input']['cooldown_idle_frames'])
        finally:
            logger.removeHandler(errors)
        self.assertEqual(errors.messages, [])
        self.assertEqual(observed, [{1, 2}, {1, 2}])
        self.assertEqual(before - self.vitals().total, contract['expected']['two_contact_damage'])
        self.assertEqual(self.fight.knockbacks.serial - serial, 2)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 2)
        for player, identity in zip((self.a, self.b), identities, strict=True):
            self.assertEqual(player.body, identity[0])
            self.assertIs(player.body_script, identity[1])
            self.assertIs(player.body_script.component, identity[2])
            self.assertIs(player.mover, identity[3])
        for viewer, actor in ((self.a, self.b), (self.b, self.a)):
            model = ClientBody.for_hero(actor.hero.guid)
            values = []
            for update in viewer.client.entities:
                if update.entity != actor.body or update.chunk is None:
                    continue
                frame = model.apply(update)
                for number, wire in frame.instances.items():
                    if model.instances.get(number) == 0x0B27 and 6 in wire.states:
                        active, payload = wire.states[6]
                        if active:
                            values.append(payload['m_B4802CE3'])
            self.assertGreaterEqual(len(values), 2)
            self.assertEqual(set(values), {contract['expected']['begin_byte']})
            self.assertTrue(self.fight.observers.has(viewer, actor.body))
