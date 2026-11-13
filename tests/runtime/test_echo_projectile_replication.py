"""Echo projectile cardinality and per-projectile transport retirement.

These production command-path regressions use immediate-ACK test connections.
Original-client visibility is recorded separately by the repair owner.
"""
import json
import unittest
from pathlib import Path

import test_echo_combat as echo_cases
from test_game_projectiles import bits_of, read_shot

from ow174.game import combat, projectiles, server_snapshot
from ow174.game.commands import Command
from ow174.game.mover import Mover, mover_data
from ow174.game.world import OP_DESTROY


class EchoProjectileReplicationTests(unittest.TestCase):
    def test_grouped_volley_ack_state_is_in_the_stored_graph(self):
        group = projectiles._ProjectileVolley(3)
        group.creations.append(projectiles._ProjectileArrival())

        def stored():
            graph = server_snapshot._Graph({}, None, None)
            root = graph.value(group)
            return root, graph.finish()

        before = stored()
        group.queued = True
        self.assertNotEqual(stored(), before)
        group.queued = False
        self.assertEqual(stored(), before)
        group.remaining -= 1
        self.assertNotEqual(stored(), before)
        group.remaining += 1
        group.creations[0].arrived(0)
        self.assertNotEqual(stored(), before)

    def test_synthetic_prediction_id_contract(self):
        fixture = json.loads((Path(__file__).resolve().parents[2]
                              / "fixtures/behavior/echo-projectile-lease-v1.json").read_text())
        run, shooter, _, _, _ = echo_cases.EchoPrimaryTests().session()
        system = run.match.combat.projectiles
        given, expected = fixture["input"], fixture["expected"]
        self.assertEqual(shooter.slot, given["slot"])
        definition = int(given["projectile_definition"], 16)
        first = system.lease_id(shooter, definition, given["frame"])
        self.assertEqual([first + offset for offset in range(given["projectiles_per_shot"])],
                         expected["ids"])
        self.assertEqual(system.lease_id(shooter, definition, given["frame"] + 1),
                         expected["next_frame_first_id"])
        block = next(row for row in system.lease(shooter) if row[0] == definition)
        self.assertEqual(block[-1], expected["per_frame"])
        self.assertEqual(block[2] - block[1], expected["window_frames"])

    def test_three_prediction_ids_and_one_three_id_observer_record(self):
        run, shooter, victim, target, angles = echo_cases.EchoPrimaryTests().session()
        fight = run.match.combat
        system = fight.projectiles
        definition = 0x0400000000002CBF
        lease = next(row for row in system.lease(shooter) if row[0] == definition)
        self.assertEqual(lease[-1], 3)
        span = projectiles.LEASE_SPAN // 3
        for frame in (1, span - 2, span, span + 1):
            first = system.lease_id(shooter, definition, frame)
            second = system.lease_id(shooter, definition, frame + 1)
            if frame % span != span - 1:
                self.assertEqual(second - first, 3)

        before = fight.vitals_of(target).total
        victim.client.entities.clear()
        run.step({shooter: (*angles, 1)})
        rockets = tuple(fight.rockets)
        self.assertEqual(len(rockets), 3)
        self.assertEqual(len({rocket.direction for rocket in rockets}), 3)
        self.assertEqual(len({id(rocket.shot) for rocket in rockets}), 3)
        deliveries = [rocket.shot.observers[0] for rocket in rockets]
        self.assertEqual(len({id(item.launch) for item in deliveries}), 1)
        ids = deliveries[0].record.ids
        self.assertEqual(len(set(ids)), 3)
        reader = bits_of(deliveries[0].launch.ch2)
        self.assertEqual((reader.bit(), reader.bit()), (1, 1))
        reader.var_b()
        self.assertEqual(read_shot(reader)["ids"], list(ids))
        for _ in range(25):
            run.step({})
        self.assertEqual(before - fight.vitals_of(target).total, 51)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 3)
        owner_ids = [entity for rocket in rockets for entity in rocket.shot.created]
        self.assertEqual(len(owner_ids), 3)
        self.assertEqual(len(set(owner_ids)), 3)
        launches = [item for item in victim.client.entities
                    if any(item is delivery.launch for delivery in deliveries)]
        self.assertEqual(len(launches), 1)

    def test_successive_tri_shots_keep_their_activation_and_spread(self):
        run, shooter, _, _, _ = echo_cases.EchoPrimaryTests().session(distance=95)
        for _ in range(70):
            run.step({shooter: (0, 0, 1)})
            if len(run.match.combat.rockets) == 6:
                break
        rockets = tuple(run.match.combat.rockets)
        groups = {}
        for rocket in rockets:
            groups.setdefault(rocket.shot.activation[0], []).append(rocket)
        self.assertEqual(set(groups), {1, 2})
        for index, volley in groups.items():
            self.assertEqual(len(volley), 3)
            record = volley[0].shot.observers[0].record
            self.assertEqual(record.activation, index)
            self.assertEqual(record.shot, 0)
            self.assertEqual(len(record.ids), 3)
        self.assertFalse({rocket.direction for rocket in groups[1]}
                         & {rocket.direction for rocket in groups[2]})

    def test_observer_launch_waits_for_all_three_create_acknowledgements(self):
        run, shooter, victim, _, angles = echo_cases.EchoPrimaryTests().session()
        run.step({shooter: (*angles, 1)})
        system = run.match.combat.projectiles
        deliveries = [rocket.shot.observers[0] for rocket in run.match.combat.rockets]
        for delivery in deliveries[1:]:
            delivery.creation.received = False
        system.update(run.frame)
        self.assertFalse(deliveries[0].volley.queued)
        deliveries[1].creation.arrived(0)
        system.update(run.frame)
        self.assertFalse(deliveries[0].volley.queued)
        deliveries[2].creation.arrived(0)
        for _ in range(5):
            run.step({})
        self.assertTrue(deliveries[0].volley.queued)
        self.assertEqual(sum(item is deliveries[0].launch for item in victim.client.entities), 1)

    def test_one_pellet_hit_does_not_retire_the_other_two_flights(self):
        run, shooter, victim, target, angles = echo_cases.EchoPrimaryTests().session(distance=60)
        fight = run.match.combat
        run.step({shooter: (*angles, 1)})
        rockets = tuple(fight.rockets)
        first = rockets[0]
        at = tuple(first.position[i] + first.direction[i] * 50 for i in range(3))
        victim.mover = Mover((at[0], target.feet[1], at[2]),
                             world=run.match.collision_world,
                             data=mover_data(victim.hero.body))
        for _ in range(46):
            run.step({})
        self.assertNotIn(first, fight.rockets)
        self.assertEqual(len(fight.rockets), 2)
        self.assertIsNotNone(first.shot.release_at)
        for rocket in rockets[1:]:
            self.assertIsNone(rocket.shot.release_at)
        for _ in range(80):
            run.step({})
        self.assertFalse(fight.rockets)
        ids = set(rockets[0].shot.observers[0].record.ids)
        destroyed = {item.entity for item in victim.client.entities if item.op == OP_DESTROY}
        self.assertTrue(ids <= destroyed)

    def test_six_stickies_have_distinct_ids_damage_and_retirements(self):
        run, shooter, victim, target, angles = echo_cases.EchoPrimaryTests().session()
        fight = run.match.combat
        echo_cases.EchoStickyTests().launch(run, shooter, angles)
        rockets = tuple(fight.rockets)
        self.assertEqual(len(rockets), 6)
        self.assertTrue(all(rocket.attached_entity == target.entity for rocket in rockets))
        owner_ids = [entity for rocket in rockets for entity in rocket.shot.created]
        self.assertEqual(len(owner_ids), 6)
        self.assertEqual(len(set(owner_ids)), 6)
        observer_ids = {rocket.shot.observers[0].record.ids[0] for rocket in rockets}
        self.assertEqual(len(observer_ids), 6)
        self.assertEqual([rocket.shot.observers[0].record.shot for rocket in rockets], list(range(6)))
        self.assertEqual(fight.vitals_of(target).total, 170)
        for _ in range(90):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 20)
        self.assertFalse(fight.rockets)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 12)
        destroyed = {item.entity for item in victim.client.entities if item.op == OP_DESTROY}
        self.assertTrue(observer_ids <= destroyed)


class EchoProjectileClockTests(unittest.TestCase):
    @staticmethod
    def fixture():
        return json.loads((Path(__file__).resolve().parents[2]
                           / "fixtures/behavior/echo-projectile-clock-v1.json").read_text())

    def replay(self, batch, buttons, distance):
        fixture = self.fixture()
        given = fixture["input"]
        run, shooter, _, target, _ = echo_cases.EchoPrimaryTests().session(distance=distance)
        fight = run.match.combat
        start = run.frame
        pending, samples, births = [], [], {}
        digits = fixture["expected"]["batch_comparison_decimal_places"]
        for offset in range(1, max(given["sample_offsets"]) + 1):
            at = start + offset
            run.match.update(at * combat.TICK, at)
            pending.append(Command(at, buttons=buttons if offset == given["event_offset"] else 0))
            if offset % batch == 0:
                shooter.take_commands(pending)
                pending = []
            for rocket in fight.rockets:
                key = rocket.shot.frame, rocket.shot.lease_offset
                if key not in births:
                    births[key] = rocket.last_tick
                    self.assertEqual(rocket.last_tick, rocket.shot.frame)
            if offset in given["sample_offsets"]:
                samples.append({
                    "health": round(fight.vitals_of(target).total, digits),
                    "hits": len(shooter.client.messages(combat.HIT)),
                    "live": [
                        (rocket.shot.frame, tuple(round(x, digits) for x in rocket.position),
                         round(rocket.flown, digits),
                         None if rocket.fuse_left is None else round(rocket.fuse_left, digits),
                         rocket.last_tick, rocket.started_tick)
                        for rocket in fight.rockets
                    ],
                })
        return samples, births

    def test_primary_command_event_has_identical_flight_and_expiry_across_packet_batches(self):
        fixture = self.fixture()
        expected = fixture["expected"]
        results = [self.replay(batch, 1, fixture["input"]["primary_target_distance"])
                   for batch in fixture["input"]["packet_batch_sizes"]]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        samples, births = results[0]
        self.assertEqual(len(births), 3)
        self.assertEqual(len(samples[0]["live"]), expected["primary_first_projectiles"])
        self.assertEqual(samples[0]["live"][0][2], expected["primary_first_flight_seconds"])
        self.assertEqual(len(samples[-1]["live"]), expected["primary_final_projectiles"])

    def test_sticky_command_event_has_identical_contacts_fuses_damage_and_retirement(self):
        fixture = self.fixture()
        expected = fixture["expected"]
        results = [self.replay(batch, 2, fixture["input"]["sticky_target_distance"])
                   for batch in fixture["input"]["packet_batch_sizes"]]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        samples, births = results[0]
        self.assertEqual(len(births), 6)
        self.assertEqual(samples[0]["health"], expected["sticky_first_health"])
        self.assertEqual(samples[-1]["health"], expected["sticky_final_health"])
        self.assertEqual(samples[-1]["hits"], expected["sticky_final_hit_notices"])
        self.assertEqual(len(samples[-1]["live"]), expected["sticky_final_projectiles"])
