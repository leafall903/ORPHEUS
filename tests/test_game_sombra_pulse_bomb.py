"""Actual 0034 Pulse Bomb from cast to contact, fuse and explosion."""

import unittest
from dataclasses import replace

import test_game_sombra_grenade as grenades
import test_game_sombra_npc as npcs
import test_game_sombra_tracer_blink as blink
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge

from ow174.game import combat, pulse_bomb, stats, tracer_combat, winston_combat
from ow174.game.content import heroes
from ow174.game.world import HEALTH_POOL


def bombs(fight):
    return [r for r in fight.rockets if r.definition == pulse_bomb.DEFINITION]


def throw(f, point=(0, 1.15, 0)):
    fight = f.run.match.combat
    angle = aim(fight._eye(f.b), point)
    f.run.step({f.b: (*angle, 32)})
    for _ in range(45):
        f.run.step({f.b: (*angle, 0)})
        active = bombs(fight)
        if active:
            return active[0]
    raise AssertionError("0034 did not release")


def wait_for_contact(f, rocket):
    for _ in range(70):
        if rocket.pulse.stuck:
            return f.run.frame
        blink.step(f)
    raise AssertionError("Pulse Bomb did not contact")


class SombraPulseBombTests(unittest.TestCase):
    def arena(self, wall=None):
        f, _ = blink.SombraTracerBlinkTests().arena(wall)
        place(f.b, (0, 0, 3))
        f.b.body_script.ult_seconds = 0
        f.b.body_script.set_ult_charge(1260)
        return f, f.run.match.combat

    def test_paid_bomb_sticks_follows_sombra_and_explodes_once_on_fuse(self):
        f, fight = self.arena()
        owner = f.b.body
        rocket = throw(f)
        self.assertEqual(charge(f.b), 0)
        self.assertEqual(rocket.blast.amount, 350)
        self.assertEqual(rocket.direct.amount, 5)
        self.assertEqual(rocket.gravity, 30)
        at = wait_for_contact(f, rocket)
        self.assertEqual(rocket.pulse.body, f.a.body)
        self.assertEqual(f.health(f.a), 195)
        position = rocket.position
        place(f.a, (6, 0, 0))
        blink.step(f)
        self.assertAlmostEqual(rocket.position[0] - position[0], 6)
        for _ in range(59):
            blink.step(f)
        self.assertEqual(f.health(f.a), 195)
        for _ in range(4):
            blink.step(f)
        self.assertFalse(bombs(fight))
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(fight.vitals[f.a.body].killer, owner)
        self.assertIn(fight.vitals[f.a.body].death_tick - at, (62, 63))
        self.assertEqual(f.health(f.b), 150)
        self.assertEqual(charge(f.b), 0)

    def test_late_sombra_emp_preserves_the_released_bomb(self):
        f, fight = self.arena()
        rocket = throw(f)
        wait_for_contact(f, rocket)
        f.a.body_script.set_ult_charge(1400)
        f.press(32, 43)
        self.assertIn(f.b.body, fight.hacks)
        self.assertIn(rocket, bombs(fight))
        self.assertEqual(f.health(f.a), 195)
        for _ in range(25):
            blink.step(f)
        self.assertFalse(bombs(fight))
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(f.b.stats.value(stats.HERO_DAMAGE), 200)

    def test_released_bomb_survives_actual_lethal_sombra_fire(self):
        f, fight = self.arena()
        rocket = throw(f)
        wait_for_contact(f, rocket)
        f.health(f.b)
        source = f.b.body
        fight.vitals[source].now[HEALTH_POOL] = 1
        f.press(1)
        self.assertTrue(fight.is_dead(f.b))
        self.assertEqual(fight.vitals[source].killer, f.a.body)
        self.assertIn(rocket, bombs(fight))
        for _ in range(65):
            blink.step(f)
        self.assertFalse(bombs(fight))
        self.assertEqual(fight.vitals[f.a.body].killer, source)

    def test_actual_recall_detaches_bomb_at_last_world_position(self):
        f, fight = self.arena()
        self.assertTrue(f.run.match.switch_hero(f.a, heroes()[tracer_combat.HERO]))
        place(f.a, (0, 0, 0))
        f.press(0, 55)
        rocket = throw(f)
        wait_for_contact(f, rocket)
        self.assertEqual(rocket.pulse.body, f.a.body)
        self.assertEqual(f.health(f.a), 145)
        point = rocket.position
        f.run.step({f.a: (0, 0, 16)})
        self.assertTrue(tracer_combat.recalling(f.a))
        blink.step(f)
        self.assertEqual(rocket.pulse.body, 0)
        self.assertEqual(rocket.position, point)
        for _ in range(85):
            blink.step(f)
        self.assertFalse(bombs(fight))
        self.assertEqual(f.health(f.a), 150)

    def test_paid_emp_in_windup_spends_charge_without_release(self):
        control, control_fight = self.arena()
        control.a.body_script.set_ult_charge(1400)
        control.press(32)
        delay = 0
        while control.b.body not in control_fight.hacks and delay < 45:
            control.press(0)
            delay += 1
        self.assertGreater(delay, 2)
        self.assertIn(control.b.body, control_fight.hacks)

        f, fight = self.arena()
        f.a.body_script.set_ult_charge(1400)
        f.press(32)
        f.press(0, delay - 3)
        blink.step(f, 32)
        self.assertEqual(charge(f.b), 0)
        self.assertFalse(bombs(fight))
        for _ in range(45):
            blink.step(f)
            self.assertFalse(bombs(fight))
        self.assertIn(f.b.body, fight.hacks)
        self.assertEqual(charge(f.b), 0)
        self.assertEqual(f.health(f.a), 200)

    def test_silence_blocks_payment_and_fresh_pulse_is_available_after_expiry(self):
        f, fight = self.arena()
        f.press(2, 75)
        self.assertIn(f.b.body, fight.hacks)
        blink.step(f, 32)
        for _ in range(50):
            blink.step(f)
        self.assertEqual(charge(f.b), 1260)
        self.assertFalse(bombs(fight))
        for _ in range(340):
            blink.step(f)
        self.assertNotIn(f.b.body, fight.hacks)
        self.assertIsNotNone(throw(f))
        self.assertEqual(charge(f.b), 0)

    def test_bomb_sticks_to_winston_barrier_and_does_not_spill_to_sombra(self):
        f, fight = self.arena()
        place(f.b, (0, 0, 0))
        place(f.a, (0, 0, 5.2))
        defender = grenades.SombraGrenadeTests().join_ally(f, winston_combat.HERO, (0, 0, 8))
        f.run.step({defender: (0, 0, 16)})
        for _ in range(6):
            blink.step(f)
        dome, = (b for b in fight.barriers.values() if isinstance(b, winston_combat.Dome))
        rocket = throw(f, (0, 1.15, 5.2))
        wait_for_contact(f, rocket)
        self.assertEqual(rocket.pulse.barrier, dome.entity)
        self.assertEqual(dome.health, 695)
        for _ in range(66):
            blink.step(f)
        self.assertFalse(bombs(fight))
        self.assertEqual(dome.health, 345)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(f.health(defender), 500)

    def test_blast_rings_preserve_ally_and_exclude_enemy_beyond_four_metres(self):
        f, fight = self.arena()
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 1, 0
        helper = grenades.SombraGrenadeTests()
        ally = helper.join_ally(f, winston_combat.HERO, (8, 0, 8))
        far = helper.join_ally(f, winston_combat.HERO, (10, 0, 8))
        falloff = helper.join_ally(f, winston_combat.HERO, (12, 0, 8))
        ally.team, far.team, falloff.team = 0, 1, 1
        rocket = throw(f)
        wait_for_contact(f, rocket)
        x, _, z = rocket.position
        receiver = next(t for t in fight._targets() if t.owner is falloff)
        place(ally, (x - 1, 0, z))
        place(far, (x - 5.5, 0, z))
        place(falloff, (x + 3 + receiver.radius, 0, z))
        place(f.b, (8, 0, 3))
        for _ in range(66):
            blink.step(f)
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(f.health(ally), 500)
        self.assertEqual(f.health(far), 500)
        self.assertAlmostEqual(f.health(falloff), 295, delta=.04)  # Pose rounds to the movement grid.
        self.assertFalse(bombs(fight))

    def test_world_only_gap_agrees_for_flight_and_armed_fuse(self):
        results = []
        for gap in ("none", "flight", "fuse"):
            with self.subTest(gap=gap):
                f, fight = self.arena()
                rocket = throw(f)
                if gap == "fuse":
                    wait_for_contact(f, rocket)
                if gap != "none":
                    f.run.frame += 130
                    f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                else:
                    for _ in range(130):
                        blink.step(f)
                self.assertFalse(bombs(fight))
                results.append((f.health(f.a), f.health(f.b), fight.vitals[f.a.body].death_tick))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertEqual(results[0][:2], (0, 0))

    def test_wall_stick_explodes_on_approach_side_and_preserves_sombra_behind_it(self):
        f, fight = self.arena(wall=1.5)
        rocket = throw(f)
        wait_for_contact(f, rocket)
        self.assertTrue(rocket.pulse.wall)
        self.assertEqual(rocket.pulse.body, 0)
        self.assertEqual(f.health(f.a), 200)
        for _ in range(66):
            blink.step(f)
        self.assertFalse(bombs(fight))
        self.assertEqual(f.health(f.a), 200)
        self.assertTrue(fight.is_dead(f.b))
        self.assertEqual(charge(f.b), 0)


class SombraPulseBombNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npcs.SombraActualNpcTests.restore_graphs(path)

    def test_bomb_contact_and_blast_reach_all_five_actual_npcs(self):
        self.assertEqual(len(self.catalog), 5)
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = npcs.SombraActualNpcTests.make(self, key)
                blink.prepare(f)
                place(f.a, (10, 0, 0))
                place(f.b, (0, 0, 3))
                bot.position = (0, 0, 0)
                f.b.body_script.ult_seconds = 0
                f.b.body_script.set_ult_charge(1260)
                fight = f.run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                target = next(t for t in fight._targets() if t.owner is bot)
                rocket = throw(f, (0, target.height * .45, 0))
                wait_for_contact(f, rocket)
                self.assertEqual(rocket.pulse.body, bot.entity)
                self.assertAlmostEqual(before - vitals.total, 5)
                place(f.b, (10, 0, 3))
                for _ in range(66):
                    blink.step(f)
                self.assertAlmostEqual(vitals.total, max(0, before - 355))
                self.assertFalse(bombs(fight))
                self.assertEqual(charge(f.b), 0)
