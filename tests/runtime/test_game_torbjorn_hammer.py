"""Actual command-driven hammer damage under the documented timing reconstruction."""

import unittest
from dataclasses import replace

import test_game_torbjorn as torbjorn
from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, hero_torbjorn
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


class TorbjornHammerTests(unittest.TestCase):
    def arena(self, distance=1.5):
        run, shooter, victim = torbjorn.TorbjornProjectileTests().arena(distance)
        run.frame += 1
        shooter.take_commands([Command(run.frame, action=2)])
        for _ in range(45):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        return run, shooter, victim, fight.vitals_of(target)

    def swing(self, run, shooter, ticks=45, yaw=0):
        for _ in range(ticks):
            run.step({shooter: (yaw, 0, 1)})

    def test_hammer_delays_damage_and_hits_once_per_swing(self):
        run, shooter, _, vitals = self.arena()
        self.swing(run, shooter, 19)
        self.assertEqual(vitals.total, 200.0)
        self.swing(run, shooter, 1)
        self.assertEqual(vitals.total, 145.0)
        self.swing(run, shooter, 25)
        self.assertEqual(vitals.total, 145.0)
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x4"], 55)

    def test_hammer_repeat_rate_does_not_duplicate_an_activation(self):
        run, shooter, _, vitals = self.arena()
        self.swing(run, shooter, 100)
        self.assertEqual(vitals.total, 90.0)
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[1]["+0x84"] - hits[0]["+0x84"], 47)

    def test_overload_uses_earlier_impact_and_shorter_repeat_interval(self):
        run, shooter, _, vitals = self.arena()
        run.step({shooter: (0, 0, 16)})
        for _ in range(45):
            run.step({})
        self.swing(run, shooter, 9)
        self.assertEqual(vitals.total, 200.0)
        self.swing(run, shooter, 1)
        self.assertEqual(vitals.total, 145.0)
        self.swing(run, shooter, 33)
        self.assertEqual(vitals.total, 90.0)
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[1]["+0x84"] - hits[0]["+0x84"], 33)

    def test_hammer_miss_range_and_friendly_filter(self):
        for distance, yaw, friendly in ((5.0, 0, False), (1.5, -32768, False), (1.5, 0, True)):
            with self.subTest(distance=distance, yaw=yaw, friendly=friendly):
                run, shooter, victim, vitals = self.arena(distance)
                if friendly:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    victim.team = shooter.team
                self.swing(run, shooter, yaw=yaw)
                self.assertEqual(vitals.total, 200.0)
                self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_hammer_is_occluded_by_real_collision_triangles(self):
        run, shooter, _, vitals = self.arena()
        x, y, z = shooter.mover.position
        wall = z + 0.75
        run.match.collision_world = World([
            (x-100, y, z-100, x-100, y, z+100, x+100, y, z+100),
            (x-100, y, z-100, x+100, y, z+100, x+100, y, z-100),
            (x - 3, y, wall, x + 3, y, wall, x + 3, y + 4, wall),
            (x - 3, y, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
        ], name="hammer-wall")
        self.swing(run, shooter)
        self.assertEqual(vitals.total, 200.0)

    def test_holstering_or_dying_before_impact_cancels_damage(self):
        for cause in ("holster", "death"):
            with self.subTest(cause=cause):
                run, shooter, victim, vitals = self.arena()
                self.swing(run, shooter, 5)
                if cause == "holster":
                    run.frame += 1
                    shooter.take_commands([Command(run.frame, action=1)])
                else:
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is shooter)
                    fight._damage(victim, target, 500.0, False, None, run.frame)
                for _ in range(35):
                    run.step({})
                self.assertEqual(vitals.total, 200.0)

    def test_late_contact_inside_authored_window_hits_after_fire_release(self):
        run, shooter, victim, hp = self.arena(5.0)
        self.swing(run, shooter, 20)
        self.assertEqual(hp.total, 200.0)
        x, y, z = shooter.mover.position
        victim.mover.position = [x, y, z + 1.5]
        for _ in range(4):
            run.step({})  # A committed swing retains its window after release.
        self.assertEqual(hp.total, 145.0)
        self.assertEqual(run.match.combat.hammer_contacts[shooter.body].hit, {victim.body})

    def test_targets_entering_after_the_window_ends_are_not_hit(self):
        for boosted, end in ((False, 33), (True, 23)):
            with self.subTest(boosted=boosted):
                run, shooter, victim, hp = self.arena(5.0)
                if boosted:
                    run.step({shooter: (0, 0, 16)})
                    for _ in range(45):
                        run.step({})
                self.swing(run, shooter, end)
                x, y, z = shooter.mover.position
                victim.mover.position = [x, y, z + 1.5]
                self.swing(run, shooter, 5)
                self.assertEqual(hp.total, 200.0)

    def test_one_window_hits_distinct_late_targets_without_reentry_duplicates(self):
        run = Run(KANEZAKA_DM, names=("Hammer", "First", "Late"))
        owner, first, late = run.players
        run.match.switch_hero(owner, heroes()[hero_torbjorn.HERO])
        x, y, z = owner.mover.position
        first.mover.position = [x, y, z + 1.5]
        late.mover.position = [x + 5, y, z]
        for _ in range(35):
            run.step({})
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        for _ in range(45):
            run.step({})
        self.swing(run, owner, 20)
        fight = run.match.combat
        self.assertEqual(fight.vitals[first.body].total, 145)
        self.assertEqual(fight.vitals[late.body].total, 200)
        first.mover.position = [x + 5, y, z]
        late.mover.position = [x, y, z + 1.5]
        self.swing(run, owner, 4)
        self.assertEqual(fight.vitals[late.body].total, 145)
        first.mover.position = [x, y, z + 1.5]
        self.swing(run, owner, 4)
        self.assertEqual(fight.vitals[first.body].total, 145)
        self.assertEqual(fight.vitals[late.body].total, 145)
        self.assertEqual(fight.hammer_contacts[owner.body].hit, {first.body, late.body})

    def test_turning_during_contact_window_can_hit_a_new_direction(self):
        run, shooter, _, hp = self.arena(-1.5)
        self.swing(run, shooter, 20)
        self.assertEqual(hp.total, 200)
        self.swing(run, shooter, 3, yaw=-32768)
        self.assertEqual(hp.total, 145)

    def test_holster_or_death_inside_the_window_prevents_late_contact(self):
        for cause in ("holster", "death"):
            with self.subTest(cause=cause):
                run, shooter, victim, hp = self.arena(5.0)
                self.swing(run, shooter, 20)
                if cause == "holster":
                    run.frame += 1
                    shooter.take_commands([Command(run.frame, action=1)])
                else:
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is shooter)
                    fight._damage(victim, target, 1000, False, None, run.frame)
                x, y, z = shooter.mover.position
                victim.mover.position = [x, y, z + 1.5]
                for _ in range(5):
                    run.step({})
                self.assertEqual(hp.total, 200)
                self.assertNotIn(shooter.body, run.match.combat.hammer_contacts)

    def test_overload_expiry_during_swing_does_not_move_its_selected_impact(self):
        run, shooter, _, hp = self.arena()
        run.step({shooter: (0, 0, 16)})
        for _ in range(305):
            run.step({})
        overload = next(i for i in shooter.body_script.component.instances.values()
                        if i.graph.index == 0x1721)
        self.assertTrue(overload.states[3].active)
        self.swing(run, shooter, 9)
        self.assertEqual(hp.total, 200)
        self.assertFalse(overload.states[3].active)
        self.swing(run, shooter, 1)
        self.assertEqual(hp.total, 145)

    def test_contact_targets_and_cancel_flag_are_in_passive_snapshot(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        owner, victim = players
        fight = owner.match.combat
        plan = hero_torbjorn.HammerContacts(12, 1, 1000, 1200)
        fight.hammer_contacts[owner.body] = plan
        first = snapshot_digest(case.capture(session))
        plan.hit.add(victim.body)
        second = snapshot_digest(case.capture(session))
        self.assertNotEqual(first, second)
        plan.cancelled = True
        self.assertNotEqual(second, snapshot_digest(case.capture(session)))
