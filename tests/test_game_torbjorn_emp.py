"""Actual Sombra EMP body application against Torbjorn."""

import math
import os
import unittest
from dataclasses import replace

import test_game_torbjorn_hack as hack_tests
import test_game_torbjorn_orisa as orisa_tests
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import (
    combat,
    hero_torbjorn,
    reinhardt_barrier,
    sigma_barrier,
    sombra_control,
    stats,
    world_bubbles,
)
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs prepared combat inputs")
class TorbjornEmpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hack_tests.TorbjornHackTests.setUpClass()
        cls.addClassCleanup(hack_tests.TorbjornHackTests.doClassCleanups)

    def arena(self, distance=4):
        return hack_tests.TorbjornHackTests().arena(distance)

    @staticmethod
    def press(run, sombra, *, ready=True):
        if ready:
            charge = sombra.body_script.component.entity_var(338).value()
            sombra.body_script.set_ult_charge(charge)
        # Face away from the primary target to exercise the authored radial query.
        run.step({sombra: (0, 0, 32)})

    @staticmethod
    def advance(run, frames):
        for _ in range(frames):
            run.step({})

    def test_actual_emp_selects_two_enemies_without_aim_and_preserves_overload_armour(self):
        fixture, run, torb, sombra = self.arena()
        other = run.players[2]
        run.match.switch_hero(other, heroes()[sombra_control.TORBJORN])
        x, y, z = sombra.mover.position
        fixture.place(other, (x + 4, y, z))
        run.step({torb: (0, 0, 16)})
        self.advance(run, 25)
        self.assertEqual(fixture.health(run, torb).total, 350)
        self.press(run, sombra)
        fight = run.match.combat
        for _ in range(70):
            run.step({})
            if fight.sombra_hacks.silenced(torb.body, fight.tick):
                break
        self.assertEqual(set(fight.sombra_hacks.active), {torb.body, other.body})
        self.assertEqual(fixture.health(run, torb).total, 350)
        self.assertEqual(fixture.health(run, other).total, 250)
        self.assertEqual(sombra.body_script.component.entity_var(342).value(), 0)
        deadlines = {}
        for target in (torb, other):
            (component, effect, until), = fight.sombra_hacks.active[target.body]
            instance = component.instances[effect]
            self.assertEqual(instance.graph.index, sombra_control.EFFECT)
            duration = instance.evaluate(instance.graph.node(96).field("m_value"))
            self.assertEqual(expr.to_float(duration), 5)
            self.assertIn(until - fight.tick, (312, 313))
            deadlines[target.body] = until
        self.assertLessEqual(max(deadlines.values()) - min(deadlines.values()), 1)
        charge = other.body_script.component.entity_var(338).value()
        other.body_script.set_ult_charge(charge)
        run.step({other: (0, 0, 8 | 16 | 32)})
        self.advance(run, 65)
        self.assertEqual(fixture.health(run, other).total, 250)
        self.assertEqual(other.body_script.component.entity_var(342).value(), charge)
        self.assertFalse(fight.turrets or fight.molten_pools)
        until = deadlines[other.body]
        self.press(run, sombra, ready=False)
        for _ in range(until - fight.tick - 1):
            run.step({other: (0, 0, 16)})
        self.assertEqual(fight.sombra_hacks.active[other.body][-1][2], until)
        run.step({other: (0, 0, 16)})
        self.assertNotIn(other.body, fight.sombra_hacks.active)
        self.assertEqual(fixture.health(run, other).total, 250)
        run.step({})
        run.step({other: (0, 0, 16)})
        self.advance(run, 25)
        self.assertEqual(fixture.health(run, other).total, 350)

    def test_emp_drains_exposed_base_shields_but_preserves_overload_armor(self):
        fixture, run, torb, sombra = self.arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        sombra.team = 1 - torb.team
        zarya = run.players[2]
        self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.team = torb.team
        x, y, z = sombra.mover.position
        fixture.place(zarya, (x + 6, y, z))
        run.step({torb: (0, 0, 16)})
        self.advance(run, 20)
        hp = fixture.health(run, zarya)
        torb_hp = fixture.health(run, torb)
        self.assertEqual(hp.now[SHIELDS_POOL], 200)
        self.assertEqual(torb_hp.over_armour.amount, 100)
        self.press(run, sombra)
        self.advance(run, 55)
        self.assertEqual(hp.now[SHIELDS_POOL], 0)
        self.assertEqual(hp.now[HEALTH_POOL], 200)
        self.assertEqual(torb_hp.total, 350)
        self.assertEqual(torb_hp.over_armour.amount, 100)
        self.assertTrue(match.combat.sombra_hacks.silenced(torb.body, match.combat.tick))
        self.assertEqual(sombra.body_script.component.entity_var(342).value(), 0)

    def test_projected_barrier_blocks_aimed_hack_but_emp_silence_outlasts_it(self):
        fixture, run, torb, sombra = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        sombra.team = 1 - torb.team
        zarya = run.players[2]
        self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.team = torb.team
        x, y, z = torb.mover.position
        fixture.place(zarya, (x - 5, y, z))
        run.step({torb: (0, 0, 16), zarya: (*fixture.body_aim(run, zarya, torb), 16)})
        bubble = fight.bubbles.for_target(torb)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, zarya)
        hp = fixture.health(run, torb)
        self.assertEqual(hp.total, 350)
        source = hp.over_armour.source
        fixture.advance(run, torb, sombra, 40, defense=2)
        self.assertFalse(fight.sombra_hacks.active)
        self.assertEqual(hack_tests.TorbjornHackTests.ability(sombra).remaining(), 0)
        self.assertEqual(bubble.health, 200)

        self.press(run, sombra)
        for _ in range(65):
            if fight.sombra_hacks.silenced(torb.body, fight.tick):
                break
            run.step({})
        (component, effect, deadline), = fight.sombra_hacks.active[torb.body]
        self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
        self.assertIs(fight.bubbles.for_target(torb), bubble)
        self.assertEqual(bubble.health, 200)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(sombra.stats.value(stats.HERO_DAMAGE), 0)

        for _ in range(125):
            if fight.bubbles.for_target(torb) is None:
                break
            run.step({torb: (16384, 4000, 8)})
        self.assertIsNone(fight.bubbles.for_target(torb))
        self.assertEqual(bubble.health, 200)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.assertEqual(fight.sombra_hacks.active[torb.body], [(component, effect, deadline)])
        self.assertNotIn(torb.entity, fight.turrets)
        self.assertFalse(fight.rockets)

        sombra_hp = fixture.health(run, sombra)
        fixture.step(run, torb, sombra, fire=1)
        self.advance(run, 20)
        self.assertEqual(sombra_hp.total, 130)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.advance(run, deadline - fight.tick - 1)
        run.step({})
        self.assertEqual(fight.tick, deadline)
        self.assertNotIn(torb.body, fight.sombra_hacks.active)
        self.assertEqual(hero_torbjorn.turret_cooldown_remaining(torb), 0)
        run.step({torb: (16384, 4000, 8)})
        for _ in range(100):
            if torb.entity in fight.turrets:
                break
            run.step({torb: (16384, 4000, 0)})
        self.assertIn(torb.entity, fight.turrets)
        self.assertIs(fight.turrets[torb.entity].actor.combat_owner, torb)

    def test_emp_rejects_allied_out_of_range_and_wall_obscured_torbjorn(self):
        for obstacle in ("team", "range", "wall", "uncharged"):
            with self.subTest(obstacle=obstacle):
                _, run, torb, sombra = self.arena(25 if obstacle == "range" else 4)
                if obstacle == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    torb.team = sombra.team
                elif obstacle == "wall":
                    x, y, z = torb.mover.position
                    run.match.collision_world = World(
                        box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                        + wall(x - 5, z + 2, x + 5, z + 2, y0=y, y1=y + 4)
                    )
                self.press(run, sombra, ready=obstacle != "uncharged")
                self.advance(run, 70)
                self.assertFalse(run.match.combat.sombra_hacks.active)
                self.assertEqual(sombra.body_script.component.entity_var(342).value(), 0)

    def test_emp_destroys_enemy_world_shell_and_reaches_torbjorn(self):
        fixture, run, torb, sombra = self.arena(7)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sombra.team = 1 - torb.team
        shell = self.deploy_shell(fixture, run, torb, team=torb.team)
        fight = run.match.combat
        # A normal aimed Hack is blocked along this same path.
        fixture.advance(run, torb, sombra, 55, defense=2)
        self.assertFalse(fight.sombra_hacks.active)
        self.press(run, sombra)
        self.advance(run, 55)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.assertEqual(fixture.health(run, torb).total, 250)
        self.assertEqual(shell.health, 0)
        self.assertNotIn(shell.entity, fight.world_bubbles.actors)
        self.assertEqual(sombra.stats.value(stats.HERO_DAMAGE), 0)

    def test_emp_crosses_winston_shell_that_blocks_hack_on_the_same_turret(self):
        for caster_inside in (False, True):
            with self.subTest(caster_inside=caster_inside):
                hack = hack_tests.TorbjornHackTests()
                fixture, run, turret = hack.deployed_turret_arena(world_bubbles.WINSTON)
                torb, sombra, winston, bait = run.players
                fight = run.match.combat
                entity = turret.actor.entity
                x, y, z = turret.actor.position
                floor = torb.mover.position[1]
                fixture.place(torb, (x - 30, floor, z))
                fixture.place(winston, (x, floor, z + (7 if caster_inside else 0)))
                run.step({winston: (0, 0, 16)})
                self.advance(run, 15)
                shell, = fight.world_bubbles.actors.values()
                self.assertEqual(shell.health, 700)
                fixture.place(winston, (x + 30, floor, z))
                fixture.place(sombra, (x, floor, z + 7))
                fixture.place(bait, (x - 1.7, floor, z))
                target = (x, y + turret.actor.combat_geometry[2], z)
                origin = fight._eye(sombra)
                self.assertEqual(math.dist(origin, shell.point) < shell.radius, caster_inside)
                self.assertEqual(math.dist(target, shell.point) < shell.radius, not caster_inside)
                direction = aim(origin, target)
                hit = fight.world_bubbles.trace(
                    sombra, origin, combat.aim(*direction), math.dist(origin, target),
                )
                self.assertIsNotNone(hit)
                self.assertIs(hit[1], shell)
                ability = hack.ability(sombra)
                hp = fixture.health(run, bait)
                before, shots = hp.total, turret.shots
                for _ in range(55):
                    run.step({sombra: (*direction, 2)})
                    self.assertFalse(fight.sombra_hacks.active)
                    self.assertEqual(shell.health, 700)
                self.assertEqual(ability.remaining(), 0)
                self.assertGreater(turret.shots, shots)
                self.assertEqual(hp.total, before - 14 * (turret.shots - shots))

                self.press(run, sombra)
                for _ in range(70):
                    if fight.sombra_hacks.silenced(entity, fight.tick):
                        break
                    run.step({})
                (component, effect, deadline), = fight.sombra_hacks.active[entity]
                self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
                self.assertIn(deadline - fight.tick, (312, 313))
                self.assertFalse(fight.sombra_hacks.silenced(torb.body, fight.tick))
                self.assertFalse(fight.sombra_hacks.silenced(winston.body, fight.tick))
                self.assertEqual(shell.health, 0)
                self.assertNotIn(shell.entity, fight.world_bubbles.actors)
                self.assertEqual(sombra.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(fight.vitals[entity].total, 250)
                before, shots = hp.total, turret.shots
                self.advance(run, deadline - fight.tick - 1)
                self.assertTrue(fight.sombra_hacks.silenced(entity, fight.tick))
                self.assertEqual(turret.shots, shots)
                self.assertEqual(hp.total, before)
                run.step({})
                self.assertEqual(fight.tick, deadline)
                self.assertNotIn(effect, component.instances)
                self.assertNotIn(entity, fight.sombra_hacks.active)
                self.assertIs(fight.turrets[torb.entity], turret)
                self.assertEqual(turret.target, bait.body)
                self.assertEqual(turret.shots, shots + 1)
                self.assertEqual(hp.total, before - 14)

    @unittest.skipUnless(all(os.environ.get(key) for key in (
        "ORPHEUS_REINHARDT_COLLISION", "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_ORISA_COLLISION",
    )), "needs authored barrier meshes")
    def test_emp_breaks_hostile_meshes_and_silences_the_turret_behind_them(self):
        reinhardt = reinhardt_barrier.HERO
        sigma = sigma_barrier.HERO
        orisa = 0x02E000000000013E
        cases = ((reinhardt, "hit"), (sigma, "hit"), (orisa, "hit"),
                 (sigma, "team"), (orisa, "wall"), (reinhardt, "range"))
        for hero, outcome in cases:
            with self.subTest(hero=hero, outcome=outcome):
                fixture, run, turret = hack_tests.TorbjornHackTests().deployed_turret_arena(hero)
                torb, sombra, owner, bait = run.players
                match = run.match
                fight = match.combat
                entity = turret.actor.entity
                x, y, z = turret.actor.position
                floor = torb.mover.position[1]
                fixture.place(torb, (x - 30, floor, z))
                shield_input = {}
                if hero == reinhardt:
                    fixture.place(owner, (x + 1.4, floor, z + 2.3))
                    shield_input = {owner: (0, 0, 2)}
                    for _ in range(5):
                        run.step(shield_input)
                    shield = fight.barriers.states[owner.body]
                    barriers = fight.barriers
                    capacity = 1600
                    front = z + 5
                else:
                    fixture.place(owner, (x, floor, z + 8))
                    if hero == sigma:
                        for _ in range(15):
                            run.step({owner: (-32768, 0, 2)})
                        run.step({owner: (-32768, 0, 0)})
                        barriers = fight.sigma_barriers
                        shield = barriers.actors[owner.body]
                        capacity = 700
                    else:
                        shield = orisa_tests.TorbjornOrisaTests().deploy(run, owner)
                        barriers = fight.orisa_barriers
                        capacity = 600
                    fixture.place(owner, (x + 25, floor, z))
                    front = shield.position[2] + 3
                self.assertEqual(shield.current if hero == reinhardt else shield.health, capacity)
                fixture.place(sombra, (x, floor, front))
                target = (x, y + turret.actor.combat_geometry[2], z)
                origin = fight._eye(sombra)
                direction = aim(origin, target)
                hit = barriers.trace(sombra, origin, combat.aim(*direction), math.dist(origin, target))
                self.assertIsNotNone(hit)
                self.assertIs(hit[1], owner if hero == reinhardt else shield)
                if outcome == "team":
                    owner.team = sombra.team
                if outcome == "range":
                    fixture.place(sombra, (x, floor, z + 40))
                if outcome == "wall":
                    divider = front - 1
                    match.collision_world = World(
                        box(x - 25, x + 25, floor - 1, floor, z - 25, z + 25)
                        + wall(x - 10, divider, x + 10, divider, y0=floor, y1=floor + 4)
                    )
                fixture.place(bait, (x - 1.7, floor, z))
                charge = sombra.body_script.component.entity_var(338).value()
                sombra.body_script.set_ult_charge(charge)
                run.step({sombra: (0, 0, 32), **shield_input})
                for _ in range(70):
                    if fight.sombra_hacks.silenced(entity, fight.tick):
                        break
                    run.step(shield_input)

                if outcome == "hit":
                    self.assertEqual(shield.current if hero == reinhardt else shield.health, 0)
                    (component, effect, deadline), = fight.sombra_hacks.active[entity]
                    self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
                    self.assertIn(deadline - fight.tick, (312, 313))
                    self.assertEqual(fight.vitals[entity].total, 250)
                    hp = fixture.health(run, bait)
                    before, shots = hp.total, turret.shots
                    self.advance(run, deadline - fight.tick - 1)
                    self.assertEqual(turret.shots, shots)
                    self.assertEqual(hp.total, before)
                    run.step({})
                    self.assertNotIn(effect, component.instances)
                    self.assertEqual(turret.shots, shots + 1)
                    self.assertEqual(hp.total, before - 14)
                else:
                    self.assertEqual(shield.current if hero == reinhardt else shield.health, capacity)
                    self.assertEqual(fight.sombra_hacks.silenced(entity, fight.tick), outcome == "team")
                self.assertLess(sombra.body_script.component.entity_var(342).value(), charge)
                self.assertEqual(sombra.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(fight.sombra_hacks.silenced(torb.body, fight.tick))

    def deploy_shell(self, fixture, run, torb, *, team):
        winston_guid = 0x02E0000000000009
        winston = run.match.add_player(8, "Winston", winston_guid, team, False)
        in_world(run.match, winston)
        run.players.append(winston)
        run.match.switch_hero(winston, heroes()[winston_guid])
        x, y, z = torb.mover.position
        fixture.place(winston, (x + 2, y, z))
        self.advance(run, 35)
        run.step({winston: (0, 0, 16)})
        self.advance(run, 15)
        fight = run.match.combat
        shell, = fight.world_bubbles.actors.values()
        self.assertEqual(shell.health, 700)
        return shell

    def test_emp_spares_allied_distant_and_world_occluded_shells(self):
        for obstacle in ("team", "range", "wall"):
            with self.subTest(obstacle=obstacle):
                fixture, run, torb, sombra = self.arena(7)
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                sombra.team = 1 - torb.team
                team = sombra.team if obstacle == "team" else torb.team
                shell = self.deploy_shell(fixture, run, torb, team=team)
                x, _, z = shell.point
                y = torb.mover.position[1]
                if obstacle != "team":
                    fixture.place(sombra, (x, y, z + (40 if obstacle == "range" else 14)))
                    triangles = box(x - 50, x + 50, y - 1, y, z - 50, z + 50)
                    if obstacle == "wall":
                        triangles += wall(x - 6, z + 12, x + 6, z + 12, y0=y, y1=y + 5)
                    match.collision_world = World(triangles)
                self.press(run, sombra)
                self.advance(run, 55)
                self.assertEqual(shell.health, 700)
                self.assertIn(shell.entity, match.combat.world_bubbles.actors)
                self.assertEqual(sombra.body_script.component.entity_var(342).value(), 0)

    def test_later_emp_keeps_skill_lock_past_earlier_hack_expiry(self):
        fixture, run, torb, sombra = self.arena()
        fight = run.match.combat
        first = hack_tests.TorbjornHackTests().cast(fixture, run, torb, sombra)
        self.advance(run, 45)
        self.press(run, sombra)
        last = first
        for _ in range(70):
            run.step({})
            last = max(row[2] for row in fight.sombra_hacks.active[torb.body])
            if last > first:
                break
        self.assertGreater(last, first)
        self.advance(run, first - fight.tick)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        run.step({torb: (0, 0, 16)})
        self.advance(run, 15)
        self.assertEqual(fixture.health(run, torb).total, 250)
        self.advance(run, last - fight.tick)
        self.assertFalse(fight.sombra_hacks.active)
        run.step({torb: (0, 0, 16)})
        self.advance(run, 25)
        self.assertEqual(fixture.health(run, torb).total, 350)

    def test_equipped_hammer_repairs_during_applied_emp(self):
        case = hack_tests.TorbjornHackTests()
        fixture, run, torb, sombra, turret, hp, distant = case.damaged_turret_with_hammer_equipped()
        fight = run.match.combat
        x, y, z = turret.actor.position
        before = hp.total
        fixture.place(sombra, (x - 1.6, y, z + 6))
        self.press(run, sombra)
        for _ in range(70):
            if fight.sombra_hacks.silenced(torb.body, fight.tick):
                break
            run.step({})
        (_, _, deadline), = fight.sombra_hacks.active[torb.body]
        fixture.place(sombra, distant)
        repair_aim = aim(fight._eye(torb), (x, y + .4, z))
        charge = torb.body_script.component.entity_var(338).value()
        torb.body_script.set_ult_charge(charge)
        run.step({torb: (*repair_aim, 1 | 16 | 32)})
        for _ in range(40):
            run.step({torb: (*repair_aim, 0)})
        self.assertEqual(hp.total, before + 50)
        self.assertIs(fight.turrets[torb.entity], turret)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.assertEqual(fight.sombra_hacks.active[torb.body][-1][2], deadline)
        self.assertEqual(fixture.health(run, torb).over_armour.amount, 0)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), charge)

    def test_emp_preserves_weapon_switch_and_holstered_refill_timing(self):
        for scenario in ('refill', 'reselect', 'holster'):
            with self.subTest(scenario=scenario):
                self.exercise_emp_rifle_refill(scenario)

    def exercise_emp_rifle_refill(self, scenario):
        from ow174.game.commands import Command

        fixture, run, torb, sombra = self.arena(distance=1.5)
        fight = run.match.combat
        component = torb.body_script.component
        rifle = next(instance for instance in component.instances.values()
                     if instance.graph.index == 0x1683)
        ammo = rifle.vars[53]
        run.step({torb: (0, -12000, 1)})
        self.advance(run, 50)
        hp = fixture.health(run, sombra)

        def select(action):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            yaw, pitch = aim(fight._eye(torb), fight._eye(sombra))
            torb.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, action=action)])
            sombra.take_commands([Command(run.frame)])

        wait = rifle.state(25)
        if scenario != 'holster':
            select(2)
            started = wait.start
        self.advance(run, 35)
        self.press(run, sombra)
        for _ in range(70):
            if fight.sombra_hacks.silenced(torb.body, fight.tick):
                break
            run.step({})
        (_, _, emp_deadline), = fight.sombra_hacks.active[torb.body]
        self.assertEqual(hp.total, 200)
        self.assertEqual(ammo.value(), 17)
        self.assertEqual(wait.active, scenario != 'holster')
        if scenario != 'holster':
            self.assertEqual(wait.start, started)
        if scenario == 'reselect':
            select(1)
            self.assertFalse(wait.active)
        if scenario == 'holster':
            select(2)
            self.assertTrue(wait.active)
        self.assertEqual(wait.ms, 2000)
        deadline = wait.start + wait.ms
        self.advance(run, 130)
        self.assertFalse(wait.active)
        self.assertGreaterEqual(component.now, deadline)
        if scenario == 'reselect':
            self.assertLess(wait.ended_at, deadline)
            self.assertEqual(ammo.value(), 17)
        else:
            self.assertEqual(wait.ended_at, deadline)
            self.assertEqual(ammo.value(), 18)
        if scenario != 'reselect':
            select(1)
        self.advance(run, 45)
        run.step({torb: (*aim(fight._eye(torb), fight._eye(sombra)), 1)})
        self.advance(run, 35)
        self.assertEqual(hp.total, 130)
        self.assertEqual(ammo.value(), 16 if scenario == 'reselect' else 17)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.assertEqual(fight.sombra_hacks.active[torb.body][-1][2], emp_deadline)

    def test_turret_lethal_shot_interrupts_emp_windup_and_respawn_can_recast(self):
        hack = hack_tests.TorbjornHackTests()
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                fixture, run, turret = hack.deployed_turret_arena(world_bubbles.WINSTON)
                torb, sombra, winston, _ = run.players
                match = run.match
                fight = match.combat
                entity = turret.actor.entity
                x, _, z = turret.actor.position
                floor = torb.mover.position[1]
                fixture.place(torb, (x - 30, floor, z))
                fixture.place(winston, (x, floor, z))
                run.step({winston: (0, 0, 16)})
                self.advance(run, 15)
                shell, = fight.world_bubbles.actors.values()
                self.assertEqual(shell.health, 700)
                fixture.place(winston, (x + 30, floor, z))
                cast_position = (x, floor, z + 7)
                fixture.place(sombra, cast_position)
                hp = fixture.health(run, sombra)
                old_body = sombra.body
                if lethal:
                    hp.damage(hp.total - 14)
                before = hp.total
                clear = match.collision_world
                match.collision_world = World(
                    box(x - 25, x + 25, floor - 1, floor, z - 25, z + 25)
                    + wall(x - 6, z + 3, x + 6, z + 3, y0=floor, y1=floor + 5)
                )
                self.press(run, sombra)
                self.advance(run, 17)
                emp = next(i for i in sombra.body_script.component.instances.values()
                           if i.graph.index == sombra_control.EMP)
                windup = emp.state(11)  # The 0.35 s wait starts radial target selection when it finishes.
                self.assertTrue(windup.active)
                self.assertEqual(turret.shots, 0)
                self.assertFalse(fight.sombra_hacks.active)
                self.assertEqual(shell.health, 700)
                match.collision_world = clear
                for _ in range(3):
                    run.step({})
                    if turret.shots:
                        break
                self.assertEqual(turret.shots, 1)
                self.assertEqual(hp.total, before - 14)
                self.assertFalse(fight.sombra_hacks.active)
                self.assertEqual(hp.dead, lethal)
                if lethal:
                    self.assertEqual(hp.killer, torb.body)
                    for _ in range(55):
                        run.step({sombra: (0, 0, 32)})
                        self.assertFalse(fight.sombra_hacks.active)
                        self.assertEqual(shell.health, 700)
                    self.assertFalse(windup.active)
                    self.assertTrue(fight.is_dead(sombra))
                    self.assertIs(fight.turrets[torb.entity], turret)
                    self.assertEqual(turret.shots, 1)
                    for _ in range(650):
                        run.step({})
                        if sombra.body != old_body:
                            break
                    self.assertNotEqual(sombra.body, old_body)
                    self.assertFalse(fight.is_dead(sombra))
                    self.assertEqual(fixture.health(run, sombra).total, 200)
                    self.assertFalse(fight.sombra_hacks.active)
                    fixture.place(sombra, cast_position)
                    self.press(run, sombra)
                else:
                    self.assertTrue(windup.active)

                for _ in range(70):
                    if fight.sombra_hacks.silenced(entity, fight.tick):
                        break
                    run.step({})
                (component, effect, deadline), = fight.sombra_hacks.active[entity]
                self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
                self.assertIn(deadline - fight.tick, (312, 313))
                self.assertEqual(fight.vitals[entity].total, 250)
                self.assertIs(fight.turrets[torb.entity], turret)
                if not lethal:
                    self.assertEqual(shell.health, 0)
                    self.assertNotIn(shell.entity, fight.world_bubbles.actors)

    def test_deployed_turret_kills_emp_caster_without_shortening_silence(self):
        self.exercise_turret_emp(in_range=False)

    def test_emp_before_on_or_after_turret_landing_selects_live_actor(self):
        for press_frame, emp_frame in ((0, 22), (1, 23), (3, 25)):
            with self.subTest(emp_frame=emp_frame):
                fixture, run, torb, sombra = self.arena()
                fight = run.match.combat
                x, y, z = torb.mover.position
                fixture.place(sombra, (x + 3, y, z + 3))
                charge = sombra.body_script.component.entity_var(338).value()
                sombra.body_script.set_ult_charge(charge)
                hp = fixture.health(run, sombra)
                for frame in range(26):
                    inputs = {torb: (16384, 4000, 8)} if frame == 0 else {}
                    if frame == press_frame:
                        inputs[sombra] = (0, 0, 32)
                    run.step(inputs)
                    self.assertEqual(torb.entity in fight.turrets, frame >= 23)
                    if 3 <= frame <= 22:
                        self.assertTrue(any(
                            rocket.spawn == hero_torbjorn.TURRET for rocket in fight.rockets
                        ))
                    self.assertEqual(
                        fight.sombra_hacks.silenced(torb.body, fight.tick), frame >= emp_frame,
                    )
                turret = fight.turrets[torb.entity]
                entity = turret.actor.entity
                turret_hp = fight.vitals[entity]
                (_, _, deadline), = fight.sombra_hacks.active[torb.body]
                self.assertEqual(turret.shots, 0)
                self.assertEqual(hp.total, 200)
                if emp_frame == 25:
                    (component, effect, turret_deadline), = fight.sombra_hacks.active[entity]
                    self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
                    self.assertEqual(turret_deadline, deadline)
                    self.advance(run, deadline - fight.tick - 1)
                    self.assertTrue(fight.sombra_hacks.silenced(entity, fight.tick))
                    self.assertEqual(turret_hp.total, 250)
                    self.assertEqual(turret.shots, 0)
                    self.assertEqual(hp.total, 200)
                    run.step({})
                    self.assertEqual(fight.tick, deadline)
                else:
                    self.assertNotIn(entity, fight.sombra_hacks.active)
                    while fight.tick < deadline - 1 and turret.shots == 0:
                        run.step({})
                    self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
                self.assertFalse(fight.sombra_hacks.silenced(entity, fight.tick))
                self.assertIs(fight.turrets[torb.entity], turret)
                self.assertEqual(turret_hp.total, 250)
                self.assertEqual(turret.shots, 1)
                self.assertEqual(hp.total, 186)

    def test_emp_disables_in_range_turret_until_authored_effect_expires(self):
        self.exercise_turret_emp(in_range=True)

    def exercise_turret_emp(self, *, in_range):
        fixture, run, torb, sombra = self.arena()
        fight = run.match.combat
        position = tuple(sombra.mover.position)
        x, y, z = torb.mover.position
        fixture.place(sombra, (x + 100, y, z + 100))
        if not in_range:
            fixture.place(torb, (x + 20, y, z))
        run.step({torb: (16384, 4000, 8)})
        self.advance(run, 225)
        fixture.place(torb, (x, y, z))
        turret = fight.turrets[torb.entity]
        turret_hp = fight.vitals[turret.actor.entity]
        self.assertEqual(turret_hp.total, 250)
        self.assertEqual(turret.shots, 0)

        fixture.place(sombra, position)
        hp = fixture.health(run, sombra)
        self.assertEqual(hp.total, 200)
        self.press(run, sombra)
        if in_range:
            for _ in range(70):
                if fight.sombra_hacks.silenced(turret.actor.entity, fight.tick):
                    break
                run.step({})
            component, effect, deadline = fight.sombra_hacks.active[turret.actor.entity][0]
            self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
            self.assertIn(deadline - fight.tick, (312, 313))
            shots, before = turret.shots, hp.total
            self.advance(run, deadline - fight.tick - 1)
            self.assertEqual(turret.shots, shots)
            self.assertEqual(hp.total, before)
            self.assertIsNone(turret.target)
            self.assertEqual(turret_hp.total, 250)
            run.step({})
            self.assertFalse(fight.sombra_hacks.silenced(turret.actor.entity, fight.tick))
            self.assertEqual(turret.shots, shots + 1)
            self.assertEqual(hp.total, before - 14)
            return
        for _ in range(70):
            if fight.sombra_hacks.silenced(torb.body, fight.tick):
                break
            run.step({})
        (_, _, deadline), = fight.sombra_hacks.active[torb.body]
        self.assertFalse(fight.sombra_hacks.silenced(turret.actor.entity, fight.tick))
        self.assertFalse(hp.dead)
        shots = turret.shots
        while fight.tick < deadline - 1 and not hp.dead:
            run.step({})
            self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
            self.assertEqual(fight.sombra_hacks.active[torb.body][-1][2], deadline)
            self.assertIs(fight.turrets[torb.entity], turret)
            self.assertEqual(turret_hp.total, 250)
        self.assertTrue(hp.dead)
        self.assertGreater(turret.shots, shots)
        self.assertEqual(turret.shots, 15)
        self.assertEqual(hp.killer, torb.body)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 200)
        self.advance(run, deadline - fight.tick - 1)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        run.step({})
        self.assertNotIn(torb.body, fight.sombra_hacks.active)
        self.assertIs(fight.turrets[torb.entity], turret)

    def test_turret_hack_and_emp_overlap_until_the_later_effect_expires(self):
        for first in ("Hack", "EMP"):
            with self.subTest(first=first):
                self.check_turret_silence_expiry(first)

    def turret_silence_overlap(self, first):
        fixture, run, torb, sombra = self.arena()
        match = run.match
        fight = match.combat
        hacks = fight.sombra_hacks
        match.game_map = replace(match.game_map, free_for_all=False)
        bait = run.players[2]
        bait.team = sombra.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(sombra, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        self.advance(run, 225)
        turret = fight.turrets[torb.entity]
        entity = turret.actor.entity
        x, y, z = turret.actor.position
        floor = torb.mover.position[1]
        fixture.place(bait, (x + 2, floor, z))
        fixture.place(sombra, (x, floor, z + 5))
        direction = aim(fight._eye(sombra), (x, y + turret.actor.combat_geometry[2], z))

        def cast(kind, expected_count):
            if kind == "EMP":
                self.press(run, sombra)
            for _ in range(70):
                if len(hacks.active.get(entity, ())) == expected_count:
                    break
                run.step({sombra: (*direction, 2)} if kind == "Hack" else {})
            self.assertEqual(len(hacks.active.get(entity, ())), expected_count)
            return hacks.active[entity][-1]

        first_effect = cast(first, 1)
        self.advance(run, 40)
        second_effect = cast("EMP" if first == "Hack" else "Hack", 2)
        fixture.place(bait, (x + 100, floor, z))
        return fixture, run, torb, sombra, turret, first_effect, second_effect

    def check_turret_silence_expiry(self, first):
        fixture, run, torb, sombra, turret, initial, latest = self.turret_silence_overlap(first)
        fight = run.match.combat
        hacks = fight.sombra_hacks
        entity = turret.actor.entity
        turret_hp = fight.vitals[entity]
        component, old_effect, earlier = initial
        same_component, new_effect, later = latest
        self.assertIs(same_component, component)
        self.assertNotEqual(new_effect, old_effect)
        self.assertGreater(later, earlier)
        for effect in (old_effect, new_effect):
            self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
        self.assertTrue(component.entity_var(888).value())

        sombra_hp = fixture.health(run, sombra)
        shots, before = turret.shots, sombra_hp.total
        self.advance(run, earlier - fight.tick)
        self.assertNotIn(old_effect, component.instances)
        self.assertIn(new_effect, component.instances)
        self.assertEqual(hacks.active[entity], [(component, new_effect, later)])
        self.assertTrue(component.entity_var(888).value())
        self.advance(run, later - fight.tick - 1)
        self.assertEqual(turret.shots, shots)
        self.assertEqual(sombra_hp.total, before)
        self.assertIsNone(turret.target)

        run.step({})
        self.assertEqual(fight.tick, later)
        self.assertNotIn(entity, hacks.active)
        self.assertNotIn(new_effect, component.instances)
        self.assertFalse(component.entity_var(888).value())
        self.assertIs(fight.turrets[torb.entity], turret)
        self.assertEqual(turret_hp.total, 250)
        self.assertEqual(turret.target, sombra.body)
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(sombra_hp.total, before - 14)

    def test_hero_switch_retires_both_turret_silences_before_fresh_deployment(self):
        fixture, run, torb, sombra, turret, initial, latest = self.turret_silence_overlap("Hack")
        match = run.match
        fight = match.combat
        hacks = fight.sombra_hacks
        entity = turret.actor.entity
        component, hack_effect, earlier = initial
        _, emp_effect, later = latest
        self.assertLess(fight.tick, earlier)
        self.assertEqual(hacks.active[entity], [initial, latest])
        self.assertTrue(hacks.silenced(entity, fight.tick))
        self.assertTrue(hacks.silenced(torb.body, fight.tick))
        old_body = torb.body
        old_hero = torb.hero
        old_position = tuple(torb.mover.position)
        sombra_position = tuple(sombra.mover.position)
        old_shots = turret.shots

        self.assertTrue(match.switch_hero(torb, heroes()[SOLDIER]))
        self.assertNotIn(torb.entity, fight.turrets)
        self.assertNotIn(turret.actor, match.bots)
        self.assertNotIn(entity, fight.vitals)
        self.assertNotIn(entity, hacks.active)
        self.assertNotIn(entity, hacks.bases)
        self.assertNotIn(old_body, hacks.active)
        self.assertNotIn(hack_effect, component.instances)
        self.assertNotIn(emp_effect, component.instances)
        self.assertFalse(component.entity_var(888).value())

        self.assertTrue(match.switch_hero(torb, old_hero))
        self.assertNotEqual(torb.body, old_body)
        fixture.place(torb, old_position)
        x, y, z = old_position
        fixture.place(sombra, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        self.advance(run, 215)
        fresh = fight.turrets[torb.entity]
        self.assertNotEqual(fresh.actor.entity, entity)
        self.assertEqual(fight.vitals[fresh.actor.entity].total, 250)
        self.assertEqual(fresh.shots, 0)
        self.assertLess(fight.tick, earlier)
        fixture.place(sombra, sombra_position)
        hp = fixture.health(run, sombra)
        before = hp.total
        for _ in range(12):
            run.step({})
            if fresh.shots:
                break
        self.assertLess(fight.tick, earlier)
        self.assertEqual(fresh.target, sombra.body)
        self.assertEqual(fresh.shots, 1)
        self.assertEqual(hp.total, before - 14)

        fixture.place(sombra, (x + 100, y, z + 100))
        self.advance(run, later - fight.tick)
        self.assertEqual(turret.shots, old_shots)
        self.assertIs(fight.turrets[torb.entity], fresh)
        self.assertNotIn(fresh.actor.entity, hacks.active)
        self.assertEqual(fresh.shots, 1)

    def test_emp_allows_active_molten_fire_and_existing_pool_kills_caster(self):
        fixture, run, torb, sombra = self.arena(8)
        fight = run.match.combat
        pool = hack_tests.TorbjornHackTests().launch_molten_pool(fixture, run, torb, sombra)
        self.press(run, sombra)
        for _ in range(70):
            run.step({})
            if fight.sombra_hacks.silenced(torb.body, fight.tick):
                break
        (_, _, deadline), = fight.sombra_hacks.active[torb.body]
        hp = fixture.health(run, sombra)
        initial_health = hp.total
        self.assertEqual(initial_health, hp.maximum)
        fixture.place(sombra, pool.position)

        for _ in range(15):
            run.step({})
            if hp.total < initial_health:
                break
        self.assertLess(hp.total, initial_health)
        self.assertEqual(list(fight.molten_pools.values()), [pool])
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))

        # Fire the second globule away from Sombra, leaving the first pool to kill her.
        run.step({torb: (16384, 4000, 1 | 8 | 16 | 32)})
        for _ in range(160):
            run.step({})
            if hp.dead and len(fight.molten_pools) == 2:
                break
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, torb.body)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), initial_health)
        self.assertNotIn((torb.entity, sombra.body), fight.molten_burns)
        self.assertEqual(len(fight.molten_pools), 2)
        self.assertIn(pool.entity, fight.molten_pools)
        self.assertFalse(fight.turrets)
        self.assertEqual(fixture.health(run, torb).total, 250)
        self.assertLess(fight.tick, deadline)
        self.assertEqual(fight.sombra_hacks.active[torb.body][-1][2], deadline)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.advance(run, deadline - fight.tick)
        self.assertNotIn(torb.body, fight.sombra_hacks.active)
