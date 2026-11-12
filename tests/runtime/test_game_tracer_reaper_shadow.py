"""Shadow Step through actual Reaper and Tracer body commands."""

import math
from dataclasses import replace

import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box
from test_game_tracer_reaper import ReaperArena

from ow174.game import body_protection, combat, reaper
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.script.driver import ULT_COST


class TracerReaperShadowTests(ReaperArena):
    def setUp(self):
        super().setUp()
        self.shadow = next(i for i in self.reaper.body_script.component.instances.values()
                           if i.graph.index == reaper.SHADOW_GRAPH)
        self.start = tuple(self.reaper.mover.position)

    def select(self, *, pitch=5000):
        self.run.step({self.reaper: (0, pitch, 16)})
        self.run.step({self.reaper: (0, pitch, 0)})
        return self.fight.reaper_shadows.get(self.reaper.body)

    def confirm(self):
        self.run.step({self.reaper: (0, 5000, FIRE)})

    def hit_reaper(self):
        self.run.step({self.tracer: (*self.facing(self.tracer, self.reaper), FIRE)})

    def test_tracer_hits_before_phase_and_after_locked_teleport(self):
        selected = self.select()
        self.assertIsNotNone(selected.point)
        self.assertLess(math.dist(self.start, selected.point), 35)
        self.hit_reaper()
        self.assertEqual(self.reaper_health.total, 238)
        self.confirm()
        destination = selected.point
        self.assertIsNotNone(selected.cast)
        for _ in range(70):
            if body_protection.active(self.reaper):
                break
            self.run.step({self.reaper: (16000, -4000, 0)})
        self.assertEqual(body_protection.active(self.reaper), "phase")
        self.assertEqual(tuple(self.reaper.mover.position), self.start)
        self.hit_reaper()
        self.assertEqual(self.reaper_health.total, 238)
        for _ in range(45):
            self.run.step({self.reaper: (16000, -4000, 0)})
            if self.reaper.body not in self.fight.reaper_shadows:
                break
        self.assertLess(math.dist(self.reaper.mover.position, destination), 0.002)
        self.assertIsNone(body_protection.active(self.reaper))
        self.hit_reaper()
        self.assertEqual(self.reaper_health.total, 226)

    def test_tracer_kills_reaper_before_phase_and_prevents_teleport(self):
        record = self.select()
        self.confirm()
        self.assertIsNotNone(record.cast)
        self.assertIsNone(body_protection.active(self.reaper))
        self.reaper_health.now[combat.HEALTH_POOL] = 6
        self.hit_reaper()
        self.assertTrue(self.reaper_health.dead)
        self.assertEqual(self.reaper_health.killer, self.tracer.body)
        self.assertNotIn(self.reaper.body, self.fight.reaper_shadows)
        for _ in range(110):
            self.run.step({})
        self.assertEqual(tuple(self.reaper.mover.position), self.start)

    def test_cancel_preserves_cooldown_and_can_reopen(self):
        record = self.select()
        self.assertIsNotNone(record.point)
        self.run.step({self.reaper: (0, 5000, 2)})
        self.assertNotIn(self.reaper.body, self.fight.reaper_shadows)
        self.assertEqual(self.shadow.vars[61].value(), 0)
        self.run.step({})
        self.assertIsNotNone(self.select().point)

    def test_destination_occupied_during_cast_rejects_teleport(self):
        record = self.select()
        self.assertIsNotNone(record.point)
        self.confirm()
        self.assertIsNotNone(record.cast)
        self.place(self.tracer, record.point)
        for _ in range(155):
            self.run.step({})
        self.assertNotIn(self.reaper.body, self.fight.reaper_shadows)
        self.assertEqual(tuple(self.reaper.mover.position), self.start)
        self.assertGreater(self.shadow.vars[61].value(), 0)

    def test_cooldown_rejects_held_retry_and_fresh_press_reopens_after_expiry(self):
        record = self.select()
        self.assertIsNotNone(record.point)
        self.confirm()
        self.assertIsNotNone(record.cast)
        for _ in range(155):
            self.run.step({})
        self.assertGreater(self.shadow.vars[61].value(), 9)
        counter = self.shadow.state(0).counter
        for _ in range(700):
            self.run.step({self.reaper: (0, 5000, 16)})
            if self.shadow.vars[61].value() <= 0:
                break
        self.assertEqual(self.shadow.vars[61].value(), 0)
        self.assertEqual(self.shadow.state(0).counter, counter)
        self.assertNotIn(self.reaper.body, self.fight.reaper_shadows)
        self.run.step({})
        self.assertIsNotNone(self.select().point)

    def test_actual_pulse_passes_through_shadow_phase_and_sticks_behind(self):
        other, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer)
        x, y, z = self.start
        self.place(other, (x, y, z - 2))
        record = self.select()
        self.confirm()
        self.assertIsNotNone(record.cast)
        for _ in range(75):
            self.run.step({})
            if body_protection.active(self.reaper):
                break
        self.assertEqual(body_protection.active(self.reaper), "phase")
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        facing = aim(self.fight._eye(self.tracer), (x, y + 0.6, z - 2))
        self.run.step({self.tracer: (*facing, 32)})
        for _ in range(35):
            for bomb in self.fight.pulse_bombs.bombs:
                self.assertNotEqual(bomb.attached, self.reaper.body)
                if bomb.attached == other.body:
                    self.assertEqual(body_protection.active(self.reaper), "phase")
                    self.assertEqual(self.reaper_health.total, 250)
                    self.assertEqual(self.vitals(other).total, 195)
                    return
            self.run.step({self.tracer: (*facing, 0)})
        self.fail("Pulse Bomb did not reach the player behind the phased Reaper")

    def test_flashbang_aborts_confirmed_cast_before_phase(self):
        caster, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000042)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        caster.team = self.tracer.team = 0
        self.reaper.team = 1
        x, y, z = self.start
        self.place(caster, (x + 2, y, z))
        self.run.step({})
        record = self.select()
        self.confirm()
        self.assertIsNotNone(record.cast)
        self.run.step({caster: (*self.facing(caster, self.reaper), 16)})
        for _ in range(45):
            if self.fight.is_stunned(self.reaper):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(self.reaper))
        self.assertFalse(self.shadow.state(32).active)
        self.assertNotIn(self.reaper.body, self.fight.reaper_shadows)
        for _ in range(110):
            self.run.step({})
        self.assertEqual(tuple(self.reaper.mover.position), self.start)

    def test_nearer_vertical_wall_refuses_confirmation(self):
        x, y, z = self.start
        world = World(box(x - 10, x + 10, y - 2, y, z - 10, z + 10)
                      + box(x - 4, x + 4, y, y + 4, z + 0.6, z + 0.7))
        self.match.collision_provider = StaticCollisionProvider(world)
        self.match.collision_world = world
        for actor in self.run.players:
            actor.mover.use(world=world)
        record = self.select()
        self.assertIsNone(record.point)
        self.confirm()
        self.assertIsNone(record.cast)
        self.assertFalse(self.shadow.vars[983].value())
        self.assertEqual(tuple(self.reaper.mover.position), self.start)
