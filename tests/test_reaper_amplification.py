"""Actual Baptiste Matrix placement and Reaper ray crossing."""

import unittest

import test_game_reaper as reaper_tests
from test_game_combat import aim

from ow174.game import amplification, combat
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class ReaperAmplificationTests(unittest.TestCase):
    def scene(self, *, names=("Reaper", "Enemy", "Baptiste")):
        run, owner, victim, _ = reaper_tests.ReaperTests().scenario(
            team_mode=True, names=names,
        )
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[amplification.HERO]))
        owner.team = caster.team = 0
        victim.team = 1
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x + 2, y, z)
        caster.mover.body = None
        victim.mover.position[:] = (x, y, z + 4)
        victim.mover.body = None
        caster.body_script.ult_seconds = 0
        for _ in range(27):
            run.step({})
        fight = run.match.combat
        pool = combat.Vitals(victim.body, 1000)
        fight.vitals[victim.body] = pool
        return run, owner, victim, caster, pool

    def deploy(self, run, caster, *, charged=True, pitch=8192):
        component = caster.body_script.component
        cost = component.entity_var(ULT_COST).value()
        caster.body_script.set_ult_charge(cost if charged else cost - 1)
        run.step({caster: (0, pitch, 32)})
        for _ in range(7):
            run.step({caster: (0, pitch, 0)})
        run.step({caster: (0, pitch, 32)})
        run.step({caster: (0, pitch, 0)})
        run.step({caster: (0, pitch, 1)})
        for _ in range(30):
            run.step({})

    def window(self, run, caster):
        self.deploy(run, caster)
        window, = run.match.combat.amplification.windows.values()
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(window.expires - window.created, 625)
        return window

    def test_actual_window_doubles_shell_and_reaping_then_expires(self):
        run, owner, victim, caster, pool = self.scene()
        fight = run.match.combat
        self.deploy(run, caster, charged=False)
        self.assertFalse(fight.amplification.windows)
        window = self.window(run, caster)
        own = fight.vitals[owner.body]
        own.damage(100)
        reaper_tests.ReaperTests().fire(run, owner, victim)
        self.assertAlmostEqual(pool.total, 748)
        self.assertAlmostEqual(own.total, 238.2, places=4)
        run.frame = window.expires
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(fight.amplification.windows)
        reaper_tests.ReaperTests().fire(run, owner, victim)
        self.assertAlmostEqual(pool.total, 622)

    def test_actual_death_blossom_does_not_get_window_amplification(self):
        run, owner, _, caster, pool = self.scene()
        fight = run.match.combat
        self.window(run, caster)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertEqual(pool.total, 966)
        for _ in range(200):
            run.step({})
        self.assertEqual(pool.total, 490)
        self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])

    def test_actual_hostile_window_and_target_before_window_keep_base_damage(self):
        for mode in ("hostile", "before"):
            with self.subTest(mode=mode):
                run, owner, victim, caster, pool = self.scene()
                if mode == "hostile":
                    caster.team = victim.team
                window = self.window(run, caster)
                if mode == "before":
                    x, y, z = owner.mover.position
                    victim.mover.position[:] = (x, y, z + 1.25)
                    victim.mover.body = None
                    self.assertGreater(window.position[2], victim.mover.position[2])
                    for _ in range(20):
                        run.step({})
                reaper_tests.ReaperTests().fire(run, owner, victim)
                self.assertAlmostEqual(pool.total, 874 if mode == "hostile" else 880)

    def test_actual_matrix_amplifies_reverse_crossing_and_skips_outside_width(self):
        for reverse in (True, False):
            with self.subTest(reverse=reverse):
                run, owner, victim, caster, pool = self.scene()
                window = self.window(run, caster)
                x, y, z = window.position
                side = 0 if reverse else 7
                owner.mover.position[:] = (x + side, y, z + 2.5)
                victim.mover.position[:] = (x + side, y, z - 1.5)
                owner.mover.body = victim.mover.body = None
                for _ in range(30):
                    run.step({})
                reaper_tests.ReaperTests().fire(run, owner, victim)
                self.assertAlmostEqual(pool.total, 748 if reverse else 874)

    def test_actual_static_wall_before_window_stops_shell(self):
        run, owner, victim, caster, pool = self.scene()
        self.window(run, caster)
        x, y, z = owner.mover.position
        mid = z + 0.75
        run.match.collision_world = World([
            (x-30,y,z-30,x-30,y,z+30,x+30,y,z+30),
            (x-30,y,z-30,x+30,y,z+30,x+30,y,z-30),
            (x-5,y,mid,x+5,y,mid,x+5,y+5,mid),
            (x-5,y,mid,x+5,y+5,mid,x-5,y+5,mid),
        ], name="Amplification Matrix cover")
        reaper_tests.ReaperTests().fire(run, owner, victim)
        self.assertEqual(pool.total, 1000)

    def test_actual_helix_crosses_enemy_matrix_then_hits_reaper(self):
        run, owner, victim, caster, _ = self.scene()
        self.assertTrue(run.match.switch_hero(victim, heroes()[SOLDIER]))
        x, y, z = owner.mover.position
        victim.mover.position[:] = (x, y, z)
        owner.mover.position[:] = (x, y, z + 7)
        victim.mover.body = owner.mover.body = None
        victim.team = caster.team = 1
        owner.team = 0
        for _ in range(28):
            run.step({})
        fight = run.match.combat
        self.window(run, caster)
        own = fight.vitals[owner.body]
        angles = aim(fight._eye(victim), (x, y + 1, z + 7))
        run.step({victim: (*angles, 2)})
        rocket, = fight.rockets
        self.assertFalse(rocket.amplified)
        for _ in range(20):
            run.step({})
        self.assertTrue(rocket.amplified)
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (80, 160))
        self.assertAlmostEqual(own.total, 10)

    def test_actual_invalid_preview_retains_full_charge(self):
        run, _, _, caster, _ = self.scene()
        component = caster.body_script.component
        cost = component.entity_var(ULT_COST).value()
        self.deploy(run, caster, pitch=0)
        self.assertFalse(run.match.combat.amplification.windows)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)

    def test_actual_melee_across_window_keeps_30_damage(self):
        run, owner, victim, caster, pool = self.scene()
        window = self.window(run, caster)
        x, y, z = window.position
        owner.mover.position[:] = (x, y, z - 0.75)
        victim.mover.position[:] = (x, y, z + 0.75)
        owner.mover.body = victim.mover.body = None
        for _ in range(25):
            run.step({})
        run.step({owner: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(pool.total, 970)

    def test_actual_two_allied_windows_amplify_each_pellet_once(self):
        run, owner, victim, caster, pool = self.scene(names=("Reaper", "Enemy", "Bap", "Second Bap"))
        other = run.players[3]
        self.assertTrue(run.match.switch_hero(other, heroes()[amplification.HERO]))
        other.team = owner.team
        x, y, z = owner.mover.position
        other.mover.position[:] = (x - 2, y, z + 0.5)
        other.mover.body = None
        other.body_script.ult_seconds = 0
        for _ in range(25):
            run.step({})
        self.window(run, caster)
        self.deploy(run, other)
        self.assertEqual(len(run.match.combat.amplification.windows), 2)
        reaper_tests.ReaperTests().fire(run, owner, victim)
        self.assertEqual(pool.total, 748)

    def test_actual_helix_crossing_uses_window_deadline_during_skipped_ticks(self):
        for before in (True, False):
            with self.subTest(before=before):
                run, owner, victim, caster, _ = self.scene()
                self.assertTrue(run.match.switch_hero(victim, heroes()[SOLDIER]))
                x, y, z = owner.mover.position
                victim.mover.position[:] = (x, y, z)
                owner.mover.position[:] = (x, y, z + 7)
                victim.mover.body = owner.mover.body = None
                victim.team = caster.team = 1
                for _ in range(28):
                    run.step({})
                fight = run.match.combat
                window = self.window(run, caster)
                run.frame = window.expires - (7 if before else 2)
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                angles = aim(fight._eye(victim), (x, y + 1, z + 7))
                run.step({victim: (*angles, 2)})
                rocket, = fight.rockets
                run.step({})
                run.frame += 20
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(rocket.amplified, before)
                self.assertAlmostEqual(fight.vitals[owner.body].total, 10 if before else 130)

    def test_actual_baptiste_death_leaves_window_until_deadline(self):
        from ow174.game import reaper

        run, owner, victim, caster, _ = self.scene()
        position = tuple(victim.mover.position)
        self.assertTrue(run.match.switch_hero(victim, heroes()[reaper.HERO]))
        victim.mover.position[:] = position
        victim.mover.body = None
        for _ in range(25):
            run.step({})
        window = self.window(run, caster)
        fight = run.match.combat
        source = next(t for t in fight._targets() if t.owner is caster)
        caster_pool = fight.vitals_of(source)
        caster_pool.damage(caster_pool.total - 50)
        reaper_tests.ReaperTests().fire(run, victim, caster)
        self.assertTrue(caster_pool.dead)
        self.assertIn(window, fight.amplification.windows.values())
        pool = combat.Vitals(victim.body, 1000)
        fight.vitals[victim.body] = pool
        reaper_tests.ReaperTests().fire(run, owner, victim)
        self.assertEqual(pool.total, 748)
