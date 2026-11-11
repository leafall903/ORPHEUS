"""Actual Roadhog Hook against Sombra and the five static NPC bodies."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_ana_rifle as rifle_fixture
import test_game_sombra_assassin as assassin_fixture
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_matrix as matrix_fixture
import test_game_sombra_npc as npc_fixture
import test_game_sombra_npc_projected as projected_fixture
from test_game_combat import SOLDIER, aim

from ow174.game import combat, dva, hook, phase_defenses, reinhardt_barrier, shot_guards, translocators
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL


class SombraRoadhogHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rifle_fixture.SombraAnaRifleTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        rifle_fixture.SombraAnaRifleTests.restore_graphs(path)

    def arena(self, *, distance=8, ally=False):
        f = grenade_fixture.SombraGrenadeTests().arena(ally=ally)
        self.assertTrue(f.run.match.switch_hero(f.b, heroes()[hook.HERO]))
        f.b.mover.position[:] = (0, 0, distance)
        f.b.mover.body = None
        f.press(0, 40)
        return f

    def launch(self, f, hog, point, *, held=None):
        fight = f.run.match.combat
        facing = aim(fight._eye(hog), point)
        commands = dict(held or ())
        for offset in range(22):
            commands[hog] = (*facing, 8 if offset == 0 else 0)
            f.run.step(commands)
            flight = fight.hooks.flights.get(hog.body)
            if flight is not None:
                return flight
        self.fail("The authored 01C8 launch did not start")

    def catch(self, f, flight):
        fight = f.run.match.combat
        for _ in range(26):
            if flight.target is not None:
                return flight.target
            if flight.body not in fight.hooks.flights:
                break
            f.run.step({})
        self.fail("The authored launch did not catch a body")

    def test_actual_hook_damage_pull_release_match_contiguous_and_world_only_gap(self):
        for catchup in (False, True):
            with self.subTest(catchup=catchup):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                flight = self.launch(f, f.b, (0, 1, 0))
                self.assertEqual((flight.reach, flight.speed, flight.damage.amount), (20, 60, 30))
                start = tuple(f.a.mover.position)
                if catchup:
                    f.run.frame += 90
                    match.update(f.run.frame * combat.TICK, f.run.frame)
                else:
                    target = self.catch(f, flight)
                    self.assertIs(target.owner, f.a)
                    self.assertTrue(fight.is_stunned(f.a))
                    self.assertEqual(f.health(f.a), 170)
                    caught = f.run.frame
                    f.run.step({f.a: (0, 0, FIRE | 2 | 8 | 16 | 32)})
                    self.assertEqual(f.health(f.b), 600)
                    self.assertFalse(f.a.client.messages(combat.HIT))
                    self.assertGreater(f.a.mover.position[2], start[2])
                    f.press(0, 89 - (caught - flight.launch_tick))
                self.assertEqual(f.health(f.a), 170)
                self.assertFalse(fight.hooks.flights)
                self.assertFalse(fight.is_stunned(f.a))
                self.assertAlmostEqual(math.dist(f.b.mover.position, f.a.mover.position),
                                       flight.separation, delta=0.15)
                self.assertEqual(f.a.mover.velocity, (0, 0, 0))
                f.press(FIRE)
                self.assertEqual(f.health(f.b), 592)

    def test_open_wall_miss_ally_and_authored_range(self):
        for mode in ("open", "wall", "miss", "ally", "outside"):
            with self.subTest(mode=mode):
                f = self.arena(distance=21 if mode == "outside" else 19 if mode == "open" else 8,
                               ally=mode == "ally")
                fight = f.run.match.combat
                if mode == "wall":
                    f.wall()
                self.launch(f, f.b, (4 if mode == "miss" else 0, 1, 0))
                f.press(0, 80)
                self.assertEqual(f.health(f.a), 170 if mode == "open" else 200)
                self.assertEqual(bool(f.a.mover.position[2]), mode == "open")
                self.assertFalse(fight.hooks.flights)

    def test_actual_hook_catches_pulls_and_retires_all_five_npcs(self):
        fixture = projected_fixture.SombraNpcProjectedTests()
        fixture.catalog = self.catalog
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = fixture.arena(key)
                fight = f.run.match.combat
                hog, _ = fixture.add_attacker(f, bot, hook.HERO)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                armour = vitals.now[ARMOUR_POOL] > 0 and vitals.now[SHIELDS_POOL] == 0
                x, y, z = bot.position
                flight = self.launch(f, hog, (x, y + 1, z))
                self.assertIs(self.catch(f, flight).owner, bot)
                self.assertEqual(vitals.total, before - (25 if armour else 30))
                self.assertTrue(fight.is_stunned(bot))
                f.press(0, 65)
                self.assertFalse(fight.is_stunned(bot))
                self.assertFalse(fight.hooks.flights)
                self.assertGreater(bot.position[2], z)
                self.assertEqual(bot.velocity, (0, 0, 0))

    def test_actual_camo_is_hit_and_ends_on_hook_contact(self):
        f = self.arena()
        f.press(8)
        f.press(0, 65)
        camo = f.camo().state(13)
        self.assertTrue(camo.flags & nodes.Ability.RUNNING)
        flight = self.launch(f, f.b, (0, 1, 0))
        self.assertIs(self.catch(f, flight).owner, f.a)
        self.assertEqual(f.health(f.a), 170)
        self.assertFalse(camo.flags & nodes.Ability.RUNNING)
        self.assertGreater(camo.remaining(), 0)

    def test_projected_barrier_blocks_contact_or_breaks_existing_pull(self):
        for late in (False, True):
            with self.subTest(late=late):
                f = self.arena()
                fight = f.run.match.combat
                scene = grenade_fixture.SombraGrenadeTests()
                zarya = scene.join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
                if late:
                    flight = self.launch(f, f.b, (0, 1, 0))
                    self.assertIs(self.catch(f, flight).owner, f.a)
                    self.assertTrue(fight.is_stunned(f.a))
                z = f.a.mover.position[2] + (flight.pull_speed * combat.TICK if late else 0)
                zarya_aim = aim(fight._eye(zarya), (0, 1, z))
                f.run.step({zarya: (*zarya_aim, 16)})
                bubble, = fight.barriers.values()
                self.assertIs(bubble.protected, f.a)
                if not late:
                    self.launch(f, f.b, (0, 1, 0))
                f.press(0, 25)
                self.assertFalse(fight.hooks.flights)
                self.assertFalse(fight.is_stunned(f.a))
                self.assertEqual(bubble.vitals.total, 200 if late else 170)
                self.assertEqual(f.health(f.a), 170 if late else 200)

    def test_actual_emp_on_caster_releases_other_sombra_and_refuses_fresh_hook(self):
        f = self.arena(distance=17)
        fight = f.run.match.combat
        scene = grenade_fixture.SombraGrenadeTests()
        rescuer = scene.join_ally(f, f.a.hero.guid, (4, 0, 10))
        rescuer.body_script.set_ult_charge(1400)
        flight = self.launch(f, f.b, (0, 1, 0))
        self.assertIs(self.catch(f, flight).owner, f.a)
        f.run.step({rescuer: (0, 0, 32)})
        for _ in range(30):
            if f.b.body in fight.hacks:
                break
            f.run.step({})
        effect, = fight.hacks[f.b.body]
        self.assertEqual(effect.application.source_entity(), rescuer.body)
        self.assertLess(f.run.frame - flight.caught_at, math.ceil(flight.limit / combat.TICK))
        self.assertFalse(fight.hooks.flights)
        self.assertFalse(fight.is_stunned(f.a))
        self.assertEqual(f.a.mover.velocity, (0, 0, 0))
        f.press(8, shooter=f.b, target=f.a)
        f.press(0, 20)
        self.assertFalse(fight.hooks.flights)
        self.assertEqual(f.health(f.a), 170)

    def test_held_recall_is_denied_during_hook_then_fresh_input_consumes_beacon(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        f = self.arena()
        fight = f.run.match.combat
        f.floor = [(-100, 0, -10, -100, 0, 100, 100, 0, 100),
                   (-100, 0, -10, 100, 0, 100, 100, 0, -10)]
        f.geometry()
        for _ in range(20):
            f.run.step({f.a: (16384, 0, 16)})
        f.press(0, 180)
        beacon, = fight.translocators.entries.values()
        self.assertTrue(beacon.settled)
        flight = self.launch(f, f.b, (0, 1, 0))
        self.assertIs(self.catch(f, flight).owner, f.a)
        f.press(16, 6)
        self.assertIn(beacon.entity, fight.translocators.entries)
        f.press(0, 70)
        self.assertFalse(fight.hooks.flights)
        self.assertIn(beacon.entity, fight.translocators.entries)
        f.press(16)
        f.press(0, 20)
        self.assertNotIn(beacon.entity, fight.translocators.entries)
        self.assertAlmostEqual(f.a.mover.position[0], beacon.position[0], delta=0.15)

    def test_lethal_hook_never_pulls_and_caster_death_releases_active_pull(self):
        for victim_dies in (False, True):
            with self.subTest(victim_dies=victim_dies):
                f = self.arena()
                fight = f.run.match.combat
                scene = grenade_fixture.SombraGrenadeTests()
                soldier = scene.join_ally(f, SOLDIER, (5, 0, 8))
                target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a if victim_dies else f.b)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = 20
                flight = self.launch(f, f.b, (0, 1, 0))
                if victim_dies:
                    f.press(0, 25)
                    self.assertIsNone(flight.caught_at)
                    self.assertEqual(vitals.killer, f.b.body)
                    self.assertEqual(f.a.mover.position[2], 0)
                else:
                    self.assertIs(self.catch(f, flight).owner, f.a)
                    f.press(FIRE, shooter=soldier, target=f.b)
                    self.assertEqual(vitals.killer, soldier.body)
                    self.assertEqual(f.health(f.a), 170)
                self.assertTrue(vitals.dead)
                self.assertFalse(fight.hooks.flights)
                self.assertFalse(fight.is_stunned(f.a))

    def test_active_hook_retires_on_late_cover_or_either_body_replacement(self):
        for reason in ("wall", "source", "target"):
            with self.subTest(reason=reason):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                flight = self.launch(f, f.b, (0, 1, 0))
                self.assertIs(self.catch(f, flight).owner, f.a)
                self.assertTrue(fight.is_stunned(f.a))
                if reason == "wall":
                    feet = tuple(f.a.mover.position)
                    f.geometry((-3, 0, 4, 3, 0, 4, 3, 4, 4),
                               (-3, 0, 4, 3, 4, 4, -3, 4, 4))
                    f.press(0)
                    self.assertEqual(tuple(f.a.mover.position), feet)
                else:
                    player = f.b if reason == "source" else f.a
                    old = player.body
                    self.assertTrue(match.switch_hero(player, player.hero))
                    self.assertNotEqual(player.body, old)
                self.assertFalse(fight.hooks.flights)
                self.assertFalse(fight.is_stunned(f.a))
                f.press(0, 25)
                self.assertEqual(f.health(f.a), 200 if reason == "target" else 170)

    def test_live_hook_snapshot_distinguishes_recovery_clock(self):
        f = self.arena()
        match = f.run.match
        flight = self.launch(f, f.b, (0, 1, 0))
        self.assertIs(self.catch(f, flight).owner, f.a)

        def digest():
            stored = _Graph({"match": match, "fight": match.combat, "source": f.b, "target": f.a}, None)
            roots = {
                "hooks": stored.value(match.combat.hooks),
                "victim": stored.value(f.a.body_script.component),
            }
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": stored.finish(), "assets": [],
            })

        before = digest()
        self.assertEqual(digest(), before)
        flight.recovery += combat.TICK
        self.assertNotEqual(digest(), before)

    def test_actual_front_reinhardt_barrier_takes_hook_instead_of_sombra(self):
        f = self.arena()
        fight = f.run.match.combat
        scene = grenade_fixture.SombraGrenadeTests()
        rein = scene.join_ally(f, reinhardt_barrier.HERO, (0, 0, 3))
        held = {rein: (0, 0, 2)}
        for _ in range(40):
            f.run.step(held)
        barrier, = fight.barriers.values()
        self.assertEqual(barrier.vitals.total, 1600)
        self.launch(f, f.b, (0, 1, 0), held=held)
        for _ in range(30):
            f.run.step(held)
        self.assertFalse(fight.hooks.flights)
        self.assertEqual(barrier.vitals.total, 1570)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(f.health(rein), 500)

    def test_actual_wraith_and_fade_allow_hook_to_reach_sombra_behind(self):
        for hero in (grenade_fixture.REAPER, grenade_fixture.MOIRA):
            for phased in (False, True):
                with self.subTest(hero=hero, phased=phased):
                    f = self.arena()
                    scene = grenade_fixture.SombraGrenadeTests()
                    defender = scene.join_ally(f, hero, (0, 0, 3))
                    before = f.health(defender)
                    if phased:
                        f.run.step({defender: (0, 0, 8)})
                        self.assertTrue(phase_defenses.active(defender))
                    flight = self.launch(f, f.b, (0, 1, 0))
                    self.assertIs(self.catch(f, flight).owner, f.a if phased else defender)
                    self.assertEqual(f.health(f.a), 170 if phased else 200)
                    self.assertEqual(f.health(defender), before if phased else before - 30)

    def test_hook_bypasses_actual_genji_deflect_and_sigma_grasp(self):
        for hero, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            with self.subTest(hero=hero):
                f = self.arena()
                fight = f.run.match.combat
                scene = grenade_fixture.SombraGrenadeTests()
                defender = scene.join_ally(f, hero, (0, 0, 3))
                before = f.health(defender)
                f.run.step({defender: (0, 0, button)})
                self.assertIsNotNone(shot_guards.active_guard(defender, fight.tick * combat.TICK * 1000))
                flight = self.launch(f, f.b, (0, 1, 0))
                self.assertIs(self.catch(f, flight).owner, defender)
                self.assertEqual(f.health(defender), before - 30)
                self.assertEqual(f.health(f.a), 200)

    def test_hook_interrupts_held_hack_with_authored_damage_cooldown(self):
        f = self.arena()
        fight = f.run.match.combat
        scene = grenade_fixture.SombraGrenadeTests()
        target = scene.join_ally(f, SOLDIER, (5, 0, 0))
        target.team = f.b.team
        angles = aim(fight._eye(f.a), (5, 1, 0))
        held = {f.a: (*angles, 2)}
        for _ in range(10):
            f.run.step(held)
        hack = f.weapon().state(53)
        self.assertTrue(hack.flags & nodes.Ability.RUNNING)
        self.assertEqual(f.weapon().vars[8909].value().id, target.body)
        flight = self.launch(f, f.b, (0, 1, 0), held=held)
        for _ in range(20):
            if flight.target is not None:
                break
            f.run.step(held)
        self.assertIs(flight.target.owner, f.a)
        self.assertEqual(f.health(f.a), 170)
        self.assertFalse(hack.flags & nodes.Ability.RUNNING)
        self.assertAlmostEqual(hack.remaining(), 2, delta=0.04)
        self.assertNotIn(target.body, fight.hacks)

    def test_actual_hook_prevents_paid_sombra_emp_pulse_without_refund(self):
        f = self.arena()
        fight = f.run.match.combat
        f.a.body_script.set_ult_charge(1400)
        emp = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0B31)
        pulse = emp.node_state(emp.graph.node(40))
        flight = self.launch(f, f.b, (0, 1, 0))
        f.run.step({f.a: (0, 0, 32)})
        self.assertTrue(emp.state(1).flags & nodes.Ability.RUNNING)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        self.assertIs(self.catch(f, flight).owner, f.a)
        self.assertFalse(emp.state(1).flags & nodes.Ability.RUNNING)
        f.press(0, 50)
        self.assertEqual(pulse.counter, 0)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        self.assertNotIn(f.b.body, fight.hacks)

    def test_hook_cooldown_refuses_fresh_press_until_authored_recovery(self):
        f = self.arena()
        fight = f.run.match.combat
        flight = self.launch(f, f.b, (0, 1, 0))
        self.assertIs(self.catch(f, flight).owner, f.a)
        f.press(0, 45)
        ability = next(s for s in flight.instance.states.values() if s.node.pos == 11)
        self.assertGreater(ability.remaining(), 0)
        f.press(8, shooter=f.b, target=f.a)
        f.press(0, 20)
        self.assertFalse(fight.hooks.flights)
        self.assertEqual(f.health(f.a), 170)
        f.press(0, math.ceil(ability.remaining() / combat.TICK) + 1)
        x, y, z = f.a.mover.position
        fresh = self.launch(f, f.b, (x, y + 1, z))
        self.assertIsNot(flight, fresh)
        self.assertIs(self.catch(f, fresh).owner, f.a)
        self.assertEqual(f.health(f.a), 140)

    def test_ally_hook_releases_actual_assassin_pin_and_heavy_carry(self):
        for key in ("4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=1.5)
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = f.b.team = 0
                f.b.spawned = True
                self.assertTrue(match.switch_hero(f.b, heroes()[hook.HERO]))
                f.b.mover.position[:] = (3, 0, -2)
                f.b.mover.body = None
                f.press(0, 50)
                if key == "4":
                    assassin_fixture.SombraAssassinTests().engage(f, bot)
                else:
                    bot.yaw = -32768
                    bot.body_script.component.edges([(35, True)])
                    f.press(0, 62)
                self.assertEqual(fight.pins.get(bot.entity), f.a.body)
                self.assertTrue(fight.is_stunned(f.a))
                before = f.health(f.a)
                flight = None
                for frame in range(22):
                    x, y, z = bot.position
                    vx, _, vz = bot.velocity
                    angles = aim(fight._eye(f.b), (x + vx * 0.065, y + 1, z + vz * 0.065))
                    f.run.step({f.b: (*angles, 8 if frame == 0 else 0)})
                    flight = fight.hooks.flights.get(f.b.body)
                    if flight is not None:
                        break
                self.assertIsNotNone(flight)
                self.assertIs(self.catch(f, flight).owner, bot)
                self.assertTrue(fight.is_stunned(bot))
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(fight.is_stunned(f.a))
                f.press(0, 3)
                f.press(8)
                camo = f.camo().state(13)
                self.assertTrue(camo.flags & nodes.Ability.RUNNING)
                self.assertEqual(f.health(f.a), before)
                f.press(0, 60)
                if key == "4":
                    self.assertEqual(fight.pins.get(bot.entity), f.a.body)
                    self.assertEqual(f.health(f.a), before - 25)
                    self.assertFalse(camo.flags & nodes.Ability.RUNNING)
                else:
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(f.health(f.a), before)
                    self.assertTrue(camo.flags & nodes.Ability.RUNNING)


class RoadhogHookOwnedMechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_fixture.SombraMatrixTests.setUpClass()

    def test_owned_mech_hook_bypasses_matrix_and_keeps_displacement_after_input_resumes(self):
        f, _ = matrix_fixture.SombraMatrixTests().pilot_arena()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        pilot = f.b.body_script
        pilot.set_ult_charge(pilot.component.entity_var(338).value())
        f.run.step({f.b: (0, 0, 32)})
        f.press(0, 120)
        mech = dva.controlled_mech(f.b)
        self.assertIsNotNone(mech)
        x, y, z = mech.position
        scene = grenade_fixture.SombraGrenadeTests()
        hog = scene.join_ally(f, hook.HERO, (x, y, z - 6))
        held = {f.b: (32768, 0, 2)}
        f.run.step(held)
        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
        fixture = SombraRoadhogHookTests()
        flight = fixture.launch(f, hog, (x, y + 1, z), held=held)
        for _ in range(20):
            if flight.target is not None:
                break
            f.run.step(held)
        self.assertIs(flight.target.owner, mech)
        self.assertEqual(fight.vitals[mech.entity].total, 575)
        self.assertTrue(fight.is_stunned(mech))
        self.assertIsNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
        for _ in range(3):
            f.run.step(held)
        self.assertLess(mech.position[2], z - 1)
        f.run.frame += 90
        match.update(f.run.frame * combat.TICK, f.run.frame)
        self.assertFalse(fight.is_stunned(mech))
        end = mech.position
        self.assertLess(end[2], z - 2)
        f.run.step({})
        self.assertAlmostEqual(mech.position[2], end[2], delta=0.05)
        self.assertEqual(f.health(f.a), 200)
