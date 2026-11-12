"""Actual Hellfire Shotgun and Reaping interactions with Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, reaper, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class ReaperArena(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.reaper, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.reaper, heroes()[reaper.HERO]))
        x, y, z = self.reaper.mover.position
        self.place(self.tracer, (x, y, z + 1.5))
        for _ in range(35):
            self.run.step({})
        self.health = self.vitals(self.tracer)
        self.reaper_health = self.vitals(self.reaper)
        self.weapon = next(i for i in self.reaper.body_script.component.instances.values()
                           if i.graph.index == reaper.SHOTGUN_GRAPH)

    @staticmethod
    def place(actor, position):
        actor.mover.position[:] = position
        actor.mover.body = None

    def vitals(self, actor):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is actor))

    def facing(self, source, target):
        x, y, z = target.mover.position
        return aim(self.fight._eye(source), (x, y + 1, z))

    def fire(self, *, angles=None, recall=False):
        angles = self.facing(self.reaper, self.tracer) if angles is None else angles
        self.run.step({self.tracer: (0, 0, 16 if recall else 0), self.reaper: (*angles, FIRE)})

    def hits(self):
        return [hit["+0x78"]["+0x4"] for hit in self.reaper.client.messages(combat.HIT)]

class TracerReaperShotgunTests(ReaperArena):
    def test_twenty_pellets_spend_one_shell_and_heal_from_actual_pool_loss(self):
        self.reaper_health.damage(100)
        self.run.step({self.tracer: (*self.facing(self.tracer, self.reaper), FIRE)})
        before = self.reaper_health.total
        self.assertEqual(before, 138)
        self.fire()
        self.assertEqual(self.hits(), [6] * 20)
        self.assertEqual(self.weapon.vars[53].value(), 7)
        self.assertEqual(self.health.total, 30)
        self.assertAlmostEqual(self.reaper_health.total, before + 42, places=5)

    def test_both_quick_melees_hit_and_reaper_heals_once(self):
        self.reaper_health.damage(100)
        self.run.step({self.tracer: (*self.facing(self.tracer, self.reaper), 0x800),
                       self.reaper: (*self.facing(self.reaper, self.tracer), 0x800)})
        for _ in range(45):
            self.run.step({})
        self.assertEqual(self.health.total, 120)
        self.assertAlmostEqual(self.reaper_health.total, 130.5, places=5)
        self.assertEqual(self.hits(), [30])
        self.assertEqual(self.weapon.vars[53].value(), 8)

    def test_off_axis_pellets_hit_when_the_centre_ray_misses(self):
        x, y, z = self.reaper.mover.position
        self.place(self.tracer, (x + 0.9, y, z + 10))
        self.run.step({})
        self.fire(angles=aim(self.fight._eye(self.reaper), (x, y + 1, z + 10)))
        self.assertGreater(len(self.hits()), 0)
        self.assertLess(len(self.hits()), 20)
        self.assertLess(self.health.total, 150)

    def test_miss_wall_range_and_allied_body_do_not_reap(self):
        for gate in ("miss", "wall", "range", "ally"):
            with self.subTest(gate=gate):
                self.setUp()
                self.reaper_health.damage(100)
                x, y, z = self.reaper.mover.position
                angles = None
                if gate == "wall":
                    world = World(box(x - 10, x + 10, y - 2, y, z - 10, z + 10)
                                  + box(x - 4, x + 4, y, y + 4, z + 0.6, z + 0.7))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for actor in self.run.players:
                        actor.mover.use(world=world)
                elif gate == "range":
                    self.place(self.tracer, (x, y, z + 102))
                    self.run.step({})
                elif gate == "ally":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.tracer.team = self.reaper.team
                else:
                    angles = (16384, 0)
                self.fire(angles=angles)
                self.assertEqual(self.weapon.vars[53].value(), 7)
                self.assertEqual(self.health.total, 150)
                self.assertEqual(self.reaper_health.total, 150)
                self.assertFalse(self.hits())

    def test_partial_wall_blocks_only_intersecting_pellets_and_their_healing(self):
        self.reaper_health.damage(100)
        x, y, z = self.reaper.mover.position
        world = World(box(x - 10, x + 10, y - 2, y, z - 10, z + 10)
                      + box(x + 0.01, x + 4, y, y + 4, z + 0.6, z + 0.7))
        self.match.collision_provider = StaticCollisionProvider(world)
        self.match.collision_world = world
        for actor in self.run.players:
            actor.mover.use(world=world)
        self.fire()
        hits = self.hits()
        self.assertGreater(len(hits), 0)
        self.assertLess(len(hits), 20)
        self.assertEqual(hits, [6] * len(hits))
        self.assertEqual(150 - self.health.total, sum(hits))
        self.assertAlmostEqual(self.reaper_health.total, 150 + sum(hits) * reaper.REAPING_RATIO)

    def test_manual_reload_refills_and_held_fire_resumes_against_tracer(self):
        self.health.most[combat.HEALTH_POOL] = self.health.now[combat.HEALTH_POOL] = 1000
        self.fire()
        self.assertEqual(self.health.total, 880)
        self.run.step({self.reaper: (*self.facing(self.reaper, self.tracer), 0x400)})
        self.assertEqual(self.weapon.vars[53].value(), 7)
        for _ in range(100):
            self.fire()
            self.assertEqual(self.health.total, 880)
            if self.weapon.vars[53].value() == 8:
                break
        self.assertEqual(self.weapon.vars[53].value(), 8)
        for _ in range(90):
            self.fire()
            if self.health.total < 880:
                break
        self.assertEqual(self.weapon.vars[53].value(), 7)
        self.assertEqual(self.health.total, 760)

    def test_lethal_pellet_only_reaps_tracers_remaining_health(self):
        self.reaper_health.damage(100)
        self.health.now[combat.HEALTH_POOL] = 5
        self.fire()
        self.assertTrue(self.health.dead)
        self.assertEqual(self.health.killer, self.reaper.body)
        self.assertEqual(self.hits(), [5])
        self.assertAlmostEqual(self.reaper_health.total, 151.75, places=5)

    def test_blink_leaves_shotgun_line_and_return_blink_allows_new_hit(self):
        facing = self.facing(self.reaper, self.tracer)
        x = self.tracer.mover.position[0]
        self.run.step({self.tracer: (16384, 0, 8)})
        self.fire(angles=facing)
        self.assertAlmostEqual(self.tracer.mover.position[0] - x, 7.5, places=2)
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.weapon.vars[53].value(), 7)
        self.assertFalse(self.hits())
        for _ in range(40):
            self.run.step({})
        self.run.step({self.tracer: (-16384, 0, 8)})
        self.fire(angles=facing)
        self.assertAlmostEqual(self.tracer.mover.position[0], x, places=2)
        self.assertEqual(self.health.total, 30)
        self.assertEqual(self.weapon.vars[53].value(), 6)

    def test_recall_avoids_shell_and_restores_health_before_normal_fire_resumes(self):
        self.reaper_health.damage(100)
        self.fire()
        self.assertEqual(self.health.total, 30)
        for _ in range(40):
            self.run.step({})
        self.fire(recall=True)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertEqual(self.weapon.vars[53].value(), 6)
        self.assertEqual(self.hits(), [6] * 20)
        self.assertAlmostEqual(self.reaper_health.total, 192, places=5)
        for _ in range(95):
            self.run.step({})
            if not tracer.recalling(self.tracer):
                break
        self.assertEqual(self.health.total, 150)
        self.fire()
        self.assertEqual(self.weapon.vars[53].value(), 5)
        self.assertEqual(self.health.total, 30)
        self.assertEqual(self.hits(), [6] * 40)


class TracerReaperBlossomTests(ReaperArena):
    def cast(self, *, charge=True, recall=False):
        script = self.reaper.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value() if charge else 0)
        self.run.step({self.tracer: (0, 0, 16 if recall else 0), self.reaper: (0, 0, 32)})
        return script

    def test_full_channel_stops_after_fifteen_hits_and_rejects_uncharged_cast(self):
        self.health.most[combat.HEALTH_POOL] = self.health.now[combat.HEALTH_POOL] = 1000
        self.reaper_health.damage(200)
        script = self.cast(charge=False)
        self.assertIsNone(reaper.blossom_state(script))
        self.assertEqual(self.health.total, 1000)
        self.run.step({})
        self.cast()
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(self.health.total, 966)
        for _ in range(192):
            self.run.step({})
        self.assertIsNone(reaper.blossom_state(script))
        self.assertTrue(self.fight.reaper_blossoms[self.reaper.body].ended)
        self.assertEqual(self.hits(), [34] * 15)
        self.assertEqual(self.health.total, 490)
        self.assertAlmostEqual(self.reaper_health.total, 228.5, places=4)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_team_wall_and_range_exclude_tracer(self):
        for gate in ("team", "wall", "range"):
            with self.subTest(gate=gate):
                self.setUp()
                x, y, z = self.reaper.mover.position
                if gate == "team":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.tracer.team = self.reaper.team
                elif gate == "range":
                    self.place(self.tracer, (x, y, z + 10))
                    self.run.step({})
                else:
                    world = World(box(x - 10, x + 10, y - 2, y, z - 10, z + 10)
                                  + box(x - 4, x + 4, y, y + 4, z + 0.6, z + 0.7))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for actor in self.run.players:
                        actor.mover.use(world=world)
                self.cast()
                for _ in range(40):
                    self.run.step({})
                self.assertEqual(self.health.total, 150)
                self.assertFalse(self.hits())

    def test_recall_blocks_blossom_then_exposes_tracer_to_the_next_pulse(self):
        self.cast(recall=True)
        self.assertTrue(tracer.recalling(self.tracer))
        for _ in range(95):
            if not tracer.recalling(self.tracer):
                break
            self.assertEqual(self.health.total, 150)
            self.run.step({})
        self.assertFalse(tracer.recalling(self.tracer))
        for _ in range(18):
            if self.health.total < 150:
                break
            self.run.step({})
        self.assertEqual(self.health.total, 116)
        self.assertEqual(self.hits(), [34])

    def test_blink_leaves_radius_and_return_acquires_without_backfill(self):
        x, y, z = self.reaper.mover.position
        self.place(self.tracer, (x, y, z + 4.8))
        self.run.step({})
        self.cast()
        self.assertEqual(self.health.total, 116)
        self.run.step({self.tracer: (16384, 0, 8)})
        for _ in range(34):
            self.run.step({})
        self.assertEqual(self.health.total, 116)
        self.run.step({self.tracer: (-16384, 0, 8)})
        self.run.step({})
        self.assertEqual(self.health.total, 82)
        self.assertEqual(self.hits(), [34, 34])

    def test_channel_finishes_after_input_gap_without_replaying_hits(self):
        self.health.most[combat.HEALTH_POOL] = self.health.now[combat.HEALTH_POOL] = 1000
        self.cast()
        at = self.run.frame + 250
        self.match.update(at * combat.TICK, at)
        self.assertEqual(self.health.total, 490)
        self.assertEqual(self.hits(), [34] * 15)
        self.assertTrue(self.fight.reaper_blossoms[self.reaper.body].ended)
        self.match.update((at + 1) * combat.TICK, at + 1)
        self.assertEqual(self.health.total, 490)

    def test_winston_bubble_takes_breaking_pulse_once_before_next_body_hit(self):
        support, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000009)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        support.team = self.tracer.team = 0
        self.reaper.team = 1
        x, y, z = self.reaper.mover.position
        self.place(self.tracer, (x, y, z + 6))
        self.place(support, (x + 0.8, y, z + 6))
        for _ in range(10):
            self.run.step({})
        self.run.step({support: (0, 0, 16)})
        for _ in range(5):
            self.run.step({})
        bubble = self.fight.winston_barriers.actors[support.body]
        self.reaper_health.damage(100)
        self.cast()
        self.assertEqual(bubble.health, 666)
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.reaper_health.total, 150)
        bubble.damage(632)  # Set up a break on the next pulse.
        for _ in range(15):
            self.run.step({})
            if bubble.health == 0:
                break
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.health.total, 150)
        for _ in range(15):
            self.run.step({})
            if self.health.total < 150:
                break
        self.assertEqual(self.health.total, 116)

    def test_flashbang_finishes_the_authored_channel_and_stops_damage(self):
        caster, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000042)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        caster.team = self.tracer.team = 0
        self.reaper.team = 1
        x, y, z = self.reaper.mover.position
        self.place(caster, (x + 2, y, z))
        for _ in range(10):
            self.run.step({})
        script = self.cast()
        self.assertEqual(self.health.total, 116)
        self.run.step({caster: (*self.facing(caster, self.reaper), 16)})
        for _ in range(40):
            if self.fight.is_stunned(self.reaper):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(self.reaper))
        self.assertIsNone(reaper.blossom_state(script))
        self.assertNotIn(self.reaper.body, self.fight.reaper_blossoms)
        after = self.health.total
        for _ in range(80):
            self.run.step({})
        self.assertEqual(self.health.total, after)

    def test_tracer_pulse_kills_reaper_and_stops_the_active_channel(self):
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        x, y, z = self.tracer.mover.position
        self.place(self.reaper, (x, y, z + 3))
        self.run.step({})
        self.run.step({self.tracer: (0, 0, 32)})
        for _ in range(65):
            self.run.step({})
            bombs = self.fight.pulse_bombs.bombs
            if bombs and bombs[0].attached == self.reaper.body:
                break
        bomb, = self.fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, self.reaper.body)
        while bomb.fuse_left > 0.32:
            self.run.step({})
        self.cast()
        self.assertEqual(self.health.total, 116)
        for _ in range(35):
            self.run.step({})
            if self.reaper_health.dead:
                break
        self.assertTrue(self.reaper_health.dead)
        self.assertEqual(self.reaper_health.killer, self.tracer.body)
        self.assertNotIn(self.reaper.body, self.fight.reaper_blossoms)
        after = self.health.total
        for _ in range(50):
            self.run.step({})
        self.assertEqual(self.health.total, after)
