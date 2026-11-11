"""Mei against actual Flashbang input; native full status hosting remains open."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_mei_blizzard as blizzard
import test_mei_combat as fixtures
import test_mei_frost as frost
import test_mei_wall as walls

from ow174.game import combat, mei
from ow174.game.content import heroes


class MeiControlTests(unittest.TestCase):
    def scene(self):
        run, owner, caster, _target = fixtures.MeiCombatTests().duel()
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(caster, (x, y, z + 3.0))
        for _ in range(30):
            run.step({})
        return run, owner, caster

    def cast(self, run, owner, caster):
        point = tuple(owner.mover.position)
        aim = fixtures.aim(run.match.combat._eye(caster), (point[0], point[1] + 1.0, point[2]))
        run.step({caster: (*aim, 16)})
        for _ in range(25):
            run.step({caster: (*aim, 0)})
            if run.match.combat.is_stunned(owner):
                break

    def target(self, run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)

    def thaw(self, run, owner):
        fight = run.match.combat
        while fight.is_stunned(owner):
            run.step({})

    def test_actual_flashbang_mei_damage_deadline_and_input_recovery(self):
        run, owner, caster = self.scene()
        fight = run.match.combat
        self.cast(run, owner, caster)
        self.assertEqual(fight.vitals_of(self.target(run, owner)).total, 225.0)
        self.assertTrue(fight.is_stunned(owner))
        end = fight.stuns[owner.body]
        self.assertEqual(end - fight.tick, 50)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x5E6)
        position, ammo = list(owner.mover.position), weapon.vars[53].value()
        frost.FrostTests().commands(run, owner, 20, buttons=1 | 8 | 16 | 32 | 0x800, forward=127)
        self.assertEqual(owner.mover.position, position)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertFalse(mei.protected(owner))
        self.assertFalse(fight.walls.pieces)
        self.assertFalse(fight.blizzard.flights)
        self.thaw(run, owner)
        target = self.target(run, caster)
        before = fight.vitals_of(target).total
        fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
        self.assertEqual(fight.vitals_of(target).total, before - 75.0)
        frost.FrostTests().commands(run, owner, 15, forward=127)
        self.assertGreater(owner.mover.position[2], position[2] + 0.2)

    def test_cryo_rejects_flash_damage_and_stun_without_interrupting_protection(self):
        run, owner, caster = self.scene()
        run.step({owner: (0, 0, 8)})
        self.assertTrue(mei.protected(owner))
        self.cast(run, owner, caster)
        fight = run.match.combat
        self.assertTrue(mei.protected(owner))
        self.assertEqual(fight.vitals_of(self.target(run, owner)).total, 250.0)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(caster.client.messages(combat.HIT), [])
        run.step({owner: (0, 0, 2)})
        self.assertFalse(mei.protected(owner))
        self.assertGreater(mei.cryo_instance(owner).vars[61].value(), 11.9)

    def test_flashbang_during_quick_melee_recovery_blocks_new_hits(self):
        run, owner, caster = self.scene()
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(caster, (x, y, z + 2.0))
        target = self.target(run, caster)
        before = fight.vitals_of(target).total
        melee = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x43)
        run.step({owner: (0, 0, 0x800)})
        recovery = melee.states[melee.graph.node(20).state]
        hit = melee.states[melee.graph.node(10).state]
        self.assertTrue(recovery.active)
        self.assertEqual(hit.counter, 1)
        self.assertEqual(fight.vitals_of(target).total, before - 30.)

        strike_tick = fight.tick
        self.cast(run, owner, caster)
        self.assertTrue(fight.is_stunned(owner))
        self.assertLess((fight.tick - strike_tick) * combat.TICK, .2)
        for buttons in (0x800, 0, 0x800, 0):
            run.step({owner: (0, 0, buttons)})
        self.thaw(run, owner)
        self.assertEqual(hit.counter, 1)
        self.assertEqual(fight.vitals_of(target).total, before - 30.)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)

        fixtures.MeiCombatTests().fire(run, owner, target, 0x800, .9)
        self.assertEqual(hit.counter, 2)
        self.assertEqual(fight.vitals_of(target).total, before - 60.)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 2)

    def test_flashbang_stops_new_spray_and_preserves_admitted_flight(self):
        run, owner, caster = self.scene()
        fight = run.match.combat
        target = self.target(run, caster)
        vitals = fight.vitals_of(target)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x5E6)
        ammo = weapon.vars[53]
        spray_aim = fixtures.aim(fight._eye(owner), (target.feet[0], target.feet[1] + .9, target.feet[2]))
        x, y, z = owner.mover.position
        flash_aim = fixtures.aim(fight._eye(caster), (x, y + 1., z))
        before = vitals.total
        for _ in range(12):
            run.step({owner: (*spray_aim, 1)})
        self.assertLess(vitals.total, before)
        self.assertLess(ammo.value(), 120.)
        self.assertGreater(fight.frost(caster)[0], 0.)

        for frame in range(25):
            run.step({owner: (*spray_aim, 1), caster: (*flash_aim, 16 if frame == 0 else 0)})
            if fight.is_stunned(owner):
                break
        self.assertTrue(fight.is_stunned(owner))
        spent, health = ammo.value(), vitals.total
        frost_at_stun = fight.frost(caster)[0]
        hit_count = len(owner.client.messages(combat.HIT))
        airborne = [rocket for rocket in fight.rockets if rocket.shooter is owner]
        self.assertTrue(airborne)
        admitted = {id(rocket) for rocket in airborne}
        for _ in range(20):
            run.step({owner: (*spray_aim, 1)})
            self.assertEqual(ammo.value(), spent)
            self.assertTrue({id(rocket) for rocket in fight.rockets if rocket.shooter is owner} <= admitted)
        self.assertTrue(fight.is_stunned(owner))
        self.assertFalse(any(rocket.shooter is owner for rocket in fight.rockets))
        self.assertEqual(vitals.total, health - 2.75)
        self.assertGreater(fight.frost(caster)[0], frost_at_stun)
        self.assertEqual(len(owner.client.messages(combat.HIT)), hit_count + 1)
        health = vitals.total
        while fight.is_stunned(owner):
            self.assertEqual(ammo.value(), spent)
            self.assertEqual(vitals.total, health)
            self.assertEqual(len(owner.client.messages(combat.HIT)), hit_count + 1)
            run.step({owner: (*spray_aim, 1)})

        sample = fight.frost(caster)
        frost_after_stun = sample[0] if sample else 0.
        for _ in range(12):
            run.step({owner: (*spray_aim, 1)})
        self.assertLess(ammo.value(), spent)
        self.assertLess(vitals.total, health)
        self.assertGreater(fight.frost(caster)[0], frost_after_stun)

    def test_flashbang_cancels_icicle_windup_without_refunding_spent_ammo(self):
        for keep_held in (False, True):
            with self.subTest(keep_held=keep_held):
                run, owner, caster = self.scene()
                fight = run.match.combat
                target = self.target(run, caster)
                vitals = fight.vitals_of(target)
                before = vitals.total
                weapon = next(i for i in owner.body_script.component.instances.values()
                              if i.graph.index == 0x5E6)
                aim = fixtures.aim(fight._eye(owner), (target.feet[0], target.feet[1] + .9, target.feet[2]))
                x, y, z = owner.mover.position
                flash_aim = fixtures.aim(fight._eye(caster), (x, y + 1., z))
                run.step({owner: (*aim, 2)})
                windup = weapon.states[weapon.graph.node(63).state]
                self.assertTrue(windup.active)
                self.assertFalse(fight.rockets)

                held = {owner: (*aim, 2)} if keep_held else {}
                for frame in range(25):
                    run.step({**held, caster: (*flash_aim, 16 if frame == 0 else 0)})
                    if fight.is_stunned(owner):
                        break
                self.assertTrue(fight.is_stunned(owner))
                self.assertFalse(windup.active)
                ammo = weapon.vars[53].value()
                self.assertGreater(ammo, 110.)
                self.assertLess(ammo, 120.)
                while fight.is_stunned(owner):
                    self.assertFalse(windup.active)
                    self.assertEqual(weapon.vars[53].value(), ammo)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(owner.client.messages(combat.HIT), [])
                    run.step(held)

                for _ in range(50):
                    if fight.rockets:
                        break
                    run.step({owner: (*aim, 2)})
                self.assertEqual(len(fight.rockets), 1)
                for _ in range(45):
                    run.step({})
                self.assertAlmostEqual(weapon.vars[53].value(), ammo - 10., places=4)
                self.assertEqual(vitals.total, before - 75.)
                self.assertEqual(len(owner.client.messages(combat.HIT)), 1)

    def test_released_icicle_hits_a_target_while_its_owner_is_stunned(self):
        run, owner, caster = self.scene()
        fight = run.match.combat
        bot = fixtures.Run().match.bots[0]
        bot.body_script, bot.route, bot.firing, bot.team = None, [], False, caster.team
        x, y, z = owner.mover.position
        bot.position = (x + 16., y, z)
        run.match.bots.append(bot)
        target = self.target(run, bot)
        before = fight.vitals_of(target).total
        aim = fixtures.aim(fight._eye(owner), (x + 16., y + .9, z))
        for _ in range(30):
            run.step({owner: (*aim, 2)})
            if fight.rockets:
                break
        self.assertEqual(len(fight.rockets), 1)
        icicle = fight.rockets[0]

        self.cast(run, owner, caster)
        self.assertTrue(fight.is_stunned(owner))
        self.assertIn(icicle, fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, before)
        for _ in range(15):
            run.step({})
        self.assertTrue(fight.is_stunned(owner))
        self.assertNotIn(icicle, fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, before - 75.)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)

    def test_flashbang_interrupts_blizzard_windup_after_charge_spend(self):
        run, owner, caster = self.scene()
        fight = run.match.combat
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(1610.)
        charge = owner.body_script.component.entity_var(342)
        held = (0, 9000, 32)
        run.step({owner: held})
        self.assertEqual(charge.value(), 0.)
        self.assertFalse(fight.blizzard.flights)
        x, y, z = owner.mover.position
        flash_aim = fixtures.aim(fight._eye(caster), (x, y + 1., z))
        for frame in range(25):
            run.step({owner: held, caster: (*flash_aim, 16 if frame == 0 else 0)})
            if fight.is_stunned(owner):
                break
        self.assertTrue(fight.is_stunned(owner))
        for _ in range(110):
            run.step({owner: held})
            self.assertEqual(charge.value(), 0.)
            self.assertFalse(fight.blizzard.flights)
            self.assertFalse(fight.blizzard.areas)
        self.assertFalse(fight.is_stunned(owner))

        run.step({})
        flight = blizzard.BlizzardTests().launch(run, owner)
        self.assertIn(flight, fight.blizzard.flights)
        self.assertEqual(charge.value(), 0.)

    def test_released_blizzard_persists_through_flashbang_on_its_owner(self):
        run, owner, caster = self.scene()
        flight = blizzard.BlizzardTests().launch(run, owner)
        self.cast(run, owner, caster)
        self.assertTrue(run.match.combat.is_stunned(owner))
        for _ in range(70):
            run.step({})
        self.assertNotIn(flight, run.match.combat.blizzard.flights)
        self.assertTrue(run.match.combat.blizzard.areas)
        self.assertLess(run.match.combat.vitals_of(self.target(run, caster)).total, 225.0)

    def test_flashbang_cancels_wall_preview_and_releases_weapon_suppression(self):
        run, owner, caster = self.scene()
        x, y, z = owner.mover.position
        aim = fixtures.aim(run.match.combat._eye(owner), (x, y, z + 2.0))
        wall = walls.WallTests()
        wall.tap(run, owner, 16, aim)
        self.assertTrue(run.match.combat.walls.inputs[owner.body].preview)
        self.cast(run, owner, caster)
        self.assertTrue(run.match.combat.is_stunned(owner))
        self.assertFalse(run.match.combat.walls.inputs[owner.body].preview)
        self.assertFalse(run.match.combat.walls.pieces)
        fight = run.match.combat
        cooldown = fight.walls.instance(owner).vars[61]
        while fight.is_stunned(owner):
            run.step({owner: (*aim, 1)})
        for _ in range(12):
            run.step({owner: (*aim, 1)})
        self.assertFalse(fight.walls.inputs[owner.body].preview)
        self.assertFalse(fight.walls.pieces)
        self.assertEqual(cooldown.value(), 0.)
        target = self.target(run, caster)
        before = run.match.combat.vitals_of(target).total
        fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, before - 75.0)
        wall.tap(run, owner, 16, aim)
        self.assertTrue(fight.walls.inputs[owner.body].preview)
        wall.tap(run, owner, 1, aim)
        self.assertEqual(len(fight.walls.pieces), 5)
        self.assertGreater(cooldown.value(), 11.9)

    def test_flash_death_and_body_retirement_remove_only_that_body_deadline(self):
        for dead in (True, False):
            with self.subTest(dead=dead):
                run, owner, caster = self.scene()
                self.cast(run, owner, caster)
                self.assertTrue(run.match.combat.is_stunned(owner))
                body = owner.body
                if dead:
                    run.match.combat._damage(caster, self.target(run, owner), 1000., False, None, run.frame)
                else:
                    self.assertTrue(run.match.switch_hero(owner, owner.hero))
                self.assertNotIn(body, run.match.combat.stuns)

    def test_expiring_flash_does_not_clear_a_longer_existing_frost_lock(self):
        run, owner, caster = self.scene()
        self.cast(run, owner, caster)
        fight = run.match.combat
        now = run.frame * 16
        # Explicit independent status fixtures isolate the union of deadlines.
        fight.frost_hits[owner.body] = (1., now, now + 1300, 0, 0)
        self.thaw(run, owner)
        self.assertTrue(fight.frozen(owner))
        before = list(owner.mover.position)
        frost.FrostTests().commands(run, owner, 10, forward=127)
        self.assertEqual(owner.mover.position, before)
        for _ in range(50):
            run.step({})
        self.assertFalse(fight.frozen(owner))


if __name__ == '__main__':
    unittest.main()
