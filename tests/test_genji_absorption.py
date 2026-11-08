"""Actual Sigma activation versus Genji attacks, using reconstructed Grasp boxes."""

import unittest

from test_game_combat import aim
from test_game_mover import FLOOR, wall
from test_genji_combat import GENJI, GenjiFixture

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes


class GenjiAbsorptionTests(GenjiFixture):
    def scene(self, *, facing=-32768, three=False, offset=0):
        run, genji = self.spawn(4, ("Genji", "Sigma", "Victim") if three else ("Genji", "Sigma"))
        sigma = run.players[1]
        run.match.switch_hero(sigma, heroes()[0x02E000000000023B])
        sigma.mover.position[:] = [offset, 1, 4]
        genji.body_script.ult_seconds = 0
        if three:
            run.match.game_map = run.match.game_map.__class__(
                **{**run.match.game_map.__dict__, "free_for_all": False}
            )
            genji.team = 0
            sigma.team = run.players[2].team = 1
            run.players[2].mover.position[:] = [0, 1, 8]
        for frame in range(5):
            run.step({sigma: (facing, 0, 8 if frame == 0 else 0)})
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
        run, genji, sigma = self.scene(three=True)
        target = next(t for t in run.match.combat._targets() if t.owner is sigma)
        run.match.combat._damage(genji, target, 400, False, None, run.frame)
        genji.body_script.set_ult_charge(0)
        self.shoot(run, genji, sigma, target=run.players[2])
        self.assertEqual(self.health(run, run.players[2]), 113)

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

    def test_reflected_shuriken_can_be_absorbed_after_team_transfer(self):
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


if __name__ == "__main__":
    unittest.main()
