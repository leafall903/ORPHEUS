"""Sigma projectile clock checks; encrypted fixtures do not establish client visuals."""
import dataclasses
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import aim
from test_server_journal import MAP, ServerJournalTests
from test_sigma_combat import SIGMA, SigmaCombatTests

from ow174.game import combat
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import Spawn, heroes


def wire_case(batch, button, *, steps=96, distance=1000, details=None):
    case = ServerJournalTests()
    session, sent = case.make()
    scene = dataclasses.replace(MAP, spawns=(Spawn(0, (0.,0.,0.),0.),
                                            Spawn(1, (0.,0.,float(distance)),180.)))
    peers, (owner, enemy) = case.connected(session, sent, first_hero=SIGMA, scene=scene)
    fight = owner.match.combat
    if distance == 1000:
        owner.match.collision_world = World([])
    facing = aim(fight._eye(owner), fight._eye(enemy)) if distance < 1000 else (0, 0)
    first = owner.body_script.frame
    tracked = []
    commit = fight._commit_attack

    def observe(shooter, frame, payload, boost):
        if isinstance(payload, combat.Rocket):
            tracked.append([payload, None])
        return commit(shooter, frame, payload, boost)

    with patch.object(fight, '_commit_attack', side_effect=observe):
        for start in range(1, steps + 1, batch):
            end = min(start + batch - 1, steps)
            for cursor in range(start, end + 1):
                now = 100.944 + cursor * .016
                session.tick(now)
                for item in tracked:
                    if item[0].ended is not None and item[1] is None:
                        item[1] = fight.tick
            cursor = start
            while cursor <= end:
                last = min(end, 5) if cursor <= 5 else end
                peers[0].commands(first + cursor, last - cursor + 1,
                                  button if cursor <= 5 else 0,
                                  yaw=facing[0], pitch=facing[1], now=now)
                cursor = last + 1
            peers[1].drain()
            peers[1].send(now=now)
    notices = [(item['+0x84'], item['+0x78']['+0x4']) for item in peers[0].hit_notices(0)]
    if details is not None:
        details.update(observed_retirement_ticks=[retired for _, retired in tracked],
                       hit_notices=notices, hp=fight.vitals[enemy.body].total)
    return [(rocket.definition, rocket.position, rocket.flown, rocket.left,
             rocket.started_tick, rocket.ended) for rocket, _ in tracked]


class SigmaProjectileClockTests(unittest.TestCase):
    def test_shared_birth_fixture_against_actual_volley_admission(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] /
                              'fixtures/behavior/sigma-projectile-clock-v1.json').read_text())
        data = fixture['input']
        for case in data['cases']:
            with self.subTest(case=case):
                run, owner, _, _ = SigmaCombatTests().arena(distance=1000)
                owner.quanta = case['quanta_us']
                run.match.switch_hero(owner, heroes()[SIGMA], start_frame=data['body_birth'])
                world_tick = max(data['world_birth'] + 1, case['expected_world_tick'])
                run.match.update(world_tick * .016, world_tick)
                tracked = []
                commit = run.match.combat._commit_attack

                def observe(shooter, frame, payload, boost, tracked=tracked, commit=commit):
                    if isinstance(payload, combat.Rocket):
                        tracked.append((payload.shot.frame, payload.last_tick))
                    return commit(shooter, frame, payload, boost)

                with patch.object(run.match.combat, '_commit_attack', side_effect=observe):
                    owner.take_commands([Command(frame, buttons=int(frame == case['shot_frame']))
                        for frame in range(data['body_birth'] + 1, case['shot_frame'] + 1)])
                self.assertIn((case['shot_frame'], case['expected_world_tick']), tracked)

    def test_birth_epoch_is_causal_snapshot_state(self):
        _, owner, _, _ = SigmaCombatTests().arena()
        script = owner.body_script
        before = snapshot_digest(export_body_vm(script))
        world, frame = script.sigma_birth
        script.sigma_birth = (world + 1, frame)
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), before)

    def test_rock_encrypted_batches_share_flight(self):
        baseline = wire_case(1, 16)
        self.assertEqual(len(baseline), 1)
        for batch in (8, 32):
            self.assertEqual(wire_case(batch, 16), baseline)

    def test_sphere_encrypted_batches_share_flight_and_expiry(self):
        baseline = wire_case(1, 1)
        self.assertEqual(len(baseline), 2)
        for batch in (8, 32):
            self.assertEqual(wire_case(batch, 1), baseline)

    def test_sphere_encrypted_batches_share_direct_hit_tick(self):
        baseline_details = {}
        baseline = wire_case(1, 1, distance=6, details=baseline_details)
        self.assertEqual(baseline_details['hp'], 90)
        self.assertEqual(len(baseline_details['hit_notices']), 4)
        for batch in (8, 32):
            details = {}
            self.assertEqual(wire_case(batch, 1, distance=6, details=details), baseline)
            self.assertEqual(details['hp'], baseline_details['hp'])
            self.assertEqual(details['hit_notices'], baseline_details['hit_notices'])

    def test_rock_encrypted_batches_share_direct_hit_tick(self):
        baseline_details = {}
        baseline = wire_case(1, 16, distance=6, details=baseline_details)
        self.assertEqual(baseline_details['hp'], 130)
        self.assertEqual(len(baseline_details['hit_notices']), 2)
        for batch in (8, 32):
            details = {}
            self.assertEqual(wire_case(batch, 16, distance=6, details=details), baseline)
            self.assertEqual(details['hp'], baseline_details['hp'])
            self.assertEqual(details['hit_notices'], baseline_details['hit_notices'])

    def test_birth_maps_distinct_origins_and_quantum_boundaries(self):
        for quanta in (15000, 16000, 17000):
            signatures = []
            for batch in (1, 8, 32):
                run, owner, _, _ = SigmaCombatTests().arena(distance=1000)
                owner.quanta = quanta
                run.match.switch_hero(owner, heroes()[SIGMA], start_frame=5000)
                script = owner.body_script
                self.assertEqual(script.sigma_birth, (1000, 5000))
                self.assertIsNone(script.world_frame_anchor)
                run.match.collision_world = World([])
                tracked = []
                commit = run.match.combat._commit_attack

                def observe(shooter, frame, payload, boost, tracked=tracked, commit=commit, script=script):
                    if isinstance(payload, combat.Rocket):
                        expected = 1000 + (payload.shot.frame - 5000) * script.frame_ms // 16
                        self.assertEqual(payload.last_tick, expected)
                        tracked.append(payload)
                    return commit(shooter, frame, payload, boost)

                with patch.object(run.match.combat, '_commit_attack', side_effect=observe):
                    for start in range(1, 97, batch):
                        end = min(start + batch - 1, 96)
                        run.match.update((1000 + end) * .016, 1000 + end)
                        owner.take_commands([Command(5000 + cursor, buttons=16 if cursor <= 5 else 0)
                                             for cursor in range(start, end + 1)])
                    run.match.update(1097 * .016, 1097)
                self.assertEqual(script.sigma_birth, (1000, 5000))
                self.assertEqual(len(tracked), 1)
                signatures.append((tracked[0].position, tracked[0].flown, tracked[0].left))
            self.assertEqual(signatures, [signatures[0]] * 3)
