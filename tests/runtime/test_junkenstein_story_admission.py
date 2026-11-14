"""Prepared-runtime server admission; native local playback is a separate acceptance."""

import unittest

from ow174.game import content, junkenstein_scene, observers
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.match import Match
from ow174.game.script import runtime
from tests.runtime.test_game_server import pick

CASSIDY = 0x02E0000000000042


class JunkensteinStoryAdmissionTests(unittest.TestCase):
    def make_match(self, count=1):
        game_map = content.game_map(
            content.JUNKENSTEIN_MAP,
            content.JUNKENSTEIN_MODES[0],
            "Junkenstein's Revenge",
            team_sizes=(4, 0),
        )
        self.assertIsNotNone(game_map)
        factory = junkenstein_scene.runtime_factory(game_map)
        self.assertIsNotNone(factory)
        match = Match(
            game_map,
            card=0x063000000000003C,
            collision_provider=StaticCollisionProvider(flat_world(0)),
            scene_host_factory=factory,
        )
        players = [match.add_player(i + 1, f"Defender{i}", content.SOLDIER, 0, False) for i in range(count)]
        self.assertTrue(match._advance_scene(0.016, 1), match.scene_host_error)
        self.addCleanup(match.scene_host.close)
        return match, players

    def mode(self, match):
        owner = match.scene_host.units[runtime.GAME_MODE_ENTITY].component
        return next(instance for instance in owner.instances.values() if instance.graph.index == 0x0C8C)

    def assert_waiting(self, match):
        mode = self.mode(match)
        self.assertFalse(mode.node_state(mode.graph.node(6)).current)
        for position in (141, 142, 18):
            state = mode.states.get(mode.graph.node(position).state)
            self.assertFalse(state is not None and state.active, position)
        owner = match.scene_host.units[runtime.GAME_MODE_ENTITY].component
        self.assertFalse(
            any(
                instance.graph.index in (0x027F, 0x17DF, 0x181E, 0x0D94, 0x184C)
                for instance in owner.instances.values()
            )
        )

    def test_delayed_confirm_keeps_server_ready_closed_then_admits(self):
        match, (player,) = self.make_match()
        # A four-minute selection delay must not age the authoritative opening.
        for tick in (2, 100, 1000, 15000):
            self.assertTrue(match._advance_scene(tick * 0.016, tick), match.scene_host_error)
            self.assertFalse(player.has_body)
            self.assert_waiting(match)
        match.game_message(player, pick(player.entity, CASSIDY), 240.016)
        self.assertTrue(player.has_body)
        for tick in (15001, 15002):
            self.assertTrue(match._advance_scene(tick * 0.016, tick), match.scene_host_error)
        mode = self.mode(match)
        self.assertTrue(mode.node_state(mode.graph.node(6)).current)
        self.assertTrue(mode.node_state(mode.graph.node(141)).active)

    def test_all_admitted_defenders_must_confirm(self):
        match, players = self.make_match(2)
        match.game_message(players[0], pick(players[0].entity, CASSIDY), 0.032)
        for tick in (2, 3, 1000):
            self.assertTrue(match._advance_scene(tick * 0.016, tick), match.scene_host_error)
            self.assert_waiting(match)
        match.game_message(players[1], pick(players[1].entity, CASSIDY), 16.016)
        for tick in (1001, 1002):
            self.assertTrue(match._advance_scene(tick * 0.016, tick), match.scene_host_error)
        mode = self.mode(match)
        self.assertTrue(mode.node_state(mode.graph.node(6)).current)
        self.assertTrue(mode.node_state(mode.graph.node(141)).active)

    def test_waiting_cinematic_wire_state_does_not_age_with_mode_clock(self):
        match, (player,) = self.make_match()

        def cinematic_wire():
            host = match.scene_host
            snap = observers.snapshot(host.mode_projection)
            owners = {item for item, (index, _) in snap.instances.items() if index == 0x180A}
            self.assertEqual(len(owners), 1)
            return (
                {item: value for item, value in snap.instances.items() if item in owners},
                {key: value for key, value in snap.vars.items() if key[0] in owners},
                {key: value for key, value in snap.states.items() if key[0] in owners},
            )

        initial = cinematic_wire()
        for tick in (2, 1800, 15000):
            self.assertTrue(match._advance_scene(tick * 0.016, tick), match.scene_host_error)
            self.assertFalse(player.has_body)
            self.assert_waiting(match)
            self.assertEqual(cinematic_wire(), initial)
        mode_owner = match.scene_host.units[runtime.GAME_MODE_ENTITY].component
        self.assertGreater(mode_owner.entity_var(19530).value(), 239.0)
