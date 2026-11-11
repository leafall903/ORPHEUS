"""Actual Ana Biotic Rifle volleys against Sombra and the five NPC bodies."""

import unittest
from dataclasses import replace

import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_matrix as matrix_fixture
import test_game_sombra_npc_projected as projected_fixture
from test_game_combat import SOLDIER, aim

from ow174.game import combat, dva, npc, phase_defenses, reinhardt_barrier, shot_guards, translocators
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_COST
from ow174.game.world import HEALTH_POOL, OP_DESTROY


class SombraAnaRifleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        projected_fixture.SombraNpcProjectedTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        projected_fixture.SombraNpcProjectedTests.restore_graphs(path)

    @staticmethod
    def shoot(run, ana, target, *, scoped=False, wait=8, held=()):
        x, y, z = target.feet
        angles = aim(run.match.combat._eye(ana), (x, y + 1, z))
        scope = 2 if scoped else 0
        commands = dict(held)

        def step(buttons):
            commands[ana] = (*angles, buttons)
            run.step(commands)

        if scoped:
            for _ in range(35):
                step(scope)
        step(scope | FIRE)
        for _ in range(wait):
            if run.match.combat.ana_rifle_doses:
                break
            step(scope)

    @staticmethod
    def target(fight, owner):
        return next(target for target in fight._targets() if target.owner is owner)

    def test_actual_hip_and_scoped_damage_and_healing_finish_after_38_world_ticks(self):
        for ally in (False, True):
            for scoped in (False, True):
                with self.subTest(ally=ally, scoped=scoped):
                    f = grenade_fixture.SombraGrenadeTests().arena(ally=ally)
                    fight = f.run.match.combat
                    target = self.target(fight, f.a)
                    vitals = fight.vitals_of(target)
                    before = vitals.now[HEALTH_POOL] = 100 if ally else 200
                    self.shoot(f.run, f.b, target, scoped=scoped)
                    dose, = fight.ana_rifle_doses
                    self.assertEqual((dose.source, dose.target, dose.healing), (f.b.body, f.a.body, ally))
                    self.assertEqual(vitals.total, before)
                    f.press(0, 12)
                    self.assertAlmostEqual(abs(vitals.total - before), 22.4, places=3)
                    f.press(0, 25)
                    self.assertTrue(fight.ana_rifle_doses)
                    f.press(0, 1)
                    self.assertEqual(vitals.total, before + (70 if ally else -70))
                    self.assertEqual(fight.ana_rifle_doses, [])

    def test_omitted_world_frames_preserve_dose_death_tick_and_original_ana_body(self):
        for catchup in (False, True):
            with self.subTest(catchup=catchup):
                f = grenade_fixture.SombraGrenadeTests().arena()
                match = f.run.match
                fight = match.combat
                target = self.target(fight, f.a)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = 20
                self.shoot(f.run, f.b, target)
                dose, = fight.ana_rifle_doses
                self.assertTrue(match.switch_hero(f.b, heroes()[SOLDIER]))
                self.assertNotEqual(f.b.body, dose.source)
                if catchup:
                    tick = f.run.frame + 100
                    match.update(tick * combat.TICK, tick)
                else:
                    f.press(0, 100)
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.death_tick, dose.start_tick + 11)
                self.assertEqual(vitals.killer, dose.source)
                self.assertEqual(fight.ana_rifle_doses, [])

    def test_early_projected_shield_absorbs_round_and_late_shield_cleanses_existing_dose(self):
        for late in (False, True):
            with self.subTest(late=late):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena()
                fight = f.run.match.combat
                zarya = scene.join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
                target = self.target(fight, f.a)
                vitals = fight.vitals_of(target)
                angles = aim(fight._eye(zarya), (0, 1, 0))
                if late:
                    self.shoot(f.run, f.b, target)
                    self.assertEqual(len(fight.ana_rifle_doses), 1)
                f.run.step({zarya: (*angles, 16)})
                shield, = fight.barriers.values()
                self.assertIs(shield.protected, f.a)
                if not late:
                    self.shoot(f.run, f.b, target)
                self.assertEqual(fight.ana_rifle_doses, [])
                after = vitals.total
                self.assertEqual(shield.vitals.total, 200 if late else 130)
                if late:
                    self.assertLess(after, 200)
                else:
                    self.assertEqual(after, 200)
                f.press(0, 45)
                self.assertEqual(vitals.total, after)

    def test_actual_round_doses_each_npc_and_explicit_retirement_clears_target(self):
        fixture = projected_fixture.SombraNpcProjectedTests
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = fixture.arena(self, key)
                match = f.run.match
                fight = match.combat
                ana, _ = fixture.add_attacker(self, f, bot, grenade_fixture.ANA)
                target = self.target(fight, bot)
                vitals = fight.vitals_of(target)
                before = vitals.total
                self.shoot(f.run, ana, target)
                dose, = fight.ana_rifle_doses
                self.assertEqual((dose.source, dose.target), (ana.body, bot.entity))
                f.press(0, 38)
                self.assertEqual(vitals.total, before - 70)
                self.assertEqual(fight.ana_rifle_doses, [])
                f.press(0, 32)
                self.shoot(f.run, ana, self.target(fight, bot), scoped=True)
                self.assertEqual(len(fight.ana_rifle_doses), 1)
                npc.retire_body(match, bot)
                self.assertEqual(fight.ana_rifle_doses, [])

    def test_actual_genji_and_sigma_intercept_hip_and_scoped_rounds_before_sombra(self):
        for hero, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            for scoped in (False, True):
                with self.subTest(hero=hero, scoped=scoped):
                    scene = grenade_fixture.SombraGrenadeTests()
                    f = scene.arena()
                    fight = f.run.match.combat
                    defender = scene.join_ally(f, hero, (0, 0, 3))
                    f.run.step({defender: (0, 0, button)})
                    guard = shot_guards.active_guard(defender, fight.tick * combat.TICK * 1000)
                    self.assertIsNotNone(guard)
                    self.shoot(f.run, f.b, self.target(fight, f.a), scoped=scoped)
                    if hero == shot_guards.GENJI:
                        dose, = fight.ana_rifle_doses
                        self.assertEqual((dose.source, dose.target), (defender.body, f.b.body))
                    else:
                        self.assertEqual(guard.instance.vars[21312].value(), 70)
                        self.assertEqual(fight.ana_rifle_doses, [])
                    f.press(0, 38)
                    self.assertEqual(f.health(f.a), 200)
                    self.assertEqual(f.health(f.b), 130 if hero == shot_guards.GENJI else 200)

    def test_actual_grenade_antiheal_and_boost_apply_to_rifle_doses(self):
        for block in (False, True):
            with self.subTest(block=block):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena()
                fight = f.run.match.combat
                healer = scene.join_ally(f, grenade_fixture.ANA, (5, 0, 0))
                scene.throw(f.run, f.b if block else healer, f.a)
                f.press(0, 45)
                target = self.target(fight, f.a)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = 50
                self.shoot(f.run, healer, target)
                dose, = fight.ana_rifle_doses
                self.assertTrue(dose.healing)
                f.press(0, 38)
                self.assertEqual(vitals.total, 50 if block else 155)
                self.assertEqual(fight.ana_rifle_doses, [])

    def test_enemy_dose_interrupts_active_sombra_camo(self):
        f = grenade_fixture.SombraGrenadeTests().arena()
        fight = f.run.match.combat
        f.press(8)
        f.press(0, 65)
        camo = f.camo().state(13)
        self.assertTrue(camo.flags & nodes.Ability.RUNNING)
        self.shoot(f.run, f.b, self.target(fight, f.a))
        self.assertEqual(f.health(f.a), 200)
        self.assertTrue(camo.flags & nodes.Ability.RUNNING)
        f.press(0, 1)
        self.assertLess(f.health(f.a), 200)
        f.press(0, 55)
        self.assertFalse(camo.flags & nodes.Ability.RUNNING)
        self.assertGreater(camo.remaining(), 0)

    def test_enemy_dose_interrupts_held_hack_without_silencing_target(self):
        scene = grenade_fixture.SombraGrenadeTests()
        f = scene.arena()
        fight = f.run.match.combat
        victim = scene.join_ally(f, SOLDIER, (5, 0, 0))
        victim.team = f.b.team
        x, y, z = victim.mover.position
        hack_aim = aim(fight._eye(f.a), (x, y + 1, z))
        rifle_aim = aim(fight._eye(f.b), (0, 1, 0))
        for _ in range(10):
            f.run.step({f.a: (*hack_aim, 2)})
        hack = f.weapon().state(53)
        self.assertTrue(hack.flags & nodes.Ability.RUNNING)
        self.assertEqual(f.weapon().vars[8909].value().id, victim.body)
        f.run.step({f.a: (*hack_aim, 2), f.b: (*rifle_aim, FIRE)})
        for _ in range(8):
            if fight.ana_rifle_doses:
                break
            f.run.step({f.a: (*hack_aim, 2)})
        self.assertTrue(fight.ana_rifle_doses)
        self.assertTrue(hack.flags & nodes.Ability.RUNNING)
        f.run.step({f.a: (*hack_aim, 2)})
        impact = f.run.frame
        self.assertLess(f.health(f.a), 200)
        for _ in range(35):
            if not hack.flags & nodes.Ability.RUNNING:
                break
            f.run.step({f.a: (*hack_aim, 2)})
        self.assertFalse(hack.flags & nodes.Ability.RUNNING)
        self.assertAlmostEqual(hack.remaining() + (f.run.frame - impact) * combat.TICK, 2, delta=0.04)
        self.assertNotIn(victim.body, fight.hacks)

    def test_paid_emp_pulses_after_nonlethal_dose_but_lethal_dose_prevents_pulse(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f = grenade_fixture.SombraGrenadeTests().arena()
                fight = f.run.match.combat
                script = f.a.body_script
                script.set_ult_charge(1400)
                emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
                pulse = emp.node_state(emp.graph.node(40))
                vitals = fight.vitals_of(self.target(fight, f.a))
                vitals.now[HEALTH_POOL] = 2 if lethal else 200
                angles = aim(fight._eye(f.b), (0, 1, 0))
                f.run.step({f.a: (0, 0, 32), f.b: (*angles, FIRE)})
                self.assertTrue(emp.state(1).flags & nodes.Ability.RUNNING)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                f.press(0, 38)
                self.assertEqual(fight.is_dead(f.a), lethal)
                self.assertEqual(pulse.counter, 0 if lethal else 1)
                self.assertEqual(f.b.body in fight.hacks, not lethal)

    def test_both_actual_modes_respect_static_cover_and_authored_range(self):
        for scoped in (False, True):
            for distance, cover in ((8, True), (98, False), (102, False)):
                with self.subTest(scoped=scoped, distance=distance, cover=cover):
                    f = grenade_fixture.SombraGrenadeTests().arena()
                    fight = f.run.match.combat
                    f.floor = [(-10, 0, -10, -10, 0, 120, 10, 0, 120),
                               (-10, 0, -10, 10, 0, 120, 10, 0, -10)]
                    f.b.mover.position[:] = (0, 0, distance)
                    if cover:
                        f.wall()
                    else:
                        f.geometry()
                    f.b.mover.body = None
                    f.press(0, 1)
                    self.shoot(f.run, f.b, self.target(fight, f.a), scoped=scoped, wait=65)
                    self.assertAlmostEqual(f.b.mover.position[2] - f.a.mover.position[2], distance)
                    admitted = not cover and distance < 100
                    self.assertEqual(bool(fight.ana_rifle_doses), admitted)
                    f.press(0, 38)
                    self.assertEqual(f.health(f.a), 130 if admitted else 200)

    def test_full_health_allied_sombra_allows_round_to_reach_enemy_behind_her(self):
        for wounded in (False, True):
            with self.subTest(wounded=wounded):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena(ally=True)
                fight = f.run.match.combat
                enemy = scene.join_ally(f, SOLDIER, (0, 0, -4))
                enemy.team = 1
                vitals = fight.vitals_of(self.target(fight, f.a))
                vitals.now[HEALTH_POOL] = 100 if wounded else 200
                self.shoot(f.run, f.b, self.target(fight, enemy), scoped=True)
                dose, = fight.ana_rifle_doses
                self.assertEqual((dose.target, dose.healing),
                                 (f.a.body, True) if wounded else (enemy.body, False))
                f.press(0, 38)
                self.assertEqual(vitals.total, 170 if wounded else 200)
                self.assertEqual(f.health(enemy), 200 if wounded else 130)

    def test_real_wraith_and_fade_cleanse_hostile_doses_and_leave_ally_healing(self):
        for hero in (grenade_fixture.REAPER, grenade_fixture.MOIRA):
            for ally in (False, True):
                with self.subTest(hero=hero, ally=ally):
                    scene = grenade_fixture.SombraGrenadeTests()
                    f = scene.arena(ally=ally)
                    fight = f.run.match.combat
                    target = scene.join_ally(f, hero, (0, 0, 3))
                    vitals = fight.vitals_of(self.target(fight, target))
                    before = vitals.total
                    if ally:
                        vitals.damage(100)
                    self.shoot(f.run, f.b, self.target(fight, target))
                    dose, = fight.ana_rifle_doses
                    self.assertEqual((dose.target, dose.healing), (target.body, ally))
                    f.run.step({target: (0, 0, 8)})
                    self.assertTrue(phase_defenses.active(target))
                    self.assertEqual(bool(fight.ana_rifle_doses), ally)
                    after = vitals.total
                    f.press(0, 40)
                    if ally:
                        self.assertEqual(vitals.total, before - 30)
                    else:
                        self.assertLess(after, before)
                        self.assertEqual(vitals.total, after)
                    self.assertEqual(f.health(f.a), 200)

    def settled_beacon(self, f):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        f.press(16, 20)
        f.press(0, 180)
        beacon, = f.run.match.combat.translocators.entries.values()
        self.assertTrue(beacon.settled)
        return beacon

    def test_actual_rifle_destroys_settled_beacon_on_contact_without_body_dose(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                f = grenade_fixture.SombraGrenadeTests().arena()
                fight = f.run.match.combat
                beacon = self.settled_beacon(f)
                angles = aim(fight._eye(f.b), beacon.position)
                scope = 2 if scoped else 0
                if scoped:
                    for _ in range(35):
                        f.run.step({f.b: (*angles, scope)})
                f.run.step({f.b: (*angles, scope | FIRE)})
                for _ in range(60):
                    if beacon.entity not in fight.translocators.entries:
                        break
                    f.run.step({f.b: (*angles, scope)})
                self.assertNotIn(beacon.entity, fight.translocators.entries)
                self.assertNotIn(beacon.entity, fight.vitals)
                self.assertFalse(beacon.body_script.component.instances)
                self.assertEqual(fight.ana_rifle_doses, [])
                self.assertFalse(f.b.client.messages(combat.HIT))
                self.assertFalse(f.b.client.messages(combat.KILL_NOTICE))
                self.assertEqual(f.health(f.a), 200)
                destroys = [u for u in f.a.client.entities
                            if u.entity == beacon.entity and u.op == OP_DESTROY]
                self.assertEqual(len(destroys), 1)

    def test_pending_dose_follows_actual_translocator_recall_and_retires_with_body(self):
        for replace_body in (False, True):
            with self.subTest(replace_body=replace_body):
                f = grenade_fixture.SombraGrenadeTests().arena()
                match = f.run.match
                fight = match.combat
                beacon = self.settled_beacon(f)
                self.shoot(f.run, f.b, self.target(fight, f.a))
                dose, = fight.ana_rifle_doses
                self.assertEqual(dose.target, f.a.body)
                if replace_body:
                    self.assertTrue(match.switch_hero(f.a, f.a.hero))
                    self.assertNotEqual(f.a.body, dose.target)
                    self.assertEqual(fight.ana_rifle_doses, [])
                else:
                    f.press(16)
                    for _ in range(24):
                        if beacon.entity not in fight.translocators.entries:
                            break
                        f.press(0)
                    self.assertNotIn(beacon.entity, fight.translocators.entries)
                    self.assertAlmostEqual(f.a.mover.position[2], beacon.position[2], places=2)
                    self.assertEqual(fight.ana_rifle_doses, [dose])
                f.press(0, 38)
                self.assertEqual(f.health(f.a), 200 if replace_body else 130)
                self.assertEqual(fight.ana_rifle_doses, [])


class SombraAnaRifleMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_fixture.SombraMatrixTests.setUpClass()

    def test_called_matrix_blocks_actual_rifle_until_sombra_hacks_or_emps_it(self):
        for button in (0, 2, 32):
            with self.subTest(silence=button):
                f, _ = matrix_fixture.SombraMatrixTests().pilot_arena()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team, f.b.team = 0, 1
                pilot = f.b.body_script
                pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
                f.run.step({f.b: (0, 0, 32)})
                f.press(0, 120)
                mech = dva.controlled_mech(f.b)
                self.assertIsNotNone(mech)
                x, y, z = mech.position
                f.a.mover.position[:] = (x + 2, y, z - 1)
                f.a.mover.body = None
                scene = grenade_fixture.SombraGrenadeTests()
                ana = scene.join_ally(f, grenade_fixture.ANA, (x, y, z - 5))
                held = {f.b: (32768, 0, 2)}
                f.run.step(held)
                self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
                if button:
                    angles = aim(fight._eye(f.a), (x, y + 1, z))
                    if button == 32:
                        f.a.body_script.set_ult_charge(1400)
                    for frame in range(90):
                        attack = button if button == 2 or frame == 0 else 0
                        f.run.step({**held, f.a: (*angles, attack)})
                        if mech.entity in fight.hacks:
                            break
                    effect, = fight.hacks[mech.entity]
                    self.assertEqual(effect.application.source_entity(), f.a.body)
                target = SombraAnaRifleTests.target(fight, mech)
                SombraAnaRifleTests.shoot(f.run, ana, target, held=held)
                self.assertEqual(bool(fight.ana_rifle_doses), bool(button))
                f.press(0, 38)
                self.assertEqual(fight.vitals[mech.entity].total, 530 if button else 600)
                self.assertEqual(f.health(f.a), 200)
