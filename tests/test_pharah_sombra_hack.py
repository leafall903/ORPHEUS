"""Actual Sombra Hack against Pharah's skills and weapon input."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_defenses as defense

from ow174.game import combat, sombra_hack, zarya_barrier
from ow174.game.collision import World
from ow174.game.content import heroes


class PharahHackTests(unittest.TestCase):
    def arena(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, sombra, other = fixture.encounter("Sombra")
        other.mover.position[0] = 20
        other.mover.body = None
        return fixture, run, pharah, sombra

    def cast(self, run, pharah, sombra):
        fight = run.match.combat
        for _ in range(65):
            run.step({sombra: (-32768, 0, 2), pharah: (-32768, 0, 0)})
            if pharah.body in fight.hacks:
                effect, = fight.hacks[pharah.body]
                self.assertEqual(effect.instance.graph.index, 0x0B1F)
                self.assertEqual(effect.deadline - run.frame, 313)
                return effect
        self.fail("Actual Hack did not apply")

    def test_hack_blocks_flight_and_abilities_but_primary_still_hits(self):
        fixture, run, pharah, sombra = self.arena()
        effect = self.cast(run, pharah, sombra)
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        graphs = {i.graph.index: i for i in script.component.instances.values()}
        run.step({pharah: (0, 0, 1 | 2 | 8 | 16 | 32)})
        for _ in range(45):
            run.step({pharah: (0, 0, 2 | 8 | 16 | 32)})
        self.assertEqual(fixture.health(run, sombra).total, 80)
        self.assertEqual(graphs[0x23].vars[53].value(), 5)
        self.assertEqual(graphs[0x27].vars[61].value(), 0)
        self.assertEqual(graphs[0x7B].vars[61].value(), 0)
        self.assertEqual(pharah.mover.velocity[1], 0)
        self.assertFalse(script.ultimate_running())
        while run.frame < effect.deadline - 1:
            run.step({})
        run.step({pharah: (0, 0, 8)})
        self.assertNotIn(pharah.body, run.match.combat.hacks)
        self.assertEqual(pharah.mover.velocity[1], 20)

    def test_hack_cancels_pending_jump_hover_and_fresh_input_recovers(self):
        _, run, pharah, sombra = self.arena()
        hover = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x25)
        wait = hover.state(hover.graph.node(2).state)
        thrust = hover.state(hover.graph.node(12).state)
        fight = run.match.combat
        facing = {sombra: (-32768, 0, 2)}
        for frame in range(35):
            run.step({**facing, pharah: (0, 0, 2 if frame >= 26 else 0)})
        self.assertTrue(thrust.active)
        run.step({**facing, pharah: (0, 0, 0x40)})
        self.assertTrue(wait.active)
        self.assertFalse(thrust.active)
        fuel = hover.vars[87].value()
        for _ in range(12):
            if pharah.body in fight.hacks:
                break
            run.step({**facing, pharah: (0, 0, 0x40)})
        effect, = fight.hacks[pharah.body]
        self.assertFalse(wait.active)
        self.assertFalse(thrust.active)
        while run.frame < effect.deadline - 1:
            run.step({pharah: (0, 0, 0x40)})
            self.assertFalse(thrust.active)
            self.assertGreaterEqual(hover.vars[87].value(), fuel)
        run.step({})
        self.assertNotIn(pharah.body, fight.hacks)
        self.assertFalse(thrust.active)
        run.step({pharah: (0, 0, 2)})
        self.assertTrue(thrust.active)
        self.assertLess(hover.vars[87].value(), hover.vars[971].value())

    def test_hack_cancels_concussion_before_release_without_spending_cooldown(self):
        _, reference, player, hacker = self.arena()
        start = reference.frame
        self.cast(reference, player, hacker)
        hack_ticks = reference.frame - start

        fixture, run, pharah, sombra = self.arena()
        fight = run.match.combat
        concussion = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x7B)
        initial = tuple(sombra.mover.position)
        # The shot normally releases on frame 11, one frame after this Hack.
        for offset in range(1, hack_ticks):
            held = 16 if offset >= hack_ticks - 9 else 0
            run.step({sombra: (-32768, 0, 2), pharah: (0, 0, held)})
        self.assertTrue(concussion.states[1].active)
        self.assertEqual(concussion.vars[61].value(), 0)
        self.assertFalse(fight.rockets)
        self.assertNotIn(pharah.body, fight.hacks)

        run.step({sombra: (-32768, 0, 2), pharah: (0, 0, 16)})
        effect, = fight.hacks[pharah.body]
        self.assertFalse(concussion.states[1].active)
        while run.frame < effect.deadline - 1:
            run.step({pharah: (0, 0, 16)})
            self.assertFalse(fight.rockets)
        run.step({})
        self.assertNotIn(pharah.body, fight.hacks)
        self.assertFalse(fight.rockets)
        self.assertEqual(concussion.vars[61].value(), 0)
        self.assertEqual(tuple(sombra.mover.position), initial)
        self.assertEqual(fixture.health(run, sombra).total, 200)
        self.assertFalse(pharah.client.messages(25001))

        run.step({})
        run.step({pharah: (0, 0, 16)})
        for _ in range(10):
            run.step({})
        self.assertTrue(any(rocket.shooter is pharah for rocket in fight.rockets))
        self.assertGreater(concussion.vars[61].value(), 0)

    def test_in_flight_concussion_survives_hack_and_keeps_cooldown(self):
        _, reference, player, hacker = self.arena()
        start = reference.frame
        self.cast(reference, player, hacker)
        hack_ticks = reference.frame - start

        fixture, run, pharah, sombra = self.arena()
        fight = run.match.combat
        script = pharah.body_script
        concussion = next(i for i in script.component.instances.values() if i.graph.index == 0x7B)
        facing = {sombra: (-32768, 0, 2)}
        initial = sombra.mover.position[2]
        for offset in range(1, hack_ticks):
            fire = 16 if offset == hack_ticks - 12 else 0
            run.step({**facing, pharah: (0, 0, fire)})
        rocket, = (rocket for rocket in fight.rockets if rocket.shooter is pharah)
        self.assertNotIn(pharah.body, fight.hacks)
        self.assertGreater(concussion.vars[61].value(), 0)
        run.step(facing)
        effect, = fight.hacks[pharah.body]
        self.assertIn(rocket, fight.rockets)
        for _ in range(35):
            run.step({})
        self.assertGreater(sombra.mover.position[2], initial + 3)
        self.assertEqual(fixture.health(run, sombra).total, 200)
        self.assertEqual(fixture.health(run, pharah).total, 200)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(pharah.client.messages(25001))

        while run.frame < effect.deadline:
            run.step({})
        self.assertNotIn(pharah.body, fight.hacks)
        self.assertGreater(concussion.vars[61].value(), 0)
        run.step({pharah: (0, 0, 16)})
        for _ in range(10):
            run.step({})
        self.assertFalse(fight.rockets)
        for _ in range(600):
            if concussion.vars[61].value() <= 0:
                break
            run.step({})
        self.assertLessEqual(concussion.vars[61].value(), 0)
        run.step({pharah: (0, 0, 16)})
        for _ in range(10):
            if fight.rockets:
                break
            run.step({})
        self.assertTrue(any(rocket.shooter is pharah for rocket in fight.rockets))

    def test_hack_interrupts_active_barrage_and_retires_with_the_body(self):
        _, run, pharah, sombra = self.arena()
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (-32768, 0, 32)})
        self.assertTrue(script.ultimate_running())
        effect = self.cast(run, pharah, sombra)
        self.assertFalse(script.ultimate_running())
        old = pharah.body
        self.assertTrue(run.match.switch_hero(pharah, pharah.hero))
        self.assertNotEqual(pharah.body, old)
        self.assertNotIn(old, run.match.combat.hacks)
        self.assertNotIn(effect.instance.id, effect.component.instances)

    def test_active_hack_survives_sombra_death_until_its_original_deadline(self):
        fixture, run, pharah, sombra = self.arena()
        fight = run.match.combat
        effect = self.cast(run, pharah, sombra)
        health = fixture.health(run, sombra)
        health.damage(health.total - 60)
        fixture.fire(run, pharah)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, pharah.body)
        self.assertEqual(fight.hacks[pharah.body], [effect])
        self.assertIs(effect.component.instances[effect.instance.id], effect.instance)
        run.step({pharah: (0, 0, 8)})
        self.assertEqual(pharah.mover.velocity[1], 0)
        while run.frame < effect.deadline:
            run.step({})
        self.assertNotIn(pharah.body, fight.hacks)
        self.assertNotIn(effect.instance.id, effect.component.instances)
        run.step({pharah: (0, 0, 8)})
        self.assertEqual(pharah.mover.velocity[1], 20)

    def test_actual_pharah_death_retires_hack_and_respawn_allows_jump_jet(self):
        fixture, run, pharah, sombra = self.arena()
        fight = run.match.combat
        old_body = pharah.body
        effect = self.cast(run, pharah, sombra)
        health = fixture.health(run, pharah)
        health.damage(health.total - 1)
        yaw, pitch = fixture.aim_at_body(fight, sombra, pharah)
        for _ in range(42):
            run.step({sombra: (yaw, pitch, 1)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, sombra.body)
        self.assertLess(run.frame, effect.deadline)
        self.assertNotIn(old_body, fight.hacks)
        self.assertNotIn(effect.instance.id, effect.component.instances)
        for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK) + 4):
            if pharah.body != old_body:
                break
            run.step({})
        self.assertNotEqual(pharah.body, old_body)
        self.assertFalse(fight.is_dead(pharah))
        self.assertNotIn(pharah.body, fight.hacks)
        run.step({pharah: (0, 0, 8)})
        self.assertEqual(pharah.mover.velocity[1], 20)

    def test_release_wall_and_team_reject_hack(self):
        for reason in ("release", "wall", "team"):
            with self.subTest(reason=reason):
                _, run, pharah, sombra = self.arena()
                fight = run.match.combat
                if reason == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    pharah.team = sombra.team = 0
                y = pharah.mover.position[1]
                wall = World([(-4, y, 4, -4, y + 5, 4, 4, y + 5, 4),
                              (-4, y, 4, 4, y + 5, 4, 4, y, 4)])
                with patch.object(fight, "_collision", return_value=wall if reason == "wall"
                                  else fight._collision()):
                    for frame in range(70):
                        held = reason != "release" or frame < 12
                        run.step({sombra: (-32768, 0, 2 if held else 0)})
                self.assertFalse(sombra_hack.active(fight, pharah.body, fight.tick))

    def test_hacked_pharah_can_still_use_quick_melee(self):
        fixture, run, pharah, sombra = self.arena()
        self.cast(run, pharah, sombra)
        pharah.mover.position[2] = 6.4
        pharah.mover.body = None
        run.step({pharah: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(fixture.health(run, sombra).total, 170)
        self.assertIn(pharah.body, run.match.combat.hacks)

    def test_projected_barrier_rejects_hack_before_or_during_channel(self):
        for deployed_during_channel in (False, True):
            with self.subTest(deployed_during_channel=deployed_during_channel):
                _, run, pharah, sombra = self.arena()
                zarya = run.players[2]
                hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
                self.assertTrue(run.match.switch_hero(zarya, hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                zarya.team = pharah.team = 0
                sombra.team = 1
                zarya.mover.position = [4.5, pharah.mover.position[1], 0]
                zarya.mover.body = None
                fight = run.match.combat
                facing = {sombra: (-32768, 0, 2)}
                if deployed_during_channel:
                    for _ in range(30):
                        run.step(facing)
                    instance = next(i for i in sombra.body_script.component.instances.values()
                                    if i.graph.index == 0x0B1B)
                    self.assertTrue(instance.states[53].flags & instance.states[53].RUNNING)
                    self.assertNotIn(pharah.body, fight.hacks)
                hack = facing if deployed_during_channel else {}
                run.step({**hack, zarya: (-16384, 1000, 16)})
                for _ in range(3):
                    run.step(hack)
                bubble = fight.zarya_bubbles[zarya.body, zarya_barrier.PROJECTED]
                self.assertIs(bubble.target, pharah)
                for _ in range(65):
                    run.step(facing)
                    self.assertNotIn(pharah.body, fight.hacks)
                self.assertIsNotNone(bubble.phase())
                self.assertEqual(bubble.health, 200)

                for _ in range(160):
                    run.step({})
                    if bubble.phase() is None:
                        break
                self.assertIsNone(bubble.phase())
                self.cast(run, pharah, sombra)

    def test_pharah_rocket_damage_interrupts_channel(self):
        fixture, run, pharah, sombra = self.arena()
        facing = {sombra: (-32768, 0, 2)}
        for _ in range(8):
            run.step(facing)
        run.step({**facing, pharah: (0, 0, 1)})
        for _ in range(55):
            run.step(facing)
        self.assertEqual(fixture.health(run, sombra).total, 80)
        self.assertNotIn(pharah.body, run.match.combat.hacks)
        instance = next(i for i in sombra.body_script.component.instances.values() if i.graph.index == 0x0B1B)
        self.assertGreater(instance.states[53].remaining(), 0)
        self.assertLess(instance.states[53].remaining(), 2)
