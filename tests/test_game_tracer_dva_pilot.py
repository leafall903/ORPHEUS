"""Actual pilot Light Gun input against Tracer's selected gameplay body."""

import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import FLOOR, wall

from ow174.game import combat, stats
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.content import find_hero


class TracerDvaPilotTests(unittest.TestCase):
    def scenario(self, distance=5, *, cover=False, ally=False):
        run = Run(KANEZAKA_DM, ("D.Va", "Tracer"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        pilot, tracer = run.players
        self.assertTrue(match.switch_hero(pilot, find_hero("dva")))
        self.assertTrue(match.switch_hero(tracer, find_hero("tracer")))
        self.assertEqual(pilot.hero.body, 0x040000000000040A)
        world = World(FLOOR + (wall(-8, 2, 8, 2) if cover else []))
        match.collision_provider = StaticCollisionProvider(world)
        match.collision_world = world
        for player, z in ((pilot, 0.0), (tracer, distance)):
            player.mover.world = world
            player.mover.position = [0.0, 1.0, z]
            player.mover.body = None
        if ally:
            match.game_map = replace(match.game_map, free_for_all=False)
            tracer.team = pilot.team
        run.step({})
        fight = match.combat
        target = next(body for body in fight._targets() if body.owner is tracer)
        yaw, pitch = aim(fight._eye(pilot), (0.0, 1.75, distance))
        return run, pilot, target, yaw, pitch

    def step(self, run, pilot, yaw, pitch, count, buttons=0):
        for _ in range(count):
            run.step({pilot: (yaw, pitch, buttons)})

    def test_light_gun_travels_and_applies_attributed_damage_to_tracer(self):
        run, pilot, target, yaw, pitch = self.scenario()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        self.step(run, pilot, yaw, pitch, 1, FIRE)
        self.assertEqual(vitals.total, 150)
        self.assertEqual(len(fight.rockets), 1)
        self.step(run, pilot, yaw, pitch, 10)
        self.assertEqual(vitals.total, 136)
        self.assertEqual(pilot.stats.value(stats.HERO_DAMAGE), 14)
        notice, = pilot.client.messages(combat.HIT)
        self.assertEqual(notice["+0x78"]["+0x0"]["+0x0"], target.entity)
        weapon = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x02B8)
        self.assertEqual(weapon.vars[53].value(), 19)

    def test_light_gun_rejects_miss_cover_range_and_ally(self):
        for case in ("miss", "cover", "range", "ally"):
            with self.subTest(case=case):
                run, pilot, target, yaw, pitch = self.scenario(
                    105 if case == "range" else 5, cover=case == "cover", ally=case == "ally",
                )
                self.step(run, pilot, 16384 if case == "miss" else yaw, pitch, 1, FIRE)
                self.assertEqual(len(run.match.combat.rockets), 1)
                self.step(run, pilot, yaw, pitch, 130 if case == "range" else 12)
                self.assertEqual(run.match.combat.vitals_of(target).total, 150)
                self.assertEqual(pilot.client.messages(combat.HIT), [])
                if case in ("cover", "range"):
                    self.assertFalse(run.match.combat.rockets)
                else:
                    self.assertEqual(len(run.match.combat.rockets), 1)
