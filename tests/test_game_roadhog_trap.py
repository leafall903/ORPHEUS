"""Real Steel Trap input against Roadhog's movement and ability commands."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as fixtures
from test_game_roadhog_graviton import RoadhogGravitonTests

from ow174.game import combat
from ow174.game.commands import JUMP, Command
from ow174.game.content import heroes
from ow174.game.script.nodes import Ability


class RoadhogTrapTests(unittest.TestCase):
    def scene(self):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, junkrat, third = fixture.session("Junkrat", distance=7)
        x, y, z = hog.mover.position
        for actor, offset in ((hog, 15), (third, 30)):
            actor.mover.position[:] = (x + offset, y, z)
            actor.mover.body = None
        fight = run.match.combat
        run.step({junkrat: (-32768, 0, 16)})
        for _ in range(100):
            run.step({junkrat: (-32768, 0, 0)})
            if any(t.armed_at is not None and fight.now >= t.armed_at for t in fight.traps.live.values()):
                break
        trap, = fight.traps.live.values()
        self.assertIsNotNone(trap.armed_at)
        self.assertGreaterEqual(fight.now, trap.armed_at)
        self.assertIsNone(trap.victim)
        return run, hog, junkrat, third, trap

    def place(self, actor, point):
        actor.mover.position[:] = point
        actor.mover.body = None

    def movement(self, run, hog, *, buttons=0, forward=0):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        for actor in run.players:
            actor.client.queue_entities(run.match.remote_movements(actor))
            actor.take_commands([Command(run.frame, forward=forward if actor is hog else 0,
                                         buttons=buttons if actor is hog else 0)])

    def test_live_trap_deals_damage_once_and_roots_without_blocking_whole_hog(self):
        run, hog, _, _, trap = self.scene()
        fight = run.match.combat
        fixture = fixtures.RoadhogDefenseTests()
        before = fixture.health(run, hog)
        self.place(hog, trap.flight.position)
        run.step({})
        self.assertEqual(trap.victim, hog.body)
        self.assertEqual(trap.release_tick - fight.tick, 188)
        self.assertEqual(fixture.health(run, hog), before - 80)
        self.assertFalse(fight.is_stunned(hog))
        self.assertTrue(fight.traps.rooted(hog.body, fight.tick))
        script = hog.body_script
        ultimate, = script.ultimates
        script.set_ult_charge(script.component.entity_var(338).value())
        origin = tuple(hog.mover.position)
        self.movement(run, hog, buttons=32 | JUMP, forward=127)
        self.assertTrue(ultimate.flags & 1)
        for _ in range(25):
            self.movement(run, hog, forward=127)
        self.assertEqual(tuple(hog.mover.position), origin)
        self.assertEqual(fixture.health(run, hog), before - 80)
        while fight.tick < trap.release_tick + 10:
            self.movement(run, hog, forward=127)
        self.assertFalse(fight.traps.rooted(hog.body, fight.tick))
        self.assertFalse(fight.traps.live)
        self.assertGreater(hog.mover.position[2], origin[2])
        self.assertTrue(ultimate.flags & 1)

    def test_breather_reduction_and_healing_continue_through_capture(self):
        run, hog, _, _, trap = self.scene()
        fight = run.match.combat
        fixture = fixtures.RoadhogDefenseTests()
        target = next(t for t in fight._targets() if t.owner is hog)
        pool = fight.vitals_of(target)
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        run.step({hog: (0, 0, 16)})
        for _ in range(80):
            run.step({})
            if fight.incoming_damage_scale(hog) == 0.5:
                break
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        before = fixture.health(run, hog)
        self.place(hog, trap.flight.position)
        run.step({})
        self.assertTrue(fight.traps.rooted(hog.body, fight.tick))
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        self.assertEqual(fixture.health(run, hog), before - 40)
        for _ in range(35):
            run.step({})
        self.assertGreater(fixture.health(run, hog), before - 40)
        self.assertTrue(fight.traps.rooted(hog.body, fight.tick))

    def test_late_trap_input_preserves_ability_but_cannot_replay_movement(self):
        for destroyed in (False, True):
            with self.subTest(destroyed=destroyed):
                run, hog, junkrat, ally, trap = self.scene()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = ally.team = 0
                junkrat.team = 1
                if ally.hero != hog.hero:
                    self.assertTrue(run.match.switch_hero(ally, hog.hero))
                x, y, z = trap.flight.position
                self.place(ally, (x, y, z - 2))
                for _ in range(35):
                    run.step({})
                self.place(hog, (x, y, z - 1))
                run.step({})
                self.assertTrue(fight.traps.rooted(hog.body, fight.tick))
                script = hog.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                initial_frame = script.frame
                anchor = tuple(hog.mover.position)
                aim = fixtures.aim(fight._eye(ally), trap.centre())
                with patch.object(hog, "take_commands"):
                    for at in range(190):
                        fire = destroyed and at in (8, 63, 118)
                        run.step({ally: (*aim, int(fire))})
                        if not fight.traps.rooted(hog.body, fight.tick):
                            break
                    self.assertFalse(fight.traps.rooted(hog.body, fight.tick))
                    self.assertEqual(fight.tick < trap.release_tick, destroyed, trap.health)
                    run.step({})
                self.assertEqual(script.frame, initial_frame)
                self.assertEqual(tuple(hog.mover.position), anchor)
                cutoff = fight.traps.player_command_cutoffs[hog.body]
                self.assertGreater(cutoff - 5, initial_frame)
                self.assertLess(cutoff, run.frame)
                stale = [Command(frame, buttons=JUMP | 32, forward=127)
                         for frame in range(cutoff - 5, cutoff + 1)]
                hog.take_commands([*stale, Command(run.frame)])
                self.assertEqual(tuple(hog.mover.position), anchor)
                ultimate, = script.ultimates
                self.assertTrue(ultimate.flags & Ability.RUNNING)
                self.movement(run, hog, buttons=JUMP, forward=127)
                self.assertGreater(hog.mover.position[1], anchor[1])
                self.assertGreater(hog.mover.position[2], anchor[2])

    def test_steel_trap_preserves_a_buffered_quick_melee(self):
        run, hog, junkrat, victim, trap = self.scene()
        fight = run.match.combat
        fixture = fixtures.RoadhogDefenseTests()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, junkrat.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        self.place(victim, (x, y, z + 1.5))
        run.step({})
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        run.step({hog: (0, 0, 0x800)})
        first = run.frame
        self.assertEqual(fixture.health(run, victim), 19970)
        run.step({})
        run.step({hog: (0, 0, 0x800)})
        self.assertEqual(fixture.health(run, victim), 19970)

        x, y, z = trap.flight.position
        self.place(hog, (x, y, z))
        self.place(victim, (x, y, z + 1.5))
        run.step({hog: (0, 0, 0x800)})
        self.assertEqual(trap.victim, hog.body)
        self.assertFalse(fight.is_stunned(hog))
        anchored = tuple(hog.mover.position)
        while run.frame < first + 61:
            self.movement(run, hog, buttons=0x800, forward=127)
            self.assertEqual(tuple(hog.mover.position), anchored)
            self.assertEqual(fixture.health(run, victim), 19970)
        self.movement(run, hog, buttons=0x800, forward=127)
        self.assertEqual(run.frame, first + 62)
        self.assertTrue(fight.traps.rooted(hog.body, fight.tick))
        self.assertEqual(tuple(hog.mover.position), anchored)
        self.assertEqual(fixture.health(run, victim), 19940)
        for _ in range(65):
            run.step({hog: (0, 0, 0x800)})
        self.assertEqual(fixture.health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_buffered_melee_survives_trap_to_halt_movement_handoff(self):
        for recovery_offset in (-1, 0):
            with self.subTest(recovery_offset=recovery_offset):
                self.check_melee_during_trap_halt_handoff(recovery_offset)

    def check_melee_during_trap_halt_handoff(self, recovery_offset):
        run, hog, junkrat, orisa, trap = self.scene()
        fight = run.match.combat
        health = fixtures.RoadhogDefenseTests().health
        if fight.halt.control is None or fight.halt.query is None:
            self.skipTest('prepared 104319 0CCC/0CCE Halt inputs required')
        self.assertTrue(run.match.switch_hero(orisa, next(h for h in heroes().values() if h.name == "Orisa")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, junkrat.team, orisa.team = 0, 1, 1
        x, y, z = trap.flight.position
        self.place(orisa, (x, y, z - 5))
        self.place(junkrat, (x + 1.5, y, z))
        for _ in range(30):
            run.step({})
        self.place(hog, (x, y, z))
        run.step({})
        self.assertEqual(trap.victim, hog.body)
        origin = tuple(hog.mover.position)
        release = trap.release_tick
        first = release + recovery_offset - 62
        fire = release - 61
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        before = health(run, junkrat)

        while run.frame < first + 61:
            at = run.frame + 1
            cast = 2 if at in (fire, fire + 25) else 0
            melee = 0x800 if at == first or at >= first + 2 else 0
            run.step({hog: (16384, 0, melee), orisa: (0, 0, cast)})
            if at == first:
                self.assertEqual(health(run, junkrat), before - 30)
            self.assertEqual(tuple(hog.mover.position), origin)
        self.assertTrue(fight.traps.rooted(hog.body, fight.tick))
        self.assertIn(hog.body, fight.halt.pulls)
        self.assertFalse(fight.halt.drove_pose(hog.body, fight.tick))
        self.assertEqual(health(run, junkrat), before - 30)

        held = {hog: (16384, 0, 0x800)}
        run.step(held)
        self.assertEqual(run.frame, release + recovery_offset)
        self.assertEqual(health(run, junkrat), before - 60)
        self.assertFalse(fight.is_stunned(hog))
        if recovery_offset == -1:
            self.assertTrue(fight.traps.rooted(hog.body, fight.tick))
            self.assertEqual(tuple(hog.mover.position), origin)
            run.step(held)
        self.assertEqual(run.frame, release)
        self.assertFalse(fight.traps.rooted(hog.body, fight.tick))
        self.assertTrue(fight.halt.drove_pose(hog.body, fight.tick))
        self.assertGreater(hog.mover.position[2], origin[2])
        for _ in range(65):
            run.step(held)
        self.assertNotIn(hog.body, fight.halt.pulls)
        self.assertEqual(health(run, hog), 520)
        self.assertEqual(health(run, junkrat), before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_buffered_melee_distinguishes_graviton_and_hook_at_trap_release(self):
        for incoming_hook in (False, True):
            with self.subTest(incoming_hook=incoming_hook):
                self.check_melee_at_trap_release(incoming_hook)

    def check_melee_at_trap_release(self, incoming_hook):
        run, hog, junkrat, caster, trap = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, junkrat.team, caster.team = 0, 1, 1
        hero = hog.hero if incoming_hook else next(h for h in heroes().values() if h.name == "Zarya")
        self.assertTrue(match.switch_hero(caster, hero))
        x, y, z = trap.flight.position
        self.place(caster, (x + 8, y, z) if incoming_hook else (x + 3, y, z + 9))
        self.place(junkrat, (x - 1.5, y, z))
        for _ in range(30):
            run.step({})
        self.place(hog, (x, y, z))
        run.step({})
        self.assertEqual(trap.victim, hog.body)
        anchor = tuple(hog.mover.position)
        release = trap.release_tick
        health = fixtures.RoadhogDefenseTests().health
        before = health(run, junkrat)
        while run.frame < release - 63:
            run.step({})
        held = {hog: (-16384, 0, 0x800)}
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        run.step(held)
        self.assertEqual(run.frame + 62, release)
        run.step({})
        run.step(held)
        melee = next(s for i in hog.body_script.component.instances.values() if i.graph.index == 0x0043
                     for s in i.states.values() if isinstance(s, Ability))
        hit = melee.instance.state(4)
        self.assertEqual(health(run, junkrat), before - 30)
        self.assertEqual(hit.counter, 1)
        self.assertTrue(melee.presses)

        if incoming_hook:
            while run.frame < release - 30:
                run.step(held)
            look = fixtures.aim(fight._eye(caster), fight._eye(hog))
            run.step({**held, caster: (*look, 8)})
            while run.frame < release - 1 and not fight.hooks.controlled(hog.body):
                run.step({**held, caster: (*look, 0)})
            self.assertTrue(fight.hooks.controlled(hog.body))
            self.assertFalse(melee.presses)
            self.assertEqual(health(run, hog), 490)
        else:
            field = RoadhogGravitonTests().launch(run, caster, (x + 3, y, z), held=held)
            self.assertLess(release, field.end_tick)
            self.assertTrue(melee.presses)
        while run.frame < release - 1:
            run.step(held)
            self.assertEqual(tuple(hog.mover.position), anchor)
        self.assertEqual(tuple(hog.mover.position), anchor)
        self.assertTrue(fight.traps.rooted(hog.body, fight.tick))
        self.assertNotIn(hog.body, fight.gravitons.pulled)
        self.assertEqual(health(run, junkrat), before - 30)

        run.step(held)
        self.assertEqual(run.frame, release)
        self.assertFalse(fight.traps.rooted(hog.body, fight.tick))
        self.assertGreater(hog.mover.position[0], anchor[0])
        if incoming_hook:
            self.assertTrue(fight.hooks.controlled(hog.body))
            self.assertEqual(hit.counter, 1)
            for _ in range(100):
                if not fight.hooks.controlled(hog.body):
                    break
                run.step({})
            self.assertFalse(fight.hooks.controlled(hog.body))
            self.assertEqual(health(run, hog), 490)
            x, y, z = hog.mover.position
            self.place(junkrat, (x - 1.5, y, z))
            for _ in range(8):
                run.step({})
            self.assertEqual(health(run, junkrat), before - 30)
            self.assertEqual(hit.counter, 1)
            run.step(held)
        else:
            self.assertIn(hog.body, fight.gravitons.pulled)
            self.assertEqual(health(run, junkrat), before - 60)
            while run.frame < field.end_tick:
                run.step(held)
            self.assertNotIn(hog.body, fight.gravitons.pulled)
            self.assertAlmostEqual(health(run, hog), 500)
        self.assertEqual(health(run, junkrat), before - 60)
        self.assertEqual(hit.counter, 2)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_hook_can_latch_and_pull_while_roadhog_is_rooted(self):
        run, hog, _, victim, trap = self.scene()
        fight = run.match.combat
        x, y, z = trap.flight.position
        self.place(hog, (x, y, z))
        self.place(victim, (x + 10, y, z))
        run.step({})
        self.assertEqual(trap.victim, hog.body)
        initial = tuple(victim.mover.position)
        anchored = tuple(hog.mover.position)
        run.step({hog: (16384, 0, 8)})
        caught = False
        for _ in range(80):
            run.step({hog: (16384, 0, 0)})
            caught |= fight.hooks.controlled(victim.body)
            if caught and not fight.hooks.controlled(victim.body):
                break
        self.assertTrue(caught)
        self.assertLess(victim.mover.position[0], initial[0])
        self.assertTrue(fight.traps.rooted(hog.body, fight.tick))
        self.assertEqual(tuple(hog.mover.position), anchored)

    def test_trap_holds_against_graviton_and_releases_into_the_live_field(self):
        for field_first in (False, True):
            for button in (8, 16, 32):
                with self.subTest(field_first=field_first, button=button):
                    self.check_graviton_trap(field_first, button)

    def test_steel_trap_holds_a_hooked_roadhog_until_the_root_expires(self):
        run, hog, junkrat, hooker, trap = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, junkrat.team, hooker.team = 0, 1, 1
        self.assertTrue(match.switch_hero(hooker, hog.hero))
        x, y, z = trap.flight.position
        self.place(hooker, (x + 8, y, z))
        self.place(junkrat, (x - 12, y, z))
        for _ in range(30):
            run.step({})
        self.place(hog, (x, y, z))
        run.step({})
        self.assertEqual(trap.victim, hog.body)
        anchor = tuple(hog.mover.position)
        deadline = trap.release_tick
        while fight.tick < deadline - 30:
            run.step({})
        aim = fixtures.aim(fight._eye(hooker), fight._eye(hog))
        run.step({hooker: (*aim, 8)})
        while fight.tick < deadline - 1 and not fight.hooks.controlled(hog.body):
            run.step({hooker: (*aim, 0)})
        self.assertTrue(fight.hooks.controlled(hog.body))
        pool = fixtures.RoadhogDefenseTests().health
        self.assertEqual(pool(run, hog), 490)
        while fight.tick < deadline - 1:
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(tuple(hog.mover.position), anchor)
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(fight.tick, deadline)
        self.assertFalse(fight.traps.rooted(hog.body, fight.tick))
        self.assertTrue(fight.hooks.controlled(hog.body))
        self.assertGreater(hog.mover.position[0], anchor[0])
        for _ in range(60):
            if not fight.hooks.controlled(hog.body):
                break
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.hooks.controlled(hog.body))
        self.assertEqual(hog.mover.velocity, (0, 0, 0))
        self.assertEqual(pool(run, hog), 490)

    def check_graviton_trap(self, field_first, button):
        run, hog, junkrat, zarya, trap = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, junkrat.team, zarya.team = 0, 1, 1
        hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, hero))
        x, y, z = trap.flight.position
        self.place(zarya, (x + 3, y, z + 9))
        self.place(junkrat, (x - 10, y, z))
        for _ in range(30):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is hog)
        pool = fight.vitals_of(target)
        if button == 16:
            pool.damage(350)
            fight._sync_body_health(hog, pool)
        before = pool.total
        position = (x + 3, y, z)
        if field_first:
            self.place(hog, (x - 3, y, z))
            field = RoadhogGravitonTests().launch(run, zarya, position)
            self.assertIsNone(trap.victim)
            for _ in range(45):
                run.step({})
                if trap.victim is not None:
                    break
            self.assertIn(hog.body, fight.gravitons.pulled)
            self.assertGreater(hog.mover.position[0], x - 3)
        else:
            self.place(hog, (x, y, z))
            run.step({})
            self.assertEqual(pool.total, before - 80)
            field = RoadhogGravitonTests().launch(run, zarya, position)
        self.assertEqual(trap.victim, hog.body)
        self.assertLess(trap.release_tick + 2, field.end_tick)
        anchor = tuple(hog.mover.position)
        script = hog.body_script
        if button == 32:
            script.set_ult_charge(script.component.entity_var(338).value())
        angles = fixtures.aim(fight._eye(hog), fight._eye(junkrat))
        run.step({hog: (*angles, button)})
        self.assertNotIn(hog.body, fight.gravitons.pulled)
        self.assertFalse(fight.is_stunned(hog))
        after_press = pool.total
        victim_before = fixtures.RoadhogDefenseTests().health(run, junkrat)
        latched = False
        reduced = False
        for _ in range(68):
            run.step({hog: (*angles, 0)})
            self.assertEqual(tuple(hog.mover.position), anchor)
            latched |= fight.hooks.controlled(junkrat.body)
            reduced |= fight.incoming_damage_scale(hog) == 0.5
        if button == 8:
            self.assertTrue(latched)
            self.assertEqual(fixtures.RoadhogDefenseTests().health(run, junkrat), victim_before - 30)
        elif button == 16:
            self.assertTrue(reduced)
            self.assertGreater(pool.total, after_press)
        else:
            ultimate, = script.ultimates
            self.assertTrue(ultimate.flags & 1)
            self.assertLess(fixtures.RoadhogDefenseTests().health(run, junkrat), victim_before)
        while fight.tick < trap.release_tick - 1:
            run.step({})
        self.assertEqual(tuple(hog.mover.position), anchor)
        self.assertNotIn(hog.body, fight.gravitons.pulled)
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.traps.rooted(hog.body, fight.tick))
        self.assertIn(hog.body, fight.gravitons.pulled)
        self.assertGreater(hog.mover.position[0], anchor[0])
        while fight.tick < field.end_tick:
            run.step({})
        self.assertNotIn(hog.body, fight.gravitons.pulled)
        self.assertFalse(fight.gravitons.fields)
        if button != 16:
            self.assertAlmostEqual(pool.total, before - 100)
        released = hog.mover.position[2]
        for _ in range(15):
            self.movement(run, hog, forward=127)
        self.assertGreater(hog.mover.position[2], released)

    def test_roadhog_can_shoot_the_trap_to_release_himself_early(self):
        run, hog, _, _, trap = self.scene()
        fight = run.match.combat
        x, y, z = trap.flight.position
        self.place(hog, (x, y, z - 1))
        run.step({})
        self.assertEqual(trap.victim, hog.body)
        aim = fixtures.aim(fight._eye(hog), trap.centre())
        for frame in range(130):
            run.step({hog: (*aim, 1 if frame % 55 == 0 else 0)})
            if not fight.traps.live:
                break
        self.assertLess(fight.tick, trap.release_tick)
        self.assertFalse(fight.traps.live)
        self.assertFalse(fight.traps.rooted(hog.body, fight.tick))

    def test_roadhog_primary_can_destroy_armed_enemy_trap_before_capture(self):
        run, hog, _, _, trap = self.scene()
        fight = run.match.combat
        x, y, z = trap.flight.position
        self.place(hog, (x, y, z - 3.5))
        for _ in range(10):
            run.step({})
        aim = fixtures.aim(fight._eye(hog), trap.centre())
        for frame in range(210):
            run.step({hog: (*aim, 1 if frame % 55 == 0 else 0)})
            if not fight.traps.live:
                break
        self.assertFalse(fight.traps.live)
        self.assertIsNone(trap.victim)
        self.assertEqual(fixtures.RoadhogDefenseTests().health(run, hog), 600)


    def test_roadhog_secondary_melee_and_whole_hog_damage_the_trap(self):
        for button, distance in ((2, 3.5), (0x800, 1.75), (32, 3.5)):
            with self.subTest(button=button):
                run, hog, _, _, trap = self.scene()
                fight = run.match.combat
                x, y, z = trap.flight.position
                self.place(hog, (x, y, z - distance))
                for _ in range(8):
                    run.step({})
                self.assertIsNone(trap.victim)
                if button == 32:
                    script = hog.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                angles = fixtures.aim(fight._eye(hog), trap.centre())
                run.step({hog: (*angles, button)})
                for _ in range(55):
                    run.step({hog: (*angles, 0)})
                    if trap.health < 100:
                        break
                if button == 2:
                    self.assertEqual(trap.health, 50)
                elif button == 0x800:
                    self.assertEqual(trap.health, 70)
                else:
                    self.assertLess(trap.health, 100)
                self.assertIsNone(trap.victim)

    def test_helix_hits_enemy_trap_without_shielding_roadhog_from_splash(self):
        run, hog, _, soldier, trap = self.scene()
        fixture = fixtures.RoadhogDefenseTests()
        fight = run.match.combat
        x, y, z = trap.flight.position
        self.place(hog, (x, y, z + 2.2))
        self.place(soldier, (x, y, z - 4))
        for _ in range(12):
            run.step({})
        self.assertIsNone(trap.victim)
        before = fixture.health(run, hog)
        angles = fixtures.aim(fight._eye(soldier), trap.centre())
        run.step({soldier: (*angles, 2)})
        for _ in range(50):
            run.step({soldier: (*angles, 0)})
            if not fight.traps.live:
                break
        self.assertFalse(fight.traps.live)
        self.assertLess(fixture.health(run, hog), before)
        self.assertFalse(fight.traps.rooted(hog.body, fight.tick))
