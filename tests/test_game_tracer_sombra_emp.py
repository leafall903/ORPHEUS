"""Actual Sombra EMP and Tracer ability and projectile lifetimes."""

import os
import unittest
from dataclasses import replace

import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_sombra_hack as fixtures
from test_game_combat import aim
from test_game_mover import box
from test_game_tracer_hammer import REINHARDT
from test_game_tracer_zarya_barrier import ZARYA

from ow174.game import combat, tracer, zarya_barrier
from ow174.game.collision import World


class TracerSombraEmpTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.TracerSombraHackTests()
        self.case.setUp()
        self.run, self.fight = self.case.run, self.case.fight
        self.player, self.sombra, self.health = self.case.player, self.case.sombra, self.case.health
        self.script = self.sombra.body_script
        self.script.ult_seconds = 0
        self.cost = self.script.component.entity_var(338).value()

    def start(self, *, charge=None, buttons=0):
        self.script.set_ult_charge(self.cost if charge is None else charge)
        self.run.step({self.sombra: (*self.case.angles, 32), self.player: (0, 0, buttons)})

    def finish(self):
        for _ in range(40):
            self.run.step({})
            if self.health.hack_until:
                return

    def test_actual_emp_spends_charge_and_rejects_blink_until_expiry(self):
        self.start(charge=self.cost - 1)
        self.finish()
        self.assertEqual(self.script.component.entity_var(342).value(), self.cost - 1)
        self.assertEqual(self.health.hack_until, 0)
        self.start()
        self.assertEqual(self.script.component.entity_var(342).value(), 0)
        self.assertEqual(self.health.hack_until, 0)
        self.finish()
        self.assertEqual(self.health.hack_until - self.fight.tick, 313)
        blink = next(i for i in self.player.body_script.component.instances.values() if i.graph.index == 0x24)
        self.run.step({self.player: (16384, 0, 8)})
        self.assertEqual(blink.vars[11].value(), 3)
        while self.fight.tick + 1 < self.health.hack_until:
            self.run.step({})
        self.run.step({self.player: (16384, 0, 8)})
        self.assertEqual(blink.vars[11].value(), 2)

    def test_actual_emp_obeys_range_cover_and_team(self):
        for gate in ("range", "cover", "team"):
            with self.subTest(gate=gate):
                self.setUp()
                x, y, z = self.player.mover.position
                if gate == "range":
                    self.player.mover.position[2] -= 40
                    self.player.mover.body = None
                elif gate == "cover":
                    floor = [(-1000, y, -1000, -1000, y, 1000, 1000, y, 1000),
                             (-1000, y, -1000, 1000, y, 1000, 1000, y, -1000)]
                    self.run.match.collision_world = World(floor + box(x-4, x+4, y, y+4, z+3, z+3.2))
                else:
                    match = self.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    self.sombra.team = self.player.team
                self.start()
                self.finish()
                self.assertEqual(self.script.component.entity_var(342).value(), 0)
                self.assertEqual(self.health.hack_until, 0)
                self.assertEqual(self.health.total, 150)

    def test_actual_emp_does_not_apply_to_recalling_tracer(self):
        self.start(buttons=16)
        self.assertTrue(tracer.recalling(self.player))
        self.finish()
        self.assertTrue(tracer.recalling(self.player))
        self.assertEqual(self.health.hack_until, 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "external barrier mesh required")
    def test_actual_emp_breaks_hostile_reinhardt_shield_but_respects_cover_and_team(self):
        for gate in ("open", "cover", "ally"):
            with self.subTest(gate=gate):
                self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                self.sombra.team, self.player.team = 1, 0
                rein, _ = support_tests.PulseDeflectTests().support(self.run, self.player, REINHARDT)
                rein.team = self.sombra.team if gate == "ally" else self.player.team
                for _ in range(30):
                    self.run.step({})
                for _ in range(20):
                    self.run.step({rein: (0, 0, 2)})
                shield = self.fight.reinhardt_barriers.states[rein.body]
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, 1600)
                if gate == "cover":
                    x, y, z = self.player.mover.position
                    match.collision_world = World(box(x-4, x+4, y, y+4, z+5.2, z+5.4))
                self.start()
                for _ in range(40):
                    self.run.step({rein: (0, 0, 2)})
                self.assertEqual(self.script.component.entity_var(342).value(), 0)
                self.assertEqual(shield.active, gate != "open")
                self.assertEqual(shield.current, 0 if gate == "open" else 1600)
                self.assertEqual(bool(self.health.hack_until), gate != "cover")
                self.assertEqual(self.health.total, 150)

    def test_actual_emp_drains_shield_pool_without_spending_health(self):
        # Explicit shield fixture on Tracer, whose stock body has no shields.
        self.health.most[combat.SHIELDS_POOL] = self.health.now[combat.SHIELDS_POOL] = 80
        self.start()
        self.finish()
        self.assertGreater(self.health.hack_until, self.fight.tick)
        self.assertEqual(self.health.now[combat.SHIELDS_POOL], 0)
        self.assertEqual(self.health.now[combat.HEALTH_POOL], 150)

    def test_actual_emp_breaks_personal_and_projected_particle_barriers(self):
        for gate in ("open", "cover", "range", "ally", "caster_outside"):
            with self.subTest(gate=gate):
                self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                self.sombra.team = 0 if gate == "ally" else 1
                self.player.team = 0
                zarya, _ = support_tests.PulseDeflectTests().support(self.run, self.player, ZARYA)
                zarya.team = self.player.team
                x, y, z = self.player.mover.position
                zarya.mover.position[:] = (x + 3, y, z + 1)
                zarya.mover.body = None
                for _ in range(30):
                    self.run.step({})
                facing = aim(self.fight._eye(zarya), self.fight._eye(self.player))
                self.run.step({zarya: (*facing, 16)})
                self.run.step({zarya: (*facing, 8)})
                zarya_health = self.case.case.health(zarya)
                protected = ((self.player, self.health), (zarya, zarya_health))
                for owner, vitals in protected:
                    bubble = zarya_barrier.active(owner)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(zarya_barrier.barrier_health(bubble, vitals), 200)
                if gate == "cover":
                    match.collision_world = World(box(x-4, x+6, y, y+4, z+3, z+3.2))
                elif gate == "range":
                    self.sombra.mover.position[2] += 40
                    self.sombra.mover.body = None
                elif gate == "caster_outside":
                    zarya.mover.position[0] += 40
                    zarya.mover.body = None
                self.start()
                for _ in range(40):
                    self.run.step({})
                self.assertEqual(self.script.component.entity_var(342).value(), 0)
                for owner, vitals in protected:
                    bubble = zarya_barrier.active(owner)
                    if gate == "open" or (gate == "caster_outside" and owner is self.player):
                        self.assertIsNone(bubble)
                    else:
                        self.assertIsNotNone(bubble)
                        self.assertEqual(zarya_barrier.barrier_health(bubble, vitals), 200)
                self.assertEqual(bool(self.health.hack_until), gate in ("open", "caster_outside"))
                self.assertEqual(self.health.total, 150)
                self.assertEqual(zarya_health.total, 400)
                self.assertEqual(zarya.body_script.component.entity_var(87).value() or 0, 0)

    def test_actual_emp_cancels_unreleased_pulse_without_refund(self):
        start = self.fight.tick
        self.start()
        self.finish()
        windup = self.fight.tick - start
        self.setUp()
        script = self.player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        self.start()
        for _ in range(windup - 5):
            self.run.step({})
        self.run.step({self.player: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(45):
            self.run.step({})
            self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertGreater(self.health.hack_until, self.fight.tick)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_emp_preserves_already_released_pulse(self):
        script = self.player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.start(buttons=32)
        self.finish()
        self.assertGreater(self.health.hack_until, self.fight.tick)
        bomb, = self.fight.pulse_bombs.bombs
        for _ in range(90):
            self.run.step({})
            if bomb not in self.fight.pulse_bombs.bombs:
                break
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertTrue(self.case.case.health(self.sombra).dead)


if __name__ == "__main__":
    unittest.main()
