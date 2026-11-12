"""Actual Graviton input against Roadhog movement and abilities."""

import unittest
from dataclasses import replace

import test_game_roadhog_defenses as fixtures

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import JUMP, Command
from ow174.game.content import heroes


class RoadhogGravitonTests(unittest.TestCase):
    def launch(self, run, zarya, position, *, held=None):
        held = held or {}
        fight = run.match.combat
        angles = fixtures.aim(fight._eye(zarya), position)
        zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
        run.step({**held, zarya: (*angles, 32)})
        for _ in range(100):
            run.step({**held, zarya: (*angles, 0)})
            if fight.gravitons.fields:
                break
        field, = fight.gravitons.fields
        return field

    def test_graviton_preserves_melee_buffer_and_moves_its_origin(self):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, zarya, victim = fixture.session("Zarya", distance=5)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, zarya.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x - 0.8, y, z + 1.5)
        victim.mover.body = None
        run.step({})
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        caster_health = fixture.health(run, zarya)
        melee = (0, 0, 0x800)
        run.step({hog: melee})
        first = run.frame
        self.assertEqual(fixture.health(run, victim), 19970)
        self.assertEqual(fixture.health(run, zarya), caster_health)
        run.step({})
        run.step({hog: melee})
        field = self.launch(run, zarya, (x + 3, y, z), held={hog: melee})
        while run.frame < first + 53:
            run.step({hog: melee})
        self.assertFalse(fight.is_stunned(hog))
        self.assertIn(hog.body, fight.gravitons.pulled)
        self.assertIn(field, fight.gravitons.fields)
        self.assertGreater(hog.mover.position[0], x + 1.2)

        # Place the caster in range of the displaced Roadhog and leave the first victim in place.
        moved_x, moved_y, moved_z = hog.mover.position
        zarya.mover.position[:] = (moved_x, moved_y, moved_z + 1.5)
        zarya.mover.body = None
        while run.frame < first + 61:
            run.step({hog: melee})
        self.assertEqual(fixture.health(run, zarya), caster_health)
        self.assertEqual(fixture.health(run, victim), 19970)
        run.step({hog: melee})
        self.assertEqual(run.frame, first + 62)
        self.assertEqual(fixture.health(run, zarya), caster_health - 30)
        self.assertEqual(fixture.health(run, victim), 19970)
        for _ in range(65):
            run.step({hog: melee})
        self.assertEqual(fixture.health(run, zarya), caster_health - 30)
        self.assertEqual(fixture.health(run, victim), 19970)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_graviton_pulls_without_commands_then_roadhog_can_cast_whole_hog(self):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, zarya, _ = fixture.session("Zarya", distance=5)
        fight = run.match.combat
        x, y, z = hog.mover.position
        before = fixture.health(run, hog)
        run.step({zarya: (-32768, 0, 32)})
        for _ in range(25):
            run.step({})
        self.assertFalse(fight.gravitons.flights)
        self.assertFalse(fight.gravitons.fields)
        field = self.launch(run, zarya, (x + 3, y, z))
        self.assertAlmostEqual(fixture.health(run, hog), before)
        self.assertEqual(field.end_tick - field.last_tick, 250)
        self.assertEqual(field.radius, 6)
        for _ in range(25):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertGreater(hog.mover.position[0], x + 0.5)
        self.assertIn(hog.body, fight.gravitons.pulled)
        self.assertFalse(fight.is_stunned(hog))
        ultimate, = hog.body_script.ultimates
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        run.step({hog: (16384, 0, 32 | JUMP)})
        self.assertTrue(ultimate.flags & 1)
        self.assertAlmostEqual(hog.mover.position[1], y, places=2)
        while fight.tick < field.end_tick:
            run.step({hog: (16384, 0, 0)})
        self.assertFalse(fight.gravitons.fields)
        self.assertNotIn(hog.body, fight.gravitons.pulled)
        self.assertTrue(ultimate.flags & 1)
        self.assertAlmostEqual(fixture.health(run, hog), before - 20)
        position = tuple(hog.mover.position)
        for _ in range(18):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            hog.client.queue_entities(run.match.remote_movements(hog))
            hog.take_commands([Command(run.frame, forward=127)])
        self.assertGreater(hog.mover.position[2], position[2])

    def test_direct_graviton_hit_deals_five_before_field_damage(self):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, zarya, _ = fixture.session("Zarya", distance=5)
        fight = run.match.combat
        x, y, z = hog.mover.position
        before = fixture.health(run, hog)
        field = self.launch(run, zarya, (x, y + 1.4, z))
        self.assertEqual(fixture.health(run, hog), before - 5)
        while fight.tick < field.end_tick:
            run.step({})
        self.assertAlmostEqual(fixture.health(run, hog), before - 25)

    def test_graviton_does_not_cancel_breather_or_block_hook(self):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, zarya, victim = fixture.session("Zarya", distance=5)
        fight = run.match.combat
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 12, y, z)
        victim.mover.body = None
        target = next(t for t in fight._targets() if t.owner is hog)
        pool = fight.vitals_of(target)
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        run.step({hog: (0, 0, 16)})
        field = self.launch(run, zarya, (x + 3, y, z))
        before = pool.total
        for _ in range(60):
            run.step({})
        self.assertIn(hog.body, fight.gravitons.pulled)
        self.assertGreater(pool.total, before)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        while fight.incoming_damage_scale(hog) == 0.5:
            run.step({})
        self.assertLess(fight.tick, field.end_tick)
        x, y, z = victim.mover.position
        angles = fixtures.aim(fight._eye(hog), (x, y + 1.2, z))
        run.step({hog: (*angles, 8)})
        for _ in range(55):
            run.step({hog: (*angles, 0)})
            if fight.hooks.controlled(victim.body):
                break
        self.assertTrue(fight.hooks.controlled(victim.body))
        self.assertIn(hog.body, fight.gravitons.pulled)

    def test_incoming_hook_takes_movement_from_graviton_then_returns_it(self):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, zarya, hooker = fixture.session("Zarya", distance=5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, zarya.team, hooker.team = 0, 1, 1
        self.assertTrue(match.switch_hero(hooker, heroes()[0x02E0000000000040]))
        x, y, z = hog.mover.position
        hooker.mover.position[:] = (x + 7, y, z)
        hooker.mover.body = None
        for _ in range(30):
            run.step({})
        field = self.launch(run, zarya, (x + 3, y, z))
        for _ in range(5):
            run.step({})
        self.assertIn(hog.body, fight.gravitons.pulled)
        angles = fixtures.aim(fight._eye(hooker), fight._eye(hog))
        run.step({hooker: (*angles, 8)})
        for _ in range(45):
            if fight.hooks.controlled(hog.body):
                break
            run.step({hooker: (*angles, 0)})
        self.assertTrue(fight.hooks.controlled(hog.body))
        self.assertNotIn(hog.body, fight.gravitons.pulled)
        self.assertGreater(hog.mover.velocity[0], 0)
        caught_x = hog.mover.position[0]
        for _ in range(75):
            if not fight.hooks.controlled(hog.body):
                break
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.hooks.controlled(hog.body))
        self.assertLess(fight.tick, field.end_tick)
        self.assertIn(hog.body, fight.gravitons.pulled)
        self.assertGreater(hog.mover.position[0], caught_x + 1)
        self.assertLess(hog.mover.velocity[0], 0)

    def test_graviton_rejects_covered_out_of_range_and_allied_roadhog(self):
        fixture = fixtures.RoadhogDefenseTests()
        for condition in ("cover", "range", "team"):
            with self.subTest(condition=condition):
                run, hog, zarya, _ = fixture.session("Zarya", distance=5)
                fight = run.match.combat
                x, y, z = hog.mover.position
                before = fixture.health(run, hog)
                field = self.launch(run, zarya, (x + 3, y, z))
                fx, fy, fz = field.position
                if condition == "cover":
                    wall = fz - 2
                    hog.mover.position[:] = (fx, fy, fz - 4)
                    hog.mover.body = None
                    run.match.collision_world = World([
                        (fx - 8, fy - 2, wall, fx + 8, fy - 2, wall, fx + 8, fy + 5, wall),
                        (fx - 8, fy - 2, wall, fx + 8, fy + 5, wall, fx - 8, fy + 5, wall),
                    ])
                elif condition == "range":
                    hog.mover.position[:] = (fx + 8, fy, fz)
                    hog.mover.body = None
                else:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = zarya.team
                for _ in range(35):
                    run.step({})
                self.assertNotIn(hog.body, fight.gravitons.pulled)
                self.assertEqual(fixture.health(run, hog), before)
