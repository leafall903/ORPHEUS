"""Actual Winston deployment and Reinhardt attacks against its shell."""

import math
import unittest
from dataclasses import replace

import test_delivered_reinhardt as fixtures
from test_game_combat import aim, in_world

from ow174.game import stats
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import SOLDIER, heroes

WINSTON = 0x02E0000000000009


def dome_ray(dome, origin, direction, radius=0.0):
    hit = dome.owner.match.combat.barriers.world.trace(
        dome.owner, origin, direction, math.inf, radius, bubbles=(dome,),
    )
    return None if hit is None else hit[0]


class ReinhardtWinstonBarrierTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health
    advance = fixtures.HammerTests.advance

    def deploy(self, distance, *, allied=False):
        fixtures.HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[WINSTON]))
        if allied:
            self.match.game_map = replace(self.match.game_map, free_for_all=False)
            self.enemy.team = self.rein.team
            self.other.team = self.rein.team + 1
        self.place(self.enemy, (0, 1, distance))
        self.place(self.other, (12, 1, 0))
        self.fight = self.match.combat
        self.advance(20, buttons=0)
        self.run.step({self.enemy: (-32768, 0, 16)})
        for _ in range(15):
            self.run.step({})
            if self.fight.barriers.world.actors:
                break
        [dome] = self.fight.barriers.world.actors.values()
        self.assertEqual(dome.health, 700)
        self.assertEqual((0x0580000000000000 | next(i for i in dome.component.instances.values() if i.graph.index == 0xFC).graph.index), 0x05800000000000FC)
        return dome

    def test_shield_bash_ignores_its_allied_deployment(self):
        dome = self.deploy(8)
        fixtures.shield_bash_at_deployment(self, dome, dome.point[2] - 10, 1, allied=True)

    def test_shield_bash_stays_stopped_after_dome_naturally_expires(self):
        dome = self.deploy(8)
        instance = next(i for i in dome.component.instances.values() if i.graph.index == 0xFC)
        duration, = instance.find_var(0, 567).value()
        expiry = dome.component.frame + math.ceil(duration * 1000 / dome.component.frame_ms)
        fixtures.shield_bash_at_deployment(self, dome, dome.point[2] - 10, 1, expiry=expiry)
        self.assertTrue(dome.retired)

    def test_actual_hammer_damages_near_shell_once_without_reaching_body(self):
        dome = self.deploy(7)
        for button in (1, 0x800):
            self.advance(45, buttons=button)
            self.advance(25, buttons=0)
        self.assertEqual(dome.health, 530)
        self.assertEqual(self.health(self.enemy), 500)
        self.assertEqual(self.rein.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_fire_strike_damages_one_shell_once_and_penetrates(self):
        dome = self.deploy(7)
        self.advance(1, buttons=16)
        self.advance(65, buttons=0)
        self.assertEqual(dome.health, 600)
        self.assertEqual(self.health(self.enemy), 405)

    def test_actual_earthshatter_stops_at_shell_even_when_it_breaks(self):
        dome = self.deploy(8)
        self.fight.barriers.world.damage(dome, 660)
        self.rein.body_script.set_ult_charge(1540)
        self.advance(1, buttons=32)
        self.advance(70, buttons=0)
        self.assertEqual(dome.health, 0)
        self.assertTrue(dome.retired)
        self.assertEqual(self.health(self.enemy), 500)
        self.assertFalse(fixtures.controlled(self.fight, self.enemy.body))

    def test_actual_earthshatter_inside_dome_reaches_body_without_crossing_shell(self):
        dome = self.deploy(8)
        self.place(self.rein, (0, 1, 4))
        self.rein.body_script.set_ult_charge(1540)
        self.advance(1, buttons=32)
        self.advance(55, buttons=0)
        self.assertEqual(self.health(self.enemy), 205)
        self.assertTrue(fixtures.controlled(self.fight, self.enemy.body))
        self.assertEqual(dome.health, 650)  # The fronts reach the far shell after the body hit.



    def test_expiring_dome_only_blocks_earthshatter_rays_that_reached_it(self):
        self.check_removed_dome_fronts('expiry')

    def test_rifle_breaks_dome_between_earthshatter_ray_contacts(self):
        self.check_removed_dome_fronts('rifle')

    def test_dome_broken_by_earthshatter_blocks_later_rays(self):
        self.check_removed_dome_fronts('wave')

    def check_removed_dome_fronts(self, removal):
        initial_health = {'expiry': 700, 'rifle': 60, 'wave': 40}[removal]
        dome = self.deploy(11.5)
        angle = math.radians(25)
        dx, dz = math.sin(angle), math.cos(angle)
        self.place(self.other, (14 * dx, 1, 14 * dz))
        late_contact = dome_ray(dome, (0, 1.1, 0), (dx, 0, dz))
        self.assertGreater(late_contact, 9)
        if removal == 'rifle':
            self.match.game_map = replace(self.match.game_map, free_for_all=False)
            self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
            rifle = self.match.add_player(4, 'Rifle support', SOLDIER, 0, False)
            in_world(self.match, rifle)
            self.assertTrue(self.match.switch_hero(rifle, heroes()[SOLDIER]))
            self.place(rifle, (8, 1, 11.5))
            self.run.players.append(rifle)
            self.advance(20, buttons=0)
        elif removal == 'expiry':
            self.advance(520, buttons=0)
        self.fight.barriers.world.damage(dome, 700 - initial_health)
        self.rein.body_script.set_ult_charge(1540)
        self.advance(1, buttons=32)
        for _ in range(60):
            self.advance(1, buttons=0)
            if dome.health < initial_health:
                break
        wave = self.fight.shatters[self.rein.body]
        self.assertEqual(dome.health, max(initial_health - 50, 0))
        self.assertIn(50, wave.blocked)
        self.assertNotIn(100, wave.blocked)
        stopped = wave.fronts[50]

        for _ in range(8):
            if dome.retired:
                break
            if removal == 'rifle':
                angles = aim(self.fight._eye(rifle), (0, 2.1, 11.5))
                self.run.step({rifle: (*angles, 1)})
            else:
                self.advance(1, buttons=0)
        self.assertTrue(dome.retired)
        self.assertEqual(dome.body in wave.dome_poses, removal == 'wave')
        self.assertLess(wave.travelled, late_contact)
        self.advance(28, buttons=0)
        self.assertEqual(wave.fronts[50], stopped)
        self.assertEqual(100 in wave.blocked, removal == 'wave')
        self.assertEqual(self.health(self.enemy), 500)
        self.assertEqual(self.health(self.other), 200 if removal == 'wave' else 150)
        self.assertEqual(fixtures.controlled(self.fight, self.other.body), removal != 'wave')

    def test_actual_charge_crosses_shell_pins_and_wall_impacts_without_shell_damage(self):
        dome = self.deploy(7)
        fixtures.charge_wall(self, 10)
        self.advance(1, buttons=8)
        for _ in range(100):
            self.advance(1, buttons=0)
            if self.health(self.enemy) < 500:
                break
        self.assertEqual(self.health(self.enemy), 205)
        self.assertEqual(dome.health, 700)
        self.assertFalse(self.fight.charge_contacts)

    def test_dome_expiry_after_winston_leaves_and_vm_records_shell_damage(self):
        dome = self.deploy(7)
        before = snapshot_digest(export_component_vm(dome.component))
        self.advance(45, buttons=1)
        self.assertEqual(dome.health, 615)
        self.assertNotEqual(snapshot_digest(export_component_vm(dome.component)), before)
        self.place(self.enemy, (12, 1, 7))
        self.advance(490, buttons=0)
        self.assertEqual(dome.point, (0, 1.1, 7))
        self.assertIn(dome.body, self.fight.barriers.world.actors)
        self.advance(30, buttons=0)
        self.assertTrue(dome.retired)
        self.assertNotIn(dome.body, self.fight.barriers.world.actors)
        self.assertFalse(dome.component.instances)

    def test_static_cover_blocks_actual_hammer_fire_strike_and_shatter(self):
        dome = self.deploy(7)
        fixtures.charge_wall(self, 1)
        self.advance(45, buttons=1)
        self.advance(25, buttons=0)
        self.advance(1, buttons=16)
        self.advance(55, buttons=0)
        self.rein.body_script.set_ult_charge(1540)
        self.advance(1, buttons=32)
        self.advance(65, buttons=0)
        self.assertEqual(dome.health, 700)
        self.assertEqual(self.health(self.enemy), 500)
        self.assertFalse(fixtures.controlled(self.fight, self.enemy.body))


    def test_allied_dome_allows_fire_strike_to_hit_enemy_inside(self):
        dome = self.deploy(7, allied=True)
        self.place(self.enemy, (3, 1, 7))
        self.place(self.other, (0, 1, 7))
        self.advance(1, buttons=16)
        self.advance(65, buttons=0)
        self.assertEqual(dome.health, 700)
        self.assertEqual(self.health(self.other), 100)


    def test_actual_fire_strike_kills_owner_and_leaves_dome_live(self):
        dome = self.deploy(7)
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        self.fight.vitals_of(target).damage(450)
        self.advance(1, buttons=16)
        self.advance(65, buttons=0)
        self.assertEqual(self.health(self.enemy), 0)
        self.assertTrue(self.fight.is_dead(self.enemy))
        self.assertIn(dome.body, self.fight.barriers.world.actors)
        self.assertEqual(dome.health, 600)
        self.assertFalse(dome.retired)
