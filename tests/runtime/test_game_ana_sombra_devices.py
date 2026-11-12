"""Actual Sombra applications to deployed devices during Ana combat."""

import math
import unittest

import test_game_ana_baptiste_field as field_fixture
import test_game_ana_nano as nano_fixture
import test_game_ana_symmetra_portal as portal_fixture
import test_game_ana_symmetra_sentry as sentry_fixture
import test_game_ana_torbjorn_turret as turret_fixture
from test_game_ana import set_static_wall
from test_game_combat import aim

from ow174.game.script import expr
from ow174.game.script.driver import ULT_COST
from ow174.game.server_snapshot import _Graph
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL


class AnaSombraDeviceTests(unittest.TestCase):
    def arena(self, hero):
        run, ana, sombra, owner = nano_fixture.AnaNanoTests().fight(ally_hero="Sombra", enemy_hero=hero)
        x, y, z = ana.mover.position
        self.place(owner, (x + 4, y, z + 6))
        self.place(sombra, (x - 4, y, z + 1))
        for _ in range(20):
            run.step({})
        return run, ana, sombra, owner

    @staticmethod
    def place(actor, point):
        field_fixture.AnaBaptisteFieldTests.place(actor, point)

    @staticmethod
    def vitals(run, actor):
        fight = run.match.combat
        target = next(item for item in fight._targets() if item.owner is actor)
        return fight.vitals_of(target)

    def cast(self, run, sombra, device, button):
        fight = run.match.combat
        direction = aim(fight._eye(sombra), device.centre())
        if button == 32:
            script = sombra.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        initial = len(fight.sombra_controls.locks.get(device.body, ()))
        for frame in range(85):
            run.step({sombra: (*direction, button if button == 2 or frame == 0 else 0)})
            locks = fight.sombra_controls.locks.get(device.body, ())
            if len(locks) > initial:
                lock = locks[-1]
                self.assertEqual(lock.application.graph.index, 0x0B1F)
                self.assertEqual(lock.application.source_entity(), sombra.body)
                self.assertAlmostEqual(lock.until - fight.now, 10)
                self.assertTrue(expr.truthy(lock.application.component.entity_var(8912).value()))
                return lock
        self.fail("actual cast never applied 0B1F to the device")

    @staticmethod
    def advance_to(run, deadline):
        run.frame = max(run.frame + 1, math.ceil(deadline / .016))
        run.match.update(run.frame * .016, run.frame, frames=False)

    def test_turret_hack_and_emp_overlap_stop_then_resume_fire_on_ana(self):
        run, ana, sombra, torb = self.arena("Torbjorn")
        device = turret_fixture.AnaTorbjornTurretTests().deploy(run, torb)
        fight = run.match.combat
        x, y, z = device.position
        self.place(torb, (x + 26, y, z))
        self.place(ana, (x, y, z - 3))
        self.place(sombra, (x - 4, y, z))
        vitals = self.vitals(run, ana)
        for _ in range(110):
            run.step({})
            if vitals.total < 200:
                break
        self.assertEqual(vitals.total, 186)
        first = self.cast(run, sombra, device, 2)
        second = self.cast(run, sombra, device, 32)
        self.assertIsNone(first.pulse)
        self.assertIsNotNone(second.pulse)
        before = vitals.total
        self.assertEqual(device.health, 250)
        self.assertNotIn(torb.body, fight.sombra_controls.locks)
        self.advance_to(run, first.until)
        self.assertEqual(fight.sombra_controls.locks[device.body], [second])
        self.assertEqual(vitals.total, before)
        self.advance_to(run, second.until)
        self.assertNotIn(device.body, fight.sombra_controls.locks)
        self.assertEqual(vitals.total, before - 14)
        self.assertIs(fight.torbjorn_turrets.devices[torb.body], device)

    def test_ana_rifle_destruction_retires_turret_applications(self):
        run, ana, sombra, torb = self.arena("Torbjorn")
        device = turret_fixture.AnaTorbjornTurretTests().deploy(run, torb)
        fight = run.match.combat
        x, y, z = device.position
        self.place(torb, (x + 26, y, z))
        self.place(sombra, (x - 4, y, z))
        self.place(ana, (x, y, z - 4))
        first = self.cast(run, sombra, device, 2)
        second = self.cast(run, sombra, device, 32)
        fight.torbjorn_turrets.damage(device, 180)
        field_fixture.AnaBaptisteFieldTests().fire(run, ana, device.centre(), 1, scoped=True)
        self.assertNotIn(torb.body, fight.torbjorn_turrets.devices)
        self.assertNotIn(device.body, fight.sombra_controls.locks)
        self.assertNotIn(device.body, fight.sombra_controls.device_receivers)
        self.assertTrue(first.application.stopped)
        self.assertTrue(second.application.stopped)
        self.assertTrue(second.pulse.stopped)

    def test_field_hack_and_emp_allow_ana_rifle_to_cross_health_floor(self):
        for button in (2, 32):
            with self.subTest(button=button):
                run, ana, sombra, bap = self.arena("Baptiste")
                x, y, z = bap.mover.position
                device = field_fixture.AnaBaptisteFieldTests().deploy(run, bap, point=(x + 2, y, z))
                self.place(sombra, (x + 4, y, z + 1))
                self.place(ana, (x, y, z - 4))
                fight = run.match.combat
                target = next(item for item in fight._targets() if item.owner is bap)
                vitals = fight.vitals_of(target)
                vitals.damage(165)
                self.assertEqual(fight.baptiste_fields.health_floor(target, vitals), 20)
                lock = self.cast(run, sombra, device, button)
                self.assertEqual(fight.baptiste_fields.health_floor(target, vitals), 0)
                self.assertEqual(device.health, 150)
                field_fixture.AnaBaptisteFieldTests().fire(run, ana, (x, y + 1, z), 1)
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, ana.body)
                self.advance_to(run, fight.now + 6)
                self.assertNotIn(device.body, fight.sombra_controls.locks)
                self.assertTrue(lock.application.stopped)

    def test_sentry_hack_and_emp_clear_ana_beam_then_allow_same_sentry_to_fire(self):
        for button in (2, 32):
            with self.subTest(button=button):
                run, ana, sombra, sym = self.arena("Symmetra")
                helper = sentry_fixture.AnaSymmetraSentryTests()
                sentry = helper.throw(run, sym)
                helper.attach(run, sentry)
                x, y, z = sentry.position
                self.place(sym, (x + 26, y, z))
                self.place(ana, (x, y, z - 2))
                self.place(sombra, (x - 4, y, z))
                vitals = self.vitals(run, ana)
                for _ in range(100):
                    run.step({})
                    if sentry.target == ana.body:
                        break
                self.assertEqual(sentry.target, ana.body)
                lock = self.cast(run, sombra, sentry, button)
                self.assertIsNone(sentry.target)
                self.assertIsNone(sentry.aura)
                before = vitals.total
                for _ in range(25):
                    run.step({})
                self.assertEqual(vitals.total, before)
                self.assertEqual(sentry.health, 30)
                self.advance_to(run, lock.until)
                self.assertEqual(sentry.target, ana.body)
                self.assertEqual(vitals.total, before - 8)

    def test_teleporter_emp_relays_both_directions_without_draining_remote_shields(self):
        for index in (0, 1):
            with self.subTest(exposed=index):
                run, ana, sombra, sym = self.arena("Symmetra")
                fight = run.match.combat
                ana.team = sym.team
                helper = portal_fixture.AnaSymmetraPortalTests()
                pair = helper.deploy(run, sym)
                helper.ready(run, pair)
                exposed, remote = pair.endpoints[index], pair.endpoints[1 - index]
                x, y, z = exposed.position
                self.place(sombra, (x - 2, y, z + (4 if index == 0 else -4)))
                self.place(ana, remote.position)
                lock = self.cast(run, sombra, exposed, 32)
                self.assertEqual(exposed.vitals.now[HEALTH_POOL], 50)
                self.assertEqual(exposed.vitals.now[SHIELDS_POOL], 0)
                self.assertEqual(remote.vitals.now[HEALTH_POOL], 50)
                self.assertEqual(remote.vitals.now[SHIELDS_POOL], 250)
                partner, = fight.sombra_controls.locks[remote.body]
                self.assertIs(partner.pulse, lock.pulse)
                self.assertEqual(partner.application.source_entity(), sombra.body)
                self.assertEqual(partner.until, lock.until)
                self.assertTrue(expr.truthy(partner.application.component.entity_var(8912).value()))
                capture = _Graph({"match": run.match, "fight": fight}, None)
                capture.value((lock, partner))
                self.assertTrue(capture.finish())
                run.step({ana: (0, 0, 4)})
                self.assertAlmostEqual(ana.mover.position[2], remote.position[2], places=2)
                self.assertNotIn(ana.body, fight.symmetra_portals.transits)
                self.advance_to(run, lock.until)
                self.assertNotIn(exposed.body, fight.sombra_controls.locks)
                self.assertNotIn(remote.body, fight.sombra_controls.locks)
                self.assertTrue(partner.application.stopped)
                self.assertTrue(lock.pulse.stopped)
                run.step({})
                run.step({ana: (0, 0, 4)})
                self.assertAlmostEqual(ana.mover.position[2], exposed.position[2], places=2)

    def test_teleporter_hack_does_not_relay_or_drain_endpoint_shields(self):
        run, _, sombra, sym = self.arena("Symmetra")
        fight = run.match.combat
        helper = portal_fixture.AnaSymmetraPortalTests()
        pair = helper.deploy(run, sym)
        helper.ready(run, pair)
        remote, exposed = pair.endpoints
        x, y, z = exposed.position
        self.place(sym, (x + 26, y, z))
        self.place(sombra, (x - 2, y, z - 4))
        lock = self.cast(run, sombra, exposed, 2)
        self.assertIsNone(lock.pulse)
        self.assertNotIn(remote.body, fight.sombra_controls.locks)
        self.assertEqual(exposed.vitals.now[SHIELDS_POOL], 250)
        self.assertEqual(remote.vitals.now[SHIELDS_POOL], 250)

    def test_ana_destroying_pulsed_teleporter_retires_both_endpoint_applications(self):
        run, ana, sombra, sym = self.arena("Symmetra")
        fight = run.match.combat
        helper = portal_fixture.AnaSymmetraPortalTests()
        pair = helper.deploy(run, sym)
        helper.ready(run, pair)
        remote, exposed = pair.endpoints
        x, y, z = exposed.position
        self.place(sym, (x + 26, y, z))
        self.place(sombra, (x - 2, y, z - 4))
        self.place(ana, (x + 2, y, z - 4))
        lock = self.cast(run, sombra, exposed, 32)
        self.assertEqual(exposed.vitals.total, 50)
        partner, = fight.sombra_controls.locks[remote.body]
        field_fixture.AnaBaptisteFieldTests().fire(run, ana, exposed.centre(), 1, scoped=True)
        self.assertNotIn(sym.body, fight.symmetra_portals.pairs)
        for endpoint in pair.endpoints:
            self.assertNotIn(endpoint.body, fight.sombra_controls.locks)
            self.assertNotIn(endpoint.body, fight.sombra_controls.device_receivers)
        self.assertTrue(lock.application.stopped)
        self.assertTrue(lock.pulse.stopped)
        self.assertTrue(partner.application.stopped)

    def test_teleporter_emp_does_not_follow_an_unrelated_published_endpoint(self):
        run, ana, sombra, sym = self.arena("Symmetra")
        fight = run.match.combat
        helper = portal_fixture.AnaSymmetraPortalTests()
        pair = helper.deploy(run, sym)
        helper.ready(run, pair)
        remote, exposed = pair.endpoints
        x, y, z = exposed.position
        self.place(sombra, (x - 2, y, z - 4))
        self.place(ana, (x + 40, y, z))
        pair.instance.component.set(1624, expr.Entity(exposed.body))
        lock = self.cast(run, sombra, exposed, 32)
        self.assertEqual(fight.sombra_controls.locks[exposed.body], [lock])
        self.assertNotIn(remote.body, fight.sombra_controls.locks)
        self.assertEqual(remote.vitals.now[SHIELDS_POOL], 250)

    def test_turret_application_rejects_allied_device_world_cover_and_distance(self):
        for button in (2, 32):
            for gate in ("team", "cover", "range"):
                with self.subTest(button=button, gate=gate):
                    run, ana, sombra, torb = self.arena("Torbjorn")
                    device = turret_fixture.AnaTorbjornTurretTests().deploy(run, torb)
                    fight = run.match.combat
                    x, y, z = device.position
                    self.place(ana, (x + 35, y, z - 4))
                    self.place(sombra, (x, y, z - (36 if gate == "range" else 5)))
                    if gate == "team":
                        torb.team = sombra.team
                    elif gate == "cover":
                        set_static_wall(run, sombra, 2.5)
                    direction = aim(fight._eye(sombra), device.centre())
                    if button == 32:
                        script = sombra.body_script
                        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    for frame in range(75):
                        run.step({sombra: (*direction, button if button == 2 or frame == 0 else 0)})
                    self.assertNotIn(device.body, fight.sombra_controls.locks)
                    self.assertNotIn(device.body, fight.sombra_controls.device_receivers)
                    self.assertEqual(device.health, 250)

    def test_active_device_application_and_deadline_enter_stored_state(self):
        run, _, sombra, torb = self.arena("Torbjorn")
        device = turret_fixture.AnaTorbjornTurretTests().deploy(run, torb)
        fight = run.match.combat
        x, y, z = device.position
        self.place(torb, (x + 26, y, z))
        self.place(sombra, (x - 4, y, z))
        lock = self.cast(run, sombra, device, 32)

        def stored():
            capture = _Graph({"fight": fight}, None)
            capture.value(fight.sombra_controls)
            return capture.finish()

        first = stored()
        deadline = lock.until
        lock.until += 1
        self.assertNotEqual(stored(), first)
        lock.until = deadline
        self.assertEqual(stored(), first)
        fight.sombra_controls.retire(device.body)
        self.assertNotEqual(stored(), first)
