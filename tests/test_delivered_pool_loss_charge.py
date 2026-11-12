"""Source0006 pool-loss policy on existing Genji/Orisa charge hosts and real Vitals."""

import os
import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from ow174.game import orisa_charge, torbjorn_charge
from ow174.game.content import heroes
from ow174.game.script import message_receive

GENJI = 0x02E0000000000029
ORISA = orisa_charge.ORISA
TAG = torbjorn_charge.ADAPTIVE_SHIELD_TAG


class DeliveredPoolLossChargeTests(unittest.TestCase):
    def arena(self, hero):
        if hero == ORISA and not os.environ.get("ORPHEUS_ORISA_CHARGE"):
            self.skipTest("requires original Orisa body-tag metadata")
        run = Run(KANEZAKA_DM, ("Source", "Victim"))
        source, victim = run.players
        self.assertTrue(run.match.switch_hero(source, heroes()[hero]))
        source.body_script.ult_seconds = 0
        run.step({})
        source.body_script.set_ult_charge(0)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        return run, source, victim, target, fight.vitals_of(target)

    @staticmethod
    def charge(source):
        return source.body_script.component.entity_var(342).value()

    def test_real_tagged_pool_loss_is_deducted_by_only_the_existing_host(self):
        for hero in (GENJI, ORISA):
            for tag, amount, expected in ((TAG, 100, 0), (TAG, 50, 20), (0x5847, 50, 70)):
                with self.subTest(hero=hero, tag=tag, amount=amount):
                    run, source, _, target, hp = self.arena(hero)
                    self.assertEqual(hp.grant_temporary_shields(tag, amount, amount), amount)
                    pool = next(p for p in hp.extra.values() if p.identifier == tag and not p.removed)
                    before = hp.total
                    script = source.body_script
                    with patch.object(message_receive, "receive", wraps=message_receive.receive) as receive:
                        with patch.object(script, "gain_ult_charge", wraps=script.gain_ult_charge) as gain:
                            self.assertTrue(run.match.combat._damage(source, target, 70, False, None, run.frame))
                    self.assertEqual(before - hp.total, 70)
                    self.assertEqual(pool.now, max(0, amount - 70))
                    self.assertEqual(self.charge(source), expected)
                    damage_packets = [call.args[1] for call in receive.call_args_list
                                      if call.args[1].message == orisa_charge.DEALT]
                    self.assertEqual(len(damage_packets), 1 if hero == ORISA else 0)
                    self.assertEqual(gain.call_count, 0 if hero == ORISA else 1)
                    if hero == ORISA:
                        controller = next(i for i in script.component.instances.values() if i.graph.index == 0xB8B)
                        loss = min(amount, 70) if tag == TAG else 0
                        self.assertEqual(controller.vars[16100].value(), ((TAG, float(loss)),) if loss else ())
                        self.assertEqual(controller.vars[16102].value(), float(loss))

    def test_overkill_deducts_only_actual_extra_pool_loss(self):
        for hero in (GENJI, ORISA):
            with self.subTest(hero=hero):
                run, source, _, target, hp = self.arena(hero)
                self.assertEqual(hp.grant_temporary_shields(TAG, 50, 50), 50)
                self.assertTrue(run.match.combat._damage(source, target, 400, False, None, run.frame))
                self.assertEqual(hp.total, 0)
                self.assertEqual(self.charge(source), 200)

    def test_actual_adaptive_cast_damage_spill_and_expiry_use_same_extra_pool(self):
        for hero in (GENJI, ORISA):
            with self.subTest(hero=hero):
                run, source, victim, _, _ = self.arena(hero)
                self.assertTrue(run.match.switch_hero(victim, heroes()[torbjorn_charge.WRECKING_BALL]))
                source.mover.position[:] = (0, 0, 0)
                victim.mover.position[:] = (0, 0, 100)
                victim.mover.body = None
                for _ in range(35):
                    run.step({})
                run.step({victim: (0, 0, 16)})
                for _ in range(3):
                    run.step({})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is victim)
                hp = fight.vitals_of(target)
                pool = hp.extra[("adaptive", TAG)]
                self.assertEqual(pool.now, 100)
                source.body_script.set_ult_charge(0)
                base = hp.base_total
                self.assertTrue(fight._damage(source, target, 130, False, None, run.frame))
                self.assertEqual(pool.now, 0)
                self.assertEqual(base - hp.base_total, 25)
                self.assertEqual(self.charge(source), 25)
                with patch.object(message_receive, "receive", wraps=message_receive.receive) as receive:
                    for _ in range(600):
                        run.step({})
                self.assertTrue(pool.removed or ("adaptive", TAG) not in hp.extra)
                self.assertFalse(any(call.args[1].message == orisa_charge.DEALT for call in receive.call_args_list))

    def test_retired_source_and_suppressed_award_do_not_credit_new_body(self):
        for hero in (GENJI, ORISA):
            for retired in (False, True):
                with self.subTest(hero=hero, retired=retired):
                    run, source, _, target, hp = self.arena(ORISA if hero == GENJI else GENJI)
                    old_body = source.body
                    self.assertTrue(run.match.switch_hero(source, heroes()[hero]))
                    source.body_script.ult_seconds = 0
                    run.step({})
                    source.body_script.set_ult_charge(0)
                    self.assertNotEqual(old_body, source.body)
                    self.assertEqual(hp.grant_temporary_shields(TAG, 50, 50), 50)
                    before = hp.total
                    with patch.object(message_receive, "receive", wraps=message_receive.receive) as receive:
                        self.assertTrue(run.match.combat._damage(
                            source, target, 70, False, None, run.frame,
                            source_body=old_body if retired else source.body,
                            award_ult=retired,
                        ))
                    self.assertEqual(before - hp.total, 70)
                    self.assertEqual(hp.extra[("adaptive", TAG)].now, 0)
                    self.assertEqual(self.charge(source), 0)
                    self.assertFalse(any(call.args[1].message == orisa_charge.DEALT for call in receive.call_args_list))
