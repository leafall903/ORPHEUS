"""Actual Brigitte commands and incoming fire against Sigma's live Grasp."""

import unittest
from dataclasses import replace

import test_game_brigitte as brigitte_tests
from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box

from ow174.game import sigma_barrier
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.combat import HIT, SHIELDS_POOL, TICK
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import find_hero


class GraspTests(unittest.TestCase):
    @unittest.skipUnless(sigma_barrier.MODEL_PATH, "requires the verified build104319 C015 barrier mesh")
    def test_actual_helix_splash_reaches_exposed_mesh_edge_but_not_through_wall(self):
        for contact, offset in (("inner", 4.1), ("falloff", 5.3), ("outside", 7.1), ("wall", 4.1)):
            with self.subTest(contact=contact):
                run, brig = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Sigma", "Soldier"))
                sigma, soldier = run.players[1:]
                self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                brig.team = sigma.team = 0
                soldier.team = 1
                x, y, z = brig.mover.position
                sigma.mover.position, sigma.mover.body = [x, y, z + 6], None
                brig.mover.position, brig.mover.body = [x + offset, y, z], None
                soldier.mover.position, soldier.mover.body = [x + offset, y, z - 6], None
                for frame in range(50):
                    run.step({sigma: (-32768, 0, SECONDARY if frame < 28 else 0)})
                fight = run.match.combat
                barrier, = fight.sigma_barriers.values()
                self.assertTrue(barrier.active)
                if contact == "wall":
                    collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                      + box(x + 3.6, x + 3.7, y, y + 4, z - 4, z + 2))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, brig))
                direction = aim(fight._eye(soldier), (x + offset, y + 1.3, z))
                run.step({soldier: (*direction, SECONDARY)})
                for _ in range(36):
                    run.step({soldier: (*direction, 0)})
                self.assertEqual(hp.total, 90)
                if contact == "falloff":
                    self.assertGreater(barrier.vitals.total, 620)
                    self.assertLess(barrier.vitals.total, 660)
                else:
                    self.assertEqual(barrier.vitals.total, 620 if contact == "inner" else 700)

    @unittest.skipUnless(sigma_barrier.MODEL_PATH, "requires the verified build104319 C015 barrier mesh")
    def test_experimental_barrier_covers_brigitte_from_actual_rifle_and_breaking_helix(self):
        cases = (("rifle", FIRE, 680), ("helix", SECONDARY, 580), ("break", SECONDARY, 0),
                 ("body", FIRE, 700), ("friendly", FIRE, 700), ("wall", FIRE, 700))
        for contact, button, remaining in cases:
            with self.subTest(contact=contact):
                run, brig = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Sigma", "Soldier"))
                sigma, soldier = run.players[1:]
                self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                brig.team = sigma.team = 0
                soldier.team = 1
                x, y, z = brig.mover.position
                sigma.mover.position, sigma.mover.body = [x, y, z + 6], None
                soldier.mover.position, soldier.mover.body = [x, y, z - 6], None
                held_frames = 20 if contact == "body" else 28
                for frame in range(50):
                    run.step({sigma: (-32768, 0, SECONDARY if frame < held_frames else 0)})
                fight = run.match.combat
                barrier, = fight.sigma_barriers.values()
                self.assertTrue(barrier.active)
                if contact != "body":
                    self.assertLess(barrier.position[2], z - 0.8)
                hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, brig))
                direction = aim(fight._eye(soldier), (x, y + 1.3, z))
                if contact == "break":
                    barrier.vitals.damage(670)
                elif contact == "friendly":
                    sigma.team = soldier.team
                elif contact == "wall":
                    collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                      + box(x - 2, x + 2, y, y + 4, z - 3, z - 2.9))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                run.step({soldier: (*direction, button)})
                for _ in range(32):
                    run.step({soldier: (*direction, 0)})
                body_hit = contact in ("body", "friendly")
                self.assertEqual(hp.total, 185 if body_hit else 200)
                self.assertEqual(barrier.vitals.total, remaining)
                self.assertEqual(bool(soldier.client.messages(HIT)), body_hit)
                self.assertFalse(fight.rockets)
                if contact == "break":
                    self.assertFalse(barrier.active)
                    run.step({soldier: (*direction, FIRE)})
                    self.assertLess(hp.total, 200)

    @unittest.skipUnless(sigma_barrier.MODEL_PATH, "requires the verified build104319 C015 barrier mesh")
    def test_experimental_barrier_stops_shield_bash_travel_and_stun(self):
        for condition in ("enemy", "recalled", "friendly"):
            with self.subTest(condition=condition):
                run, brig = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Sigma", "Protected"))
                sigma, protected = run.players[1:]
                self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                brig.team, sigma.team, protected.team = 0, 1, 1
                x, y, z = brig.mover.position
                sigma.mover.position, sigma.mover.body = [x, y, z + 6], None
                protected.mover.position, protected.mover.body = [x, y, z + 4], None
                for frame in range(50):
                    run.step({sigma: (-32768, 0, SECONDARY if frame < 20 else 0)})
                fight = run.match.combat
                barrier, = fight.sigma_barriers.values()
                self.assertTrue(barrier.active)
                crossing = barrier.intersection((x, y + 1, z), (0, 0, 1), 4)
                self.assertIsNotNone(crossing)
                if condition == "recalled":
                    run.step({sigma: (-32768, 0, SECONDARY)})
                    run.step({sigma: (-32768, 0, 0)})
                    self.assertFalse(barrier.active)
                elif condition == "friendly":
                    sigma.team = brig.team
                for _ in range(20):
                    run.step({brig: (0, 0, SECONDARY)})
                hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, protected))
                before = hp.total
                run.step({brig: (0, 0, SECONDARY | FIRE)})
                for _ in range(15):
                    run.step({brig: (0, 0, SECONDARY)})
                if condition != "enemy":
                    self.assertEqual(hp.total, before - 5)
                    self.assertTrue(fight.is_stunned(protected))
                    self.assertGreater(brig.mover.position[2], z + crossing)
                else:
                    self.assertEqual(hp.total, before)
                    self.assertFalse(fight.is_stunned(protected))
                    self.assertLess(brig.mover.position[2], z + crossing)

    @unittest.skipUnless(sigma_barrier.MODEL_PATH, "requires the verified build104319 C015 barrier mesh")
    def test_flail_and_quick_melee_reach_enemy_through_experimental_barrier(self):
        for button in (FIRE, 0x800):
            with self.subTest(button=button):
                case = brigitte_tests.BrigitteTests()
                run, brig = case.fixture(("Brigitte", "Sigma", "Protected"))
                sigma, protected = run.players[1:]
                self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                brig.team, sigma.team, protected.team = 0, 1, 1
                x, y, z = brig.mover.position
                sigma.mover.position, sigma.mover.body = [x, y, z + 6], None
                protected.mover.position, protected.mover.body = [x, y, z + 4], None
                for frame in range(50):
                    run.step({sigma: (-32768, 0, SECONDARY if frame < 20 else 0)})
                fight = run.match.combat
                barrier, = fight.sigma_barriers.values()
                self.assertTrue(barrier.active)
                self.assertIsNotNone(barrier.intersection((x, y + 1, z), (0, 0, 1), 4))
                hp = fight.vitals_of(case.target(run, protected))
                sigma_hp = fight.vitals_of(case.target(run, sigma))
                before, sigma_before = hp.total, sigma_hp.total
                run.step({brig: (0, 0, button)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(hp.total, before - 35)
                self.assertEqual(sigma_hp.total, sigma_before - 35)
                self.assertTrue(barrier.active)
                self.assertEqual(barrier.vitals.total, 700)
                self.assertIn((brig.body, brig.body), fight.brigitte.inspire)

    @unittest.skipUnless(sigma_barrier.MODEL_PATH, "requires the verified build104319 C015 barrier mesh")
    def test_actual_experimental_barrier_stops_whip_until_recalled(self):
        run, brig = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Sigma"))
        sigma = run.players[1]
        self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
        x, y, z = brig.mover.position
        sigma.mover.position, sigma.mover.body = [x, y, z + 8], None
        for frame in range(50):
            run.step({sigma: (-32768, 0, SECONDARY if frame < 20 else 0)})
        fight = run.match.combat
        barrier, = fight.sigma_barriers.values()
        self.assertTrue(barrier.active)
        self.assertEqual(len(barrier.geometry.masks), 120)
        self.assertEqual(barrier.vitals.total, 700)
        hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, sigma))
        start = tuple(sigma.mover.position)
        brigitte_tests.BrigitteTests().whip(run, brig)
        self.assertEqual(barrier.vitals.total, 630)
        self.assertEqual(hp.total, 400)
        self.assertEqual(tuple(sigma.mover.position), start)
        self.assertFalse(fight.brigitte.inspire)
        run.step({sigma: (-32768, 0, SECONDARY)})
        run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(barrier.active)
        self.assertGreater(barrier.instance.states[111].remaining(), 1.9)
        run.step({sigma: (-32768, 0, SECONDARY)})
        run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(barrier.active)
        for _ in range(230):
            run.step({})
        self.assertEqual(barrier.vitals.total, 700)
        brigitte_tests.BrigitteTests().whip(run, brig)
        self.assertEqual(hp.total, 330)
        self.assertGreater(sigma.mover.position[2], start[2])
        self.assertEqual(barrier.instance.states[111].remaining(), 0)
        for frame in range(30):
            run.step({sigma: (-32768, 0, SECONDARY if frame < 20 else 0)})
        self.assertTrue(barrier.active)
        for _ in range(250):
            run.step({})
        brigitte_tests.BrigitteTests().whip(run, brig)
        self.assertEqual(barrier.vitals.total, 630)
        self.assertEqual(hp.total, 330)

    @unittest.skipUnless(sigma_barrier.MODEL_PATH, "requires the verified build104319 C015 barrier mesh")
    def test_experimental_barrier_deploy_and_recall_during_actual_whip_flight(self):
        for action in ("keep", "recall", "deploy"):
            with self.subTest(action=action):
                case = brigitte_tests.BrigitteTests()
                run, brig = case.fixture(("Brigitte", "Sigma"))
                sigma = run.players[1]
                self.assertTrue(run.match.switch_hero(sigma, find_hero("Sigma")))
                x, y, z = brig.mover.position
                sigma.mover.position, sigma.mover.body = [x, y, z + 18], None
                fight = run.match.combat
                for frame in range(50):
                    held = action != "deploy" and frame < 20
                    run.step({sigma: (-32768, 0, SECONDARY if held else 0)})
                hp = fight.vitals_of(case.target(run, sigma))
                start = tuple(sigma.mover.position)
                case.whip(run, brig, 14)
                self.assertEqual(len(fight.brigitte.whips), 1)
                self.assertEqual(hp.total, 400)
                self.assertEqual(any(barrier.active for barrier in fight.sigma_barriers.values()),
                                 action != "deploy")
                if action != "keep":
                    run.step({sigma: (-32768, 0, SECONDARY)})
                    for _ in range(5):
                        active = any(barrier.active for barrier in fight.sigma_barriers.values())
                        if active == (action == "deploy"):
                            break
                        run.step({sigma: (-32768, 0, SECONDARY if action == "deploy" else 0)})
                barrier, = fight.sigma_barriers.values()
                self.assertEqual(barrier.active, action != "recall")
                self.assertEqual(barrier.vitals.total, 700)
                self.assertEqual(len(fight.brigitte.whips), 1)
                for _ in range(40):
                    run.step({})
                self.assertFalse(fight.brigitte.whips)
                if action == "recall":
                    self.assertEqual(barrier.vitals.total, 700)
                    self.assertEqual(hp.total, 330)
                    self.assertGreater(sigma.mover.position[2], start[2] + 2)
                    self.assertIn((brig.body, brig.body), fight.brigitte.inspire)
                else:
                    self.assertEqual(barrier.vitals.total, 630)
                    self.assertEqual(hp.total, 400)
                    self.assertEqual(tuple(sigma.mover.position), start)
                    self.assertFalse(fight.brigitte.inspire)

    def scene(self, distance=3, *, health=None):
        run, brig = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Sigma", "Soldier"))
        sigma, soldier = run.players[1:]
        self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
        x, y, z = brig.mover.position
        sigma.mover.position, sigma.mover.body = [x, y, z + distance], None
        soldier.mover.position, soldier.mover.body = [x + 8, y, z], None
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, sigma))
        if health is not None:
            hp.damage(hp.total - health)
        run.step({sigma: (-32768, 0, 8)})
        instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(instance.state(1).active)
        self.assertEqual(instance.vars[21312].value(), 0)
        return run, brig, sigma, soldier, instance, hp

    def test_flail_quick_melee_whip_and_bash_bypass_grasp(self):
        for button, damage in ((FIRE, 35), (0x800, 35), (8, 70), (SECONDARY | FIRE, 5)):
            with self.subTest(button=button):
                run, brig, sigma, _, instance, hp = self.scene()
                initial = hp.total
                bash = button == SECONDARY | FIRE
                if bash:
                    for _ in range(22):
                        run.step({brig: (0, 0, SECONDARY), sigma: (-32768, 0, 0)})
                for frame in range(45):
                    run.step({brig: (0, 0, button if frame == 0 else SECONDARY if bash else 0),
                              sigma: (-32768, 0, 0)})
                    if hp.total < initial:
                        break
                self.assertEqual(hp.total, initial - damage)
                self.assertEqual(instance.vars[21312].value(), 0)
                if bash:
                    self.assertTrue(run.match.combat.is_stunned(sigma))
                else:
                    self.assertTrue(instance.state(1).active)

    def test_actual_rifle_and_helix_feed_grasp_but_earlier_wall_wins(self):
        for button, expected, wall in ((FIRE, 20, False), (SECONDARY, 120, False), (SECONDARY, 0, True)):
            with self.subTest(button=button, wall=wall):
                run, brig, sigma, soldier, instance, hp = self.scene(6)
                fight = run.match.combat
                x, y, z = brig.mover.position
                brig.mover.position, brig.mover.body = [x + 8, y, z + 4], None
                soldier.mover.position, soldier.mover.body = [x, y, z], None
                if wall:
                    collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                      + box(x - 1, x + 1, y, y + 4, z + 1, z + 1.1))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                target = brigitte_tests.BrigitteTests().target(run, sigma)
                direction = aim(fight._eye(soldier), (x, y + target.height / 2, z + 6))
                initial = hp.total
                run.step({sigma: (-32768, 0, 0), soldier: (*direction, button)})
                for _ in range(30):
                    run.step({sigma: (-32768, 0, 0), soldier: (*direction, 0)})
                self.assertEqual(hp.total, initial)
                self.assertEqual(instance.vars[21312].value(), expected)
                self.assertFalse(soldier.client.messages(HIT))
                self.assertFalse(fight.rockets)

    def test_rifle_hits_after_grasp_expiry_or_from_behind(self):
        for expire in (False, True):
            with self.subTest(expire=expire):
                run, brig, sigma, soldier, instance, hp = self.scene(6)
                fight = run.match.combat
                x, y, z = brig.mover.position
                brig.mover.position, brig.mover.body = [x + 8, y, z + 4], None
                soldier.mover.position, soldier.mover.body = [x, y, z if expire else z + 12], None
                target = brigitte_tests.BrigitteTests().target(run, sigma)
                direction = aim(fight._eye(soldier), (x, y + target.height / 2, z + 6))
                for _ in range(150 if expire else 1):
                    run.step({sigma: (-32768, 0, 0), soldier: (*direction, 0)})
                self.assertEqual(instance.state(1).active, not expire)
                initial = hp.total
                run.step({sigma: (-32768, 0, 0), soldier: (*direction, FIRE)})
                self.assertEqual(hp.total, initial - 20)
                self.assertEqual(instance.vars[21312].value(), 0)

    def test_real_grasp_exit_grants_shield_that_brigitte_spends_before_base_shields(self):
        run, brig, sigma, soldier, instance, hp = self.scene(3)
        fight = run.match.combat
        x, y, z = brig.mover.position
        brig.mover.position, brig.mover.body = [x + 8, y, z], None
        soldier.mover.position, soldier.mover.body = [x, y, z - 3], None
        target = brigitte_tests.BrigitteTests().target(run, sigma)
        direction = aim(fight._eye(soldier), (x, y + target.height / 2, z + 3))
        run.step({sigma: (-32768, 0, 0), soldier: (*direction, SECONDARY)})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0), soldier: (*direction, 0)})
        self.assertFalse(instance.state(1).active)
        self.assertEqual(instance.vars[21312].value(), 120)
        shield = hp.temporary_shield
        self.assertIsNotNone(shield)
        self.assertAlmostEqual(shield.amount, 72)
        self.assertAlmostEqual(hp.total, 472)
        self.assertEqual(hp.now[SHIELDS_POOL], 100)
        brig.mover.position, brig.mover.body = [x, y, z], None
        for _ in range(20):
            run.step({brig: (0, 0, FIRE)})
        self.assertAlmostEqual(shield.amount, 37)
        self.assertAlmostEqual(hp.total, 437)
        self.assertEqual(hp.now[SHIELDS_POOL], 100)
        self.assertEqual(hp.heal(1000), 0)
        self.assertAlmostEqual(shield.amount, 37)
        before_decay = shield.decay_from
        run.frame = round((before_decay + 2) / TICK)
        run.match.update(run.frame * TICK, run.frame)
        self.assertAlmostEqual(shield.amount, 27, delta=0.05)
        self.assertEqual(hp.now[SHIELDS_POOL], 100)
        run.frame += 600
        run.match.update(run.frame * TICK, run.frame)
        self.assertEqual(hp.total, 400)
        self.assertEqual(hp.maximum, 400)

    def test_six_actual_helix_contacts_respect_the_authored_shield_cap(self):
        run = Run(KANEZAKA_DM, ("Sigma", *(f"Soldier{i}" for i in range(6))))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        sigma, *soldiers = run.players
        self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sigma.team = 0
        for soldier in soldiers:
            soldier.team = 1
        fight = run.match.combat
        x, y, z = sigma.mover.position
        target = brigitte_tests.BrigitteTests().target(run, sigma)
        centre = (x, y + target.height / 2, z)
        directions = {}
        for index, soldier in enumerate(soldiers):
            soldier.mover.position, soldier.mover.body = [x + (index - 2.5) * 0.45, y, z - 6], None
            directions[soldier] = aim(fight._eye(soldier), centre)
        for _ in range(25):
            run.step({sigma: (-32768, 0, 0), **{s: (*d, 0) for s, d in directions.items()}})
        run.step({sigma: (-32768, 0, 8), **{s: (*d, SECONDARY) for s, d in directions.items()}})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0), **{s: (*d, 0) for s, d in directions.items()}})
        instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertEqual(instance.vars[21312].value(), 720)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.temporary_shield.amount, 400)
        self.assertEqual(vitals.total, 800)

    def test_brigitte_interrupt_and_death_cancel_the_pending_grasp_grant(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, brig, sigma, soldier, instance, hp = self.scene(health=50 if lethal else None)
                fight = run.match.combat
                x, y, z = brig.mover.position
                soldier.mover.position, soldier.mover.body = [x, y, z - 3], None
                brig.team, sigma.team, soldier.team = 1, 0, 1
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                target = brigitte_tests.BrigitteTests().target(run, sigma)
                direction = aim(fight._eye(soldier), (x, y + target.height / 2, z + 3))
                for frame in range(24):
                    run.step({sigma: (-32768, 0, 0), brig: (0, 0, 0 if lethal else SECONDARY),
                              soldier: (*direction, SECONDARY if frame == 0 else 0)})
                self.assertEqual(instance.vars[21312].value(), 120)
                self.assertTrue(instance.state(1).active)
                self.assertIsNone(hp.temporary_shield)
                pressed = 8 if lethal else SECONDARY | FIRE
                held = 0 if lethal else SECONDARY
                for frame in range(50 if lethal else 4):
                    run.step({brig: (0, 0, pressed if frame == 0 else held), sigma: (-32768, 0, 0)})
                if lethal:
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.total, 0)
                else:
                    self.assertTrue(fight.is_stunned(sigma))
                    self.assertFalse(instance.state(1).active)
                    self.assertEqual(hp.total, 395)
                    self.assertGreater(instance.vars[61].value(), 0)
                for _ in range(150):
                    run.step({})
                self.assertIsNone(hp.temporary_shield)

    def test_actual_hypersphere_pair_hits_brigitte_or_her_raised_barrier(self):
        for shield in (False, True):
            with self.subTest(shield=shield):
                run, brig = brigitte_tests.BrigitteTests().fixture()
                sigma = run.players[1]
                self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
                x, y, z = brig.mover.position
                sigma.mover.position, sigma.mover.body = [x, y, z + 6], None
                fight = run.match.combat
                hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, brig))
                brig_command = (0, 0, SECONDARY if shield else 0)
                for _ in range(25):
                    run.step({brig: brig_command})
                direction = aim(fight._eye(sigma), (x, y + 1.0, z))
                for frame in range(61):
                    run.step({brig: brig_command, sigma: (*direction, FIRE if frame == 0 else 0)})
                if shield:
                    self.assertEqual(hp.total, 200)
                    self.assertEqual(fight.brigitte.barriers[brig.body].health, 140)
                    self.assertFalse(sigma.client.messages(HIT))
                else:
                    self.assertEqual(hp.total, 105)
                    self.assertEqual(len(sigma.client.messages(HIT)), 4)
                self.assertFalse(fight.rockets)

    def test_actual_accretion_stuns_brigitte_only_on_an_unshielded_direct_hit(self):
        for shield, wall in ((False, False), (False, True), (True, False)):
            with self.subTest(shield=shield, wall=wall):
                run, brig = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Sigma", "Bystander"))
                sigma, bystander = run.players[1:]
                self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
                x, y, z = brig.mover.position
                sigma.mover.position, sigma.mover.body = [x, y, z + 6], None
                bystander.mover.position, bystander.mover.body = [x + 1.2, y, z], None
                if wall:
                    collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                      + box(x - 4, x + 4, y, y + 4, z - 1, z - 0.9))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                fight = run.match.combat
                hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, brig))
                collateral = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, bystander))
                brig_command = (0, 0, SECONDARY if shield else 0)
                for _ in range(25):
                    run.step({brig: brig_command})
                direction = aim(fight._eye(sigma), (x, y + 1.0, z))
                for frame in range(100):
                    run.step({brig: brig_command, sigma: (*direction, 16 if frame == 0 else 0)})
                    if hp.total < 200 or (shield and fight.brigitte.barriers[brig.body].health < 250):
                        break
                if shield:
                    self.assertEqual(hp.total, 200)
                    self.assertEqual(fight.brigitte.barriers[brig.body].health, 180)
                    self.assertFalse(fight.is_stunned(brig))
                    self.assertEqual(brig.mover.velocity, (0, 0, 0))
                else:
                    self.assertEqual(hp.total, 140)
                    self.assertTrue(fight.is_stunned(brig))
                    self.assertLess(collateral.total, 200)
                    self.assertFalse(fight.is_stunned(bystander))
                    self.assertGreater(bystander.mover.velocity[0], 0)
                    self.assertLess(brig.mover.velocity[2], -5)
                    self.assertGreater(brig.mover.velocity[1], 2.5)
                    self.assertTrue(brig.mover.external_impulse)
                    stun = next(lease for lease in fight.brigitte.stuns if lease.target is brig)
                    self.assertEqual(stun.instance.graph.index, 0x224E)
                    self.assertAlmostEqual(stun.instance.vars[215].value(), 0.8, delta=0.03)
                    for _ in range(52):
                        run.step({})
                    self.assertFalse(fight.is_stunned(brig))
                    if wall:
                        radius = brigitte_tests.BrigitteTests().target(run, brig).radius
                        self.assertGreaterEqual(brig.mover.position[2], z - 0.9 + radius - 0.02)
                        self.assertLess(brig.mover.position[2], z - 0.2)
                    else:
                        self.assertLess(brig.mover.position[2], z - 1)
                    self.assertFalse(brig.mover.external_impulse)
                self.assertFalse(fight.rockets)

    def test_actual_accretion_launch_bias_and_gravity(self):
        run, brig = brigitte_tests.BrigitteTests().fixture()
        sigma = run.players[1]
        self.assertTrue(run.match.switch_hero(sigma, find_hero("sigma")))
        x, y, z = sigma.mover.position
        brig.mover.position, brig.mover.body = [x + 12, y, z + 25], None
        fight = run.match.combat
        run.step({sigma: (0, 0, 16)})
        for _ in range(100):
            if fight.rockets:
                break
            run.step({})
        rocket, = fight.rockets
        self.assertEqual(rocket.gravity, 18)
        self.assertAlmostEqual(rocket.speed, 37, delta=0.05)
        self.assertGreater(rocket.velocity[1], 3)
        self.assertEqual(rocket.direct.knockback, (6, 3, ((3, 1, 1),)))
        self.assertEqual(rocket.blast.knockback[:2], (5, 1))
        height, vertical = rocket.position[1], rocket.velocity[1]
        for frame in range(1, 20):
            run.step({})
            self.assertAlmostEqual(rocket.velocity[1], vertical - frame * 18 * TICK, places=5)
        self.assertLess(rocket.velocity[1], 0)
        self.assertGreater(rocket.position[1], height)
