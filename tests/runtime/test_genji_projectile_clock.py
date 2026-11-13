"""Real Genji input events retain their world launch time across packet batches."""

import json
import math
from pathlib import Path
from unittest.mock import patch

from test_genji_combat import GenjiFixture

from ow174.game import combat
from ow174.game.commands import Command


class GenjiProjectileClockTests(GenjiFixture):
    def assert_same_sample(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for current, baseline in zip(actual, expected, strict=True):
            position, flown, left, last_tick, born = current
            point, elapsed, remaining, tick, launch = baseline
            for value, reference in zip(position, point, strict=True):
                self.assertAlmostEqual(value, reference, delta=1e-9)
            self.assertAlmostEqual(flown, elapsed, delta=1e-12)
            self.assertAlmostEqual(left, remaining, delta=1e-12)
            self.assertEqual((last_tick, born), (tick, launch))

    def encounter(self, button, batch, *, body_start=1000, target=False, target_distance=40, impacts=None):
        run, player = self.spawn(target_distance, names=("Genji", "Target") if target else ("Genji",))
        if body_start != 1000:
            run.match.switch_hero(player, player.hero, start_frame=body_start)
            player.mover.position[:] = [0, 1, 0]
        for _ in range(30):
            at = run.frame + 1
            run.frame = at
            run.match.update(at * combat.TICK, at)
            player.take_commands([Command(body_start + at - 1000)])
        fight = run.match.combat
        launches, flights, endings = [], [], []
        commit = fight._commit_attack
        landed = fight._landed
        tell_hit = fight._tell_hit

        def observe_launch(actor, frame, payload, boost):
            result = commit(actor, frame, payload, boost)
            if isinstance(payload, combat.Rocket):
                launches.append((frame, payload.last_tick))
                flights.append(payload)
            return result

        def observe_end(rocket, **kwargs):
            if rocket in fight.rockets:
                endings.append((fight.tick, rocket.ended))
            return landed(rocket, **kwargs)

        def observe_hit(shooter, recipient, dealt, flags, frame):
            if impacts is not None:
                impacts.append((fight.tick, frame, dealt, fight.vitals_of(recipient).total))
            return tell_hit(shooter, recipient, dealt, flags, frame)

        with patch.object(fight, "_commit_attack", observe_launch), patch.object(fight, "_landed", observe_end), \
                patch.object(fight, "_tell_hit", observe_hit):
            for at in range(1031, 1183):
                run.frame = at
                run.match.update(at * combat.TICK, at)
                if (at - 1030) % batch == 0:
                    first = body_start + at - batch + 1 - 1000
                    player.take_commands([
                        Command(frame, buttons=button if frame == body_start + 31 else 0)
                        for frame in range(first, first + batch)
                    ])
                if at == 1078:
                    sample = [(tuple(rocket.position), rocket.flown, rocket.left,
                               rocket.last_tick, rocket.started_tick) for rocket in flights]
        self.assertEqual(len(launches), 3)
        self.assertFalse(fight.rockets)
        hits = [notice["+0x78"] for notice in player.client.messages(combat.HIT)]
        health = self.health(run, run.players[1]) if target else None
        return launches, sample, endings, hits, health

    def test_primary_and_fan_keep_launch_time_and_position_across_packet_batches(self):
        fixture = Path(__file__).resolve().parents[2] / "fixtures/behavior/genji-projectile-clock-v1.json"
        expected = json.loads(fixture.read_text(encoding="utf-8"))["expected"]
        for button in (1, 2):
            baseline = self.encounter(button, 1)
            for batch in (8, 32):
                with self.subTest(button=button, batch=batch):
                    actual = self.encounter(button, batch)
                    self.assertEqual(actual[0], baseline[0])
                    self.assert_same_sample(actual[1], baseline[1])
                    self.assertEqual(actual[2:], baseline[2:])
            launches, sample, endings, hits, _ = baseline
            frames = expected[str(button)]["world_launch_ticks"]
            self.assertEqual(launches, [(frame, frame) for frame in frames])
            for index, (position, flown, _left, last_tick, born) in enumerate(sample):
                elapsed = (1078 - frames[index]) * combat.TICK
                self.assertAlmostEqual(flown, elapsed)
                angle = math.radians((4.5, 0, -4.5)[index] if button == 2 else 0)
                self.assertAlmostEqual(position[0], 60 * elapsed * math.sin(angle))
                self.assertAlmostEqual(position[2], 60 * elapsed * math.cos(angle))
                self.assertEqual(last_tick, 1078)
                self.assertEqual(born, frames[index])
            self.assertEqual(len(endings), 3)
            self.assertEqual([tick for tick, _ in endings], expected[str(button)]["expiry_world_ticks"])
            self.assertFalse(hits)

    def test_launch_mapping_uses_the_body_epoch(self):
        for button in (1, 2):
            aligned = self.encounter(button, 1)
            for batch in (1, 8, 32):
                with self.subTest(button=button, batch=batch):
                    launches, sample, endings, hits, health = self.encounter(button, batch, body_start=5000)
                    self.assertEqual([(frame - 4000, tick) for frame, tick in launches], aligned[0])
                    self.assert_same_sample(sample, aligned[1])
                    self.assertEqual((endings, hits, health), aligned[2:])

    def test_packet_batches_preserve_target_damage_hit_feedback_and_retirement(self):
        for button, expected in ((1, 113), (2, 171)):
            baseline = self.encounter(button, 1, target=True)
            for batch in (8, 32):
                with self.subTest(button=button, batch=batch):
                    actual = self.encounter(button, batch, target=True)
                    self.assertEqual(actual[0], baseline[0])
                    self.assertEqual(actual[2:], baseline[2:])
            self.assertEqual(baseline[-1], expected)
            self.assertEqual([hit["+0x4"] for hit in baseline[-2]], [29] * (3 if button == 1 else 1))

    def test_near_hits_keep_physical_time_across_packet_batches_with_offset_epoch(self):
        for button, ticks in ((1, [1033, 1039, 1046]), (2, [1033, 1033, 1033])):
            baseline = None
            for batch in (1, 8, 32):
                with self.subTest(button=button, batch=batch):
                    impacts = []
                    result = self.encounter(button, batch, body_start=5000, target=True,
                                            target_distance=2, impacts=impacts)
                    self.assertEqual([tick for tick, _, _, _ in impacts], ticks)
                    self.assertEqual([frame for _, frame, _, _ in impacts], ticks)
                    self.assertEqual([damage for _, _, damage, _ in impacts], [29] * 3)
                    self.assertEqual([health for _, _, _, health in impacts], [171, 142, 113])
                    self.assertEqual(result[-1], 113)
                    if baseline is None:
                        baseline = (impacts, result[2:])
                    else:
                        self.assertEqual((impacts, result[2:]), baseline)
