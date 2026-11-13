"""Actual held shield/weapon input with a reconstructed missing-Pet consumer."""

import unittest
from unittest.mock import patch

import test_game_brigitte as brigitte_tests

from ow174.game.brigitte import HERO
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import find_hero, heroes
from ow174.game.mover import Mover


class BarrierTests(unittest.TestCase):
    def fixture(self):
        run, player = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Shooter", "Protected"))
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        shooter, protected = run.players[1:]
        shooter.team, protected.team = 1 - player.team, player.team
        x, y, z = player.mover.position
        shooter.mover.position, protected.mover.position = [x, y, z + 10], [x, y, z - 3]
        self.raise_shield(run, player, 20)
        return run, player, shooter, protected

    def raise_shield(self, run, player, frames):
        for _ in range(frames):
            run.step({player: (0, 0, SECONDARY)})

    def health(self, run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is owner)).total

    def test_actual_orisa_protective_barrier_blocks_whip_and_bash(self):
        from dataclasses import replace
        from pathlib import Path

        from ow174.game.body_vm_snapshot import export_component_vm

        mesh = Path(__file__).resolve().parents[3] / "013E" / "orisa_geometry_174.json"
        if not mesh.is_file():
            self.skipTest("verified build 104319 Protective Barrier mesh required")
        self.enterContext(patch.dict("os.environ", {"ORPHEUS_ORISA_GEOMETRY": str(mesh)}))
        normal_expiry = None
        for attack in ("whip", "bash", "break", "expiry", "scheduler_gap", "friendly"):
            with self.subTest(attack=attack):
                case = brigitte_tests.BrigitteTests()
                names = ("Brigitte", "Orisa", "Enemy") if attack == "friendly" else ("Brigitte", "Orisa")
                run, brig = case.fixture(names)
                fight = run.match.combat
                orisa = run.players[1]
                self.assertTrue(run.match.switch_hero(orisa, find_hero("Orisa")))
                if attack == "friendly":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    brig.team = orisa.team = 0
                    run.players[2].team = 1
                x, y, z = brig.mover.position
                orisa.mover.position, orisa.mover.body = [x, y, z + 8], None
                for _ in range(25):
                    run.step({})
                run.step({orisa: (-32768, 0, 16)})
                flight_seen = False
                for _ in range(120):
                    deployment = next((r for r in fight.rockets if r.deploy_body == orisa.body), None)
                    flight_seen |= deployment is not None
                    if (attack == "scheduler_gap" and deployment is not None
                            and deployment.last_tick is not None):
                        run.frame += 80
                        run.match.update(run.frame * .016, run.frame)
                        break
                    if fight.orisa_barriers.active:
                        break
                    run.step({})
                self.assertTrue(flight_seen)
                [shield] = fight.orisa_barriers.active.values()
                self.assertEqual((shield.script.graph.index, shield.health), (0x0BD8, 600))
                self.assertEqual(shield.component.entity_var(567).value(), 20)
                export_component_vm(shield.component)
                if attack == "whip":
                    normal_expiry = shield.until
                if attack == "scheduler_gap":
                    self.assertEqual(shield.until, normal_expiry)
                    continue
                x, y, z = shield.position
                brig.mover.position, brig.mover.body = [x, y, z - 4], None
                orisa.mover.position, orisa.mover.body = [x, y, z + 3], None
                if attack == "friendly":
                    enemy = run.players[2]
                    orisa.mover.position, orisa.mover.body = [x + 5, y, z + 3], None
                    enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
                    before = self.health(run, enemy)
                    case.whip(run, brig)
                    self.assertEqual(self.health(run, enemy), before - 70)
                    self.assertEqual(shield.health, 600)
                    continue
                if attack == "break":
                    fight.orisa_barriers.damage(shield, 550)
                if attack == "expiry":
                    run.frame = shield.until
                    run.match.update(run.frame * .016, run.frame)
                    self.assertNotIn(shield.entity, fight.orisa_barriers.active)
                before = self.health(run, orisa)
                if attack == "bash":
                    self.raise_shield(run, brig, 25)
                    self.bash(run, brig)
                    self.assertLess(brig.mover.position[2], z + 1)
                    self.assertFalse(fight.is_stunned(orisa))
                else:
                    case.whip(run, brig)
                self.assertEqual(self.health(run, orisa), before - (65 if attack == "expiry" else 0))
                self.assertEqual(shield.health, {"whip": 530, "break": 0}.get(attack, 600))
                if attack == "break":
                    self.assertNotIn(shield.entity, fight.orisa_barriers.active)
                    self.assertFalse(fight.brigitte.inspire)

    def test_winston_dome_intercepts_only_when_attack_crosses_live_shell(self):
        for attack, distance, wait in (("whip", 8, False), ("whip", 3, False),
                                       ("whip", 8, True), ("bash", 6, False)):
            with self.subTest(attack=attack, distance=distance, wait=wait):
                case = brigitte_tests.BrigitteTests()
                run, brig = case.fixture()
                winston = run.players[1]
                self.assertTrue(run.match.switch_hero(winston, find_hero("Winston")))
                x, y, z = brig.mover.position
                winston.mover.position, winston.mover.body = [x, y, z + distance], None
                hold = SECONDARY if attack == "bash" else 0
                for _ in range(25):
                    run.step({brig: (0, 0, hold)})
                run.step({brig: (0, 0, hold), winston: (-32768, 0, 16)})
                fight = run.match.combat
                for _ in range(20):
                    run.step({brig: (0, 0, hold)})
                dome = fight.winston_domes[winston.body]
                self.assertEqual(dome.health, 700)
                if wait:
                    for _ in range(570):
                        run.step({})
                    self.assertNotIn(winston.body, fight.winston_domes)
                hp = fight.vitals_of(case.target(run, winston))
                before = hp.total
                if attack == "whip":
                    case.whip(run, brig)
                else:
                    self.bash(run, brig)
                    self.assertLess(brig.mover.position[2], z + 1.1)
                blocked = distance > 5 and not wait
                self.assertEqual(before - hp.total, 0 if blocked else 65)
                self.assertEqual(dome.health, 630 if blocked and attack == "whip" else 700)
                self.assertFalse(fight.is_stunned(winston))

    def test_winston_dome_allows_friendly_whip_and_break_does_not_spill(self):
        from dataclasses import replace

        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                case = brigitte_tests.BrigitteTests()
                run, brig = case.fixture(("Brigitte", "Winston", "Enemy"))
                winston, enemy = run.players[1:]
                self.assertTrue(run.match.switch_hero(winston, find_hero("Winston")))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                brig.team, winston.team, enemy.team = 0, 0 if friendly else 1, 1
                x, y, z = brig.mover.position
                winston.mover.position, winston.mover.body = [x, y, z + 8], None
                enemy.mover.position, enemy.mover.body = [x, y, z + 11], None
                for _ in range(25):
                    run.step({})
                run.step({winston: (-32768, 0, 16)})
                for _ in range(20):
                    run.step({})
                fight = run.match.combat
                dome = fight.winston_domes[winston.body]
                wearer_hp = fight.vitals_of(case.target(run, winston))
                enemy_hp = fight.vitals_of(case.target(run, enemy))
                wearer_before, enemy_before = wearer_hp.total, enemy_hp.total
                if not friendly:
                    dome.health = 50
                case.whip(run, brig)
                self.assertEqual(wearer_hp.total, wearer_before)
                self.assertEqual(enemy_hp.total, enemy_before - (70 if friendly else 0))
                if friendly:
                    self.assertEqual(dome.health, 700)
                else:
                    self.assertEqual(dome.health, 0)
                    self.assertNotIn(winston.body, fight.winston_domes)
                    self.assertFalse(fight.brigitte.inspire)
                    for _ in range(255):
                        run.step({})
                    case.whip(run, brig)
                    self.assertEqual(wearer_hp.total, wearer_before - 65)

    def test_reinhardt_field_blocks_bash_and_whip_until_lowered(self):
        from ow174.game.reinhardt_barrier import configured_geometry

        if configured_geometry() is None:
            self.skipTest("requires the prepared 104319 Reinhardt barrier mesh")
        attacks = (("bash", 4, 2.5, 0), ("whip", 8, 65, 70))
        for attack, distance, body_damage, field_damage in attacks:
            for raised in (False, True):
                with self.subTest(attack=attack, raised=raised):
                    case = brigitte_tests.BrigitteTests()
                    run, brig = case.fixture()
                    rein = run.players[1]
                    self.assertTrue(run.match.switch_hero(rein, find_hero("Reinhardt")))
                    x, y, z = brig.mover.position
                    rein.mover.position, rein.mover.body = [x, y, z + distance], None
                    hold = SECONDARY if attack == "bash" else 0
                    for _ in range(25):
                        run.step({brig: (0, 0, hold), rein: (-32768, 0, SECONDARY)})
                    fight = run.match.combat
                    barrier = fight.reinhardt_barriers[rein.body]
                    self.assertTrue(barrier.active)
                    if not raised:
                        for _ in range(20):
                            run.step({brig: (0, 0, hold), rein: (-32768, 0, 0)})
                        self.assertFalse(barrier.active)
                    hp = fight.vitals_of(case.target(run, rein))
                    before, shield_before = hp.total, barrier.current
                    button = (SECONDARY | FIRE) if attack == "bash" else 8
                    stunned = False
                    for frame in range(55):
                        run.step({brig: (0, 0, button if frame == 0 else hold),
                                  rein: (-32768, 0, SECONDARY if raised else 0)})
                        stunned |= fight.is_stunned(rein)
                    self.assertEqual(hp.total, before - (0 if raised else body_damage))
                    self.assertEqual(barrier.current, shield_before - (field_damage if raised else 0))
                    self.assertEqual(stunned, not raised and attack == "bash")
                    if raised:
                        self.assertFalse(fight.brigitte.inspire)
                        self.assertLess(brig.mover.position[2], z + distance - 0.4)
                    if raised and attack == "whip":
                        for _ in range(100):
                            run.step({rein: (-32768, 0, 0)})
                        self.assertFalse(barrier.active)
                        self.assertEqual(barrier.current, 1530)
                        for _ in range(65):
                            run.step({rein: (-32768, 0, 0)})
                        self.assertEqual(barrier.current, 1600)

    def test_enemy_barrier_shield_stops_bash_before_wearer(self):
        run, player, enemy, _ = self.fixture()
        self.assertTrue(run.match.switch_hero(enemy, find_hero("Brigitte")))
        x, y, z = player.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        for _ in range(20):
            run.step({player: (0, 0, SECONDARY), enemy: (-32768, 0, SECONDARY)})
        fight = run.match.combat
        held = fight.brigitte.barriers[enemy.body]
        self.assertTrue(fight.brigitte._barrier_active(enemy, held))
        before = self.health(run, enemy)
        run.step({player: (0, 0, SECONDARY | FIRE), enemy: (-32768, 0, SECONDARY)})
        for _ in range(15):
            run.step({player: (0, 0, SECONDARY), enemy: (-32768, 0, SECONDARY)})
        self.assertEqual(self.health(run, enemy), before)
        self.assertFalse(fight.is_stunned(enemy))
        self.assertLess(player.mover.position[2], z + 2.5)

    def test_bash_contact_with_enemy_barrier_ends_the_cast_before_barrier_drops(self):
        run, player, enemy, _ = self.fixture()
        self.assertTrue(run.match.switch_hero(enemy, find_hero("Brigitte")))
        x, y, z = player.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 2], None
        hold = {player: (0, 0, SECONDARY), enemy: (-32768, 0, SECONDARY)}
        for _ in range(20):
            run.step(hold)
        fight = run.match.combat
        barrier = fight.brigitte.barriers[enemy.body]
        self.assertTrue(fight.brigitte._barrier_active(enemy, barrier))
        run.step({**hold, player: (0, 0, SECONDARY | FIRE)})
        dash = fight.brigitte.barriers[player.body].instance.states[14]
        self.assertTrue(dash.active)
        for _ in range(8):
            run.step(hold)
            if player.mover.position[2] > z and player.mover.velocity[2] == 0:
                break
        self.assertGreater(player.mover.position[2], z)
        self.assertEqual(player.mover.velocity[2], 0)
        self.assertFalse(dash.active)
        contact = player.mover.position[2]
        before = self.health(run, enemy)
        for _ in range(16):
            run.step({player: (0, 0, SECONDARY), enemy: (-32768, 0, 0)})
        self.assertFalse(fight.brigitte._barrier_active(enemy, barrier))
        self.assertFalse(dash.active)
        self.assertEqual(player.mover.position[2], contact)
        self.assertEqual(self.health(run, enemy), before)
        self.assertFalse(fight.is_stunned(enemy))

    def test_bash_reaches_enemy_when_held_barrier_lowers_before_contact(self):
        run, player, enemy, _ = self.fixture()
        self.assertTrue(run.match.switch_hero(enemy, find_hero("Brigitte")))
        x, y, z = player.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 4], None
        hold = {player: (0, 0, SECONDARY), enemy: (-32768, 0, SECONDARY)}
        for _ in range(20):
            run.step(hold)
        fight = run.match.combat
        guard = fight.brigitte.barriers[enemy.body]
        self.assertTrue(fight.brigitte._barrier_active(enemy, guard))
        before = self.health(run, enemy)
        run.step({**hold, player: (0, 0, SECONDARY | FIRE)})
        dash = fight.brigitte.barriers[player.body].instance.states[14]
        self.assertTrue(dash.active)
        run.step({player: (0, 0, SECONDARY), enemy: (-32768, 0, 0)})
        self.assertFalse(fight.brigitte._barrier_active(enemy, guard))
        self.assertTrue(dash.active)
        self.assertEqual(self.health(run, enemy), before)
        for _ in range(18):
            run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.health(run, enemy), before - 2.5)
        self.assertTrue(fight.is_stunned(enemy))
        self.assertFalse(dash.active)

    def test_bash_authored_movement_runs_without_walk_input_then_stops(self):
        run, player, *_ = self.fixture()
        x, _y, z = player.mover.position
        run.step({player: (0, 0, SECONDARY | FIRE)})
        self.assertEqual(player.mover.position[2], z)  # mods begin next posed frame
        self.raise_shield(run, player, 9)
        self.assertGreater(player.mover.position[2] - z, 4)
        self.assertAlmostEqual(player.mover.position[0], x, places=2)
        self.assertEqual(player.mover.throttles, (0, 0))  # raw command remains idle
        self.raise_shield(run, player, 35)
        end = tuple(player.mover.position)
        self.assertEqual(player.mover.velocity, (0.0, 0.0, 0.0))
        run.step({player: (0, 0, SECONDARY | FIRE)})  # still in native seven-second cooldown
        self.raise_shield(run, player, 20)
        self.assertEqual(tuple(player.mover.position), end)

    def test_bash_throttle_scope_starts_and_ends_on_next_pose_and_keeps_raw_input(self):
        for lateral in (-127, 0, 127):
            with self.subTest(lateral=lateral):
                run, player, *_ = self.fixture()
                observed = {}
                original = Mover._accelerate

                def accelerate(mover, dt, right, forward, axes, grounded, *,
                               player=player, observed=observed, run=run, original=original):
                    if mover is player.mover:
                        observed[run.frame] = (right, forward)
                    return original(mover, dt, right, forward, axes, grounded)

                with patch.object(Mover, "_accelerate", accelerate):
                    activation = run.frame + 1
                    run.step({player: (0, 0, SECONDARY | FIRE)})
                    mod = next(m for m in player.mover.mods.values() if m.index == 17)
                    self.assertEqual(mod.start, activation + 1)
                    self.assertEqual(observed[activation], (0, 0))
                    for _ in range(100):
                        run.frame += 1
                        run.match.update(run.frame * 0.016, run.frame)
                        player.take_commands([Command(run.frame, forward=-127, right=lateral,
                                                      buttons=SECONDARY)])
                        self.assertEqual(player.mover.throttles, (lateral, -127))
                        self.assertEqual(player.body_script.world.throttles, (-127, lateral))
                        if mod.end is not None and run.frame >= mod.end + 5:
                            break
                self.assertIsNotNone(mod.end)
                self.assertEqual(observed[mod.start], (0, 1))
                self.assertEqual(observed[mod.end - 1], (0, 1))
                self.assertEqual(observed[mod.end], (lateral / 127, -1))
                self.assertEqual(player.mover.input_throttles, (lateral, -127))

    def test_bash_forward_wish_uses_existing_capsule_wall_collision(self):
        from test_game_mover import box

        run, player, *_ = self.fixture()
        x, y, z = player.mover.position
        world = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                      + box(x - 5, x + 5, y, y + 5, z + 2, z + 2.5))
        run.match.collision_provider = StaticCollisionProvider(world)
        run.match.collision_world = world
        player.mover.world = world
        player.mover.body = None
        run.step({player: (0, 0, SECONDARY | FIRE)})
        self.raise_shield(run, player, 30)
        self.assertGreater(player.mover.position[2] - z, 1)
        self.assertLess(player.mover.position[2] - z, 2)

    def test_primary_without_shield_cannot_start_bash_movement(self):
        run, player = brigitte_tests.BrigitteTests().fixture()
        start = tuple(player.mover.position)
        for _ in range(30):
            run.step({player: (0, 0, FIRE)})
        self.assertEqual(tuple(player.mover.position), start)

    def bash(self, run, player, frames=35):
        run.step({player: (0, 0, SECONDARY | FIRE)})
        self.raise_shield(run, player, frames - 1)

    def test_bash_hits_one_enemy_once_for_five_noncritical_damage(self):
        run, player, enemy, _ = self.fixture()
        x, y, z = player.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        host = run.match.combat.knockbacks
        with patch.object(host, "apply", wraps=host.apply) as impulse:
            self.bash(run, player, frames=2)
        self.assertEqual(impulse.call_count, 1)
        self.assertEqual(impulse.call_args.args[0].entity, enemy.body)
        self.assertEqual(tuple(impulse.call_args.args[1]), (0.0, 1.0, 3.0))
        barrier = run.match.combat.brigitte.barriers[player.body]
        self.assertFalse(barrier.instance.states[14].active)  # authored488 transition ends the dash
        self.raise_shield(run, player, 33)
        self.assertEqual(self.health(run, enemy), 195)
        self.assertGreater(enemy.mover.position[2], z + 3)
        hits = player.client.messages(25001)
        self.assertEqual([(v["+0x78"]["+0x4"], v["+0x78"]["+0x8"]) for v in hits], [(5, 0)])
        self.assertFalse(run.match.combat.brigitte.inspire)  # no flail/whip proc

    def test_bash_cannot_cleave_or_damage_later_tracker_replacements(self):
        run, player, first, second = self.fixture()
        x, y, z = player.mover.position
        second.team = first.team
        first.mover.position, second.mover.position = [x, y, z + 2.5], [x, y, z + 4]
        first.mover.body = second.mover.body = None
        self.bash(run, player)
        self.assertEqual(self.health(run, first), 195)
        self.assertEqual(self.health(run, second), 200)

    def test_bash_damage_reopens_only_after_authored_cooldown(self):
        run, player, enemy, _ = self.fixture()
        x, y, z = player.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        self.bash(run, player)
        player.mover.position, player.mover.body = [x, y, z], None
        self.bash(run, player)
        self.assertEqual(self.health(run, enemy), 195)
        self.raise_shield(run, player, 450)
        player.mover.position, player.mover.body = [x, y, z], None
        self.bash(run, player)
        self.assertEqual(self.health(run, enemy), 190)

    def test_bash_rejects_allies_rear_side_and_beyond_travel(self):
        for offset, allied in (((0, 0, 3), True), ((0, 0, -3), False),
                               ((4, 0, 0), False), ((0, 0, 10), False)):
            with self.subTest(offset=offset, allied=allied):
                run, player, enemy, _ = self.fixture()
                x, y, z = player.mover.position
                enemy.team = player.team if allied else 1 - player.team
                enemy.mover.position = [x + offset[0], y + offset[1], z + offset[2]]
                enemy.mover.body = None
                self.bash(run, player)
                self.assertEqual(self.health(run, enemy), 200)

    def test_bash_authored_degree_cone_rejects_near_lateral_and_rear_diagonal_targets(self):
        for dx, dz in ((1.5, 0.5), (-1.5, 0.5), (1.0, -0.5), (-1.0, -0.5)):
            with self.subTest(offset=(dx, dz)):
                run, player, enemy, _ = self.fixture()
                x, y, z = player.mover.position
                enemy.mover.position, enemy.mover.body = [x + dx, y, z + dz], None
                self.bash(run, player)
                self.assertEqual(self.health(run, enemy), 200)
                self.assertFalse(player.client.messages(25001))

    def test_bash_hits_armor_for_two_and_a_half_and_credits_only_effective_damage(self):
        run, player, enemy, _ = self.fixture()
        run.match.switch_hero(enemy, heroes()[HERO])
        x, y, z = player.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        self.bash(run, player)
        self.assertEqual(self.health(run, enemy), 197.5)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 2.5)

    def test_bash_static_wall_occludes_target_even_inside_tracker_radius(self):
        from test_game_mover import box

        run, player, enemy, _ = self.fixture()
        x, y, z = player.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 2], None
        wall = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                     + box(x - 5, x + 5, y, y + 5, z + 0.8, z + 1))
        run.match.collision_provider = StaticCollisionProvider(wall)
        run.match.collision_world = player.mover.world = wall
        player.mover.body = None
        self.bash(run, player)
        self.assertEqual(self.health(run, enemy), 200)

    def test_death_after_bash_start_cancels_contact_damage(self):
        run, player, enemy, _ = self.fixture()
        x, y, z = player.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        run.step({player: (0, 0, SECONDARY | FIRE)})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        self.raise_shield(run, player, 20)
        self.assertEqual(self.health(run, enemy), 200)

    def rocket(self, run, player, shooter, yaw=-32768, pitch=0):
        run.step({player: (0, 0, SECONDARY), shooter: (yaw, pitch, SECONDARY)})
        self.assertEqual(len(run.match.combat.rockets), 1)
        self.raise_shield(run, player, 70)
        self.assertFalse(run.match.combat.rockets)

    def test_real_helix_projectile_and_splash_damage_shield_for120(self):
        run, player, shooter, protected = self.fixture()
        self.rocket(run, player, shooter)
        self.assertEqual(run.match.combat.brigitte.barriers[player.body].health, 130)
        self.assertEqual(self.health(run, player), 200)
        self.assertEqual(self.health(run, protected), 200)

    def test_helix_break_does_not_spill_the_same_explosion_through_shield(self):
        run, player, shooter, protected = self.fixture()
        barrier = run.match.combat.brigitte.barriers[player.body]
        barrier.health = 30
        run.match.combat.brigitte._sync_barrier(barrier)
        self.rocket(run, player, shooter)
        self.assertEqual(barrier.health, 1)
        self.assertEqual(self.health(run, player), 200)
        self.assertEqual(self.health(run, protected), 200)

    def test_rear_helix_and_splash_bypass_directional_shield(self):
        run, player, shooter, protected = self.fixture()
        x, y, z = player.mover.position
        shooter.mover.position, protected.mover.position = [x, y, z - 10], [x + 8, y, z]
        shooter.mover.body = protected.mover.body = None
        self.rocket(run, player, shooter, yaw=0)
        self.assertEqual(run.match.combat.brigitte.barriers[player.body].health, 250)
        self.assertEqual(self.health(run, player), 90)  #40+80, two armor reductions

    def test_friendly_helix_passes_shield_and_hits_enemy_beyond_it(self):
        run, player, shooter, enemy = self.fixture()
        shooter.team, enemy.team = player.team, 1 - player.team
        self.rocket(run, player, shooter)
        self.assertEqual(run.match.combat.brigitte.barriers[player.body].health, 250)
        self.assertEqual(self.health(run, enemy), 80)
        self.assertEqual(self.health(run, player), 200)

    def test_front_floor_explosion_covers_owner_but_not_exposed_side_ally(self):
        from test_game_combat import aim

        run, player, shooter, exposed = self.fixture()
        x, y, z = player.mover.position
        exposed.mover.position, exposed.mover.body = [x + 2.5, y, z], None
        yaw, pitch = aim(run.match.combat._eye(shooter), (x + 1.5, y, z + 1.5))
        self.rocket(run, player, shooter, yaw, pitch)
        self.assertLess(run.match.combat.brigitte.barriers[player.body].health, 250)
        self.assertEqual(self.health(run, player), 200)
        self.assertLess(self.health(run, exposed), 200)

    def test_static_wall_occludes_helix_splash_to_exposed_side_ally(self):
        from test_game_combat import aim
        from test_game_mover import box

        run, player, shooter, exposed = self.fixture()
        x, y, z = player.mover.position
        exposed.mover.position, exposed.mover.body = [x + 2.5, y, z], None
        wall = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                     + box(x + 2, x + 2.1, y, y + 5, z - 2, z + 4))
        run.match.collision_provider = StaticCollisionProvider(wall)
        run.match.collision_world = wall
        yaw, pitch = aim(run.match.combat._eye(shooter), (x + 1.5, y, z + 1.5))
        self.rocket(run, player, shooter, yaw, pitch)
        self.assertLess(run.match.combat.brigitte.barriers[player.body].health, 250)
        self.assertEqual(self.health(run, player), 200)
        self.assertEqual(self.health(run, exposed), 200)

    def test_enemy_rifle_hits_shield_without_damaging_hero(self):
        run, player, shooter, _protected = self.fixture()
        run.step({player: (0, 0, SECONDARY), shooter: (-32768, 0, FIRE)})
        barrier = run.match.combat.brigitte.barriers[player.body]
        self.assertEqual(barrier.health, 230)
        self.assertEqual(barrier.instance.vars[586].value(), 230)
        self.assertEqual(self.health(run, player), 200)
        self.assertFalse(shooter.client.messages(25001))

    def test_shield_covers_an_ally_outside_owners_capsule(self):
        run, player, shooter, protected = self.fixture()
        x, y, z = player.mover.position
        shooter.mover.position, protected.mover.position = [x + 0.75, y, z + 10], [x + 0.75, y, z - 3]
        shooter.mover.body = protected.mover.body = None  # fixture teleport invalidates cached contacts
        run.step({player: (0, 0, SECONDARY), shooter: (-32768, 0, FIRE)})
        self.assertEqual(run.match.combat.brigitte.barriers[player.body].health, 230)
        self.assertEqual(self.health(run, protected), 200)

    def test_rear_shot_bypasses_directional_shield(self):
        run, player, shooter, protected = self.fixture()
        x, y, z = player.mover.position
        shooter.mover.position, protected.mover.position = [x, y, z - 10], [x + 8, y, z]
        shooter.mover.body = protected.mover.body = None
        run.step({player: (0, 0, SECONDARY), shooter: (0, 0, FIRE)})
        self.assertEqual(run.match.combat.brigitte.barriers[player.body].health, 250)
        self.assertEqual(self.health(run, player), 165)  # critical40, armor blocks5

    def test_release_shield_exposes_hero(self):
        run, player, shooter, _protected = self.fixture()
        run.step({shooter: (-32768, 0, FIRE)})
        self.assertEqual(run.match.combat.brigitte.barriers[player.body].health, 250)
        self.assertEqual(self.health(run, player), 165)

    def test_lowered_shield_waits_two_seconds_then_regenerates85_per_second(self):
        run, player, shooter, _protected = self.fixture()
        run.step({player: (0, 0, SECONDARY), shooter: (-32768, 0, FIRE)})
        barrier = run.match.combat.brigitte.barriers[player.body]
        self.raise_shield(run, player, 160)
        self.assertEqual(barrier.health, 230)  # no regeneration while raised
        for _ in range(100):
            run.step({})
        self.assertEqual(barrier.health, 230)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(barrier.health, 235.44, places=4)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(barrier.health, 250, places=4)

    def test_break_uses_authored_five_second_lockout_then_reopens(self):
        run, player, shooter, _protected = self.fixture()
        bridge = run.match.combat.brigitte
        barrier = bridge.barriers[player.body]
        for _ in range(200):
            run.step({player: (0, 0, SECONDARY), shooter: (-32768, 0, FIRE)})
            if barrier.health == 1:
                break
        self.assertEqual(barrier.health, 1)  # authored broken-Pet sentinel
        self.assertEqual(self.health(run, player), 200)  # breaking shot does not spill
        self.assertEqual(barrier.instance.vars[61].value(), 5)
        self.assertFalse(bridge._barrier_active(player, barrier))
        self.raise_shield(run, player, 180)
        self.assertGreater(barrier.health, 1)
        self.assertFalse(bridge._barrier_active(player, barrier))
        self.raise_shield(run, player, 150)
        self.assertEqual(barrier.health, 250)
        self.assertEqual(barrier.instance.vars[61].value(), 0)
        self.assertTrue(bridge._barrier_active(player, barrier))

    def test_enemy_whip_stops_at_shield_without_inspire(self):
        run, player, shooter, _protected = self.fixture()
        run.match.switch_hero(shooter, heroes()[HERO])
        x, y, z = player.mover.position
        shooter.mover.position = [x, y, z + 10]
        run.step({player: (0, 0, SECONDARY), shooter: (-32768, 0, 8)})
        for _ in range(50):
            run.step({player: (0, 0, SECONDARY), shooter: (-32768, 0, 0)})
        bridge = run.match.combat.brigitte
        self.assertEqual(bridge.barriers[player.body].health, 180)
        self.assertEqual(self.health(run, player), 200)
        self.assertNotIn((shooter.body, shooter.body), bridge.inspire)

    def test_enemy_melee_passes_barrier_and_damages_armor(self):
        run, player, shooter, _protected = self.fixture()
        run.match.switch_hero(shooter, heroes()[HERO])
        x, y, z = player.mover.position
        shooter.mover.position = [x, y, z + 3]
        for _ in range(20):
            run.step({player: (0, 0, SECONDARY), shooter: (-32768, 0, FIRE)})
        self.assertEqual(run.match.combat.brigitte.barriers[player.body].health, 250)
        self.assertEqual(self.health(run, player), 170)

    def test_allied_rifle_passes_friendly_shield_and_hits_enemy(self):
        run, player, shooter, protected = self.fixture()
        shooter.team, protected.team = player.team, 1 - player.team
        x, y, z = player.mover.position
        shooter.mover.position, protected.mover.position = [x + 0.75, y, z + 10], [x + 0.75, y, z - 3]
        shooter.mover.body = protected.mover.body = None
        run.step({player: (0, 0, SECONDARY), shooter: (-32768, 0, FIRE)})
        self.assertEqual(run.match.combat.brigitte.barriers[player.body].health, 250)
        self.assertEqual(self.health(run, protected), 160)

    def test_dead_owner_cannot_shield_a_teammate(self):
        run, player, shooter, protected = self.fixture()
        x, y, z = player.mover.position
        shooter.mover.position, protected.mover.position = [x + 0.75, y, z + 10], [x + 0.75, y, z - 3]
        shooter.mover.body = protected.mover.body = None
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(shooter, target, 1000, False, None, run.frame)
        run.step({shooter: (-32768, 0, FIRE)})
        self.assertEqual(self.health(run, protected), 160)

    def test_wall_before_shield_prevents_shield_damage(self):
        run, player, shooter, _protected = self.fixture()
        x, y, z = player.mover.position
        wall = World([
            (x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
            (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50),
            (x - 5, y, z + 5, x + 5, y + 5, z + 5, x + 5, y, z + 5),
            (x - 5, y, z + 5, x - 5, y + 5, z + 5, x + 5, y + 5, z + 5),
        ])
        run.match.collision_provider = StaticCollisionProvider(wall)
        run.match.collision_world = wall
        run.step({player: (0, 0, SECONDARY), shooter: (-32768, 0, FIRE)})
        self.assertEqual(run.match.combat.brigitte.barriers[player.body].health, 250)
        self.assertEqual(self.health(run, player), 200)


if __name__ == "__main__":
    unittest.main()
