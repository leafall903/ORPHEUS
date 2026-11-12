"""Executed Blizzard authority; scheduling/physics are explicit reconstructions."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_delivered_mei_combat as fixtures
import test_mei_frost as frost_fixtures
import test_mei_wall as wall_fixtures
import test_mei_wall_placement as placement_fixtures
from test_game_combat import in_world

from ow174.game import combat, mei, mei_blizzard, world


class BlizzardTests(unittest.TestCase):
    def scene(self):
        return fixtures.MeiCombatTests().duel()

    def launch(self, run, owner, *, horizontal=False):
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        x, y, z = owner.mover.position
        aim = (0, 0) if horizontal else fixtures.aim(run.match.combat._eye(owner), (x, y, z + 5.0))
        for frame in range(32):
            run.step({owner: (*aim, 32 if frame == 0 else 0)})
        manager = run.match.combat.blizzard
        self.assertEqual(len(manager.flights), 1)
        return manager.flights[0]

    def landed(self, run):
        manager = run.match.combat.blizzard
        for _ in range(150):
            if manager.areas:
                break
            run.step({})
        self.assertEqual(len(manager.areas), 1)
        return next(iter(manager.areas.values()))

    def finish(self, run, area):
        while run.frame * 16 < area.ends:
            run.step({})

    def test_charge_windup_release_and_ammo_are_driven_by_actual_graph(self):
        run, owner, _enemy, _target = self.scene()
        manager = run.match.combat.blizzard
        for frame in range(40):
            run.step({owner: (0, 9000, 32 if frame == 0 else 0)})
        self.assertFalse(manager.flights)
        self.assertFalse(manager.areas)
        component = owner.body_script.component
        self.assertEqual(component.entity_var(338).value(), 1610.0)
        owner.body_script.set_ult_charge(1610.0)
        for frame in range(31):
            run.step({owner: (0, 9000, 32 if frame == 0 else 0)})
        self.assertEqual(component.entity_var(342).value(), 0.0)
        self.assertFalse(manager.flights)
        run.step({})
        self.assertEqual(len(manager.flights), 1)
        self.assertEqual(manager.flights[0].left, 60.0)
        self.assertEqual(manager.flights[0].gravity, 9.800000190734863)
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x5E6)
        self.assertEqual(weapon.vars[53].value(), 120)

    def test_gravity_flight_passes_through_bodies_without_direct_damage(self):
        run, owner, _enemy, target = self.scene()
        flight = self.launch(run, owner, horizontal=True)
        start, velocity = flight.position, flight.velocity
        for _ in range(20):
            run.step({})
        elapsed = 20 * combat.TICK
        self.assertAlmostEqual(flight.position[1], start[1] - flight.gravity * elapsed**2 / 2)
        self.assertAlmostEqual(flight.position[2], start[2] + velocity[2] * elapsed)
        self.assertGreater(flight.position[2], target.feet[2])
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
        self.assertFalse(run.match.combat.blizzard.areas)

    def test_actual_launch_lands_delays_damage_and_expires_after_exact_85_damage(self):
        run, owner, enemy, target = self.scene()
        flight = self.launch(run, owner)
        area = self.landed(run)
        fight = run.match.combat
        self.assertNotIn(flight, fight.blizzard.flights)
        self.assertIsNotNone(flight.shot)
        self.assertIsNotNone(flight.shot.release_at)
        self.assertAlmostEqual(area.position[1], owner.mover.position[1])
        self.assertEqual(area.ends - area.starts, 4250)
        self.assertEqual(fight.vitals_of(target).total, 200.0)
        while run.frame * 16 <= area.starts:
            run.step({})
        self.assertLess(fight.vitals_of(target).total, 200.0)
        self.assertGreaterEqual(fight.frost(enemy)[0], 0.5)
        self.finish(run, area)
        self.assertFalse(fight.blizzard.areas)
        self.assertAlmostEqual(fight.vitals_of(target).total, 115.0)
        for op in (world.OP_CREATE, world.OP_DESTROY):
            self.assertTrue(any(u.entity == area.entity and u.op == op for u in owner.client.entities))
        before = fight.vitals_of(target).total
        for _ in range(100):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, before)
        self.assertIsNone(fight.frost(enemy))

    def test_frost_uses_area_initial_and_point04_per_point2_seconds_then_thaws(self):
        run, owner, enemy, _target = self.scene()
        self.launch(run, owner)
        area = self.landed(run)
        fight = run.match.combat
        while run.frame * 16 < area.starts + 2500:
            run.step({})
        self.assertFalse(fight.frozen(enemy))
        while run.frame * 16 <= area.starts + 2600:
            run.step({})
        self.assertTrue(fight.frozen(enemy))
        sample = fight.frost(enemy)
        self.assertEqual(sample[2] - sample[1], 1300)
        self.assertGreaterEqual(sample[1], area.starts + 2600)
        while run.frame * 16 < sample[2] + 32:
            run.step({})
        self.assertFalse(fight.frozen(enemy))

    def test_allies_and_owner_are_immune(self):
        run, owner, ally, target = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = owner.team
        self.launch(run, owner)
        area = self.landed(run)
        self.finish(run, area)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(target).total, 200.0)
        self.assertIsNone(fight.frost(ally))
        own = next(t for t in fight._targets() if t.owner is owner)
        self.assertEqual(fight.vitals_of(own).total, 250.0)
        self.assertIsNone(fight.frost(owner))

    def test_active_area_resamples_team_and_does_not_replay_allied_ticks(self):
        for source_changes in (False, True):
            with self.subTest(source_changes=source_changes):
                run, owner, enemy, target = self.scene()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, enemy.team = 0, 1
                fight = run.match.combat
                self.launch(run, owner)
                area = self.landed(run)
                while run.frame * 16 < area.starts + 500:
                    run.step({})
                vitals = fight.vitals_of(target)
                health = vitals.total
                frost = fight.frost(enemy)
                self.assertLess(health, 200.)
                self.assertGreater(frost[0], .5)
                self.assertIn(enemy.body, area.frost_due)

                changed, other = (owner, enemy) if source_changes else (enemy, owner)
                previous_team = changed.team
                changed.team = other.team  # Explicit mid-area team-field fixture.
                run.step({})
                self.assertEqual(vitals.total, health)
                self.assertNotIn(enemy.body, area.frost_due)
                self.assertEqual(fight.frost(enemy), frost)
                while run.frame * 16 < frost[1] + mei.FROST_GAP_MS:
                    run.step({})
                    self.assertEqual(vitals.total, health)
                self.assertIsNone(fight.frost(enemy))

                changed.team = previous_team
                run.step({})
                self.assertIn(area.entity, fight.blizzard.areas)
                self.assertAlmostEqual(vitals.total, health - .32)
                self.assertAlmostEqual(fight.frost(enemy)[0], .5, places=6)
                self.assertIn(enemy.body, area.frost_due)

    def test_walking_out_and_back_into_blizzard_does_not_replay_ticks(self):
        run, owner, enemy, target = self.scene()
        fight = run.match.combat
        self.launch(run, owner)
        area = self.landed(run)
        x, y, z = area.position
        fight.walls.place_player(enemy, (x, y, z + 9.4))
        while run.frame * 16 < area.starts + 500:
            run.step({})
        self.assertGreater(fight.frost(enemy)[0], .5)
        self.assertIn(enemy.body, area.frost_due)

        move = frost_fixtures.FrostTests().commands
        for _ in range(40):
            move(run, enemy, 1, forward=127)
            if enemy.body not in area.frost_due:
                break
        self.assertNotIn(enemy.body, area.frost_due)
        vitals = fight.vitals_of(target)
        health = vitals.total
        for _ in range(65):
            run.step({})
            self.assertEqual(vitals.total, health)
        self.assertIsNone(fight.frost(enemy))

        for _ in range(40):
            move(run, enemy, 1, forward=-127)
            if enemy.body in area.frost_due:
                break
        self.assertIn(enemy.body, area.frost_due)
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertAlmostEqual(vitals.total, health - .32)
        self.assertAlmostEqual(fight.frost(enemy)[0], .5)
        self.assertFalse(fight.frozen(enemy))

    def test_range_and_static_occlusion_reject_damage_and_status(self):
        for distance, blocked in ((9.9, False), (10.6, False), (9.9, True)):
            with self.subTest(distance=distance, blocked=blocked):
                run, owner, enemy, target = self.scene()
                self.launch(run, owner)
                area = self.landed(run)
                fight = run.match.combat
                x, y, z = area.position
                fight.walls.place_player(enemy, (x, y, z + distance))
                if blocked:
                    fixtures.static_wall(run, owner, distance=z - owner.mover.position[2] + 2.)
                self.finish(run, area)
                if distance < 10. and not blocked:
                    self.assertAlmostEqual(fight.vitals_of(target).total, 115.)
                    self.assertIsNotNone(fight.frost(enemy))
                else:
                    self.assertEqual(fight.vitals_of(target).total, 200.)
                    self.assertIsNone(fight.frost(enemy))

    def test_static_wall_impact_exposes_only_the_impact_side(self):
        run, owner, rear, rear_target = self.scene()
        fight = run.match.combat
        front = run.match.add_player(17, "Front", rear.hero.guid, 0, False)
        in_world(run.match, front)
        run.players.append(front)
        self.assertTrue(run.match.switch_hero(front, rear.hero))
        x, y, z = owner.mover.position
        fight.walls.place_player(front, (x + 2., y, z + .5))
        fixtures.static_wall(run, owner, distance=3.)
        front_target = next(target for target in fight._targets() if target.owner is front)

        flight = self.launch(run, owner, horizontal=True)
        area = self.landed(run)
        self.assertNotIn(flight, fight.blizzard.flights)
        self.assertAlmostEqual(area.position[2], z + 3.)
        self.assertGreater(area.position[1], y)
        self.assertEqual(area.static_impact_normal, (0., 0., -1.))

        self.finish(run, area)
        self.assertAlmostEqual(fight.vitals_of(front_target).total, 115.)
        self.assertEqual(fight.vitals_of(rear_target).total, 200.)
        self.assertIsNone(fight.frost(rear))

    def test_overhead_impact_keeps_exposure_on_the_lower_side(self):
        run, owner, lower, lower_target = self.scene()
        fight = run.match.combat
        upper = run.match.add_player(17, "Upper", lower.hero.guid, 0, False)
        in_world(run.match, upper)
        run.players.append(upper)
        self.assertTrue(run.match.switch_hero(upper, lower.hero))
        x, y, z = owner.mover.position
        ceiling = y + 2.7
        shape = placement_fixtures.WallPlacementTests()
        shape.install(run, shape.floor(x, y, z) + shape.floor(x, ceiling, z))
        fight.walls.place_player(upper, (x + 2., ceiling, z + 3.))
        upper_target = next(target for target in fight._targets() if target.owner is upper)
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        for frame in range(32):
            run.step({owner: (0, -8192, 32 if frame == 0 else 0)})
        area = self.landed(run)
        self.assertAlmostEqual(area.position[1], ceiling)
        self.assertEqual(area.static_impact_normal, (0., -1., 0.))
        self.finish(run, area)
        self.assertAlmostEqual(fight.vitals_of(lower_target).total, 115.)
        self.assertEqual(fight.vitals_of(upper_target).total, 200.)
        self.assertIsNone(fight.frost(upper))

    def test_ice_wall_stops_blizzard_flight_at_near_face(self):
        run, owner, enemy, target = self.scene()
        fight = run.match.combat
        wall = wall_fixtures.WallTests()
        pieces, aim = wall.place(run, owner)
        self.assertEqual(len(pieces), 5)
        centre = pieces[2]
        vitals = fight.vitals_of(target)
        health = vitals.total
        flight = self.launch(run, owner, horizontal=True)
        area = self.landed(run)
        self.assertNotIn(flight, fight.blizzard.flights)
        self.assertIs(area.owner, owner)
        self.assertAlmostEqual(area.position[0], centre.feet[0])
        self.assertAlmostEqual(area.position[2], centre.feet[2] - centre.depth)
        self.assertGreater(area.position[1], centre.feet[1])
        for _ in range(44):
            run.step({})
        self.assertEqual(vitals.total, health)
        self.assertIsNone(fight.frost(enemy))
        self.assertTrue(all(piece.health == 400. for piece in pieces))

        wall.tap(run, owner, 16, aim)
        self.assertFalse(fight.walls.pieces)
        for _ in range(30):
            run.step({})
        self.assertIs(fight.blizzard.areas[area.entity], area)
        self.assertLess(vitals.total, health)
        self.assertIsNotNone(fight.frost(enemy))

    def test_ice_wall_occludes_area_until_cancelled(self):
        run, owner, enemy, target = self.scene()
        # Land at1m: wall at3m separates centre from the enemy at5m.
        owner.body_script.set_ult_charge(1610.0)
        x, y, z = owner.mover.position
        aim = fixtures.aim(run.match.combat._eye(owner), (x, y, z + 1.0))
        for frame in range(32):
            run.step({owner: (*aim, 32 if frame == 0 else 0)})
        area = self.landed(run)
        for _ in range(25):
            run.step({})
        # Weapon actions become available again after the authored .25s end wait.
        pieces, wall_aim = wall_fixtures.WallTests().place(run, owner)
        self.assertEqual(len(pieces), 5)
        before = run.match.combat.vitals_of(target).total
        for _ in range(30):
            run.step({})
        self.assertEqual(run.match.combat.vitals_of(target).total, before)
        wall_fixtures.WallTests().tap(run, owner, 16, wall_aim)
        for _ in range(30):
            run.step({})
        self.assertLess(run.match.combat.vitals_of(target).total, before)
        self.assertIsNotNone(run.match.combat.frost(enemy))
        self.assertIn(area.entity, run.match.combat.blizzard.areas)

    def test_owner_death_interrupts_windup_but_does_not_cancel_released_ultimate(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, owner, enemy, target = self.scene()
                if released:
                    self.launch(run, owner)
                else:
                    owner.body_script.set_ult_charge(1610.0)
                    run.step({owner: (0, 9000, 32)})
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is owner)
                fight._damage(enemy, own, 1000.0, False, None, run.frame)
                for _ in range(160):
                    run.step({})
                if released:
                    self.assertTrue(fight.blizzard.areas)
                    self.assertLess(fight.vitals_of(target).total, 200.0)
                else:
                    self.assertFalse(fight.blizzard.flights)
                    self.assertFalse(fight.blizzard.areas)
                    self.assertEqual(fight.vitals_of(target).total, 200.0)

    def test_landed_blizzard_keeps_original_deadline_after_caster_is_killed(self):
        run, owner, enemy, target = self.scene()
        fight = run.match.combat
        self.launch(run, owner)
        area = self.landed(run)
        deadline = area.ends
        vitals = fight.vitals_of(target)
        while run.frame * 16 <= area.starts:
            run.step({})
        self.assertLess(vitals.total, 200.0)
        own = next(t for t in fight._targets() if t.owner is owner)
        fight.vitals_of(own).damage(240.0)
        x, y, z = own.feet
        rifle_aim = fixtures.aim(fight._eye(enemy), (x, y + 0.9, z))
        run.step({enemy: (*rifle_aim, 1)})
        self.assertTrue(fight.is_dead(owner))
        self.assertIs(fight.blizzard.areas.get(area.entity), area)
        self.assertEqual(area.ends, deadline)
        after_death = vitals.total
        self.finish(run, area)
        self.assertNotIn(area.entity, fight.blizzard.areas)
        self.assertLess(vitals.total, after_death)
        self.assertAlmostEqual(vitals.total, 115.0)
        destroys = [u for u in enemy.client.entities if u.entity == area.entity and u.op == world.OP_DESTROY]
        self.assertEqual(len(destroys), 1)
        for _ in range(100):
            run.step({})
        self.assertAlmostEqual(vitals.total, 115.0)
        self.assertIsNone(fight.frost(enemy))

    def test_lifetime_in_empty_space_does_not_spawn_area(self):
        run, owner, _enemy, _target = self.scene()
        flight = self.launch(run, owner, horizontal=True)
        flight.left = 0.02  # Explicit short lifetime fixture, not a different authored lifetime.
        with patch.object(run.match.collision_world, 'cast_ray', return_value=None):
            for _ in range(3):
                run.step({})
        self.assertFalse(run.match.combat.blizzard.flights)
        self.assertFalse(run.match.combat.blizzard.areas)
        self.assertAlmostEqual(flight.flown, 0.02)

    def test_body_replacement_retires_flight_and_area(self):
        for landed in (False, True):
            with self.subTest(landed=landed):
                run, owner, enemy, target = self.scene()
                fight = run.match.combat
                flight = self.launch(run, owner)
                vitals = fight.vitals_of(target)
                area = None
                if landed:
                    area = self.landed(run)
                    while run.frame * 16 <= area.starts:
                        run.step({})
                    self.assertLess(vitals.total, 200.)
                    self.assertIsNotNone(fight.frost(enemy))
                    self.assertIn(area.entity, enemy.seen)
                else:
                    self.assertIn(flight, fight.blizzard.flights)
                    self.assertEqual(vitals.total, 200.)
                health = vitals.total
                body = owner.body

                self.assertTrue(run.match.switch_hero(owner, enemy.hero))
                self.assertNotEqual(owner.body, body)
                self.assertFalse(fight.blizzard.flights)
                self.assertFalse(fight.blizzard.areas)
                if area is not None:
                    self.assertNotIn(area.entity, enemy.seen)
                    destroys = [u for u in enemy.client.entities
                                if u.entity == area.entity and u.op == world.OP_DESTROY]
                    self.assertEqual(len(destroys), 1)
                    self.finish(run, area)
                for _ in range(160):
                    run.step({})
                    self.assertFalse(fight.blizzard.flights)
                    self.assertFalse(fight.blizzard.areas)
                self.assertEqual(vitals.total, health)
                self.assertIsNone(fight.frost(enemy))

    def test_active_area_resamples_replacement_target_from_its_new_position(self):
        for stays in (False, True):
            with self.subTest(stays=stays):
                run, owner, enemy, _target = self.scene()
                fight = run.match.combat
                self.launch(run, owner)
                area = self.landed(run)
                while run.frame * 16 < area.starts + 1000:
                    run.step({})
                old = enemy.body
                self.assertAlmostEqual(fight.frost(enemy)[0], .66, places=6)
                self.assertIn(old, area.frost_due)
                x, y, z = enemy.mover.position
                enemy.spawn = ((x if stays else x + 20., y, z), enemy.spawn[1])
                self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
                self.assertNotEqual(enemy.body, old)
                self.assertNotIn(old, fight.vitals)
                self.assertNotIn(old, fight.frost_sources)
                self.assertIsNone(fight.frost(enemy))
                target = next(t for t in fight._targets() if t.owner is enemy)
                self.assertEqual(fight.vitals_of(target).total, 200.)
                run.step({})
                self.assertNotIn(old, area.frost_due)
                self.assertEqual(enemy.body in area.frost_due, stays)
                self.assertAlmostEqual(fight.vitals_of(target).total, 199.68 if stays else 200.)
                if stays:
                    self.assertAlmostEqual(fight.frost(enemy)[0], .5)
                else:
                    self.assertIsNone(fight.frost(enemy))

    def test_cryo_prevents_area_damage_and_frost_then_cancel_restores_exposure(self):
        run, owner, enemy, _target = self.scene()
        self.assertTrue(run.match.switch_hero(enemy, owner.hero))
        own_pos = owner.mover.position
        run.match.combat.walls.place_player(enemy, (own_pos[0], own_pos[1], own_pos[2] + 5.0))
        for _ in range(30):
            run.step({})
        self.launch(run, owner)
        area = self.landed(run)
        run.step({enemy: (0, 0, 8)})
        self.assertTrue(mei.protected(enemy))
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        for _ in range(75):
            run.step({})
        self.assertEqual(run.match.combat.vitals_of(target).total, 250.0)
        self.assertIsNone(run.match.combat.frost(enemy))
        run.step({enemy: (0, 0, 2)})
        self.assertFalse(mei.protected(enemy))
        for _ in range(20):
            run.step({})
        self.assertLess(run.match.combat.vitals_of(target).total, 250.0)
        self.assertGreaterEqual(run.match.combat.frost(enemy)[0], 0.5)
        self.assertIn(area.entity, run.match.combat.blizzard.areas)

    def test_frozen_input_cannot_start_and_freeze_during_windup_prevents_launch(self):
        for running in (False, True):
            with self.subTest(running=running):
                run, owner, _enemy, _target = self.scene()
                owner.body_script.set_ult_charge(1610.0)
                if running:
                    run.step({owner: (0, 9000, 32)})
                now = run.frame * 16
                run.match.combat.frost_hits[owner.body] = combat.Frost(sample=(1.0, now, now + 1300, 0, 0))
                for _ in range(70):
                    run.step({owner: (0, 9000, 32)})
                self.assertFalse(run.match.combat.blizzard.flights)
                self.assertFalse(run.match.combat.blizzard.areas)
                if not running:
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), 1610.0)

    def test_partial_charge_refusal_passive_fill_and_repeated_cast_spends_once(self):
        run, owner, _enemy, _target = self.scene()
        body = owner.body_script
        body.ult_seconds = 0
        body.set_ult_charge(1609.0)
        for frame in range(40):
            run.step({owner: (0, 9000, 32 if frame % 2 else 0)})
        self.assertFalse(run.match.combat.blizzard.flights)
        self.assertFalse(run.match.combat.blizzard.areas)
        for _ in range(12):
            run.step({})  # Let the authored .15s button buffer expire before passive charge resumes.
        body.ult_seconds = 60.0
        for _ in range(4):
            run.step({})
        self.assertEqual(body.component.entity_var(342).value(), 1610.0)
        for frame in range(75):
            run.step({owner: (0, 9000, 32 if frame % 2 else 0)})
        self.assertEqual(len(run.match.combat.blizzard.areas), 1)
        self.assertFalse(run.match.combat.blizzard.flights)
        ultimate = next(i for i in body.component.instances.values() if i.graph.index == 0x64A)
        self.assertEqual(ultimate.state(3).counter, 1)
        self.assertGreater(body.component.entity_var(342).value(), 0.0)
        self.assertLess(body.component.entity_var(342).value(), 100.0)

    def test_passive_storage_observes_flight_area_and_per_target_deadlines(self):
        import test_server_snapshot as snapshots

        from ow174.game.server_snapshot import snapshot_digest

        fixture = snapshots.ServerSnapshotTests()
        session, _peers, players = fixture.scene(two=True)
        owner, victim = players
        manager = owner.match.combat.blizzard
        before = snapshot_digest(fixture.capture(session))
        flight = mei_blizzard.Flight(owner, owner.body, (0., 2., 0.), (0., 0., 20.), 9.8, 60., 100)
        manager.flights.append(flight)
        flying = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, flying)
        flight.velocity = (0., -1., 20.)
        gravity = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(flying, gravity)
        manager.land(flight, (0., 0., 5.), 101)
        manager.show()
        landed = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(gravity, landed)
        area = next(iter(manager.areas.values()))
        area.static_impact_normal = (0., 0., -1.)
        self.assertNotEqual(landed, snapshot_digest(fixture.capture(session)))
        area.static_impact_normal = None
        self.assertEqual(landed, snapshot_digest(fixture.capture(session)))
        area.frost_due[victim.body] = 2000
        frost = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(landed, frost)
        second_flight = mei_blizzard.Flight(owner, owner.body, (0., 2., 0.), (0., 0., 20.), 9.8, 60., 102)
        manager.flights.append(second_flight)
        manager.land(second_flight, (0., 0., 5.), 103)
        second = next(item for item in manager.areas.values() if item is not area)
        second.frost_due[victim.body] = 2200
        fight = owner.match.combat
        first_key, second_key = (owner.body, area.entity), (owner.body, second.entity)
        first_sample, second_sample = (.6, 1900, 0, 0, 0), (.5, 1900, 0, 0, 0)
        fight.frost_hits[victim.body] = first_sample
        samples = {first_key: (first_sample, False), second_key: (second_sample, False)}
        fight.frost_sources[victim.body] = samples
        overlap = snapshot_digest(fixture.capture(session))

        area.frost_due[victim.body], second.frost_due[victim.body] = 2200, 2000
        self.assertNotEqual(overlap, snapshot_digest(fixture.capture(session)))
        area.frost_due[victim.body], second.frost_due[victim.body] = 2000, 2200
        self.assertEqual(overlap, snapshot_digest(fixture.capture(session)))
        samples[first_key], samples[second_key] = samples[second_key], samples[first_key]
        self.assertNotEqual(overlap, snapshot_digest(fixture.capture(session)))
        samples[first_key], samples[second_key] = samples[second_key], samples[first_key]
        self.assertEqual(overlap, snapshot_digest(fixture.capture(session)))

        area.last_ms += 1
        self.assertNotEqual(overlap, snapshot_digest(fixture.capture(session)))


if __name__ == '__main__':
    unittest.main()
