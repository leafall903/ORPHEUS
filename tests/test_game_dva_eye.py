"""Authored104319 FirstPerson scalars in combat; camera/bone transport is open."""

import unittest
from dataclasses import replace

import test_game_dva_created_body as created_body
from test_game_combat import KANEZAKA_DM, Run, aim, dva_pilot_input
from test_game_mover import FLOOR, box

from ow174.game import combat, mover
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE, Command
from ow174.game.content import find_hero, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class FirstPersonTests(unittest.TestCase):
    def scene(self, body):
        run = Run(KANEZAKA_DM, ('Shooter', 'Target'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter, victim = run.players
        hero = heroes()[0x02E0000000000042] if body == 0x04000000000002A3 else find_hero('dva')
        self.assertTrue(match.switch_hero(shooter, replace(hero, body=body)))
        dva_pilot_input(shooter)
        shooter.mover.position = [0, 1, 0]
        victim.mover.position = [0, 1, 3]
        for player in run.players:
            player.mover.body = None
        for _ in range(100):
            run.step({})
        target = next(t for t in match.combat._targets() if t.owner is victim)
        return run, shooter, match.combat.vitals_of(target)

    def command(self, run, shooter, buttons):
        run.frame += 1
        run.match.update(run.frame*.016, run.frame)
        shooter.take_commands([Command(run.frame, buttons=buttons)])

    def test_both_forms_and_mccree_share_authored_source_and_target_eye(self):
        for body, expected in ((0x0400000000000407, (1.74, 1.74)),
                               (0x040000000000040A, (1.32, .8)),
                               (0x04000000000002A3, (1.425, .9975))):
            run, player, _ = self.scene(body)
            for crouched, height in zip((False, True), expected, strict=True):
                with self.subTest(body=body, crouched=crouched):
                    player.mover.flags_word = ((player.mover.flags_word & ~mover.CROUCHED)
                                               | (mover.CROUCHED if crouched else 0))
                    # Static stance contract; this does not accept native mech crouching.
                    target = next(t for t in run.match.combat._targets() if t.owner is player)
                    self.assertEqual(target.eye, height)
                    self.assertEqual(run.match.combat._eye(player), (0, 1+height, 0))

    def test_pilot_standing_and_mccree_stances_fire_against_low_cover(self):
        for body, cover in ((0x040000000000040A, 2.0), (0x04000000000002A3, 2.1)):
            # Pilot's unresolved initialization gravity0 scope prevents live
            # crouching. Its static crouched eye is checked separately below.
            for crouched in ((False,) if body == 0x040000000000040A else (False, True)):
                with self.subTest(body=body, crouched=crouched):
                    run, player, vitals = self.scene(body)
                    world = World(FLOOR+box(-10, 10, 0, cover, 1.4, 1.6))
                    run.match.collision_world = world
                    for actor in run.players:
                        actor.mover.use(world=world)
                    held = CROUCH if crouched else 0
                    for _ in range(20):
                        self.command(run, player, held)
                    self.assertEqual(player.mover.crouched, crouched, (
                        player.mover.flags, player.mover.position,
                        [(mod.index, mod.value, mod.key) for mod in player.mover.mods.values()]))
                    before = vitals.total
                    self.command(run, player, held | 1)
                    for _ in range(12):
                        self.command(run, player, held)
                    if crouched:
                        self.assertEqual(vitals.total, before)
                    else:
                        self.assertLess(vitals.total, before)

    def test_pilot_authored_crouched_eye_is_occluded_in_explicit_static_stance(self):
        run, player, _ = self.scene(0x040000000000040A)
        world = World(FLOOR+box(-10, 10, 0, 2.0, 1.4, 1.6))
        standing = run.match.combat._eye(player)
        self.assertIsNone(combat.ray_world(world, standing, (0, 0, 1), 3))
        player.mover.flags_word |= mover.CROUCHED
        crouched = run.match.combat._eye(player)
        self.assertEqual(crouched, (0, 1.8, 0))
        self.assertAlmostEqual(combat.ray_world(world, crouched, (0, 0, 1), 3), 1.4)

    def test_live_pilot_crouches_and_recovers_fire_over_cover_after_both_returns(self):
        created_body.CreatedBodyTests.setUpClass()
        for replacement, self_destruct in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(replacement=replacement, self_destruct=self_destruct):
                run, pilot, enemy, _, mech = created_body.CreatedBodyTests().scenario()
                match, fight = run.match, run.match.combat
                if replacement:
                    self.eject(run, pilot, enemy, mech, self_destruct=False)
                    x, y, z = pilot.mover.position
                    enemy.mover.position[:] = (x, y, z + 25)
                    enemy.mover.body = None
                    for _ in range(100):
                        run.step({})
                    script = pilot.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({pilot: (0, 0, 32)})
                    for _ in range(120):
                        if pilot.controlled_mech is not None:
                            break
                        run.step({})
                    self.assertIsNotNone(pilot.controlled_mech)
                    self.assertIsNot(pilot.controlled_mech, mech)
                    mech = pilot.controlled_mech
                self.eject(run, pilot, enemy, mech, self_destruct=self_destruct)

                x, y, z = pilot.mover.position
                world = World(box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
                              + box(x - 5, x + 5, y, y + 1.05, z + 1.4, z + 1.6))
                match.collision_world = world
                enemy.mover.position[:] = (x, y, z + 3)
                enemy.mover.body = None
                for _ in range(30):
                    run.step({pilot: (0, 0, CROUCH)})
                self.assertTrue(pilot.mover.crouched)
                self.assertEqual(fight.player_target(pilot).eye, .8)
                self.assertAlmostEqual(fight._eye(pilot)[1] - pilot.mover.position[1], .8)

                health = fight.vitals_of(fight.player_target(enemy))
                before = health.total
                run.step({pilot: (0, 0, CROUCH | FIRE)})
                self.assertTrue(any(rocket.shooter is pilot for rocket in fight.rockets))
                for _ in range(16):
                    run.step({pilot: (0, 0, CROUCH)})
                self.assertEqual(health.total, before)
                for _ in range(12):
                    run.step({})
                self.assertFalse(pilot.mover.crouched)
                self.assertEqual(fight.player_target(pilot).eye, 1.32)
                run.step({pilot: (0, 0, FIRE)})
                for _ in range(12):
                    run.step({})
                self.assertEqual(before - health.total, 14)

    def eject(self, run, pilot, enemy, mech, *, self_destruct):
        fight = run.match.combat
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z - 3)
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        self.assertIs(pilot.controlled_mech, mech)
        if self_destruct:
            component = mech.body_script.component
            component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
            run.step({pilot: (0, 0, 32)})
        else:
            fight.vitals[mech.entity] = combat.Vitals(mech.entity, 5)
            angles = aim(fight._eye(enemy), (x, y + 1.2, z))
            run.step({enemy: (*angles, FIRE)})
            self.assertTrue(mech.dead)
        run.step({})
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(mech.detached_bomb, self_destruct)
        self.assertIs(fight.player_target(pilot).owner, pilot)

    def test_mech_authored_eye_fires_above_cover_that_blocks_the_old_default(self):
        run, player, vitals = self.scene(0x0400000000000407)
        world = World(FLOOR+box(-10, 10, 0, 2.65, 1.4, 1.6))
        run.match.collision_world = world
        self.assertAlmostEqual(combat.ray_world(world, (0, 2.55, 0), (0, 0, 1), 3), 1.4)
        self.assertIsNone(combat.ray_world(world, run.match.combat._eye(player), (0, 0, 1), 3))
        before = vitals.total
        self.command(run, player, 1)
        for _ in range(12):
            self.command(run, player, 0)
        self.assertLess(vitals.total, before)
