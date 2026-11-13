"""Production Roadhog command batching with independent command/world origins."""

import unittest

from test_game_combat import KANEZAKA_DM, Run
from test_game_roadhog import ROADHOG, torso_aim

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import heroes


class RoadhogClockTests(unittest.TestCase):
    def sample(self, origin, batch, button, distance):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        self.assertTrue(match.switch_hero(hog, heroes()[ROADHOG], start_frame=origin))
        hog.commands.reset(last_frame=origin)
        script = hog.body_script
        birth = (match.tick, origin)
        self.assertEqual(script.roadhog_birth, birth)
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x, y, z + distance)
        enemy.mover.body = None
        fight = match.combat
        target = next(target for target in fight._targets() if target.owner is enemy)
        pool = fight.vitals_of(target)
        angles = torso_aim(run, hog, enemy)
        pending = []
        samples = []
        for elapsed in range(1, 137):
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            enemy.take_commands([Command(run.frame)])
            command = Command(origin + elapsed, yaw=angles[0], pitch=angles[1],
                              buttons=button if elapsed == 41 else 0)
            if elapsed <= 40:
                hog.take_commands([command])
            else:
                pending.append(command)
                if (elapsed - 40) % batch == 0:
                    hog.take_commands(pending)
                    pending = []
            if elapsed in (72, 104, 136):
                rockets = [rocket for rocket in fight.rockets if rocket.shooter is hog]
                flight = fight.hooks.flights.get(hog.body)
                samples.append({
                    "health": round(pool.total, 5),
                    "target_position": tuple(round(value, 5) for value in enemy.mover.position),
                    "hits": len(hog.client.messages(combat.HIT)),
                    "rockets": sorted((tuple(round(v, 5) for v in rocket.position),
                                       round(rocket.flown, 5), round(rocket.left, 5))
                                      for rocket in rockets),
                    "hook": None if flight is None else (
                        flight.launch_tick, flight.last_tick, round(flight.travelled, 5),
                        round(flight.caught_at, 5),
                        None if flight.arrived_at is None else round(flight.arrived_at, 5)),
                })
        self.assertEqual(script.roadhog_birth, birth)
        return samples

    def test_same_events_match_across_batches_and_command_origins(self):
        for button, distance, health, hits in (
            (1, 2, 35, 25), (1, 200, 200, 0), (2, 200, 200, 0), (2, 10, 35, 25),
            (8, 10, 170, 1), (8, 30, 200, 0),
        ):
            reference = self.sample(1000, 1, button, distance)
            self.assertAlmostEqual(reference[-1]["health"], health, places=4)
            self.assertEqual(reference[-1]["hits"], hits)
            self.assertFalse(reference[-1]["rockets"])
            self.assertIsNone(reference[-1]["hook"])
            for origin in (1000, 17000, 900000):
                for batch in (1, 8, 32):
                    with self.subTest(button=button, distance=distance, origin=origin, batch=batch):
                        self.assertEqual(self.sample(origin, batch, button, distance), reference)

    def test_birth_pair_is_causal_stored_state(self):
        import test_server_snapshot as fixtures
        from test_server_journal import pick
        from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
        from ow174.game.server_snapshot import snapshot_digest as server_digest

        case = fixtures.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        hog = players[0]
        now = 101.1
        peers[0].message(21616, pick(hog.entity, ROADHOG), now=now)
        for _ in range(8):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        script = hog.body_script
        birth = script.roadhog_birth
        vm_before = snapshot_digest(export_body_vm(script))
        server_before = server_digest(case.capture(session))
        script.roadhog_birth = (birth[0] + 1, birth[1])
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), vm_before)
        self.assertNotEqual(server_digest(case.capture(session)), server_before)
        script.roadhog_birth = birth
        self.assertEqual(snapshot_digest(export_body_vm(script)), vm_before)
        self.assertEqual(server_digest(case.capture(session)), server_before)
