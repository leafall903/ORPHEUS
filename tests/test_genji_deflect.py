"""Reconstructed Deflect authority through real player commands and volleys."""

import unittest

from test_genji_combat import GENJI, GenjiFixture

from ow174.game import combat
from ow174.game.content import heroes


class GenjiDeflectTests(GenjiFixture):
    def start(self, distance=4, facing=0, projectile=False):
        run, genji = self.spawn(distance)
        source = run.players[1]
        if projectile:
            run.match.switch_hero(source, heroes()[GENJI])
            source.mover.position[:] = [0, 1, distance]
        for frame in range(5):
            run.step({genji: (facing, 0, 16 if frame == 0 else 0)})
        return run, genji, source

    def incoming(self, run, genji, source, *, facing=0, button=1, frames=24):
        for frame in range(frames):
            run.step({genji: (facing, 0, 0), source: (-32768, 0, button if frame < 2 else 0)})

    def state(self, genji):
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        return instance.state(10), instance.state(0)

    def test_frontal_soldier_hit_is_reflected_and_credited_to_genji(self):
        run, genji, source = self.start()
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 160)  # reflected eye-height rifle hit
        self.assertEqual(len(genji.client.messages(combat.HIT)), 1)
        hit = genji.client.messages(combat.HIT)[0]["+0x78"]
        self.assertEqual(hit["+0x4"], 40)
        self.assertEqual(hit["+0x8"] & combat.CRITICAL, combat.CRITICAL)
        self.assertEqual(source.client.messages(combat.HIT), [])

    def test_reflected_kill_belongs_to_genji(self):
        run, genji, source = self.start()
        wounded = combat.Vitals(source.body, 200)
        wounded.damage(180)
        run.match.combat.vitals[source.body] = wounded
        self.incoming(run, genji, source)
        self.assertTrue(run.match.combat.is_dead(source))
        self.assertEqual(wounded.killer, genji.body)
        self.assertEqual(len(genji.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(self.health(run, genji), 200)

    def test_rear_hitscan_damages_genji(self):
        run, genji, source = self.start(facing=-32768)
        self.incoming(run, genji, source, facing=-32768)
        self.assertEqual(self.health(run, genji), 160)
        self.assertEqual(self.health(run, source), 200)

    def test_shuriken_projectiles_transfer_owner_and_team(self):
        run, genji, source = self.start(projectile=True)
        self.incoming(run, genji, source, frames=60)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 113)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 3)

    def test_projectiles_from_rear_are_not_reflected(self):
        run, genji, source = self.start(facing=-32768, projectile=True)
        self.incoming(run, genji, source, facing=-32768, frames=60)
        self.assertEqual(self.health(run, genji), 113)
        self.assertEqual(self.health(run, source), 200)

    def test_reflected_projectile_uses_deflectors_enemy_team(self):
        run, genji = self.spawn(4, ("Genji", "Shooter", "Shooters ally"))
        source, ally = run.players[1:]
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        source.team = ally.team = 1
        genji.team = 0
        run.match.switch_hero(source, heroes()[GENJI])
        source.mover.position[:] = [0, 1, 4]
        ally.mover.position[:] = [0, 1, 2]
        for frame in range(5):
            run.step({genji: (0, 0, 16 if frame == 0 else 0)})
        self.incoming(run, genji, source, frames=60)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 200)
        self.assertEqual(self.health(run, ally), 113)

    def test_quick_melee_is_blocked_without_return_damage(self):
        run, genji, source = self.start(distance=1)
        self.incoming(run, genji, source, button=0x800, frames=60)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 200)

    def test_rear_quick_melee_is_not_blocked(self):
        run, genji, source = self.start(distance=1, facing=-32768)
        self.incoming(run, genji, source, facing=-32768, button=0x800, frames=60)
        self.assertEqual(self.health(run, genji), 170)

    def test_expiry_removes_protection_and_starts_cooldown(self):
        run, genji, source = self.start()
        for _ in range(140):
            run.step({})
        state, ability = self.state(genji)
        self.assertFalse(state.active)
        self.assertGreater(ability.var.value(), 7)
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, genji), 160)

    def test_cancel_removes_protection_and_cooldown_rejects_reactivation(self):
        run, genji, source = self.start()
        for frame in range(60):
            run.step({genji: (0, 0, 16 if frame in (25, 45) else 0)})
        state, ability = self.state(genji)
        self.assertFalse(state.active)
        self.assertEqual(state.counter, 1)
        self.assertGreater(ability.var.value(), 7)
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, genji), 160)

    def test_after_cooldown_new_press_can_deflect_again(self):
        run, genji, source = self.start()
        for _ in range(650):
            run.step({})
        state, ability = self.state(genji)
        self.assertEqual(ability.var.value(), 0)
        run.step({genji: (0, 0, 16)})
        self.assertTrue(state.active)
        self.assertEqual(state.counter, 2)
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 160)

    def test_death_removes_protection(self):
        run, genji, source = self.start()
        target = next(t for t in run.match.combat._targets() if t.owner is genji)
        run.match.combat._damage(source, target, 200, False, None, run.frame)
        run.step({})
        self.assertFalse(run.match.combat._genji_deflects(target, (0, 0, -1)))

    def test_noneligible_damage_is_not_blanket_blocked(self):
        run, genji, source = self.start()
        # This tests the explicit eligibility contract, not a restored beam hero.
        run.match.combat.rockets.append(combat.Rocket(
            source, source.team, (0, 2, 2), (0, 0, -1), 60, 1, combat.HitDef(10, None), None,
        ))
        for _ in range(10):
            run.step({})
        self.assertEqual(self.health(run, genji), 190)
        self.assertEqual(self.health(run, source), 200)

    def test_splash_damage_is_not_blanket_blocked(self):
        run, genji, source = self.start()
        blast = combat.HitDef(80, None, rings=((3, 1),))
        run.match.combat.rockets.append(combat.Rocket(
            source, source.team, (0, 2, 2), (0, 0, -1), 60, 1, None, blast,
        ))
        for _ in range(10):
            run.step({})
        self.assertEqual(self.health(run, genji), 120)


if __name__ == "__main__":
    unittest.main()
