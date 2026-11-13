"""Authored Pet/controller and VM/body identities, not native color acceptance."""

import unittest
from dataclasses import replace
from unittest import mock

import test_delivered_winston_barrier as fixtures
from test_game_repeated_lifecycles import wire_frame

from ow174.game import winston_barrier, world
from ow174.game.link import KIND_FRAME, Packet
from ow174.game.script import decode, expr
from ow174.game.script.driver import BodyStream


class WinstonBarrierRelationshipTests(unittest.TestCase):
    def arena(self, team, delayed=False):
        fixture = fixtures.BarrierTests()
        fixture.setUp()
        fixture.match.game_map = replace(fixture.match.game_map,
                                         free_for_all=False, team_sizes=(2, 2))
        fixture.owner.team = fixture.ally.team = team
        fixture.enemy.team = 1 - team
        if delayed:
            fixture.ally.spawned = False
        actor = fixture.cast()
        fixture.run.step({})
        return fixture, actor

    def assert_delivery(self, fixture, actor, viewers):
        # Original 00FA/CreateEntity26 evaluates component43/body -> controller.
        source = next(i for i in actor.source_component.instances.values()
                      if i.graph.index == 0xFA)
        authored = source.evaluate(source.graph.node(26).field("m_D749D7D8"))
        self.assertEqual(authored, expr.Entity(fixture.owner.entity))
        self.assertEqual(actor.pet_source, fixture.owner.entity)
        self.assertEqual(fixture.ally.team_bit, fixture.owner.team_bit)
        self.assertNotEqual(fixture.enemy.team_bit, fixture.owner.team_bit)
        self.assertNotEqual(fixture.owner.entity, actor.source_body)
        for viewer in viewers:
            creates = [u for u in viewer.client.entities
                       if u.entity == actor.entity and u.op == world.OP_CREATE]
            self.assertEqual(len(creates), 1)
            payload = world.frame(fixture.run.frame, 0, entities=creates)
            _, records = wire_frame(Packet(kind=KIND_FRAME, payload=payload))
            fields = next(fields for update, fields in records if update.entity == actor.entity)
            self.assertEqual(fields[72], {0: fixture.owner.entity})
            self.assertEqual(fields[26], {0: fixture.owner.team_bit})
        # 00FC's replicated source27 stays the caster BODY, unlike Pet72.
        projected = winston_barrier.projection(fixture.fight, actor.entity)
        frame, snapshot = projected.initial
        model = decode.ClientBody()
        model.apply(BodyStream(actor.entity).full(frame, snapshot))
        self.assertIn(expr.Entity(actor.source_body),
                      [value for (instance, var), value in model.vars.items() if var == 27])

    def test_both_teams_publish_actual_owner_to_self_ally_and_enemy(self):
        for team in (0, 1):
            with self.subTest(owner_team=team):
                fixture, actor = self.arena(team)
                self.assert_delivery(fixture, actor, fixture.run.players)

    def test_delayed_viewer_gets_same_owner_and_source(self):
        fixture, actor = self.arena(1, delayed=True)
        self.assertFalse(any(u.entity == actor.entity for u in fixture.ally.client.entities))
        fixture.ally.spawned = True
        fixture.run.step({})
        self.assert_delivery(fixture, actor, [fixture.ally])

    def test_missing_or_wrong_authored_owner_does_not_fabricate_a_pet(self):
        fixture, actor = self.arena(0)
        manager = fixture.fight.barriers.world
        source = next(i for i in actor.source_component.instances.values()
                      if i.graph.index == 0xFA)
        node = source.graph.node(26)
        query = node.field("m_D749D7D8")
        evaluate = source.evaluate
        before = (dict(manager.actors), manager.next_id)
        for unavailable in (expr.EVALUATION_FAILED, expr.Entity(0),
                            expr.Entity(actor.source_body), expr.Entity(fixture.enemy.entity)):
            with self.subTest(value=unavailable), mock.patch.object(
                    source, "evaluate", side_effect=lambda value, unavailable=unavailable:
                    unavailable if value is query else evaluate(value)):
                self.assertIs(manager.create(fixture.owner.body_script.world, source, node),
                              NotImplemented)
            self.assertEqual((manager.actors, manager.next_id), before)
