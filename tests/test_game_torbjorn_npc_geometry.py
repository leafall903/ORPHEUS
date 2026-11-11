"""Actual five-NPC capsule boundaries and collision-backed hammer acceptance."""

import math
import os
import unittest
from pathlib import Path

import test_game_torbjorn_npcs as npc_tests
import test_game_torbjorn_turret as turret_tests

from ow174.game import mover, npc, stats
from ow174.game.collision import World
from ow174.game.commands import Command


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_MOVERS_PATH"), "needs authored five-NPC mover input")
class TorbjornNpcGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        turret_tests.TorbjornTurretTests.setUpClass()
        cls.old_movers = mover.MOVERS_PATH
        mover.MOVERS_PATH = Path(os.environ["ORPHEUS_NPC_MOVERS_PATH"])
        mover._movers.cache_clear()
        cls.catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")

    @classmethod
    def tearDownClass(cls):
        mover.MOVERS_PATH = cls.old_movers
        mover._movers.cache_clear()
        turret_tests.TorbjornTurretTests.tearDownClass()

    def arena(self, key, distance=1.5):
        fixture = npc_tests.TorbjornNpcTests()
        fixture.catalog = self.catalog
        return fixture.arena(key, distance)

    @staticmethod
    def equip_hammer(run, owner):
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        for _ in range(45):
            run.step({})

    def swing(self, run, owner):
        self.equip_hammer(run, owner)
        for _ in range(40):
            run.step({owner: (0, 0, 1)})

    def test_five_distinct_authored_capsules_reach_combat_targets(self):
        expected = {"Trooper": (1.7, .5, 5.75), "Enforcer": (1.9, .5, 5),
                    "Sniper": (1.75, .5, 4.5), "Assassin": (1.5, .5, 14),
                    "Heavy Assault": (2.3, .7, 2.2)}
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, _, actor, _ = self.arena(key)
                target = next(t for t in run.match.combat._targets() if t.owner is actor)
                data = mover.mover_data(actor.body)
                self.assertEqual((target.height, target.radius, data.run_forward), expected[definition.name])

    def test_each_actual_capsule_has_a_passing_and_failing_hammer_range_boundary(self):
        for key, definition in self.catalog.items():
            data = mover.mover_data(int(definition.body) if isinstance(definition.body, int)
                                   else int(definition.body, 16))
            vertical = max(data.stand_radius - 1.2, 1.2 - (data.stand_height - data.stand_radius), 0)
            boundary = 1.5 + math.sqrt((1.0 + data.stand_radius) ** 2 - vertical ** 2)
            for inside in (True, False):
                with self.subTest(npc=definition.name, inside=inside):
                    run, owner, _, hp = self.arena(key, boundary + (-.05 if inside else .05))
                    before = hp.total
                    self.swing(run, owner)
                    self.assertEqual(before - hp.total, 55 if inside else 0)

    def test_real_wall_occludes_all_five_with_floor_and_open_control(self):
        for key, definition in self.catalog.items():
            for blocked in (True, False):
                with self.subTest(npc=definition.name, blocked=blocked):
                    run, owner, _, hp = self.arena(key)
                    x, y, z = owner.mover.position
                    triangles = [
                        (x-100, y, z-100, x-100, y, z+100, x+100, y, z+100),
                        (x-100, y, z-100, x+100, y, z+100, x+100, y, z-100),
                    ]
                    if blocked:
                        front = z + .75
                        triangles.extend([
                            (x-3, y, front, x+3, y, front, x+3, y+4, front),
                            (x-3, y, front, x+3, y+4, front, x-3, y+4, front),
                        ])
                    run.match.collision_world = World(triangles, name="authored-npc-wall")
                    before = hp.total
                    self.swing(run, owner)
                    self.assertEqual(before - hp.total, 0 if blocked else 55)

    def test_all_five_can_enter_during_the_window_but_not_after_it(self):
        for key, definition in self.catalog.items():
            for entry in (20, 33):
                with self.subTest(npc=definition.name, entry=entry):
                    run, owner, actor, hp = self.arena(key, 5)
                    self.equip_hammer(run, owner)
                    before = hp.total
                    for _ in range(entry):
                        run.step({owner: (0, 0, 1)})
                    self.assertEqual(hp.total, before)
                    x, y, z = owner.mover.position
                    actor.position = (x, y, z + 1.5)
                    for _ in range(5):
                        run.step({})
                    self.assertEqual(before - hp.total, 55 if entry == 20 else 0)

    def test_hammer_checks_live_allegiance_without_hitting_an_npc_twice(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, owner, actor, hp = self.arena(key)
                fight = run.match.combat
                self.equip_hammer(run, owner)
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0)
                charge = owner.body_script.component.entity_var(342)
                before = hp.total
                enemy_team = actor.team
                for _ in range(19):
                    run.step({owner: (0, 0, 1)})
                self.assertEqual(hp.total, before)

                actor.team = owner.team
                run.step({owner: (0, 0, 1)})
                contact = fight.hammer_contacts[owner.body]
                self.assertLessEqual(contact.begin, owner.body_script.component.now)
                self.assertEqual(contact.hit, set())
                self.assertEqual(hp.total, before)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(charge.value(), 0)

                actor.team = enemy_team
                run.step({})
                self.assertEqual(contact.hit, {actor.entity})
                self.assertEqual(hp.total, before - 55)

                actor.team = owner.team
                run.step({})
                actor.team = enemy_team
                run.step({})
                self.assertIs(fight.hammer_contacts[owner.body], contact)
                self.assertEqual(contact.hit, {actor.entity})
                self.assertEqual(hp.total, before - 55)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 55)
                self.assertEqual(charge.value(), 55)

    def test_rivet_volleys_use_live_allegiance_at_each_npc_impact(self):
        for key, definition in self.catalog.items():
            for button, count, cost, damage in ((1, 1, 1, 70), (2, 10, 2, 125)):
                with self.subTest(npc=definition.name, button=button):
                    run, owner, bot, hp = self.arena(key, 1.5)
                    fight = run.match.combat
                    script = owner.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    rifle = next(i for i in script.component.instances.values() if i.graph.index == 0x1683)
                    charge = script.component.entity_var(342)
                    enemy_team = bot.team
                    mark = owner.entity, bot.entity
                    before = hp.total

                    self.launch_rivet_volley(run, owner, bot, button, count)
                    self.assertEqual(rifle.vars[53].value(), 18 - cost)
                    self.assertEqual(hp.total, before)
                    bot.team = owner.team
                    for _ in range(65):
                        run.step({})
                    self.assertEqual(hp.total, before)
                    self.assertNotIn(mark, fight.torbjorn_marks)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(charge.value(), 0)

                    self.launch_rivet_volley(run, owner, bot, button, count)
                    self.assertEqual(rifle.vars[53].value(), 18 - 2 * cost)
                    self.assertEqual(hp.total, before)
                    bot.team = enemy_team
                    for _ in range(20):
                        run.step({})
                    self.assertEqual(hp.total, before - damage)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                    self.assertEqual(charge.value(), damage)
                    self.assertEqual(mark in fight.torbjorn_marks, button == 1)

    def launch_rivet_volley(self, run, owner, bot, button, count):
        fight = run.match.combat
        old = tuple(fight.rockets)
        known = {id(rocket) for rocket in old}
        yaw, pitch = npc_tests.TorbjornNpcTests.body_aim(run, owner, bot)
        run.step({owner: (yaw, pitch, button)})
        for _ in range(25):
            launched = [rocket for rocket in fight.rockets if id(rocket) not in known]
            if launched:
                self.assertEqual(len(launched), count)
                self.assertTrue(all(rocket.shooter is owner and rocket.distance == 0 for rocket in launched))
                return
            run.step({})
        self.fail("rivet volley did not launch")
