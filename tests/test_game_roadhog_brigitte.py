"""Actual Brigitte shield inputs into the selected 104319 mesh and pool host."""

import math
import os
import unittest
from dataclasses import replace

import test_game_roadhog_ana as ana_tests
import test_game_roadhog_defenses as phases
import test_game_roadhog_stun as stun_tests
from test_game_combat import aim

from ow174.game import barriers, combat, npc, zarya_barriers
from ow174.game.collision import World
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


HOOK_SHIELD_LAYOUTS = ((0, True, False), (5, False, False), (0, True, True))


@unittest.skipUnless(os.environ.get("ORPHEUS_BRIGITTE_COLLISION"), "Brigitte model input required")
class RoadhogBrigitteTests(unittest.TestCase):
    def session(self, distance=4):
        run, hog, brigitte, rear = phases.RoadhogDefenseTests().session("Brigitte", distance=distance)
        rear.mover.position[0] = 30
        rear.mover.body = None
        for _ in range(35):
            self.step(run, hog, brigitte)
        fight = run.match.combat
        state = fight.barriers[brigitte.body]
        self.assertEqual(state.maximum, 250)
        self.assertFalse(state.active)
        return run, hog, brigitte, state

    def step(self, run, hog, brigitte, *, fire=0, raised=False, facing=-32768):
        x, y, z = brigitte.mover.position
        angles = aim(run.match.combat._eye(hog), (x, y + 0.85, z))
        run.step({hog: (*angles, fire), brigitte: (facing, 0, 2 if raised else 0)})

    def test_live_barrier_pool_keeps_authored_shield_bash_search_open(self):
        run, hog, brigitte, shield = self.session(distance=8)
        fight = run.match.combat
        for _ in range(10):
            self.step(run, hog, brigitte, raised=True)
        instance = fight._barrier_instance(brigitte)
        self.assertTrue(shield.active)
        self.assertEqual(instance.find_var(0, 586).value(), 250)
        run.step({brigitte: (-32768, 0, 3)})
        self.assertTrue(instance.state(instance.graph.node(21).state).active)
        self.assertTrue(instance.state(instance.graph.node(17).state).active)
        for _ in range(20):
            self.step(run, hog, brigitte, raised=True)
        self.assertFalse(instance.state(instance.graph.node(21).state).active)
        self.assertGreater(instance.find_var(0, 13062).value(), 6)

    def test_actual_shield_bash_interrupts_roadhog_breather(self):
        run, hog, brigitte, shield = self.session(distance=2)
        fight = run.match.combat
        for _ in range(10):
            self.step(run, hog, brigitte, raised=True)
        self.assertTrue(shield.active)
        self.step(run, hog, brigitte, fire=16, raised=True)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        run.step({brigitte: (-32768, 0, 3)})
        for _ in range(10):
            if fight.is_stunned(hog):
                break
            self.step(run, hog, brigitte, raised=True)
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(fight.stuns[hog.body] - run.frame, 47)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 597.5)
        instance = fight._barrier_instance(brigitte)
        deadline = fight.stuns[hog.body]
        while run.frame < deadline - 1:
            self.step(run, hog, brigitte, fire=8|16, raised=True)
            self.assertFalse(hog.body_script.component.held)
        self.step(run, hog, brigitte, raised=True)
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertGreater(instance.find_var(0, 13062).value(), 6)

    def test_shield_bash_moves_into_contact_and_stops_on_hit(self):
        run, hog, brigitte, _ = self.session(distance=5)
        fight = run.match.combat
        for _ in range(10):
            self.step(run, hog, brigitte, raised=True)
        start = brigitte.mover.position[2]
        run.step({brigitte: (-32768, 0, 3)})
        self.assertFalse(fight.is_stunned(hog))
        for _ in range(12):
            if fight.is_stunned(hog):
                break
            self.step(run, hog, brigitte, raised=True)
        self.assertTrue(fight.is_stunned(hog))
        contact = brigitte.mover.position[2]
        self.assertLess(contact, start-1)
        for _ in range(20):
            self.step(run, hog, brigitte, raised=True)
        self.assertAlmostEqual(brigitte.mover.position[2], contact, places=3)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 595)

    def test_projected_barrier_stops_bash_travel_without_damage_or_control(self):
        run, hog, brigitte, _ = self.session(distance=5)
        match = run.match
        fight = match.combat
        zarya = run.players[2]
        hero = next(h for h in phases.heroes().values() if h.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, brigitte.team, zarya.team = 0, 1, 0
        x, y, z = hog.mover.position
        zarya.mover.position[:] = (x - 4, y, z)
        zarya.mover.body = None
        look = aim(fight._eye(zarya), (x, y + 1, z))
        for _ in range(35):
            self.step(run, hog, brigitte, raised=True)
        run.step({zarya: (*look, 16), brigitte: (-32768, 0, 2)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        zarya_cooldown = bubble.phase.instance.vars[61]
        instance = fight._barrier_instance(brigitte)
        window = instance.state(instance.graph.node(21).state)
        cooldown = instance.find_var(0, 13062)
        health = phases.RoadhogDefenseTests().health

        self.step(run, hog, brigitte, fire=16, raised=True)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        start = brigitte.mover.position[2]
        run.step({brigitte: (-32768, 0, 3)})
        self.assertEqual(window.counter, 1)
        for _ in range(20):
            self.step(run, hog, brigitte, raised=True)
            self.assertFalse(fight.is_stunned(hog))
            self.assertEqual(health(run, hog), 600)
            self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
            self.assertEqual(bubble.health, 200)
            self.assertGreaterEqual(math.dist(fight._eye(brigitte), bubble.centre()), zarya_barriers.RADIUS)
        contact = brigitte.mover.position[2]
        self.assertGreater(contact, z + 1)
        self.assertLess(contact, start - 2)
        self.assertGreater(cooldown.value(), 6)

        for _ in range(145):
            if fight.zarya_bubbles.for_target(hog) is None:
                break
            self.step(run, hog, brigitte, raised=True)
            self.assertAlmostEqual(brigitte.mover.position[2], contact, places=3)
        self.assertIsNone(fight.zarya_bubbles.for_target(hog))
        self.assertFalse(bubble.phase.active)
        self.assertGreater(zarya_cooldown.value(), 0)
        self.assertGreater(cooldown.value(), 0)
        run.step({zarya: (*look, 16), brigitte: (-32768, 0, 3)})
        self.assertIsNone(fight.zarya_bubbles.for_target(hog))
        for _ in range(460):
            self.step(run, hog, brigitte, raised=True)
            self.assertEqual(window.counter, 1)
            self.assertEqual(health(run, hog), 600)
            self.assertFalse(fight.is_stunned(hog))
            if cooldown.value() == 0:
                break
        self.assertEqual(cooldown.value(), 0)

        run.step({brigitte: (-32768, 0, 3)})
        self.assertEqual(window.counter, 2)
        self.assertEqual(health(run, hog), 595)
        self.assertEqual(fight.stuns[hog.body] - run.frame, 47)
        for _ in range(710):
            if zarya_cooldown.value() == 0:
                break
            self.step(run, hog, brigitte, raised=True)
            self.assertIsNone(fight.zarya_bubbles.for_target(hog))
        self.assertEqual(zarya_cooldown.value(), 0)
        run.step({zarya: (*look, 16)})
        second = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(second)
        self.assertNotEqual(second.entity, bubble.entity)
        self.assertEqual(second.health, 200)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "Reinhardt model input required")
    def test_shield_bash_is_stopped_only_by_hostile_raised_barrier(self):
        for hostile in (False, True):
            with self.subTest(hostile=hostile):
                run, hog, brigitte, _ = self.session(distance=5)
                match = run.match
                fight = match.combat
                reinhardt = run.players[2]
                hero = next(h for h in phases.heroes().values() if h.name == "Reinhardt")
                self.assertTrue(match.switch_hero(reinhardt, hero))
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team, brigitte.team = 0, 1
                reinhardt.team = hog.team if hostile else brigitte.team
                x, y, z = hog.mover.position
                reinhardt.mover.position[:] = (x, y, z + 1)
                reinhardt.mover.body = None
                for _ in range(35):
                    run.step({brigitte: (-32768, 0, 2), reinhardt: (0, 0, 2)})
                shield = fight.barriers[reinhardt.body]
                self.assertTrue(shield.active)
                pool = shield.current
                health = phases.RoadhogDefenseTests().health
                before = health(run, reinhardt)
                run.step({brigitte: (-32768, 0, 3), reinhardt: (0, 0, 2)})
                for _ in range(20):
                    run.step({brigitte: (-32768, 0, 2), reinhardt: (0, 0, 2)})
                self.assertEqual(shield.current, pool)
                self.assertEqual(health(run, reinhardt), before)
                self.assertFalse(fight.is_stunned(reinhardt))
                self.assertEqual(health(run, hog), 600 if hostile else 595)
                self.assertEqual(fight.is_stunned(hog), not hostile)
                if hostile:
                    self.assertGreater(brigitte.mover.position[2], z + 1)
                    self.assertLess(brigitte.mover.position[2], z + 4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "Reinhardt model input required")
    def test_lowering_barrier_does_not_resume_an_intercepted_bash(self):
        for release in ("before contact", "on contact", "after contact"):
            with self.subTest(release=release):
                self.check_bash_barrier_release(release)

    def check_bash_barrier_release(self, release):
        run, hog, brigitte, _ = self.session(distance=3.25)
        match = run.match
        fight = match.combat
        reinhardt = run.players[2]
        hero = next(h for h in phases.heroes().values() if h.name == "Reinhardt")
        self.assertTrue(match.switch_hero(reinhardt, hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, reinhardt.team, brigitte.team = 0, 0, 1
        x, y, z = hog.mover.position
        reinhardt.mover.position[:] = (x + 1.25, y, z + 1)
        reinhardt.mover.body = None
        raised = (0, 0, 2)
        guard = (-32768, 0, 2)
        for _ in range(35):
            run.step({brigitte: guard, reinhardt: raised})
        shield = fight.barriers[reinhardt.body]
        self.assertTrue(shield.active)
        instance = fight._barrier_instance(brigitte)
        window = instance.state(instance.graph.node(21).state)
        cooldown = instance.find_var(0, 13062)
        start = brigitte.mover.position[2]
        run.step({brigitte: (-32768, 0, 3), reinhardt: raised})
        self.assertTrue(window.active)
        cast_frame = run.frame

        if release == "on contact":
            # The Brigitte command moves before Reinhardt's command lowers his shield.
            reach = instance.find_var(0, 7726).value() * 60 * combat.TICK
            for _ in range(6):
                hit = fight._barrier_contact(brigitte, fight._eye(brigitte), (0, 0, -1), reach)
                if hit is not None:
                    break
                run.step({brigitte: guard, reinhardt: raised})
            self.assertIsNotNone(hit)
            self.assertTrue(window.active)
        elif release == "after contact":
            for _ in range(6):
                before = brigitte.mover.position[2]
                run.step({brigitte: guard, reinhardt: raised})
                if brigitte.mover.position[2] == before and before < start:
                    break
            self.assertEqual(brigitte.mover.position[2], before)
            self.assertLess(before, start)
        self.assertLess((run.frame + 1 - cast_frame) * combat.TICK * 1000, window.ms)
        run.step({brigitte: guard})
        self.assertFalse(shield.active)
        contact = brigitte.mover.position[2]
        contacted = release != "before contact"
        self.assertEqual(window.active, not contacted)

        for _ in range(20):
            run.step({brigitte: guard})
        health = phases.RoadhogDefenseTests().health
        self.assertEqual(health(run, hog), 600 if contacted else 595)
        self.assertEqual(fight.is_stunned(hog), not contacted)
        self.assertEqual(window.counter, 1)
        self.assertFalse(window.active)
        self.assertGreater(cooldown.value(), 6)
        if contacted:
            self.assertAlmostEqual(brigitte.mover.position[2], contact, places=3)

    def test_shield_bash_and_sleep_dart_use_the_later_control_deadline(self):
        for order in ("early bash", "late bash", "bash first"):
            with self.subTest(order=order):
                self.check_bash_sleep_overlap(order)

    def check_bash_sleep_overlap(self, order):
        fixture = ana_tests.RoadhogAnaTests()
        run, hog, ana = fixture.session()
        match = run.match
        fight = match.combat
        brigitte = run.players[2]
        hero = next(h for h in phases.heroes().values() if h.name == "Brigitte")
        self.assertTrue(match.switch_hero(brigitte, hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, ana.team, brigitte.team = 0, 1, 1
        x, y, z = hog.mover.position
        brigitte.mover.position[:] = (x + 2, y, z)
        brigitte.mover.body = None
        look = aim(fight._eye(brigitte), fight._eye(hog))
        for _ in range(35):
            run.step({brigitte: (*look, 2)})
        self.assertTrue(fight.barriers[brigitte.body].active)
        pool = fixture.pool(run, hog)
        body = hog.body

        if order != "bash first":
            original_sleep = fixture.land(run, hog, ana, 8)
            floor = fight.sleep_wake_floor[body]
            self.assertEqual(pool.total, 595)
            if order == "late bash":
                while run.frame < floor:
                    run.step({brigitte: (*look, 2)})
            else:
                # The Ana-only fixture sends neutral input to Brigitte.
                run.step({brigitte: (*look, 2)})
            self.assertEqual(fight.sleep[body], original_sleep)

        self.assertTrue(fight.barriers[brigitte.body].active)
        before = pool.total
        run.step({brigitte: (*look, 3)})
        for _ in range(15):
            if pool.total < before:
                break
            run.step({brigitte: (*look, 2)})
        self.assertEqual(pool.total, before - 5)
        bash_end = run.frame + 47
        if order == "early bash":
            self.assertLess(bash_end, floor)
            self.assertEqual(fight.sleep[body], floor)
            self.assertNotIn(body, fight.stuns)
            deadline = floor
        elif order == "late bash":
            self.assertLessEqual(floor, run.frame)
            self.assertGreater(bash_end, run.frame + math.ceil(0.5 / combat.TICK))
            self.assertLess(bash_end, original_sleep)
            self.assertNotIn(body, fight.sleep)
            self.assertNotIn(body, fight.sleep_wake_floor)
            self.assertEqual(fight.stuns[body], bash_end)
            deadline = bash_end
        else:
            self.assertEqual(fight.stuns[body], bash_end)
            deadline = fixture.land(run, hog, ana, 8)
            self.assertLess(run.frame, bash_end)
            self.assertEqual(deadline - run.frame, math.ceil(5 / combat.TICK))
            self.assertEqual(fight.sleep_wake_floor[body] - run.frame, math.ceil(1.5 / combat.TICK))
            self.assertNotIn(body, fight.stuns)
        self.assertEqual(pool.total, 590)
        self.assertEqual(fight.control_deadline(body), deadline)

        x, y, z = ana.mover.position
        angles = aim(fight._eye(hog), (x, y + 1, z))
        while run.frame < deadline - 1:
            run.step({hog: (*angles, 8 | 16)})
            self.assertTrue(fight.is_stunned(hog))
            self.assertFalse(hog.body_script.component.held)
        run.step({hog: (*angles, 0)})
        self.assertEqual(run.frame, deadline)
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(body, fight.sleep_wake_floor)
        for _ in range(20):
            run.step({hog: (*angles, 0)})
            self.assertNotIn(body, fight.hooks.flights)
            self.assertEqual(fight.incoming_damage_scale(hog), 1)
            self.assertEqual(pool.total, 590)

        ana_pool = fixture.pool(run, ana)
        before = ana_pool.total
        run.step({hog: (*angles, 8)})
        for _ in range(65):
            if fight.hooks.controlled(ana.body):
                break
            run.step({hog: (*angles, 0)})
        self.assertTrue(fight.hooks.controlled(ana.body))
        self.assertEqual(ana_pool.total, before - 30)

    def test_shield_bash_cancels_hook_flight_and_active_pull(self):
        for caught in (False, True):
            with self.subTest(caught=caught):
                self.check_bash_cancels_hook(caught)

    def check_bash_cancels_hook(self, caught):
        run, hog, brigitte, _ = self.session(distance=2)
        fight = run.match.combat
        victim = run.players[2]
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x, y, z-18)
        victim.mover.body = None
        for _ in range(12):
            self.step(run, hog, brigitte, raised=True)
        x, y, z = victim.mover.position
        angles = aim(fight._eye(hog), (x, y+1, z))
        run.step({hog: (*angles, 8), brigitte: (-32768, 0, 2)})
        for _ in range(65):
            flight_ready = hog.body in fight.hooks.flights
            pull_ready = fight.hooks.controlled(victim.body)
            if (pull_ready if caught else flight_ready):
                break
            run.step({hog: (*angles, 0), brigitte: (-32768, 0, 2)})
        self.assertIn(hog.body, fight.hooks.flights)
        self.assertEqual(fight.hooks.controlled(victim.body), caught)
        flight = fight.hooks.flights[hog.body]
        self.assertIsNone(flight.arrived_at)
        hook = stun_tests.RoadhogStunTests().ability(hog, 0x01C8)
        before = hook.remaining()
        run.step({hog: (*angles, 0), brigitte: (-32768, 0, 3)})
        for _ in range(15):
            if fight.is_stunned(hog):
                break
            run.step({hog: (*angles, 0), brigitte: (-32768, 0, 2)})
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(fight.stuns[hog.body] - run.frame, 47)
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(victim.body))
        self.assertGreater(hook.remaining(), 0)
        self.assertLessEqual(hook.remaining(), before)
        position = tuple(victim.mover.position)
        for _ in range(55):
            run.step({hog: (*angles, 8), brigitte: (-32768, 0, 2)})
            self.assertFalse(fight.hooks.controlled(victim.body))
            self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(tuple(victim.mover.position), position)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, victim), 19970 if caught else 20000)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_shield_bash_on_hook_owner_releases_all_five_npc_pulls(self):
        import test_game_roadhog_npcs as actual

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        npc_scene = fixture()
        hero = next(h for h in phases.heroes().values() if h.name == "Brigitte")
        health = phases.RoadhogDefenseTests().health
        for definition in fixture.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, vitals, angles = npc_scene.session(definition, distance=18, caster=True)
                match = run.match
                fight = match.combat
                brigitte = run.players[1]
                self.assertTrue(match.switch_hero(brigitte, hero))
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team, brigitte.team = 0, bot.team
                x, y, z = hog.mover.position
                brigitte.mover.position[:] = (x + 2, y, z)
                brigitte.mover.body = None
                look = aim(fight._eye(brigitte), fight._eye(hog))
                for _ in range(35):
                    run.step({brigitte: (*look, 2)})
                self.assertTrue(fight.barriers[brigitte.body].active)
                initial = vitals.total
                run.step({hog: (*angles, 8), brigitte: (*look, 2)})
                for _ in range(65):
                    if fight.hooks.controlled(bot.entity):
                        break
                    run.step({brigitte: (*look, 2)})
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertIsNone(fight.hooks.flights[hog.body].arrived_at)
                hook = stun_tests.RoadhogStunTests().ability(hog, 0x01C8)
                before = hook.remaining()
                run.step({brigitte: (*look, 3)})
                self.assertEqual(fight.stuns.get(hog.body, 0) - run.frame, 47)
                self.assertEqual(health(run, hog), 595)
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertFalse(fight.hooks.controlled(bot.entity))
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertGreater(hook.remaining(), 0)
                self.assertLessEqual(hook.remaining(), before)
                position = tuple(bot.position)
                for _ in range(60):
                    run.step({hog: (*angles, 8), brigitte: (*look, 2)})
                    self.assertFalse(fight.hooks.controlled(bot.entity))
                self.assertFalse(fight.is_stunned(hog))
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertEqual(tuple(bot.position), position)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertEqual(vitals.total, initial - 30)

    def test_shield_bash_stops_whole_hog_without_refund_or_replay(self):
        run, hog, brigitte, _ = self.session(distance=2)
        fight = run.match.combat
        witness = run.players[2]
        x, y, z = hog.mover.position
        witness.mover.position[:] = (x, y, z - 8)
        witness.mover.body = None
        for _ in range(12):
            self.step(run, hog, brigitte, raised=True)
        x, y, z = witness.mover.position
        angles = aim(fight._eye(hog), (x, y + 1, z))
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        charge = script.component.entity_var(ULT_CHARGE)
        ultimate, = script.ultimates
        health = phases.RoadhogDefenseTests().health
        run.step({hog: (*angles, 32), brigitte: (-32768, 0, 2)})
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        for _ in range(85):
            run.step({hog: (*angles, 0), brigitte: (-32768, 0, 2)})
            if health(run, witness) < 20000:
                break
        self.assertLess(health(run, witness), 20000)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)

        run.step({hog: (*angles, 0), brigitte: (-32768, 0, 3)})
        for _ in range(15):
            if fight.is_stunned(hog):
                break
            run.step({hog: (*angles, 0), brigitte: (-32768, 0, 2)})
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(health(run, hog), 595)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), 0)
        stopped = health(run, witness)
        deadline = fight.stuns[hog.body]
        for _ in range(75):
            button = 32 if run.frame < deadline - 1 else 0
            run.step({hog: (*angles, button), brigitte: (-32768, 0, 2)})
            self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
            self.assertEqual(health(run, witness), stopped)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(charge.value(), 0)
        run.step({hog: (*angles, 1), brigitte: (-32768, 0, 2)})
        for _ in range(20):
            run.step({hog: (*angles, 0), brigitte: (-32768, 0, 2)})
        self.assertLess(health(run, witness), stopped)

    def test_roadhog_hook_through_unprotected_rear_prevents_bash_until_release(self):
        run, hog, brigitte, shield = self.session(distance=8)
        fight = run.match.combat
        for _ in range(10):
            self.step(run, hog, brigitte, raised=True, facing=0)
        self.assertTrue(shield.active)
        self.step(run, hog, brigitte, fire=8, raised=True, facing=0)
        for _ in range(65):
            if fight.hooks.controlled(brigitte.body):
                break
            self.step(run, hog, brigitte, raised=True, facing=0)
        self.assertTrue(fight.hooks.controlled(brigitte.body))
        instance = fight._barrier_instance(brigitte)
        window = instance.state(instance.graph.node(21).state)
        for _ in range(5):
            run.step({brigitte: (-32768, 0, 3)})
            self.assertTrue(fight.hooks.controlled(brigitte.body))
            self.assertEqual(window.counter, 0)
        for _ in range(80):
            self.step(run, hog, brigitte)
            self.assertEqual(window.counter, 0)
            self.assertFalse(fight.is_stunned(hog))
            if not fight.hooks.controlled(brigitte.body):
                break
        self.assertFalse(fight.hooks.controlled(brigitte.body))
        self.assertFalse(shield.active)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, brigitte), 19970)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)
        for _ in range(15):
            self.step(run, hog, brigitte, raised=True)
        run.step({brigitte: (-32768, 0, 3)})
        self.assertEqual(window.counter, 1)

    def test_shield_bash_cooldown_refuses_repress_and_accepts_fresh_cast(self):
        run, hog, brigitte, _ = self.session(distance=2)
        fight = run.match.combat
        for _ in range(10):
            self.step(run, hog, brigitte, raised=True)
        instance = fight._barrier_instance(brigitte)
        window = instance.state(instance.graph.node(21).state)
        cooldown = instance.find_var(0, 13062)
        health = phases.RoadhogDefenseTests().health
        run.step({brigitte: (-32768, 0, 3)})
        for _ in range(65):
            self.step(run, hog, brigitte, raised=True)
        self.assertEqual(window.counter, 1)
        self.assertEqual(health(run, hog), 595)
        self.assertGreater(cooldown.value(), 5)
        run.step({brigitte: (-32768, 0, 3)})
        for _ in range(460):
            self.step(run, hog, brigitte, raised=True)
            self.assertEqual(window.counter, 1)
            if cooldown.value() <= 0:
                break
        self.assertEqual(cooldown.value(), 0)
        self.assertEqual(health(run, hog), 595)
        run.step({brigitte: (-32768, 0, 3)})
        self.assertEqual(window.counter, 2)
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(health(run, hog), 590)

    def test_shield_bash_cannot_cross_triangle_wall_or_hit_through_it(self):
        run, hog, brigitte, _ = self.session(distance=5)
        fight = run.match.combat
        x, y, z = hog.mover.position
        wall_z = z + 3
        world = World([
            (x-5, y-2, wall_z, x+5, y-2, wall_z, x+5, y+5, wall_z),
            (x-5, y-2, wall_z, x+5, y+5, wall_z, x-5, y+5, wall_z),
        ])
        run.match.collision_world = brigitte.mover.world = world
        brigitte.mover.body = None
        for _ in range(10):
            self.step(run, hog, brigitte, raised=True)
        start = brigitte.mover.position[2]
        run.step({brigitte: (-32768, 0, 3)})
        for _ in range(20):
            self.step(run, hog, brigitte, raised=True)
            self.assertGreater(brigitte.mover.position[2], wall_z)
            self.assertFalse(fight.is_stunned(hog))
        self.assertLess(brigitte.mover.position[2], start-1)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_shield_bash_requires_raised_shield_and_respects_team_and_facing(self):
        for case in ("lowered", "ally", "away"):
            with self.subTest(case=case):
                run, hog, brigitte, shield = self.session(distance=5)
                fight = run.match.combat
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    brigitte.team = hog.team
                yaw = 0 if case == "away" else -32768
                raised = case != "lowered"
                for _ in range(10):
                    self.step(run, hog, brigitte, raised=raised, facing=yaw)
                self.assertEqual(shield.active, raised)
                instance = fight._barrier_instance(brigitte)
                window = instance.state(instance.graph.node(21).state)
                start = brigitte.mover.position[2]
                run.step({brigitte: (yaw, 0, 3 if raised else 1)})
                for _ in range(20):
                    self.step(run, hog, brigitte, raised=raised, facing=yaw)
                    self.assertFalse(fight.is_stunned(hog))
                self.assertEqual(window.counter, int(raised))
                self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)
                if case == "away":
                    self.assertGreater(brigitte.mover.position[2], start+1)

    def test_raised_brigitte_shield_cuts_only_crossing_live_hook_without_spending_health(self):
        for lateral, crosses, allied in HOOK_SHIELD_LAYOUTS:
            with self.subTest(crosses=crosses, allied=allied):
                self.check_raised_brigitte_shield_live_hook(lateral, crosses, allied)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                         "actual manual NPC inputs required")
    def test_raised_brigitte_shield_cuts_crossing_live_hook_on_each_npc(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for lateral, crosses, allied in HOOK_SHIELD_LAYOUTS:
                with self.subTest(npc=definition.name, crosses=crosses, allied=allied):
                    self.check_raised_brigitte_shield_live_hook(
                        lateral, crosses, allied, definition=definition, client=actual.NpcClient,
                    )

    def check_raised_brigitte_shield_live_hook(self, lateral, crosses, allied, *, definition=None, client=None):
        run, hog, caster, shield = self.session()
        fight = run.match.combat
        cuts = crosses and not allied
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        rear = run.players[2]
        hog.team, caster.team, rear.team = 0, int(not allied), 1
        y = hog.mover.position[1]
        caster.mover.position[:] = (5, y, 5)
        caster.mover.body = None
        if definition is None:
            victim = rear
            victim.mover.position[:] = (0, y, 17)
            victim.mover.body = None
        else:
            for player in run.players:
                player.client = client(player)
            victim = npc.spawn(run.match, definition, (0, y, 17), team=1)
        for _ in range(10):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is victim)
        entity = target.entity
        pool = fight.vitals_of(target)

        def depth():
            return (victim.mover.position if definition is None else victim.position)[2]

        before = pool.total
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        look = aim(fight._eye(hog), centre)
        run.step({hog: (*look, 8)})
        for _ in range(55):
            run.step({hog: (*look, 0)})
            if fight.hooks.controlled(entity):
                break
        self.assertTrue(fight.hooks.controlled(entity))
        self.assertEqual(pool.total, before - 30)
        # Position the support body after latch so the test isolates the newly raised mesh.
        caster.mover.position[:] = (lateral, y, 5)
        caster.mover.body = None
        for _ in range(3):
            run.step({caster: (-32768, 0, 0)})
            self.assertTrue(fight.hooks.controlled(entity))
            self.assertFalse(shield.active)
        position = depth()
        for _ in range(10):
            run.step({caster: (-32768, 0, 2)})
            if not fight.hooks.controlled(entity):
                break
        self.assertTrue(shield.active)
        self.assertEqual(fight.hooks.controlled(entity), not cuts)
        current = next(t for t in fight._targets() if t.owner is victim)
        origin = fight._eye(hog)
        x, y, z = current.feet
        delta = (x - origin[0], y + current.height / 2 - origin[1], z - origin[2])
        length = math.hypot(*delta)
        direction = tuple(axis / length for axis in delta)
        reach = length - current.radius
        mover = caster.mover
        crossing = barriers.intersect(fight._attached_geometry(caster), mover.position, mover.yaw,
                                      mover.pitch, origin, direction, reach, 0,
                                      mover.values.stand_radius, mover.values.stand_height)
        self.assertEqual(crossing is not None, crosses)
        contacts = fight._barrier_contacts(hog, origin, direction, reach)
        self.assertEqual(any(hit[1] is caster for hit in contacts), cuts)
        if cuts:
            position = depth()
        for _ in range(6):
            run.step({})
        if cuts:
            self.assertEqual(depth(), position)
        else:
            self.assertLess(depth(), position)
        if definition is not None and cuts:
            self.assertEqual(victim.velocity, (0, 0, 0))
        self.assertFalse(shield.active)
        self.assertEqual(shield.current, 250)
        self.assertEqual(pool.total, before - 30)

    def test_raise_primary_hook_release_and_repair(self):
        run, hog, brigitte, state = self.session()
        fight = run.match.combat
        health = phases.RoadhogDefenseTests().health
        pool_var = fight._barrier_instance(brigitte).find_var(0, 586)
        for _ in range(15):
            self.step(run, hog, brigitte, raised=True)
        self.assertTrue(state.active)
        self.assertEqual(len(fight.brigitte_geometry.triangles), 132)
        self.step(run, hog, brigitte, fire=1, raised=True)
        for _ in range(20):
            self.step(run, hog, brigitte, raised=True)
        self.assertAlmostEqual(state.current, 85, places=3)
        self.assertEqual(pool_var.value(), state.current)
        self.assertEqual(health(run, brigitte), 20000)
        self.step(run, hog, brigitte, fire=8, raised=True)
        for _ in range(30):
            self.step(run, hog, brigitte, raised=True)
        self.assertAlmostEqual(state.current, 55, places=3)
        self.assertEqual(pool_var.value(), state.current)
        self.assertEqual(health(run, brigitte), 20000)
        self.assertFalse(fight.hooks.controlled(brigitte.body))
        for _ in range(100):
            self.step(run, hog, brigitte)
        self.assertFalse(state.active)
        self.assertAlmostEqual(state.current, 55, places=3)
        for _ in range(200):
            self.step(run, hog, brigitte)
        self.assertEqual(state.current, 250)
        self.assertEqual(pool_var.value(), state.current)

    def test_close_secondary_hits_body_from_behind_raised_shield(self):
        run, hog, brigitte, state = self.session()
        for _ in range(15):
            self.step(run, hog, brigitte, raised=True, facing=0)
        self.assertTrue(state.active)
        self.step(run, hog, brigitte, fire=2, raised=True, facing=0)
        for _ in range(25):
            self.step(run, hog, brigitte, raised=True, facing=0)
        self.assertEqual(state.current, 250)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, brigitte), 19950)

    def test_whole_hog_breaks_barrier_and_later_hits_body(self):
        run, hog, brigitte, state = self.session()
        for _ in range(15):
            self.step(run, hog, brigitte, raised=True)
        hog.body_script.set_ult_charge(2240)
        self.step(run, hog, brigitte, fire=32, raised=True)
        for _ in range(140):
            self.step(run, hog, brigitte, raised=True)
        self.assertGreater(state.broken_until, run.match.combat.now)
        self.assertFalse(state.active)
        self.assertLess(phases.RoadhogDefenseTests().health(run, brigitte), 20000)
        self.assertEqual(brigitte.hero.guid, barriers.BRIGITTE_HERO)
        self.assertTrue(hog.client.messages(combat.HIT))
        while run.match.combat.now < state.broken_until - combat.TICK:
            self.step(run, hog, brigitte, raised=True)
            self.assertFalse(state.active)
        for _ in range(5):
            self.step(run, hog, brigitte)
        self.step(run, hog, brigitte, raised=True)
        self.assertTrue(state.active)
        self.assertGreater(state.current, 0)

    def test_front_melee_and_secondary_fragments_reach_the_mesh(self):
        for distance, button, body_damage in ((1.7, 0x800, 30), (10, 2, 0)):
            with self.subTest(button=button):
                run, hog, brigitte, state = self.session(distance)
                for _ in range(15):
                    self.step(run, hog, brigitte, raised=True)
                self.step(run, hog, brigitte, fire=button, raised=True)
                for _ in range(35):
                    self.step(run, hog, brigitte, raised=True)
                self.assertLess(state.current, 250)
                # Quick melee can overlap both a world barrier and a reachable body.
                self.assertEqual(phases.RoadhogDefenseTests().health(run, brigitte), 20000 - body_damage)

    def test_replacement_retires_raised_barrier(self):
        from ow174.game.content import find_hero

        run, hog, brigitte, state = self.session()
        for _ in range(15):
            self.step(run, hog, brigitte, raised=True)
        self.assertTrue(state.active)
        body = brigitte.body
        run.match.switch_hero(brigitte, find_hero("Reaper"))
        self.assertNotIn(body, run.match.combat.barriers)
        self.assertNotIn(brigitte.body, run.match.combat.barriers)


    def test_raised_barrier_is_stored_in_snapshot(self):
        import test_server_snapshot as stored

        from ow174.game.server_snapshot import snapshot_digest

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene()
        brigitte = players[0]
        session.apply("switch_hero", {"account": brigitte.account_lo, "hero": barriers.BRIGITTE_HERO})
        fight = brigitte.match.combat
        for offset in range(52):
            frame = brigitte.body_script.frame + 1
            fight.tick = brigitte.match.tick = frame
            fight.now = frame * combat.TICK
            brigitte.body_script.command(frame, 2 if offset >= 35 else 0)
            state = fight._sync_barrier(brigitte)
        self.assertTrue(state.active)
        before = snapshot_digest(case.capture(session))
        self.assertEqual(snapshot_digest(case.capture(session)), before)
        state.current -= 10
        self.assertNotEqual(snapshot_digest(case.capture(session)), before)


    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graphs required")
    def test_brigitte_protects_five_actual_npc_targets_from_primary_slug_and_hook(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        actual.RoadhogFiveNpcTests.setUpClass()
        try:
            for definition in actual.RoadhogFiveNpcTests.definitions.values():
                for button, damage in ((1, 165), (2, 50), (8, 30)):
                    with self.subTest(npc=definition.name, button=button):
                        run, hog, brigitte, shield = self.session()
                        fight = run.match.combat
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        hog.team, brigitte.team = 0, 1
                        bot = npc.spawn(run.match, definition, (0, hog.mover.position[1], 8), team=1)
                        for _ in range(15):
                            self.step(run, hog, brigitte, raised=True)
                        target = next(t for t in fight._targets() if t.owner is bot)
                        pool = fight.vitals_of(target)
                        before = pool.total
                        self.step(run, hog, brigitte, fire=button, raised=True)
                        for _ in range(40):
                            self.step(run, hog, brigitte, raised=True)
                        self.assertAlmostEqual(shield.current, 250 - damage, places=3)
                        self.assertEqual(pool.total, before)
                        self.assertEqual(phases.RoadhogDefenseTests().health(run, brigitte), 20000)
                        self.assertFalse(fight.hooks.controlled(bot.entity))
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graphs required")
    def test_four_manual_npc_weapons_hit_allied_shield_before_roadhog(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc, npc_control

        actual.RoadhogFiveNpcTests.setUpClass()
        try:
            for definition in actual.RoadhogFiveNpcTests.definitions.values():
                if definition.name == "Assassin":
                    continue
                with self.subTest(npc=definition.name):
                    run, hog, brigitte, shield = self.session(6)
                    fight = run.match.combat
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = brigitte.team = 0
                    brigitte.mover.position[0] = 3
                    brigitte.mover.body = None
                    for player in run.players:
                        player.client = actual.NpcClient(player)
                    bot = npc.spawn(run.match, definition, (0, hog.mover.position[1], 12), team=1)
                    for _ in range(15):
                        self.step(run, hog, brigitte, raised=True, facing=0)
                    target = next(t for t in fight._targets() if t.owner is bot)
                    a, b = target.segment()
                    centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
                    hog.mover.yaw, hog.mover.pitch = aim(fight._eye(hog), centre)
                    self.assertEqual(npc_control.start(run.match, hog), bot.entity)
                    brigitte.mover.position[0] = 0
                    brigitte.mover.body = None
                    for _ in range(280):
                        self.step(run, hog, brigitte, raised=True, facing=0)
                        if shield.current < 250:
                            break
                    self.assertLess(shield.current, 250)
                    self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)
                    self.assertEqual(phases.RoadhogDefenseTests().health(run, brigitte), 20000)
                    npc_control.stop(run.match)
                    self.assertFalse(bot.firing)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()

    def test_flashbang_from_front_is_blocked_and_rear_hit_lowers_shield(self):
        from ow174.game.content import heroes

        health = phases.RoadhogDefenseTests().health
        for rear in (False, True):
            with self.subTest(rear=rear):
                run, hog, brigitte, shield = self.session()
                fight = run.match.combat
                caster = run.players[2]
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                x, y, z = brigitte.mover.position
                caster.mover.position[:] = (x, y, z + (2.5 if rear else -2.5))
                caster.mover.body = None
                for _ in range(35):
                    self.step(run, hog, brigitte, raised=True)
                self.assertTrue(shield.active)
                angles = aim(fight._eye(caster), (x, y+1, z))
                run.step({caster: (*angles, 16), brigitte: (-32768, 0, 2)})
                for _ in range(40):
                    self.step(run, hog, brigitte, raised=True)
                    if fight.is_stunned(brigitte) or shield.current < 250:
                        break
                if rear:
                    self.assertEqual(health(run, brigitte), 19975)
                    self.assertTrue(fight.is_stunned(brigitte))
                    self.step(run, hog, brigitte, raised=True)
                    self.assertFalse(shield.active)
                    self.assertEqual(shield.current, 250)
                    while fight.is_stunned(brigitte):
                        self.step(run, hog, brigitte, raised=True)
                    self.step(run, hog, brigitte)
                    self.step(run, hog, brigitte, raised=True)
                    self.assertTrue(shield.active)
                else:
                    self.assertEqual(health(run, brigitte), 20000)
                    self.assertEqual(shield.current, 225)
                    self.assertFalse(fight.is_stunned(brigitte))
                    self.assertTrue(shield.active)
