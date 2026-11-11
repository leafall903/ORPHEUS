"""Actual Sombra inputs and receiver graphs against Reaper."""

import unittest
from dataclasses import replace

import test_game_reaper as reaper_tests
import test_reaper_control as control_tests
from test_game_combat import aim

from ow174.game import combat, reaper, sombra
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class ReaperSombraTests(unittest.TestCase):
    def scene(self, distance=4, *, names=("Reaper", "Sombra")):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(caster, heroes()[sombra.SOMBRA]))
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x, y, z + distance)
        caster.mover.body = None
        caster.body_script.ult_seconds = 0
        for _ in range(30):
            self.step(run, owner, caster)
        fight = run.match.combat
        fight.vitals_of(next(t for t in fight._targets() if t.owner is caster))
        return run, owner, caster

    def step(self, run, owner, caster, *, hack=0, action=0):
        fight = run.match.combat
        x, y, z = owner.mover.position
        caster_aim = aim(fight._eye(caster), (x, y + 1, z))
        x, y, z = caster.mover.position
        owner_aim = aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (*owner_aim, action), caster: (*caster_aim, hack)})

    def land(self, run, owner, caster, button=2):
        for offset in range(75):
            self.step(run, owner, caster, hack=button if button == 2 or offset == 0 else 0)
            if sombra.locked(owner):
                return
        self.fail("actual Sombra input did not apply 0B1F")

    def test_actual_hack_masks_reaper_skills_but_leaves_shell_and_reaping(self):
        run, owner, caster = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(100)
        self.land(run, owner, caster)
        receiver = sombra.instance_for(owner, 0xB1F)
        self.assertEqual(receiver.states[58].ms, 5000)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(own.total, 150)
        owner.body_script.set_ult_charge(2100)
        self.step(run, owner, caster, action=8 | 16 | 32)
        self.assertFalse(reaper.intangible(owner))
        self.assertNotIn(owner.body, fight.reaper_shadows)
        self.assertNotIn(owner.body, fight.reaper_blossoms)
        self.assertEqual(owner.body_script.component.vars[342].value(), 2100)
        before = fight.vitals[caster.body].total
        self.step(run, owner, caster, action=1)
        self.assertLess(fight.vitals[caster.body].total, before)
        self.assertGreater(own.total, 150)
        self.assertTrue(sombra.locked(owner))
        start = tuple(owner.mover.position)
        for _ in range(12):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame, forward=127)])
        self.assertNotEqual(tuple(owner.mover.position), start)
        self.assertTrue(sombra.locked(owner))
        for _ in range(325):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(sombra.locked(owner))
        self.step(run, owner, caster, action=8)
        self.assertTrue(reaper.intangible(owner))

    def test_actual_hack_allows_quick_melee_reaping_and_manual_reload(self):
        run, owner, caster = self.scene(distance=1.5)
        fight = run.match.combat
        own = fight.vitals[owner.body]
        enemy = fight.vitals[caster.body]
        own.damage(100)
        self.land(run, owner, caster)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x19)
        self.step(run, owner, caster, action=1)
        self.assertEqual(weapon.vars[53].value(), 7)
        for _ in range(40):
            self.step(run, owner, caster)
        self.assertTrue(sombra.locked(owner))
        before, health = enemy.total, own.total
        self.step(run, owner, caster, action=0x800)
        for _ in range(40):
            self.step(run, owner, caster)
        self.assertTrue(sombra.locked(owner))
        self.assertEqual(enemy.total, before - 30)
        self.assertAlmostEqual(own.total, health + 10.5, places=4)
        self.step(run, owner, caster, action=0x400)
        self.assertEqual(weapon.vars[53].value(), 7)
        for _ in range(110):
            self.step(run, owner, caster)
        self.assertTrue(sombra.locked(owner))
        self.assertEqual(weapon.vars[53].value(), 8)
        self.assertEqual(enemy.total, before - 30)

    def test_actual_emp_interrupts_blossom_consumes_charge_and_stops_pulses(self):
        run, owner, caster = self.scene(distance=6)
        fight = run.match.combat
        component = caster.body_script.component
        cost = component.entity_var(ULT_COST).value()
        caster.body_script.set_ult_charge(cost)
        owner.body_script.set_ult_charge(2100)
        self.step(run, owner, caster, action=32)
        channel = fight.reaper_blossoms[owner.body]
        self.assertFalse(channel["ended"])
        self.land(run, owner, caster, 32)
        self.assertTrue(channel["ended"])
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        before = fight.vitals[caster.body].total
        for _ in range(200):
            self.step(run, owner, caster)
        self.assertEqual(fight.vitals[caster.body].total, before)

    def test_actual_hack_from_outside_blossom_range_ends_channel(self):
        run, owner, caster = self.scene(distance=10)
        fight = run.match.combat
        enemy = fight.vitals[caster.body]
        before = enemy.total
        owner.body_script.set_ult_charge(2100)
        self.step(run, owner, caster, action=32)
        channel = fight.reaper_blossoms[owner.body]
        self.assertFalse(channel["ended"])
        self.assertEqual(enemy.total, before)
        self.land(run, owner, caster)
        self.assertTrue(channel["ended"])
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x, y, z + 4)
        caster.mover.body = None
        for _ in range(40):
            self.step(run, owner, caster)
        self.assertEqual(enemy.total, before)
        self.step(run, owner, caster, action=1)
        self.assertLess(enemy.total, before)
        self.assertTrue(sombra.locked(owner))

    def test_actual_shell_cancels_hack_windup_then_fresh_hack_succeeds(self):
        run, owner, caster = self.scene()
        self.step(run, owner, caster, hack=2)
        for _ in range(9):
            self.step(run, owner, caster, hack=2)
        wait = sombra.instance_for(caster, 0xB1B).states[54]
        self.assertTrue(wait.active)
        self.step(run, owner, caster, hack=2, action=1)
        self.assertFalse(wait.active)
        self.assertFalse(sombra.locked(owner))
        for _ in range(140):
            self.step(run, owner, caster)
        self.assertFalse(sombra.locked(owner))
        self.land(run, owner, caster)

    def test_actual_blossom_cancels_hack_windup_then_fresh_hack_succeeds(self):
        run, owner, caster = self.scene()
        fight = run.match.combat
        enemy = fight.vitals[caster.body] = combat.Vitals(caster.body, 1000)
        owner.body_script.set_ult_charge(2100)
        for _ in range(10):
            self.step(run, owner, caster, hack=2)
        wait = sombra.instance_for(caster, 0xB1B).states[54]
        self.assertTrue(wait.active)
        self.step(run, owner, caster, hack=2, action=32)
        self.assertFalse(wait.active)
        self.assertFalse(sombra.locked(owner))
        self.assertEqual(enemy.total, 966)
        for _ in range(200):
            self.step(run, owner, caster)
        self.assertFalse(sombra.locked(owner))
        self.assertEqual(enemy.total, 490)
        self.land(run, owner, caster)
        self.assertTrue(sombra.locked(owner))

    def test_actual_hack_cooldown_refusal_then_reapplication(self):
        run, owner, caster = self.scene()
        self.land(run, owner, caster)
        receiver = sombra.instance_for(owner, 0xB1F)
        ability = sombra.instance_for(caster, 0xB1B).states[53]
        self.assertGreater(ability.remaining(), 7)
        self.step(run, owner, caster)
        for _ in range(40):
            self.step(run, owner, caster, hack=2)
        self.assertIs(sombra.instance_for(owner, 0xB1F), receiver)
        for _ in range(550):
            self.step(run, owner, caster)
            if ability.remaining() == 0:
                break
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(sombra.locked(owner))
        self.land(run, owner, caster)
        self.assertIsNot(sombra.instance_for(owner, 0xB1F), receiver)

    def test_actual_wraith_rejects_hack_and_emp(self):
        run, owner, caster = self.scene()
        fight = run.match.combat
        self.step(run, owner, caster, action=8)
        self.assertTrue(reaper.intangible(owner))
        for _ in range(55):
            self.step(run, owner, caster, hack=2)
        self.assertFalse(sombra.locked(owner))
        component = caster.body_script.component
        caster.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
        self.step(run, owner, caster, hack=32)
        for _ in range(65):
            self.step(run, owner, caster)
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(sombra.locked(owner))
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_emp_resolves_shadow_immunity_boundary(self):
        for immune in (False, True):
            with self.subTest(immune=immune):
                run, owner, caster = self.scene()
                fight = run.match.combat
                component = caster.body_script.component
                caster.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
                fixture = control_tests.ReaperControlTests()
                start = tuple(owner.mover.position)
                record = fixture.start_shadow(run, owner)
                if immune:
                    for _ in range(75):
                        if reaper.intangible(owner):
                            break
                        self.step(run, owner, caster)
                self.assertEqual(reaper.intangible(owner), immune)
                self.step(run, owner, caster, hack=32)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
                for _ in range(120):
                    self.step(run, owner, caster)
                self.assertEqual(sombra.locked(owner), not immune)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertNotEqual(record["target"], start)
                expected_position = record["target"] if immune else start
                for actual, expected in zip(owner.mover.position, expected_position, strict=True):
                    self.assertAlmostEqual(actual, expected, places=2)

    def test_actual_hack_interrupts_shadow_before_immunity(self):
        run, owner, caster = self.scene()
        fixture = control_tests.ReaperControlTests()
        start = tuple(owner.mover.position)
        record = fixture.start_shadow(run, owner)
        self.land(run, owner, caster)
        shadow = fixture.shadow(owner)
        self.assertFalse(shadow.state(19).active)
        self.assertFalse(shadow.state(32).active)
        self.assertGreater(shadow.vars[61].value(), 9)
        for _ in range(100):
            self.step(run, owner, caster)
        self.assertEqual(tuple(owner.mover.position), start)
        self.assertNotEqual(start, record["target"])

    def test_actual_shadow_immunity_breaks_hack_windup_and_fresh_hack_lands(self):
        self.check_shadow_immunity_hack(held=False)

    def test_actual_held_hack_starts_new_windup_after_shadow_immunity(self):
        self.check_shadow_immunity_hack(held=True)

    def check_shadow_immunity_hack(self, *, held):
        run, owner, caster = self.scene()
        fight = run.match.combat
        record = control_tests.ReaperControlTests().start_shadow(run, owner)
        for _ in range(48):
            self.step(run, owner, caster)
        self.assertFalse(reaper.intangible(owner))
        for _ in range(8):
            self.step(run, owner, caster, hack=2)
        wait = sombra.instance_for(caster, 0xB1B).states[54]
        self.assertTrue(wait.active)
        first = wait.counter
        for _ in range(15):
            self.step(run, owner, caster, hack=2)
            self.assertFalse(sombra.locked(owner))
            if not wait.active:
                break
        self.assertFalse(wait.active)
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(fight.vitals[owner.body].total, 250)
        if held:
            for _ in range(150):
                self.step(run, owner, caster, hack=2)
                if reaper.intangible(owner):
                    self.assertEqual(wait.counter, first)
                    self.assertFalse(sombra.locked(owner))
                if wait.counter > first:
                    break
            self.assertEqual(wait.counter, first + 1)
            self.assertTrue(wait.active)
            self.assertFalse(reaper.intangible(owner))
            self.assertFalse(sombra.locked(owner))
            self.assertTrue(record["moved"])
            self.land(run, owner, caster)
            return
        for _ in range(150):
            self.step(run, owner, caster)
        self.assertTrue(record["moved"])
        self.assertFalse(sombra.locked(owner))
        self.land(run, owner, caster)

    def test_actual_los_and_faction_reject_hack_and_emp(self):
        for kind in ("wall", "ally"):
            with self.subTest(kind=kind):
                run, owner, caster = self.scene()
                if kind == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    caster.team = owner.team
                else:
                    x, y, z = owner.mover.position
                    mid = z + 2
                    run.match.collision_world = World([
                        (x-30,y,z-30,x-30,y,z+30,x+30,y,z+30),
                        (x-30,y,z-30,x+30,y,z+30,x+30,y,z-30),
                        (x-5,y-1,mid,x+5,y-1,mid,x+5,y+5,mid),
                        (x-5,y-1,mid,x+5,y+5,mid,x-5,y+5,mid),
                    ], name="Sombra line of sight")
                for _ in range(65):
                    self.step(run, owner, caster, hack=2)
                self.assertFalse(sombra.locked(owner))
                component = caster.body_script.component
                caster.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
                self.step(run, owner, caster, hack=32)
                for _ in range(60):
                    self.step(run, owner, caster)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
                self.assertFalse(sombra.locked(owner))

    def test_actual_emp_removes_zen_shields_but_reaper_health_is_unchanged(self):
        run, owner, caster = self.scene(names=("Reaper", "Sombra", "Zenyatta"))
        zen = run.players[2]
        self.assertTrue(run.match.switch_hero(zen, heroes()[0x02E0000000000020]))
        x, y, z = owner.mover.position
        zen.mover.position[:] = (x + 3, y, z)
        zen.mover.body = None
        for _ in range(30):
            self.step(run, owner, caster)
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is zen))
        self.assertEqual(pool.now[combat.SHIELDS_POOL], 150)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.land(run, owner, caster, 32)
        self.assertTrue(sombra.locked(zen))
        self.assertEqual(pool.now[combat.SHIELDS_POOL], 0)
        self.assertEqual(pool.now[combat.HEALTH_POOL], 50)
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_dome_blocks_hack_then_emp_breaks_dome_and_locks_reaper(self):
        run, owner, caster = self.scene(distance=8, names=("Reaper", "Sombra", "Winston"))
        winston = run.players[2]
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = winston.team = 0
        caster.team = 1
        self.assertTrue(run.match.switch_hero(winston, heroes()[0x02E0000000000009]))
        x, y, z = owner.mover.position
        winston.mover.position[:] = (x + 2, y, z)
        winston.mover.body = None
        for _ in range(20):
            self.step(run, owner, caster)
        run.step({winston: (0, 0, 16)})
        for _ in range(45):
            self.step(run, owner, caster)
            if fight.winston_bubbles.actors:
                break
        dome, = fight.winston_bubbles.actors.values()
        self.assertEqual(dome.health, 700)
        for _ in range(60):
            self.step(run, owner, caster, hack=2)
        self.assertFalse(sombra.locked(owner))
        self.assertEqual(dome.health, 700)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.land(run, owner, caster, 32)
        self.assertEqual(dome.health, 0)
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_actual_emp_charge_refusal_and_hack_emp_ranges(self):
        for distance, in_range in ((14.8, True), (16.5, False)):
            with self.subTest(distance=distance):
                run, owner, caster = self.scene(distance=distance)
                component = caster.body_script.component
                cost = component.entity_var(ULT_COST).value()
                self.assertEqual(cost, 1400)
                caster.body_script.set_ult_charge(cost - 1)
                self.step(run, owner, caster, hack=32)
                for _ in range(55):
                    self.step(run, owner, caster)
                self.assertFalse(sombra.locked(owner))
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost - 1)
                for _ in range(65):
                    self.step(run, owner, caster, hack=2)
                self.assertEqual(sombra.locked(owner), in_range)
                for _ in range(335):
                    self.step(run, owner, caster)
                self.assertFalse(sombra.locked(owner))
                caster.body_script.set_ult_charge(cost)
                self.step(run, owner, caster, hack=32)
                for _ in range(55):
                    self.step(run, owner, caster)
                self.assertEqual(sombra.locked(owner), in_range)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
