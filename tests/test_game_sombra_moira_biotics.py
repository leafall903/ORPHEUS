"""Actual Moira primary spray and both Biotic Orb releases."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_grenade as grenades
import test_game_sombra_matrix as matrix_fixture
import test_game_sombra_moira_beams as beams
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge, jump

from ow174.game import combat, dva, moira, moira_biotics, phase_defenses, reinhardt_barrier, shot_guards
from ow174.game.content import SOLDIER
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_COST
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL


def arena(ally=False, distance=5):
    fixture = beams.SombraMoiraBeamsTests().arena(distance=distance)
    set_teams(fixture, ally)
    return fixture


def set_teams(fixture, ally):
    match = fixture.run.match
    match.game_map = replace(match.game_map, free_for_all=False)
    fixture.a.team, fixture.b.team = 0, 0 if ally else 1


def root(fixture):
    return next(i for i in fixture.b.body_script.component.instances.values()
                if i.graph.index == moira_biotics.ORB_GRAPH)


def aim_at(fixture, point=(0, 1, 0)):
    return aim(fixture.run.match.combat._eye(fixture.b), point)


def step(fixture, angle, button=0, count=1):
    for _ in range(count):
        fixture.run.step({fixture.b: (*angle, button)})


def single_spray(test, fixture, angle):
    fight = fixture.run.match.combat
    for _ in range(20):
        step(fixture, angle, 1)
        if fight.rockets:
            break
    rocket, = fight.rockets
    test.assertEqual(rocket.spray_width, .75)
    test.assertEqual(rocket.direct.amount, 3.5)
    return rocket


def launch(test, fixture, *, healing=False, angle=None):
    fight = fixture.run.match.combat
    angle = aim_at(fixture) if angle is None else angle
    old = {id(orb) for orb in fight.biotics.orbs}
    step(fixture, angle, 16)
    for _ in range(40):
        step(fixture, angle, 1 if healing else 2)
        for orb in fight.biotics.orbs:
            if id(orb) not in old:
                test.assertEqual(orb.healing, healing)
                return orb
    test.fail("actual orb input did not emit an orb")


class MoiraSprayTests(unittest.TestCase):
    def test_single_actual_spray_restores_3_5_then_35_and_charges_effective_health(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                fixture = arena(ally=True)
                fight = fixture.run.match.combat
                fixture.health(fixture.a)
                fight.vitals[fixture.a.body].damage(100)
                angle = aim_at(fixture)
                rocket = single_spray(self, fixture, angle)
                if gap:
                    jump(fixture, fixture.run.frame + 190)
                else:
                    while fixture.a.body not in fight.biotics.tails and rocket in fight.rockets:
                        step(fixture, angle)
                    tail = fight.biotics.tails[fixture.a.body]
                    self.assertAlmostEqual(fixture.health(fixture.a), 103.5)
                    deadline = math.ceil(tail.start / 16) - 1
                    step(fixture, angle, count=deadline - fixture.run.frame)
                    self.assertAlmostEqual(fixture.health(fixture.a), 103.5)
                    step(fixture, angle, count=math.ceil(tail.end / 16) + 2 - fixture.run.frame)
                self.assertAlmostEqual(fixture.health(fixture.a), 138.5, places=5)
                self.assertAlmostEqual(charge(fixture.b), 38.5, places=3)
                self.assertFalse(fight.biotics.tails)
                self.assertFalse(fixture.b.client.messages(combat.HIT))

    def test_actual_spray_pierces_enemy_and_heals_two_allies_once_each(self):
        fixture = arena(ally=True, distance=7)
        fight = fixture.run.match.combat
        join = grenades.SombraGrenadeTests().join_ally
        friendly = join(fixture, SOLDIER, (.5, 0, 2))
        hostile = join(fixture, SOLDIER, (0, 0, 4))
        hostile.team = 1
        for player in (fixture.a, friendly, hostile):
            fixture.health(player)
            fight.vitals[player.body].damage(100)
        single_spray(self, fixture, aim_at(fixture))
        jump(fixture, fixture.run.frame + 190)
        self.assertAlmostEqual(fixture.health(fixture.a), 138.5, places=5)
        self.assertAlmostEqual(fixture.health(friendly), 138.5, places=5)
        self.assertEqual(fixture.health(hostile), 100)
        self.assertAlmostEqual(charge(fixture.b), 77, places=3)

    def test_wall_range_and_enemy_block_healing_and_charge(self):
        for gate in ("cover", "range", "enemy"):
            with self.subTest(gate=gate):
                fixture = arena(ally=gate != "enemy", distance=25 if gate == "range" else 5)
                fight = fixture.run.match.combat
                fixture.health(fixture.a)
                fight.vitals[fixture.a.body].damage(100)
                if gate == "cover":
                    fixture.geometry((-3, 0, 2, 3, 0, 2, 3, 4, 2),
                                     (-3, 0, 2, 3, 4, 2, -3, 4, 2))
                single_spray(self, fixture, aim_at(fixture))
                jump(fixture, fixture.run.frame + 180)
                self.assertEqual(fixture.health(fixture.a), 100)
                self.assertEqual(charge(fixture.b), 0)

    def test_fuel_gates_empty_primary_and_original_recovery_allows_fresh_spray(self):
        fixture = arena(ally=True)
        fight = fixture.run.match.combat
        weapon = beams.weapon(fixture)
        weapon.write((0, 87), 0.)
        angle = aim_at(fixture)
        step(fixture, angle, 1, 20)
        self.assertFalse(fight.rockets)
        self.assertLess(weapon.vars[87].value(), 5)
        step(fixture, angle, count=215)
        self.assertGreaterEqual(weapon.vars[87].value(), 5)
        single_spray(self, fixture, angle)

    def test_actual_ana_antiheal_denies_spray_and_tail_then_allows_fresh_healing(self):
        fixture = arena(ally=True)
        fight = fixture.run.match.combat
        helper = grenades.SombraGrenadeTests()
        ana = helper.join_ally(fixture, 0x02E000000000013B, (3, 0, 0))
        ana.team = 1
        helper.throw(fixture.run, ana, fixture.a)
        vitals = fight.vitals[fixture.a.body]
        self.assertGreater(vitals.healing_block_until, fixture.run.frame)
        before = vitals.total
        single_spray(self, fixture, aim_at(fixture))
        jump(fixture, vitals.healing_block_until)
        self.assertEqual(vitals.total, before)
        self.assertEqual(charge(fixture.b), 0)
        single_spray(self, fixture, aim_at(fixture))
        jump(fixture, fixture.run.frame + 185)
        self.assertAlmostEqual(vitals.total - before, 38.5, places=4)


class MoiraOrbTests(unittest.TestCase):
    def test_actual_orb_finite_shared_budgets_and_world_only_equivalence(self):
        for healing in (False, True):
            for gap in (False, True):
                with self.subTest(healing=healing, gap=gap):
                    fixture = arena(ally=healing)
                    fight = fixture.run.match.combat
                    fixture.health(fixture.a)
                    if healing:
                        fight.vitals[fixture.a.body].damage(100)
                    fixture.b.body_script.component.set(12618 if healing else 12619, .25)
                    orb = launch(self, fixture, healing=healing)
                    self.assertEqual(orb.energy, 75 if healing else 50)
                    self.assertEqual(orb.speed, 20)
                    self.assertEqual(orb.left, 7)
                    if gap:
                        jump(fixture, fixture.run.frame + 475)
                    else:
                        step(fixture, aim_at(fixture), count=475)
                    self.assertFalse(fight.biotics.orbs)
                    self.assertAlmostEqual(fixture.health(fixture.a), 175 if healing else 150, places=5)
                    self.assertAlmostEqual(charge(fixture.b), 75 if healing else 50, places=3)

    def test_actual_orb_bounces_off_world_and_cannot_tether_through_it(self):
        fixture = arena()
        fight = fixture.run.match.combat
        fixture.geometry((-3, 0, 1.5, 3, 0, 1.5, 3, 4, 1.5),
                         (-3, 0, 1.5, 3, 4, 1.5, -3, 4, 1.5))
        orb = launch(self, fixture)
        jump(fixture, fixture.run.frame + 60)
        self.assertGreater(orb.bounces, 0)
        self.assertEqual(orb.energy, 200)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertIn(orb, fight.biotics.orbs)
        jump(fixture, fixture.run.frame + 450)
        self.assertNotIn(orb, fight.biotics.orbs)

    def test_healing_orb_shares_one_budget_and_self_heal_earns_no_charge(self):
        fixture = arena(ally=True)
        fight = fixture.run.match.combat
        ally = grenades.SombraGrenadeTests().join_ally(fixture, SOLDIER, (0, 0, 2.5))
        for player in (fixture.a, ally, fixture.b):
            fixture.health(player)
            fight.vitals[player.body].damage(100)
        fixture.b.body_script.component.set(12618, .25)
        launch(self, fixture, healing=True)
        jump(fixture, fixture.run.frame + 195)
        healed = [fixture.health(player) - 100 for player in (fixture.a, ally, fixture.b)]
        self.assertTrue(all(given > 0 for given in healed))
        self.assertAlmostEqual(sum(healed), 75, places=4)
        self.assertAlmostEqual(charge(fixture.b), sum(healed[:2]), places=3)

    def test_actual_projected_barrier_spends_damage_orb_budget_without_body_charge(self):
        fixture = arena()
        fight = fixture.run.match.combat
        zarya = grenades.SombraGrenadeTests().join_ally(fixture, reinhardt_barrier.ZARYA, (8, 0, 0))
        angle = aim(fight._eye(zarya), (0, 1, 0))
        fixture.run.step({zarya: (*angle, 16)})
        bubble, = fight.barriers.values()
        self.assertIs(bubble.protected, fixture.a)
        fixture.b.body_script.component.set(12619, .1)
        orb = launch(self, fixture)
        jump(fixture, fixture.run.frame + 55)
        self.assertNotIn(orb, fight.biotics.orbs)
        self.assertAlmostEqual(bubble.vitals.total, 180, places=4)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(charge(fixture.b), 0)

    def test_selection_cancel_and_shared_cooldown_then_alternate_release(self):
        fixture = arena()
        fight = fixture.run.match.combat
        angle = aim_at(fixture)
        ability = root(fixture).state(0)
        step(fixture, angle, 16)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        step(fixture, angle, count=20)
        step(fixture, angle, 16)
        step(fixture, angle, count=15)
        self.assertFalse(fight.biotics.orbs)
        self.assertEqual(ability.remaining(), 0)
        first = launch(self, fixture)
        count = root(fixture).state(11).counter
        step(fixture, angle, count=30)
        self.assertGreater(ability.remaining(), 7)
        for button in (16, 1, 0, 16, 1 | 2, 0):
            step(fixture, angle, button, 3)
        self.assertEqual(root(fixture).state(11).counter, count)
        self.assertEqual(tuple(fight.biotics.orbs), (first,))
        step(fixture, angle, count=520)
        second = launch(self, fixture, healing=True)
        self.assertIsNot(first, second)

    def test_hack_cancels_open_selection_and_emp_does_not_remove_released_orb(self):
        fixture = arena()
        fight = fixture.run.match.combat
        angle = aim_at(fixture)
        step(fixture, angle, 16)
        self.assertTrue(root(fixture).state(0).flags & nodes.Ability.RUNNING)
        for _ in range(80):
            fixture.press(2)
            if fixture.b.body in fight.hacks:
                break
        effect, = fight.hacks[fixture.b.body]
        self.assertFalse(root(fixture).state(0).flags & nodes.Ability.RUNNING)
        step(fixture, angle, 16)
        step(fixture, angle, 2, 30)
        self.assertFalse(fight.biotics.orbs)
        fixture.press(0, effect.until_tick - fixture.run.frame + 1)
        orb = launch(self, fixture)
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(32)
        fixture.press(0, 25)
        self.assertIn(fixture.b.body, fight.hacks)
        self.assertIn(orb, fight.biotics.orbs)
        before = fixture.health(fixture.a)
        fixture.press(0, 12)
        self.assertLess(fixture.health(fixture.a), before)

    def test_actual_genji_deflect_changes_damage_attribution(self):
        fixture = arena()
        fight = fixture.run.match.combat
        guard = grenades.SombraGrenadeTests().join_ally(fixture, shot_guards.GENJI, (0, 0, 2))
        facing = aim(fight._eye(guard), (0, 1, 5))
        fixture.run.step({guard: (*facing, 16)})
        orb = launch(self, fixture)
        source = fixture.b.body
        for _ in range(55):
            fixture.press(0)
            if orb.body != source:
                break
        self.assertIs(orb.shooter, guard)
        self.assertEqual(orb.launch_body, source)
        self.assertEqual(orb.body, guard.body)
        before = fixture.health(fixture.b)
        protected_health = fixture.health(fixture.a)
        fixture.press(0, 12)
        self.assertLess(fixture.health(fixture.b), before)
        self.assertTrue(guard.client.messages(combat.HIT))
        self.assertEqual(fixture.health(fixture.a), protected_health)

    def test_reflected_healing_orb_keeps_operation_and_changes_allies(self):
        fixture = arena()
        fight = fixture.run.match.combat
        guard = grenades.SombraGrenadeTests().join_ally(fixture, shot_guards.GENJI, (0, 0, 2))
        for player in (fixture.a, guard):
            fixture.health(player)
            fight.vitals[player.body].damage(100)
        facing = aim(fight._eye(guard), (0, 1, 5))
        fixture.run.step({guard: (*facing, 16)})
        orb = launch(self, fixture, healing=True)
        for _ in range(55):
            fixture.press(0)
            if orb.shooter is guard:
                break
        self.assertIs(orb.shooter, guard)
        self.assertTrue(orb.healing)
        self.assertEqual(orb.team, fixture.a.team)
        fixture.press(0, 14)
        self.assertGreater(fixture.health(guard), 100)
        self.assertGreater(fixture.health(fixture.a), 100)
        self.assertEqual(fixture.health(fixture.b), 200)
        self.assertEqual(charge(fixture.b), 0)

    def test_damage_orb_can_tether_to_actual_concealed_sombra(self):
        fixture = arena()
        fixture.press(8)
        fixture.press(0, 60)
        camo = fixture.camo().state(13)
        self.assertTrue(camo.flags & camo.RUNNING)
        launch(self, fixture)
        fixture.press(0, 80)
        self.assertLess(fixture.health(fixture.a), 200)
        self.assertFalse(camo.flags & camo.RUNNING)
        self.assertGreater(camo.remaining(), 0)

    def test_actual_wraith_is_not_a_tether_and_does_not_slow_damage_orb(self):
        fixture = arena()
        place(fixture.a, (8, 0, 0))
        reaper = grenades.SombraGrenadeTests().join_ally(fixture, 0x02E0000000000002, (0, 0, 1.5))
        fixture.run.step({reaper: (0, 0, 8)})
        self.assertTrue(phase_defenses.active(reaper))
        orb = launch(self, fixture, angle=aim_at(fixture, (0, 1, 1.5)))
        origin = orb.position
        fixture.press(0, 10)
        self.assertTrue(phase_defenses.active(reaper))
        self.assertEqual(orb.energy, 200)
        self.assertEqual(fixture.health(reaper), 250)
        self.assertAlmostEqual(math.dist(orb.position, origin), 3.2, places=5)

    def test_orb_preserves_release_faction_after_caster_switches_team(self):
        fixture = arena()
        orb = launch(self, fixture)
        self.assertNotEqual(orb.team, fixture.a.team)
        fixture.b.team = fixture.a.team
        fixture.press(0, 34)
        self.assertLess(fixture.health(fixture.a), 200)
        self.assertLess(orb.energy, 200)
        self.assertEqual(charge(fixture.b), 0)

    def test_actual_sigma_absorbs_damage_orb_and_credits_nominal_100(self):
        fixture = arena()
        fight = fixture.run.match.combat
        guard = grenades.SombraGrenadeTests().join_ally(fixture, shot_guards.SIGMA, (0, 0, 3.5))
        facing = aim(fight._eye(guard), (0, 1, 5))
        fixture.run.step({guard: (*facing, 8)})
        fixture.press(0, 4)
        state = shot_guards.active_guard(guard, fight.tick * 16)
        self.assertIsNotNone(state)
        orb = launch(self, fixture)
        fixture.press(0, 28)
        self.assertNotIn(orb, fight.biotics.orbs)
        self.assertEqual(state.instance.vars[21312].value(), 100)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(charge(fixture.b), 0)

    def test_damage_orb_survives_source_death_and_snapshot_tracks_budget(self):
        fixture = arena()
        fight = fixture.run.match.combat
        orb = launch(self, fixture)

        def digest():
            graph = _Graph({"combat": fight, "caster": fixture.b, "target_client": fixture.a.client,
                            "caster_client": fixture.b.client}, None)
            roots = {"biotic": graph.value(fight.biotics)}
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": roots, "nodes": graph.finish(), "assets": []})

        before = digest()
        orb.energy -= 1
        self.assertNotEqual(before, digest())
        fixture.health(fixture.b)
        fight.vitals[fixture.b.body].now[HEALTH_POOL] = 8
        fixture.press(1)
        self.assertTrue(fight.is_dead(fixture.b))
        self.assertIn(orb, fight.biotics.orbs)
        before = fixture.health(fixture.a)
        fixture.press(0, 20)
        self.assertLess(fixture.health(fixture.a), before)
        self.assertEqual(charge(fixture.b), 0)


class NpcMoiraBioticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_actual_spray_and_both_orbs_affect_all_five_npc_classes(self):
        for key, definition in self.catalog.items():
            for ability in ("spray", "heal", "damage"):
                with self.subTest(npc=definition.name, ability=ability):
                    fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=4)
                    beams.prepare(fixture)
                    set_teams(fixture, ally=ability != "damage")
                    bot.team = 0
                    place(fixture.a, (7, 0, 0))
                    bot.position = (0, 0, 1)
                    fight = fixture.run.match.combat
                    vitals = fight.vitals[bot.entity]
                    if ability != "damage":
                        vitals.damage(100)
                    before = vitals.total
                    angle = aim_at(fixture, (0, 1, 1))
                    if ability == "spray":
                        single_spray(self, fixture, angle)
                        jump(fixture, fixture.run.frame + 170)
                        self.assertAlmostEqual(vitals.total - before, 38.5, places=4)
                    else:
                        orb = launch(self, fixture, healing=ability == "heal", angle=angle)
                        jump(fixture, fixture.run.frame + 35)
                        self.assertLess(orb.energy, 300 if ability == "heal" else 200)
                        self.assertTrue(vitals.total > before if ability == "heal" else vitals.total < before)
                    self.assertAlmostEqual(charge(fixture.b), abs(vitals.total - before), places=3)

    def test_damage_orb_spends_exact_fractional_budget_after_heavy_armour(self):
        fixture, bot = npc_fixture.SombraActualNpcTests.make(self, "5", distance=4)
        beams.prepare(fixture)
        set_teams(fixture, ally=False)
        bot.team = 0
        bot.position = (0, 0, 1)
        place(fixture.a, (8, 0, 0))
        fight = fixture.run.match.combat
        vitals = fight.vitals[bot.entity]
        fixture.b.body_script.component.set(12619, .001)
        orb = launch(self, fixture, angle=aim_at(fixture, (0, 1, 1)))
        self.assertAlmostEqual(orb.energy, .2)
        jump(fixture, fixture.run.frame + 30)
        self.assertNotIn(orb, fight.biotics.orbs)
        self.assertAlmostEqual(vitals.total, 3999.8, places=4)
        self.assertAlmostEqual(charge(fixture.b), .2, places=4)


class OwnedMatrixMoiraBioticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_fixture.SombraMatrixTests.setUpClass()

    def test_both_actual_orbs_are_consumed_by_owned_matrix(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                fixture, _ = matrix_fixture.SombraMatrixTests().pilot_arena()
                match = fixture.run.match
                fight = match.combat
                set_teams(fixture, False)
                pilot = fixture.b
                pilot.body_script.set_ult_charge(pilot.body_script.component.entity_var(ULT_COST).value())
                fixture.run.step({pilot: (0, 0, 32)})
                fixture.press(0, 120)
                mech = dva.controlled_mech(pilot)
                self.assertIsNotNone(mech)
                x, y, z = mech.position
                caster = grenades.SombraGrenadeTests().join_ally(fixture, moira.HERO, (x, y, z - 3))
                place(fixture.a, (x, y, z + 5))
                caster.body_script.ult_seconds = 0
                caster.body_script.set_ult_charge(0)
                facing = aim(fight._eye(caster), (x, y + 1, z))
                for _ in range(5):
                    fixture.run.step({pilot: (32768, 0, 2)})
                self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * 16))
                before = fight.vitals[mech.entity].total
                fixture.run.step({pilot: (32768, 0, 2), caster: (*facing, 16)})
                orb = None
                for _ in range(25):
                    button = (1 if healing else 2) if orb is None else 0
                    fixture.run.step({pilot: (32768, 0, 2), caster: (*facing, button)})
                    if fight.biotics.orbs:
                        orb = fight.biotics.orbs[0]
                    if orb is not None and orb not in fight.biotics.orbs:
                        break
                self.assertIsNotNone(orb)
                self.assertNotIn(orb, fight.biotics.orbs)
                self.assertEqual(fight.vitals[mech.entity].total, before)
                self.assertEqual(charge(caster), 0)
