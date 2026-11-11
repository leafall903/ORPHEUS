import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_mei_combat as fixtures
import test_mei_pve as pve_fixtures
import test_mei_wall as walls
import test_server_snapshot as snapshots
from test_game_combat import aim, in_world
from test_game_server import pick

from ow174.game import combat, mei
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import runtime
from ow174.game.server_snapshot import snapshot_digest


class MeiHookTests(unittest.TestCase):
    def scene(self, distance=8.0):
        run, owner, hog, _ = fixtures.MeiCombatTests().duel()
        self.assertTrue(run.match.switch_hero(hog, heroes()[0x02E0000000000040]))
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(hog, (x, y, z + distance))
        for _ in range(24):
            run.step({})
        return run, owner, hog, fight

    def health(self, fight, actor):
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is actor)).total

    def angle(self, fight, hog, owner):
        return aim(fight._eye(hog), fight._eye(owner))

    def launch(self, run, owner, hog, fight):
        angle = self.angle(fight, hog, owner)
        for frame in range(30):
            run.step({hog: (*angle, 8 if frame == 0 else 0)})
            if hog.body in fight.hooks.flights:
                flight = fight.hooks.flights[hog.body]
                self.assertIsNone(flight.target)
                return flight
        self.fail("actual Hook did not reach the launch state")

    def test_real_hook_damage_pull_and_recovery(self):
        run, owner, hog, fight = self.scene()
        start = owner.mover.position[2]
        flight = self.launch(run, owner, hog, fight)
        active = False
        for _ in range(60):
            run.step({owner: (0, 0, 1)})
            active |= fight.hooks.controlled(owner.body)
        self.assertTrue(active)
        self.assertEqual(self.health(fight, owner), 220.0)
        self.assertAlmostEqual(owner.mover.position[2] - start, 5.0, places=2)
        self.assertAlmostEqual(hog.mover.position[2] - owner.mover.position[2], 3.0, places=2)
        self.assertFalse(fight.hooks.flights)
        self.assertEqual((flight.speed, flight.reach), (60.0, 20.0))
        self.assertEqual(flight.damage.amount, 30.0)

    def test_cryo_blocks_in_flight_hook_and_protects_rear(self):
        run, owner, hog, fight = self.scene(distance=14.0)
        x, y, z = owner.mover.position
        rear = run.match.add_player(3, "Rear", SOLDIER, owner.team, False)
        in_world(run.match, rear)
        run.players.append(rear)
        self.assertTrue(run.match.switch_hero(rear, heroes()[SOLDIER]))
        fight.walls.place_player(rear, (x, y, z - 1.8))
        flight = self.launch(run, owner, hog, fight)
        run.step({owner: (0, 0, 8)})
        self.assertTrue(mei.protected(owner))
        for _ in range(50):
            run.step({})
            self.assertFalse(fight.hooks.controlled(owner.body) or fight.hooks.controlled(rear.body))
        self.assertIsNone(flight.target)
        self.assertEqual((self.health(fight, owner), self.health(fight, rear)), (250.0, 200.0))
        self.assertFalse(fight.hooks.flights)

    def test_cryo_cancel_in_flight_allows_same_hook_to_hit(self):
        run, owner, hog, fight = self.scene(distance=14.0)
        run.step({owner: (0, 0, 8)})
        for _ in range(25):
            run.step({})
        flight = self.launch(run, owner, hog, fight)
        self.assertTrue(mei.protected(owner))
        run.step({owner: (0, 0, 2)})
        self.assertFalse(mei.protected(owner))
        self.assertIs(fight.hooks.flights[hog.body], flight)
        for _ in range(38):
            run.step({})
        self.assertIs(flight.target.owner, owner)
        self.assertEqual(self.health(fight, owner), 220.0)

    def test_actual_wall_takes_hook_and_cooldown_rejects_immediate_retry(self):
        run, owner, hog, fight = self.scene()
        pieces, wall_aim = walls.WallTests().place(run, owner, distance=3.0)
        self.assertEqual(len(pieces), 5)
        self.launch(run, owner, hog, fight)
        for _ in range(32):
            run.step({})
        self.assertEqual(pieces[2].health, 370.0)
        self.assertEqual(self.health(fight, owner), 250.0)
        self.assertFalse(fight.hooks.flights)
        walls.WallTests().tap(run, owner, 16, wall_aim)
        angle = self.angle(fight, hog, owner)
        for frame in range(45):
            run.step({hog: (*angle, 8 if frame == 0 else 0)})
            self.assertFalse(fight.hooks.flights)
        self.assertEqual(self.health(fight, owner), 250.0)
        for _ in range(520):
            run.step({})
        self.launch(run, owner, hog, fight)
        for _ in range(45):
            run.step({})
        self.assertEqual(self.health(fight, owner), 220.0)

    def caught(self):
        run, owner, hog, fight = self.scene()
        self.launch(run, owner, hog, fight)
        for _ in range(20):
            run.step({})
            if fight.hooks.controlled(owner.body):
                break
        self.assertTrue(fight.hooks.controlled(owner.body))
        return run, owner, hog, fight

    def test_commandless_pull_matches_stepped_motion_and_damage(self):
        results = []
        for jump in (False, True):
            run, owner, _, fight = self.caught()
            if jump:
                run.frame += 45
                run.match.update(run.frame * 0.016, run.frame, frames=False)
            else:
                for _ in range(45):
                    run.step({})
            self.assertFalse(fight.hooks.flights)
            results.append((tuple(owner.mover.position), self.health(fight, owner)))
        self.assertEqual(*results)
        self.assertEqual(results[0][1], 220.0)

    def test_hook_blocks_cryo_input_and_body_replacement_retires_pull(self):
        run, owner, _, fight = self.caught()
        run.step({owner: (0, 0, 8)})
        self.assertFalse(mei.protected(owner))
        self.assertTrue(fight.hooks.controlled(owner.body))
        body = owner.body
        self.assertTrue(run.match.switch_hero(owner, owner.hero))
        self.assertNotEqual(owner.body, body)
        self.assertFalse(fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(body))
        position = tuple(owner.mover.position)
        for _ in range(25):
            run.step({})
        self.assertEqual(tuple(owner.mover.position), position)

    def test_actual_teammate_freeze_retires_attached_hook(self):
        freeze_at = self.freeze_hook_caster()
        self.freeze_hook_caster(freeze_at - 24)

    def freeze_hook_caster(self, cast_at=None):
        run, owner, hog, fight = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        helper = run.match.add_player(3, "Second Mei", SOLDIER, owner.team, False)
        in_world(run.match, helper)
        run.players.append(helper)
        self.assertTrue(run.match.switch_hero(helper, heroes()[fixtures.MEI]))
        x, y, z = hog.mover.position
        fight.walls.place_player(helper, (x + 5.0, y, z - 2.0))
        for _ in range(15):
            run.step({})
        spray = aim(fight._eye(helper), fight._eye(hog))
        hook = self.angle(fight, hog, owner)
        caught = None
        for frame in range(160):
            run.step({helper: (*spray, 1), hog: (*hook, 8 if frame == cast_at else 0)})
            if caught is None and fight.hooks.controlled(owner.body):
                caught = frame
            if fight.frozen(hog):
                break
        self.assertTrue(fight.frozen(hog))
        if cast_at is None:
            return frame
        self.assertIsNotNone(caught)
        self.assertLess(caught, frame)
        self.assertFalse(fight.hooks.flights)
        position = tuple(owner.mover.position)
        for _ in range(12):
            run.step({})
        self.assertEqual(tuple(owner.mover.position), position)
        self.assertEqual(self.health(fight, owner), 220.0)

    def test_active_pull_is_in_passive_server_snapshot(self):
        fixture = snapshots.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        hog, mei = players
        peers[0].message(21616, pick(hog.entity, 0x02E0000000000040), now=100.8)
        peers[1].message(21616, pick(mei.entity, 0x02E00000000000DD), now=100.8)
        session.tick(100.960)
        fight = hog.match.combat
        x, y, z = mei.mover.position
        fight.walls.place_player(hog, (x, y, z + 8.0))
        attack = aim(fight._eye(hog), fight._eye(mei))
        for offset in range(55):
            session.tick(100.976 + offset * 0.016)
            hog.take_commands(
                [
                    Command(
                        hog.body_script.frame + 1,
                        yaw=attack[0],
                        pitch=attack[1],
                        buttons=8 if offset == 1 else 0,
                    )
                ]
            )
            mei.take_commands([Command(mei.body_script.frame + 1)])
            if fight.hooks.controlled(mei.body):
                break
        assert fight.hooks.controlled(mei.body)
        flight = fight.hooks.flights[hog.body]
        assert flight.target.owner is mei and flight.instance.graph.index == 0x01C8
        with patch.object(
            runtime.Component, "run_now", side_effect=AssertionError("snapshot evaluated the VM")
        ):
            before = snapshot_digest(fixture.capture(session))
            flight.travelled += 0.01
            assert before != snapshot_digest(fixture.capture(session))

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_DATA_DIR"), "requires the 104319 Talon NPC bundle")
    def test_hook_pulls_each_actual_talon_npc_in_meis_match(self):
        case = pve_fixtures.MeiPveTests()
        case.setUpClass()
        case.setUp()
        self.addCleanup(case.doCleanups)
        for definition in case.definitions.values():
            with self.subTest(npc=definition.name):
                run, mei_player, bot, _ = case.scene(definition, distance=6.0)
                hog = next(player for player in run.players if player is not mei_player)
                self.assertTrue(run.match.switch_hero(hog, heroes()[0x02E0000000000040]))
                fight = run.match.combat
                x, y, z = mei_player.mover.position
                fight.walls.place_player(hog, (x, y, z))
                fight.walls.place_player(mei_player, (x + 3.0, y, z))
                for _ in range(12):
                    run.step({})
                angle = aim(fight._eye(hog), (bot.position[0], bot.position[1] + 1.1, bot.position[2]))
                initial = tuple(bot.position)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                expected.damage(30.0)
                caught = False
                for frame in range(85):
                    run.step({hog: (*angle, 8 if frame == 0 else 0)})
                    caught |= fight.hooks.controlled(bot.entity)
                self.assertTrue(caught)
                self.assertFalse(fight.hooks.flights)
                self.assertEqual(fight.vitals[bot.entity].now, expected.now)
                self.assertLess(bot.position[2], initial[2] - 1.0)
                self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))

    def test_hook_range_and_static_los(self):
        for reason, distance, expected in (
            ("in range", 19.5, 220.0),
            ("out of range", 21.5, 250.0),
            ("static cover", 8.0, 250.0),
        ):
            with self.subTest(reason=reason):
                run, owner, hog, fight = self.scene(distance=distance)
                if reason == "static cover":
                    fixtures.static_wall(run, owner, distance=3.0)
                self.launch(run, owner, hog, fight)
                for _ in range(75):
                    run.step({})
                self.assertEqual(self.health(fight, owner), expected)
                self.assertFalse(fight.hooks.flights)

    def test_hook_passes_unprotected_ally_and_hits_hostile_rear_body(self):
        run, owner, hog, fight = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = hog.team
        x, y, z = owner.mover.position
        rear = run.match.add_player(3, "Hostile rear", SOLDIER, 1 - hog.team, False)
        in_world(run.match, rear)
        run.players.append(rear)
        self.assertTrue(run.match.switch_hero(rear, heroes()[SOLDIER]))
        fight.walls.place_player(rear, (x, y, z - 2.0))
        self.launch(run, owner, hog, fight)
        caught = False
        for _ in range(65):
            run.step({})
            caught |= fight.hooks.controlled(rear.body)
            self.assertFalse(fight.hooks.controlled(owner.body))
        self.assertTrue(caught)
        self.assertEqual((self.health(fight, owner), self.health(fight, rear)), (250.0, 170.0))


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
