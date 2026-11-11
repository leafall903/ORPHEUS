"""Actual Icicle input, trajectory, hit regions and defenses against Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer_mei_frost as frost_tests
import test_game_tracer_mei_wall as wall_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, deflection, sigma_grasp, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import CROUCH
from ow174.game.content import SOLDIER, find_hero, heroes
from ow174.game.script import expr


class TracerMeiIcicleTests(unittest.TestCase):
    def setUp(self):
        self.case = frost_tests.TracerMeiFrostTests()
        self.case.setUp()
        self.run, self.fight = self.case.run, self.case.fight
        self.tracer, self.mei = self.case.tracer, self.case.mei

    def aim_at_tracer(self, height):
        x, y, z = self.tracer.mover.position
        self.case.aim = aim(self.fight._eye(self.mei), (x, y + height, z))

    def fire(self, frames=45, *, buttons=0):
        for frame in range(frames):
            self.case.step(spray=2 if frame < 20 else 0, buttons=buttons)

    def notice(self):
        notice, = self.mei.client.messages(combat.HIT)
        return notice["+0x78"]

    def test_actual_body_contact_deals_75_without_frost(self):
        self.aim_at_tracer(0.9)
        self.fire()
        self.assertEqual(self.case.health.total, 75)
        self.assertEqual(self.notice()["+0x4"], 75)
        self.assertEqual(self.notice()["+0x8"], 0)
        self.assertIsNone(self.case.frost())

    def test_actual_release_spends_ten_ammo_and_fresh_press_reuses(self):
        self.aim_at_tracer(0.9)
        instance = next(i for i in self.mei.body_script.component.instances.values()
                        if i.graph.index == 0x05E6)
        ammo = instance.find_var(expr.INSTANCE, 53)
        self.assertEqual(ammo.value(), 120)
        self.fire(frames=60)
        self.assertEqual(ammo.value(), 110)
        self.assertEqual(self.case.health.total, 75)
        self.fire(frames=60)
        self.assertEqual(ammo.value(), 100)
        self.assertTrue(self.case.health.dead)
        self.assertEqual(len(self.mei.client.messages(combat.HIT)), 2)

    def test_actual_head_contact_deals_150_and_reports_critical_kill(self):
        self.aim_at_tracer(1.55)
        self.fire()
        self.assertTrue(self.case.health.dead)
        self.assertEqual(self.notice()["+0x4"], 150)
        self.assertEqual(self.notice()["+0x8"], combat.CRITICAL | combat.KILLING)
        self.assertIsNone(self.case.frost())

    def test_ice_wall_and_allied_team_block_actual_hit(self):
        wall_case = wall_tests.TracerMeiWallTests()
        wall_case.setUp()
        wall = wall_case.place()
        x, y, z = wall_case.tracer.mover.position
        wall_case.fixture.aim = aim(wall_case.fight._eye(wall_case.mei), (x, y + 1.55, z))
        for frame in range(50):
            wall_case.step(spray=2 if frame < 20 else 0)
        self.assertEqual(wall_case.fixture.health.total, 150)
        self.assertEqual(wall.health, 400)
        self.assertFalse(wall_case.fight.rockets)

        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team = self.mei.team
        self.aim_at_tracer(1.55)
        self.fire()
        self.assertEqual(self.case.health.total, 150)
        self.assertFalse(self.mei.client.messages(combat.HIT))

    def launch_distant(self, height=1.55, *, buttons=0):
        self.tracer.mover.position[2] -= 14
        self.tracer.mover.body = None
        self.aim_at_tracer(height)
        for frame in range(40):
            self.case.step(spray=2 if frame < 20 else 0, buttons=buttons)
            if self.fight.rockets:
                break
        rocket, = self.fight.rockets
        self.assertEqual(rocket.definition, combat.MEI_ICICLE)
        self.assertEqual(rocket.speed, 115)
        return rocket

    def test_icicle_resolves_crouched_body_critical_height_and_static_cover(self):
        for gate, height, damage, flags in (
            ("body", 0.45, 75, 0),
            ("critical", 0.875, 150, combat.CRITICAL | combat.KILLING),
            ("high miss", 1.567, 0, 0),
            ("wall", 0.875, 0, 0),
        ):
            with self.subTest(gate=gate):
                self.setUp()
                x, y, z = self.tracer.mover.position
                if gate == "wall":
                    floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                    mesh = World(floor + wall(x - 4, z + 1.5, x + 4, z + 1.5, y, y + 3))
                    self.run.match.collision_provider = StaticCollisionProvider(mesh)
                    self.run.match.collision_world = mesh
                    for actor in self.run.players:
                        actor.mover.use(world=mesh)
                for _ in range(8):
                    self.case.step(buttons=CROUCH)
                self.assertTrue(self.tracer.mover.crouched)
                self.aim_at_tracer(height)
                self.fire(buttons=CROUCH)
                self.assertEqual(self.case.health.total, 150 - damage)
                self.assertIsNone(self.case.frost())
                if damage:
                    hit = self.notice()
                    self.assertEqual((hit["+0x4"], hit["+0x8"]), (damage, flags))
                else:
                    self.assertFalse(self.mei.client.messages(combat.HIT))

    def test_in_flight_icicle_uses_tracers_contact_stance(self):
        for crouch_at_contact in (False, True):
            with self.subTest(crouch_at_contact=crouch_at_contact):
                self.setUp()
                initial = 0 if crouch_at_contact else CROUCH
                rocket = self.launch_distant(height=1.567, buttons=initial)
                self.assertEqual(self.tracer.mover.crouched, bool(initial))
                held = CROUCH if crouch_at_contact else 0
                self.case.step(buttons=held)
                self.assertEqual(self.tracer.mover.crouched, crouch_at_contact)
                self.assertIn(rocket, self.fight.rockets)
                for _ in range(24):
                    self.case.step(buttons=held)
                self.assertEqual(self.case.health.total, 150 if crouch_at_contact else 0)
                if crouch_at_contact:
                    self.assertFalse(self.mei.client.messages(combat.HIT))
                else:
                    self.assertEqual(self.notice()["+0x8"], combat.CRITICAL | combat.KILLING)

    def test_actual_in_flight_icicle_misses_after_lateral_blink(self):
        self.launch_distant()
        x = self.tracer.mover.position[0]
        self.case.step(buttons=8, yaw=16384)
        self.assertGreater(self.tracer.mover.position[0] - x, 5)
        for _ in range(24):
            self.case.step()
        self.assertEqual(self.case.health.total, 150)
        self.assertFalse(self.mei.client.messages(combat.HIT))

    def test_actual_recall_rejects_icicle_contact(self):
        rocket = self.launch_distant()
        self.case.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        for _ in range(12):
            self.case.step()
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertIn(rocket, self.fight.rockets)
        self.assertLess(rocket.position[2], self.tracer.mover.position[2])
        self.assertEqual(self.case.health.total, 150)
        self.assertFalse(self.mei.client.messages(combat.HIT))

    def test_actual_tracer_kill_before_release_cancels_icicle(self):
        self.aim_at_tracer(1.55)
        self.case.case.health(self.mei).damage(249)
        self.case.step(spray=2, buttons=1)
        self.assertTrue(self.fight.is_dead(self.mei))
        for _ in range(45):
            self.case.step()
            self.assertFalse(self.fight.rockets)
        self.assertEqual(self.case.health.total, 150)

    def test_actual_tracer_kill_after_release_preserves_icicle_without_charge(self):
        script = self.mei.body_script
        script.ult_seconds = 0
        script.set_ult_charge(100)
        rocket = self.launch_distant(0.9)
        self.case.case.health(self.mei).damage(249)
        self.case.step(buttons=1)
        self.assertTrue(self.fight.is_dead(self.mei))
        self.assertIn(rocket, self.fight.rockets)
        self.assertEqual(self.case.health.total, 150)
        for _ in range(24):
            self.case.step()
        self.assertNotIn(rocket, self.fight.rockets)
        self.assertEqual(self.case.health.total, 75)
        self.assertEqual(script.component.entity_var(342).value(), 100)

    def test_hero_replacement_retires_actual_in_flight_icicle(self):
        rocket = self.launch_distant()
        self.assertTrue(self.run.match.switch_hero(self.mei, heroes()[SOLDIER]))
        self.assertNotIn(rocket, self.fight.rockets)
        for _ in range(24):
            self.case.step()
        self.assertEqual(self.case.health.total, 150)

    def defense(self, hero, button, *, mei_back=4):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team, self.mei.team = 0, 1
        defender, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=hero)
        if hero == 0x02E000000000007A:
            mech = replace(find_hero("dva"), body=0x0400000000000407)
            self.assertTrue(self.run.match.switch_hero(defender, mech))
        defender.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        self.mei.mover.position[2] += mei_back
        self.mei.mover.body = None
        defender.mover.position = [x, y, z + 3]
        defender.mover.body = None
        for _ in range(110 if hero == 0x02E000000000007A else 25):
            self.run.step({})
        self.aim_at_tracer(1.3)
        facing = aim(self.fight._eye(defender), (x, y + 1.3, z + 3 + mei_back))
        self.run.step({defender: (*facing, button)})
        rockets = {}
        for frame in range(55):
            self.run.step({self.mei: (*self.case.aim, 2 if frame < 20 else 0), defender: (*facing, button)})
            for rocket in self.fight.rockets:
                rockets[id(rocket)] = rocket
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.case.health.total, 150)
        rocket, = rockets.values()
        return defender, rocket

    def test_actual_winston_barrier_takes_base_damage_before_tracer(self):
        defender, rocket = self.defense(0x02E0000000000009, 16, mei_back=8)
        self.assertIs(rocket.shooter, self.mei)
        bubble = self.fight.winston_barriers.actors[defender.body]
        self.assertEqual(bubble.health, 625)
        self.assertEqual(self.case.case.health(defender).total, 500)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile tags")
    def test_actual_deflect_reassigns_icicle_and_hits_mei(self):
        defender, rocket = self.defense(0x02E0000000000029, 16)
        self.assertIs(rocket.shooter, defender)
        self.assertEqual(self.case.case.health(self.mei).total, 175)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile tags")
    def test_actual_grasp_absorbs_icicle_before_tracer(self):
        defender, rocket = self.defense(sigma_grasp.HERO, 8)
        instance = next(i for i in defender.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        self.assertIs(rocket.shooter, self.mei)
        self.assertEqual(instance.vars[21312].value(), 75)
        self.assertEqual(self.case.case.health(defender).total, 400)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile tags")
    def test_actual_matrix_absorbs_icicle_before_tracer(self):
        defender, rocket = self.defense(0x02E000000000007A, 2)
        self.assertIs(rocket.shooter, self.mei)
        self.assertEqual(self.case.case.health(defender).total, 600)


if __name__ == "__main__":
    unittest.main()
