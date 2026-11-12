"""Actual Sigma activation versus Genji attacks, using reconstructed Grasp boxes."""

import math
import unittest
from dataclasses import replace

from test_game_combat import aim
from test_game_mover import FLOOR, wall
from test_genji_combat import GENJI, GenjiFixture

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes


class GenjiAbsorptionTests(GenjiFixture):
    def scene(self, *, facing=-32768, three=False, offset=0, activate=True, distance=4,
              victim_position=(0, 1, 8)):
        run, genji = self.spawn(distance, ("Genji", "Sigma", "Victim") if three else ("Genji", "Sigma"))
        sigma = run.players[1]
        run.match.switch_hero(sigma, heroes()[0x02E000000000023B])
        sigma.mover.position[:] = [offset, 1, distance]
        genji.body_script.ult_seconds = 0
        if three:
            run.match.game_map = run.match.game_map.__class__(
                **{**run.match.game_map.__dict__, "free_for_all": False}
            )
            genji.team = 0
            sigma.team = run.players[2].team = 1
            run.players[2].mover.position[:] = victim_position
        for frame in range(5):
            run.step({sigma: (facing, 0, 8 if activate and frame == 0 else 0)})
        return run, genji, sigma

    def instance(self, sigma):
        return next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)

    def shoot(self, run, genji, sigma, *, target=None, button=1, facing=-32768):
        target = sigma if target is None else target
        x, y, z = target.mover.position
        yaw, pitch = aim(run.match.combat._eye(genji), (x, y + 1, z))
        for frame in range(60):
            run.step({genji: (yaw, pitch, button if frame == 0 else 0), sigma: (facing, 0, 0)})

    def test_primary_is_eaten_without_health_loss_hit_or_ultimate_credit(self):
        run, genji, sigma = self.scene()
        self.shoot(run, genji, sigma)
        self.assertEqual(self.health(run, sigma), 400)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 87)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(genji.client.messages(combat.HIT), [])
        self.assertNotIn(sigma.body, run.match.combat.genji_assists)

    def test_fan_is_eaten_as_three_projectiles(self):
        run, genji, sigma = self.scene()
        self.shoot(run, genji, sigma, button=2)
        self.assertEqual(self.health(run, sigma), 400)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 87)

    def test_fan_centre_is_absorbed_while_either_outside_pellet_can_hit(self):
        for side in (-1, 1):
            with self.subTest(side=side):
                distance = 20
                offset = side * math.tan(math.radians(4.5)) * distance
                run, genji, sigma = self.scene(three=True, distance=distance,
                                              victim_position=(offset, 1, distance))
                victim = run.players[2]
                self.shoot(run, genji, sigma, button=2)

                self.assertEqual(self.health(run, sigma), 400)
                self.assertEqual(self.instance(sigma).vars[21312].value(), 29)
                self.assertEqual(self.health(run, victim), 171)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 29)
                hits = genji.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29])

    def test_rear_hits_are_not_eaten(self):
        run, genji, sigma = self.scene(facing=0)
        self.shoot(run, genji, sigma, facing=0)
        self.assertEqual(self.health(run, sigma), 313)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 0)

    def test_volume_can_protect_an_ally_without_intersecting_sigma_body(self):
        run, genji, sigma = self.scene(three=True, offset=0.8)
        victim = run.players[2]
        self.shoot(run, genji, sigma, target=victim)
        self.assertEqual(self.health(run, victim), 200)
        self.assertEqual(self.health(run, sigma), 400)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 87)

    def test_allied_grasp_does_not_eat_friendly_fire(self):
        run, genji, sigma = self.scene(three=True)
        sigma.team = genji.team
        victim = run.players[2]
        self.shoot(run, genji, sigma, target=victim)
        self.assertEqual(self.health(run, victim), 113)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 0)

    def test_wall_stops_projectile_before_absorption(self):
        run, genji, sigma = self.scene()
        run.match.collision_world = World(FLOOR + wall(-5, 1, 5, 1))
        self.shoot(run, genji, sigma)
        self.assertEqual(self.health(run, sigma), 400)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 0)

    def test_expired_grasp_no_longer_eats_shuriken(self):
        run, genji, sigma = self.scene()
        for _ in range(140):
            run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(self.instance(sigma).state(1).active)
        self.shoot(run, genji, sigma)
        self.assertEqual(self.health(run, sigma), 313)

    def test_grasp_activates_after_launch_and_absorbs_in_flight_primary(self):
        for activation_frame, absorbed in ((1, 87), (5, 58)):
            with self.subTest(activation_frame=activation_frame):
                run, genji, sigma = self.scene(activate=False)
                grasp = self.instance(sigma)
                yaw, pitch = aim(run.match.combat._eye(genji), (0, 2, 4))
                run.step({genji: (yaw, pitch, 1), sigma: (-32768, 0, 0)})
                self.assertFalse(grasp.state(1).active)
                airborne, = run.match.combat.rockets
                for frame in range(1, 30):
                    if frame == activation_frame:
                        self.assertEqual(airborne in run.match.combat.rockets, absorbed == 87)
                    run.step({genji: (yaw, pitch, 0),
                              sigma: (-32768, 0, 8 if frame == activation_frame else 0)})
                self.assertTrue(grasp.state(1).active)
                self.assertEqual(grasp.vars[21312].value(), absorbed)
                damage = 87 - absorbed
                self.assertEqual(self.health(run, sigma), 400 - damage)
                hits = genji.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29] * (damage // 29))
                self.assertEqual(genji.body_script.component.entity_var(342).value(), damage)
                self.assertFalse(run.match.combat.rockets)

    def test_primary_already_in_flight_hits_after_grasp_expires(self):
        run, genji, sigma = self.scene()
        grasp = self.instance(sigma)
        yaw, pitch = aim(run.match.combat._eye(genji), (0, 2, 4))
        vitals = run.match.combat.vitals[sigma.body]
        # Start late enough that the second projectile is airborne at expiry.
        for frame in range(119):
            run.step({genji: (yaw, pitch, 1 if frame == 112 else 0), sigma: (-32768, 0, 0)})
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(grasp.vars[21312].value(), 29)
        self.assertEqual(vitals.total, 400)
        self.assertFalse(genji.client.messages(combat.HIT))
        airborne, = run.match.combat.rockets

        run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(grasp.state(1).active)
        self.assertIs(run.match.combat.rockets[0], airborne)
        pool, = vitals.extra.values()
        self.assertEqual(pool.identifier, 0x5847)
        self.assertAlmostEqual(pool.now, 17.4, places=4)
        for _ in range(20):
            run.step({sigma: (-32768, 0, 0)})

        self.assertEqual(grasp.vars[21312].value(), 29)
        self.assertTrue(pool.removed)
        self.assertAlmostEqual(vitals.now[2], 59.4, places=4)
        self.assertEqual(vitals.now[0], 300)
        hits = genji.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29, 29])
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 58)

    def test_quick_melee_bypasses_grasp_but_respects_wall_range_and_team(self):
        for reason in ("hit", "wall", "range", "team"):
            with self.subTest(reason=reason):
                run, genji, sigma = self.scene(distance=4 if reason == "range" else 1.5)
                if reason == "wall":
                    run.match.collision_world = World(FLOOR + wall(-5, .75, 5, .75))
                if reason == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sigma.team = genji.team
                grasp = self.instance(sigma)
                self.assertTrue(grasp.state(1).active)
                self.shoot(run, genji, sigma, button=0x800)
                self.assertTrue(grasp.state(1).active)
                melee = next(i for i in genji.body_script.component.instances.values()
                             if i.graph.index == 0x0043)
                self.assertEqual(melee.state(4).counter, 1)
                damage = 30 if reason == "hit" else 0
                self.assertEqual(self.health(run, sigma), 400 - damage)
                self.assertEqual(grasp.vars[21312].value(), 0)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), damage)
                hits = genji.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [30] if damage else [])

    def test_strike_bypasses_projectile_absorption(self):
        run, genji, sigma = self.scene()
        for frame in range(60):
            run.step({genji: (0, 0, 8 if frame == 0 else 0), sigma: (-32768, 0, 0)})
        self.assertEqual(self.health(run, sigma), 350)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 0)

    def test_blade_bypasses_projectile_absorption(self):
        run, genji, sigma = self.scene()
        genji.body_script.set_ult_charge(genji.body_script.component.entity_var(338).value())
        for frame in range(110):
            run.step({genji: (0, 0, 32 if frame == 0 else (1 if frame > 70 else 0)),
                      sigma: (-32768, 0, 0)})
        self.assertEqual(self.health(run, sigma), 290)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 0)

    def test_noneligible_attack_is_not_blanket_blocked(self):
        run, genji, sigma = self.scene()
        # Negative primitive contract; no restored beam hero is claimed here.
        run.match.combat.rockets.append(combat.Rocket(
            genji, genji.team, (0, 2, 0), (0, 0, 1), 60, 1, combat.HitDef(10, None), None,
        ))
        for _ in range(30):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.health(run, sigma), 390)

    def test_dead_defender_no_longer_protects_an_ally(self):
        run, genji, sigma = self.scene(three=True, distance=1.5, activate=False)
        victim = run.players[2]
        fight = run.match.combat
        grasp = self.instance(sigma)
        for _ in range(4):
            self.shoot(run, genji, sigma)
        self.shoot(run, genji, sigma, button=0x800)
        self.assertEqual(self.health(run, sigma), 22)
        hits_before = len(genji.client.messages(combat.HIT))

        run.step({sigma: (-32768, 0, 8)})
        yaw, pitch = aim(fight._eye(genji), (0, 2, 8))
        for frame in range(26):
            run.step({genji: (yaw, pitch, 1 if frame == 0 else 0), sigma: (-32768, 0, 0)})
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(grasp.vars[21312].value(), 87)
        self.assertEqual(self.health(run, victim), 200)

        target = next(t for t in fight._targets() if t.owner is sigma)
        vitals = fight.vitals_of(target)
        for frame in range(70):
            run.step({genji: (yaw, pitch, 0x800 if frame == 0 else 0), sigma: (-32768, 0, 0)})
        self.assertTrue(fight.is_dead(sigma))
        self.assertEqual(vitals.killer, genji.body)
        self.assertFalse(grasp.state(1).active)
        self.assertFalse(vitals.extra)

        for frame in range(40):
            run.step({genji: (yaw, pitch, 1 if frame == 0 else 0)})
        self.assertEqual(self.health(run, victim), 113)
        hits = genji.client.messages(combat.HIT)[hits_before:]
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [22, 29, 29, 29])
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 487)

    def test_actual_hitscan_volley_is_absorbed_before_body_hit(self):
        run, shooter, sigma = self.scene()
        run.match.switch_hero(shooter, heroes()[SOLDIER])
        shooter.mover.position[:] = [0, 1, 0]
        shooter.body_script.ult_seconds = 0
        self.shoot(run, shooter, sigma)
        self.assertEqual(self.health(run, sigma), 400)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 20)
        self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_actual_helix_absorption_cancels_direct_and_splash(self):
        run, shooter, sigma = self.scene()
        run.match.switch_hero(shooter, heroes()[SOLDIER])
        shooter.mover.position[:] = [0, 1, 0]
        shooter.body_script.ult_seconds = 0
        self.shoot(run, shooter, sigma, button=2)
        self.assertEqual(self.health(run, sigma), 400)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 120)
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_reflected_shuriken_grant_spendable_shields_after_team_transfer(self):
        run, genji = self.spawn(4, ("Genji", "Sigma", "Shooter"))
        sigma, source = run.players[1:]
        run.match.switch_hero(sigma, heroes()[0x02E000000000023B])
        run.match.switch_hero(source, heroes()[GENJI])
        sigma.mover.position[:] = [0, 1, 2]
        source.mover.position[:] = [0, 1, 5]
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        genji.team = 0
        sigma.team = source.team = 1
        genji.body_script.ult_seconds = 0
        for frame in range(5):
            run.step({genji: (0, 0, 16 if frame == 0 else 0),
                      sigma: (-32768, 0, 8 if frame == 0 else 0)})
        for frame in range(60):
            run.step({genji: (0, 0, 0), sigma: (-32768, 0, 0),
                      source: (-32768, 0, 1 if frame == 0 else 0)})
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 200)
        self.assertEqual(self.health(run, sigma), 400)
        self.assertEqual(self.instance(sigma).vars[21312].value(), 87)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertFalse(source.client.messages(combat.HIT))

        vitals = run.match.combat.vitals[sigma.body]
        deflect = next(i for i in genji.body_script.component.instances.values()
                       if i.graph.index == 0x03A3).state(10)
        for _ in range(220):
            run.step({})
            if vitals.extra and not deflect.active:
                break
        self.assertFalse(deflect.active)
        self.assertEqual(len(vitals.extra), 1)
        pool = next(iter(vitals.extra.values()))
        self.assertEqual(pool.identifier, 0x5847)
        self.assertAlmostEqual(pool.now, 52.2, places=4)
        self.assertAlmostEqual(vitals.total, 452.2, places=4)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

        # Deflect's protection ends before its weapon lock releases.
        for _ in range(20):
            run.step({})
        self.shoot(run, genji, sigma)
        self.assertTrue(pool.removed)
        self.assertEqual(vitals.now[0], 300)
        self.assertAlmostEqual(vitals.now[2], 65.2, places=4)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 87)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 3)


if __name__ == "__main__":
    unittest.main()
