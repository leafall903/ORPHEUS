"""Production Combat integration for reconstructed support services; no client claims."""

import unittest

from test_game_combat import Run, aim
from test_game_mover import box
from test_game_mover import wall as wall_triangles

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes
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

    def test_hero_switch_ends_transcendence_and_new_zen_takes_damage(self):
        run, zen, ally, enemy = self.arena()
        fight = run.match.combat
        ally_vitals = fight.vitals_of(self.target(run, ally))
        ally_vitals.damage(150)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.cast(run, zen, zen, 0x20)
        for _ in range(5):
            run.step({})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertAlmostEqual(ally_vitals.total, 74, places=4)
        healed = ally_vitals.total
        old_body = zen.body
        position = list(zen.mover.position)
        self.assertTrue(run.match.switch_hero(zen, heroes()[SOLDIER]))
        self.assertNotIn(old_body, fight.zenyatta.owners)
        self.assertTrue(run.match.switch_hero(zen, heroes()[HERO]))
        self.assertNotEqual(zen.body, old_body)
        zen.mover.position = position
        zen.mover.body = None
        for _ in range(5):
            run.step({})
            self.assertFalse(fight.zenyatta.ultimate(zen))
            self.assertEqual(ally_vitals.total, healed)
        self.cast(run, zen, zen, 0x20)
        self.assertFalse(fight.zenyatta.ultimate(zen))
        self.cast(run, enemy, zen, FIRE)
        for _ in range(10):
            run.step({})
        self.assertLess(fight.vitals_of(self.target(run, zen)).total, 200)
        self.assertEqual(ally_vitals.total, healed)

    def test_active_transcendence_retargets_replaced_ally_body(self):
        run, zen, ally, _ = self.arena()
        fight = run.match.combat
        old_body = ally.body
        old_vitals = fight.vitals_of(self.target(run, ally))
        old_vitals.damage(150)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.cast(run, zen, zen, 0x20)
        run.step({})
        self.assertAlmostEqual(old_vitals.total, 54.8, places=4)
        position = list(ally.mover.position)
        self.assertTrue(run.match.switch_hero(ally, heroes()[HERO]))
        self.assertNotEqual(ally.body, old_body)
        ally.mover.position = position
        ally.mover.body = None
        new_vitals = fight.vitals_of(self.target(run, ally))
        new_vitals.damage(100)
        for _ in range(5):
            run.step({})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertAlmostEqual(new_vitals.total, 124, places=4)
        self.assertAlmostEqual(old_vitals.total, 54.8, places=4)
        self.assertNotIn(old_body, fight.vitals)

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

    def test_restoring_los_resets_only_that_orbs_expiry(self):
        for restored in ("harmony", "discord"):
            with self.subTest(restored=restored):
                run, zen, ally, enemy = self.arena()
                fight = run.match.combat
                self.cast(run, zen, ally, 16)
                self.cast(run, zen, enemy, 8)
                owner = fight.zenyatta.owners[zen.body]
                self.assertEqual(owner.harmony.target, ally.body)
                self.assertEqual(owner.discord.target, enemy.body)
                self.wall(run, zen)
                for _ in range(115):
                    run.step({})
                self.assertIsNotNone(owner.harmony)
                self.assertIsNotNone(owner.discord)

                x, y, z = zen.mover.position
                # At z+4, the ally ray crosses x and the enemy ray crosses x+3.
                blocked_x = x+3 if restored == "harmony" else x
                run.match.collision_world = World(
                    box(x-50, x+50, y-2, y, z-50, z+50)
                    + wall_triangles(blocked_x-1, z+4, blocked_x+1, z+4, y-1, y+5)
                )
                for _ in range(80):
                    run.step({})
                other = "discord" if restored == "harmony" else "harmony"
                self.assertIsNotNone(getattr(owner, restored))
                self.assertIsNone(getattr(owner, other))
                if restored == "harmony":
                    health = fight.vitals_of(self.target(run, ally))
                    health.damage(20)
                    for _ in range(20):
                        run.step({})
                    self.assertGreater(health.total, 180)
                else:
                    health = fight.vitals_of(self.target(run, enemy))
                    ex, ey, ez = enemy.mover.position
                    direction = aim(fight._eye(ally), (ex, ey+1, ez))
                    run.step({ally: (*direction, FIRE)})
                    self.assertEqual(health.total, 175)

                self.wall(run, zen)
                for _ in range(180):
                    run.step({})
                self.assertIsNotNone(getattr(owner, restored))
                for _ in range(10):
                    run.step({})
                self.assertIsNone(getattr(owner, restored))

    def test_replacing_one_orb_target_preserves_the_other_orbs_effect(self):
        for replaced in ("harmony", "discord"):
            with self.subTest(replaced=replaced):
                run, zen, ally, enemy = self.arena()
                fight = run.match.combat
                self.cast(run, zen, ally, 16)
                self.cast(run, zen, enemy, 8)
                owner = fight.zenyatta.owners[zen.body]
                self.assertEqual(owner.harmony.target, ally.body)
                self.assertEqual(owner.discord.target, enemy.body)
                target = ally if replaced == "harmony" else enemy
                original = target.body
                position = list(target.mover.position)
                self.assertTrue(run.match.switch_hero(target, target.hero))
                self.assertNotEqual(target.body, original)
                target.mover.position = position
                target.mover.body = None
                for _ in range(12):
                    run.step({})
                self.assertIsNone(getattr(owner, replaced))
                survivor = owner.discord if replaced == "harmony" else owner.harmony
                self.assertEqual(survivor.target, enemy.body if replaced == "harmony" else ally.body)

                ally_hp = fight.vitals_of(self.target(run, ally))
                ally_hp.damage(20)
                enemy_hp = fight.vitals_of(self.target(run, enemy))
                x, y, z = enemy.mover.position
                direction = aim(fight._eye(zen), (x, y+1, z))
                run.step({zen: (*direction, FIRE)})
                for _ in range(35):
                    run.step({})
                self.assertEqual(enemy_hp.total, 140 if replaced == "harmony" else 152)
                if replaced == "harmony":
                    self.assertEqual(ally_hp.total, 180)
                else:
                    self.assertGreater(ally_hp.total, 180)

                self.cast(run, zen, target, 16 if replaced == "harmony" else 8)
                self.assertEqual(getattr(owner, replaced).target, target.body)

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

    def test_actual_target_death_retires_only_its_orb(self):
        for lost in ("harmony", "discord"):
            with self.subTest(lost=lost):
                run, zen, ally, enemy = self.arena()
                fight = run.match.combat
                self.cast(run, zen, ally, 16)
                self.cast(run, zen, enemy, 8)
                owner = fight.zenyatta.owners[zen.body]
                self.assertEqual(owner.harmony.target, ally.body)
                self.assertEqual(owner.discord.target, enemy.body)
                ally_hp = fight.vitals_of(self.target(run, ally))
                enemy_hp = fight.vitals_of(self.target(run, enemy))
                killer, victim = (enemy, ally) if lost == "harmony" else (zen, enemy)
                hp = ally_hp if victim is ally else enemy_hp
                hp.damage(190)
                x, y, z = victim.mover.position
                direction = aim(fight._eye(killer), (x, y+1, z))
                run.step({killer: (*direction, FIRE)})
                for _ in range(20):
                    run.step({})
                self.assertTrue(hp.dead)
                self.assertEqual(hp.total, 0)
                self.assertIsNone(getattr(owner, lost))

                if lost == "harmony":
                    self.assertEqual(owner.discord.target, enemy.body)
                    x, y, z = enemy.mover.position
                    direction = aim(fight._eye(zen), (x, y+1, z))
                    run.step({zen: (*direction, FIRE)})
                    for _ in range(20):
                        run.step({})
                    self.assertEqual(enemy_hp.total, 140)
                else:
                    self.assertEqual(owner.harmony.target, ally.body)
                    ally_hp.damage(20)
                    for _ in range(20):
                        run.step({})
                    self.assertGreater(ally_hp.total, 180)
                self.assertEqual(hp.total, 0)

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

    def test_actual_damage_and_harmony_charge_ultimate_and_cap_at_cost(self):
        run, zen, ally, enemy = self.arena()
        fight = run.match.combat
        zen.body_script.ult_seconds = 0
        component = zen.body_script.component
        charge = component.entity_var(342)
        cost = component.entity_var(338).value()
        zen.body_script.set_ult_charge(0)
        enemy_hp = fight.vitals_of(self.target(run, enemy))
        ally_hp = fight.vitals_of(self.target(run, ally))
        ally_hp.damage(10)
        x, y, z = enemy.mover.position
        direction = aim(fight._eye(zen), (x, y+1, z))
        run.step({zen: (*direction, FIRE)})
        for _ in range(25):
            run.step({})
        self.assertEqual(enemy_hp.total, 152)
        self.assertEqual(charge.value(), 48)
        self.cast(run, zen, ally, 16)
        for _ in range(48):
            run.step({})
        self.assertEqual(ally_hp.total, 200)
        self.assertAlmostEqual(charge.value(), 58, places=4)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(charge.value(), 58, places=4)
        zen.body_script.set_ult_charge(cost-5)
        run.step({zen: (*direction, FIRE)})
        for _ in range(25):
            run.step({})
        self.assertEqual(enemy_hp.total, 104)
        self.assertEqual(charge.value(), cost)

    def test_actual_lethal_hit_removes_both_orbs_and_their_combat_effects(self):
        run, zen, ally, enemy = self.arena()
        fight = run.match.combat
        self.cast(run, zen, ally, 16)
        self.cast(run, zen, enemy, 8)
        body = zen.body
        owner = fight.zenyatta.owners[body]
        self.assertEqual(owner.harmony.target, ally.body)
        self.assertEqual(owner.discord.target, enemy.body)
        zen_hp = fight.vitals_of(self.target(run, zen))
        ally_hp = fight.vitals_of(self.target(run, ally))
        enemy_hp = fight.vitals_of(self.target(run, enemy))
        zen_hp.damage(180)
        x, y, z = zen.mover.position
        lethal_aim = aim(fight._eye(enemy), (x, y+1, z))
        run.step({enemy: (*lethal_aim, FIRE)})
        self.assertTrue(zen_hp.dead)
        self.assertEqual(fight.zenyatta.boost(self.target(run, enemy)), 1)
        run.step({})
        self.assertNotIn(body, fight.zenyatta.owners)
        self.assertEqual(enemy.client.messages(combat.HIT)[-1]["+0x78"]["+0x8"], combat.KILLING)

        ally_hp.damage(25)
        x, y, z = enemy.mover.position
        direction = aim(fight._eye(ally), (x, y+1, z))
        run.step({ally: (*direction, FIRE)})
        for _ in range(40):
            run.step({})
        self.assertEqual(ally_hp.total, 175)
        self.assertEqual(enemy_hp.total, 180)

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

    def test_passive_ultimate_charge_uses_command_time_and_pauses_during_transcendence(self):
        run, zen, _, _ = self.arena()
        fight = run.match.combat
        component = zen.body_script.component
        charge = component.entity_var(342)
        cost = component.entity_var(338).value()
        zen.body_script.set_ult_charge(0)
        for _ in range(125):
            run.step({})
        self.assertAlmostEqual(charge.value(), 10, places=4)
        zen.body_script.set_ult_charge(cost-.04)
        run.step({})
        self.assertEqual(charge.value(), cost)
        run.step({zen: (0, 0, 32)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(charge.value(), 0)
        for _ in range(350):
            run.step({})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(charge.value(), 0)
        for _ in range(40):
            run.step({})
        self.assertFalse(fight.zenyatta.ultimate(zen))
        after = charge.value()
        for _ in range(125):
            run.step({})
        self.assertAlmostEqual(charge.value()-after, 10, places=4)

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
