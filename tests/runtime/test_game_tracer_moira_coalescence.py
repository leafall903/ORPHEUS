"""Actual Coalescence commands and Tracer interactions."""

from dataclasses import replace

import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box
from test_game_tracer_moira import MoiraArena

from ow174.game import combat, moira, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class TracerMoiraCoalescenceTests(MoiraArena):
    def setUp(self):
        super().setUp()
        self.script = self.moira.body_script
        self.script.ult_seconds = 0
        self.beam = next(i for i in self.script.component.instances.values()
                         if i.graph.index == moira.COALESCENCE)

    def launch(self):
        self.script.set_ult_charge(self.script.component.entity_var(ULT_COST).value())
        self.cast(buttons=32)
        self.assertEqual(self.script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(60):
            if self.beam.state(20).active:
                return
            self.run.step({})
        self.fail("Authored Coalescence did not enter its effect phase")

    def test_enemy_and_self_rates_then_natural_end_and_fresh_reuse(self):
        self.health.most[combat.HEALTH_POOL] = self.health.now[combat.HEALTH_POOL] = 1600
        self.moira_health.damage(100)
        self.cast(buttons=32)
        for _ in range(25):
            self.run.step({})
        self.assertFalse(self.beam.state(20).active)
        self.assertEqual(self.health.total, 1600)
        self.launch()
        enemy, source = self.health.total, self.moira_health.total
        for _ in range(20):
            self.run.step({})
        self.assertAlmostEqual(enemy - self.health.total, 22.4, places=4)
        self.assertAlmostEqual(self.moira_health.total - source, 16, places=4)
        for _ in range(520):
            self.cast(buttons=32)
            if not self.beam.state(20).active:
                break
        self.assertFalse(self.beam.state(20).active)
        after = self.health.total
        for _ in range(25):
            self.cast(buttons=32)
        self.assertEqual(self.health.total, after)
        self.assertEqual(self.script.component.entity_var(ULT_CHARGE).value(), 0)
        self.run.step({})
        self.launch()
        self.assertLess(self.health.total, after)

    def test_allied_tracer_heals_while_beam_pierces_to_enemy(self):
        enemy, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.moira.team = self.tracer.team = 0
        enemy.team = 1
        x, y, z = self.moira.mover.position
        self.place(enemy, (x, y, z + 6))
        self.health.damage(100)
        self.launch()
        friendly, hostile = self.health.total, self.vitals(enemy).total
        for _ in range(20):
            self.run.step({})
        self.assertAlmostEqual(self.health.total - friendly, 44.8, places=4)
        self.assertAlmostEqual(hostile - self.vitals(enemy).total, 22.4, places=4)
        self.assertEqual(self.beam.vars[1184].value(), (expr.Entity(self.tracer.body),))
        self.assertEqual(self.beam.vars[1182].value(), (expr.Entity(enemy.body),))
        self.assertEqual(self.beam.state(17).targets, (self.tracer.body,))
        self.assertEqual(self.beam.state(19).targets, (enemy.body,))

    def test_world_cover_and_range_exclude_enemy_tracer(self):
        for gate in ("wall", "range"):
            with self.subTest(gate=gate):
                self.setUp()
                x, y, z = self.moira.mover.position
                if gate == "wall":
                    world = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                  + box(x - 4, x + 4, y, y + 4, z + 1, z + 1.2))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for actor in self.run.players:
                        actor.mover.use(world=world)
                else:
                    self.place(self.tracer, (x, y, z + 35))
                    self.run.step({})
                self.launch()
                for _ in range(20):
                    self.run.step({})
                self.assertEqual(self.health.total, 150)

    def test_blink_and_recall_leave_beam_and_damage_resumes(self):
        self.launch()
        self.run.step({self.tracer: (16384, 0, 8)})
        displaced = self.health.total
        for _ in range(16):
            self.run.step({})
        self.assertEqual(self.health.total, displaced)
        self.run.step({self.tracer: (-16384, 0, 8)})
        self.run.step({})
        self.assertLess(self.health.total, displaced)
        self.cast(tracer_buttons=16, buttons=0)
        self.assertTrue(tracer.recalling(self.tracer))
        protected = self.health.total
        for _ in range(95):
            if not tracer.recalling(self.tracer):
                break
            self.assertEqual(self.health.total, protected)
            self.run.step({})
        self.assertFalse(tracer.recalling(self.tracer))
        after = self.health.total
        self.run.step({})
        self.assertLess(self.health.total, after)

    def test_tracer_lethal_fire_stops_active_beam(self):
        self.launch()
        self.moira_health.now[combat.HEALTH_POOL] = 6
        x, y, z = self.moira.mover.position
        facing = aim(self.fight._eye(self.tracer), (x, y + 1, z))
        before = self.health.total
        self.run.step({self.tracer: (*facing, FIRE)})
        self.assertTrue(self.moira_health.dead)
        self.assertEqual(self.moira_health.killer, self.tracer.body)
        for _ in range(40):
            self.run.step({})
        self.assertEqual(self.health.total, before)


    def test_beam_crosses_live_winston_barrier_without_spending_it(self):
        winston, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000009)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        winston.team = self.tracer.team = 0
        self.moira.team = 1
        x, y, z = self.moira.mover.position
        self.place(self.tracer, (x, y, z + 6))
        self.place(winston, (x + 1, y, z + 6))
        for _ in range(10):
            self.run.step({})
        self.run.step({winston: (0, 0, 16)})
        for _ in range(5):
            self.run.step({})
        bubble = self.fight.winston_barriers.actors[winston.body]
        self.launch()
        before = self.health.total
        for _ in range(20):
            self.run.step({})
        self.assertAlmostEqual(before - self.health.total, 22.4, places=4)
        self.assertEqual(bubble.health, 700)

    def test_flashbang_interrupts_active_beam_without_automatic_recast(self):
        caster, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000042)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        caster.team = self.tracer.team = 0
        self.moira.team = 1
        x, y, z = self.moira.mover.position
        self.place(caster, (x + 2, y, z))
        for _ in range(10):
            self.run.step({})
        self.launch()
        facing = aim(self.fight._eye(caster), (x, y + 1, z))
        self.run.step({caster: (*facing, 16)})
        for _ in range(45):
            if self.fight.is_stunned(self.moira):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(self.moira))
        self.assertFalse(self.beam.state(20).active)
        stopped = self.health.total
        for _ in range(80):
            self.cast(buttons=32)
        self.assertEqual(self.health.total, stopped)
        self.assertEqual(self.script.component.entity_var(ULT_CHARGE).value(), 0)


    def test_active_deflect_does_not_redirect_beam_away_from_tracer(self):
        genji, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000029)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        genji.team = self.tracer.team = 0
        self.moira.team = 1
        x, y, z = self.moira.mover.position
        self.place(genji, (x, y, z + 2))
        self.place(self.tracer, (x, y, z + 5))
        for _ in range(8):
            self.run.step({})
        self.launch()
        self.run.step({genji: (-32768, 0, 16)})
        guard = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(guard.state(10).active)
        before = self.health.total, self.vitals(genji).total, self.moira_health.total
        for _ in range(15):
            self.run.step({genji: (-32768, 0, 0)})
        self.assertTrue(guard.state(10).active)
        self.assertAlmostEqual(before[0] - self.health.total, 16.8, places=4)
        self.assertAlmostEqual(before[1] - self.vitals(genji).total, 16.8, places=4)
        self.assertEqual(self.moira_health.total, before[2])
