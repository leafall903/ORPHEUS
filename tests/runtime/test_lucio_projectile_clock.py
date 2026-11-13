"""Lucio primary command clocks through actual command and encrypted packet entry points."""

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import combat, lucio, projectile_clock, projectiles
from ow174.game.collision import flat_world
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from tests.runtime import test_lucio_combat as lucio_tests
from tests.runtime import test_server_journal as journal_tests


class LucioProjectileClockTests(unittest.TestCase):
    def contract(self):
        root = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
                    or Path(__file__).resolve().parents[2] / "fixtures/behavior")
        return json.loads((root / "lucio-projectile-command-world-clock-v1.json").read_text(
            encoding="utf-8-sig",
        ))

    def test_primary_batches_preserve_flight_hit_and_timeout_with_different_epochs(self):
        contract = self.contract()
        inputs, expected = contract["input"], contract["expected"]
        for epoch_offset in inputs["command_epoch_offsets"]:
            for distance in inputs["enemy_distances"]:
                for batch in inputs["batch_sizes"]:
                    with self.subTest(epoch_offset=epoch_offset, distance=distance, batch=batch):
                        self.check_primary_batch(inputs, expected, epoch_offset, distance, batch)

    def check_primary_batch(self, inputs, expected, epoch_offset, distance, batch):
        run, source, enemy, ally = lucio_tests.LucioCombatTests().scenario()
        fight = run.match.combat
        run.match.collision_world = flat_world(1)
        self.assertTrue(run.match.switch_hero(
            source, heroes()[lucio.HERO], start_frame=run.frame + epoch_offset,
        ))
        source.mover.position[:] = (0, 1, 0)
        source.mover.body = None
        enemy.mover.position[:] = (0, 1, distance)
        enemy.mover.body = None
        ally.mover.position[:] = (20, 1, 4)
        command_start = source.body_script.frame
        for number in range(1, inputs["warmup_commands"] + 1):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            source.take_commands([Command(command_start + number)])
        world_start, command_start = run.frame, source.body_script.frame
        shots, retired = [], []
        commit, landed = fight._commit_attack, fight._landed

        def observe_commit(shooter, frame, payload, boost):
            if shooter is source and isinstance(payload, combat.Rocket):
                shots.append((frame, payload))
            return commit(shooter, frame, payload, boost)

        def observe_landed(rocket, **kwargs):
            if rocket.shooter is source:
                retired.append(fight.tick - world_start)
            return landed(rocket, **kwargs)

        with (patch.object(fight, "_commit_attack", observe_commit),
              patch.object(fight, "_landed", observe_landed)):
            for first in range(1, inputs["commands"] + 1, batch):
                last = min(first + batch - 1, inputs["commands"])
                run.frame = world_start + last
                run.match.update(run.frame * combat.TICK, run.frame)
                source.take_commands([
                    Command(command_start + number,
                            buttons=FIRE if number <= inputs["held_commands"] else 0)
                    for number in range(first, last + 1)
                ])
            run.frame = world_start + inputs["query_offset"]
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual([frame - command_start for frame, _ in shots],
                             expected["shot_command_offsets"])
            self.assertEqual(len(fight.rockets), 4)
            for (_, rocket), position in zip(shots, expected["positions_at_query"], strict=True):
                self.assertAlmostEqual(rocket.position[2], position)
                self.assertAlmostEqual(rocket.flown, position / expected["projectile_speed"])
            for offset in range(inputs["query_offset"] + 1, 155):
                run.frame = world_start + offset
                run.match.update(run.frame * combat.TICK, run.frame)
        hit = distance == 90
        self.assertEqual(retired, expected["impact_world_offsets"] if hit
                         else expected["timeout_world_offsets"])
        helper = lucio_tests.LucioCombatTests()
        self.assertEqual(helper.health(run, enemy), expected["hit_enemy_health"] if hit
                         else expected["miss_enemy_health"])
        messages = source.client.messages(combat.HIT)
        self.assertEqual([message["+0x78"]["+0x4"] for message in messages],
                         [expected["hit_damage_each"]] * 4 if hit else [])
        weapon = next(instance for instance in source.body_script.component.instances.values()
                      if instance.graph.index == 0x0776)
        self.assertEqual(weapon.vars[53].value(), expected["remaining_ammo"])
        self.assertFalse(fight.rockets)

    def test_encrypted_packet_batches_keep_the_same_primary_positions(self):
        expected_positions = [82.4, 76.8, 70.4, 64.0]
        for batch in (1, 8, 32):
            with self.subTest(batch=batch):
                fixture = journal_tests.ServerJournalTests()
                session, sent = fixture.make()
                peers, (source, enemy) = fixture.connected(session, sent, first_hero=lucio.HERO)
                fight = source.match.combat
                source.match.collision_world = flat_world(0)
                source.mover.position[:] = (0, 0, 0)
                source.mover.body = None
                enemy.mover.position[:] = (0, 0, 1000)
                enemy.mover.body = None
                start = source.body_script.frame
                for first in range(1, 97, batch):
                    last = min(first + batch - 1, 96)
                    for number in range(first, last + 1):
                        now = 100.944 + number * combat.TICK
                        session.tick(now)
                    cursor = first
                    while cursor <= last:
                        end = min(last, 5) if cursor <= 5 else last
                        peers[0].commands(start + cursor, end - cursor + 1,
                                          FIRE if cursor <= 5 else 0, now=now)
                        cursor = end + 1
                    peers[1].drain()
                    peers[1].send(now=now)
                self.assertEqual(fight.tick, 1108)
                self.assertEqual(len(fight.rockets), 4)
                for rocket, position in zip(fight.rockets, expected_positions, strict=True):
                    self.assertAlmostEqual(rocket.position[2], position)
                    self.assertAlmostEqual(rocket.flown, position / 50)


    def test_unequal_epoch_sync_none_flights_retire_with_late_owner_and_observer_acks(self):
        expected = self.contract()["expected"]["unequal_epoch_delivery"]
        for batch in (1, 32):
            for hit in (True, False):
                with self.subTest(batch=batch, hit=hit):
                    self.check_sync_none_delivery(expected, batch, hit)

    def check_sync_none_delivery(self, expected, batch, hit):
        fixture = journal_tests.ServerJournalTests()
        session, sent = fixture.make()
        peers, (source, enemy) = fixture.connected(session, sent, first_hero=lucio.HERO)
        match = source.match
        match.collision_world = flat_world(0)
        peers[0].commands(expected["birth_owner"], 1, 0, now=100.944)
        peers[0].message(21616, journal_tests.pick(source.entity, 0x02E0000000000009), now=100.944)
        peers[0].message(21616, journal_tests.pick(source.entity, lucio.HERO), now=100.944)
        for number in range(8):
            now = 100.96 + number * combat.TICK
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        source.mover.position[:] = (0, 0, 0)
        source.mover.body = None
        enemy.mover.position[:] = (0, 0, 4 if hit else 1000)
        enemy.mover.body = None
        fight = match.combat
        self.assertEqual(source.body_script.world_frame_anchor,
                         (expected["birth_world"], expected["birth_owner"]))
        command_start = source.body_script.frame
        first_packet = len(sent)
        flights, retired = [], []
        commit, landed = fight._commit_attack, fight._landed

        def observe_commit(shooter, frame, payload, boost):
            if shooter is source and isinstance(payload, combat.Rocket):
                flights.append(payload)
            return commit(shooter, frame, payload, boost)

        def observe_landed(rocket, **kwargs):
            if rocket in flights:
                retired.append(fight.tick)
            return landed(rocket, **kwargs)

        with (patch.object(fight, "_commit_attack", observe_commit),
              patch.object(fight, "_landed", observe_landed),
              patch.object(peers[0], "drain"),
              patch.object(peers[1], "drain")):
            for first in range(1, 161, batch):
                last = min(first + batch - 1, 160)
                for number in range(first, last + 1):
                    now = 101.088 + number * combat.TICK
                    session.tick(now)
                cursor = first
                while cursor <= last:
                    end = min(last, 5) if cursor <= 5 else last
                    peers[0].commands(command_start + cursor, end - cursor + 1,
                                      FIRE if cursor <= 5 else 0, now=now)
                    cursor = end + 1
                peers[1].send(now=now)
        for number in range(161, 241):
            now = 101.088 + number * combat.TICK
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertEqual(len(flights), 4)
        self.assertTrue(all(rocket.shot is None for rocket in flights))
        self.assertEqual(retired, expected["impact_worlds"] if hit else expected["timeout_worlds"])
        notices = peers[0].hit_notices(first_packet)
        self.assertEqual([notice["+0x84"] for notice in notices], expected["impact_worlds"] if hit else [])
        self.assertEqual([notice["+0x78"]["+0x4"] for notice in notices], [20] * 4 if hit else [])
        target = next(target for target in fight._targets() if target.owner is enemy)
        self.assertEqual(fight.vitals_of(target).total, 120 if hit else 200)
        self.assertFalse(fight.rockets)
        self.assertEqual(len(fight.projectiles.shots), expected["shot_proxies"])
        self.assertEqual(len(fight.projectiles.observed), expected["observer_proxies"])
        self.assertEqual(fight.projectiles.next_seen, 0)
        self.assertIsNone(fight.projectiles.lease(source))
        weapon = next(instance for instance in source.body_script.component.instances.values()
                      if instance.graph.index == 0x0776)
        self.assertEqual(projectiles._sync(weapon.state(14)), expected["sync_mode"])
        watch = fight.observers.watches[(enemy.slot, source.body)]
        self.assertIsNotNone(watch.stream.base)
        self.assertFalse(watch.stream.base.states[(weapon.id, 14)][1])


    def test_physical_effect_deadline_input_lock_and_dose_dependencies(self):
        run, source, enemy, _ = lucio_tests.LucioCombatTests().scenario()
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is enemy)
        vitals = fight.vitals_of(target)
        impact = fight.tick - 10
        fight._hit_effect(target, combat.FLASHBANG_EFFECT, shooter=source, world_tick=fight.tick - 100)
        self.assertNotIn(target.entity, fight.stuns)
        fight._hit_effect(target, combat.SLEEP_DART_EFFECT, shooter=source, world_tick=impact)
        self.assertEqual(vitals.sleep_until, impact + combat.SLEEP_TICKS)
        self.assertEqual(vitals.sleep_wake_floor, impact + combat.SLEEP_WAKE_FLOOR_TICKS)
        lock = fight.input_locks[target.entity][-1]
        self.assertEqual(lock.start, impact)
        until = vitals.sleep_until
        fight._damage(source, target, 1, False, None, fight.tick, world_tick=impact - 1)
        self.assertEqual(vitals.sleep_until, until)
        fight._damage(source, target, 1, False, None, fight.tick, world_tick=impact + 1)
        self.assertEqual(vitals.sleep_until, impact + combat.SLEEP_WAKE_FLOOR_TICKS)
        fight.close_input_lock(target.entity, combat.SLEEP_DART_EFFECT, world_tick=impact + 2)
        self.assertEqual(lock.end, impact + 2)
        definition = combat.HitDef(10, None, duration=.2)
        fight._queue_rifle_dose(source, target, definition, 10, fight.tick, world_tick=impact)
        self.assertAlmostEqual(fight.timed_hits[-1].started, fight.now - 10 * combat.TICK)

    def test_shared_replay_restores_host_clock_after_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                self.check_replay_clock(fail)

    def check_replay_clock(self, fail):
        selected = SimpleNamespace(last_tick=97)
        other = SimpleNamespace(last_tick=90)
        fight = SimpleNamespace(now=1.6, tick=100, rockets=[selected, other])
        visited = []

        def step(rocket):
            self.assertIs(rocket, selected)
            visited.append(fight.tick)
            self.assertAlmostEqual(fight.now, fight.tick * projectile_clock.TICK)
            if fail and fight.tick == 99:
                raise RuntimeError("flight failure")
            rocket.last_tick = fight.tick

        fight._fly_current = step
        if fail:
            with self.assertRaisesRegex(RuntimeError, "flight failure"):
                projectile_clock.advance_accepted(fight, selected)
        else:
            projectile_clock.advance_accepted(fight, selected)
        self.assertEqual(visited, [98, 99] if fail else [98, 99, 100])
        self.assertEqual((fight.now, fight.tick), (1.6, 100))
        self.assertEqual(other.last_tick, 90)
