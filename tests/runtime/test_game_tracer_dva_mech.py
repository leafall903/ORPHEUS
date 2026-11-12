"""Selected authored D.Va 0407 combat against Tracer; transitions are separate."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import FLOOR, wall

from ow174.game import combat, pools, stats
from ow174.game import tracer as tracer_ability
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph

MECH = 0x0400000000000407


class TracerDvaMechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if graph.entity_body(MECH) is None or defined_mover_data(MECH) is None:
            raise unittest.SkipTest("requires explicit build104319 0407 graph and mover inputs")

    def scenario(self, distance=2.0, *, cover=False, ally=False):
        run = Run(KANEZAKA_DM, ("D.Va", "Tracer"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        mech, tracer = run.players
        self.assertTrue(match.switch_hero(mech, replace(find_hero("dva"), body=MECH)))
        self.assertTrue(match.switch_hero(tracer, find_hero("tracer")))
        self.assertEqual(mech.mover.data.stand_radius, defined_mover_data(MECH).stand_radius)
        world = World(FLOOR + (wall(-8, 1, 8, 1) if cover else []))
        match.collision_provider = StaticCollisionProvider(world)
        match.collision_world = world
        for player, z in ((mech, 0.0), (tracer, distance)):
            player.mover.world = world
            player.mover.position = [0.0, 1.0, z]
            player.mover.body = None
        if ally:
            match.game_map = replace(match.game_map, free_for_all=False)
            tracer.team = mech.team
        for _ in range(100):
            run.step({})
        self.assertTrue(any(i.graph.index == 0x050A for i in mech.body_script.component.instances.values()))
        return run, mech, tracer

    @staticmethod
    def vitals(run, owner):
        fight = run.match.combat
        target = next(body for body in fight._targets() if body.owner is owner)
        return fight.vitals_of(target)

    @staticmethod
    def fire(run, shooter, height, target, *, yaw_override=None):
        x, y, z = target.mover.position
        yaw, pitch = aim(run.match.combat._eye(shooter), (x, y + height, z))
        run.step({shooter: (yaw if yaw_override is None else yaw_override, pitch, FIRE)})

    def test_mech_record_cannot_replace_an_unrelated_hero(self):
        run, _, tracer = self.scenario()
        original_body, original_mover, original_script = tracer.body, tracer.mover, tracer.body_script
        self.assertFalse(run.match.switch_hero(tracer, replace(tracer.hero, body=MECH)))
        self.assertEqual(tracer.body, original_body)
        self.assertIs(tracer.mover, original_mover)
        self.assertIs(tracer.body_script, original_script)

    def test_actual_fusion_cannons_damage_tracer_as_eleven_pellets(self):
        run, mech, tracer = self.scenario()
        self.fire(run, mech, 0.75, tracer)
        self.assertEqual(self.vitals(run, tracer).total, 128)
        self.assertEqual(mech.stats.value(stats.HERO_DAMAGE), 22)
        hits = mech.client.messages(combat.HIT)
        self.assertEqual(len(hits), 11)
        self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == tracer.body for hit in hits))
        for _ in range(20):
            run.step({})
        self.assertEqual(self.vitals(run, tracer).total, 128)
        self.fire(run, mech, 0.75, tracer)
        self.assertEqual(self.vitals(run, tracer).total, 106)

    def test_actual_tracer_pair_is_mitigated_by_mech_armour_per_bullet(self):
        run, mech, tracer = self.scenario()
        self.assertEqual(pools.active_pools(mech.hero.guid, mech.hero.body), (300, 300, 0))
        vitals = self.vitals(run, mech)
        self.assertEqual(vitals.total, 600)
        self.fire(run, tracer, 1.0, mech)
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 294)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)
        self.assertEqual(tracer.stats.value(stats.HERO_DAMAGE), 6)
        self.assertEqual(len(tracer.client.messages(combat.HIT)), 2)

    def test_fusion_cannons_respect_aim_cover_range_and_team(self):
        for case in ("miss", "cover", "range", "ally"):
            with self.subTest(case=case):
                run, mech, tracer = self.scenario(
                    105.0 if case == "range" else 2.0, cover=case == "cover", ally=case == "ally",
                )
                fight = run.match.combat
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    self.fire(run, mech, 0.75, tracer, yaw_override=16384 if case == "miss" else None)
                shot, = shoot.call_args_list
                self.assertEqual(shot.args[2].instance.graph.index, 0x050A)
                self.assertEqual(self.vitals(run, tracer).total, 150)
                self.assertEqual(mech.client.messages(combat.HIT), [])


    def test_actual_recall_rejects_a_volley_and_fire_resumes_after_playback(self):
        run, mech, tracer = self.scenario()
        run.step({tracer: (0, 0, 16)})
        self.assertTrue(tracer_ability.recalling(tracer))
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            self.fire(run, mech, 0.75, tracer)
        shot, = shoot.call_args_list
        self.assertEqual(shot.args[2].instance.graph.index, 0x050A)
        self.assertEqual(self.vitals(run, tracer).total, 150)
        for _ in range(105):
            run.step({})
        self.assertFalse(tracer_ability.recalling(tracer))
        self.fire(run, mech, 0.75, tracer)
        self.assertEqual(self.vitals(run, tracer).total, 128)

    def test_micro_missiles_burst_cooldown_and_reuse_against_tracer(self):
        run, mech, tracer = self.scenario()
        yaw, pitch = aim(run.match.combat._eye(mech), (0.0, 1.75, 2.0))

        def step(frames, buttons=0):
            for _ in range(frames):
                run.step({mech: (yaw, pitch, buttons)})

        vitals = self.vitals(run, tracer)
        step(160, 16)
        self.assertEqual(vitals.total, 24)
        self.assertEqual(len(mech.client.messages(combat.HIT)), 36)
        script = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x1128)
        self.assertGreater(script.vars[61].value(), 7)
        step(1)
        step(30, 16)
        self.assertEqual(vitals.total, 24)
        step(500)
        self.assertEqual(script.vars[61].value(), 0)
        step(160, 16)
        self.assertTrue(vitals.dead)
        self.assertEqual(len(mech.client.messages(combat.KILL_NOTICE)), 1)
