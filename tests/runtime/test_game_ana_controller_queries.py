"""Match player relation binding; native NPC allocation remains explicitly unknown."""
import unittest

import test_game_ana as fixture

from ow174.game import world
from ow174.game.content import find_hero
from ow174.game.script import controller_queries as query
from ow174.game.script import expr


def read(actor, entity, cls=query.PREDICATE, *, initial=None):
    instance = next(iter(actor.body_script.component.instances.values()))
    return instance.evaluate({'$': cls,
                              'm_801B298F' if cls == query.RELATED else 'm_entity': expr.Entity(entity)},
                             initial=initial)


class AnaControllerQueryTests(unittest.TestCase):
    def test_real_player_body_and_controller_queries_use_distinct_identity(self):
        run, ana, target, _ = fixture.AnaRifleTests().setup_fight(enemy=True)
        for player in (ana, target):
            with self.subTest(player=player.body):
                self.assertEqual(read(ana, player.body, query.RELATED), expr.Entity(player.entity))
                self.assertIs(read(ana, player.body), True)
                self.assertEqual(read(ana, player.entity, query.RELATED), expr.Entity(0))
                self.assertIs(read(ana, player.entity), True)
                self.assertEqual(frozenset(run.match._possession(player)), frozenset((44, 75)))

    def test_death_keeps_relation_and_replacement_retires_old_body(self):
        run, ana, target, entry = fixture.AnaRifleTests().setup_fight(enemy=True)
        fight, old = run.match.combat, target.body
        fight._damage(ana, entry, 1000, False, None, run.frame)
        self.assertEqual(read(ana, old, query.RELATED), expr.Entity(target.entity))
        self.assertIs(read(ana, old), True)  # membership query is not a health query
        self.assertTrue(run.match.switch_hero(target, find_hero('Genji')))
        sentinel = object()
        self.assertIs(read(ana, old, query.RELATED, initial=sentinel), sentinel)
        self.assertIs(read(ana, old), False)
        self.assertEqual(read(ana, target.body, query.RELATED), expr.Entity(target.entity))

    def test_detached_client_does_not_remove_an_existing_relation(self):
        _run, ana, target, _ = fixture.AnaRifleTests().setup_fight(enemy=True)
        target.client = None
        self.assertEqual(read(ana, target.body, query.RELATED), expr.Entity(target.entity))
        self.assertIs(read(ana, target.body), True)

    def test_mode_and_absent_entity_keep_failure_and_zero_distinct(self):
        _run, ana, _target, _ = fixture.AnaRifleTests().setup_fight(enemy=True)
        self.assertEqual(read(ana, world.GAME_MODE_ENTITY, query.RELATED), expr.Entity(0))
        self.assertIs(read(ana, world.GAME_MODE_ENTITY), False)
        sentinel = object()
        self.assertIs(read(ana, 0xA1234567, query.RELATED, initial=sentinel), sentinel)
        self.assertIs(read(ana, 0xA1234567), False)

    def test_controller_record_service_leaves_real_server_snapshot_unchanged(self):
        import test_server_snapshot as snapshots

        helper = snapshots.ServerSnapshotTests()
        session, _peers, players = helper.scene(two=True)
        actor = players[0]
        before = snapshots.snapshot_digest(helper.capture(session))
        self.assertEqual(actor.match.controller_record(actor.body).related, actor.entity)
        self.assertEqual(actor.match.controller_record(actor.entity).components, frozenset((44, 75)))
        self.assertEqual(snapshots.snapshot_digest(helper.capture(session)), before)

    def test_actual_leave_removes_body_controller_and_health_lookup(self):
        from test_game_ana_health_queries import read as health_read

        run, ana, target, entry = fixture.AnaRifleTests().setup_fight(enemy=True)
        run.match.combat._damage(ana, entry, 25, False, None, run.frame)
        old_body, controller = target.body, target.entity
        run.match.leave(target)
        self.assertTrue(target.gone)
        self.assertIn(target, run.match.players)  # retained history is not an active entity
        sentinel = object()
        for entity in (old_body, controller):
            with self.subTest(entity=entity):
                self.assertIs(read(ana, entity, query.RELATED, initial=sentinel), sentinel)
                self.assertIs(read(ana, entity), False)
                self.assertIs(health_read(ana, entity, initial=sentinel), sentinel)
                self.assertIs(health_read(ana, entity, maximum=True, initial=sentinel), sentinel)


if __name__ == '__main__':
    unittest.main()
