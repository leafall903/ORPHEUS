"""Authored affiliation inputs and actual observer records, not rendered colors."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_junkrat_tire as tires
import test_junkrat_traps as traps
from test_game_combat import in_world
from test_mei_ice_presentation import decode_ice_components

from ow174.game import junkrat_actor, world
from ow174.game.content import SOLDIER
from ow174.game.link import LinkCipher
from ow174.game.script import decode, entity_team, expr, runtime
from ow174.game.server import Client, GameServer


class JunkratAffiliationTests(unittest.TestCase):
    def test_tire_consumes_original_factory_source_separately_from_combat_body(self):
        for override in (False, True):
            with self.subTest(overridden_authored_input=override):
                scene = tires.JunkratTireTests()
                scene.setUp()
                scene.player.team = 1
                scene.run.match.game_map = replace(scene.run.match.game_map, free_for_all=False)
                evaluate = runtime.State.evaluate

                def source(state, cfg, *args, override_input=override, scene_input=scene,
                           evaluate_input=evaluate, **kwargs):
                    if (override_input and state.instance.graph.index == 0x232
                            and cfg is state.instance.graph.node(30).field("m_D749D7D8")):
                        return expr.Entity(scene_input.enemy.entity)
                    return evaluate_input(state, cfg, *args, **kwargs)

                with patch.object(runtime.State, "evaluate", new=source):
                    tire = scene.start()
                expected = scene.enemy.entity if override else scene.player.entity
                self.assertEqual(tire.pet_owner, expected)
                self.assertEqual(tire.source_body, scene.player.body)
                self.assertIs(tire.owner, scene.player)
                self.assertNotEqual(tire.pet_owner, tire.source_body)
                self.assertEqual(entity_team.index_from_filters(tire.filter_bits), 1)
                self.assertEqual(tire.filter_bits, scene.player.team_bit)

    def test_missing_authored_tire_owner_does_not_allocate_a_misowned_actor(self):
        scene = tires.JunkratTireTests()
        scene.setUp()
        evaluate = runtime.State.evaluate
        before = scene.system.next_entity

        def unavailable(state, cfg, *args, **kwargs):
            if (state.instance.graph.index == 0x232
                    and cfg is state.instance.graph.node(30).field("m_D749D7D8")):
                return expr.Entity(0)
            return evaluate(state, cfg, *args, **kwargs)

        scene.script.set_ult_charge(scene.script.component.vars[338].value())
        with patch.object(runtime.State, "evaluate", new=unavailable):
            scene.step(buttons=0x20)
        self.assertFalse(scene.system.actors)
        self.assertEqual(scene.system.next_entity, before)
        self.assertEqual(scene.system.last_error, "RIP-Tire authored Pet source is unavailable")

    def test_mine_keeps_volley_source_and_team_filter_before_landing(self):
        scene = traps.JunkratTrapTests()
        scene.setUp()
        scene.step(player_buttons=8)
        for _ in range(30):
            if scene.fight.junkrat.mines:
                break
            scene.step()
        mine, = scene.fight.junkrat.mines.values()
        self.assertEqual(mine.source_body, scene.player.body)
        self.assertEqual(mine.flight.shot.body, mine.source_body)
        self.assertEqual(mine.filter_bits, scene.player.team_bit)
        self.assertEqual(mine.filter_bits, mine.flight.shot.filter_bits)
        self.assertEqual(entity_team.index_from_filters(mine.filter_bits), 4)  # Existing FFA source flags.

    def test_trap_cast_and_owner_ally_enemy_late_join_keep_actual_team_inputs(self):
        scene = traps.JunkratTrapTests()
        scene.setUp()
        match = scene.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        scene.player.team, scene.enemy.team = 1, 0
        ally = match.add_player(3, "Ally", SOLDIER, 1, False)
        in_world(match, ally)
        scene.step(player_buttons=16)
        for _ in range(30):
            if scene.fight.junkrat.traps:
                break
            scene.step()
        trap, = scene.fight.junkrat.traps.values()
        self.assertFalse(trap.parked)
        self.assertEqual(trap.filter_bits, scene.player.team_bit)
        self.assertEqual(trap.flight.shot.body, scene.player.body)
        self.assertEqual(trap.flight.shot.filter_bits, trap.filter_bits)
        for _ in range(60):
            scene.step()
        self.assertTrue(trap.parked)
        for viewer in (scene.player, ally, scene.enemy):
            create, = [u for u in viewer.client.entities
                       if u.entity == trap.entity and u.op == world.OP_CREATE]
            fields = decode_ice_components(create.build(0), create=True)
            self.assertEqual(fields[26][0], trap.filter_bits)
            self.assertEqual(entity_team.index_from_filters(fields[26][0]), 1)

        late = match.add_player(4, "Late ally", SOLDIER, 1, False)
        in_world(match, late)
        client = Client(GameServer(), ("memory.test", 43004), 4,
                        LinkCipher(bytes([4]) * 32, bytes([14]) * 32), late)
        late.client = client
        client.open = True
        client.queue_entities(match.spawn_updates(late))
        junkrat_actor.sync(scene.fight.junkrat)
        create, = [u for u in client.entities if u.entity == trap.entity and u.op == world.OP_CREATE]
        self.assertEqual(decode_ice_components(create.build(0), create=True)[26][0], trap.filter_bits)
        client._ack_entity_create(trap.entity)
        record = next(u for u in client.entities if u.entity == trap.entity and u.chunk is not None)
        model = decode.ClientBody((0x33, 0x25B4))
        model.apply(record)
        self.assertEqual(model.vars[(3, 27)], expr.Entity(scene.player.body))
        self.assertEqual(model.vars[(3, 40)], expr.Entity(scene.player.body))
        self.assertEqual(trap.filter_bits, 1 << 24)  # Native team1, not a chosen blue/red value.
