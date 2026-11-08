"""Production Combat integration for reconstructed support services; no client claims."""

import unittest

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL
from ow174.game.zenyatta import HERO


class ZenyattaSupportTests(unittest.TestCase):
    def arena(self):
        run = Run(PRACTICE_RANGE, ("Zenyatta", "Ally", "Enemy"))
        run.match.bots.clear()
        zen, ally, enemy = run.players
        zen.team = ally.team = 0
        enemy.team = 1
        run.match.switch_hero(zen, heroes()[HERO])
        x, y, z = zen.mover.position
        ally.mover.position = [x, y, z + 8]
        enemy.mover.position = [x + 6, y, z + 8]
        run.step({})
        return run, zen, ally, enemy

    def target(self, run, player):
        return next(t for t in run.match.combat._targets() if t.owner is player)

    def cast(self, run, zen, target, button):
        where = target.mover.position
        angles = aim(run.match.combat._eye(zen), (where[0], where[1] + combat.EYE, where[2]))
        run.step({zen: (*angles, button)})

    def wall(self, run, zen):
        x, y, z = zen.mover.position
        run.match.collision_world = World([
            (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
            (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
            (x-10, y, z+4, x+10, y, z+4, x+10, y+5, z+4),
            (x-10, y, z+4, x+10, y+5, z+4, x-10, y+5, z+4),
        ])

    def test_harmony_heals_actual_ally_pools_at_thirty_per_second(self):
        run, zen, ally, _ = self.arena()
        v = run.match.combat.vitals_of(self.target(run, ally))
        v.damage(100)
        self.cast(run, zen, ally, 0x10)
        for _ in range(60):
            run.step({})
        self.assertAlmostEqual(v.total, 128.8)
        self.assertIsNotNone(run.match.combat.zenyatta.owners[zen.body].harmony)

    def test_support_rejects_wrong_team_without_consuming_recovery(self):
        run, zen, ally, enemy = self.arena()
        self.cast(run, zen, enemy, 0x10)
        run.step({})
        self.cast(run, zen, ally, 0x08)
        owner = run.match.combat.zenyatta.owners[zen.body]
        self.assertIsNone(owner.harmony)
        self.assertIsNone(owner.discord)
        self.assertEqual(owner.ready, {})

    def test_support_rejects_wall_and_out_of_range(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, zen, ally, _ = self.arena()
                if wall:
                    self.wall(run, zen)
                else:
                    ally.mover.position[2] += 40
                    ally.mover.body = None
                self.cast(run, zen, ally, 0x10)
                self.assertIsNone(run.match.combat.zenyatta.owners[zen.body].harmony)

    def test_harmony_loses_target_after_three_seconds_without_los(self):
        run, zen, ally, _ = self.arena()
        self.cast(run, zen, ally, 0x10)
        self.wall(run, zen)
        for _ in range(180):
            run.step({})
        owner = run.match.combat.zenyatta.owners[zen.body]
        self.assertIsNotNone(owner.harmony)
        for _ in range(10):
            run.step({})
        self.assertIsNone(owner.harmony)

    def test_discord_increases_real_primary_body_damage_to_sixty(self):
        run, zen, _, enemy = self.arena()
        self.cast(run, zen, enemy, 0x08)
        where = enemy.mover.position
        angles = aim(run.match.combat._eye(zen), (where[0], where[1] + 1, where[2]))
        run.step({zen: (*angles, FIRE)})
        for _ in range(20):
            run.step({})
        v = run.match.combat.vitals_of(self.target(run, enemy))
        self.assertEqual(v.total, 140)
        self.assertEqual(zen.client.messages(combat.HIT)[0]["+0x78"]["+0x4"], 60)

    def test_discord_amplifies_another_hero_actual_weapon_hit(self):
        run, zen, ally, enemy = self.arena()
        self.cast(run, zen, enemy, 0x08)
        where = enemy.mover.position
        angles = aim(run.match.combat._eye(ally), (where[0], where[1] + 1, where[2]))
        run.step({ally: (*angles, FIRE)})
        v = run.match.combat.vitals_of(self.target(run, enemy))
        self.assertEqual(v.total, 175)
        self.assertEqual(ally.client.messages(combat.HIT)[0]["+0x78"]["+0x4"], 25)

    def test_discord_expiry_restores_another_hero_normal_damage(self):
        run, zen, ally, enemy = self.arena()
        self.cast(run, zen, enemy, 0x08)
        self.wall(run, zen)
        for _ in range(190):
            run.step({})
        self.assertIsNone(run.match.combat.zenyatta.owners[zen.body].discord)
        where = enemy.mover.position
        angles = aim(run.match.combat._eye(ally), (where[0], where[1] + 1, where[2]))
        run.step({ally: (*angles, FIRE)})
        v = run.match.combat.vitals_of(self.target(run, enemy))
        self.assertEqual(v.total, 180)

    def test_harmony_never_revives_dead_target(self):
        run, zen, ally, enemy = self.arena()
        self.cast(run, zen, ally, 0x10)
        target = self.target(run, ally)
        run.match.combat._damage(enemy, target, 500, False, None, run.frame)
        for _ in range(20):
            run.step({})
        self.assertTrue(run.match.combat.vitals_of(target).dead)
        self.assertIsNone(run.match.combat.zenyatta.owners[zen.body].harmony)

    def test_harmony_clears_on_team_change(self):
        run, zen, ally, _ = self.arena()
        self.cast(run, zen, ally, 0x10)
        ally.team = 1
        run.step({})
        self.assertIsNone(run.match.combat.zenyatta.owners[zen.body].harmony)

    def test_recovery_starts_on_success_rejects_recast_then_allows_new_target(self):
        run, zen, ally, second = self.arena()
        second.team = 0
        self.cast(run, zen, ally, 0x10)
        owner = run.match.combat.zenyatta.owners[zen.body]
        first_deadline = owner.ready['harmony']
        run.step({})
        self.cast(run, zen, second, 0x10)
        self.assertEqual(owner.harmony.target, ally.body)
        self.assertEqual(owner.ready['harmony'], first_deadline)
        for _ in range(50):
            run.step({})
        self.cast(run, zen, second, 0x10)
        self.assertEqual(owner.harmony.target, second.body)
        self.assertGreater(owner.ready['harmony'], first_deadline)

    def test_harmony_and_discord_have_independent_recovery(self):
        run, zen, ally, enemy = self.arena()
        self.cast(run, zen, ally, 0x10)
        self.cast(run, zen, enemy, 0x08)
        owner = run.match.combat.zenyatta.owners[zen.body]
        self.assertEqual(owner.harmony.target, ally.body)
        self.assertEqual(owner.discord.target, enemy.body)

    def test_ultimate_rejects_weapons_and_orb_casts_then_damage_resumes(self):
        run, zen, ally, enemy = self.arena()
        f = run.match.combat
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.cast(run, zen, zen, 0x20)
        for _ in range(30):
            self.cast(run, zen, enemy, FIRE | 0x08)
        self.assertFalse(f.rockets)
        self.assertIsNone(f.zenyatta.owners[zen.body].discord)
        self.cast(run, zen, ally, 0x10)
        self.assertIsNone(f.zenyatta.owners[zen.body].harmony)
        for _ in range(360):
            run.step({})
        own = f.vitals_of(self.target(run, zen))
        f._damage(enemy, self.target(run, zen), 50, False, None, run.frame)
        self.assertEqual(own.total, 150)

    def test_ultimate_does_not_heal_through_wall(self):
        run, zen, ally, _ = self.arena()
        self.wall(run, zen)
        v = run.match.combat.vitals_of(self.target(run, ally))
        v.damage(100)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.cast(run, zen, zen, 0x20)
        for _ in range(20):
            run.step({})
        self.assertEqual(v.total, 100)

    def test_shield_regen_waits_three_seconds_and_does_not_restore_health(self):
        run, zen, _, enemy = self.arena()
        f = run.match.combat
        target = self.target(run, zen)
        v = f.vitals_of(target)
        f._damage(enemy, target, 175, False, None, run.frame)
        self.assertEqual(v.total, 25)
        for _ in range(180):
            run.step({})
        self.assertEqual(v.total, 25)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(v.now[SHIELDS_POOL], 6)
        self.assertEqual(v.now[HEALTH_POOL], 25)

    def test_real_damage_resets_shield_regen_delay(self):
        run, zen, _, enemy = self.arena()
        f = run.match.combat
        target = self.target(run, zen)
        v = f.vitals_of(target)
        f._damage(enemy, target, 50, False, None, run.frame)
        for _ in range(180):
            run.step({})
        f._damage(enemy, target, 50, False, None, run.frame)
        for _ in range(180):
            run.step({})
        self.assertEqual(v.now[SHIELDS_POOL], 50)

    def test_damage_and_effective_healing_charge_ultimate_and_cap_at_cost(self):
        run, zen, ally, enemy = self.arena()
        f = run.match.combat
        c = zen.body_script.component
        zen.body_script.set_ult_charge(0)
        f._damage(zen, self.target(run, enemy), 48, False, None, run.frame)
        self.assertEqual(c.entity_var(342).value(), 48)
        a = f.vitals_of(self.target(run, ally))
        a.damage(10)
        f.zenyatta.heal(zen, self.target(run, ally), 100)
        self.assertEqual(c.entity_var(342).value(), 58)
        f.zenyatta.heal(zen, self.target(run, ally), 100)
        self.assertEqual(c.entity_var(342).value(), 58)
        f.zenyatta.charge(zen, 10000)
        self.assertEqual(c.entity_var(342).value(), c.entity_var(338).value())

    def test_owner_death_removes_orbs(self):
        run, zen, _, enemy = self.arena()
        self.cast(run, zen, enemy, 0x08)
        run.match.combat._damage(enemy, self.target(run, zen), 500, False, None, run.frame)
        run.step({})
        self.assertNotIn(zen.body, run.match.combat.zenyatta.owners)

    def test_ultimate_requires_charge_consumes_it_and_ends(self):
        run, zen, _, _ = self.arena()
        self.cast(run, zen, zen, 0x20)
        service = run.match.combat.zenyatta
        self.assertFalse(service.ultimate(zen))

        run.step({})
        c = zen.body_script.component
        zen.body_script.set_ult_charge(c.entity_var(338).value())
        self.cast(run, zen, zen, 0x20)
        self.assertTrue(service.ultimate(zen))
        self.assertEqual(c.entity_var(342).value(), 0)
        for _ in range(350):
            run.step({})
        self.assertTrue(service.ultimate(zen))
        for _ in range(30):
            run.step({})
        self.assertFalse(service.ultimate(zen))

    def test_ultimate_repress_does_not_restart_its_six_second_lifetime(self):
        run, zen, _, _ = self.arena()
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.cast(run, zen, zen, 0x20)
        for i in range(380):
            run.step({zen: (0, 0, 0x20 if i % 2 else 0)})
        self.assertFalse(run.match.combat.zenyatta.ultimate(zen))

    def test_ultimate_restores_self_blocks_damage_heals_allies_but_not_enemies(self):
        run, zen, ally, enemy = self.arena()
        f = run.match.combat
        own = f.vitals_of(self.target(run, zen))
        own.damage(100)
        a = f.vitals_of(self.target(run, ally))
        e = f.vitals_of(self.target(run, enemy))
        a.damage(100)
        e.damage(100)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.cast(run, zen, zen, 0x20)
        self.assertEqual(own.total, 200)
        f._damage(enemy, self.target(run, zen), 500, False, None, run.frame)
        self.assertEqual(own.total, 200)
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(a.total, 148)
        self.assertEqual(e.total, 100)


if __name__ == "__main__":
    unittest.main()
