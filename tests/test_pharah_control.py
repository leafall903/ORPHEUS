"""Actual Flashbang resources against Pharah; server control policy remains reconstructed."""

import unittest

import test_pharah_combat as primary
from test_game_combat import aim

from ow174.game.commands import Command
from ow174.game.content import heroes


class PharahControlTests(unittest.TestCase):
    health = primary.PharahCombatTests.health

    def step(self, run, pharah, caster, *, buttons=0, cast=0, forward=0):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        point = run.match.combat._eye(pharah)
        yaw, pitch = aim(run.match.combat._eye(caster), point)
        pharah.take_commands([Command(run.frame, yaw=-32768, buttons=buttons, forward=forward)])
        caster.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=cast)])

    def arena(self):
        run, pharah, caster = primary.PharahCombatTests.encounter(self)
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        caster.mover.position = [0, pharah.mover.position[1], 3]
        caster.mover.body = None
        for _ in range(30):
            self.step(run, pharah, caster)
        return run, pharah, caster

    def flash(self, run, pharah, caster, *, buttons=0):
        self.step(run, pharah, caster, buttons=buttons, cast=16)
        for _ in range(50):
            if run.match.combat.is_stunned(pharah):
                self.assertEqual(self.health(run, pharah).total, 175)
                return run.match.combat.stuns[pharah.body]
            self.step(run, pharah, caster, buttons=buttons)
        self.fail("Actual Flashbang input did not stun Pharah")

    def test_actual_flashbang_ends_barrage_and_new_shots_do_not_resume_after_expiry(self):
        run, pharah, caster = self.arena()
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, pharah, caster, buttons=32)
        self.assertTrue(script.ultimate_running())
        self.flash(run, pharah, caster)
        self.assertFalse(script.ultimate_running())
        volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
        count = volley.reported
        for _ in range(80):
            self.step(run, pharah, caster, buttons=32)
        self.assertFalse(run.match.combat.is_stunned(pharah))
        self.assertFalse(script.ultimate_running())
        self.assertEqual(volley.reported, count)
        self.assertFalse(any(mod.acts(run.frame) and isinstance(mod.value, tuple)
                             for mod in pharah.mover.mods.values()))
        # Aborting the skill stack must preserve its permanent button listener.
        self.step(run, pharah, caster)
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, pharah, caster, buttons=32)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(volley.counter, 2)

    def test_actual_flashbang_releases_hover_thrust_and_fresh_hover_recovers_after_expiry(self):
        run, pharah, caster = self.arena()
        for _ in range(15):
            self.step(run, pharah, caster, buttons=2)
        self.assertGreater(pharah.mover.velocity[1], 0)
        end = self.flash(run, pharah, caster, buttons=2)
        hover = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x25)
        fuel = hover.vars[87].value()
        for _ in range(20):
            self.step(run, pharah, caster, buttons=2)
        self.assertGreaterEqual(hover.vars[87].value(), fuel)
        self.assertLess(pharah.mover.velocity[1], 0)
        self.assertFalse(any(s.active and s.node.cls == "STU_A9C4FF83" for s in hover.states.values()))
        while run.frame < end:
            self.step(run, pharah, caster)
        for _ in range(20):
            self.step(run, pharah, caster, buttons=2)
        self.assertGreater(pharah.mover.velocity[1], 0)
        self.assertTrue(any(s.active and s.node.cls == "STU_A9C4FF83" for s in hover.states.values()))

    def test_actual_flashbang_blocks_flight_and_concussion_until_exclusive_expiry(self):
        run, pharah, caster = self.arena()
        end = self.flash(run, pharah, caster)
        instances = {i.graph.index: i for i in pharah.body_script.component.instances.values()}
        position = tuple(pharah.mover.position)
        while run.frame < end - 1:
            self.step(run, pharah, caster, buttons=8 | 16 | 2 | 0x800 | 1, forward=127)
        self.assertEqual(tuple(pharah.mover.position), position)
        self.assertEqual(instances[0x27].vars[61].value(), 0)
        self.assertEqual(instances[0x7B].vars[61].value(), 0)
        self.assertEqual(instances[0x23].vars[53].value(), 6)
        self.step(run, pharah, caster, buttons=8)
        self.assertFalse(run.match.combat.is_stunned(pharah))
        self.assertEqual(pharah.mover.velocity[1], 20)
        self.assertGreater(instances[0x27].vars[61].value(), 9.9)

    def test_flashbang_does_not_restart_manual_or_automatic_rocket_reload(self):
        def reloading(automatic):
            run, pharah, caster = self.arena()
            weapon = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x23)
            if automatic:
                for _ in range(420):
                    self.step(run, pharah, caster, buttons=1)
                    if (reload_wait := weapon.states.get(16)) is not None and reload_wait.active:
                        break
                self.assertEqual(weapon.vars[53].value(), 0)
                self.assertEqual(weapon.states[6].counter, 6)
            else:
                self.step(run, pharah, caster, buttons=1)
                for _ in range(65):
                    self.step(run, pharah, caster)
                self.step(run, pharah, caster, buttons=0x400)
                self.assertEqual(weapon.vars[53].value(), 5)
                self.assertEqual(weapon.states[6].counter, 1)
            self.assertTrue(weapon.states[16].active)
            return run, pharah, caster, weapon

        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                run, pharah, caster, weapon = reloading(automatic)
                baseline, free, spare, normal = reloading(automatic)
                fight = run.match.combat
                emitted = weapon.states[6].counter
                for frame in range(100):
                    self.step(run, pharah, caster, cast=16 if frame == 8 else 0)
                    self.step(baseline, free, spare)
                    self.assertEqual(weapon.vars[53].value(), normal.vars[53].value())
                    self.assertEqual(weapon.states[6].counter, emitted)
                    if weapon.vars[53].value() == 6:
                        break
                self.assertEqual(weapon.vars[53].value(), 6)
                self.assertTrue(fight.is_stunned(pharah))
                self.assertEqual(self.health(run, pharah).total, 175)
                for _ in range(130):
                    self.step(run, pharah, caster)
                self.assertEqual(weapon.states[6].counter, emitted)
                self.assertFalse(fight.is_stunned(pharah))
                self.step(run, pharah, caster, buttons=1)
                self.assertEqual(weapon.states[6].counter, emitted + 1)
                self.assertEqual(weapon.vars[53].value(), 5)

    def test_airborne_flashbang_stops_horizontal_jet_motion_and_preserves_ascent(self):
        run, pharah, caster = self.arena()
        baseline, free, spare = self.arena()
        fight = run.match.combat
        jet = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x27)
        stun_tick = None
        stopped_z = None
        for frame in range(30):
            buttons = 8 if frame == 3 else 0
            self.step(run, pharah, caster, buttons=buttons, cast=16 if frame == 0 else 0)
            self.step(baseline, free, spare, buttons=buttons)
            self.assertEqual(pharah.mover.position[1], free.mover.position[1])
            self.assertEqual(pharah.mover.velocity[1], free.mover.velocity[1])
            if stun_tick is None and fight.is_stunned(pharah):
                stun_tick = run.frame
                stopped_z = pharah.mover.position[2]
                self.assertGreater(pharah.mover.velocity[1], 0)
                self.assertTrue(pharah.mover.airborne)
                self.assertEqual(self.health(run, pharah).total, 175)
            if stun_tick is None:
                self.assertEqual(pharah.mover.position[2], free.mover.position[2])
            else:
                self.assertEqual(pharah.mover.position[2], stopped_z)
                self.assertEqual(pharah.mover.velocity[2], 0)
        self.assertIsNotNone(stun_tick)
        self.assertTrue(fight.is_stunned(pharah))
        self.assertLess(free.mover.position[2], pharah.mover.position[2])

        deadline = fight.stuns[pharah.body]
        cooldown = jet.vars[61].value()
        frame = run.frame
        while run.frame < deadline - 1:
            self.step(run, pharah, caster)
        self.step(run, pharah, caster, buttons=8)
        self.assertFalse(fight.is_stunned(pharah))
        self.assertGreater(jet.vars[61].value(), 8)
        self.assertAlmostEqual(jet.vars[61].value(), cooldown - (run.frame - frame) * .016, places=3)
        self.assertLess(pharah.mover.velocity[1], 20)
        for _ in range(640):
            if jet.vars[61].value() == 0:
                break
            self.step(run, pharah, caster)
        self.assertEqual(jet.vars[61].value(), 0)
        self.assertFalse(pharah.mover.airborne)
        self.step(run, pharah, caster, buttons=8)
        self.assertEqual(pharah.mover.velocity[1], 20)
        self.assertGreater(jet.vars[61].value(), 9.9)

    def test_flashbang_brackets_concussion_launch_and_cooldown_commit(self):
        for flash_frame, released in ((4, False), (5, True)):
            with self.subTest(flash_frame=flash_frame):
                run, pharah, caster = self.arena()
                fight = run.match.combat
                skill = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x7B)
                first_launch = first_stun = None
                original = None
                for frame in range(24):
                    self.step(run, pharah, caster, buttons=16 if frame == 0 else 0,
                              cast=16 if frame == flash_frame else 0)
                    if first_launch is None:
                        original = next((rocket for rocket in fight.rockets if rocket.shooter is pharah), None)
                        if original is not None:
                            first_launch = frame
                    if first_stun is None and fight.is_stunned(pharah):
                        first_stun = frame
                        if released:
                            self.assertIsNotNone(original)
                            self.assertIn(original, fight.rockets)
                self.assertEqual(first_stun, flash_frame + 6)
                self.assertEqual(first_launch, 10 if released else None)
                self.assertEqual(self.health(run, pharah).total, 175)
                self.assertEqual(skill.states[0].counter, int(released))
                self.assertEqual(skill.vars[61].value() > 0, released)

                end = fight.stuns[pharah.body]
                while run.frame < end + 12:
                    self.step(run, pharah, caster)
                self.assertFalse(fight.is_stunned(pharah))
                self.assertEqual(skill.states[0].counter, int(released))
                self.step(run, pharah, caster, buttons=16)
                for _ in range(16):
                    self.step(run, pharah, caster)
                self.assertEqual(skill.states[0].counter, 1)
                self.assertGreater(skill.vars[61].value(), 7)
                launched_again = any(rocket.shooter is pharah and rocket is not original for rocket in fight.rockets)
                self.assertEqual(launched_again, not released)

    def test_full_charge_barrage_is_rejected_during_stun_and_needs_input_at_expiry(self):
        for fresh in (False, True):
            with self.subTest(fresh=fresh):
                run, pharah, caster = self.arena()
                script = pharah.body_script
                charge = script.component.entity_var(338).value()
                script.set_ult_charge(charge)
                end = self.flash(run, pharah, caster)
                while run.frame < end - 1:
                    self.step(run, pharah, caster, buttons=32)
                    self.assertFalse(script.ultimate_running())
                    self.assertEqual(script.component.entity_var(342).value(), charge)
                self.step(run, pharah, caster, buttons=32 if fresh else 0)
                self.assertFalse(run.match.combat.is_stunned(pharah))
                self.assertEqual(script.ultimate_running(), fresh)
                self.assertEqual(script.component.entity_var(342).value(), 0 if fresh else charge)

    def test_enemy_primary_after_roll_uses_refill_from_partial_and_empty_magazine(self):
        # Exercises actual weapon/refill/input against Pharah. Roll movement and
        # native inter-object arbitration order remain separate acceptance items.
        for empty in (False, True):
            with self.subTest(empty=empty):
                run, pharah, caster = self.arena()
                weapon = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x1D2)
                # Fire away from Pharah to create the real magazine state.
                run.step({caster: (0, 0, 1)})
                if empty:
                    for _ in range(300):
                        if weapon.vars[53].value() == 0:
                            break
                        run.step({caster: (0, 0, 1)})
                    self.assertEqual(weapon.vars[53].value(), 0)
                else:
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(weapon.vars[53].value(), 5)
                self.assertEqual(self.health(run, pharah).total, 200)
                run.step({})
                run.step({caster: (0, 0, 8)})
                for _ in range(24):
                    self.step(run, pharah, caster, cast=1)
                self.assertEqual(self.health(run, pharah).total, 200)
                for _ in range(10):
                    self.step(run, pharah, caster, cast=1)
                    if self.health(run, pharah).total < 200:
                        break
                self.assertEqual(self.health(run, pharah).total, 130)
                self.assertEqual(weapon.vars[53].value(), 5)


if __name__ == "__main__":
    unittest.main()
