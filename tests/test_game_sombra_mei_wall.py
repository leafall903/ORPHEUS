"""Actual 0622 Ice Wall placement against Sombra and all five Talon bodies."""

import unittest
from dataclasses import replace

import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_mei as mei_fixture
import test_game_sombra_npc as npc_fixture
from test_game_combat import SOLDIER, aim
from test_game_sombra_orisa_halt import place

from ow174.game import combat, npc_control, stats
from ow174.game.collision_fingerprint import world_fingerprint
from ow174.game.commands import FIRE, Command
from ow174.game.script.runtime import send_instance_message
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest


def tap(fixture, actor, button, facing):
    fixture.run.step({actor: (*facing, button)})
    fixture.run.step({actor: (*facing, 0)})


def deploy(fixture, point=(0, 0, 3), *, rotated=False):
    fight = fixture.run.match.combat
    caster = fixture.b
    facing = aim(fight._eye(caster), point)
    tap(fixture, caster, 16, facing)
    if rotated:
        tap(fixture, caster, 2, facing)
    tap(fixture, caster, FIRE, facing)
    return tuple(fight.walls.pieces.values())


def fire_at(fixture, point, button=FIRE):
    fight = fixture.run.match.combat
    fixture.run.step({fixture.a: (*aim(fight._eye(fixture.a), point), button)})


class SombraIceWallTests(unittest.TestCase):
    def arena(self):
        return mei_fixture.SombraMeiTests().arena(distance=7)

    def test_original_preview_rotation_pillars_and_cooldown_do_not_fire_weapon(self):
        for rotated in (False, True):
            with self.subTest(rotated=rotated):
                fixture = self.arena()
                match = fixture.run.match
                fight = match.combat
                fingerprint = world_fingerprint(match.collision_world)
                instances = fixture.b.body_script.component.instances
                weapon = next(i for i in instances.values() if i.graph.index == 0x5E6)
                wall = fight.walls.instance(fixture.b)
                pieces = deploy(fixture, rotated=rotated)
                self.assertEqual(len(pieces), 5)
                self.assertEqual([piece.health for piece in pieces], [400] * 5)
                self.assertEqual({piece.owner for piece in pieces}, {fixture.b.body})
                self.assertEqual(weapon.vars[53].value(), 120)
                self.assertGreater(wall.vars[61].value(), 11.9)
                self.assertAlmostEqual(abs(pieces[-1].feet[2] - pieces[0].feet[2]), 6.2 if rotated else 0)
                self.assertAlmostEqual(abs(pieces[-1].feet[0] - pieces[0].feet[0]), 0 if rotated else 6.2)
                tap(fixture, fixture.b, 16, (0, 0))
                self.assertFalse(fight.walls.pieces)
                deploy(fixture, rotated=rotated)
                self.assertFalse(fight.walls.pieces)
                self.assertEqual(world_fingerprint(match.collision_world), fingerprint)

    def test_pistol_and_melee_hit_pillar_break_opens_new_shot_only(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        centre = deploy(fixture)[2]
        fixture.press(FIRE)
        self.assertEqual(centre.health, 392)
        self.assertEqual(fixture.health(fixture.b), 250)
        self.assertEqual(fixture.a.stats.value(stats.HERO_DAMAGE), 0)
        x, y, z = centre.feet
        place(fixture.a, (x, y, z - 1.6))
        fire_at(fixture, (x, y + 1, z), 0x800)
        fixture.press(0, 35)
        self.assertEqual(centre.health, 362)
        centre.health = 8  # Isolate the boundary between the breaking and following rounds.
        fire_at(fixture, (0, 1, 7))
        self.assertNotIn(centre.entity, fight.walls.pieces)
        self.assertEqual(fixture.health(fixture.b), 250)
        fixture.press(0, 25)
        fire_at(fixture, (0, 1, 7))
        self.assertEqual(fixture.health(fixture.b), 242)

    def test_hack_and_emp_obey_wall_then_reacquire_after_cancel(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        pieces = deploy(fixture)
        fixture.press(2, 80)
        self.assertFalse(fight.hacks)
        self.assertEqual(fixture.weapon().state(53).remaining(), 0)
        fixture.press(0)
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(32)
        fixture.press(0, 65)
        self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.hacks)
        self.assertTrue(all(p.health == 400 for p in pieces))
        tap(fixture, fixture.b, 16, (0, 0))
        self.assertFalse(fight.walls.pieces)
        fixture.press(2, 80)
        self.assertIn(fixture.b.body, fight.hacks)

    def test_player_capsule_stops_at_wall_and_can_cross_after_expiry(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        wall = deploy(fixture)[2]

        def walk(frames):
            for _ in range(frames):
                run = fixture.run
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                fixture.a.take_commands([Command(run.frame, forward=127)])
                fixture.b.take_commands([Command(run.frame)])

        walk(70)
        self.assertLess(fixture.a.mover.position[2], wall.feet[2] - wall.depth)
        self.assertGreater(fixture.a.mover.position[2], 1)
        fixture.run.frame = wall.expires
        fixture.run.match.update(wall.expires * combat.TICK, wall.expires)
        self.assertFalse(fight.walls.pieces)
        walk(55)
        self.assertGreater(fixture.a.mover.position[2], wall.feet[2] + wall.depth)

    def test_invalid_confirmation_cancel_and_friendly_collision(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        sky = (0, -10000)
        tap(fixture, fixture.b, 16, sky)
        tap(fixture, fixture.b, FIRE, sky)
        self.assertTrue(fight.walls.inputs[fixture.b.body].preview)
        self.assertFalse(fight.walls.pieces)
        self.assertEqual(fight.walls.instance(fixture.b).vars[61].value(), 0)
        tap(fixture, fixture.b, 16, sky)
        self.assertFalse(fight.walls.inputs[fixture.b.body].preview)
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team = fixture.b.team = 0
        pieces = deploy(fixture)
        self.assertEqual(len(pieces), 5)
        fixture.press(FIRE)
        self.assertEqual([piece.health for piece in pieces], [400] * 5)
        self.assertIsNotNone(fight.walls.cast((0, 1, 0), (0, 0, 1), 7))

    def test_actual_helix_wall_contact_protects_sombra_from_splash(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                fixture = self.arena()
                match = fixture.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                fixture.a.team = fixture.b.team = 0
                centre = deploy(fixture)[2]
                soldier = grenade_fixture.SombraGrenadeTests().join_ally(fixture, SOLDIER, (0, 0, 9))
                soldier.team = 1
                place(fixture.b, (6, 0, 7))
                facing = aim(fight._eye(soldier), (0, 1, 0))
                fixture.run.step({soldier: (*facing, 2)})
                self.assertTrue(fight.rockets)
                if gap:
                    fixture.run.frame = centre.expires + 1
                    match.update(fixture.run.frame * combat.TICK, fixture.run.frame)
                    self.assertFalse(fight.walls.pieces)
                else:
                    fixture.press(0, 24)
                self.assertFalse(fight.rockets)
                self.assertEqual(centre.health, 280)
                self.assertEqual(fixture.health(fixture.a), 200)

    def test_recall_crosses_wall_but_refuses_destination_inside_pillar(self):
        for intersect in (False, True):
            with self.subTest(intersect=intersect):
                fixture = self.arena()
                fight = fixture.run.match.combat
                place(fixture.b, (5, 0, 7))
                for _ in range(20):
                    fixture.run.step({fixture.a: (0, 0, 16)})
                fixture.press(0, 180)
                beacon, = fight.translocators.entries.values()
                self.assertTrue(beacon.settled)
                x, _, z = beacon.position
                wall = deploy(fixture, (x, 0, z if intersect else 3))
                self.assertTrue(wall)
                fixture.press(16)
                fixture.press(0, 25)
                if intersect:
                    self.assertLess(fixture.a.mover.position[2], 1)
                else:
                    self.assertFalse(fight.translocators.entries)
                    self.assertAlmostEqual(fixture.a.mover.position[2], z, places=2)

    def test_wall_state_digest_and_owner_replacement(self):
        fixture = self.arena()
        match = fixture.run.match
        walls = match.combat.walls
        pieces = deploy(fixture)

        def digest():
            store = _Graph({"combat": match.combat}, None)
            roots = {"walls": store.value(walls)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": store.finish(), "assets": [],
            })

        initial = digest()
        self.assertEqual(digest(), initial)
        pieces[2].expires += 1
        self.assertNotEqual(digest(), initial)
        self.assertTrue(match.switch_hero(fixture.b, fixture.b.hero))
        self.assertFalse(walls.pieces)


class NpcIceWallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_wall_protects_all_five_actual_npcs_from_sombra_pistol_and_emp(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=6)
                match = fixture.run.match
                fight = match.combat
                fixture.b.spawned = True
                self.assertTrue(match.switch_hero(fixture.b, mei_fixture.scene.heroes()[mei_fixture.MEI]))
                place(fixture.a, (0, 0, 0))
                place(fixture.b, (4, 0, 6))
                fixture.press(0, 45)
                pieces = deploy(fixture)
                self.assertEqual(len(pieces), 5)
                health = fight.vitals[bot.entity].total
                npc_fixture.SombraActualNpcTests.press(fixture, bot, FIRE)
                self.assertEqual(fight.vitals[bot.entity].total, health)
                self.assertTrue(any(piece.health < 400 for piece in pieces))
                fixture.a.body_script.set_ult_charge(1400)
                npc_fixture.SombraActualNpcTests.press(fixture, bot, 32)
                fixture.press(0, 55)
                self.assertNotIn(bot.entity, fight.hacks)
                tap(fixture, fixture.b, 16, (0, 0))
                npc_fixture.SombraActualNpcTests.press(fixture, bot, FIRE)
                self.assertEqual(fight.vitals[bot.entity].total, health - 8)

    def test_four_actual_npc_guns_wait_for_wall_to_clear_then_hit_sombra(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=6)
                match = fixture.run.match
                fixture.b.spawned = True
                self.assertTrue(match.switch_hero(fixture.b, mei_fixture.scene.heroes()[mei_fixture.MEI]))
                place(fixture.a, (0, 0, 0))
                place(fixture.b, (4, 0, 6))
                fixture.press(0, 45)
                npc_fixture.SombraActualNpcTests.press(fixture, bot, 0)
                self.assertEqual(npc_control.start(match, fixture.a), bot.entity)
                self.assertTrue(deploy(fixture))
                before = fixture.health(fixture.a)
                fixture.press(0, 38)
                self.assertFalse(bot.attack_input)
                self.assertEqual(fixture.health(fixture.a), before)
                if key == "3":
                    special = next(i for i in bot.body_script.component.instances.values()
                                   if i.graph.index == 0x142C)
                    send_instance_message(special, 0x0240000000000A45, {}, bot.entity)
                tap(fixture, fixture.b, 16, (0, 0))
                for _ in range(380):
                    fixture.press(0)
                    if fixture.health(fixture.a) < before:
                        break
                self.assertLess(fixture.health(fixture.a), before)
