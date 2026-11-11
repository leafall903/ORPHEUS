"""Rifle time/pool/source behavior and projectile damage-boost capture, using real casts."""

import unittest

import test_game_ana as rifle_fixture
import test_game_ana_nano as nano_fixture
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.content import find_hero
from ow174.game.world import ARMOUR_POOL


class AnaTimedRifleTests(unittest.TestCase):
    def fight(self, *, ally=False, distance=6):
        return rifle_fixture.AnaRifleTests().setup_fight(enemy=not ally, distance=distance)

    def release(self, run, shooter, entry, *, scoped=False):
        direction = aim(run.match.combat._eye(shooter),
                        (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        if scoped:
            for _ in range(35):
                run.step({shooter: (*direction, 2)})
        run.step({shooter: (*direction, 1 | (2 if scoped else 0))})
        for _ in range(10):
            if run.match.combat.timed_hits:
                break
            run.step({shooter: (*direction, 2 if scoped else 0)})
        self.assertEqual(len(run.match.combat.timed_hits), 1)
        return direction

    def test_hip_and_scoped_damage_and_healing_progress_over_time_then_complete(self):
        for ally in (False, True):
            for scoped in (False, True):
                with self.subTest(ally=ally, scoped=scoped):
                    run, shooter, _target, entry = self.fight(ally=ally)
                    fight = run.match.combat
                    vitals = fight.vitals_of(entry)
                    if ally:
                        vitals.damage(100)
                    before = vitals.total
                    direction = self.release(run, shooter, entry, scoped=scoped)
                    self.assertEqual(vitals.total, before)
                    for _ in range(12):
                        run.step({shooter: (*direction, 2 if scoped else 0)})
                    delta = abs(vitals.total - before)
                    self.assertGreater(delta, 0)
                    self.assertLess(delta, 70)
                    for _ in range(35):
                        run.step({shooter: (*direction, 2 if scoped else 0)})
                    self.assertEqual(vitals.total, before + (70 if ally else -70))
                    self.assertEqual(fight.timed_hits, [])
                    self.assertEqual(shooter.stats.value(stats.HEALING if ally else stats.HERO_DAMAGE), 70)

    def test_ana_dose_bypasses_actual_hero_armour_but_soldier_bullets_do_not(self):
        run, shooter, target, _entry = self.fight()
        self.assertTrue(run.match.switch_hero(target, find_hero("Brigitte")))
        x, y, z = shooter.mover.position
        target.mover.position = [x, y, z + 6]
        run.step({})
        fight = run.match.combat
        entry = next(t for t in fight._targets() if t.owner is target)
        vitals = fight.vitals_of(entry)
        self.assertGreater(vitals.now[ARMOUR_POOL], 0)
        before = vitals.total
        self.release(run, shooter, entry)
        for _ in range(45):
            run.step({})
        self.assertEqual(vitals.total, before - 70)
        vitals.heal(1000)
        self.assertTrue(run.match.switch_hero(shooter, find_hero("Soldier: 76")))
        direction = aim(fight._eye(shooter), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        before = vitals.total
        run.step({shooter: (*direction, 1)})
        for _ in range(5):
            run.step({})
        self.assertEqual(vitals.total, before - 15)

    def test_applied_dose_survives_caster_replacement_with_original_killer_identity(self):
        run, shooter, target, entry = self.fight()
        fight = run.match.combat
        vitals = fight.vitals_of(entry)
        vitals.damage(180)
        old_source = shooter.body
        self.release(run, shooter, entry)
        self.assertTrue(run.match.switch_hero(shooter, find_hero("Reaper")))
        self.assertNotEqual(shooter.body, old_source)
        for _ in range(50):
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, old_source)
        self.assertEqual(target.client.messages(combat.DEATH_NOTICE)[-1]["+0x7C"], {"+0x0": old_source})
        self.assertEqual(shooter.client.messages(combat.KILL_NOTICE)[-1]["+0x80"], {"+0x0": old_source})

    def test_target_replacement_discards_old_dose_and_never_heals_or_damages_new_body(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, shooter, target, entry = self.fight(ally=ally)
                fight = run.match.combat
                if ally:
                    fight.vitals_of(entry).damage(100)
                self.release(run, shooter, entry)
                old_target = target.body
                self.assertTrue(run.match.switch_hero(target, target.hero))
                self.assertNotIn(old_target, fight.vitals)
                self.assertEqual(fight.timed_hits, [])
                for _ in range(50):
                    run.step({})
                fresh = next(t for t in fight._targets() if t.owner is target)
                self.assertEqual(fight.vitals_of(fresh).total, 200)

    def test_skipped_world_ticks_complete_the_same_dose_once(self):
        run, shooter, _target, entry = self.fight()
        fight = run.match.combat
        self.release(run, shooter, entry)
        run.frame += 55
        run.match.update(run.frame * 0.016, run.frame, frames=False)
        self.assertEqual(fight.vitals_of(entry).total, 130)
        self.assertEqual(fight.timed_hits, [])
        run.match.update(run.frame * 0.016, run.frame, frames=False)
        self.assertEqual(fight.vitals_of(entry).total, 130)

    def boosted_scene(self):
        fixture = nano_fixture.AnaNanoTests()
        run, caster, shooter, enemy = fixture.fight(ally_hero="Ana")
        # Reset the enemy body before its first new command history so its pose
        # can be placed60m away; Nano caster and shooter remain within40m.
        self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
        x, y, z = shooter.mover.position
        enemy.mover.position = [x + 60, y, z]
        run.step({})
        return fixture, run, caster, shooter, enemy

    def test_projectile_keeps_firing_time_boost_after_nano_expires_in_flight(self):
        fixture, run, caster, shooter, enemy = self.boosted_scene()
        fight = run.match.combat
        fixture.cast(run, caster, shooter)
        source_vitals = fight.vitals_of(fixture.target(fight, shooter))
        while source_vitals.nano_until - fight.now > 0.15:
            run.step({})
        direction = fixture.direction(fight, shooter, enemy)
        run.step({shooter: (*direction, 1)})
        for _ in range(100):
            run.step({})
        self.assertLessEqual(source_vitals.nano_until, fight.now)
        self.assertEqual(fight.vitals_of(fixture.target(fight, enemy)).total, 95)

    def test_new_nano_during_flight_does_not_retroactively_boost_projectile(self):
        fixture, run, caster, shooter, enemy = self.boosted_scene()
        fight = run.match.combat
        direction = fixture.direction(fight, shooter, enemy)
        run.step({shooter: (*direction, 1)})
        fixture.cast(run, caster, shooter)
        self.assertGreater(fight.vitals_of(fixture.target(fight, shooter)).nano_until, fight.now)
        for _ in range(100):
            run.step({})
        self.assertEqual(fight.vitals_of(fixture.target(fight, enemy)).total, 130)

    def test_permanent_range_and_miss_matrix_for_both_modes_and_allegiances(self):
        for ally in (False, True):
            for scoped in (False, True):
                for distance, miss in ((99, False), (101, False), (6, True)):
                    with self.subTest(ally=ally, scoped=scoped, distance=distance, miss=miss):
                        run, shooter, _target, entry = self.fight(ally=ally, distance=distance)
                        fight = run.match.combat
                        vitals = fight.vitals_of(entry)
                        if ally:
                            vitals.damage(100)
                        before = vitals.total
                        direction = ((16384, 0) if miss else aim(fight._eye(shooter),
                                     (entry.feet[0], entry.feet[1] + 1, entry.feet[2])))
                        if scoped:
                            for _ in range(35):
                                run.step({shooter: (*direction, 2)})
                        run.step({shooter: (*direction, 1 | (2 if scoped else 0))})
                        for _ in range(110):
                            run.step({shooter: (*direction, 2 if scoped else 0)})
                        delta = (70 if ally else -70) if distance == 99 and not miss else 0
                        self.assertEqual(vitals.total, before + delta)

    def test_static_wall_blocks_rifle_healing_and_damage_in_both_modes(self):
        for ally in (False, True):
            for scoped in (False, True):
                with self.subTest(ally=ally, scoped=scoped):
                    run, shooter, _target, entry = self.fight(ally=ally)
                    fight = run.match.combat
                    vitals = fight.vitals_of(entry)
                    if ally:
                        vitals.damage(100)
                    before = vitals.total
                    x, y, z = shooter.mover.position
                    wall_z = z + 2.5
                    floor = [(x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
                             (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50)]
                    wall = [(x - 3, y, wall_z, x + 3, y, wall_z, x + 3, y + 4, wall_z),
                            (x - 3, y, wall_z, x + 3, y + 4, wall_z, x - 3, y + 4, wall_z)]
                    run.match.collision_world = World([*floor, *wall])
                    rifle_fixture.AnaRifleTests().shoot(run, shooter, entry, scoped)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(fight.timed_hits, [])

    def test_actual_crouch_shot_uses_ana_first_person_component_offset(self):
        run, shooter, _target, entry = self.fight()
        fight = run.match.combat
        self.assertEqual(fight._eye(shooter)[1], shooter.mover.position[1] + 1.25)
        for _ in range(20):
            run.step({shooter: (0, 0, 0x80)})
        self.assertTrue(shooter.mover.crouched)
        self.assertEqual(fight._eye(shooter)[1], shooter.mover.position[1] + 0.875)
        direction = aim(fight._eye(shooter), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        run.step({shooter: (*direction, 1 | 0x80)})
        for _ in range(55):
            run.step({shooter: (*direction, 0x80)})
        self.assertEqual(fight.vitals_of(entry).total, 130)

    def test_active_dose_is_captured_in_real_server_domain_snapshot(self):
        import test_server_snapshot as snapshot_fixture

        fixture = snapshot_fixture.ServerSnapshotTests()
        session, _peers, players = fixture.scene(two=True)
        shooter, target = players
        fight = shooter.match.combat
        self.assertTrue(session.apply("switch_hero", {"account": shooter.account_lo,
                                                    "hero": find_hero("Ana").guid}))
        entry = next(t for t in fight._targets() if t.owner is target)
        before = snapshot_fixture.snapshot_digest(fixture.capture(session))
        # Actual effect queue/actor identities, not a stand-in serialized dict.
        fight._direct_hit(shooter, entry, combat.HitDef(70, duration=0.6, bypass_armour=True),
                          70, False, shooter.match.tick)
        self.assertEqual(len(fight.timed_hits), 1)
        active = snapshot_fixture.snapshot_digest(fixture.capture(session))
        self.assertNotEqual(active, before)
        self.assertEqual(snapshot_fixture.snapshot_digest(fixture.capture(session)), active)


if __name__ == "__main__":
    unittest.main()
