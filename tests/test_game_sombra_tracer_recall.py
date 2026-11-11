"""Tracer's actual 001A Recall against Sombra and world-only timing."""

import unittest

import test_game_sombra_grenade as grenades
import test_game_sombra_tracer_blink as blink_fixture
from test_game_combat import aim
from test_game_sombra_mei_wall import tap
from test_game_sombra_orisa_halt import place

from ow174.game import combat, mei, tracer_combat
from ow174.game.commands import Command
from ow174.game.script import expr
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest


def weapon(f):
    return next(i for i in f.b.body_script.component.instances.values() if i.graph.index == 0x16)


def rewind(f):
    blink_fixture.step(f, 16)


def finish(f):
    for _ in range(85):
        blink_fixture.step(f)
        if not tracer_combat.recalling(f.b):
            return
    raise AssertionError("Recall did not finish")


class SombraTracerRecallTests(unittest.TestCase):
    def arena(self):
        return blink_fixture.SombraTracerBlinkTests().arena()[0]

    def injured(self):
        f = self.arena()
        for _ in range(205):
            blink_fixture.step(f, yaw=0)
        blink_fixture.step(f, 8, yaw=0)
        for _ in range(12):
            blink_fixture.step(f, 1, yaw=0)
        f.press(1, 15)
        self.assertLess(f.health(f.b), 150)
        self.assertLess(weapon(f).vars[53].value(), 40)
        return f

    def test_actual_recall_restores_wounded_health_past_pose_ammo_and_cooldown(self):
        f = self.injured()
        recall = tracer_combat.recall_instance(f.b.body_script)
        self.assertAlmostEqual(f.b.mover.position[2], 13.5)
        rewind(f)
        self.assertTrue(tracer_combat.recalling(f.b))
        finish(f)
        self.assertEqual(f.health(f.b), 150)
        self.assertAlmostEqual(f.b.mover.position[2], 6)
        self.assertEqual(weapon(f).vars[53].value(), weapon(f).vars[198].value())
        self.assertGreater(recall.vars[61].value(), 11.9)
        blink_fixture.step(f)
        rewind(f)
        self.assertFalse(tracer_combat.recalling(f.b))
        f.run.frame += 755
        f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
        blink_fixture.step(f)
        rewind(f)
        self.assertTrue(tracer_combat.recalling(f.b))

    def test_recall_is_immune_to_actual_sombra_pistol_hack_and_paid_emp(self):
        f = self.arena()
        fight = f.run.match.combat
        f.press(2, 18)
        rewind(f)
        self.assertTrue(tracer_combat.recalling(f.b))
        f.press(2, 7)
        self.assertNotIn(f.b.body, fight.hacks)
        f.press(1, 16)
        self.assertEqual(f.health(f.b), 150)
        f.a.body_script.set_ult_charge(1400)
        f.press(32, 37)
        self.assertNotIn(f.b.body, fight.hacks)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        finish(f)
        f.press(1, 18)
        self.assertLess(f.health(f.b), 150)

    def test_hack_denies_fresh_recall_and_no_old_press_replays(self):
        f = self.arena()
        fight = f.run.match.combat
        f.press(2, 75)
        self.assertIn(f.b.body, fight.hacks)
        rewind(f)
        self.assertFalse(tracer_combat.recalling(f.b))
        for _ in range(380):
            blink_fixture.step(f)
        self.assertNotIn(f.b.body, fight.hacks)
        self.assertFalse(tracer_combat.recalling(f.b))
        rewind(f)
        self.assertTrue(tracer_combat.recalling(f.b))

    def test_world_only_rewind_matches_stepped_and_rejects_a_late_fire_packet(self):
        results = []
        for gap in (False, True):
            with self.subTest(gap=gap):
                f = self.injured()
                fight = f.run.match.combat
                rewind(f)
                accepted = f.run.frame
                if gap:
                    f.run.frame += 90
                    f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                    self.assertFalse(tracer_combat.recalling(f.b))
                    f.b.take_commands([Command(accepted + 70, buttons=1, forward=127)])
                else:
                    finish(f)
                results.append((tuple(f.b.mover.position), f.health(f.b), weapon(f).vars[53].value()))
                self.assertFalse(fight.tracer_recalls)
                self.assertEqual(f.health(f.a), 200)
        self.assertEqual(results[0], results[1])

    def test_actual_recall_cleanses_ana_antiheal_and_restores_health(self):
        f = self.arena()
        helper = grenades.SombraGrenadeTests()
        ana = helper.join_ally(f, 0x02E000000000013B, (0, 0, 2))
        fight = f.run.match.combat
        helper.throw(f.run, ana, f.b)
        vitals = fight.vitals[f.b.body]
        self.assertEqual(vitals.total, 100)
        self.assertGreater(vitals.healing_block_until, fight.tick)
        rewind(f)
        finish(f)
        self.assertEqual(vitals.healing_block_until, 0)
        self.assertEqual(vitals.total, 150)

    def test_motion_blink_and_weapon_input_are_locked_during_recall(self):
        f = self.arena()
        rewind(f)
        position = tuple(f.b.mover.position)
        blink = next(i for i in f.b.body_script.component.instances.values()
                     if i.graph.index == tracer_combat.BLINK)
        before = weapon(f).vars[53].value()
        for _ in range(60):
            f.run.frame += 1
            f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
            f.b.take_commands([Command(f.run.frame, yaw=-32768, buttons=1 | 8, forward=127)])
            self.assertEqual(tuple(f.b.mover.position), position)
        self.assertTrue(tracer_combat.recalling(f.b))
        self.assertEqual(blink.vars[11].value(), 3)
        self.assertEqual(weapon(f).vars[53].value(), before)
        self.assertEqual(f.health(f.a), 200)

    def test_historical_destination_inside_actual_ice_wall_uses_later_safe_pose(self):
        f = self.arena()
        helper = grenades.SombraGrenadeTests()
        caster = helper.join_ally(f, mei.HERO, (0, 0, 3))
        fight = f.run.match.combat
        for _ in range(200):
            blink_fixture.step(f, yaw=0)
        blink_fixture.step(f, 8, yaw=0)
        facing = aim(fight._eye(caster), (0, 0, 6))
        tap(f, caster, 16, facing)
        tap(f, caster, 1, facing)
        self.assertFalse(fight.translocators.fits_destination(f.b, expr.Vec3(0, 0, 6)))
        self.assertEqual(len(fight.walls.pieces), 5)
        rewind(f)
        self.assertTrue(tracer_combat.recalling(f.b))
        finish(f)
        self.assertAlmostEqual(f.b.mover.position[2], 13.5)

    def test_active_recall_snapshot_captures_phase_and_history(self):
        f = self.injured()
        fight = f.run.match.combat

        def image():
            stored = _Graph({"match": f.run.match, "tracer": f.b}, None)
            roots = {
                "history": stored.value(fight.tracer_history),
                "session": stored.value(fight.tracer_recalls),
            }
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": roots, "nodes": stored.finish(), "assets": []})

        before = image()
        rewind(f)
        warmup = image()
        self.assertNotEqual(warmup, before)
        for _ in range(30):
            blink_fixture.step(f)
        self.assertNotEqual(image(), warmup)
        self.assertTrue(tracer_combat.recalling(f.b))

    def test_body_replacement_drops_the_active_recall(self):
        f = self.injured()
        fight = f.run.match.combat
        old = f.b.body
        rewind(f)
        self.assertIn(old, fight.tracer_recalls)
        blink_fixture.prepare(f)
        self.assertNotEqual(f.b.body, old)
        self.assertNotIn(old, fight.tracer_recalls)
        self.assertNotIn(old, fight.tracer_history)
        place(f.b, (1, 0, 8))
        for _ in range(90):
            blink_fixture.step(f)
        self.assertEqual(tuple(f.b.mover.position), (1, 0, 8))
