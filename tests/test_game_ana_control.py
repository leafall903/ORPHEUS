"""Actual enemy Flashbang versus Ana and real-channel sleep interruption."""
import unittest
from unittest.mock import patch

import test_game_ana as ana_fixture
import test_game_ana_deflect as deflect_fixture
import test_game_ana_nano as nano_fixture
import test_game_ana_sleep as sleep_fixture
from test_game_combat import aim

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script import graph, nodes


class AnaControlTests(unittest.TestCase):
    def fight(self):
        run, ana, caster, entry = ana_fixture.AnaRifleTests().setup_fight(enemy=True, distance=3)
        self.assertTrue(run.match.switch_hero(caster, find_hero("0x02E0000000000042")))
        caster.mover.position = list(entry.feet)
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        for _ in range(25):
            run.step({})
        return run, ana, caster

    @staticmethod
    def entry(run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)

    @staticmethod
    def direction(run, actor, target):
        return aim(run.match.combat._eye(actor),
                   (target.mover.position[0], target.mover.position[1] + 1, target.mover.position[2]))

    def step(self, run, ana, caster, *, own=0, flash=0, forward=0):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            if player is ana:
                yaw, pitch = self.direction(run, ana, caster)
                command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=own, forward=forward)
            elif player is caster:
                yaw, pitch = self.direction(run, caster, ana)
                command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=flash)
            else:
                command = Command(run.frame)
            player.take_commands([command])

    def cast(self, run, ana, caster, *, own=0):
        self.step(run, ana, caster, own=own, flash=16)
        for _ in range(50):
            if run.match.combat.is_stunned(ana) or run.match.combat.is_dead(ana):
                return
            self.step(run, ana, caster, own=own)
        self.fail("actual enemy Flashbang never hit Ana")

    def test_actual_flashbang_damage_deadline_and_scope_channel_release(self):
        run, ana, caster = self.fight()
        for _ in range(35):
            self.step(run, ana, caster, own=2)
        abilities = [s for i in ana.body_script.component.instances.values()
                     for s in i.states.values() if isinstance(s, nodes.Ability)]
        rifle = next(i for i in ana.body_script.component.instances.values() if i.graph.index == 0x0B94)
        self.assertIn(29, ana.body_script.component.held)
        self.assertTrue(rifle.states[56].active)
        self.cast(run, ana, caster, own=2)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(self.entry(run, ana)).total, 175)
        self.assertEqual(fight.stuns[ana.body] - fight.tick, 50)
        self.assertTrue(fight.is_stunned(ana, tick=fight.stuns[ana.body] - 1))
        self.assertFalse(fight.is_stunned(ana, tick=fight.stuns[ana.body]))
        self.assertFalse(ana.body_script.component.held)
        self.assertFalse(rifle.states[56].active)
        self.assertTrue(all(not s.presses and not s.flags & nodes.Ability.RUNNING for s in abilities))

    def test_stunned_ana_cannot_move_fire_or_bypass_with_future_input_then_recovers(self):
        run, ana, caster = self.fight()
        self.cast(run, ana, caster)
        fight = run.match.combat
        before = tuple(ana.mover.position)
        target = self.entry(run, caster)
        health = fight.vitals_of(target).total
        end = fight.stuns[ana.body]
        while run.frame < end - 1:
            self.step(run, ana, caster, own=1 | 2 | 8 | 16 | 32 | 0x40 | 0x800, forward=127)
            self.assertEqual(tuple(ana.mover.position), before)
            self.assertEqual(fight.vitals_of(target).total, health)
            self.assertFalse(ana.body_script.component.held)
        # Check the future-frame gate in a separate fresh body clock so normal
        # post-expiry commands are not made stale by this deliberate jump.
        for _ in range(90):
            self.step(run, ana, caster, own=1, forward=127)
        self.assertFalse(fight.is_stunned(ana))
        self.assertNotEqual(tuple(ana.mover.position), before)
        self.assertLess(fight.vitals_of(target).total, health)
        run, ana, caster = self.fight()
        self.cast(run, ana, caster)
        before = tuple(ana.mover.position)
        ana.take_commands([Command(run.frame + 100, forward=127, buttons=1 | 8 | 16 | 32)])
        self.assertEqual(tuple(ana.mover.position), before)
        self.assertEqual(ana.body_script.buttons, 0)

    def test_wall_and_ally_reject_flashbang_damage_and_control(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, ana, caster = self.fight()
                if wall:
                    ana_fixture.set_static_wall(run, ana, 1.5)
                else:
                    caster.team = ana.team
                self.step(run, ana, caster, flash=16)
                for _ in range(100):
                    self.step(run, ana, caster)
                self.assertEqual(run.match.combat.vitals_of(self.entry(run, ana)).total, 200)
                self.assertFalse(run.match.combat.is_stunned(ana))

    def test_lethal_flashbang_and_body_replacement_leave_no_stun(self):
        run, ana, caster = self.fight()
        fight = run.match.combat
        fight.vitals_of(self.entry(run, ana)).damage(175)
        self.cast(run, ana, caster)
        self.assertTrue(fight.is_dead(ana))
        self.assertNotIn(ana.body, fight.stuns)
        run, ana, caster = self.fight()
        self.cast(run, ana, caster)
        old_body = ana.body
        self.assertTrue(run.match.switch_hero(ana, find_hero("Soldier")))
        self.assertNotIn(old_body, run.match.combat.stuns)
        self.assertFalse(run.match.combat.is_stunned(ana))

    def test_flashbang_interrupts_nano_before_native_release(self):
        helper = nano_fixture.AnaNanoTests()
        run, ana, ally, caster = helper.fight(ally_hero="Roadhog")
        self.assertTrue(run.match.switch_hero(caster, find_hero("0x02E0000000000042")))
        x, y, z = ana.mover.position
        caster.mover.position = [x + 3, y, z]
        fight = run.match.combat
        target = helper.target(fight, ally)
        fight.vitals_of(target).damage(400)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        self.step(run, ana, caster, flash=16)
        for _ in range(40):
            if any(r.shooter is caster for r in fight.rockets):
                break
            self.step(run, ana, caster)
        self.assertTrue(any(r.shooter is caster for r in fight.rockets))
        direction = helper.direction(fight, ana, ally)
        run.step({ana: (*direction, 32)})
        for _ in range(25):
            run.step({})
        self.assertTrue(fight.is_stunned(ana))
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(fight.vitals_of(target).nano_until, 0)
        self.assertEqual(ana.body_script.component.vars[342].value(), 0)

    def test_sleep_stops_actual_genji_deflect_channel(self):
        helper = deflect_fixture.AnaDeflectTests()
        run, ana, genji, target, _own, forward = helper.fight(facing=False)
        helper.attack(run, ana, genji, target, forward, 8)
        self.assertTrue(run.match.combat.is_asleep(genji))
        state = next(i.states[10] for i in genji.body_script.component.instances.values()
                     if i.graph.index == 0x03A3)
        self.assertFalse(state.active)
        self.assertFalse(genji.body_script.component.held)

    def test_released_grenade_continues_after_flashbang_interrupts_ana(self):
        helper = nano_fixture.AnaNanoTests()
        run, ana, target, caster = helper.fight()
        self.assertTrue(run.match.switch_hero(caster, find_hero("0x02E0000000000042")))
        x, y, z = ana.mover.position
        caster.mover.position = [x + 3, y, z]
        target.team = 1
        target.mover.position = [x, y, z + 20]
        direction = ana_fixture.grenade_aim(
            run, ana, (target.mover.position[0], target.mover.position[1] + 1, target.mover.position[2]))
        run.step({ana: (*direction, 16)})
        fight = run.match.combat
        for _ in range(40):
            if any(r.shooter is ana for r in fight.rockets):
                break
            run.step({})
        self.assertTrue(any(r.shooter is ana for r in fight.rockets))
        self.cast(run, ana, caster)
        self.assertTrue(fight.is_stunned(ana))
        self.assertTrue(any(r.shooter is ana for r in fight.rockets))
        for _ in range(90):
            run.step({})
        self.assertEqual(fight.vitals_of(self.entry(run, target)).total, 150)
        self.assertGreater(fight.vitals_of(self.entry(run, target)).healing_block_until, fight.now)

    def test_authored_long_duration_branch_uses_actual_flashbang(self):
        run, ana, caster = self.fight()
        values = dict(graph.mode_variables(run.match.game_map.mode_guid) or {})
        values[combat.FLASHBANG_MODE_FLAG] = True
        with patch.object(graph, "mode_variables", return_value=values):
            self.cast(run, ana, caster)
        fight = run.match.combat
        self.assertEqual(fight.stuns[ana.body] - fight.tick, 107)

    def test_second_actual_enemy_flashbang_refreshes_without_adding_full_duration(self):
        run, ana, second, caster = nano_fixture.AnaNanoTests().fight()
        x, y, z = ana.mover.position
        for attacker, point in ((caster, [x + 3, y, z]), (second, [x, y, z + 3])):
            self.assertTrue(run.match.switch_hero(attacker, find_hero("0x02E0000000000042")))
            attacker.team = 1
            attacker.mover.position = point
        self.cast(run, ana, caster)
        fight = run.match.combat
        first_end = fight.stuns[ana.body]
        for _ in range(10):
            self.step(run, ana, second)
        self.step(run, ana, second, flash=16)
        for _ in range(40):
            if fight.vitals_of(self.entry(run, ana)).total == 150:
                break
            self.step(run, ana, second)
        self.assertEqual(fight.vitals_of(self.entry(run, ana)).total, 150)
        self.assertGreater(fight.stuns[ana.body], first_end)
        self.assertEqual(fight.stuns[ana.body] - fight.tick, 50)

    def test_flashbang_damage_wakes_sleep_then_applies_its_own_stun(self):
        run, ana, caster, old_entry = ana_fixture.AnaRifleTests().setup_fight(enemy=True, distance=3)
        self.assertTrue(run.match.switch_hero(caster, find_hero("Ana")))
        caster.mover.position = list(old_entry.feet)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        sleep_fixture.AnaSleepTests().cast(run, caster, self.entry(run, ana))
        self.assertTrue(fight.is_asleep(ana))
        self.assertTrue(run.match.switch_hero(caster, find_hero("0x02E0000000000042")))
        caster.mover.position = list(old_entry.feet)
        for _ in range(25):
            run.step({})
        self.cast(run, ana, caster)
        self.assertFalse(fight.is_asleep(ana))
        self.assertTrue(fight.is_stunned(ana))
        self.assertEqual(fight.vitals_of(self.entry(run, ana)).total, 170)

    def test_early_flashbang_cannot_shorten_sleep_below_entry_floor(self):
        run, ana, caster, old_entry = ana_fixture.AnaRifleTests().setup_fight(enemy=True, distance=3)
        self.assertTrue(run.match.switch_hero(caster, find_hero("Ana")))
        caster.mover.position = list(old_entry.feet)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        direction = aim(fight._eye(caster), fight._eye(ana))
        run.step({caster: (*direction, 8)})
        for _ in range(55):
            if fight.is_asleep(ana):
                break
            run.step({caster: (*direction, 0)})
        self.assertTrue(fight.is_asleep(ana))
        pool = fight.vitals_of(self.entry(run, ana))
        floor = pool.sleep_wake_floor
        self.assertTrue(run.match.switch_hero(caster, find_hero("0x02E0000000000042")))
        caster.mover.position = list(old_entry.feet)
        for _ in range(15):
            run.step({})
        self.step(run, ana, caster, flash=16)
        for _ in range(50):
            if pool.total == 170:
                break
            self.step(run, ana, caster)
        self.assertEqual(pool.total, 170)
        self.assertLess(fight.now + .8, floor)
        self.assertEqual(pool.sleep_until, floor)
        self.assertTrue(fight.is_asleep(ana))
        self.assertFalse(fight.is_stunned(ana))
