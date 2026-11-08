"""Known live player relationships, actual bow input and body retirement/storage."""

import unittest
from unittest.mock import patch

import test_game_hanzo as hero_tests

from ow174.game import bodytrace, movelog
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.script import body_owner, expr, runtime


def query(script, body):
    instance = next(iter(script.component.instances.values()))
    script.component.set(39999, expr.Entity(body))
    return instance.evaluate({"$": body_owner.CLASS, "m_801B298F": {
        "$": "STUConfigVarDynamic", "m_identifier": 39999, "m_60DB8F99": 1,
    }})


class HanzoBodyOwnerTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def test_real_player_bodies_resolve_their_live_controllers_and_bow_still_hits(self):
        run, hero, enemy = hero_tests.HanzoCombatTests().arena()
        self.assertEqual(query(hero.body_script, hero.body), expr.Entity(hero.entity))
        self.assertEqual(query(hero.body_script, enemy.body), expr.Entity(enemy.entity))
        self.assertNotEqual(hero.body, hero.entity)
        for _ in range(70):
            run.step({hero: (0, 0, 1)})
        run.step({})
        for _ in range(20):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 75)

    def test_replacement_retires_old_context_and_does_not_infer_an_old_or_unknown_relation(self):
        run, hero, _enemy = hero_tests.HanzoCombatTests().arena()
        old = hero.body_script
        old_body = hero.body
        context = old.world._body_owner_context
        run.match.switch_hero(hero, hero.hero)
        self.assertTrue(old.retired)
        self.assertTrue(context.retired)
        self.assertIsNot(hero.body_script.world._body_owner_context, context)
        self.assertEqual(query(hero.body_script, hero.body), expr.Entity(hero.entity))
        self.assertIs(hero.body_script.world._lookup_body_owner(old_body), NotImplemented)
        self.assertIs(hero.body_script.world._lookup_body_owner(0xB0002000), NotImplemented)

    def test_context_is_passively_stored_and_causal_without_running_queries(self):
        _run, hero, _enemy = hero_tests.HanzoCombatTests().arena()
        script = hero.body_script
        with patch.object(runtime.Instance, "evaluate", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(export_body_vm(script))
            self.assertEqual(before, snapshot_digest(export_body_vm(script)))
            script.world._body_owner_context.retired = True
            self.assertNotEqual(before, snapshot_digest(export_body_vm(script)))
