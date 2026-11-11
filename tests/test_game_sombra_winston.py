"""Winston weapon and sphere through actual player and Talon combat input."""

import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
import test_game_sombra_grenade as grenades
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge
from test_game_sombra_visibility import tracker

from ow174.game import combat, npc_control, winston_combat
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.runtime import send_instance_message
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


def prepare(f, distance=3):
    f.b.spawned = True
    assert f.run.match.switch_hero(f.b, heroes()[winston_combat.HERO])
    place(f.a, (0, 0, 0))
    place(f.b, (0, 0, distance))
    f.b.body_script.ult_seconds = 0
    f.b.body_script.set_ult_charge(0)
    f.press(0, 55)
    return f.run.match.combat


def drive(f, button, frames=1, point=(0, 1.4, 0)):
    for _ in range(frames):
        f.run.step({f.b: (*aim(f.run.match.combat._eye(f.b), point), button)})


def domes(fight):
    return [barrier for barrier in fight.barriers.values() if isinstance(barrier, winston_combat.Dome)]


class SombraWinstonTests(unittest.TestCase):
    def arena(self, distance=3):
        f = scenes.SombraCombatTests()
        f.setUp()
        return f, prepare(f, distance)

    def test_tesla_damage_charge_and_release(self):
        f, _ = self.arena()
        drive(f, 1, 32)
        lost = 200 - f.health(f.a)
        self.assertAlmostEqual(lost, 32 * .96)
        self.assertAlmostEqual(charge(f.b), lost, places=4)
        drive(f, 0, 20)
        self.assertAlmostEqual(f.health(f.a), 200 - lost)

    def test_tesla_ammo_is_owned_by_the_original_reload_graph(self):
        f, _ = self.arena()
        weapon = next(i for i in f.b.body_script.component.instances.values() if i.graph.index == 0xFE)
        self.assertEqual(weapon.vars[53].value(), 100)
        drive(f, 2, 20)
        self.assertEqual(weapon.vars[53].value(), 100)
        drive(f, 1, 20)
        ammo = weapon.vars[53].value()
        self.assertLess(ammo, 100)
        drive(f, 0, 2)
        self.assertEqual(weapon.vars[53].value(), ammo)
        drive(f, 0x400)
        self.assertTrue(weapon.vars[51].value())
        drive(f, 0, 110)
        self.assertFalse(weapon.vars[51].value())
        self.assertEqual(weapon.vars[53].value(), 100)

    def test_tesla_hits_two_enemies_in_one_frame_and_one_dome_only_once(self):
        f, fight = self.arena(7)
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        ally = grenades.SombraGrenadeTests().join_ally(f, winston_combat.HERO, (1, 0, 0))
        ally.team = f.a.team
        f.run.step({ally: (0, 0, 16)})
        drive(f, 0, 6)
        dome, = domes(fight)
        drive(f, 1, 10)
        self.assertAlmostEqual(dome.health, 700 - 10 * .96)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(f.health(ally), 500)
        self.assertEqual(charge(f.b), 0)

        # Put both targets on the same side of the shell as the attacker.
        place(f.a, (0, 0, 5.5))
        place(ally, (1, 0, 5.5))
        drive(f, 0, 10)
        before = dome.health
        drive(f, 1, 24, (0.5, 1.4, 5.5))
        lost = 200 - f.health(f.a)
        self.assertGreater(lost, 0)
        self.assertAlmostEqual(500 - f.health(ally), lost * .8)
        self.assertAlmostEqual(dome.health, before - lost)

    def test_tesla_rejects_wall_range_rear_and_allied_body(self):
        for case in ('wall', 'range', 'rear', 'ally'):
            with self.subTest(case=case):
                f, _ = self.arena(11 if case == 'range' else 3)
                if case == 'wall':
                    f.wall()
                if case == 'ally':
                    match = f.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = f.b.team
                drive(f, 1, 34, (0, 1.4, 12) if case == 'rear' else (0, 1.4, 0))
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(charge(f.b), 0)

    def test_tesla_interrupts_sombra_hack_and_respects_cloak_damage(self):
        f, fight = self.arena()
        f.press(8, 75)
        self.assertTrue(tracker(f.a).active)
        drive(f, 1, 4)
        self.assertLess(f.health(f.a), 200)
        self.assertFalse(tracker(f.a).active)
        f.press(0, 1)
        f.press(2, 20)
        drive(f, 1, 5)
        f.press(2, 50)
        self.assertNotIn(f.b.body, fight.hacks)

    def test_dome_actual_deployment_blocks_gun_and_hack_then_emp_removes_it(self):
        f, fight = self.arena(7)
        drive(f, 16)
        self.assertFalse(domes(fight))
        drive(f, 0, 5)
        dome, = domes(fight)
        self.assertEqual(dome.health, 700)

        def image():
            stored = _Graph({"match": f.run.match, "winston": f.b}, None)
            roots = {"dome": stored.value(dome)}
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": roots, "nodes": stored.finish(), "assets": []})

        before = image()
        f.press(1, 18)
        self.assertNotEqual(image(), before)
        self.assertLess(dome.health, 700)
        self.assertEqual(f.health(f.b), 500)
        f.press(2, 60)
        self.assertNotIn(f.b.body, fight.hacks)
        f.a.body_script.set_ult_charge(1400)
        f.press(32, 60)
        self.assertFalse(dome.active())
        self.assertFalse(domes(fight))
        self.assertIn(f.b.body, fight.hacks)
        f.press(1, 18)
        self.assertLess(f.health(f.b), 500)

    def test_dome_same_side_and_inside_to_outside(self):
        f, fight = self.arena(3)
        drive(f, 16)
        drive(f, 0, 6)
        dome, = domes(fight)
        f.press(1, 20)
        self.assertEqual(dome.health, 700)
        self.assertLess(f.health(f.b), 500)
        place(f.b, (0, 0, 10))
        f.press(1, 20)
        self.assertLess(dome.health, 700)
        self.assertEqual(dome.position, (0, .1, 3))

    def test_world_only_deployment_keeps_the_stepped_release_and_expiry(self):
        results = []
        for idle in (False, True):
            with self.subTest(idle=idle):
                f, fight = self.arena(7)
                drive(f, 16)
                accepted = f.run.frame
                self.assertFalse(domes(fight))
                if idle:
                    f.run.frame += 30
                    f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                else:
                    drive(f, 0, 30)
                dome, = domes(fight)
                results.append((dome.expiry_tick - accepted, dome.health, dome.position))
                drive(f, 16)
                drive(f, 0, 5)
                self.assertEqual(domes(fight), [dome])
                f.run.frame = dome.expiry_tick - 1
                f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                self.assertEqual(domes(fight), [dome])
                f.run.frame += 1
                f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                self.assertFalse(domes(fight))
                f.run.frame = accepted + 850
                f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                drive(f, 16)
                drive(f, 0, 5)
                fresh, = domes(fight)
                self.assertNotEqual(fresh.entity, dome.entity)
        self.assertEqual(results[0], results[1])

    def test_idle_reload_refills_before_unlock_and_discards_late_input(self):
        f, fight = self.arena()
        drive(f, 1, 20)
        drive(f, 0, 2)
        drive(f, 0x400)
        weapon = winston_combat.weapon_instance(f.b.body_script)
        accepted = f.run.frame
        health = f.health(f.a)
        charge_before = charge(f.b)
        position = tuple(f.b.mover.position)
        f.run.frame = accepted + 79
        f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
        self.assertEqual(weapon.vars[53].value(), 100)
        self.assertTrue(weapon.vars[51].value())
        f.run.frame = accepted + 106
        f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
        self.assertEqual(weapon.vars[53].value(), 100)
        self.assertFalse(weapon.vars[51].value())
        f.b.take_commands([Command(accepted + 78, buttons=1, forward=127)])
        self.assertEqual(tuple(f.b.mover.position), position)
        self.assertEqual(f.health(f.a), health)
        self.assertEqual(charge(f.b), charge_before)
        self.assertEqual(weapon.vars[53].value(), 100)
        yaw, pitch = aim(fight._eye(f.b), (0, 1.4, 0))
        f.b.take_commands([Command(f.run.frame, yaw=yaw, pitch=pitch, buttons=1)])
        self.assertAlmostEqual(f.health(f.a), health - .96)
        self.assertAlmostEqual(charge(f.b), charge_before + .96, places=4)
        self.assertLess(weapon.vars[53].value(), 100)

    def test_lethal_sombra_shot_prevents_pending_idle_deployment(self):
        f, fight = self.arena(7)
        victim = next(t for t in fight._targets() if t.owner is f.b)
        vitals = fight.vitals_of(victim)
        vitals.now[ARMOUR_POOL] = 0
        vitals.now[HEALTH_POOL] = 1
        drive(f, 16)
        self.assertTrue(winston_combat.idle_pending(f.b.body_script))
        f.press(1)
        self.assertTrue(fight.is_dead(f.b))
        self.assertEqual(vitals.killer, f.a.body)
        f.run.frame += 30
        f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
        self.assertFalse(domes(fight))

    def test_empty_magazine_completes_automatic_reload_while_idle(self):
        f, _ = self.arena()
        weapon = winston_combat.weapon_instance(f.b.body_script)
        for _ in range(400):
            drive(f, 1, point=(0, 1.4, 12))
            if weapon.vars[51].value():
                break
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertTrue(weapon.vars[51].value())
        f.run.frame += 115
        f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
        self.assertEqual(weapon.vars[53].value(), 100)
        self.assertFalse(weapon.vars[51].value())
        self.assertEqual(f.health(f.a), 200)
        drive(f, 1, 1)
        self.assertAlmostEqual(f.health(f.a), 199.04)

    def test_dome_expires_and_cooldown_rejects_early_recast(self):
        f, fight = self.arena(7)
        drive(f, 16)
        drive(f, 0, 6)
        first, = domes(fight)
        drive(f, 16)
        drive(f, 0, 8)
        self.assertEqual(domes(fight), [first])
        f.run.frame = first.expiry_tick
        f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
        self.assertFalse(domes(fight))


class SombraWinstonNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_actual_ranged_npc_volleys_hit_dome_instead_of_winston(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = npc_fixture.SombraActualNpcTests.make(self, key)
                fight = prepare(f, 7)
                place(f.a, (12, 0, 0))
                bot.position = (0, 0, 0)
                drive(f, 16)
                drive(f, 0, 6)
                dome, = domes(fight)
                if key == "3":
                    special = next(i for i in bot.body_script.component.instances.values()
                                   if i.graph.index == 0x142C)
                    self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)
                self.assertEqual(npc_control.start(f.run.match, f.b), bot.entity)
                for _ in range(220):
                    drive(f, 0)
                    if dome.health < 700:
                        break
                self.assertLess(dome.health, 700)
                self.assertEqual(f.health(f.b), 500)
                self.assertEqual(charge(f.b), 0)

    def test_tesla_hits_all_five_actual_npcs_with_continuous_armour(self):
        self.assertEqual(len(self.catalog), 5)
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = npc_fixture.SombraActualNpcTests.make(self, key)
                fight = prepare(f)
                place(f.a, (10, 0, 0))
                bot.position = (0, 0, 0)
                vitals = fight.vitals[bot.entity]
                armoured = vitals.now[ARMOUR_POOL] > 0
                before = vitals.total
                drive(f, 1, 16)
                lost = before - vitals.total
                self.assertAlmostEqual(lost, 16 * .96 * (.8 if armoured else 1))
                self.assertAlmostEqual(charge(f.b), lost, places=4)
                self.assertEqual(f.health(f.a), 200)
