"""Actual 0CCB inputs and the selected 0CCC/0CCE Halt path against Roadhog."""

import os
import unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as phases
import test_game_roadhog_graviton as graviton_tests
import test_game_roadhog_matrix as matrix_tests
import test_game_roadhog_mei_wall as wall_tests
import test_game_roadhog_trap as trap_tests
import test_game_roadhog_zarya as zarya_tests
from test_game_combat import in_world
from test_game_mover import box

from ow174.game import combat, deflection, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.orisa_halt import PROJECTILE
from ow174.game.script.nodes import Ability


class RoadhogHaltTests(unittest.TestCase):
    def switch(self, run, actor, name):
        hero = next(hero for hero in heroes().values() if hero.name == name)
        self.assertTrue(run.match.switch_hero(actor, hero))

    def scene(self):
        fixture = phases.RoadhogDefenseTests()
        run, hog, orisa, rear = fixture.session('Orisa', distance=8)
        fight = run.match.combat
        if fight.halt.control is None or fight.halt.query is None:
            self.skipTest('prepared 104319 0CCC/0CCE Halt inputs required')
        x, y, z = hog.mover.position
        self.place(orisa, (x, y, z))
        self.place(hog, (x + 1.2, y, z + 5))
        self.place(rear, (x + 30, y, z))
        for _ in range(30):
            run.step({orisa: (0, 0, 0)})
        return run, hog, orisa, rear

    def place(self, player, point):
        player.mover.position[:] = point
        player.mover.body = None

    def pull_request(self, fight, entity):
        pull = fight.halt.pulls[entity]
        state = pull.script.states.get(pull.script.graph.node(3).state)
        return state.move_request() if state is not None else None

    def lethal_attack(self, run, shooter, victim, *, button=1, victim_look=(0, 0)):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = fight.vitals_of(target)
        pool.damage(pool.total - 1)
        fight._sync_body_health(victim, pool)
        for offset in range(24):
            if fight.is_dead(victim):
                break
            x, y, z = victim.mover.position
            look = phases.aim(fight._eye(shooter), (x, y + .7, z))
            run.step({shooter: (*look, button if offset == 0 else 0), victim: (*victim_look, 0)})
        self.assertTrue(fight.is_dead(victim))

    def cast(self, run, orisa, *, early=False):
        for at in range(25):
            run.step({orisa: (0, 0, 2 if at == 0 or (early and at == 15) else 0)})
        orb, = run.match.combat.halt.orbs.values()
        self.assertIsNone(orb.requested)
        run.step({orisa: (0, 0, 2)})
        self.assertEqual(orb.requested, run.match.tick)
        return orb

    def test_halt_player_pull_and_release_do_not_depend_on_victim_commands(self):
        paths = []
        for interval in (1, 7, None):
            with self.subTest(interval=interval):
                run, hog, orisa, _ = self.scene()
                fight = run.match.combat
                orb = self.cast(run, orisa)
                initial = tuple(hog.mover.position)
                script_frame = hog.body_script.frame
                path = []
                for offset in range(105):
                    run.frame += 1
                    run.match.update(run.frame * .016, run.frame)
                    orisa.take_commands([Command(run.frame)])
                    if interval and offset % interval == 0:
                        hog.take_commands([Command(run.frame)])
                    x, _, z = hog.mover.position
                    path.append((x, z, hog.body in fight.halt.pulls))
                self.assertFalse(fight.halt.pulls)
                self.assertNotIn(orb.entity, fight.halt.orbs)
                self.assertGreater(hog.mover.position[2], initial[2] + 1)
                self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)
                if interval is None:
                    self.assertEqual(hog.body_script.frame, script_frame)
                    cutoff = fight.halt.player_command_cutoffs[hog.body]
                    before = tuple(hog.mover.position)
                    late = [Command(frame, forward=127) for frame in range(cutoff - 2, cutoff + 1)]
                    hog.take_commands(late)
                    self.assertEqual(tuple(hog.mover.position), before)
                    fresh = [Command(frame, forward=127) for frame in range(cutoff + 1, cutoff + 9)]
                    hog.take_commands(fresh)
                    self.assertGreater(hog.mover.position[2], before[2])
                paths.append(path)
        self.assertEqual(paths[0], paths[1])
        self.assertEqual(paths[0], paths[2])

    def test_earthshatter_and_halt_keep_independent_release_clocks(self):
        for order in ('early_halt', 'early_knockdown', 'late_halt'):
            with self.subTest(order=order):
                self.check_halt_earthshatter_overlap(order)

    def check_halt_earthshatter_overlap(self, order):
        run, hog, orisa, rein = self.scene()
        fight = run.match.combat
        self.switch(run, rein, 'Reinhardt')
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, orisa.team, rein.team = 0, 1, 1
        x, y, z = hog.mover.position
        self.place(rein, (x + 8, y, z))
        look = phases.aim(fight._eye(rein), (x, y + 1, z))
        for _ in range(30):
            run.step({rein: (*look, 0)})
        rein.body_script.set_ult_charge(rein.body_script.component.entity_var(338).value())
        halt_first = order == 'early_halt'
        resumes = order == 'late_halt'
        if halt_first:
            for offset in range(26):
                run.step({orisa: (0, 0, 2 if offset in (0, 25) else 0),
                          rein: (*look, 32 if offset == 16 else 0)})
        else:
            run.step({rein: (*look, 32)})
        started = False
        for _ in range(70):
            x, y, z = hog.mover.position
            look = phases.aim(fight._eye(rein), (x, y + 1, z))
            run.step({rein: (*look, 0)})
            if fight.is_stunned(hog):
                break
            started |= fight.halt.drove_pose(hog.body, fight.tick)
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(started, halt_first, (fight.tick, fight.halt.player_pose_ticks.get(hog.body)))
        deadline = fight.stuns[hog.body]
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 550)
        before = tuple(hog.mover.position)
        if not halt_first:
            if resumes:
                while fight.tick < deadline - 65:
                    run.step({})
            self.cast(run, orisa)
        saw_request = False
        while fight.tick < deadline - 1:
            run.step({})
            if hog.body in fight.halt.pulls:
                saw_request |= self.pull_request(fight, hog.body) is not None
            self.assertFalse(fight.halt.drove_pose(hog.body, fight.tick))
            self.assertEqual((hog.mover.position[0], hog.mover.position[2]), (before[0], before[2]))
        self.assertTrue(saw_request)
        self.assertEqual(hog.body in fight.halt.pulls, resumes)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(fight.tick, deadline)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(fight.halt.drove_pose(hog.body, deadline), resumes)
        if resumes:
            self.assertGreater(hog.mover.position[2], before[2])
        else:
            self.assertEqual((hog.mover.position[0], hog.mover.position[2]), (before[0], before[2]))
        for _ in range(90):
            run.step({})
        self.assertFalse(fight.halt.pulls)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 550)

    def test_earthshatter_cancels_melee_buffer_that_survived_halt(self):
        run, hog, orisa, rein = self.scene()
        match = run.match
        fight = match.combat
        self.switch(run, rein, 'Reinhardt')
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, orisa.team, rein.team = 0, 1, 1
        victim = match.add_player(4, "Melee target", SOLDIER, 3, False)
        in_world(match, victim)
        run.players.append(victim)
        self.assertTrue(match.switch_hero(victim, heroes()[SOLDIER]))
        victim.team = 1
        health = phases.RoadhogDefenseTests().health
        x, y, z = hog.mover.position
        self.place(rein, (x + 8, y, z))
        self.place(victim, (x - 1.5, y, z))
        for _ in range(30):
            run.step({})
        before = health(run, victim)
        rein.body_script.set_ult_charge(rein.body_script.component.entity_var(338).value())
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        held = {hog: (-16384, 0, 0x800)}
        look = phases.aim(fight._eye(rein), (x, y + 1, z))
        run.step({**held, orisa: (0, 0, 2)})
        first = run.frame
        run.step({})
        run.step(held)
        melee = next(s for i in hog.body_script.component.instances.values() if i.graph.index == 0x0043
                     for s in i.states.values() if isinstance(s, Ability))
        hit = melee.instance.state(4)
        self.assertEqual(health(run, victim), before - 30)
        self.assertEqual(hit.counter, 1)
        self.assertTrue(melee.presses)
        for offset in range(3, 26):
            run.step({**held, orisa: (0, 0, 2 if offset == 25 else 0),
                      rein: (*look, 32 if offset == 16 else 0)})

        drove_before_stun = False
        while run.frame < first + 61 and not fight.is_stunned(hog):
            x, y, z = hog.mover.position
            look = phases.aim(fight._eye(rein), (x, y + 1, z))
            run.step({**held, rein: (*look, 0)})
            if fight.halt.drove_pose(hog.body, fight.tick):
                drove_before_stun = True
                self.assertTrue(melee.presses)
        self.assertTrue(drove_before_stun)
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(health(run, hog), 550)
        self.assertFalse(fight.halt.drove_pose(hog.body, fight.tick))
        self.assertFalse(melee.presses)
        deadline = fight.stuns[hog.body]
        while run.frame < first + 62:
            run.step(held)
            self.assertTrue(fight.is_stunned(hog))
            self.assertFalse(fight.halt.drove_pose(hog.body, fight.tick))
        self.assertEqual(hit.counter, 1)
        while run.frame < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.halt.pulls)

        x, y, z = hog.mover.position
        self.place(victim, (x - 1.5, y, z))
        for _ in range(8):
            run.step({})
        self.assertEqual(hit.counter, 1)
        self.assertEqual(health(run, victim), before - 30)
        run.step(held)
        self.assertEqual(hit.counter, 2)
        self.assertEqual(health(run, victim), before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_whole_hog_damage_continues_without_displacing_an_active_halt_target(self):
        run, hog, orisa, shooter = self.scene()
        self.check_whole_hog_halt_overlap(run, orisa, shooter, hog)

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual NPC graph required')
    def test_whole_hog_does_not_displace_a_trooper_during_halt(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        run, hog, orisa, shooter = self.scene()
        origin = tuple(hog.mover.position)
        self.place(hog, (30, origin[1], 0))
        definition = next(d for d in fixture.definitions.values() if d.name == 'Trooper')
        bot = npc.spawn(run.match, definition, origin, team=0)
        self.check_whole_hog_halt_overlap(run, orisa, shooter, bot)

    def check_whole_hog_halt_overlap(self, run, orisa, shooter, victim):
        fight = run.match.combat
        entity = combat._source_entity(victim)

        def position():
            return next(t.feet for t in fight._targets() if t.owner is victim)

        self.switch(run, shooter, 'Roadhog')
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        victim.team, orisa.team, shooter.team = 0, 1, 1
        x, y, z = position()
        self.place(shooter, (x + 4, y, z))
        fight.vitals[entity] = combat.Vitals(entity, 20000)
        for _ in range(30):
            run.step({})
        shooter.body_script.set_ult_charge(2240)
        run.step({shooter: (0, 0, 32)})
        orb = self.cast(run, orisa)
        while fight.tick < orb.requested + 19:
            run.step({})
        prior_impulse = False
        damage_while_pulled = False
        first_motion = False
        moved_while_pulled = False
        for _ in range(75):
            x, y, z = position()
            look = phases.aim(fight._eye(shooter), (x, y + 1, z))
            damage = shooter.stats.value(stats.HERO_DAMAGE)
            old = fight.impulses.live.get(entity)
            mover = (old.npc_mover or victim.mover) if old is not None else None
            observe = (
                nullcontext() if mover is None else patch.object(mover, "displace", wraps=mover.displace)
            )
            with observe as impulse:
                run.step({shooter: (*look, 0)})
            if fight.halt.drove_pose(entity, fight.tick):
                first_motion = True
                moved_while_pulled |= position() != (x, y, z)
                if impulse is not None:
                    impulse.assert_not_called()
                self.assertNotIn(entity, fight.impulses.live)
                damage_while_pulled |= shooter.stats.value(stats.HERO_DAMAGE) > damage
            elif not first_motion:
                prior_impulse |= entity in fight.impulses.live
            elif shooter.stats.value(stats.HERO_DAMAGE) > damage:
                self.assertIn(entity, fight.impulses.live)
                break
        self.assertTrue(prior_impulse)
        self.assertTrue(first_motion)
        self.assertTrue(moved_while_pulled)
        self.assertTrue(damage_while_pulled)
        self.assertIn(entity, fight.impulses.live)

    def test_halt_arming_repress_pull_and_authored_cooldown(self):
        run, hog, orisa, _ = self.scene()
        fight = run.match.combat
        script = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0xCCB)
        before = tuple(hog.mover.position)
        orb = self.cast(run, orisa, early=True)
        stopped = orb.position
        for _ in range(24):
            run.step({})
        self.assertEqual(tuple(hog.mover.position), before)
        self.assertEqual(orb.position, stopped)
        self.assertEqual(fight.halt.pulls[hog.body].script.graph.index, 0xCCE)
        for _ in range(60):
            run.step({})
        self.assertLess(hog.mover.position[0], before[0])
        self.assertGreater(hog.mover.position[2], before[2] + 1)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)
        self.assertFalse(fight.halt.orbs)
        self.assertFalse(fight.halt.pulls)
        self.assertGreater(script.state(2).remaining(), 0)
        for at in range(30):
            run.step({orisa: (0, 0, 2 if at % 3 == 0 else 0)})
        self.assertFalse(fight.halt.orbs)
        for _ in range(420):
            if script.state(2).remaining() == 0:
                break
            run.step({})
        self.assertEqual(script.state(2).remaining(), 0)
        run.step({orisa: (0, 0, 2)})
        for _ in range(20):
            if fight.halt.orbs:
                break
            run.step({})
        fresh, = fight.halt.orbs.values()
        self.assertNotEqual(fresh.entity, orb.entity)

    def test_halt_rejects_team_range_and_static_cover(self):
        for condition in ('team', 'range', 'cover'):
            with self.subTest(condition=condition):
                run, hog, orisa, rear = self.scene()
                match = run.match
                fight = match.combat
                x, y, z = orisa.mover.position
                if condition == 'team':
                    match.game_map = replace(match.game_map, free_for_all=False)
                    hog.team = orisa.team = 0
                    rear.team = 1
                elif condition == 'range':
                    self.place(hog, (x + 12, y, z + 5))
                else:
                    self.place(hog, (x + 2.2, y, z + 5))
                self.cast(run, orisa)
                before = tuple(hog.mover.position)
                if condition == 'cover':
                    match.collision_world = World(
                        box(x - 40, x + 40, y - 1, y, z - 40, z + 40)
                        + box(x + .5, x + .7, y, y + 6, z + 2, z + 11),
                    )
                for _ in range(70):
                    run.step({})
                    self.assertNotIn(hog.body, fight.halt.pulls)
                self.assertEqual(tuple(hog.mover.position), before)
                self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_projected_barrier_prevents_halt_acquisition_or_releases_the_active_pull(self):
        for active_pull in (False, True):
            with self.subTest(active_pull=active_pull):
                run, hog, orisa, zarya = self.scene()
                fight = run.match.combat
                self.switch(run, zarya, 'Zarya')
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = zarya.team = 0
                orisa.team = 1
                x, y, z = hog.mover.position
                self.place(zarya, (x + 8, y, z))
                for _ in range(30):
                    run.step({})
                if active_pull:
                    self.cast(run, orisa)
                    for _ in range(35):
                        run.step({})
                        if hog.mover.position[2] > z + .2:
                            break
                    self.assertIn(hog.body, fight.halt.pulls)
                    self.assertGreater(hog.mover.velocity[2], 0)
                x, y, z = hog.mover.position
                look = phases.aim(fight._eye(zarya), (x, y + 1, z))
                bubble = zarya_tests.RoadhogZaryaTests().cast(run, orisa, zarya, hog, look, projected=True)
                if not active_pull:
                    self.cast(run, orisa)
                before = tuple(hog.mover.position)
                for _ in range(150):
                    run.frame += 1
                    run.match.update(run.frame * .016, run.frame)
                    self.assertNotIn(hog.body, fight.halt.pulls)
                    self.assertEqual(tuple(hog.mover.position), before)
                    self.assertEqual(hog.mover.velocity, (0, 0, 0))
                    self.assertFalse(hog.mover.driven)
                    zarya.take_commands([Command(run.frame)])
                self.assertIsNone(fight.zarya_bubbles.for_target(hog))
                self.assertEqual(bubble.health, 200)
                self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    @unittest.skipUnless(os.environ.get('ORPHEUS_REINHARDT_COLLISION'), 'actual barrier mesh required')
    def test_halt_crosses_raised_barrier_but_enemy_mesh_blocks_pull(self):
        for allied_barrier in (False, True):
            with self.subTest(allied_barrier=allied_barrier):
                self.check_halt_raised_barrier('Reinhardt', allied_barrier)

    @unittest.skipUnless(os.environ.get('ORPHEUS_BRIGITTE_COLLISION'), 'actual barrier mesh required')
    def test_halt_crosses_brigitte_shield_but_enemy_mesh_blocks_pull(self):
        for allied_barrier in (False, True):
            with self.subTest(allied_barrier=allied_barrier):
                self.check_halt_raised_barrier('Brigitte', allied_barrier)

    def check_halt_raised_barrier(self, hero, allied_barrier):
        run, hog, orisa, defender = self.scene()
        fight = run.match.combat
        self.switch(run, defender, hero)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, orisa.team = 0, 1
        defender.team = orisa.team if allied_barrier else hog.team
        x, y, z = orisa.mover.position
        self.place(defender, (x + .6, y, z + 5.3))
        if hero == 'Brigitte':
            self.place(hog, (x + .45, y, z + 5))
        raised = (0, 0, 2)
        for _ in range(40):
            run.step({defender: raised})
        shield = fight.barriers[defender.body]
        self.assertTrue(shield.active)
        for offset in range(26):
            run.step({defender: raised, orisa: (0, 0, 2 if offset in (0, 25) else 0)})
        orb, = fight.halt.orbs.values()
        self.assertEqual(orb.requested, fight.tick)
        self.assertGreater(orb.position[2], z + 6.6)
        flight_origin = (*orb.position[:2], z)
        contact = fight._barrier_contact(orisa, flight_origin, orb.direction, orb.position[2] - z)
        if not allied_barrier:
            self.assertIsNotNone(contact)
            self.assertIs(contact[1], defender)
        else:
            self.assertIsNone(contact)
        before = tuple(hog.mover.position[::2])
        run.step({defender: raised})
        self.assertTrue(orb.acquired)
        self.assertEqual(hog.body in fight.halt.pulls, allied_barrier)
        self.assertEqual(shield.current, shield.maximum)
        for _ in range(45):
            run.step({defender: (0, 0, 0)})
        if allied_barrier:
            self.assertGreater(hog.mover.position[2], before[1])
        else:
            self.assertEqual(tuple(hog.mover.position[::2]), before)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_halt_crosses_winston_dome_and_only_pulls_on_its_own_side(self):
        import test_game_roadhog_winston as winston_tests

        for covered in (False, True):
            with self.subTest(covered=covered):
                run, hog, orisa, winston = self.scene()
                fight = run.match.combat
                self.switch(run, winston, 'Winston')
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = winston.team = 0
                orisa.team = 1
                x, y, z = orisa.mover.position
                self.place(winston, (x + 1.2, y, z + .7))
                if not covered:
                    self.place(hog, (x + 1.2, y, z + 7.7))
                for _ in range(30):
                    run.step({})
                dome = winston_tests.RoadhogWinstonTests().cast(run, hog, winston)
                orb = self.cast(run, orisa)
                flight_origin = (*orb.position[:2], z)
                contact = fight._barrier_contact(orisa, flight_origin, orb.direction, orb.position[2] - z)
                self.assertIs(contact[1], dome)
                before = tuple(hog.mover.position[::2])
                run.step({})
                self.assertEqual(hog.body in fight.halt.pulls, not covered)
                for _ in range(45):
                    run.step({})
                if covered:
                    self.assertEqual(tuple(hog.mover.position[::2]), before)
                else:
                    self.assertLess(hog.mover.position[2], before[1])
                self.assertEqual(dome.health, 700)
                self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_halt_crosses_deployed_meshes_but_cannot_pull_through_them(self):
        from test_game_roadhog_orisa_barrier import RoadhogOrisaBarrierTests
        from test_game_roadhog_sigma_barrier import RoadhogSigmaBarrierTests
        from test_game_roadhog_symmetra import RoadhogSymmetraTests

        for hero, maximum in (('Orisa', 600), ('Sigma', 700), ('Symmetra', 4000)):
            with self.subTest(hero=hero):
                if not os.environ.get(f'ORPHEUS_{hero.upper()}_COLLISION'):
                    self.skipTest(f'actual {hero} barrier mesh required')
                run, hog, orisa, defender = self.scene()
                fight = run.match.combat
                self.switch(run, defender, hero)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = defender.team = 0
                orisa.team = 1
                x, y, z = orisa.mover.position
                self.place(defender, (x, y, z + 9))
                for _ in range(35):
                    run.step({})
                if hero == 'Orisa':
                    shield = RoadhogOrisaBarrierTests().deploy(run, hog, defender)
                elif hero == 'Sigma':
                    shield = fight.sigma_barriers.actors[defender.body]
                    RoadhogSigmaBarrierTests().deploy(run, hog, defender, shield)
                else:
                    shield = RoadhogSymmetraTests().cast(run, hog, defender)
                self.place(defender, (x + 30, y, z))
                contact = fight._barrier_contact(orisa, fight._eye(orisa), (0, 0, 1), 15)
                self.assertIsNotNone(contact)
                self.assertIs(contact[1], shield)
                crossing_z = fight._eye(orisa)[2] + contact[0]
                self.place(orisa, (x, y, crossing_z - 6))
                self.place(hog, (x + .35, y, crossing_z - 1.5))
                for _ in range(10):
                    run.step({})
                orb = self.cast(run, orisa)
                self.assertGreater(orb.position[2], crossing_z + .5)
                start = (*orb.position[:2], crossing_z - 6)
                reach = orb.position[2] - start[2]
                flight_contact = fight._barrier_contact(orisa, start, orb.direction, reach)
                self.assertIsNotNone(flight_contact)
                self.assertIs(flight_contact[1], shield)
                before = tuple(hog.mover.position[::2])
                for _ in range(45):
                    run.step({})
                    self.assertNotIn(hog.body, fight.halt.pulls)
                self.assertEqual(tuple(hog.mover.position[::2]), before)
                self.assertEqual(shield.health, maximum)
                self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_halt_detonates_on_live_ice_wall_then_passes_after_cancel(self):
        run, hog, orisa, mei = self.scene()
        fight = run.match.combat
        self.switch(run, mei, 'Mei')
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = mei.team = 0
        orisa.team = 1
        x, y, z = orisa.mover.position
        self.place(mei, (x, y, z + 7))
        for _ in range(30):
            run.step({})
        pillar, aim = wall_tests.RoadhogMeiWallTests().place(run, mei, distance=4)
        health = pillar.health
        before = tuple(hog.mover.position)
        run.step({orisa: (0, 0, 2)})
        orb = None
        for _ in range(35):
            run.step({})
            orb = next(iter(fight.halt.orbs.values()), None)
            if orb is not None and orb.acquired:
                break
        self.assertIsNotNone(orb)
        self.assertTrue(orb.acquired)
        self.assertAlmostEqual(orb.position[2], pillar.feet[2] - pillar.depth, delta=.1)
        self.assertEqual(pillar.health, health)
        run.step({mei: (*aim, 16)})
        self.assertFalse(fight.walls.pieces)
        script = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0xCCB)
        for _ in range(450):
            run.step({})
            self.assertNotIn(hog.body, fight.halt.pulls)
            if not fight.halt.orbs and script.state(2).remaining() == 0:
                break
        self.assertEqual(tuple(hog.mover.position), before)
        self.cast(run, orisa)
        for _ in range(70):
            run.step({})
        self.assertGreater(hog.mover.position[2], before[2] + 1)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_steel_trap_holds_roadhog_until_the_live_halt_can_move_him(self):
        run, hog, junkrat, orisa, trap = trap_tests.RoadhogTrapTests().scene()
        fight = run.match.combat
        if fight.halt.control is None or fight.halt.query is None:
            self.skipTest('prepared 104319 0CCC/0CCE Halt inputs required')
        self.switch(run, orisa, 'Orisa')
        x, y, z = trap.flight.position
        self.place(orisa, (x, y, z - 5))
        self.place(junkrat, (x + 20, y, z))
        for _ in range(30):
            run.step({})
        self.place(hog, (x, y, z))
        run.step({})
        self.assertEqual(trap.victim, hog.body)
        until = trap.release_tick
        before = tuple(hog.mover.position)
        while fight.tick < until - 62:
            run.step({})
        self.cast(run, orisa)
        moving = False
        while fight.tick < until - 1:
            run.step({})
            moving |= self.pull_request(fight, hog.body) is not None
            self.assertEqual(tuple(hog.mover.position), before)
        self.assertTrue(moving)
        self.assertTrue(fight.traps.rooted(hog.body, fight.tick))
        run.step({})
        self.assertFalse(fight.traps.rooted(hog.body, fight.tick))
        self.assertGreater(hog.mover.position[2], before[2])
        for _ in range(25):
            run.step({})
        self.assertGreater(hog.mover.position[2], before[2] + 1)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 520)

    def test_halt_yields_to_graviton_then_pulls_on_release(self):
        run, hog, orisa, zarya = self.scene()
        fight = run.match.combat
        self.switch(run, zarya, 'Zarya')
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = 0
        orisa.team = zarya.team = 1
        x, y, z = hog.mover.position
        self.place(zarya, (x + 8, y, z))
        for _ in range(30):
            run.step({})
        field = graviton_tests.RoadhogGravitonTests().launch(run, zarya, (x + 1.5, y, z))
        while fight.tick < field.end_tick - 65:
            run.step({})
        self.assertIn(hog.body, fight.gravitons.pulled)
        self.cast(run, orisa)
        moving = False
        while fight.tick < field.end_tick - 1:
            run.step({})
            self.assertIn(hog.body, fight.gravitons.pulled)
            moving |= self.pull_request(fight, hog.body) is not None
        self.assertTrue(moving)
        before = tuple(hog.mover.position)
        self.assertGreater(before[0], x)
        run.step({})
        self.assertNotIn(hog.body, fight.gravitons.pulled)
        self.assertGreater(hog.mover.position[2], before[2])
        for _ in range(25):
            run.step({})
        self.assertLess(hog.mover.position[0], before[0] - .5)
        self.assertGreater(hog.mover.position[2], before[2] + 1)
        self.assertAlmostEqual(phases.RoadhogDefenseTests().health(run, hog), 580)

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual NPC graphs required')
    def test_halt_does_not_move_an_npc_twice_while_graviton_holds_it(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        run, hog, orisa, zarya = self.scene()
        fight = run.match.combat
        self.switch(run, zarya, 'Zarya')
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = 0
        orisa.team = zarya.team = 1
        x, y, z = hog.mover.position
        self.place(zarya, (x + 8, y, z))
        definition = next(d for d in fixture.definitions.values() if d.name == 'Trooper')
        bot = npc.spawn(run.match, definition, (x + .2, y, z + .3), team=0)
        for _ in range(30):
            run.step({})
        health = phases.RoadhogDefenseTests().health(run, bot)
        field = graviton_tests.RoadhogGravitonTests().launch(run, zarya, (x + 1.5, y, z))
        while fight.tick < field.end_tick - 65:
            run.step({})
        before = bot.position
        self.cast(run, orisa)
        moving = False
        while fight.tick < field.end_tick - 1:
            run.step({})
            self.assertIn(bot.entity, fight.gravitons.pulled)
            moving |= self.pull_request(fight, bot.entity) is not None
            self.assertEqual(bot.position, before)
        self.assertTrue(moving)
        for _ in range(25):
            run.step({})
        self.assertNotIn(bot.entity, fight.gravitons.pulled)
        self.assertGreater(bot.position[2], before[2] + 1)
        self.assertAlmostEqual(phases.RoadhogDefenseTests().health(run, bot), health - 20)

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual NPC graph required')
    def test_assassin_pin_overrides_halt_until_roadhog_melee_rescue(self):
        import test_game_roadhog_assassin as assassin_tests

        fixture = assassin_tests.RoadhogAssassinTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        for halt_first in (False, True):
            with self.subTest(halt_first=halt_first):
                self.check_assassin_halt_overlap(fixture, halt_first)

    def check_assassin_halt_overlap(self, fixture, halt_first):
        from ow174.game import npc

        run, hog, orisa, rescuer = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = rescuer.team = 0
        orisa.team = 1
        self.switch(run, rescuer, 'Roadhog')
        x, y, z = hog.mover.position
        definition = next(d for d in fixture.definitions.values() if d.name == 'Assassin')
        actor = npc.spawn(match, definition, (x, y, z + 1.5), team=1)
        self.place(rescuer, (x + 1.7, y, z + 1.5))
        for _ in range(30):
            run.step({})
        if halt_first:
            self.cast(run, orisa)
            for _ in range(50):
                run.step({})
                if fight.halt.drove_pose(hog.body, fight.tick):
                    break
            self.assertTrue(fight.halt.drove_pose(hog.body, fight.tick))
        angles = phases.aim(fight._eye(hog), (x, y + .85, z + 1.5))
        fixture().engage(run, hog, actor, angles)
        anchor = tuple(hog.mover.position[::2])
        if not halt_first:
            self.cast(run, orisa)
        for _ in range(45):
            run.step({})
            self.assertEqual(fight.pins.get(actor.entity), hog.body)
            self.assertFalse(fight.halt.drove_pose(hog.body, fight.tick))
            self.assertEqual(tuple(hog.mover.position[::2]), anchor)
            if self.pull_request(fight, hog.body) is not None:
                break
        self.assertIsNotNone(self.pull_request(fight, hog.body))
        health = phases.RoadhogDefenseTests().health
        actor_health = health(run, actor)
        look = phases.aim(fight._eye(rescuer), (x, y + .85, z + 1.5))
        run.step({rescuer: (*look, 0x800)})
        for _ in range(22):
            if actor.entity not in fight.pins:
                break
            self.assertFalse(fight.halt.drove_pose(hog.body, fight.tick))
            self.assertEqual(tuple(hog.mover.position[::2]), anchor)
            run.step({})
        self.assertNotIn(actor.entity, fight.pins)
        self.assertEqual(health(run, actor), actor_health - 30)
        self.assertTrue(fixture().phase(actor, 31).active)
        self.assertFalse(fight.halt.drove_pose(hog.body, fight.tick))
        self.assertEqual(tuple(hog.mover.position[::2]), anchor)
        run.step({})
        self.assertTrue(fight.halt.drove_pose(hog.body, fight.tick))
        self.assertGreater(hog.mover.position[2], anchor[1])
        hog_health = health(run, hog)
        for _ in range(80):
            run.step({})
            self.assertNotIn(actor.entity, fight.pins)
        self.assertEqual(health(run, hog), hog_health)

    def test_halt_yields_to_incoming_hook_then_pulls_after_release(self):
        run, hog, orisa, hooker = self.scene()
        fight = run.match.combat
        self.switch(run, hooker, 'Roadhog')
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = 0
        orisa.team = hooker.team = 1
        x, y, z = hog.mover.position
        self.place(hooker, (x + 5.3, y, z))
        for _ in range(30):
            run.step({})
        self.cast(run, orisa)
        look = phases.aim(fight._eye(hooker), fight._eye(hog))
        run.step({hooker: (*look, 8)})
        overlap = False
        for _ in range(70):
            run.step({hooker: (*look, 0)})
            hooked = fight.hooks.controlled(hog.body)
            if overlap and not hooked:
                break
            if hooked:
                overlap |= self.pull_request(fight, hog.body) is not None
                self.assertAlmostEqual(hog.mover.position[2], z, delta=.15)
        self.assertTrue(overlap)
        self.assertFalse(fight.hooks.controlled(hog.body))
        self.assertGreater(hog.mover.position[0], x + 1)
        before = tuple(hog.mover.position)
        for _ in range(25):
            run.step({})
        self.assertLess(hog.mover.position[0], before[0] - .5)
        self.assertGreater(hog.mover.position[2], before[2] + .5)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 570)

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual NPC graph required')
    def test_halt_yields_to_heavy_assault_charge_then_resumes_after_manual_stop(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc, npc_control, reinhardt_charge

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        run, hog, orisa, rear = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = rear.team = 0
        orisa.team = 1
        x, y, z = hog.mover.position
        definition = next(d for d in fixture.definitions.values() if d.name == 'Heavy Assault')
        bot = npc.spawn(match, definition, (x, y, z + 4), team=1)
        for _ in range(30):
            run.step({hog: (0, 0, 0)})
        self.assertEqual(npc_control.start(match, hog, action='charge'), bot.entity)
        self.assertEqual(npc_control.button(bot), 35)
        self.cast(run, orisa)
        for _ in range(50):
            run.step({})
            if reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(fight.heavy_charges[bot.entity].pinned, hog.body)
        pinned_z = hog.mover.position[2]
        for _ in range(20):
            run.step({})
            self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
            self.assertFalse(fight.halt.drove_pose(hog.body, fight.tick))
            if hog.body in fight.halt.pulls and self.pull_request(fight, hog.body) is not None:
                break
        self.assertIsNotNone(self.pull_request(fight, hog.body))
        self.assertLess(hog.mover.position[2], pinned_z)
        before = hog.mover.position[2]
        self.assertEqual(npc_control.stop(match), 1)
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        run.step({})
        self.assertTrue(fight.halt.drove_pose(hog.body, fight.tick))
        self.assertGreater(hog.mover.position[2], before)
        for _ in range(90):
            run.step({})
        self.assertFalse(fight.halt.pulls)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_reinhardt_charge_and_halt_handoff_after_impact_or_expiry(self):
        for wall_impact, charge_delay in ((False, 0), (True, 0), (True, 5)):
            with self.subTest(wall_impact=wall_impact, charge_delay=charge_delay):
                self.check_reinhardt_charge_halt_overlap(wall_impact, charge_delay)

    def check_reinhardt_charge_halt_overlap(self, wall_impact, charge_delay):
        from ow174.game import reinhardt_charge

        run, hog, orisa, rein = self.scene()
        fight = run.match.combat
        self.switch(run, rein, 'Reinhardt')
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, orisa.team, rein.team = 0, 1, 1
        x, _, z = hog.mover.position
        floor = box(x - 90, x + 30, 0, 1, z - 20, z + 20)
        wall = box(x - 7, x - 6, 1, 8, z - 8, z + 8) if wall_impact else []
        run.match.collision_world = World(floor + wall)
        self.place(hog, (x, 1, z))
        self.place(orisa, (x - 1.2, 1, z - 5))
        charge_z = z + 1.4 if charge_delay else z
        self.place(rein, (x + 5, 1, charge_z))
        for _ in range(30):
            run.step({rein: (-16384, 0, 0)})
        for offset in range(26):
            run.step({rein: (-16384, 0, 8 if offset == charge_delay else 0),
                      orisa: (0, 0, 2 if offset in (0, 25) else 0)})
        health = phases.RoadhogDefenseTests().health
        saw_pin = saw_request = expired_during_pin = halt_moved_first = False
        for _ in range(270):
            was_pinned = reinhardt_charge.pinned(fight, hog.body)
            run.step({rein: (-16384, 0, 0)})
            pinned = reinhardt_charge.pinned(fight, hog.body)
            if was_pinned:
                self.assertFalse(fight.halt.drove_pose(hog.body, fight.tick))
            if saw_pin and not pinned:
                break
            if not pinned:
                halt_moved_first |= fight.halt.drove_pose(hog.body, fight.tick)
            if pinned:
                saw_pin = True
                self.assertAlmostEqual(hog.mover.position[2], charge_z, delta=.08)
                self.assertEqual(health(run, hog), 600)
                if hog.body in fight.halt.pulls:
                    saw_request |= self.pull_request(fight, hog.body) is not None
                elif saw_request:
                    expired_during_pin = True
        self.assertTrue(saw_pin)
        self.assertTrue(saw_request)
        self.assertEqual(halt_moved_first, bool(charge_delay))
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(expired_during_pin, not wall_impact)
        self.assertNotIn(rein.body, fight.charges)
        self.assertEqual(health(run, hog), 300 if wall_impact else 600)
        self.assertEqual(hog.body in fight.halt.pulls, wall_impact)
        before = tuple(hog.mover.position)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(fight.halt.drove_pose(hog.body, fight.tick), wall_impact)
        if wall_impact:
            self.assertGreater(hog.mover.position[0], before[0])
        else:
            self.assertEqual(tuple(hog.mover.position), before)
        for _ in range(95):
            run.step({})
        self.assertFalse(fight.halt.pulls)
        self.assertEqual(health(run, hog), 300 if wall_impact else 600)

    def test_halt_drops_retired_source_and_victim_bodies(self):
        replacement = next(h for h in heroes().values() if h.name == 'Soldier: 76')
        for retire_source in (True, False):
            with self.subTest(retire_source=retire_source):
                run, hog, orisa, _ = self.scene()
                fight = run.match.combat
                actor = orisa if retire_source else hog
                if retire_source:
                    run.step({orisa: (0, 0, 2)})
                    for _ in range(12):
                        run.step({})
                    self.assertTrue(fight.halt.orbs)
                else:
                    self.cast(run, orisa)
                    run.step({})
                    self.assertIn(hog.body, fight.halt.pulls)
                old_body = actor.body
                self.assertTrue(run.match.switch_hero(actor, replacement))
                self.assertNotEqual(actor.body, old_body)
                for _ in range(75):
                    run.step({})
                    self.assertNotIn(old_body, fight.halt.pulls)
                self.assertFalse(fight.halt.pulls)
                self.assertFalse(fight.halt.orbs)

    def test_halt_is_absorbed_by_live_matrix(self):
        fixture = matrix_tests.RoadhogMatrixTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if not deflection.absorbable(PROJECTILE):
            self.skipTest('prepared 104319 Halt projectile tags required')
        run, hog, orisa, mech = self.scene()
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(mech, replace(heroes()[matrix_tests.DVA], body=matrix_tests.MECH)))
        x, y, z = orisa.mover.position
        self.place(mech, (x, y, z + 8))
        for _ in range(100):
            run.step({mech: (-32768, 0, 0)})
        run.step({orisa: (0, 0, 2)})
        for _ in range(9):
            run.step({mech: (-32768, 0, 0)})
        self.assertTrue(fight.halt.orbs)
        before = tuple(hog.mover.position)
        script = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0xCCB)
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)
        for _ in range(20):
            run.step({mech: (-32768, 0, 2)})
            self.assertNotIn(hog.body, fight.halt.pulls)
        self.assertTrue(matrix.state(3).active)
        self.assertFalse(fight.halt.orbs)
        self.assertGreater(script.state(2).remaining(), 0)
        self.assertEqual(tuple(hog.mover.position), before)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def reflected_scene(self):
        run, hog, orisa, genji = self.scene()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = orisa.team = 0
        genji.team = 1
        self.switch(run, genji, 'Genji')
        x, y, z = orisa.mover.position
        self.place(genji, (x, y, z + 5))
        self.place(hog, (x + 1.5, y, z - 2))
        match.collision_world = World(
            box(x - 40, x + 40, y - 1, y, z - 40, z + 40)
            + box(x - 4, x + 4, y, y + 6, z - 4, z - 3.8),
        )
        for _ in range(30):
            run.step({genji: (-32768, 0, 0)})
        run.step({orisa: (0, 0, 2), genji: (-32768, 0, 16)})
        return run, hog, orisa, genji

    def test_halt_reflection_changes_the_projectile_team(self):
        run, hog, _, genji = self.reflected_scene()
        fight = run.match.combat
        reflected = pulled = False
        origin = tuple(hog.mover.position)
        for _ in range(95):
            run.step({genji: (-32768, 0, 0)})
            reflected |= any(orb.shooter is genji for orb in fight.halt.orbs.values())
            pulled |= hog.body in fight.halt.pulls
            self.assertNotIn(genji.body, fight.halt.pulls)
        self.assertTrue(reflected)
        self.assertTrue(pulled)
        self.assertLess(hog.mover.position[2], origin[2] - .5)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    @unittest.skipUnless(os.environ.get('ORPHEUS_REINHARDT_COLLISION'), 'actual barrier mesh required')
    def test_reflected_halt_checks_barrier_team_against_deflector(self):
        from test_game_combat import in_world

        for shield_with_genji in (False, True):
            with self.subTest(shield_with_genji=shield_with_genji):
                run, hog, orisa, genji = self.reflected_scene()
                match = run.match
                fight = match.combat
                team = genji.team if shield_with_genji else orisa.team
                rein = match.add_player(8, 'Barrier Field', hog.hero.guid, team, False)
                in_world(match, rein)
                self.switch(run, rein, 'Reinhardt')
                run.players.append(rein)
                x, y, z = orisa.mover.position
                self.place(rein, (x + .7, y, z - 1.65))
                raised = {rein: (-32768, 0, 2), genji: (-32768, 0, 0)}
                before = tuple(hog.mover.position[::2])
                for _ in range(95):
                    run.step(raised)
                    orb = next(iter(fight.halt.orbs.values()), None)
                    if orb is not None and orb.acquired:
                        break
                self.assertIsNotNone(orb)
                self.assertTrue(orb.acquired)
                self.assertIs(orb.shooter, genji)
                self.assertEqual(orb.shooter_body, genji.body)
                self.assertAlmostEqual(orb.position[2], z - 3.8, delta=.1)
                shield = fight.barriers[rein.body]
                self.assertTrue(shield.active)
                self.assertEqual(hog.body in fight.halt.pulls, shield_with_genji)
                for _ in range(45):
                    run.step(raised)
                if shield_with_genji:
                    self.assertLess(hog.mover.position[2], before[1] - .5)
                else:
                    self.assertEqual(tuple(hog.mover.position[::2]), before)
                self.assertEqual(shield.current, shield.maximum)
                self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_reflected_halt_lifetime_follows_the_deflector_body(self):
        for retiring in ('original', 'deflector'):
            for acquired in (False, True):
                with self.subTest(retiring=retiring, acquired=acquired):
                    run, hog, orisa, genji = self.reflected_scene()
                    halt = run.match.combat.halt
                    for _ in range(50):
                        run.step({genji: (-32768, 0, 0)})
                        if ((not acquired or hog.body in halt.pulls)
                                and any(orb.shooter is genji for orb in halt.orbs.values())):
                            break
                    orb, = halt.orbs.values()
                    self.assertIs(orb.shooter, genji)
                    self.assertEqual(orb.acquired, acquired)
                    actor = orisa if retiring == 'original' else genji
                    old_body = actor.body
                    self.switch(run, actor, 'Soldier: 76')
                    self.assertNotEqual(actor.body, old_body)
                    if retiring == 'original':
                        self.assertIs(halt.orbs.get(orb.entity), orb)
                    else:
                        self.assertFalse(halt.orbs)
                        self.assertFalse(halt.pulls)
                    before = tuple(hog.mover.position)
                    for _ in range(135):
                        run.step({})
                    if retiring == 'original':
                        self.assertLess(hog.mover.position[2], before[2] - .5)
                    else:
                        self.assertEqual(tuple(hog.mover.position), before)
                    self.assertFalse(halt.orbs)
                    self.assertFalse(halt.pulls)
                    self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_reflected_flight_can_detonate_after_deflector_is_killed(self):
        run, hog, _, genji = self.reflected_scene()
        fight = run.match.combat
        for _ in range(30):
            run.step({genji: (-32768, 0, 0)})
            if any(orb.shooter is genji for orb in fight.halt.orbs.values()):
                break
        orb, = fight.halt.orbs.values()
        self.assertIs(orb.shooter, genji)
        self.assertIsNone(orb.requested)
        before = tuple(hog.mover.position)
        x, y, z = genji.mover.position
        self.place(hog, (x, y, z + 1.4))
        self.lethal_attack(run, hog, genji, victim_look=(-32768, 0))
        self.assertIsNone(orb.requested)
        self.assertIs(fight.halt.orbs.get(orb.entity), orb)
        self.place(hog, before)
        self.assertTrue(fight.is_dead(genji))
        for _ in range(120):
            run.step({})
        self.assertTrue(orb.acquired)
        self.assertLess(hog.mover.position[2], before[2] - .5)
        self.assertFalse(fight.halt.pulls)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_lethal_scrap_during_caster_windup_flight_or_pull(self):
        for phase in ('windup', 'flight', 'open_flight', 'pull'):
            with self.subTest(phase=phase):
                run, hog, orisa, _ = self.scene()
                fight = run.match.combat
                x, y, z = orisa.mover.position
                if phase == 'pull':
                    self.cast(run, orisa)
                    run.step({})
                    self.assertIn(hog.body, fight.halt.pulls)
                else:
                    run.step({orisa: (0, 0, 2)})
                if phase == 'flight':
                    run.match.collision_world = World(
                        box(x - 40, x + 40, y - 1, y, z - 40, z + 40)
                        + box(x - 6, x + 6, y, y + 5, z + 7.6, z + 7.8),
                    )
                if phase in ('flight', 'open_flight'):
                    for _ in range(12):
                        if fight.halt.orbs:
                            break
                        run.step({})
                launched = phase != 'windup'
                self.assertEqual(bool(fight.halt.orbs), launched)
                initial = tuple(hog.mover.position)
                if not launched:
                    self.place(hog, (x + 1.4, y, z))
                self.lethal_attack(run, hog, orisa)
                if not launched:
                    self.assertFalse(fight.halt.orbs)
                    self.place(hog, initial)
                if phase == 'open_flight':
                    self.check_dead_caster_open_flight(run, hog, orisa)
                    continue
                before = tuple(hog.mover.position)
                pulled = False
                for _ in range(92):
                    run.step({orisa: (0, 0, 2)})
                    pulled |= hog.body in fight.halt.pulls
                self.assertEqual(pulled, launched)
                self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)
                self.assertFalse(fight.halt.orbs)
                if launched:
                    self.assertGreater(hog.mover.position[2], before[2] + .5)
                else:
                    self.assertEqual(tuple(hog.mover.position), before)

    def check_dead_caster_open_flight(self, run, hog, orisa):
        fight = run.match.combat
        old_body = orisa.body
        orb, = fight.halt.orbs.values()
        before = tuple(hog.mover.position)
        for _ in range(700):
            run.step({orisa: (0, 0, 2)})
            if orisa.body != old_body:
                break
            self.assertIsNone(orb.requested)
            self.assertIs(fight.halt.orbs.get(orb.entity), orb)
            self.assertFalse(fight.halt.pulls)
        self.assertNotEqual(orisa.body, old_body)
        self.assertFalse(fight.is_dead(orisa))
        self.assertFalse(fight.halt.orbs)
        self.assertEqual(tuple(hog.mover.position), before)

    def test_lethal_melee_retires_the_halt_victim_before_respawn(self):
        run, hog, orisa, shooter = self.scene()
        fight = run.match.combat
        self.switch(run, shooter, 'Roadhog')
        self.place(shooter, (8, 1, 5))
        for _ in range(30):
            run.step({})
        self.cast(run, orisa)
        for _ in range(30):
            run.step({})
        old_body = hog.body
        victim = fight.halt.pulls[old_body].script
        self.assertIsNotNone(self.pull_request(fight, old_body))
        x, y, z = hog.mover.position
        self.place(shooter, (x + 1.4, y, z + .5))
        self.lethal_attack(run, shooter, hog, button=0x800)
        self.assertNotIn(old_body, fight.halt.pulls)
        self.assertTrue(victim.stopped)
        for _ in range(700):
            run.step({})
            if hog.body != old_body:
                break
        self.assertNotEqual(hog.body, old_body)
        self.assertFalse(fight.is_dead(hog))
        self.assertFalse(fight.halt.pulls)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)
        x, y, z = orisa.mover.position
        self.place(hog, (x + 1.2, y, z + 5))
        self.place(shooter, (x + 30, y, z))
        for _ in range(30):
            run.step({})
        self.cast(run, orisa)
        for _ in range(75):
            run.step({})
            self.assertNotIn(old_body, fight.halt.pulls)
        self.assertGreater(hog.mover.position[2], z + 6)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    def test_halt_is_absorbed_by_live_kinetic_grasp(self):
        run, hog, orisa, sigma = self.scene()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(sigma, next(h for h in heroes().values() if h.name == 'Sigma')))
        x, y, z = orisa.mover.position
        self.place(sigma, (x, y, z + 5))
        for _ in range(30):
            run.step({sigma: (-32768, 0, 0)})
        before = tuple(hog.mover.position)
        run.step({orisa: (0, 0, 2), sigma: (-32768, 0, 8)})
        seen_orb = False
        for _ in range(35):
            run.step({sigma: (-32768, 0, 0)})
            seen_orb |= bool(fight.halt.orbs)
            self.assertNotIn(hog.body, fight.halt.pulls)
        self.assertTrue(seen_orb)
        self.assertFalse(fight.halt.orbs)
        script = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0xCCB)
        self.assertGreater(script.state(2).remaining(), 0)
        self.assertEqual(tuple(hog.mover.position), before)
        self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)

    @unittest.skipUnless(os.environ.get('ORPHEUS_NPC_GRAPHS'), 'actual five NPC graphs required')
    def test_halt_respects_each_npc_victim_graph_without_damage(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        for definition in fixture.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, orisa, rear = self.scene()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team = rear.team = 0
                orisa.team = 1
                x, y, z = orisa.mover.position
                bot = npc.spawn(match, definition, (x - 1.2, y, z + 5), team=0)
                for _ in range(5):
                    run.step({})
                hp = phases.RoadhogDefenseTests().health(run, bot)
                before = bot.position
                self.cast(run, orisa)
                run.step({})
                self.assertIn(hog.body, fight.halt.pulls)
                immune = definition.name == 'Heavy Assault'
                if immune:
                    self.assertIs(bot.body_script.component.entity_var(6601).value(), True)
                self.assertEqual(bot.entity in fight.halt.pulls, not immune)
                for _ in range(90):
                    run.step({})
                if immune:
                    self.assertEqual(bot.position, before)
                else:
                    self.assertGreater(bot.position[2], before[2] + 1)
                self.assertEqual(phases.RoadhogDefenseTests().health(run, bot), hp)
                self.assertEqual(phases.RoadhogDefenseTests().health(run, hog), 600)
                self.assertFalse(fight.halt.pulls)

    def test_halt_drives_roadhog_while_buffered_melee_hits(self):
        run, hog, orisa, victim = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, orisa.team, victim.team = 0, 1, 1
        health = phases.RoadhogDefenseTests().health
        x, y, z = hog.mover.position
        self.place(victim, (x + 1.5, y, z))
        run.step({})
        held = {hog: (16384, 0, 0x800)}
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        run.step(held)
        first = run.frame
        run.step({})
        run.step(held)
        self.assertEqual(health(run, victim), 19970)

        for offset in range(28):
            run.step({**held, orisa: (0, 0, 2 if offset == 0 else 0)})
        orb, = fight.halt.orbs.values()
        self.assertIsNone(orb.requested)
        run.step({**held, orisa: (0, 0, 2)})
        self.assertEqual(orb.requested, run.frame)
        while run.frame < first + 58:
            run.step(held)
        self.assertTrue(fight.halt.drove_pose(hog.body, run.frame))
        moved_x, moved_y, moved_z = hog.mover.position
        self.assertGreater(moved_z, z)
        self.place(victim, (moved_x + 1.35, moved_y, moved_z + .45))
        while run.frame < first + 61:
            run.step(held)
        self.assertEqual(health(run, victim), 19970)
        run.step(held)
        self.assertEqual(run.frame, first + 62)
        self.assertTrue(fight.halt.drove_pose(hog.body, run.frame))
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(health(run, hog), 600)
        self.assertEqual(health(run, victim), 19940)
        for _ in range(65):
            run.step(held)
        self.assertNotIn(hog.body, fight.halt.pulls)
        self.assertEqual(health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_halt_preserves_whole_hog_breather_and_hook(self):
        for button in (8, 16, 32):
            with self.subTest(button=button):
                run, hog, orisa, rear = self.scene()
                fight = run.match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                x, y, z = hog.mover.position
                self.place(rear, (x + 12, y, z))
                if button == 16:
                    pool.damage(350)
                    fight._sync_body_health(hog, pool)
                elif button == 32:
                    hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
                self.cast(run, orisa)
                for _ in range(22):
                    run.step({})
                before = pool.total
                origin = tuple(hog.mover.position)
                look = phases.aim(fight._eye(hog), fight._eye(rear))
                run.step({hog: (*look, button)})
                hooked = reduced = False
                for _ in range(65):
                    look = phases.aim(fight._eye(hog), fight._eye(rear))
                    run.step({hog: (*look, 0)})
                    hooked |= fight.hooks.controlled(rear.body)
                    reduced |= fight.incoming_damage_scale(hog) == 0.5
                self.assertGreater(hog.mover.position[2], origin[2] + 1)
                if button == 8:
                    self.assertTrue(hooked)
                    self.assertEqual(phases.RoadhogDefenseTests().health(run, rear), 19970)
                elif button == 16:
                    self.assertTrue(reduced)
                    self.assertGreater(pool.total, before)
                else:
                    ultimate, = hog.body_script.ultimates
                    self.assertTrue(ultimate.flags & 1)
