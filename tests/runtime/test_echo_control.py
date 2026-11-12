"""Real foreign Flashbang input -> Echo control/interrupt/recovery, not injected disable bits."""
import unittest

import test_mccree_flashbang as flash
from test_game_combat import aim

from ow174.game import combat, echo_movement
from ow174.game.commands import JUMP
from ow174.game.content import heroes
from ow174.game.mover import Mover, mover_data

ECHO = 0x02E0000000000206


class EchoForeignControlTests(unittest.TestCase):
    def session(self, distance=3):
        fixture = flash.FlashbangTests()
        run, caster, echo = fixture.arena(distance=distance)
        run.match.switch_hero(echo, heroes()[ECHO], preserve_pose=True)
        for _ in range(25):
            fixture.step(run, caster, echo)
        return fixture, run, caster, echo

    def test_actual_flashbang_blocks_echo_attacks_movement_and_full_charge_copy_then_recovers(self):
        fixture, run, caster, echo = self.session()
        fixture.cast(run, caster, echo)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is echo)
        self.assertEqual(fight.vitals_of(target).total, 175)
        end = fight.stuns[echo.body]
        echo.body_script.ult_seconds = 0
        cost = echo.body_script.component.entity_var(338).value()
        echo.body_script.set_ult_charge(cost)  # isolate the actual control gate
        position = tuple(echo.mover.position)
        caster_health = fixture.health(run, caster)
        while run.frame < end - 1:
            fixture.step(run, caster, echo, target_buttons=1 | 8 | 16 | 32 | 0x40, forward=127)
            self.assertEqual(echo.hero.guid, ECHO)
            self.assertEqual(tuple(echo.mover.position), position)
            self.assertEqual(fixture.health(run, caster), caster_health)
            self.assertFalse(echo.body_script.component.held)
        fixture.step(run, caster, echo)  # expiry and real input release
        self.assertFalse(fight.is_stunned(echo))
        self.assertEqual(echo.body_script.component.entity_var(342).value(), cost)
        fixture.step(run, caster, echo, target_buttons=32)
        self.assertEqual(echo.hero.guid, caster.hero.guid)
        self.assertTrue(fight.echo_duplicates.active(echo))

    def test_flashbang_on_copied_body_does_not_stun_restored_echo(self):
        fixture, run, caster, echo = self.session()
        fight = run.match.combat
        script = echo.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fixture.step(run, caster, echo, target_buttons=32)
        self.assertTrue(fight.echo_duplicates.active(echo))
        self.assertEqual(echo.hero.guid, caster.hero.guid)
        copied_body = echo.body
        expires = fight.echo_duplicates.states[echo.entity].expires
        while expires - fight.now > 28 * combat.TICK:
            fixture.step(run, caster, echo)

        copy_health = fixture.health(run, echo)
        fixture.cast(run, caster, echo, target_buttons=0)
        self.assertTrue(fight.echo_duplicates.active(echo))
        self.assertEqual(fixture.health(run, echo), copy_health - 25)
        deadline = fight.stuns[copied_body]
        while fight.echo_duplicates.active(echo):
            fixture.step(run, caster, echo)
        self.assertLess(run.frame, deadline)
        self.assertEqual(echo.hero.guid, ECHO)
        self.assertNotEqual(echo.body, copied_body)
        self.assertNotIn(copied_body, fight.stuns)
        self.assertFalse(fight.is_stunned(echo))
        self.assertEqual(fixture.health(run, echo), 200)

        before = fixture.health(run, caster)
        x, y, z = caster.mover.position
        angles = aim(fight._eye(echo), (x, y + 1, z))
        run.step({echo: (*angles, 1)})
        for _ in range(8):
            run.step({})
        self.assertLess(run.frame, deadline)
        self.assertEqual(fixture.health(run, caster), before - 51)

    def test_actual_flashbang_during_melee_recovery_blocks_new_strikes(self):
        fixture, run, caster, echo = self.session(distance=2)
        fight = run.match.combat
        before = fixture.health(run, caster)
        melee = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x43)
        fixture.step(run, caster, echo, target_buttons=0x800)
        strike = melee.states[4]
        recovery = melee.states[6]
        self.assertEqual(strike.counter, 1)
        self.assertGreater(recovery.remaining(), 0)
        self.assertEqual(fixture.health(run, caster), before - 30)

        fixture.cast(run, caster, echo)
        self.assertTrue(fight.is_stunned(echo))
        self.assertEqual(fixture.health(run, echo), 175)
        self.assertGreater(recovery.remaining(), 0)
        self.assertFalse(strike.active)
        end = fight.stuns[echo.body]
        while run.frame < end - 1:
            fixture.step(run, caster, echo, target_buttons=0x800)
            self.assertEqual(strike.counter, 1)
            self.assertEqual(fixture.health(run, caster), before - 30)
        fixture.step(run, caster, echo)
        self.assertFalse(fight.is_stunned(echo))
        for _ in range(100):
            if recovery.remaining() == 0:
                break
            fixture.step(run, caster, echo)
        self.assertEqual(recovery.remaining(), 0)
        fixture.step(run, caster, echo, target_buttons=0x800)
        self.assertEqual(strike.counter, 2)
        self.assertEqual(fixture.health(run, caster), before - 60)

    def test_actual_flashbang_suppresses_glide_until_stun_expires(self):
        fixture, run, caster, echo = self.session()
        base = mover_data(echo.hero.body)
        for player in (caster, echo):
            x, y, z = player.mover.position
            player.mover = Mover((x, y + 10, z), world=run.match.collision_world,
                                 data=mover_data(player.hero.body))
        for _ in range(10):
            fixture.step(run, caster, echo, target_buttons=JUMP)
        self.assertEqual(echo.mover.data.terminal_velocity, 4)
        self.assertIsNotNone(echo_movement.glide_properties(echo, JUMP))

        fixture.cast(run, caster, echo, target_buttons=JUMP)
        fight = run.match.combat
        self.assertEqual(fixture.health(run, echo), 175)
        end = fight.stuns[echo.body]
        for _ in range(5):
            fixture.step(run, caster, echo, target_buttons=JUMP, forward=127)
        self.assertTrue(fight.is_stunned(echo))
        self.assertEqual(echo.mover.data, base)
        self.assertFalse(echo.body_script.buttons & JUMP)
        while run.frame < end - 1:
            fixture.step(run, caster, echo, target_buttons=JUMP)
            self.assertEqual(echo.mover.data, base)
        self.assertTrue(echo.mover.airborne)
        for _ in range(3):
            fixture.step(run, caster, echo, target_buttons=JUMP)
        self.assertFalse(fight.is_stunned(echo))
        self.assertEqual(echo.mover.data.terminal_velocity, 4)
        self.assertGreaterEqual(echo.mover.velocity[1], -4.01)

    def test_actual_flashbang_interrupts_sticky_volley_but_preserves_emitted_bombs(self):
        fixture, run, caster, echo = self.session()
        fight = run.match.combat
        initial = fixture.health(run, caster)
        fixture.cast(run, caster, echo, target_buttons=2)
        self.assertEqual(fixture.health(run, echo), 175)
        rockets = tuple(r for r in fight.rockets if r.shooter is echo)
        self.assertGreater(len(rockets), 0)
        self.assertLess(len(rockets), 6)
        sticky = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x1C36)
        self.assertGreater(sticky.states[5].remaining(), 0)
        end = fight.stuns[echo.body]
        while run.frame < end - 1:
            fixture.step(run, caster, echo, target_buttons=2)
            self.assertEqual(tuple(r for r in fight.rockets if r.shooter is echo), rockets)
        for _ in range(85):
            fixture.step(run, caster, echo)
        self.assertFalse(fight.is_stunned(echo))
        self.assertFalse(any(r.shooter is echo for r in fight.rockets))
        self.assertEqual(initial - fixture.health(run, caster), len(rockets) * 30)

    def test_actual_flashbang_interrupts_flight_and_beam_and_primary_recovers(self):
        for button, graph_id, state_id in ((8, 0x1BB6, 15), (16, 0x1FBD, 17)):
            with self.subTest(ability=hex(graph_id)):
                fixture, run, caster, echo = self.session()
                for _ in range(6 if button == 8 else 20):
                    fixture.step(run, caster, echo, target_buttons=button)
                instance = next(i for i in echo.body_script.component.instances.values()
                                if i.graph.index == graph_id)
                self.assertTrue(instance.states[state_id].active)
                fixture.cast(run, caster, echo, target_buttons=button)
                self.assertFalse(instance.states[state_id].active)
                self.assertIsNone(echo_movement.flight(echo))
                weapon = next(i for i in echo.body_script.component.instances.values()
                              if i.graph.index == 0x19E5)
                ammo = weapon.vars[53]
                initial_ammo = ammo.value()
                health = fixture.health(run, caster)
                end = run.match.combat.stuns[echo.body]
                while run.frame < end - 1:
                    fixture.step(run, caster, echo, target_buttons=button | 1)
                    self.assertFalse(instance.states[state_id].active)
                    self.assertEqual(fixture.health(run, caster), health)
                    self.assertEqual(ammo.value(), initial_ammo)
                    self.assertFalse(run.match.combat.rockets)
                fixture.step(run, caster, echo)
                x, y, z = caster.mover.position
                attack = aim(run.match.combat._eye(echo), (x, y + .8, z))
                run.step({echo: (*attack, 1)})
                self.assertEqual(ammo.value(), initial_ammo - 1)
                rockets = run.match.combat.rockets
                self.assertEqual(len(rockets), 3)
                self.assertTrue(all(rocket.direct.effect is None for rocket in rockets))
                target = next(t for t in run.match.combat._targets() if t.owner is caster)
                a, b = target.segment()
                criticals = 0
                for rocket in rockets:
                    contact = combat.ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
                    self.assertIsNotNone(contact)
                    height = rocket.position[1] + contact * rocket.direction[1]
                    criticals += height >= target.feet[1] + target.eye - combat.HEAD_BELOW
                for _ in range(12):
                    fixture.step(run, caster, echo)
                self.assertEqual(fixture.health(run, caster), health - 51 - 17 * criticals)
                hits = echo.client.messages(combat.HIT)[-3:]
                self.assertEqual(len(hits), 3)
                self.assertEqual(sum(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL) for hit in hits),
                                 criticals)
                self.assertFalse(run.match.combat.is_stunned(caster))
