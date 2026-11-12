"""Executed Viper dispersion with explicitly reconstructed seed identities/mode."""

import hashlib
import math
import struct
import unittest
from unittest.mock import patch

import test_ashe_combat as fixtures
import test_ashe_pve_npcs as pve
from test_ashe_impulses import wall_scene
from test_game_combat import aim

from ow174.game import ashe, combat, weapon_spread
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import Command
from ow174.game.content import HEROES_PATH
from ow174.game.script import graph


def fire(case, run, player, target, *, ads=False):
    fight = run.match.combat
    body = next(t for t in fight._targets() if t.owner is target)
    before = fight.vitals_of(body).total
    # Aim along x=0 even for an off-axis target; this is intentional.
    yaw, pitch = aim(fight._eye(player), (0, body.feet[1] + 1, body.feet[2]))
    vectors = []
    original = ashe.viper_direction

    def observe(forward, number, launch):
        vector = original(forward, number, launch)
        vectors.append((vector, forward, launch))
        return vector

    with patch.object(ashe, "viper_direction", side_effect=observe), \
            patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
        for _ in range(60):
            run.step({player: (yaw, pitch, 2 if ads else 0)})
        for frame in range(360):
            buttons = (1 if frame % 60 < 5 else 0) | (2 if ads else 0)
            run.step({player: (yaw, pitch, buttons)})
            # Duplicate command delivery must not create another report/ray.
            player.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons)])
    case.assertEqual(len(vectors), 6)
    case.assertEqual(hitscan.call_count, 6)
    case.assertEqual([call.args[3] for call in hitscan.call_args_list], [v[0] for v in vectors])
    weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x14FC)
    case.assertEqual(weapon.vars[53].value(), 6)
    for vector, forward, _launch in vectors:
        if ads:
            case.assertEqual(vector, weapon_spread.quantized_direction(forward))
        else:
            case.assertNotEqual(vector, forward)
            case.assertGreaterEqual(sum(a*b for a, b in zip(vector, forward, strict=True)),
                                    math.cos(math.radians(2.001)))
    return before - fight.vitals_of(body).total, vectors


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class AsheSpreadTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight

    def test_generated_tables_and_cold_cache_are_deterministic(self):
        self.assertEqual(hashlib.sha256(struct.pack("<16384I", *weapon_spread.uniform_table())).hexdigest(),
                         "4f99ff210621528fff6737582c39f0ed5464cdf1c9980f03d503dcf311b356f6")
        self.assertEqual(hashlib.sha256(struct.pack("<16384f", *weapon_spread.float_table())).hexdigest(),
                         "b3d0a3ea584bfc99c11bc39a490e7a2765c9ef74e55db6d8aafb1803c6a25281")
        expected = weapon_spread.direction((0, 0, 1), math.radians(2), 77, 2)
        weapon_spread.uniform_table.cache_clear()
        weapon_spread.float_table.cache_clear()
        self.assertEqual(weapon_spread.direction((0, 0, 1), math.radians(2), 77, 2), expected)

    def test_deferred_reports_preserve_real_rays_through_state_reentry(self):
        rays = []
        for deferred in (False, True):
            run, player, _enemy = self.fight(distance=30)
            fight = run.match.combat
            component = player.body_script.component
            consume = fight.command
            with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
                with patch.object(fight, "command", side_effect=None if deferred else consume):
                    for _ in range(3):
                        fixtures.AsheCombatTests.press(self, run, player, 1, frames=60)
                if deferred:
                    first, second, third = component.shots
                    self.assertNotEqual(component.entity & 0xFFFF, 0)
                    for state, _, _, activation, launch in component.shots:
                        self.assertEqual(launch[1], weapon_spread.seed_base(
                            0, state.instance.id, state.index, activation[0],
                        ))
                    self.assertIs(first[0], third[0])
                    self.assertNotEqual(first[3], third[3])
                    self.assertLess(first[4][0], second[4][0])
                    self.assertLess(second[4][0], third[4][0])
                    before = snapshot_digest(export_component_vm(component))
                    component.shots[0] = (*first[:4], third[4])
                    self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
                    component.shots[0] = first
                    self.assertEqual(snapshot_digest(export_component_vm(component)), before)
                    weapon_spread.uniform_table.cache_clear()
                    consume(player, Command(run.frame))
                    self.assertEqual(component.shots, [])
                self.assertEqual(hitscan.call_count, 3)
                rays.append([call.args[3] for call in hitscan.call_args_list])
        self.assertEqual(*rays)

    def test_player_real_hip_and_ads_rays_hit_and_respect_wall_and_team(self):
        for ads in (False, True):
            for policy in ("enemy", "wall", "ally"):
                with self.subTest(ads=ads, policy=policy):
                    run, player, enemy = self.fight(distance=8, team_game=policy == "ally")
                    if policy == "wall":
                        run.match.collision_world = wall_scene(4)
                    if policy == "ally":
                        enemy.team = player.team
                    damage, _ = fire(self, run, player, enemy, ads=ads)
                    if policy == "enemy":
                        self.assertGreater(damage, 0)
                    else:
                        self.assertEqual(damage, 0)

    def test_far_hip_crosshair_does_not_guarantee_hits_but_ads_stays_zero_cone(self):
        counts = []
        for ads in (False, True):
            run, player, enemy = self.fight(distance=80)
            fire(self, run, player, enemy, ads=ads)
            counts.append(len(player.client.messages(combat.HIT)))
        self.assertLess(counts[0], 6)
        self.assertEqual(counts[1], 6)


@unittest.skipUnless(pve.CATALOG.is_file() and pve.BUNDLE.is_file(), "requires external NPC inputs")
class AsheNpcSpreadTests(unittest.TestCase):
    setUpClass = classmethod(pve.AshePveNpcTests.setUpClass.__func__)
    tearDownClass = classmethod(pve.AshePveNpcTests.tearDownClass.__func__)
    spawn = pve.AshePveNpcTests.spawn
    fight = fixtures.AsheCombatTests.fight

    def test_actual_five_npcs_close_wall_ally_and_off_axis_hip_rays(self):
        for definition in self.catalog.values():
            gained = False
            for policy in ("enemy", "wall", "ally", "left", "right"):
                with self.subTest(npc=definition.name, policy=policy):
                    radius = 0.7 if definition.name == "Heavy Assault" else 0.5
                    offset = (-1 if policy == "left" else 1) * (radius + 0.03)
                    distance = 30 if policy in ("left", "right") else 8
                    run, player, bot = self.spawn(definition, distance, ally=policy == "ally",
                                                  x=offset if policy in ("left", "right") else 0)
                    if policy == "wall":
                        run.match.collision_world = wall_scene(4)
                    damage, vectors = fire(self, run, player, bot)
                    if policy == "enemy":
                        self.assertGreater(damage, 0)
                    elif policy in ("wall", "ally"):
                        self.assertEqual(damage, 0)
                    else:
                        target = next(t for t in run.match.combat._targets() if t.owner is bot)
                        a, b = target.segment()
                        origin = run.match.combat._eye(player)
                        self.assertIsNone(combat.ray_capsule(origin, vectors[0][1], a, b, target.radius))
                        gained |= damage > 0
            self.assertTrue(gained, f"{definition.name}: no off-axis spread hit")
