"""Actual Sombra casts and Ana combat inputs through the body receiver path."""

import os
import unittest

import test_game_ana as fixture
import test_game_ana_nano as nano_fixture
import test_game_ana_orisa_barrier as placement
import test_game_ana_projected_barrier as projected_fixture
import test_game_ana_sound_barrier as sound_fixture
from test_game_combat import aim

from ow174.game import moira, sombra_control, sound_barrier
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL


class AnaSombraControlTests(unittest.TestCase):
    def arena(self, *, distance=4, enemy=True):
        run, ana, sombra, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy, distance=distance)
        self.assertTrue(run.match.switch_hero(sombra, heroes()[sombra_control.HERO]))
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(sombra, (x, y, z + distance))
        for _ in range(32):
            run.step({})
        return run, ana, sombra

    @staticmethod
    def direction(run, actor, target):
        return aim(run.match.combat._eye(actor), run.match.combat._eye(target))

    @staticmethod
    def vitals(run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is actor))

    @staticmethod
    def charge(actor):
        script = actor.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())

    def cast(self, run, target, sombra, button):
        direction = self.direction(run, sombra, target)
        if button == 32:
            self.charge(sombra)
        fight = run.match.combat
        for frame in range(90):
            run.step({sombra: (*direction, button if button == 2 or frame == 0 else 0)})
            if target.body in fight.sombra_controls.locks:
                return frame
        self.fail("authored receiver never locked the target")

    def test_actual_hack_and_emp_block_dart_and_grenade_but_allow_rifle(self):
        for cast_button in (2, 32):
            with self.subTest(cast=cast_button):
                run, ana, sombra = self.arena()
                fight = run.match.combat
                self.cast(run, ana, sombra, cast_button)
                lock, = fight.sombra_controls.locks[ana.body]
                self.assertEqual(lock.application.source_entity(), sombra.body)
                self.assertEqual(lock.application.graph.index, sombra_control.EFFECT)
                self.assertAlmostEqual(lock.until - fight.now, 5.0)
                self.assertEqual(lock.pulse is not None, cast_button == 32)
                direction = self.direction(run, ana, sombra)
                target = self.vitals(run, sombra)
                for button in (8, 16, 1):
                    run.step({ana: (*direction, button)})
                    for _ in range(65):
                        run.step({})
                    self.assertEqual(target.total, 130 if button == 1 else 200)
                    self.assertFalse(fight.is_asleep(sombra))
                    entry = next(item for item in fight._targets() if item.owner is sombra)
                    self.assertEqual(fight.healing.factor(entry), 1)

    def test_sleep_lockout_ends_on_world_clock_and_held_key_needs_fresh_press(self):
        run, ana, sombra = self.arena()
        self.cast(run, ana, sombra, 32)
        fight = run.match.combat
        direction = self.direction(run, ana, sombra)
        lock, = fight.sombra_controls.locks[ana.body]
        run.step({ana: (*direction, 8)})
        frame = ana.body_script.frame
        while fight.now < lock.until:
            run.frame += 1
            run.match.update(run.frame * .016, run.frame, frames=False)
        self.assertEqual(ana.body_script.frame, frame)
        self.assertNotIn(ana.body, fight.sombra_controls.locks)
        self.assertNotIn(lock.application.id, ana.body_script.component.instances)
        self.assertNotIn(lock.pulse.id, ana.body_script.component.instances)
        for _ in range(55):
            run.step({ana: (*direction, 8)})
        self.assertFalse(fight.is_asleep(sombra))
        run.step({})
        run.step({ana: (*direction, 8)})
        for _ in range(55):
            run.step({})
        self.assertTrue(fight.is_asleep(sombra))

    def test_hack_and_emp_exclude_allies_static_cover_and_out_of_range_bodies(self):
        for button, scenario in ((2, "ally"), (32, "ally"), (2, "wall"), (32, "wall"), (2, "far"), (32, "far")):
            with self.subTest(button=button, scenario=scenario):
                run, ana, sombra = self.arena(distance=36 if scenario == "far" else 6, enemy=scenario != "ally")
                if scenario == "wall":
                    fixture.set_static_wall(run, ana, 3)
                direction = self.direction(run, sombra, ana)
                if button == 32:
                    self.charge(sombra)
                for index in range(80):
                    run.step({sombra: (*direction, button if button == 2 or index == 0 else 0)})
                self.assertNotIn(ana.body, run.match.combat.sombra_controls.locks)
                self.assertFalse(any(i.graph.index in (sombra_control.PULSE, sombra_control.EFFECT)
                                     for i in ana.body_script.component.instances.values()))

    def test_wall_entering_the_beam_interrupts_running_hack(self):
        run, ana, sombra = self.arena(distance=6)
        facing = self.direction(run, sombra, ana)
        for _ in range(14):
            run.step({sombra: (*facing, 2)})
        instance = next(i for i in sombra.body_script.component.instances.values()
                        if i.graph.index == sombra_control.HACK)
        self.assertTrue(instance.state(instance.graph.node(59).state).active)
        fixture.set_static_wall(run, ana, 3)
        for _ in range(64):
            run.step({sombra: (*facing, 2)})
        self.assertNotIn(ana.body, run.match.combat.sombra_controls.locks)

    def test_actual_sleep_dart_interrupts_running_hack_before_application(self):
        run, ana, sombra = self.arena(distance=3)
        hack_aim = self.direction(run, sombra, ana)
        dart_aim = self.direction(run, ana, sombra)
        run.step({sombra: (*hack_aim, 2), ana: (*dart_aim, 8)})
        for _ in range(85):
            run.step({sombra: (*hack_aim, 2)})
        fight = run.match.combat
        self.assertTrue(fight.is_asleep(sombra))
        self.assertNotIn(ana.body, fight.sombra_controls.locks)

    def test_emp_requires_charge_and_actual_dart_interrupts_after_spend(self):
        run, ana, sombra = self.arena(distance=3)
        fight = run.match.combat
        sombra.body_script.ult_seconds = 0
        sombra.body_script.set_ult_charge(0)
        run.step({sombra: (0, 0, 32)})
        for _ in range(45):
            run.step({})
        self.assertNotIn(ana.body, fight.sombra_controls.locks)
        direction = self.direction(run, ana, sombra)
        self.charge(sombra)
        run.step({ana: (*direction, 8)})
        for _ in range(10):
            run.step({})
        self.assertFalse(fight.is_asleep(sombra))
        run.step({sombra: (0, 0, 32)})
        self.assertEqual(sombra.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(80):
            run.step({})
        self.assertTrue(fight.is_asleep(sombra))
        self.assertNotIn(ana.body, fight.sombra_controls.locks)

    def test_hacked_ana_can_reload_and_melee(self):
        run, ana, sombra = self.arena(distance=1.5)
        self.cast(run, ana, sombra, 2)
        direction = self.direction(run, ana, sombra)
        weapon = next(i for i in ana.body_script.component.instances.values() if i.graph.index == 0x0B94)
        run.step({ana: (*direction, 1)})
        for _ in range(65):
            run.step({})
        self.assertEqual(weapon.find_var(0, 53).value(), 11)
        run.step({ana: (*direction, 0x400)})
        for _ in range(110):
            run.step({})
        self.assertEqual(weapon.find_var(0, 53).value(), 12)
        run.step({ana: (*direction, 0x800)})
        for _ in range(45):
            run.step({})
        self.assertIn(ana.body, run.match.combat.sombra_controls.locks)
        self.assertEqual(self.vitals(run, sombra).total, 100)

    def test_emp_interrupts_paid_nano_before_release(self):
        run, ana, sombra = self.arena()
        impact_frame = self.cast(run, ana, sombra, 32)
        test = nano_fixture.AnaNanoTests()
        run, ana, ally, sombra = test.fight(enemy_hero="Sombra")
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(sombra, (x + 4, y, z))
        fight = run.match.combat
        ally_vitals = self.vitals(run, ally)
        ally_vitals.damage(100)
        self.charge(ana)
        self.charge(sombra)
        target = test.direction(fight, ana, ally)
        run.step({sombra: (0, 0, 32)})
        for _ in range(impact_frame - 2):
            run.step({})
        run.step({ana: (*target, 32)})
        self.assertEqual(ana.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(75):
            run.step({})
        self.assertIn(ana.body, fight.sombra_controls.locks)
        self.assertEqual(ally_vitals.total, 100)
        self.assertLessEqual(ally_vitals.nano_until, fight.now)

    def test_emp_drains_sound_barrier_without_damaging_health_or_crediting_sombra(self):
        test = nano_fixture.AnaNanoTests()
        run, ana, lucio, sombra = test.fight(ally_hero="Lucio", enemy_hero="Sombra")
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(lucio, (x, y, z + 3))
        placement.AnaOrisaBarrierTests.place(sombra, (x + 4, y, z))
        sound = sound_fixture.AnaSoundBarrierTests()
        sound.cast(run, lucio)
        sound.land(run, lucio)
        vitals = self.vitals(run, ana)
        self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now, sound_barrier.CAPACITY)
        sombra.body_script.ult_seconds = 0
        self.cast(run, ana, sombra, 32)
        self.assertEqual(vitals.extra[sound_barrier.POOL_KEY].now, 0)
        self.assertEqual(vitals.now[HEALTH_POOL], 200)
        self.assertEqual(sombra.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_hack_preserves_adaptive_shield_and_emp_drains_it(self):
        test = nano_fixture.AnaNanoTests()
        run, ana, ball, sombra = test.fight(ally_hero="Wrecking Ball", enemy_hero="Sombra")
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(ball, (x, y, z + 3))
        placement.AnaOrisaBarrierTests.place(sombra, (x, y, z + 6))
        run.step({ball: (0, 0, 16)})
        for _ in range(5):
            run.step({})
        pool = self.vitals(run, ball).extra[("adaptive", moira.ADAPTIVE_SHIELD_TAG)]
        self.assertEqual(pool.now, 175)
        self.cast(run, ball, sombra, 2)
        self.assertEqual(pool.now, 175)
        self.charge(sombra)
        run.step({})
        run.step({sombra: (0, 0, 32)})
        for _ in range(45):
            run.step({})
        self.assertEqual(pool.now, 0)
        self.assertEqual(self.vitals(run, ball).total, 600)

    def test_projected_barrier_blocks_hack_and_emp_breaks_it_without_energy(self):
        test = nano_fixture.AnaNanoTests()
        run, ana, zarya, sombra = test.fight(ally_hero="Zarya", enemy_hero="Sombra")
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(zarya, (x + 3, y, z))
        placement.AnaOrisaBarrierTests.place(sombra, (x, y, z + 4))
        run.step({zarya: (*projected_fixture.AnaProjectedBarrierTests().direction(run, zarya, ana), 16)})
        fight = run.match.combat
        bubble = fight.sombra_controls.body_barrier(projected_fixture.AnaProjectedBarrierTests.entry(run, ana))
        self.assertIsNotNone(bubble)

        def health():
            vitals = getattr(bubble, "vitals", None)
            return bubble.health if vitals is None else vitals.total

        direction = self.direction(run, sombra, ana)
        for _ in range(65):
            run.step({sombra: (*direction, 2)})
        fight = run.match.combat
        self.assertNotIn(ana.body, fight.sombra_controls.locks)
        self.assertEqual(health(), 200)
        energy = zarya.body_script.component.entity_var(87).value()
        self.cast(run, ana, sombra, 32)
        self.assertEqual(health(), 0)
        self.assertEqual(zarya.body_script.component.entity_var(87).value(), energy)
        self.assertEqual(self.vitals(run, ana).total, 200)

    def test_hacked_immortality_field_loses_protection_and_retires_receiver_on_destruction(self):
        from test_baptiste_field import BaptisteFieldTests

        field_fixture = BaptisteFieldTests()
        run, baptiste, ally, sombra, fight, _, _ = field_fixture.make()
        actor = field_fixture.cast(run, baptiste, fight)
        ward = fight.baptiste.fields[actor.entity]
        x, y, z = actor.position
        placement.AnaOrisaBarrierTests.place(baptiste, (x, y, z + 3))
        placement.AnaOrisaBarrierTests.place(ally, (x + 2, y, z))
        self.assertTrue(run.match.switch_hero(sombra, heroes()[sombra_control.HERO]))
        placement.AnaOrisaBarrierTests.place(sombra, (x - 4, y, z))
        for _ in range(32):
            run.step({})
        self.assertIn(ally.body, ward.protections)
        direction = aim(fight._eye(sombra), (x, y + 0.13, z))
        for _ in range(80):
            run.step({sombra: (*direction, 2)})
            if actor.entity in fight.sombra_controls.locks:
                break
        self.assertIn(actor.entity, fight.sombra_controls.locks)
        entry = next(target for target in fight._targets() if target.owner is ally)
        self.assertFalse(fight.baptiste.protection_valid(entry, ward.protections[ally.body][1].id))
        run.step({})
        self.assertNotIn(ally.body, ward.protections)
        self.assertEqual(actor.health, 150)
        fight.destroy_deployable(actor.entity)
        self.assertNotIn(actor.entity, fight.sombra_controls.device_receivers)
        self.assertNotIn(actor.entity, fight.sombra_controls.locks)


        for scenario in ("enemy", "ally", "wall", "far"):
            with self.subTest(scenario=scenario):
                test = nano_fixture.AnaNanoTests()
                run, ana, winston, sombra = test.fight(ally_hero="Winston", enemy_hero="Sombra")
                x, y, z = ana.mover.position
                if scenario == "ally":
                    winston.team = sombra.team
                offset = 32 if scenario == "far" else 2
                placement.AnaOrisaBarrierTests.place(winston, (x + offset, y, z))
                placement.AnaOrisaBarrierTests.place(sombra, (x, y, z + 10))
                run.step({winston: (0, 0, 16)})
                for _ in range(15):
                    run.step({})
                manager = run.match.combat.barriers.world
                dome, = manager.actors.values()
                self.assertEqual(dome.health, 700)
                if scenario == "wall":
                    fixture.set_static_wall(run, ana, 6)
                self.charge(sombra)
                run.step({sombra: (0, 0, 32)})
                for _ in range(48):
                    run.step({})
                self.assertEqual(dome.health, 0 if scenario == "enemy" else 700)
                self.assertEqual(dome.body in manager.actors, scenario != "enemy")
                self.assertEqual(self.vitals(run, ana).total, 200)

    @unittest.skipUnless(all(os.environ.get(key) for key in (
        "ORPHEUS_ORISA_GEOMETRY", "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_SYMMETRA_COLLISION",
        "ORPHEUS_BARRIER_DATA", "ORPHEUS_BRIGITTE_COLLISION",
    )), "requires all five pinned external barrier meshes")
    def test_emp_breaks_other_hosted_enemy_barriers_without_health_or_armour_damage(self):
        import test_game_ana_brigitte_shield as brig_fixture
        import test_game_ana_photon_barrier as photon_fixture
        import test_game_ana_reinhardt as rein_fixture
        import test_game_ana_sigma_barrier as sigma_fixture

        for hero, test, prepare, field, held in (
            ("Orisa", placement.AnaOrisaBarrierTests(), "deploy", "health", False),
            ("Sigma", sigma_fixture.AnaSigmaBarrierTests(), "arena", "health", False),
            ("Symmetra", photon_fixture.AnaPhotonBarrierTests(), "deploy", "health", False),
            ("Reinhardt", rein_fixture.AnaReinhardtTests(), "arena", "hp", True),
            ("Brigitte", brig_fixture.AnaBrigitteShieldTests(), "arena", "health", True),
        ):
            with self.subTest(hero=hero):
                run, sombra, owner, barrier, *_ = getattr(test, prepare)()
                if hero == "Sigma":
                    test.deploy(run, owner, barrier)
                point = tuple(sombra.mover.position)
                self.assertTrue(run.match.switch_hero(sombra, heroes()[sombra_control.HERO]))
                placement.AnaOrisaBarrierTests.place(sombra, point)
                self.charge(sombra)
                inputs = {owner: (-32768, 0, 2)} if held else {}
                for _ in range(30):
                    run.step(inputs)
                vitals = self.vitals(run, owner)
                before = vitals.now[HEALTH_POOL], vitals.now[ARMOUR_POOL]
                self.assertGreater(getattr(barrier, field), 0)
                run.step({**inputs, sombra: (0, 0, 32)})
                for _ in range(45):
                    run.step(inputs)
                self.assertEqual(getattr(barrier, field), 0)
                self.assertEqual((vitals.now[HEALTH_POOL], vitals.now[ARMOUR_POOL]), before)
                self.assertEqual(vitals.now[SHIELDS_POOL], 0)

    def test_ordinary_and_emp_expire_independently_and_replacement_retires_both(self):
        for replace_body in (False, True):
            with self.subTest(replace_body=replace_body):
                run, ana, sombra = self.arena()
                self.cast(run, ana, sombra, 2)
                fight = run.match.combat
                controls = fight.sombra_controls
                first, = controls.locks[ana.body]
                for _ in range(36):
                    run.step({})
                self.charge(sombra)
                run.step({sombra: (0, 0, 32)})
                for _ in range(45):
                    run.step({})
                ordinary, emp = controls.locks[ana.body]
                self.assertIs(ordinary, first)
                self.assertIsNotNone(emp.pulse)
                if replace_body:
                    old = ana.body
                    self.assertTrue(run.match.switch_hero(ana, heroes()[sound_barrier.HERO]))
                    self.assertNotIn(old, controls.locks)
                    self.assertNotIn(ana.body, controls.locks)
                else:
                    while fight.now < ordinary.until:
                        run.step({})
                    self.assertEqual(controls.locks[ana.body], [emp])
                    while fight.now < emp.until:
                        run.step({})
                    self.assertNotIn(ana.body, controls.locks)

    def test_active_receiver_deadline_and_held_latch_are_causal_in_snapshot(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, (ana, sombra) = case.scene(two=True)
        peers[0].message(21616, stored.pick(ana.entity, fixture.ANA), now=101.0)
        peers[1].message(21616, stored.pick(sombra.entity, sombra_control.HERO), now=101.0)
        session.tick(101.0)
        match = ana.match
        x, y, z = ana.mover.position
        placement.AnaOrisaBarrierTests.place(sombra, (x, y, z + 4))
        self.charge(sombra)
        for index in range(45):
            match.update(101.016 + index * .016, match.tick + 1, frames=False)
            sombra.take_commands([Command(sombra.body_script.frame + 1, buttons=32 if index == 0 else 0)])
            ana.take_commands([Command(ana.body_script.frame + 1)])
        controls = match.combat.sombra_controls
        lock, = controls.locks[ana.body]
        self.assertIsNotNone(lock.pulse)
        ana.take_commands([Command(ana.body_script.frame + 1, buttons=8)])
        self.assertEqual(controls.held[ana.body], 8)
        active = stored.snapshot_digest(case.capture(session))
        lock.until += .016
        self.assertNotEqual(active, stored.snapshot_digest(case.capture(session)))
        lock.until -= .016
        self.assertEqual(active, stored.snapshot_digest(case.capture(session)))
        controls.held.pop(ana.body)
        self.assertNotEqual(active, stored.snapshot_digest(case.capture(session)))
