"""Actual Mei spray advances Mercy's frost and movement control."""

import unittest
from unittest.mock import patch

import test_game_mercy_descent as descent_tests
import test_game_mercy_resurrect as resurrect_tests
import test_game_mercy_staff as staff_tests
from test_game_combat import aim
from test_game_mercy_blaster_critical import equip, shot

from ow174.game import cryo
from ow174.game.combat import FREEZE_SECONDS, FROST_INITIAL, MEI_SPRAY_LOADOUT, TICK
from ow174.game.commands import FIRE, JUMP, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script.regeneration import MERCY


class MercyFrostTests(unittest.TestCase):
    def encounter(self, distance=4):
        f = staff_tests.MercyStaffTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.match.switch_hero(f.enemy, heroes()[cryo.HERO])
        x, y, z = f.mercy.mover.position
        f.enemy.mover.position[:] = (x + distance, y, z)
        f.enemy.mover.body = None
        target = next(t for t in f.fight._targets() if t.owner is f.mercy)
        return f, f.fight.vitals_of(target)

    def spray(self, f, target=None):
        x, y, z = (target or f.mercy).mover.position
        direction = aim(f.fight._eye(f.enemy), (x, y + 1, z))
        f.run.step({f.enemy: (*direction, FIRE)})

    def test_continuous_spray_starts_frost_from_zero_after_same_body_revival(self):
        res = resurrect_tests.ResurrectTests()
        res.setUp(ally_hero=MERCY)
        self.addCleanup(res.doCleanups)
        self.assertTrue(res.match.switch_hero(res.enemy, heroes()[cryo.HERO]))
        res.enemy.mover.position[:] = (4, 1, 3)
        res.enemy.mover.body = None
        body = res.ally.body
        res.cast()
        health = res.fight.vitals_of(res.target)
        protection_end = health.invulnerable_until
        self.assertGreater(protection_end, res.fight.now)

        with patch.object(res.fight, "_damage", wraps=res.fight._damage) as damage:
            while (res.run.frame + 1) * TICK < protection_end:
                self.spray(res, res.ally)
                self.assertEqual(health.total, 200)
                self.assertFalse(health.frost_sources)
                self.assertFalse(res.fight.is_stunned(res.ally))
        protected_contacts = [call for call in damage.call_args_list
                              if call.args[1].entity == body and call.args[4] == MEI_SPRAY_LOADOUT]
        self.assertGreater(len(protected_contacts), 1)

        for _ in range(15):
            self.spray(res, res.ally)
            if health.total < 200:
                break
        self.assertGreaterEqual(res.fight.now, protection_end)
        self.assertEqual(res.ally.body, body)
        self.assertLess(health.total, 200)
        self.assertEqual(res.fight.frost_progress(res.ally), FROST_INITIAL)
        self.assertFalse(res.fight.is_stunned(res.ally))

        for _ in range(125):
            self.spray(res, res.ally)
            if res.fight.is_stunned(res.ally):
                break
        self.assertEqual(res.fight.frost_progress(res.ally), 1)
        self.assertAlmostEqual(health.frozen_until - res.fight.now, FREEZE_SECONDS)

    @staticmethod
    def walk(f, buttons=0):
        run = f.run
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        f.mercy.take_commands([Command(run.frame, forward=127, buttons=buttons)])

    def test_actual_partial_spray_slows_movement_then_fades(self):
        f, hp = self.encounter()
        for _ in range(20):
            self.spray(f)
        self.assertLess(hp.total, 200)
        progress = f.fight.frost_progress(f.mercy)
        self.assertGreaterEqual(progress, 0.2)
        self.assertLess(progress, 1)
        self.assertFalse(f.fight.is_stunned(f.mercy))
        self.walk(f)
        self.assertAlmostEqual(f.mercy.mover.values.run_forward,
                               f.mercy.mover.data.run_forward * (1 - progress), delta=0.17)
        for _ in range(80):
            f.run.step({})
        self.walk(f)
        self.assertEqual(f.fight.frost_progress(f.mercy), 0)
        self.assertEqual(f.mercy.mover.values.run_forward, f.mercy.mover.data.run_forward)

    def test_partial_spray_keeps_descent_full_freeze_releases_it_and_thaw_allows_restart(self):
        descent = descent_tests.AngelicDescentTests()
        descent.setUp()
        self.addCleanup(descent.doCleanups)
        f = descent.fixture
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[cryo.HERO]))
        descent.place_at_elevated_firing_platform(40)
        descent.step(30, JUMP)
        state = descent.descent()
        self.assertTrue(state.active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)

        saw_partial = False
        for _ in range(125):
            x, y, z = f.mercy.mover.position
            direction = aim(f.fight._eye(f.enemy), (x, y + 1, z))
            f.run.step({f.enemy: (*direction, FIRE), f.mercy: (0, 0, JUMP)})
            progress = f.fight.frost_progress(f.mercy)
            if progress == 1:
                break
            if progress > 0:
                saw_partial = True
                self.assertTrue(state.active)
                self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)
        self.assertTrue(saw_partial)
        self.assertEqual(f.fight.frost_progress(f.mercy), 1)
        self.assertFalse(state.active)

        target = next(t for t in f.fight._targets() if t.owner is f.mercy)
        frozen_until = f.fight.vitals_of(target).frozen_until
        for _ in range(8):
            descent.step(1, JUMP)
            self.assertFalse(state.active)
            self.assertFalse(any(mod.index == 13 for mod in f.mercy.mover.mods.values()))
        self.assertLess(f.mercy.mover.velocity[1], -3)
        while (f.run.frame + 1) * TICK < frozen_until:
            descent.step(1, JUMP)
            self.assertFalse(state.active)
        descent.step(1)
        self.assertFalse(f.fight.is_stunned(f.mercy))
        self.assertGreater(f.mercy.mover.position[1], 1)
        descent.step(30, JUMP)
        self.assertTrue(state.active)
        self.assertAlmostEqual(f.mercy.mover.velocity[1], -2, places=2)

    def test_actual_spray_freezes_mercy_and_blocks_input_until_thaw(self):
        f, hp = self.encounter()
        for _ in range(125):
            self.spray(f)
            if f.fight.frost_progress(f.mercy) == 1:
                break
        self.assertLess(hp.total, 130)
        self.assertEqual(f.fight.frost_progress(f.mercy), 1)
        self.assertAlmostEqual(hp.frozen_until - f.fight.now, 1.3)
        before = tuple(f.mercy.mover.position)
        for _ in range(30):
            self.walk(f, buttons=FIRE | 8 | 16 | 32 | 0x40)
        self.assertEqual(tuple(f.mercy.mover.position), before)
        self.assertEqual(f.mercy.body_script.buttons, 0)
        self.assertTrue(f.fight.is_stunned(f.mercy))
        for _ in range(55):
            f.run.step({})
        self.assertEqual(f.fight.frost_progress(f.mercy), 0)
        self.assertFalse(f.fight.is_stunned(f.mercy))
        for _ in range(20):
            self.walk(f)
        self.assertNotEqual(tuple(f.mercy.mover.position), before)

    def freeze_during_reload(self, *, initial_damage=0, progress=0.8):
        f, health = self.encounter()
        equip(f.run, f.mercy)
        component = f.mercy.body_script.component
        slot = component.entity_var(31)
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x006D)
        magazine = weapon.find_var(0, 53)
        self.assertEqual(slot.value(), 2)
        shot(f.run, f.mercy, f.enemy.mover.position, 0.8)
        self.assertEqual(magazine.value(), 19)
        if initial_damage:
            target = next(t for t in f.fight._targets() if t.owner is f.mercy)
            f.fight._damage(f.enemy, target, initial_damage, False, None, f.run.frame)
        for _ in range(125):
            self.spray(f)
            if f.fight.frost_progress(f.mercy) >= progress:
                break
        self.assertGreaterEqual(f.fight.frost_progress(f.mercy), progress)
        self.assertFalse(f.fight.is_stunned(f.mercy))
        f.run.step({f.mercy: (0, 0, 0x400)})
        reload = weapon.node_state(weapon.graph.node(54))
        self.assertTrue(reload.active)
        for _ in range(40):
            self.spray(f)
            if f.fight.is_stunned(f.mercy):
                break
        self.assertTrue(f.fight.is_stunned(f.mercy))
        self.assertAlmostEqual(health.frozen_until - f.fight.now, 1.3)
        self.assertTrue(reload.active)
        self.assertEqual(magazine.value(), 19)

        return f, health, reload, magazine

    def test_full_freeze_blocks_switch_and_fire_during_live_reload(self):
        f, health, reload, magazine = self.freeze_during_reload()
        slot = f.mercy.body_script.component.entity_var(31)
        for _ in range(82):
            if (f.run.frame + 1) * TICK >= health.frozen_until:
                break
            f.run.frame += 1
            f.match.update(f.run.frame * TICK, f.run.frame)
            f.mercy.take_commands([Command(f.run.frame, buttons=FIRE, action=1)])
            self.assertEqual(slot.value(), 2)
            self.assertEqual(f.mercy.body_script.buttons, 0)
            self.assertFalse(any(projectile.shooter is f.mercy for projectile in f.fight.rockets))
        self.assertTrue(f.fight.is_stunned(f.mercy))
        self.assertEqual(magazine.value(), 20)
        for _ in range(15):
            f.run.step({})
        self.assertFalse(f.fight.is_stunned(f.mercy))
        self.assertFalse(reload.active)
        self.assertEqual(slot.value(), 2)
        self.assertEqual(magazine.value(), 20)

        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        enemy_health = f.fight.vitals_of(target)
        before = enemy_health.total
        shot(f.run, f.mercy, f.enemy.mover.position, 0.8)
        self.assertEqual(magazine.value(), 19)
        self.assertEqual(before - enemy_health.total, 20)
        f.run.frame += 1
        f.match.update(f.run.frame * TICK, f.run.frame)
        f.mercy.take_commands([Command(f.run.frame, action=1)])
        self.assertEqual(slot.value(), 1)

    def test_frozen_mercy_lethal_icicle_cancels_reload(self):
        f, health, reload, magazine = self.freeze_during_reload(initial_damage=55, progress=0.95)
        old_body = f.mercy.body
        self.assertGreater(health.total, 0)
        self.assertLess(health.total, 75)
        x, y, z = f.mercy.mover.position
        direction = aim(f.fight._eye(f.enemy), (x, y + 0.8, z))
        for _ in range(50):
            self.assertTrue(reload.active)
            self.assertTrue(f.fight.is_stunned(f.mercy))
            f.run.step({f.enemy: (*direction, SECONDARY)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, f.enemy.body)
        self.assertFalse(reload.active)
        self.assertEqual(magazine.value(), 19)
        for _ in range(100):
            f.run.step({f.mercy: (0, 0, FIRE)})
        self.assertEqual(f.mercy.body, old_body)
        self.assertEqual(magazine.value(), 19)
        self.assertFalse(any(r.shooter is f.mercy for r in f.fight.rockets))

        for _ in range(250):
            f.run.step({})
            if f.mercy.body != old_body:
                break
        self.assertNotEqual(f.mercy.body, old_body)
        self.assertFalse(f.fight.is_stunned(f.mercy))
        equip(f.run, f.mercy)
        new_weapon = next(i for i in f.mercy.body_script.component.instances.values() if i.graph.index == 0x006D)
        fresh = new_weapon.find_var(0, 53)
        self.assertEqual(fresh.value(), 20)
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        enemy_health = f.fight.vitals_of(target)
        before = enemy_health.total
        shot(f.run, f.mercy, f.enemy.mover.position, 0.8)
        self.assertEqual(fresh.value(), 19)
        self.assertEqual(before - enemy_health.total, 20)
        self.assertEqual(magazine.value(), 19)

    def test_friendly_and_out_of_range_spray_do_not_accumulate_frost(self):
        for friendly in (True, False):
            with self.subTest(friendly=friendly):
                f, hp = self.encounter(distance=4 if friendly else 16)
                if friendly:
                    f.enemy.team = f.mercy.team
                for _ in range(35):
                    self.spray(f)
                self.assertEqual(hp.total, 200)
                self.assertEqual(f.fight.frost_progress(f.mercy), 0)
