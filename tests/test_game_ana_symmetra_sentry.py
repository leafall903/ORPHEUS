"""Ana contacts against a sentry launched by actual Symmetra commands."""

import gzip
import json
import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_ana as fixture
import test_game_ana_baptiste_field as field_fixture
import test_game_ana_projected_barrier as barrier_fixture
from test_game_combat import aim

from ow174.game.commands import Command, FIRE
from ow174.game.content import find_hero
from ow174.game.script import expr, graph


class AnaSymmetraSentryTests(unittest.TestCase):
    def arena(self, *, enemy=True):
        run, ana, symmetra, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy)
        self.assertTrue(run.match.switch_hero(symmetra, find_hero("Symmetra")))
        x, y, z = ana.mover.position
        symmetra.mover.position[:] = x + 4, y, z + 5
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is ana))
        return run, ana, symmetra, vitals

    def throw(self, run, symmetra, *, point=None):
        fight = run.match.combat
        x, y, z = symmetra.mover.position
        direction = aim(fight._eye(symmetra), point or (x, y, z + 2))
        previous = set(fight.symmetra_sentries.devices)
        run.step({symmetra: (*direction, 8)})
        for _ in range(20):
            added = set(fight.symmetra_sentries.devices) - previous
            if added:
                body, = added
                sentry = fight.symmetra_sentries.devices[body]
                self.assertFalse(sentry.attached)
                self.assertEqual(sentry.speed, 15)
                self.assertEqual(sentry.health, 30)
                self.assertEqual(sentry.instance.find_var(*sentry.output).value(), expr.Entity(body))
                return sentry
            run.step({symmetra: (*direction, 0)})
        self.fail("actual sentry command produced no deployment")

    def attach(self, run, sentry):
        for _ in range(60):
            if sentry.attached:
                break
            run.step({})
        self.assertTrue(sentry.attached)

    def test_actual_deployment_attaches_to_a_wall_and_fires_after_build(self):
        run, ana, symmetra, vitals = self.arena()
        x, y, z = symmetra.mover.position
        fixture.set_static_wall(run, symmetra, 4)
        field_fixture.AnaBaptisteFieldTests.place(ana, (x, y, z + 1))
        sentry = self.throw(run, symmetra, point=(x, y + 1.55, z + 4))
        self.attach(run, sentry)
        self.assertAlmostEqual(sentry.position[2], z + 3.8, places=3)
        fight = run.match.combat
        first = sentry.next_shot
        while fight.now + .016 < first:
            run.step({})
        self.assertEqual(vitals.total, 200)
        while fight.now < first:
            run.step({})
        self.assertEqual(vitals.total, 192)
        self.assertEqual(sentry.target, ana.body)
        run.match.update(fight.now, fight.tick, frames=False)
        self.assertEqual(vitals.total, 192)

    @staticmethod
    def move_ana(run, ana):
        for _ in range(12):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
            ana.take_commands([Command(run.frame, forward=127)])
        return math.hypot(ana.mover.velocity[0], ana.mover.velocity[2])

    def assert_beam_speed_and_cleanup(self, *, authored):
        run, ana, symmetra, vitals = self.arena()
        sentry = self.throw(run, symmetra)
        self.attach(run, sentry)
        fight = run.match.combat
        while vitals.total == 200 and fight.now < sentry.next_shot + .016:
            run.step({})
        aura = sentry.aura
        self.assertEqual(aura is not None, authored)
        self.assertAlmostEqual(self.move_ana(run, ana), 4.4, places=2)
        self.assertEqual(ana.mover.input_throttles, (0, 127))
        direction = aim(fight._eye(ana), sentry.centre())
        run.step({ana: (*direction, FIRE)})
        for _ in range(60):
            if sentry.body not in fight.symmetra_sentries.devices:
                break
            run.step({})
        self.assertEqual(sentry.health, 0)
        self.assertFalse(fight.symmetra_sentries.movement_mods(ana.body))
        self.assertEqual(sentry.instance.find_var(*sentry.output).value(), expr.Entity(0))
        if authored:
            self.assertTrue(aura.stopped)
        self.assertAlmostEqual(self.move_ana(run, ana), 5.5, places=2)
        self.assertEqual(ana.mover.input_throttles, (0, 127))

    def test_beam_slows_ana_movement_without_blocking_fire_and_destruction_clears_it(self):
        self.assert_beam_speed_and_cleanup(authored=graph.graph(0x01E7) is not None)

    @staticmethod
    def supplement_graph(index):
        path = Path(os.environ["ORPHEUS_SYMMETRA_SENTRY_SUPPLEMENT"])
        record = next(item for item in json.loads(gzip.decompress(path.read_bytes()))["graphs"]
                      if item["index"] == index)
        return graph.Graph(record)

    @unittest.skipUnless(os.getenv("ORPHEUS_SYMMETRA_SENTRY_SUPPLEMENT"), "requires extracted 01E7")
    def test_live_sentry_attaches_and_releases_the_authored_aura(self):
        status = self.supplement_graph(0x01E7)
        lookup = graph.graph
        with patch.object(graph, "graph", side_effect=lambda index: status if index == 0x01E7 else lookup(index)):
            self.assert_beam_speed_and_cleanup(authored=True)

    def test_ana_weapon_contacts_damage_enemy_sentry(self):
        for button, scoped, remaining in ((1, False, 0), (1, True, 0), (8, False, 25),
                                           (16, False, 0), (0x800, False, 0)):
            with self.subTest(button=button, scoped=scoped):
                run, ana, symmetra, _ = self.arena()
                sentry = self.throw(run, symmetra)
                self.attach(run, sentry)
                if button == 0x800:
                    x, _, z = sentry.position
                    field_fixture.AnaBaptisteFieldTests.place(ana, (x, ana.mover.position[1], z - 1.3))
                field_fixture.AnaBaptisteFieldTests().fire(run, ana, sentry.centre(), button, scoped=scoped)
                self.assertEqual(sentry.health, remaining)

    def test_allied_sentry_ignores_ana_and_her_attacks(self):
        run, ana, symmetra, vitals = self.arena(enemy=False)
        sentry = self.throw(run, symmetra)
        self.attach(run, sentry)
        field_fixture.AnaBaptisteFieldTests().fire(run, ana, sentry.centre(), FIRE, scoped=True, frames=100)
        self.assertEqual(sentry.health, 30)
        self.assertEqual(vitals.total, 200)
        self.assertIsNone(sentry.target)

    def test_range_and_static_cover_prevent_beam_damage(self):
        run, ana, symmetra, vitals = self.arena()
        sentry = self.throw(run, symmetra)
        self.attach(run, sentry)
        fight = run.match.combat
        x, _, z = sentry.position
        y = ana.mover.position[1]
        place = field_fixture.AnaBaptisteFieldTests.place
        place(ana, (x, y, z - 11))
        while fight.now < sentry.next_shot + .05:
            run.step({})
        self.assertEqual(vitals.total, 200)
        place(ana, (x, y, z - 5))
        world = run.match.collision_world
        fixture.set_static_wall(run, ana, 2.5)
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, 200)
        run.match.collision_world = world
        run.step({})
        self.assertEqual(vitals.total, 192)

    def test_actual_projected_barrier_blocks_beam_and_slow(self):
        helper = barrier_fixture.AnaProjectedBarrierTests()
        run, ana, zarya, symmetra = helper.fight(ally_ana=True)
        self.assertTrue(run.match.switch_hero(symmetra, find_hero("Symmetra")))
        symmetra.team = 0
        x, y, z = ana.mover.position
        place = field_fixture.AnaBaptisteFieldTests.place
        place(symmetra, (x, y, z + 3))
        place(zarya, (x + 6, y, z))
        for _ in range(25):
            run.step({})
        sentry = self.throw(run, symmetra)
        self.attach(run, sentry)
        barrier = helper.cast(run, zarya, ana)
        fight = run.match.combat
        first = sentry.next_shot
        while fight.now < first:
            run.step({})
        self.assertEqual(barrier.health, 192)
        self.assertEqual(fight.vitals_of(helper.entry(run, ana)).total, 200)
        self.assertIsNone(sentry.target)
        self.assertFalse(fight.symmetra_sentries.movement_mods(ana.body))

    def test_actual_charges_exhaust_and_recharge_then_oldest_device_is_replaced(self):
        run, _, symmetra, _ = self.arena(enemy=False)
        devices = []
        x, y, z = symmetra.mover.position
        for offset in (-1, 0, 1):
            device = self.throw(run, symmetra, point=(x + offset, y, z + 2))
            self.attach(run, device)
            devices.append(device)
            for _ in range(36):
                run.step({})
        fight = run.match.combat
        charges = devices[-1].instance.find_var(expr.INSTANCE, 11)
        self.assertEqual(charges.value(), 0)
        before = set(fight.symmetra_sentries.devices)
        run.step({symmetra: (0, 0, 8)})
        for _ in range(40):
            run.step({})
        self.assertEqual(set(fight.symmetra_sentries.devices), before)
        for _ in range(900):
            if charges.value() > 0:
                break
            run.step({})
        self.assertEqual(charges.value(), 1)
        latest = self.throw(run, symmetra)
        self.assertEqual(set(fight.symmetra_sentries.devices),
                         {devices[1].body, devices[2].body, latest.body})

    def test_body_replacement_retires_both_flight_and_attached_sentry(self):
        for attached in (False, True):
            with self.subTest(attached=attached):
                run, _, symmetra, _ = self.arena()
                sentry = self.throw(run, symmetra)
                if attached:
                    self.attach(run, sentry)
                slot = sentry.instance.find_var(*sentry.output)
                self.assertTrue(run.match.switch_hero(symmetra, find_hero("Soldier: 76")))
                self.assertFalse(run.match.combat.symmetra_sentries.devices)
                self.assertEqual(slot.value(), expr.Entity(0))

    def test_managed_flight_without_a_surface_expires_and_clears_output(self):
        run, _, symmetra, _ = self.arena()
        x, y, z = symmetra.mover.position
        sentry = self.throw(run, symmetra, point=(x, y + 1.55, z + 3))
        fight = run.match.combat
        for _ in range(310):
            run.step({})
        self.assertFalse(sentry.attached)
        self.assertIn(sentry.body, fight.symmetra_sentries.devices)
        for _ in range(4):
            run.step({})
        self.assertNotIn(sentry.body, fight.symmetra_sentries.devices)
        self.assertEqual(sentry.instance.find_var(*sentry.output).value(), expr.Entity(0))

    def ice_wall_arena(self):
        helper = barrier_fixture.AnaProjectedBarrierTests()
        run, ana, mei, symmetra = helper.fight(ally_ana=True)
        self.assertTrue(run.match.switch_hero(mei, find_hero("Mei")))
        self.assertTrue(run.match.switch_hero(symmetra, find_hero("Symmetra")))
        mei.team = symmetra.team = 0
        x, y, z = ana.mover.position
        place = field_fixture.AnaBaptisteFieldTests.place
        place(mei, (x, y, z + 8))
        place(symmetra, (x, y, z + 1))
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        direction = aim(fight._eye(mei), (x, y, z + 4))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*direction, buttons)})
        self.assertEqual(len(fight.ice_wall.pieces), 5)
        centre = min(fight.ice_wall.pieces.values(), key=lambda piece: abs(piece.feet[0] - x))
        sentry = self.throw(run, symmetra, point=(x, y + 1.55, z + 4))
        self.attach(run, sentry)
        self.assertEqual(sentry.support_pillar, centre.entity)
        return run, ana, mei, sentry, centre, fight.vitals_of(helper.entry(run, ana))

    def test_mei_recall_retires_ice_attached_sentry_and_clears_slow(self):
        run, ana, mei, sentry, _, vitals = self.ice_wall_arena()
        fight = run.match.combat
        first = sentry.next_shot
        while fight.now < first:
            run.step({})
        self.assertEqual(vitals.total, 192)
        self.assertEqual(sentry.target, ana.body)
        run.step({mei: (0, 0, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        self.assertFalse(fight.symmetra_sentries.devices)
        self.assertFalse(fight.symmetra_sentries.movement_mods(ana.body))
        self.assertEqual(sentry.instance.find_var(*sentry.output).value(), expr.Entity(0))
        self.assertAlmostEqual(self.move_ana(run, ana), 5.5, places=2)

    @unittest.skipUnless(os.getenv("ORPHEUS_SYMMETRA_SENTRY_SUPPLEMENT"), "requires extracted 157F")
    def test_ice_attachment_runs_the_authored_wait_and_chase_before_beam(self):
        status = self.supplement_graph(0x157F)
        lookup = graph.graph
        with patch.object(graph, "graph", side_effect=lambda index: status if index == 0x157F else lookup(index)):
            run, _, mei, sentry, _, vitals = self.ice_wall_arena()
            placement = sentry.placement
            self.assertIs(placement.graph, status)
            self.assertEqual(placement.component.entity, sentry.body)
            for _ in range(26):
                run.step({})
            self.assertIsNone(placement.component.entity_var(639).value())
            for _ in range(20):
                run.step({})
            progress = placement.component.entity_var(639)
            self.assertGreater(progress.value(), 0)
            self.assertLess(progress.value(), 1)
            self.assertEqual(vitals.total, 200)
            first = sentry.next_shot
            while run.match.combat.now < first:
                run.step({})
            self.assertEqual(progress.value(), 1)
            self.assertEqual(vitals.total, 192)
            run.step({mei: (0, 0, 16)})
            self.assertTrue(placement.stopped)

    def test_ana_breaking_the_support_pillar_retires_the_sentry(self):
        run, ana, _, sentry, centre, _ = self.ice_wall_arena()
        fight = run.match.combat
        centre.health = 60  # Begin at a one-rifle-hit break boundary.
        x, y, z = centre.feet
        symmetra = run.players[2]
        owner_x, owner_y, owner_z = symmetra.mover.position
        field_fixture.AnaBaptisteFieldTests.place(symmetra, (owner_x + 2, owner_y, owner_z))
        point = (x, y + .45, z)
        field_fixture.AnaBaptisteFieldTests().fire(run, ana, point, FIRE, scoped=True)
        self.assertEqual(centre.health, 0)
        self.assertNotIn(sentry.body, fight.symmetra_sentries.devices)
        self.assertEqual(sentry.health, 30)
        self.assertEqual(len(fight.ice_wall.pieces), 4)

    def test_support_expiry_preserves_retained_beam_pulses_and_clears_target(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, ana, _, sentry, centre, vitals = self.ice_wall_arena()
                fight = run.match.combat
                # Place expiry between the first and second pulses while the
                # scheduler starts with the sentry still building.
                first = math.ceil(sentry.next_shot / .016)
                centre.expires = first + 5
                start, end = first - 4, first + 16
                while run.frame < start:
                    run.step({})
                if skipped:
                    run.match.update(end * .016, end, frames=False)
                else:
                    while run.frame < end:
                        run.step({})
                outcomes.append(vitals.total)
                self.assertNotIn(sentry.body, fight.symmetra_sentries.devices)
                self.assertFalse(fight.symmetra_sentries.movement_mods(ana.body))
                self.assertIsNone(sentry.target)
        self.assertEqual(outcomes, [192, 192])

    def test_flight_position_and_device_health_enter_the_server_snapshot(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        symmetra = players[1]
        peers[1].message(21616, stored.pick(symmetra.entity, find_hero("Symmetra").guid), now=100.8)
        peers[1].commands(symmetra.body_script.frame + 1, 40, 8)
        fight = symmetra.match.combat
        sentry, = fight.symmetra_sentries.devices.values()
        before = stored.snapshot_digest(case.capture(session))
        position = sentry.position
        x, y, z = position
        sentry.position = x + .25, y, z
        self.assertNotEqual(before, stored.snapshot_digest(case.capture(session)))
        sentry.position = position
        self.assertEqual(before, stored.snapshot_digest(case.capture(session)))
        fight.update(fight.now + .016, fight.tick + 1, frames=False)
        self.assertNotEqual(sentry.position, position)
        advanced = stored.snapshot_digest(case.capture(session))
        self.assertNotEqual(before, advanced)
        sentry.health -= 5
        self.assertNotEqual(advanced, stored.snapshot_digest(case.capture(session)))
