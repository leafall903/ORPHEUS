"""Actual Ana commands against Brigitte's authored shield raise."""

import unittest
from unittest.mock import patch

import test_game_ana as fixture
from test_game_combat import Run, aim

from ow174.game import brigitte_shield, combat, stats
from ow174.game.content import find_hero
from ow174.game.script import expr, nodes, runtime


@unittest.skipUnless(brigitte_shield.GEOMETRY_SHA256, "requires the pinned Brigitte collision mesh")
class AnaBrigitteShieldTests(unittest.TestCase):
    def arena(self, *, enemy=True, yaw=-32768, distance=2.5):
        run, ana, brig, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy)
        self.assertTrue(run.match.switch_hero(brig, find_hero('Brigitte')))
        x, y, z = ana.mover.position
        brig.mover.position[:] = x, y, z + distance
        for _ in range(35):
            run.step({brig: (yaw, 0, 2)})
        fight = run.match.combat
        shield = fight.brigitte_shields.by_body[brig.body]
        self.assertTrue(shield.active())
        self.assertEqual((shield.health, shield.maximum), (250, 250))
        self.assertEqual(brig.body_script.component.entity_var(13214).value(), expr.Entity(shield.entity))
        vitals = {target.entity: fight.vitals_of(target) for target in fight._targets()}
        return run, ana, brig, shield, vitals[brig.body]

    def attack(self, run, ana, brig, button, *, scoped=False, yaw=-32768):
        x, y, z = brig.mover.position
        direction = aim(run.match.combat._eye(ana), (x, y + 1, z))
        held = 2 if scoped else 0
        defense = (yaw, 0, 2)
        for _ in range(35 if scoped else 0):
            run.step({ana: (*direction, held), brig: defense})
        run.step({ana: (*direction, held | button), brig: defense})
        for _ in range(65):
            run.step({ana: (*direction, held), brig: defense})

    def test_actual_rifle_modes_dart_and_grenade_spend_raised_shield(self):
        for button, scoped, amount in ((1, False, 70), (1, True, 70), (8, False, 5), (16, False, 50)):
            with self.subTest(button=button, scoped=scoped):
                run, ana, brig, shield, vitals = self.arena()
                health = vitals.total
                self.attack(run, ana, brig, button, scoped=scoped)
                self.assertEqual(shield.health, 250 - amount)
                self.assertEqual(shield.instance.vars[586].value(), 250 - amount)
                self.assertEqual(vitals.total, health)
                self.assertFalse(run.match.combat.is_asleep(brig))
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(ana.client.messages(combat.HIT))

    def test_breaking_grenade_keeps_cover_then_rifle_hits_body(self):
        run, ana, brig, shield, vitals = self.arena()
        health = vitals.total
        shield.health = 30
        shield.publish()
        self.attack(run, ana, brig, 16)
        self.assertEqual(shield.health, 0)
        self.assertFalse(shield.active())
        self.assertEqual(vitals.total, health)
        self.assertEqual(vitals.healing_block_until, 0)
        self.attack(run, ana, brig, 1, scoped=True)
        self.assertFalse(shield.active())
        self.assertLess(vitals.total, health)

    def test_rear_dart_reaches_body_and_interrupts_raise(self):
        run, ana, brig, shield, vitals = self.arena(yaw=0)
        health = vitals.total
        self.attack(run, ana, brig, 8, yaw=0)
        self.assertEqual(shield.health, 250)
        self.assertLess(vitals.total, health)
        self.assertTrue(run.match.combat.is_asleep(brig))
        self.assertFalse(shield.active())

    def test_melee_reaches_brigitte_through_the_raised_shield(self):
        run, ana, brig, shield, vitals = self.arena(distance=1.8)
        health = vitals.total
        self.attack(run, ana, brig, 0x800)
        self.assertLess(vitals.total, health)
        self.assertEqual(shield.health, 250)

    def test_actual_shield_bash_moves_hits_and_stuns_ana_once(self):
        run, ana, brig, shield, _ = self.arena(distance=5)
        fight = run.match.combat
        vitals = fight.vitals[ana.body]
        start = brig.mover.position[2]
        run.step({brig: (-32768, 0, 3)})
        self.assertFalse(fight.is_stunned(ana))
        for _ in range(15):
            run.step({brig: (-32768, 0, 2)})
            if fight.is_stunned(ana):
                break
        self.assertTrue(fight.is_stunned(ana))
        self.assertEqual(fight.stuns[ana.body] - run.frame, 47)
        self.assertEqual(vitals.total, 195)
        contact = brig.mover.position[2]
        self.assertLess(contact, start - 1)
        self.assertEqual(shield.instance.states[10].counter, 1)
        for _ in range(47):
            run.step({ana: (0, 0, 1), brig: (-32768, 0, 2)})
            if fight.is_stunned(ana):
                self.assertFalse(ana.body_script.component.held)
        self.assertFalse(fight.is_stunned(ana))
        self.assertAlmostEqual(brig.mover.position[2], contact)
        self.assertEqual(vitals.total, 195)
        self.assertGreater(shield.instance.vars[13062].value(), 0)

    def test_actual_shield_bash_rejects_ally_facing_cover_and_lowered_shield(self):
        for gate in ("ally", "facing", "cover", "lowered"):
            with self.subTest(gate=gate):
                yaw = 0 if gate == "facing" else -32768
                run, ana, brig, shield, _ = self.arena(enemy=gate != "ally", yaw=yaw, distance=5)
                if gate == "cover":
                    fixture.set_static_wall(run, ana, 2.5)
                if gate == "lowered":
                    for _ in range(8):
                        run.step({})
                    self.assertFalse(shield.active())
                fight = run.match.combat
                run.step({brig: (yaw, 0, 1 if gate == "lowered" else 3)})
                for _ in range(25):
                    run.step({brig: (yaw, 0, 2 if gate != "lowered" else 0)})
                self.assertEqual(fight.vitals[ana.body].total, 200)
                self.assertFalse(fight.is_stunned(ana))
                if gate == "cover":
                    self.assertGreater(brig.mover.position[2], ana.mover.position[2] + 2.5)

    def test_actual_shield_bash_rejects_cooldown_repress_then_rearms(self):
        run, ana, brig, shield, _ = self.arena(distance=2)
        fight = run.match.combat
        instance = shield.instance
        run.step({brig: (-32768, 0, 3)})
        for _ in range(55):
            run.step({brig: (-32768, 0, 2)})
        window = instance.states[15]
        self.assertEqual(fight.vitals[ana.body].total, 195)
        self.assertGreater(instance.vars[13062].value(), 5)
        run.step({brig: (-32768, 0, 3)})
        for _ in range(12):
            run.step({brig: (-32768, 0, 2)})
        self.assertEqual(window.counter, 1)
        self.assertEqual(fight.vitals[ana.body].total, 195)
        for _ in range(460):
            run.step({brig: (-32768, 0, 2)})
            if instance.vars[13062].value() == 0:
                break
        self.assertEqual(instance.vars[13062].value(), 0)
        self.assertEqual(window.counter, 1)
        run.step({brig: (-32768, 0, 3)})
        for _ in range(16):
            run.step({brig: (-32768, 0, 2)})
        self.assertEqual(window.counter, 2)
        self.assertEqual(fight.vitals[ana.body].total, 190)
        self.assertTrue(fight.is_stunned(ana))

    def bash_ally_arena(self, *, distance):
        run = Run(fixture.TEAM_MAP, ("Ana", "Brigitte", "Ally"))
        ana, brig, ally = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        self.assertTrue(match.switch_hero(ana, find_hero('Ana')))
        self.assertTrue(match.switch_hero(brig, find_hero('Brigitte')))
        self.assertTrue(match.switch_hero(ally, find_hero('Ana')))
        ana.team, brig.team, ally.team = 0, 1, 0
        x, y, z = ana.mover.position
        brig.mover.position[:] = x, y, z + distance
        ally.mover.position[:] = x + 5, y, z
        for _ in range(35):
            run.step({brig: (-32768, 0, 2)})
        fight = match.combat
        shield = fight.brigitte_shields.by_body[brig.body]
        self.assertTrue(shield.active())
        for target in fight._targets():
            fight.vitals_of(target)
        return run, ana, brig, ally, shield

    def test_shield_bash_interrupts_ana_nano_before_release(self):
        run, ana, brig, ally, _ = self.bash_ally_arena(distance=2)
        fight = run.match.combat
        vitals = fight.vitals[ally.body]
        vitals.damage(100)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        x, y, z = ally.mover.position
        direction = aim(fight._eye(ana), (x, y + 1, z))
        run.step({ana: (*direction, 32), brig: (-32768, 0, 2)})
        ultimate = ana.body_script.ultimates[0]
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        run.step({brig: (-32768, 0, 3)})
        for _ in range(35):
            run.step({brig: (-32768, 0, 2)})
        self.assertTrue(fight.is_stunned(ana))
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(fight.vitals[ana.body].total, 195)
        self.assertEqual(vitals.total, 100)
        self.assertEqual(vitals.nano_until, 0)

    def test_sleep_dart_interrupts_brigitte_before_bash_contact(self):
        run, ana, brig, ally, shield = self.bash_ally_arena(distance=5)
        fight = run.match.combat
        x, y, z = brig.mover.position
        ally.mover.position[:] = (x + 4, y, z)
        for _ in range(12):
            run.step({brig: (-32768, 0, 2)})
        direction = aim(fight._eye(ally), (x, y + 1, z - 1.5))
        run.step({ally: (*direction, 8), brig: (-32768, 0, 2)})
        for _ in range(35):
            run.step({ally: (*direction, 0), brig: (-32768, 0, 2)})
            if fight.rockets:
                break
        self.assertTrue(fight.rockets)
        self.assertFalse(fight.is_asleep(brig))
        run.step({brig: (-32768, 0, 3)})
        window = shield.instance.states[15]
        self.assertTrue(window.active)
        for _ in range(10):
            run.step({brig: (-32768, 0, 2)})
            if fight.is_asleep(brig):
                break
        self.assertTrue(fight.is_asleep(brig))
        stopped = tuple(brig.mover.position)
        for _ in range(25):
            run.step({brig: (-32768, 0, 2)})
        self.assertEqual(tuple(brig.mover.position), stopped)
        self.assertFalse(window.active)
        self.assertEqual(fight.vitals[ana.body].total, 200)
        self.assertFalse(fight.is_stunned(ana))

    def test_friendly_shield_allows_healing_and_nano(self):
        run, ana, brig, shield, vitals = self.arena(enemy=False)
        health = vitals.total
        vitals.damage(100)
        wounded = vitals.total
        self.attack(run, ana, brig, 1)
        self.assertEqual(vitals.total, wounded + 70)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        self.attack(run, ana, brig, 32)
        self.assertEqual(vitals.total, health)
        self.assertGreater(vitals.nano_until, run.match.combat.now)
        self.assertEqual(shield.health, 250)

    def test_break_lockout_allows_repair_then_fresh_raise(self):
        run, ana, brig, shield, _ = self.arena()
        shield.health = 30
        shield.publish()
        self.attack(run, ana, brig, 1)
        self.assertEqual(shield.health, 0)
        self.assertFalse(shield.active())
        fight = run.match.combat
        while fight.now < shield.broken_until - 0.08:
            run.step({brig: (-32768, 0, 2)})
            self.assertFalse(shield.active())
        self.assertGreater(shield.health, 0)
        while fight.now <= shield.broken_until:
            run.step({})
        for _ in range(35):
            run.step({brig: (-32768, 0, 2)})
        self.assertTrue(shield.active())
        self.assertEqual(shield.health, 250)

    def test_replacement_retires_pool_and_mesh(self):
        run, ana, brig, shield, _ = self.arena()
        old = brig.body
        self.assertTrue(run.match.switch_hero(brig, find_hero('Ana')))
        fight = run.match.combat
        self.assertNotIn(old, fight.brigitte_shields.by_body)
        self.assertIsNone(fight.body_health(shield.entity))
        self.attack(run, ana, brig, 1, scoped=True)
        self.assertEqual(fight.vitals[brig.body].total, 130)

    def test_shield_health_and_lockout_are_causal_snapshot_state(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        brig = players[1]
        peers[1].message(21616, stored.pick(brig.entity, brigitte_shield.HERO), now=100.8)
        peers[1].commands(brig.body_script.frame + 1, 40, 2)
        shield = brig.match.combat.brigitte_shields.by_body[brig.body]
        self.assertTrue(shield.active())
        with patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated a variable")):
            before = stored.snapshot_digest(case.capture(session))
            shield.health -= 10
            damaged = stored.snapshot_digest(case.capture(session))
            shield.broken_until += 1
            locked = stored.snapshot_digest(case.capture(session))
        self.assertNotEqual(before, damaged)
        self.assertNotEqual(damaged, locked)
