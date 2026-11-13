"""Same accepted commands must retain Mei projectile time across packet batches."""
import json
import unittest
from pathlib import Path

import test_delivered_mei_combat as fixtures

from ow174.game import combat
from ow174.game.commands import Command

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/mei-projectile-clock-v1.json').read_text())


class ProjectileClockTests(unittest.TestCase):
    def scene(self, button, batch, *, hit=False):
        run, owner, enemy, target = fixtures.MeiCombatTests().duel()
        fight = run.match.combat
        facing = fixtures.aim(fight._eye(owner),
                              (target.feet[0], target.feet[1] + .9, target.feet[2])) if hit else (0, -6000)
        if not hit:
            enemy.mover.position[2] += 100
        owner.body_script.set_ult_charge(1610.)
        start = run.frame
        self.assertEqual(start, CONTRACT['input']['start_world'])
        self.assertEqual(owner.body_script.cryo_birth, (1000, 1000))
        for first in range(1, 65, batch):
            last = min(first + batch - 1, 64)
            run.frame = start + last
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([
                Command(start + i, yaw=facing[0], pitch=facing[1], buttons=button if 3 <= i <= 7 else 0)
                for i in range(first, last + 1)
            ])
        run.frame = CONTRACT['input']['query_world']
        run.match.update(run.frame * combat.TICK, run.frame)
        return run, owner, enemy, fight

    def advance(self, run):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)

    def compare(self, button, key):
        rows = []
        expected = CONTRACT['expected'][key]
        for batch in CONTRACT['input']['batch_sizes']:
            run, owner, enemy, fight = self.scene(button, batch)
            flights = fight.blizzard.flights if button == 32 else fight.rockets
            flight, = flights
            self.assertEqual(flight.shot.frame, expected['release_command'])
            self.assertEqual(flight.last_tick, CONTRACT['input']['query_world'])
            self.assertAlmostEqual(flight.flown, expected['flown_seconds'])
            row = {'event': flight.shot.frame, 'position': flight.position,
                   'left': flight.left, 'flown': flight.flown}
            while flight in flights:
                self.assertLess(run.frame, 1300)
                self.advance(run)
            row['retired'] = run.frame
            if button == 32:
                area, = fight.blizzard.areas.values()
                row.update(land=area.position, starts=area.starts, ends=area.ends)
                while fight.blizzard.areas:
                    self.assertLess(run.frame, 1600)
                    self.advance(run)
                row['area_retired'] = run.frame
            self.assertEqual(fight.vitals_of(fight.player_target(enemy)).total, 200.)
            self.assertEqual(owner.client.messages(combat.HIT), [])
            rows.append(row)
        for row in rows[1:]:
            for key, value in rows[0].items():
                if isinstance(value, tuple):
                    for expected_axis, axis in zip(value, row[key], strict=True):
                        self.assertAlmostEqual(expected_axis, axis,
                                               delta=CONTRACT['expected']['float_tolerance'])
                elif isinstance(value, float):
                    self.assertAlmostEqual(value, row[key], delta=CONTRACT['expected']['float_tolerance'])
                else:
                    self.assertEqual(value, row[key])
        return rows

    def test_blizzard_birth_flight_landing_and_area_deadline_ignore_batch_size(self):
        self.compare(32, 'blizzard')

    def test_icicle_birth_flight_and_range_retirement_ignore_batch_size(self):
        self.compare(2, 'icicle')

    def test_actual_icicle_body_damage_and_hit_feedback_ignore_batch_size(self):
        for batch in CONTRACT['input']['batch_sizes']:
            with self.subTest(batch=batch):
                _run, owner, enemy, fight = self.scene(2, batch, hit=True)
                self.assertEqual(fight.vitals_of(fight.player_target(enemy)).total, 125.)
                self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
                self.assertFalse(fight.rockets)

    def test_actual_replacement_with_unequal_epochs_maps_both_release_events(self):
        for button, event, flown in ((2, 1088, .592), (32, 1094, .496)):
            with self.subTest(button=button):
                run, owner, enemy, _ = fixtures.MeiCombatTests().duel()
                run.frame += 170
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertTrue(run.match.switch_hero(owner, owner.hero))
                self.assertEqual(owner.body_script.cryo_birth, (1200, 1030))
                for i in range(1, 31):
                    run.frame = 1200 + i
                    run.match.update(run.frame * combat.TICK, run.frame)
                    owner.take_commands([Command(1030 + i)])
                enemy.mover.position[2] += 100
                owner.body_script.set_ult_charge(1610.)
                for first in (1, 33):
                    run.frame = 1230 + first + 31
                    run.match.update(run.frame * combat.TICK, run.frame)
                    owner.take_commands([Command(1060 + i, pitch=-6000,
                                                 buttons=button if 3 <= i <= 7 else 0)
                                         for i in range(first, first + 32)])
                run.frame = 1295
                run.match.update(run.frame * combat.TICK, run.frame)
                fight = run.match.combat
                flights = fight.blizzard.flights if button == 32 else fight.rockets
                flight, = flights
                self.assertEqual(flight.shot.frame, event)
                self.assertEqual(flight.last_tick, 1295)
                self.assertAlmostEqual(flight.flown, flown)
