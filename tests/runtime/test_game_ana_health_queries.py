"""Match-backed numeric health queries against actual player health lifetimes."""
import unittest

import test_game_ana as fixture
from test_game_combat import aim

from ow174.game import combat
from ow174.game.content import find_hero
from ow174.game.script import expr, health_queries


def read(actor, target=None, *, maximum=False, initial=None):
    instance = next(iter(actor.body_script.component.instances.values()))
    return instance.evaluate({"$": health_queries.MAXIMUM if maximum else health_queries.CURRENT,
                              "m_entity": {"$": "STU_B5A0CAF0"} if target is None
                              else expr.Entity(target)}, initial=initial)


class AnaHealthQueryTests(unittest.TestCase):
    def test_actual_enemy_weapon_damage_and_healing_change_current_not_maximum(self):
        run, ana, enemy, _entry = fixture.AnaRifleTests().setup_fight(enemy=True)
        fight = run.match.combat
        self.assertEqual((read(ana), read(ana, maximum=True)), (200, 200))
        direction = aim(fight._eye(enemy), fight._eye(ana))
        run.step({enemy: (*direction, 1)})
        for _ in range(5):
            run.step({})
        # Eye-to-eye hits the head: Soldier's 20-point round doubles to 40.
        self.assertEqual((read(ana), read(ana, maximum=True)), (160, 200))
        own = next(t for t in fight._targets() if t.owner is ana)
        fight._heal(ana, own, 7)
        self.assertEqual((read(ana), read(ana, maximum=True)), (167, 200))

    def test_other_body_shield_pools_death_and_replacement_identity(self):
        run, ana, target, old_entry = fixture.AnaRifleTests().setup_fight(enemy=True)
        self.assertTrue(run.match.switch_hero(target, find_hero("Zarya")))
        target.mover.position = list(old_entry.feet)
        fight = run.match.combat
        entry = next(t for t in fight._targets() if t.owner is target)
        self.assertEqual((read(ana, target.body), read(ana, target.body, maximum=True)), (400, 400))
        fight._damage(ana, entry, 70, False, None, run.frame)
        self.assertEqual((read(ana, target.body), read(ana, target.body, maximum=True)), (330, 400))
        fight._damage(ana, entry, 1000, False, None, run.frame)
        self.assertEqual((read(ana, target.body), read(ana, target.body, maximum=True)), (0, 400))
        old_body = target.body
        self.assertTrue(run.match.switch_hero(target, find_hero("Soldier")))
        self.assertEqual(read(ana, old_body, initial=77), 77)
        self.assertEqual((read(ana, target.body), read(ana, target.body, maximum=True)), (200, 200))

    def test_known_controllers_and_mode_have_no_health_and_reads_do_not_allocate_pools(self):
        run, ana, _target, _entry = fixture.AnaRifleTests().setup_fight(enemy=True)
        fight = run.match.combat
        fight.vitals.clear()
        self.assertEqual(read(ana), 200)
        self.assertEqual(read(ana, maximum=True), 200)
        self.assertEqual(fight.vitals, {})
        self.assertEqual(read(ana, ana.entity, initial=77), 0)
        self.assertEqual(read(ana, combat.world.GAME_MODE_ENTITY, initial=77), 0)
        self.assertEqual(read(ana, 0xA1234567, initial=77), 77)

    def test_real_server_snapshot_is_unchanged_by_health_service(self):
        import test_server_snapshot as snapshots

        helper = snapshots.ServerSnapshotTests()
        session, _peers, players = helper.scene(two=True)
        actor = players[0]
        before = snapshots.snapshot_digest(helper.capture(session))
        # Expression evaluation resets the VM work budget. Isolate the world
        # service to prove pool lookup itself does not allocate/mutate state.
        self.assertEqual(actor.body_script.component.world.body_health(actor.body), (200, 200))
        self.assertEqual(snapshots.snapshot_digest(helper.capture(session)), before)

    def test_existing_body_lookup_does_not_require_a_network_client(self):
        run, ana, target, entry = fixture.AnaRifleTests().setup_fight(enemy=True)
        fight = run.match.combat
        fight._damage(ana, entry, 25, False, None, run.frame)
        target.client = None
        self.assertFalse(any(t.entity == target.body for t in fight._targets()))
        self.assertEqual((read(ana, target.body), read(ana, target.body, maximum=True)), (175, 200))
