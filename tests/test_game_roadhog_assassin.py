"""Actual1157 manual pin versus Roadhog; clock/contact/controller policy reconstructed."""

import os
import unittest
from dataclasses import replace

import test_game_roadhog_npcs as bodies
from test_game_combat import aim

from ow174.game import assassin, combat, npc_control, stats
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_COST
from ow174.game.world import HEALTH_POOL


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                     "requires actual five-NPC graphs/catalog and mover inputs")
class RoadhogAssassinTests(unittest.TestCase):
    session = bodies.RoadhogFiveNpcTests.session

    @classmethod
    def setUpClass(cls):
        bodies.RoadhogFiveNpcTests.setUpClass()
        cls.definitions = bodies.RoadhogFiveNpcTests.definitions

    @classmethod
    def tearDownClass(cls):
        bodies.RoadhogFiveNpcTests.tearDownClass()

    def encounter(self, *, support=False):
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        run, hog, actor, hp, angles = self.session(definition, distance=2, caster=support)
        if support:
            run.players[1].client = bodies.NpcClient(run.players[1])
        own = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is hog))
        self.assertEqual(actor.body_script.component.world.ai_weapon_button(0), 36)
        return run, hog, actor, hp, angles, own

    def engage(self, run, hog, actor, angles):
        hog.mover.yaw, hog.mover.pitch = angles
        self.assertEqual(npc_control.start(run.match, hog), actor.entity)
        for _ in range(100):
            run.step({})
            if run.match.combat.pins.get(actor.entity) == hog.body:
                return
        self.fail("actual1157 did not pin the selected Roadhog body")

    def phase(self, actor, position):
        instance = next(i for i in actor.body_script.component.instances.values()
                        if i.graph.index == assassin.GRAPH)
        return instance.states.get(instance.graph.node(position).state)

    def test_real_contact_continuous_damage_stop_and_restart(self):
        run, hog, actor, _, angles, own = self.encounter()
        self.engage(run, hog, actor, angles)
        self.assertEqual(own.total, 575)
        for _ in range(100):
            if (state := self.phase(actor, 15)) is not None and state.active:
                break
            run.step({})
        self.assertTrue(state.active)
        before = own.total
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(before - own.total, 16, places=4)
        npc_control.stop(run.match)
        self.assertFalse(run.match.combat.pins)
        before = own.total
        for _ in range(80):
            run.step({})
        self.assertEqual(own.total, before)
        self.engage(run, hog, actor, angles)
        self.assertEqual(own.total, before - 25)

    def test_pin_stops_whole_hog_without_refund_and_masks_attack_and_movement(self):
        run, hog, actor, _, angles, own = self.encounter()
        fight = run.match.combat
        fight.vitals[actor.entity] = combat.Vitals(actor.entity, 20000)
        fight._sync_body_health(actor, fight.vitals[actor.entity])
        hog.body_script.set_ult_charge(2240)
        run.step({hog: (*angles, 32)})
        self.assertTrue(any(s.flags & nodes.Ability.RUNNING for s in hog.body_script.ultimates))
        self.engage(run, hog, actor, angles)
        self.assertFalse(any(s.flags & nodes.Ability.RUNNING for s in hog.body_script.ultimates))
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
        before = fight.vitals[actor.entity].total
        position = tuple(hog.mover.position)
        for _ in range(10):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            hog.client.queue_entities(run.match.remote_movements(hog))
            hog.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1],
                                       buttons=1 | 8 | 16 | 32 | 0x800, forward=127)])
        self.assertEqual(fight.vitals[actor.entity].total, before)
        self.assertEqual(tuple(hog.mover.position), position)
        self.assertLess(own.total, 600)
        self.assertFalse(fight.hooks.flights)

    def test_assassin_pin_discards_buffered_quick_melee_before_release(self):
        run, hog, actor, pool, look, own = self.encounter()
        fight = run.match.combat
        before = pool.total
        run.step({hog: (*look, 0x800)})
        first = run.frame
        run.step({})
        run.step({hog: (*look, 0x800)})
        melee = next(s for i in hog.body_script.component.instances.values() if i.graph.index == 0x0043
                     for s in i.states.values() if isinstance(s, nodes.Ability))
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        hit = melee.instance.state(4)
        self.assertEqual(pool.total, before - 30)
        self.assertTrue(melee.presses)
        self.assertEqual(hit.counter, 1)

        self.assertEqual(npc_control.start(run.match, hog), actor.entity)
        while run.frame < first + 61 and fight.pins.get(actor.entity) != hog.body:
            run.step({hog: (*look, 0x800)})
        self.assertEqual(fight.pins.get(actor.entity), hog.body)
        self.assertFalse(melee.presses)
        self.assertEqual(own.total, 575)
        for _ in range(6):
            run.step({hog: (*look, 0x800)})
            self.assertEqual(fight.pins.get(actor.entity), hog.body)
        self.assertEqual(hit.counter, 1)
        run.step({})
        npc_control.stop(run.match)
        self.assertNotIn(actor.entity, fight.pins)

        x, y, z = actor.position
        hog.mover.position[:] = (x, y, z - 1.5)
        hog.mover.body = None
        while run.frame <= first + 125:
            run.step({})
        self.assertEqual(hit.counter, 1)
        self.assertEqual(pool.total, before - 30)
        look = aim(fight._eye(hog), (x, y + .8, z))
        run.step({hog: (*look, 0x800)})
        self.assertEqual(hit.counter, 2)
        self.assertEqual(pool.total, before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_whole_hog_hits_the_pinned_victim_without_external_motion_until_release(self):
        run, hog, actor, _, angles, _ = self.encounter(support=True)
        match = run.match
        fight = match.combat
        shooter = run.players[1]
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, shooter.team = 0, actor.team
        self.assertTrue(match.switch_hero(shooter, hog.hero))
        x, y, z = hog.mover.position
        shooter.mover.position[:] = (x + 4, y, z)
        shooter.mover.body = None
        for _ in range(30):
            run.step({})
        self.engage(run, hog, actor, angles)
        anchor = tuple(hog.mover.position)
        shooter.body_script.set_ult_charge(2240)
        look = aim(fight._eye(shooter), (anchor[0], anchor[1] + 1, anchor[2]))
        run.step({shooter: (*look, 32)})
        for _ in range(65):
            run.step({shooter: (*look, 0)})
            self.assertEqual(fight.pins.get(actor.entity), hog.body)
            self.assertEqual(tuple(hog.mover.position), anchor)
            self.assertNotIn(hog.body, fight.impulses.live)
            if shooter.stats.value(stats.HERO_DAMAGE):
                break
        self.assertGreater(shooter.stats.value(stats.HERO_DAMAGE), 0)
        npc_control.stop(match)
        self.assertNotIn(actor.entity, fight.pins)
        damage = shooter.stats.value(stats.HERO_DAMAGE)
        for _ in range(20):
            run.step({shooter: (*look, 0)})
            if shooter.stats.value(stats.HERO_DAMAGE) > damage:
                self.assertIn(hog.body, fight.impulses.live)
                break
        self.assertIn(hog.body, fight.impulses.live)
        run.step({})
        self.assertLess(hog.mover.position[0], anchor[0])

    def test_assassin_contact_clears_an_existing_whole_hog_impulse(self):
        run, hog, actor, _, _, _ = self.encounter(support=True)
        match = run.match
        fight = match.combat
        shooter = run.players[1]
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, shooter.team = 0, actor.team
        self.assertTrue(match.switch_hero(shooter, hog.hero))
        x, y, z = hog.mover.position
        shooter.mover.position[:] = (x + 4, y, z)
        shooter.mover.body = None
        for _ in range(30):
            run.step({})
        shooter.body_script.set_ult_charge(2240)
        look = aim(fight._eye(shooter), (x, y + 1, z))
        run.step({shooter: (*look, 32)})
        for _ in range(50):
            run.step({shooter: (*look, 0)})
            if hog.body in fight.impulses.live:
                break
        self.assertNotEqual(fight.impulses.velocity(hog.body), (0, 0, 0))
        bx, by, bz = actor.position
        pin_look = aim(fight._eye(hog), (bx, by + .8, bz))
        self.engage(run, hog, actor, pin_look)
        self.assertNotIn(hog.body, fight.impulses.live)

    def test_pin_interrupts_breather_heal_reduction_and_keeps_cooldown(self):
        run, hog, actor, _, angles, own = self.encounter()
        own.now[HEALTH_POOL] = 300
        run.match.combat._sync_body_health(hog, own)
        run.step({hog: (*angles, 16)})
        for _ in range(36):
            run.step({})
        self.assertEqual(run.match.combat.incoming_damage_scale(hog), 0.5)
        self.engage(run, hog, actor, angles)
        self.assertEqual(run.match.combat.incoming_damage_scale(hog), 1)
        heal = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x240)
        ability = next(s for s in heal.states.values() if isinstance(s, nodes.Ability))
        self.assertGreater(ability.remaining(), 0)
        npc_control.stop(run.match)
        before = own.total
        for _ in range(60):
            run.step({})
        self.assertEqual(own.total, before)
        run.step({hog: (*angles, 16)})
        for _ in range(60):
            run.step({})
        self.assertEqual(own.total, before)

    def test_pin_cancels_already_launched_outgoing_hook(self):
        run, hog, actor, _, angles, _ = self.encounter()
        run.step({hog: (16384, 0, 8)})
        for _ in range(30):
            run.step({hog: (16384, 0, 0)})
            if hog.body in run.match.combat.hooks.flights:
                break
        self.assertIn(hog.body, run.match.combat.hooks.flights)
        self.engage(run, hog, actor, angles)
        self.assertNotIn(hog.body, run.match.combat.hooks.flights)
        self.assertFalse(any(s.flags & nodes.Ability.RUNNING
                             for i in hog.body_script.component.instances.values() if i.graph.index == 0x1C8
                             for s in i.states.values() if isinstance(s, nodes.Ability)))

    def test_support_roadhog_hook_releases_pin_and_blocks_damage_during_source_control(self):
        run, hog, actor, _, angles, own = self.encounter(support=True)
        helper = run.players[1]
        run.match.switch_hero(helper, hog.hero)
        helper.mover.position[:] = (hog.mover.position[0] + 2.5, hog.mover.position[1], hog.mover.position[2])
        helper.mover.body = None
        helper.body_script.ult_seconds = 0
        self.engage(run, hog, actor, angles)
        target = next(t for t in run.match.combat._targets() if t.owner is actor)
        from test_game_combat import aim

        support_aim = aim(run.match.combat._eye(helper),
                          (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
        run.step({helper: (*support_aim, 8)})
        for _ in range(60):
            run.step({helper: (*support_aim, 0)})
            if run.match.combat.hooks.controlled(actor.entity):
                break
        self.assertTrue(run.match.combat.hooks.controlled(actor.entity))
        self.assertNotIn(actor.entity, run.match.combat.pins)
        before = own.total
        for _ in range(5):
            run.step({helper: (*support_aim, 0)})
            self.assertTrue(run.match.combat.hooks.controlled(actor.entity))
            self.assertNotIn(actor.entity, run.match.combat.pins)
        self.assertEqual(own.total, before)
        npc_control.stop(run.match)


    def test_allied_quick_melee_rescue_runs_release_clip_then_rearms(self):
        from ow174.game import ball_grapple
        from ow174.game.script.driver import ULT_CHARGE

        for hero_name in ("Roadhog", "Wrecking Ball"):
            with self.subTest(hero=hero_name):
                run, hog, actor, pool, angles, own = self.encounter(support=True)
                helper = run.players[1]
                hero = next(h for h in heroes().values() if h.name == hero_name)
                self.assertTrue(run.match.switch_hero(helper, hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                helper.team = hog.team = 0
                actor.team = 1
                x, y, z = actor.position
                helper.mover.position[:] = (x + 1.7, y, z)
                helper.mover.body = None
                helper.body_script.ult_seconds = 0
                helper.body_script.set_ult_charge(0)
                for _ in range(45):
                    run.step({})
                self.engage(run, hog, actor, angles)
                fight = run.match.combat
                look = aim(fight._eye(helper), (x, y + .85, z))
                if hero.guid == ball_grapple.HERO:
                    before = pool.total
                    run.step({helper: (*look, 1)})
                    self.assertEqual(pool.total, before - 5)
                    self.assertEqual(fight.pins.get(actor.entity), hog.body)
                    for _ in range(5):
                        run.step({})
                before = pool.total
                charge = helper.body_script.component.entity_var(ULT_CHARGE).value()
                run.step({helper: (*look, 0x800)})
                for _ in range(22):
                    if actor.entity not in fight.pins:
                        break
                    run.step({})
                self.assertNotIn(actor.entity, fight.pins)
                self.assertEqual(pool.total, before - 30)
                self.assertEqual(helper.body_script.component.entity_var(ULT_CHARGE).value(), charge + 30)
                release = self.phase(actor, 31)
                self.assertTrue(release.active)
                release_events = (event.time for event in release.instance.queue if event.state is release)
                self.assertEqual(min(release_events), release.start + 1467)
                health = own.total
                for _ in range(80):
                    run.step({})
                    self.assertEqual(own.total, health)
                    self.assertNotIn(actor.entity, fight.pins)
                    self.assertFalse(actor.attack_input)
                self.assertTrue(actor.firing)
                for _ in range(120):
                    run.step({})
                    if fight.pins.get(actor.entity) == hog.body:
                        break
                self.assertFalse(release.active)
                self.assertEqual(fight.pins.get(actor.entity), hog.body)
                self.assertLess(own.total, health)

    def test_assassin_release_requires_fresh_range_and_team_eligibility(self):
        for invalid in ("range", "team"):
            with self.subTest(invalid=invalid):
                run, hog, actor, _, angles, own = self.encounter(support=True)
                helper = run.players[1]
                self.assertTrue(run.match.switch_hero(helper, hog.hero))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                helper.team = hog.team = 0
                actor.team = 1
                x, y, z = actor.position
                helper.mover.position[:] = (x + 1.8, y, z)
                helper.mover.body = None
                for _ in range(45):
                    run.step({})
                self.engage(run, hog, actor, angles)
                fight = run.match.combat
                look = aim(fight._eye(helper), (x, y + .85, z))
                run.step({helper: (*look, 0x800)})
                for _ in range(24):
                    if actor.entity not in fight.pins:
                        break
                    run.step({})
                self.assertNotIn(actor.entity, fight.pins)
                release = self.phase(actor, 31)
                self.assertTrue(release.active)
                before = own.total
                feet = tuple(hog.mover.position)
                if invalid == "range":
                    hog.mover.position[0] += 6
                    hog.mover.body = None
                else:
                    hog.team = actor.team
                for _ in range(125):
                    run.step({})
                    self.assertNotIn(actor.entity, fight.pins)
                    self.assertEqual(own.total, before)
                self.assertFalse(release.active)
                self.assertTrue(actor.firing)
                self.assertFalse(actor.attack_input)
                hog.team = helper.team
                hog.mover.position[:] = feet
                hog.mover.body = None
                for _ in range(100):
                    run.step({})
                    if actor.entity in fight.pins:
                        break
                self.assertEqual(fight.pins.get(actor.entity), hog.body)
                self.assertLess(own.total, before)

    def test_wall_and_free_for_all_do_not_rescue_the_pinned_roadhog(self):
        from test_game_mover import box

        from ow174.game.collision import World

        for wall in (False, True):
            with self.subTest(wall=wall):
                run, hog, actor, pool, angles, _ = self.encounter(support=True)
                helper = run.players[1]
                self.assertTrue(run.match.switch_hero(helper, hog.hero))
                x, y, z = actor.position
                helper.mover.position[:] = (x + 1.8, y, z)
                helper.mover.body = None
                if wall:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    helper.team = hog.team = 0
                    actor.team = 1
                    run.match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + box(x + .8, x + 1.0, y, y + 5, z - 1, z + 1),
                    )
                for _ in range(45):
                    run.step({})
                self.engage(run, hog, actor, angles)
                fight = run.match.combat
                before = pool.total
                look = aim(fight._eye(helper), (x, y + .85, z))
                run.step({helper: (*look, 0x800)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(pool.total, before if wall else before - 30)
                self.assertEqual(fight.pins.get(actor.entity), hog.body)

    def test_allied_earthshatter_releases_live_assassin_pin(self):
        run, hog, actor, hp, angles, own = self.encounter(support=True)
        helper = run.players[1]
        run.match.switch_hero(helper, heroes()[0x02E0000000000007])
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = helper.team = 0
        x, y, z = actor.position
        helper.mover.position[:] = (x+8, y, z)
        helper.mover.body = None
        for _ in range(40):
            run.step({})
        self.engage(run, hog, actor, angles)
        fight = run.match.combat
        self.assertTrue(fight.is_stunned(hog))
        before = hp.total
        script = helper.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        support_aim = aim(fight._eye(helper), (x, y+1, z))
        run.step({helper: (*support_aim, 32)})
        for _ in range(85):
            if fight.is_stunned(actor):
                break
            run.step({helper: (*support_aim, 0)})
        self.assertTrue(fight.is_stunned(actor))
        self.assertFalse(actor.dead)
        self.assertLess(hp.total, before)
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_stunned(hog))
        saved = own.total
        for _ in range(45):
            run.step({})
            self.assertNotIn(actor.entity, fight.pins)
        self.assertEqual(own.total, saved)

    def test_allied_sleep_and_flashbang_end_assassin_pin_before_a_fresh_engagement(self):
        from test_game_roadhog_stun import MCCREE

        for hero, button, amount in ((0x02E000000000013B, 8, 5), (MCCREE, 16, 25)):
            with self.subTest(hero=heroes()[hero].name):
                self.check_allied_stun_releases_assassin(hero, button, amount)

    def check_allied_stun_releases_assassin(self, hero, button, amount):
        run, hog, actor, hp, angles, own = self.encounter(support=True)
        match = run.match
        fight = match.combat
        ally = run.players[1]
        self.assertTrue(match.switch_hero(ally, heroes()[hero]))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = ally.team = 0
        x, y, z = actor.position
        ally.mover.position[:] = (x + 4, y, z)
        ally.mover.body = None
        look = aim(fight._eye(ally), (x, y + 1, z))
        for _ in range(40):
            run.step({ally: (*look, 0)})
        self.engage(run, hog, actor, angles)
        for _ in range(100):
            if (damage := self.phase(actor, 15)) is not None and damage.active:
                break
            run.step({ally: (*look, 0)})
        self.assertIsNotNone(damage)
        self.assertTrue(damage.active)
        self.assertTrue(fight.is_stunned(hog))
        before = hp.total

        run.step({ally: (*look, button)})
        for _ in range(45):
            if fight.is_stunned(actor):
                break
            self.assertEqual(fight.pins.get(actor.entity), hog.body)
            run.step({ally: (*look, 0)})
        self.assertTrue(fight.is_stunned(actor))
        self.assertAlmostEqual(hp.total, before - amount)
        deadline = (fight.sleep if button == 8 else fight.stuns)[actor.entity]
        self.assertEqual(deadline - run.frame, 313 if button == 8 else 50)
        self.assertFalse(actor.dead)
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_stunned(hog))

        saved = own.total
        run.step({hog: (*angles, 16)})
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        while run.frame < deadline - 1:
            previous = own.total
            run.step({})
            self.assertNotIn(actor.entity, fight.pins)
            self.assertFalse(actor.attack_input)
            self.assertGreaterEqual(own.total, previous)
        self.assertGreater(own.total, saved)
        npc_control.stop(match)
        for _ in range(145):
            previous = own.total
            run.step({})
            self.assertNotIn(actor.entity, fight.pins)
            self.assertGreaterEqual(own.total, previous)
        self.assertFalse(fight.is_stunned(actor))
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.assertEqual(own.total, 600)

        self.engage(run, hog, actor, angles)
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(own.total, 575)

    def test_delayed_pinned_input_cannot_start_whole_hog_after_sleep_rescue(self):
        from unittest.mock import patch

        run, hog, actor, _, angles, own = self.encounter(support=True)
        match = run.match
        fight = match.combat
        ally = run.players[1]
        self.assertTrue(match.switch_hero(ally, heroes()[0x02E000000000013B]))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = ally.team = 0
        x, y, z = actor.position
        ally.mover.position[:] = (x + 4, y, z)
        ally.mover.body = None
        look = aim(fight._eye(ally), (x, y + 1, z))
        for _ in range(40):
            run.step({ally: (*look, 0)})
        self.engage(run, hog, actor, angles)
        for _ in range(100):
            if (damage := self.phase(actor, 15)) is not None and damage.active:
                break
            run.step({})
        self.assertIsNotNone(damage)
        self.assertTrue(damage.active)

        script = hog.body_script
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        ultimate, = script.ultimates
        start = run.frame
        command_frame = script.frame
        before = own.total
        with patch.object(hog, "take_commands"):
            for _ in range(12):
                run.step({ally: (*look, 0)})
                self.assertEqual(fight.pins.get(actor.entity), hog.body)
            self.assertAlmostEqual(before - own.total, 12 * 1.6, places=4)
            run.step({ally: (*look, 8)})
            for _ in range(45):
                if fight.is_stunned(actor):
                    break
                run.step({ally: (*look, 0)})
            self.assertTrue(fight.is_stunned(actor))
            self.assertNotIn(actor.entity, fight.pins)
            self.assertEqual(script.frame, command_frame)
            released_health = own.total
            for _ in range(4):
                run.step({})
            self.assertEqual(own.total, released_health)

        position = tuple(hog.mover.position)
        cutoff = fight.hard_control_command_cutoffs[hog.body]
        self.assertGreaterEqual(cutoff, start + 12)
        self.assertLess(cutoff, run.frame)
        old_press = Command(cutoff, yaw=angles[0], pitch=angles[1], buttons=32, forward=127)
        hog.take_commands([old_press, Command(run.frame, yaw=angles[0], pitch=angles[1])])
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(tuple(hog.mover.position), position)
        self.assertEqual(own.total, released_health)
        run.step({hog: (*angles, 32)})
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), 0)

    def test_actual_pin_releases_on_wall_range_team_replacement_and_removal(self):
        from ow174.game import npc
        from ow174.game.collision import World

        for action in ("wall", "range", "ally", "replace", "remove"):
            with self.subTest(action=action):
                run, hog, actor, _, angles, own = self.encounter()
                self.engage(run, hog, actor, angles)
                before, old_body = own.total, hog.body
                if action == "wall":
                    x, y, z = hog.mover.position
                    run.match.collision_world = World([
                        (x - 10, y - 10, z + 1, x + 10, y - 10, z + 1, x + 10, y + 10, z + 1),
                        (x - 10, y - 10, z + 1, x + 10, y + 10, z + 1, x - 10, y + 10, z + 1),
                    ])
                elif action == "range":
                    actor.position = (actor.position[0], actor.position[1], actor.position[2] + 10)
                elif action == "ally":
                    from dataclasses import replace

                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    actor.team = hog.team
                elif action == "replace":
                    self.assertTrue(run.match.switch_hero(hog, hog.hero))
                    npc_control.stop(run.match)
                else:
                    self.assertEqual(npc.remove_spawned(run.match), (actor.entity,))
                for _ in range(40):
                    run.step({})
                    self.assertNotIn(old_body, run.match.combat.pins.values())
                self.assertEqual(own.total, before)
                if action == "remove":
                    self.assertIsNone(actor.body_script)

    def test_support_actual_primary_kills_source_and_releases_control_without_late_damage(self):
        from test_game_combat import aim

        run, hog, actor, _, angles, own = self.encounter(support=True)
        helper = run.players[1]
        run.match.switch_hero(helper, hog.hero)
        helper.mover.position[:] = (hog.mover.position[0] + 2.5, hog.mover.position[1], hog.mover.position[2])
        helper.mover.body = None
        self.engage(run, hog, actor, angles)
        x, y, z = actor.position
        look = aim(run.match.combat._eye(helper), (x, y + 0.75, z))
        for _ in range(250):
            run.step({helper: (*look, 1)})
            if actor.dead:
                break
        self.assertTrue(actor.dead)
        self.assertFalse(own.dead)
        self.assertNotIn(actor.entity, run.match.combat.pins)
        before = own.total
        for _ in range(240):
            run.step({})
        self.assertEqual(own.total, before)
        self.assertIn(actor, run.match.bots)
        self.assertTrue(actor.retain_corpse)


    def test_actual_mapped_defenses_reject_pin_scope_without_late_acquisition(self):
        from ow174.game import defenses
        from ow174.game.content import heroes

        for name, button in (("Reaper", 8), ("Moira", 8), ("Mei", 8),
                             ("Zenyatta", 32), ("Orisa", 8)):
            with self.subTest(hero=name):
                run, victim, actor, _, angles, _ = self.encounter()
                fight = run.match.combat
                run.match.switch_hero(victim, next(h for h in heroes().values() if h.name == name))
                own = combat.Vitals(victim.body, 20000)
                fight.vitals[victim.body] = own  # no armor: isolate contact reduction/protection
                victim.body_script.ult_seconds = 0
                if button == 32:
                    victim.body_script.set_ult_charge(victim.body_script.component.entity_var(338).value())
                run.step({victim: (*angles, button)})
                for _ in range(5):
                    run.step({victim: (*angles, 0)})
                self.assertTrue(defenses.control_immune(victim))
                victim.mover.yaw, victim.mover.pitch = angles
                npc_control.start(run.match, victim)
                for _ in range(20):
                    run.step({victim: (*angles, 0)})
                    self.assertNotIn(actor.entity, fight.pins)
                self.assertTrue(defenses.control_immune(victim))
                self.assertAlmostEqual(own.total, 19985 if name == "Orisa" else 20000, places=4)
                world = actor.body_script.component.world
                self.assertIsNotNone(world.rejected_pin_attempt)
                for _ in range(600):
                    run.step({victim: (*angles, 0)})
                    self.assertNotIn(actor.entity, fight.pins)
                    if not defenses.control_immune(victim):
                        break
                self.assertFalse(defenses.control_immune(victim))
                before = own.total
                for _ in range(40):
                    run.step({victim: (*angles, 0)})
                    self.assertNotIn(actor.entity, fight.pins)
                self.assertEqual(own.total, before)
                npc_control.stop(run.match)
                self.assertIsNone(world.rejected_pin_attempt)
                run.step({victim: (*angles, 0)})  # process the actual input release before re-press
                self.engage(run, victim, actor, angles)
                self.assertEqual(own.total, before - 25)

    def test_meteor_protection_rejects_actual_pin_until_a_fresh_order(self):
        from ow174.game import defenses, meteor_strike

        run, victim, actor, npc_pool, look, _ = self.encounter(support=True)
        match = run.match
        fight = match.combat
        support = run.players[1]
        self.assertTrue(match.switch_hero(victim, next(h for h in heroes().values() if h.name == "Doomfist")))
        self.assertTrue(match.switch_hero(support, next(h for h in heroes().values() if h.name == "Zarya")))
        match.game_map = replace(match.game_map, free_for_all=False)
        victim.team, support.team = 0, actor.team
        x, y, z = actor.position
        support.mover.position[:] = (x + 12, y, z)
        support.mover.body = None
        for _ in range(15):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is victim)
        own = fight.vitals_of(target)
        before = own.total
        npc_before = npc_pool.total
        script = victim.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({victim: (*look, 32)})
        meteor = fight.meteors[victim.body]
        self.assertEqual(defenses.protection(victim), (True, True))
        victim.mover.yaw, victim.mover.pitch = look
        self.assertEqual(npc_control.start(match, victim), actor.entity)
        world = actor.body_script.component.world
        for _ in range(65):
            run.step({})
            self.assertNotIn(actor.entity, fight.pins)
            self.assertEqual(own.total, before)
            if world.rejected_pin_attempt is not None and meteor_strike.aiming(meteor_strike.phase_of(victim)):
                break
        rejected = world.rejected_pin_attempt
        self.assertIsNotNone(rejected)
        self.assertTrue(meteor_strike.aiming(meteor_strike.phase_of(victim)))

        # Protect the Assassin from the impact so the same pin can remain in range at expiry.
        bot_target = next(t for t in fight._targets() if t.owner is actor)
        a, b = bot_target.segment()
        centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
        cast_aim = aim(fight._eye(support), centre)
        run.step({support: (*cast_aim, 0)})
        run.step({support: (*cast_aim, 16)})
        bubble = fight.zarya_bubbles.for_target(actor)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        run.step({victim: (*look, 1)})
        self.assertTrue(meteor.committed)
        for _ in range(95):
            run.step({})
            self.assertNotIn(actor.entity, fight.pins)
            self.assertEqual(own.total, before)
            if meteor.impacted:
                break
            self.assertEqual(defenses.protection(victim), (True, True))
        self.assertTrue(meteor.impacted)
        self.assertEqual(defenses.protection(victim), (False, False))
        self.assertEqual(bubble.health, 0)
        self.assertEqual(npc_pool.total, npc_before)
        self.assertNotIn(actor.entity, fight.impulses.live)
        self.assertTrue(actor.attack_input)
        self.assertEqual(world.rejected_pin_attempt, rejected)
        for _ in range(25):
            run.step({})
            self.assertNotIn(actor.entity, fight.pins)
            self.assertEqual(own.total, before)
        self.assertTrue(actor.attack_input)
        self.assertEqual(world.rejected_pin_attempt, rejected)
        npc_control.stop(match)
        run.step({})
        self.assertIsNone(world.rejected_pin_attempt)
        look = aim(fight._eye(victim), (x, y + bot_target.height / 2, z))
        self.engage(run, victim, actor, look)
        self.assertEqual(own.total, before - 25)

    def test_actual_pin_kills_victim_and_releases_before_respawn(self):
        run, hog, actor, _, angles, own = self.encounter()
        self.engage(run, hog, actor, angles)
        for _ in range(650):
            run.step({})
            if own.dead:
                break
        self.assertTrue(own.dead)
        self.assertEqual(own.total, 0)
        self.assertNotIn(actor.entity, run.match.combat.pins)
        before_body = hog.body
        for _ in range(25):
            run.step({})
        self.assertEqual(hog.body, before_body)
        self.assertTrue(own.dead)
        self.assertEqual(own.total, 0)
        self.assertNotIn(actor.entity, run.match.combat.pins)

    def test_active_actual_pin_and_clock_export_without_health_or_target_evaluation(self):
        from unittest.mock import patch

        import test_server_snapshot as stored
        from test_game_combat import aim

        from ow174.game import npc
        from ow174.game.script import runtime
        from ow174.game.server_snapshot import snapshot_digest

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene()
        hog = players[0]
        session.apply("switch_hero", {"account": hog.account_lo, "hero": bodies.ROADHOG})
        match = hog.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.tick = match.combat.tick = hog.body_script.frame
        match.combat.now = match.tick * combat.TICK
        x, y, z = hog.mover.position
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        actor = npc.spawn(match, definition, (x, y, z + 2), team=1 - hog.team)
        hog.mover.yaw, hog.mover.pitch = aim(match.combat._eye(hog), (x, y + 0.75, z + 2))
        npc_control.start(match, hog)
        for _ in range(130):
            tick = match.tick + 1
            match.update(tick * combat.TICK, tick)
            state = self.phase(actor, 15)
            if state is not None and state.active:
                break
        self.assertTrue(state.active)
        self.assertEqual(match.combat.pins.get(actor.entity), hog.body)
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(assassin, "victim", side_effect=AssertionError("no target query")),
        ):
            first = snapshot_digest(case.capture(session))
            self.assertEqual(snapshot_digest(case.capture(session)), first)
            state.last_applied += 1
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)
            state.last_applied -= 1
            cutoff = match.combat.hard_control_command_cutoffs[hog.body]
            match.combat.hard_control_command_cutoffs[hog.body] = cutoff + 1
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)
            match.combat.hard_control_command_cutoffs[hog.body] = cutoff
            self.assertEqual(snapshot_digest(case.capture(session)), first)
            match.combat.pins.clear()
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)


if __name__ == "__main__":
    unittest.main()
