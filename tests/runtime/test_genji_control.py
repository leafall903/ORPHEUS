"""Genji command acceptance for the resource-backed Flashbang and frost policies."""

import unittest
from dataclasses import replace

import test_mccree_flashbang as flash
from test_game_combat import aim
from test_genji_combat import GenjiFixture
from test_game_mover import FLOOR, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes


class GenjiControlTests(GenjiFixture):
    step = flash.FlashbangTests.step
    cast = flash.FlashbangTests.cast

    def scene(self, distance=3):
        run, genji = self.spawn(distance)
        caster = run.players[1]
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.mover.position[:] = [0, 1, distance]
        return run, caster, genji

    def sleep_dart_scene(self, *, frontal):
        run, genji = self.spawn(4)
        ana = run.players[1]
        run.match.switch_hero(ana, heroes()[0x02E000000000013B])
        ana.mover.position[:] = [0, 1, 4]
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(ana), (0, 2, 0))
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        target = ana if frontal else genji
        for frame in range(70):
            run.step({genji: (0 if frontal else -32768, 0, 16 if frame == 0 else 0),
                      ana: (yaw, pitch, 8 if frame == 5 else 0)})
            if frame == 0:
                self.assertTrue(deflect.state(10).active)
            if fight.is_asleep(target):
                break
        self.assertTrue(fight.is_asleep(target))
        return run, ana, genji, deflect

    def test_rear_sleep_dart_interrupts_deflect_and_expires_without_resuming_it(self):
        run, ana, genji, deflect = self.sleep_dart_scene(frontal=False)
        fight = run.match.combat
        self.assertFalse(deflect.state(10).active)
        self.assertEqual(self.health(run, genji), 195)
        self.assertEqual(self.health(run, ana), 200)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in ana.client.messages(combat.HIT)], [5])
        vitals = fight.vitals[genji.body]
        deadline = vitals.sleep_until
        self.assertEqual(deadline - run.frame, combat.SLEEP_TICKS)
        position = tuple(genji.mover.position)
        while run.frame < deadline - 1:
            self.step(run, ana, genji, target_buttons=1 | 8 | 16 | 0x40, forward=127, target_pitch=0)
            self.assertEqual(tuple(genji.mover.position), position)
        self.assertTrue(fight.is_asleep(genji))
        self.step(run, ana, genji, target_buttons=16, target_pitch=0)
        self.assertEqual(vitals.sleep_until, 0)
        self.assertEqual(vitals.sleep_wake_floor, 0)
        self.assertFalse(fight.is_stunned(genji))
        self.assertFalse(deflect.state(10).active)
        self.assertGreater(deflect.state(0).var.value(), 0)
        self.fire(run, genji, 1)
        self.assertEqual(self.health(run, ana), 113)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 87)

    def test_reflected_sleep_dart_sleeps_ana_and_actual_damage_obeys_wake_floor(self):
        for late in (False, True):
            with self.subTest(late=late):
                run, ana, genji, deflect = self.sleep_dart_scene(frontal=True)
                fight = run.match.combat
                self.assertTrue(deflect.state(10).active)
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, ana), 195)
                self.assertFalse(fight.is_asleep(genji))
                self.assertFalse(ana.client.messages(combat.HIT))
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 5)
                hit, = genji.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": ana.body}, "+0x4": 5, "+0x8": 0})
                vitals = fight.vitals[ana.body]
                floor = vitals.sleep_wake_floor
                self.assertEqual(floor - run.frame, combat.SLEEP_WAKE_FLOOR_TICKS)
                run.step({genji: (0, 0, 16)})
                for _ in range(15):
                    run.step({})
                self.assertFalse(deflect.state(10).active)
                if late:
                    while run.frame < floor:
                        run.step({})
                self.step(run, ana, genji, target_buttons=1, target_pitch=0)
                for _ in range(30):
                    self.step(run, ana, genji, target_pitch=0)
                    if vitals.total < 195:
                        break
                self.assertEqual(vitals.total, 166)
                deadline = vitals.sleep_until
                self.assertEqual(deadline, max(floor, run.frame + combat.SLEEP_WAKE_RECOVERY_TICKS))
                self.assertEqual(deadline == floor, not late)
                while run.frame < deadline - 1:
                    run.step({})
                    self.assertEqual(vitals.sleep_until, deadline)
                    self.assertTrue(fight.is_asleep(ana))
                run.step({})
                self.assertFalse(fight.is_asleep(ana))
                self.assertEqual(vitals.sleep_wake_floor, 0)

    def test_sleep_dart_wall_and_friendly_controls_do_not_apply_damage_or_sleep(self):
        for blocker in ("wall", "team"):
            with self.subTest(blocker=blocker):
                run, genji = self.spawn(4)
                ana = run.players[1]
                run.match.switch_hero(ana, heroes()[0x02E000000000013B])
                ana.mover.position[:] = [0, 1, 4]
                if blocker == "wall":
                    run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
                else:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    genji.team = ana.team
                for frame in range(70):
                    self.step(run, ana, genji, cast=8 if frame == 5 else 0,
                              target_buttons=16 if frame == 0 else 0, target_pitch=0)
                for player in (genji, ana):
                    self.assertEqual(self.health(run, player), 200)
                    self.assertFalse(run.match.combat.is_asleep(player))
                    self.assertFalse(player.client.messages(combat.HIT))

    def test_projected_barrier_cleanses_sleep_only_on_success_and_blocks_second_dart(self):
        run, genji = self.spawn(4, ("Genji", "Ana", "Zarya", "Second Ana"))
        ana, zarya, second = run.players[1:]
        for player, hero, position in ((ana, 0x013B, (0, 1, 4)), (zarya, 0x0068, (0, 1, -4)),
                                       (second, 0x013B, (0, 1, 7))):
            run.match.switch_hero(player, heroes()[0x02E0000000000000 | hero])
            player.mover.position[:] = position
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji.team = zarya.team = 0
        ana.team = second.team = 1
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(ana), (0, 2, 0))
        for frame in range(70):
            run.step({ana: (yaw, pitch, 8 if frame == 0 else 0)})
            if fight.is_asleep(genji):
                break
        self.assertTrue(fight.is_asleep(genji))
        vitals = fight.vitals[genji.body]
        self.assertEqual(vitals.total, 195)
        deadline = vitals.sleep_until
        wake_floor = vitals.sleep_wake_floor

        yaw, pitch = aim(fight._eye(zarya), fight._eye(genji))
        run.match.collision_world = World(FLOOR + wall(-5, -2, 5, -2))
        run.step({zarya: (yaw, pitch, 16), genji: (0, 0, 1)})
        self.assertFalse(fight.barriers.scripted)
        self.assertEqual((vitals.sleep_until, vitals.sleep_wake_floor), (deadline, wake_floor))
        run.step({})
        run.match.collision_world = World(FLOOR)
        run.step({zarya: (yaw, pitch, 16)})
        bubble, = fight.barriers.scripted.values()
        self.assertIs(bubble.recipient, genji)
        self.assertEqual(bubble.vitals.total, 200)
        self.assertLess(run.frame, wake_floor)
        self.assertEqual((vitals.sleep_until, vitals.sleep_wake_floor), (0, 0))
        self.assertFalse(fight.is_stunned(genji))
        self.assertFalse(fight.rockets)

        yaw, pitch = aim(fight._eye(second), (0, 2, 0))
        for frame in range(60):
            run.step({second: (yaw, pitch, 8 if frame == 0 else 0), genji: (0, 0, 1 if frame == 1 else 0)})
        self.assertLess(run.frame, deadline)
        self.assertEqual(bubble.vitals.total, 195)
        self.assertEqual(vitals.total, 195)
        self.assertFalse(fight.is_asleep(genji))
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 1)
        self.assertFalse(second.client.messages(combat.HIT))
        self.assertEqual(self.health(run, ana), 113)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 87)

    def mei_scene(self):
        run, genji = self.spawn(4)
        mei = run.players[1]
        run.match.switch_hero(mei, heroes()[0x02E00000000000DD])
        mei.mover.position[:] = [0, 1, 4]
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        return run, mei, genji, deflect

    def test_mei_spray_slows_genji_through_deflect_until_contact_expires(self):
        run, mei, genji, deflect = self.mei_scene()
        fight = run.match.combat
        self.step(run, mei, genji, target_buttons=16, target_pitch=0)
        self.step(run, mei, genji, cast=1, target_pitch=0)
        for _ in range(16):
            self.step(run, mei, genji, target_pitch=0)
        self.assertTrue(deflect.state(10).active)
        self.assertEqual(self.health(run, genji), 197.25)
        self.assertAlmostEqual(fight.frost_progress(genji, tick=run.frame), 0.2)
        progress = genji.body_script.component.entity_var(combat.FROST_PROGRESS)
        self.assertAlmostEqual(progress.value(), 0.2)
        self.step(run, mei, genji, forward=127, target_pitch=0)
        self.assertAlmostEqual(genji.mover.values.run_forward, genji.mover.data.run_forward * 0.8, places=6)
        expiry = next(iter(fight.frost_hits[genji.body].sources.values()))[1]
        while run.frame < expiry:
            self.step(run, mei, genji, target_pitch=0)
        self.assertNotIn(genji.body, fight.frost_hits)
        self.assertEqual(progress.value(), 0)
        self.step(run, mei, genji, forward=127, target_pitch=0)
        self.assertEqual(genji.mover.values.run_forward, genji.mover.data.run_forward)

    def test_mei_freeze_ends_active_deflect_without_resuming_after_thaw(self):
        run, mei, genji, deflect = self.mei_scene()
        fight = run.match.combat
        for frame in range(130):
            self.step(run, mei, genji, cast=1, target_buttons=16 if frame == 35 else 0, target_pitch=0)
            if frame == 35:
                self.assertTrue(deflect.state(10).active)
            if fight.is_stunned(genji):
                break
        self.assertTrue(fight.is_stunned(genji))
        self.assertFalse(deflect.state(10).active)
        deadline = fight.frost_hits[genji.body].frozen_until
        self.assertEqual(deadline - run.frame, combat.FROST_FREEZE_TICKS)
        position = tuple(genji.mover.position)
        for _ in range(10):
            self.step(run, mei, genji, cast=1, target_buttons=1 | 8 | 16 | 0x40, forward=127, target_pitch=0)
            self.assertEqual(fight.frost_hits[genji.body].frozen_until, deadline)
            self.assertEqual(tuple(genji.mover.position), position)
        while run.frame < deadline - 1:
            self.step(run, mei, genji, target_buttons=1 | 8 | 16 | 0x40, forward=127, target_pitch=0)
            self.assertEqual(tuple(genji.mover.position), position)
        self.assertTrue(fight.is_stunned(genji))
        self.step(run, mei, genji, target_buttons=16, target_pitch=0)
        self.assertFalse(fight.is_stunned(genji))
        self.assertNotIn(genji.body, fight.frost_hits)
        self.assertFalse(deflect.state(10).active)
        self.assertGreater(deflect.state(0).var.value(), 0)
        for frame in range(45):
            self.step(run, mei, genji, target_buttons=1 if frame == 0 else 0, target_pitch=0)
        self.assertEqual(self.health(run, mei), 163)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 87)

    def test_mei_freeze_stops_active_strike_and_preserves_cooldown(self):
        run, mei, genji, _ = self.mei_scene()
        mei.mover.position[2] = 6
        fight = run.match.combat
        dash = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x01DB)
        for _ in range(130):
            self.step(run, mei, genji, cast=1, target_pitch=0)
            if self.health(run, genji) <= 120.25:
                break
        self.assertEqual(self.health(run, genji), 120.25)
        self.assertFalse(fight.is_stunned(genji))

        self.step(run, mei, genji, cast=1, target_buttons=8, target_pitch=0)
        self.assertTrue(dash.state(1).active)
        self.step(run, mei, genji, cast=1, target_pitch=0)
        stopped = tuple(genji.mover.position)
        self.assertGreater(stopped[2], 0)
        self.assertLess(stopped[2], 6)
        self.step(run, mei, genji, target_pitch=0)
        self.assertTrue(fight.is_stunned(genji))
        self.assertFalse(dash.state(1).active)
        self.assertNotIn(genji.body, fight.genji_strikes)
        self.assertEqual(tuple(genji.mover.position), stopped)
        deadline = fight.frost_hits[genji.body].frozen_until
        while run.frame < deadline - 1:
            self.step(run, mei, genji, target_buttons=8, forward=127, target_pitch=0)
            self.assertEqual(tuple(genji.mover.position), stopped)

        self.step(run, mei, genji, target_buttons=8, target_pitch=0)
        self.assertFalse(fight.is_stunned(genji))
        self.assertEqual(dash.state(1).counter, 1)
        self.assertGreater(dash.state(0).var.value(), 0)
        self.assertEqual(tuple(genji.mover.position), stopped)
        self.assertEqual(self.health(run, mei), 250)
        for _ in range(510):
            self.step(run, mei, genji, target_pitch=0)
            self.assertEqual(tuple(genji.mover.position), stopped)
            if dash.state(0).var.value() == 0:
                break
        self.assertEqual(dash.state(0).var.value(), 0)
        for frame in range(30):
            self.step(run, mei, genji, target_buttons=8 if frame == 0 else 0, target_pitch=0)
        self.assertEqual(dash.state(1).counter, 2)
        self.assertAlmostEqual(genji.mover.position[2] - stopped[2], 15, places=2)
        self.assertEqual(self.health(run, mei), 200)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 50)

    def test_mei_freeze_does_not_extend_dragonblade_or_queue_a_swing(self):
        endings = []
        for spray in (False, True):
            with self.subTest(spray=spray):
                run, mei, genji, _ = self.mei_scene()
                self.draw(run, genji, 390)
                fight = run.match.combat
                blade = self.blade(genji)
                for _ in range(180):
                    frozen = fight.is_stunned(genji)
                    self.step(run, mei, genji, cast=1 if spray and not frozen else 0,
                              target_buttons=1 if frozen else 0, target_pitch=0)
                    if not blade.flags & 1:
                        endings.append(run.frame)
                        break
                self.assertFalse(blade.flags & 1)
                self.assertEqual(fight.is_stunned(genji), spray)
                self.assertEqual(self.health(run, mei), 250)
                if spray:
                    deadline = fight.frost_hits[genji.body].frozen_until
                    while run.frame < deadline - 1:
                        self.step(run, mei, genji, target_buttons=1, target_pitch=0)
                    self.step(run, mei, genji, target_pitch=0)
                    self.assertFalse(fight.is_stunned(genji))
                for _ in range(15):
                    self.step(run, mei, genji, target_pitch=0)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)
                self.fire(run, genji, 1)
                self.assertEqual(self.health(run, mei), 163)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 87)
        self.assertEqual(len(endings), 2)
        self.assertEqual(endings[0], endings[1])

    def test_mei_icicle_kills_frozen_dragonblade_and_respawn_clears_frost(self):
        run, mei, genji, _ = self.mei_scene()
        fight = run.match.combat
        genji.spawn = ((0, 1, 0), 0)
        for frame in range(70):
            self.step(run, mei, genji, cast=2 if frame < 20 else 0, target_pitch=0)
            if self.health(run, genji) < 200:
                break
        self.assertEqual(self.health(run, genji), 125)
        self.draw(run, genji)
        old_body, old_script, blade = genji.body, genji.body_script, self.blade(genji)
        for _ in range(130):
            self.step(run, mei, genji, cast=1, target_pitch=0)
            if fight.is_stunned(genji):
                break
        self.assertTrue(fight.is_stunned(genji))
        self.assertEqual(self.health(run, genji), 42.5)
        self.assertTrue(blade.flags & 1)
        deadline = fight.frost_hits[old_body].frozen_until
        for frame in range(70):
            self.step(run, mei, genji, cast=2 if frame < 20 else 0,
                      target_buttons=1 | 8 | 16, target_pitch=0)
            if fight.is_dead(genji):
                break
        self.assertTrue(fight.is_dead(genji))
        self.assertLess(run.frame, deadline)
        self.assertEqual(genji.body, old_body)
        self.assertEqual(fight.vitals[old_body].killer, mei.body)
        self.assertNotIn(old_body, fight.frost_hits)
        self.assertEqual(old_script.component.entity_var(combat.FROST_PROGRESS).value(), 0)
        self.assertFalse(fight.is_stunned(genji))
        self.assertFalse(blade.flags & 1)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertEqual(len(genji.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertEqual(len(mei.client.messages(combat.KILL_NOTICE)), 1)

        for _ in range(650):
            self.step(run, mei, genji, target_pitch=0)
            if genji.body != old_body:
                break
        self.assertNotEqual(genji.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(fight.frost_progress(genji, tick=run.frame), 0)
        self.assertFalse(self.blade(genji).flags & 1)
        self.step(run, mei, genji, target_buttons=16, target_pitch=0)
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(deflect.state(10).active)

    def test_stun_refuses_weapon_skills_melee_jump_and_movement_then_fire_recovers(self):
        run, caster, genji = self.scene()
        genji.body_script.ult_seconds = 0
        self.cast(run, caster, genji)
        self.assertEqual(self.health(run, genji), 175)
        end = run.match.combat.stuns[genji.body]
        position = tuple(genji.mover.position)
        while run.frame < end - 1:
            self.step(run, caster, genji, target_buttons=1 | 2 | 8 | 16 | 32 | 0x40 | 0x800,
                      forward=127)
            self.assertEqual(tuple(genji.mover.position), position)
            self.assertEqual(self.health(run, caster), 225)
            self.assertFalse(genji.body_script.component.held)
        for _ in range(60):
            self.step(run, caster, genji, target_buttons=1)
        self.assertLess(self.health(run, caster), 225)
        self.assertFalse(run.match.combat.is_stunned(genji))

    def test_flashbang_preserves_reload_but_suppresses_fire(self):
        for delay, impact_ammo in ((5, 27), (57, 30)):
            with self.subTest(delay=delay):
                run, caster, genji = self.scene()
                weapon = next(i for i in genji.body_script.component.instances.values()
                              if i.graph.index == 0x0304)
                for frame in range(60):
                    self.step(run, caster, genji, target_buttons=1 if frame == 0 else 0,
                              target_pitch=0)
                self.assertEqual(self.health(run, caster), 138)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.step(run, caster, genji, target_buttons=0x400)
                reload_start = run.frame
                for _ in range(delay):
                    self.step(run, caster, genji)
                self.cast(run, caster, genji)
                self.assertEqual(self.health(run, genji), 175)
                self.assertEqual(weapon.vars[53].value(), impact_ammo)

                end = run.match.combat.stuns[genji.body]
                while run.frame < end - 1:
                    self.step(run, caster, genji, target_buttons=1, target_pitch=0)
                    self.assertEqual(weapon.state(4).counter, 1)
                self.assertTrue(run.match.combat.is_stunned(genji))
                self.assertEqual(weapon.vars[53].value(), 30)
                self.assertEqual(self.health(run, caster), 138)
                while run.frame < max(end, reload_start + 94):
                    self.step(run, caster, genji)
                self.step(run, caster, genji, target_buttons=1, target_pitch=0)
                for _ in range(60):
                    self.step(run, caster, genji, target_pitch=0)
                self.assertEqual(weapon.state(4).counter, 2)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(self.health(run, caster), 51)

    def test_flashbang_preserves_both_automatic_reload_phases(self):
        for delay, impact_ammo, wait_state in ((55, 0, 54), (100, 30, 53)):
            with self.subTest(wait_state=wait_state):
                run, caster, genji = self.scene()
                weapon = next(i for i in genji.body_script.component.instances.values()
                              if i.graph.index == 0x0304)
                for _ in range(500):
                    run.step({genji: (-32768, 0, 2)})
                    if weapon.vars[53].value() == 0:
                        break
                self.assertEqual(weapon.vars[53].value(), 0)
                self.assertEqual(self.health(run, caster), 225)
                for _ in range(delay):
                    self.step(run, caster, genji)

                self.cast(run, caster, genji)
                fight = run.match.combat
                self.assertEqual(self.health(run, genji), 175)
                self.assertTrue(weapon.state(wait_state).active)
                self.assertEqual(weapon.vars[53].value(), impact_ammo)
                end = fight.stuns[genji.body]
                while run.frame < end - 1:
                    self.step(run, caster, genji, target_buttons=1, target_pitch=0)
                    self.assertEqual(weapon.state(4).counter, 0)
                self.assertTrue(fight.is_stunned(genji))
                self.assertEqual(weapon.vars[53].value(), 30)
                self.assertEqual(self.health(run, caster), 225)

                for _ in range(100):
                    self.step(run, caster, genji)
                    if not fight.is_stunned(genji) and not weapon.state(53).active:
                        break
                self.assertFalse(fight.is_stunned(genji))
                self.assertFalse(weapon.state(53).active)
                self.assertEqual(weapon.state(4).counter, 0)
                self.step(run, caster, genji, target_buttons=1, target_pitch=0)
                for _ in range(60):
                    self.step(run, caster, genji, target_pitch=0)
                self.assertEqual(weapon.state(4).counter, 1)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(self.health(run, caster), 138)

    def test_rear_flash_interrupts_active_deflect_and_starts_authored_cooldown(self):
        run, caster, genji = self.scene(-3)
        for frame in range(5):
            run.step({genji: (0, 0, 16 if frame == 0 else 0)})
        instance = next(i for i in genji.body_script.component.instances.values()
                        if i.graph.index == 0x03A3)
        self.assertTrue(instance.state(10).active)
        self.cast(run, caster, genji)
        self.assertFalse(instance.state(10).active)
        for _ in range(70):
            self.step(run, caster, genji, target_buttons=16)
        self.assertGreater(instance.state(0).var.value(), 0)
        self.assertFalse(instance.state(10).active)
        self.assertEqual(instance.state(10).counter, 1)
        self.assertFalse(run.match.combat.is_stunned(genji))
        for _ in range(540):
            self.step(run, caster, genji)
            if instance.state(0).var.value() == 0:
                break
        self.assertEqual(instance.state(0).var.value(), 0)
        self.assertFalse(instance.state(10).active)
        self.assertEqual(instance.state(10).counter, 1)
        self.step(run, caster, genji, target_buttons=16)
        self.assertTrue(instance.state(10).active)
        self.assertEqual(instance.state(10).counter, 2)

        self.assertTrue(run.match.switch_hero(caster, heroes()[SOLDIER]))
        caster.mover.position[:] = [0, 1, -3]
        for frame in range(24):
            self.step(run, caster, genji, cast=1 if frame == 1 else 0)
            if self.health(run, caster) < 200:
                break
        self.assertEqual(self.health(run, genji), 175)
        self.assertEqual(self.health(run, caster), 160)
        hits = genji.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [40])

    def test_lethal_rear_flash_retires_deflect_and_control_before_respawn(self):
        run, caster, genji = self.scene(-3)
        genji.spawn = ((0, 1, 0), 0)
        for frame in range(5):
            run.step({genji: (0, 0, 16 if frame == 0 else 0)})
        old_script = genji.body_script
        old_body = genji.body
        old_deflect = next(i for i in old_script.component.instances.values()
                           if i.graph.index == 0x03A3).state(10)
        self.assertTrue(old_deflect.active)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is genji)
        vitals = fight.vitals_of(target)
        # Isolate Flashbang's own lethal damage.
        vitals.damage(176)
        fight._sync_body_health(genji, vitals)
        for frame in range(30):
            run.step({genji: (0, 0, 0), caster: (0, 0, 16 if frame == 0 else 0)})
            self.assertNotIn(old_body, fight.stuns)
            if fight.is_dead(genji):
                break
            self.assertTrue(old_deflect.active)
        self.assertTrue(fight.is_dead(genji))
        self.assertEqual(vitals.killer, caster.body)
        self.assertFalse(old_deflect.active)

        for _ in range(650):
            run.step({})
            if genji.body != old_body:
                break
        self.assertNotEqual(genji.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertEqual(self.health(run, genji), 200)
        self.assertFalse(fight.is_stunned(genji))
        deflect = next(i for i in genji.body_script.component.instances.values()
                       if i.graph.index == 0x03A3)
        self.assertEqual(deflect.state(0).var.value(), 0)
        self.step(run, caster, genji, target_buttons=16)
        self.assertTrue(deflect.state(10).active)
        self.assertEqual(deflect.state(10).counter, 1)
        self.assertTrue(run.match.switch_hero(caster, heroes()[SOLDIER]))
        caster.mover.position[:] = [0, 1, -3]
        for frame in range(24):
            self.step(run, caster, genji, cast=1 if frame == 1 else 0)
            if self.health(run, caster) < 200:
                break
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, caster), 160)

    def blade(self, genji):
        return next(i.state(3) for i in genji.body_script.component.instances.values()
                    if i.graph.index == 0x0317)

    def draw(self, run, genji, frames=80):
        genji.body_script.ult_seconds = 0
        genji.body_script.set_ult_charge(genji.body_script.component.entity_var(338).value())
        for frame in range(frames):
            run.step({genji: (0, 0, 32 if frame == 0 else 0)})
        self.assertTrue(self.blade(genji).flags & 1)

    def test_flash_stops_strike_displacement_without_resuming_after_expiry(self):
        for delay in (0, 3, 6, 9):
            with self.subTest(delay=delay):
                run, caster, genji = self.scene(6)
                stopped = None
                for frame in range(90):
                    self.step(run, caster, genji, cast=16 if frame == 0 else 0,
                              target_buttons=8 if frame == delay else 0, target_pitch=0)
                    if run.match.combat.is_stunned(genji) and stopped is None:
                        stopped = tuple(genji.mover.position)
                    if stopped is not None:
                        self.assertEqual(tuple(genji.mover.position), stopped)
                self.assertIsNotNone(stopped)
                self.assertGreater(stopped[2], 0)
                self.assertLess(stopped[2], 6)
                self.assertEqual(self.health(run, genji), 175)
                self.assertEqual(self.health(run, caster), 225)
                dash = next(i for i in genji.body_script.component.instances.values()
                            if i.graph.index == 0x01DB)
                self.assertFalse(dash.state(1).active)
                self.assertGreater(dash.state(0).var.value(), 0)
                self.assertFalse(run.match.combat.is_stunned(genji))
                self.step(run, caster, genji, target_buttons=8, target_pitch=0)
                self.assertFalse(dash.state(1).active)
                self.assertEqual(tuple(genji.mover.position), stopped)
                for _ in range(510):
                    self.step(run, caster, genji, target_pitch=0)
                    self.assertEqual(tuple(genji.mover.position), stopped)
                    if dash.state(0).var.value() == 0:
                        break
                self.assertEqual(dash.state(0).var.value(), 0)
                self.assertEqual(self.health(run, caster), 225)

                for frame in range(30):
                    self.step(run, caster, genji, target_buttons=8 if frame == 0 else 0,
                              target_pitch=0)
                self.assertGreater(genji.mover.position[2], 6)
                self.assertEqual(self.health(run, caster), 175)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 50)
                self.assertGreater(dash.state(0).var.value(), 0)

    def test_flash_preserves_blade_during_draw_and_active_phase_but_blocks_swings(self):
        for frames in (1, 80):
            with self.subTest(draw_frames=frames):
                run, caster, genji = self.scene()
                self.draw(run, genji, frames)
                self.cast(run, caster, genji)
                end = run.match.combat.stuns[genji.body]
                while run.frame < end - 1:
                    self.step(run, caster, genji, target_buttons=1 | 32)
                    self.assertTrue(self.blade(genji).flags & 1)
                    self.assertEqual(self.health(run, caster), 225)
                for _ in range(65):
                    self.step(run, caster, genji, target_buttons=1)
                self.assertLess(self.health(run, caster), 225)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
                self.assertTrue(self.blade(genji).flags & 1)

    def test_stun_does_not_extend_blade_or_restart_it_after_natural_end(self):
        endings = []
        for cast in (False, True):
            run, caster, genji = self.scene()
            self.draw(run, genji, 470)
            # Place the same real Flashbang late in the ultimate's timer.
            for frame in range(160):
                self.step(run, caster, genji, cast=16 if cast and frame == 0 else 0)
                if not self.blade(genji).flags & 1:
                    endings.append(run.frame)
                    if cast:
                        self.assertTrue(run.match.combat.is_stunned(genji))
                    break
            else:
                self.fail("Dragonblade did not end")
            # Tap primary at the end boundary, then release it before recovery.
            for frame in range(60):
                buttons = 32 | (1 if cast and frame == 0 else 0)
                self.step(run, caster, genji, target_buttons=buttons)
                self.assertFalse(self.blade(genji).flags & 1)
                self.assertEqual(self.health(run, caster), 225)
            self.assertFalse(run.match.combat.is_stunned(genji))
            self.assertFalse(genji.client.messages(combat.HIT))
            self.assertFalse(run.match.combat.rockets)
            self.fire(run, genji, 1)
            self.assertEqual(self.health(run, caster), 138)
            self.assertEqual(genji.body_script.component.entity_var(342).value(), 87)
            hits = genji.client.messages(combat.HIT)
            self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29, 29, 29])
        self.assertEqual(endings[0], endings[1])

    def test_death_and_body_replacement_end_the_preserved_blade_and_control(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                run, caster, genji = self.scene()
                self.draw(run, genji)
                if not replacement:
                    # Isolate a lethal Peacekeeper follow-up within the stun window.
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is genji)
                    vitals = fight.vitals_of(target)
                    vitals.damage(110)
                    fight._sync_body_health(genji, vitals)
                    genji.spawn = ((0, 1, 0), 0)
                self.cast(run, caster, genji)
                old_body, old_blade = genji.body, self.blade(genji)
                self.assertTrue(old_blade.flags & 1)
                if replacement:
                    run.match.switch_hero(genji, genji.hero)
                    self.assertNotEqual(genji.body, old_body)
                    self.assertFalse(self.blade(genji).flags & 1)
                else:
                    self.assertEqual(self.health(run, genji), 65)
                    old_script = genji.body_script
                    while run.match.combat.is_stunned(genji):
                        self.step(run, caster, genji, cast=1)
                        if run.match.combat.is_dead(genji):
                            break
                    self.assertTrue(run.match.combat.is_dead(genji))
                    self.assertEqual(vitals.killer, caster.body)
                    self.assertFalse(old_blade.flags & 1)
                    for _ in range(650):
                        self.step(run, caster, genji)
                        if genji.body != old_body:
                            break
                    self.assertNotEqual(genji.body, old_body)
                    self.assertTrue(old_script.retired)
                    self.assertFalse(self.blade(genji).flags & 1)
                    self.assertFalse(run.match.combat.is_stunned(genji))
                    self.assertEqual(self.health(run, genji), 200)
                    self.fire(run, genji, 1)
                    self.assertEqual(self.health(run, caster), 138)
                self.assertNotIn(old_body, run.match.combat.stuns)


if __name__ == "__main__":
    unittest.main()
