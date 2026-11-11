"""Actual Photon Barrier casts through the Sigma lane's combat paths."""

import os
import unittest
from dataclasses import replace

import test_sigma_combat as fixtures
import test_sigma_flux_placement as placement
from test_game_combat import aim, in_world

from ow174.game import combat, photon_barrier, sigma, symmetra_beam
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


@unittest.skipUnless(
    os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the external Photon Barrier body bundle",
)
class SigmaPhotonBarrierTests(unittest.TestCase):
    def arena(self):
        run, player, owner, _ = fixtures.SigmaCombatTests().arena(offset=0, distance=6)
        position = list(owner.mover.position)
        self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra_beam.HERO]))
        owner.mover.position = position
        run.step({})
        owner.body_script.ult_seconds = 0
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        instance = next(i for i in owner.body_script.component.instances.values()
                        if i.graph.index == photon_barrier.GRAPH)
        return run, player, owner, instance

    @staticmethod
    def walls(run):
        return [barrier for barrier in run.match.combat.barriers.values()
                if isinstance(barrier, photon_barrier.Wall)]

    def deploy(self, run, owner, instance, *, rotated=False):
        script = owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        for _ in range(10):
            run.step({owner: (-32768, 8192, 0x20)})
        if rotated:
            for _ in range(5):
                run.step({owner: (-32768, 8192, 0)})
            for _ in range(5):
                run.step({owner: (-32768, 8192, 0x20)})
        run.step({owner: (-32768, 8192, 0)})
        run.step({owner: (-32768, 8192, FIRE)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(self.walls(run))
        self.assertTrue(instance.node_state(instance.graph.node(0)).active)
        for _ in range(25):
            if self.walls(run):
                break
            run.step({})
        wall, = self.walls(run)
        self.assertEqual(wall.vitals.total, 4000)
        self.assertAlmostEqual(wall.expires - run.match.combat.now, 12)
        self.assertEqual(instance.find_var(0, 38).value(), expr.Entity(wall.entity))
        self.assertAlmostEqual(abs(wall.normal[0 if rotated else 2]), 1)
        return wall

    @staticmethod
    def vitals(run, player):
        fight = run.match.combat
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is player))

    def test_actual_photon_barrier_requires_charge_and_preserves_the_authored_rotation(self):
        run, _, owner, instance = self.arena()
        for _ in range(10):
            run.step({owner: (-32768, 8192, 0x20)})
        run.step({owner: (-32768, 8192, FIRE)})
        self.assertFalse(self.walls(run))
        self.assertFalse(instance.node_state(instance.graph.node(0)).active)
        run.step({})
        wall = self.deploy(run, owner, instance, rotated=True)
        self.assertAlmostEqual(wall.normal[2], 0)

    def test_invalid_sky_confirmation_preserves_charge_then_accepts_ground_retry(self):
        run, _, owner, instance = self.arena()
        script = owner.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        for _ in range(10):
            run.step({owner: (-32768, -8192, 0x20)})
        run.step({owner: (-32768, -8192, 0)})
        run.step({owner: (-32768, -8192, FIRE)})
        self.assertFalse(self.walls(run))
        self.assertEqual(script.component.entity_var(342).value(), cost)
        self.assertFalse(instance.find_var(0, 1002).value())
        run.step({owner: (-32768, 8192, 0)})
        self.assertTrue(instance.find_var(0, 1002).value())
        run.step({owner: (-32768, 8192, FIRE)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(25):
            run.step({})
        self.assertEqual(len(self.walls(run)), 1)

    def test_actual_hyperspheres_damage_photon_barrier_and_the_breaking_hit_does_not_spill(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, player, owner, instance = self.arena()
                wall = self.deploy(run, owner, instance)
                if breaking:
                    sigma.damage_barrier(run.match.combat, wall, 3960)
                vitals = self.vitals(run, owner)
                fixtures.SigmaCombatTests().pair(run, player, owner)
                self.assertEqual(wall.vitals.total, 0 if breaking else 3890)
                self.assertEqual(vitals.total, 170 if breaking else 225)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 55 if breaking else 0)
                self.assertEqual(wall in self.walls(run), not breaking)
                if breaking:
                    self.assertIn(instance.find_var(0, 38).value(), (0, expr.Entity(0)))

    def test_accretion_spends_photon_barrier_durability_without_stunning_its_owner(self):
        run, player, owner, instance = self.arena()
        wall = self.deploy(run, owner, instance)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(player), fight._eye(owner))
        run.step({player: (yaw, pitch, 0x10)})
        for _ in range(85):
            run.step({player: (yaw, pitch, 0)})
        self.assertEqual(wall.vitals.total, 3930)
        self.assertEqual(self.vitals(run, owner).total, 225)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_accretion_interrupts_confirmed_photon_barrier_before_the_pet_starts(self):
        run, player, owner, instance = self.arena()
        script = owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fight = run.match.combat
        for _ in range(10):
            run.step({owner: (-32768, 8192, 0x20)})
        yaw, pitch = aim(fight._eye(player), fight._eye(owner))
        run.step({player: (yaw, pitch, 0x10), owner: (-32768, 8192, 0)})
        for _ in range(55):
            if fight.rockets:
                break
            run.step({player: (yaw, pitch, 0), owner: (-32768, 8192, 0)})
        self.assertEqual(len(fight.rockets), 1)
        run.step({owner: (-32768, 8192, FIRE)})
        self.assertTrue(instance.node_state(instance.graph.node(0)).active)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(65):
            run.step({})
            self.assertFalse(self.walls(run))
        self.assertEqual(self.vitals(run, owner).total, 155)
        self.assertFalse(instance.node_state(instance.graph.node(0)).active)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.is_stunned(owner))
        self.deploy(run, owner, instance)

    def test_hypersphere_airbursts_parallel_to_the_plane_damage_only_the_wall(self):
        run, player, owner, instance = self.arena()
        wall = self.deploy(run, owner, instance)
        player.mover.position[2] = wall.centre[2] - 1
        player.mover.body = None
        run.step({player: (16384, 0, FIRE)})
        fight = run.match.combat
        sphere, = fight.rockets
        self.assertAlmostEqual(sphere.direction[2], 0)
        distance = abs(sphere.position[2] - wall.centre[2])
        splash = sphere.blast.amount * sphere.blast.splash(distance)
        self.assertGreater(splash, 0)
        for _ in range(60):
            run.step({player: (16384, 0, 0)})
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(wall.vitals.total, 4000 - 2 * splash)
        self.assertEqual(self.vitals(run, owner).total, 225)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_nearer_static_cover_protects_both_photon_barrier_and_its_owner(self):
        run, player, owner, instance = self.arena()
        wall = self.deploy(run, owner, instance)
        fixture = fixtures.SigmaCombatTests()
        fixture.wall(run, sideways=False, offset=3)
        fixture.pair(run, player, owner)
        self.assertEqual(wall.vitals.total, 4000)
        self.assertEqual(self.vitals(run, owner).total, 225)

    def test_quick_melee_spends_wall_durability_outside_body_reach(self):
        run, player, owner, instance = self.arena()
        wall = self.deploy(run, owner, instance)
        player.mover.position[2] = wall.centre[2] - 2.3
        player.mover.body = None
        run.step({player: (0, 0, 0x800)})
        for _ in range(35):
            run.step({})
        self.assertEqual(wall.vitals.total, 3970)
        self.assertEqual(self.vitals(run, owner).total, 225)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_flux_lifts_and_slams_the_owner_through_photon_barrier(self):
        run, player, owner, instance = self.arena()
        wall = self.deploy(run, owner, instance)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        placement.SigmaFluxPlacementTests().select(run, player, tuple(owner.mover.position))
        vitals = self.vitals(run, owner)
        for _ in range(150):
            if owner.mover.airborne:
                break
            run.step({})
        self.assertTrue(owner.mover.airborne)
        self.assertEqual(vitals.total, 175)
        for _ in range(340):
            if vitals.total == 62.5:
                break
            run.step({})
        self.assertEqual(vitals.total, 62.5)
        self.assertFalse(owner.mover.airborne)
        self.assertEqual(wall.vitals.total, 4000)
        self.assertIn(wall, self.walls(run))
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_allied_hyperspheres_pass_the_plane_and_hit_a_hostile_body(self):
        run, player, owner, instance = self.arena()
        wall = self.deploy(run, owner, instance)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team = owner.team
        foe = run.match.add_player(3, "Hostile", SOLDIER, 1 - owner.team, False)
        in_world(run.match, foe)
        run.players.append(foe)
        self.assertTrue(run.match.switch_hero(foe, heroes()[SOLDIER]))
        x, y, z = owner.mover.position
        foe.mover.position[:] = (x, y, z + 2)
        fixtures.SigmaCombatTests().pair(run, player, foe)
        self.assertEqual(self.vitals(run, foe).total, 90)
        self.assertEqual(self.vitals(run, owner).total, 225)
        self.assertEqual(wall.vitals.total, 4000)

    def test_owners_charged_orb_passes_photon_barrier_and_is_absorbed_by_sigma(self):
        run, player, owner, instance = self.arena()
        wall = self.deploy(run, owner, instance)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(owner), fight._eye(player))
        for _ in range(65):
            run.step({})
        for _ in range(90):
            run.step({owner: (yaw, pitch, SECONDARY)})
        self.assertFalse(fight.rockets)
        run.step({player: (0, 0, 8), owner: (yaw, pitch, 0)})
        orb, = fight.rockets
        self.assertEqual(orb.definition, 0x04000000000026F1)
        self.assertEqual(orb.direct.amount + orb.blast.amount, 120)
        self.assertIsNotNone(wall.intersection(orb.position, orb.direction, 6))
        grasp = fixtures.SigmaCombatTests().ability(player, sigma.GRASP).instance
        for _ in range(35):
            run.step({})
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(wall.vitals.total, 4000)
        self.assertEqual(grasp.vars[21312].value(), 120)
        self.assertEqual(self.vitals(run, player).total, 400)

    def test_photon_barrier_survives_owner_death_and_respawn_until_its_deadline(self):
        run, player, owner, instance = self.arena()
        wall = self.deploy(run, owner, instance)
        fight = run.match.combat
        x, y, z = owner.mover.position
        player.mover.position[:] = (x, y, z + 3)
        player.mover.body = None
        vitals = self.vitals(run, owner)
        vitals.now[combat.HEALTH_POOL] = 45
        vitals.now[combat.SHIELDS_POOL] = 0
        old = owner.body
        fixtures.SigmaCombatTests().pair(run, player, owner)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, player.body)
        self.assertIn(wall, self.walls(run))
        when, = (when for when, who in fight.respawns if who is owner)
        respawn_tick = round(when / combat.TICK)
        run.match.update(when, respawn_tick, frames=False)
        self.assertNotEqual(owner.body, old)
        self.assertIn(wall, self.walls(run))
        self.assertTrue(wall.active)
        expiry_tick = round(wall.expires / combat.TICK)
        run.match.update(wall.expires, expiry_tick, frames=False)
        self.assertFalse(wall.active)
        self.assertFalse(self.walls(run))
