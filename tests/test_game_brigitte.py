"""Real 104319 flail input -> server health; reconstructed volume, no client claim."""

import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import bots, combat
from ow174.game.brigitte import HERO
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.content import heroes


class BrigitteTests(unittest.TestCase):
    def fixture(self, names=("Brigitte", "Enemy")):
        run = Run(KANEZAKA_DM, names)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        player = run.players[0]
        run.match.switch_hero(player, heroes()[HERO])
        return run, player

    def target(self, run, owner):
        return next(t for t in run.match.combat._targets() if t.owner is owner)

    def swing(self, run, player, frames=40):
        for _ in range(frames):
            run.step({player: (0, 0, FIRE)})

    def whip(self, run, player, frames=60):
        run.step({player: (0, 0, 8)})
        for _ in range(frames - 1):
            run.step({})

    def test_quick_melee_input_uses_flail35_instead_of_generic_melee30(self):
        for distance, expected in ((1.5, 165), (3, 165), (7, 200)):
            with self.subTest(distance=distance):
                run, player = self.fixture()
                enemy = run.players[1]
                x, y, z = player.mover.position
                enemy.mover.position, enemy.mover.body = [x, y, z + distance], None
                run.step({player: (0, 0, 0x800)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, expected)
                hits = player.client.messages(combat.HIT)
                self.assertEqual([v["+0x78"]["+0x4"] for v in hits], [35] if expected < 200 else [])

    def test_quick_melee_wall_occlusion_and_death_before_contact(self):
        from test_game_mover import box

        for wall in (False, True):
            with self.subTest(wall=wall):
                run, player = self.fixture()
                enemy = run.players[1]
                x, y, z = player.mover.position
                enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
                if wall:
                    collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                      + box(x - 5, x + 5, y, y + 5, z + 1, z + 1.1))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                run.step({player: (0, 0, 0x800)})
                if not wall:
                    run.match.combat._damage(enemy, self.target(run, player), 1000, False, None, run.frame)
                for _ in range(40):
                    run.step({})
                self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_input_causes_one_noncritical_hit_per_swing(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 3]
        self.swing(run, player)
        vitals = run.match.combat.vitals_of(self.target(run, enemy))
        self.assertEqual(vitals.total, 165)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([(v["+0x78"]["+0x4"], v["+0x78"]["+0x8"]) for v in hits], [(35, 0)])
        for _ in range(8):
            run.step({})
        self.swing(run, player)
        self.assertEqual(vitals.total, 130)

    def test_cleave_front_targets_and_reject_back_and_out_of_range(self):
        run, player = self.fixture(("Brigitte", "Front", "Side", "Back", "Far"))
        x, y, z = player.mover.position
        for owner, dx, dz in zip(run.players[1:], (0, 2, 0, 0), (3, 3, -3, 8), strict=True):
            owner.mover.position = [x + dx, y, z + dz]
        self.swing(run, player)
        self.assertEqual([run.match.combat.vitals_of(self.target(run, p)).total
                          for p in run.players[1:]], [165, 165, 200, 200])

    def test_static_wall_blocks_flail(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 3]
        wall = World([
            (x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
            (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50),
            (x - 5, y, z + 1, x + 5, y + 5, z + 1, x + 5, y, z + 1),
            (x - 5, y, z + 1, x - 5, y + 5, z + 1, x + 5, y + 5, z + 1),
        ])
        run.match.collision_provider = StaticCollisionProvider(wall)
        run.match.collision_world = wall
        self.swing(run, player)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)

    def test_allied_npc_is_not_damaged_and_enemy_corpse_is_retained(self):
        run, player = self.fixture(("Brigitte",))
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        x, y, z = player.mover.position
        for index, team in enumerate((player.team, 1 - player.team)):
            bot = bots.Bot(bots.BOT_ENTITY + index, {
                "hero": "0x02E000000000016B", "team": team, "position": (x + index, y, z + 3),
                "rotation": (0, 0, 0, 1), "graphs": [],
            }, {"name": "Combat fixture", "body": "0x0400000000001403", "health": 35}, {})
            bot.retain_corpse = True
            run.match.bots.append(bot)
        ally, enemy = run.match.bots
        self.swing(run, player)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, ally)).total, 35)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 0)
        self.assertTrue(enemy.dead)
        self.assertFalse(any(who is enemy for _, who in run.match.combat.respawns))
        for _ in range(210):
            run.step({})
        self.assertIn(enemy, run.match.bots)

    def test_armor_and_body_retirement(self):
        run, player = self.fixture()
        enemy = run.players[1]
        run.match.switch_hero(enemy, heroes()[HERO])
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 3]
        self.swing(run, player, 25)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 170)
        body = player.body
        self.assertIn(body, run.match.combat.brigitte.hits)
        run.match.switch_hero(player, player.hero)
        self.assertNotIn(body, run.match.combat.brigitte.hits)

    def test_inspire_heals_self_and_allies_over_time_without_healing_enemies(self):
        run, player = self.fixture(("Brigitte", "Enemy", "Ally"))
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        enemy, ally = run.players[1:]
        enemy.team, ally.team = 1 - player.team, player.team
        x, y, z = player.mover.position
        enemy.mover.position, ally.mover.position = [x, y, z + 3], [x, y, z - 3]
        fight = run.match.combat
        self_health = fight.vitals_of(self.target(run, player))
        allied_health = fight.vitals_of(self.target(run, ally))
        # Actual shared incoming-damage path, including Brigitte's armor.
        fight._damage(enemy, self.target(run, player), 100, False, None, run.frame)
        fight._damage(enemy, self.target(run, ally), 150, False, None, run.frame)
        before = self_health.total, allied_health.total
        self.swing(run, player, 20)
        self.assertGreater(allied_health.total, before[1])
        self.assertLess(allied_health.total, before[1] + 90)
        enemy_health = fight.vitals_of(self.target(run, enemy)).total
        for _ in range(400):
            run.step({})
        self.assertAlmostEqual(self_health.total, before[0] + 90, places=4)
        self.assertAlmostEqual(allied_health.total, before[1] + 90, places=4)
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, enemy_health)
        self.assertFalse(fight.brigitte.inspire)

    def test_inspire_in_free_for_all_does_not_heal_other_players(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 3]
        self.swing(run, player, 20)
        self.assertEqual(set(run.match.combat.brigitte.inspire), {(player.body, player.body)})

    def test_inspire_proc_cooldown_rejects_second_swing_and_refreshes_on_third(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 3]
        self.swing(run, player, 20)
        fight = run.match.combat
        first = fight.brigitte.inspire[player.body, player.body].until
        self.swing(run, player, 40)
        self.assertEqual(len(player.client.messages(combat.HIT)), 2)
        self.assertEqual(fight.brigitte.inspire[player.body, player.body].until, first)
        self.swing(run, player, 35)
        self.assertEqual(len(player.client.messages(combat.HIT)), 3)
        self.assertGreater(fight.brigitte.inspire[player.body, player.body].until, first + 1)
        self.assertEqual(len(fight.brigitte.inspire), 1)

    def test_actual_second_caster_replaces_inspire_instead_of_stacking_healing(self):
        run, first = self.fixture(("First", "Second", "Enemy", "Ally"))
        second, enemy, ally = run.players[1:]
        run.match.switch_hero(second, heroes()[HERO])
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        second.team = ally.team = first.team
        enemy.team = 1 - first.team
        x, y, z = first.mover.position
        second.mover.position, second.mover.body = [x + 1, y, z], None
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        ally.mover.position, ally.mover.body = [x, y, z - 3], None
        fight = run.match.combat
        target = self.target(run, ally)
        fight._damage(enemy, target, 150, False, None, run.frame)
        self.swing(run, first, 20)
        initial = fight.brigitte.inspire[first.body, ally.body]
        start = initial.until - 6
        self.swing(run, second, 20)
        self.assertNotIn((first.body, ally.body), fight.brigitte.inspire)
        replacement = fight.brigitte.inspire[second.body, ally.body]
        self.assertIs(replacement.healer, second)
        self.assertGreater(replacement.until, initial.until)
        self.assertEqual(sum(h.target == ally.body for h in fight.brigitte.inspire.values()), 1)
        expected = 50 + 15 * (replacement.until - start)
        for _ in range(400):
            run.step({})
        self.assertAlmostEqual(fight.vitals_of(target).total, expected, places=4)
        self.assertFalse(fight.brigitte.inspire)

    def test_issued_inspire_survives_actual_caster_body_replacement(self):
        run, source = self.fixture(("Brigitte", "Enemy", "Ally"))
        enemy, ally = run.players[1:]
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        ally.team, enemy.team = source.team, 1 - source.team
        x, y, z = source.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        ally.mover.position, ally.mover.body = [x, y, z - 3], None
        fight = run.match.combat
        target = self.target(run, ally)
        fight._damage(enemy, target, 150, False, None, run.frame)
        self.swing(run, source, 20)
        old_body = source.body
        self.assertIn((old_body, ally.body), fight.brigitte.inspire)
        run.match.switch_hero(source, next(h for h in heroes().values() if h.name == "Soldier: 76"))
        self.assertNotEqual(source.body, old_body)
        self.assertIn((old_body, ally.body), fight.brigitte.inspire)
        self.assertFalse(any(h.target == old_body for h in fight.brigitte.inspire.values()))
        run.match.switch_hero(source, heroes()[HERO])
        source.body_script.ult_seconds = 0
        source.body_script.set_ult_charge(0)
        for _ in range(400):
            run.step({})
        self.assertAlmostEqual(fight.vitals_of(target).total, 140, places=4)
        self.assertEqual(source.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.brigitte.inspire)

    def test_distant_second_caster_cannot_replace_an_existing_recipient_inspire(self):
        run, first = self.fixture(("First", "Second", "Enemy1", "Enemy2", "Ally"))
        second, enemy1, enemy2, ally = run.players[1:]
        run.match.switch_hero(second, heroes()[HERO])
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        second.team = ally.team = first.team
        enemy1.team = enemy2.team = 1 - first.team
        x, y, z = first.mover.position
        for owner, point in ((second, [x + 30, y, z]), (enemy1, [x, y, z + 3]),
                             (enemy2, [x + 30, y, z + 3]), (ally, [x, y, z - 3])):
            owner.mover.position, owner.mover.body = point, None
        fight = run.match.combat
        target = self.target(run, ally)
        fight._damage(enemy1, target, 150, False, None, run.frame)
        self.swing(run, first, 20)
        original = fight.brigitte.inspire[first.body, ally.body]
        self.swing(run, second, 20)
        self.assertIs(fight.brigitte.inspire[first.body, ally.body], original)
        self.assertNotIn((second.body, ally.body), fight.brigitte.inspire)
        self.assertIn((second.body, second.body), fight.brigitte.inspire)  # actual proc occurred
        for _ in range(400):
            run.step({})
        self.assertAlmostEqual(fight.vitals_of(target).total, 140, places=4)

    def test_inspire_does_not_revive_dead_ally(self):
        run, player = self.fixture(("Brigitte", "Enemy", "Ally"))
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        enemy, ally = run.players[1:]
        enemy.team, ally.team = 1 - player.team, player.team
        x, y, z = player.mover.position
        enemy.mover.position, ally.mover.position = [x, y, z + 3], [x, y, z - 3]
        self.swing(run, player, 20)
        fight = run.match.combat
        target = self.target(run, ally)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        for _ in range(10):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 0)
        self.assertTrue(fight.vitals_of(target).dead)
        self.assertNotIn((player.body, ally.body), fight.brigitte.inspire)

    def test_whip_flies_before_damage_and_impulses_player(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 15]
        fight = run.match.combat
        target = self.target(run, enemy)
        self.whip(run, player, 14)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(len(fight.brigitte.whips), 1)
        self.assertLess(fight.brigitte.whips[0].remaining, 20)
        for _ in range(16):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 130)
        self.assertGreater(enemy.mover.position[2], z + 15)
        self.assertFalse(fight.brigitte.whips)
        self.assertIn((player.body, player.body), fight.brigitte.inspire)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in player.client.messages(combat.HIT)], [70])

    def test_whip_reads_live_knockback_rule_at_contact_without_changing_damage(self):
        for late, scalar in ((False, 0), (True, 0), (True, 2)):
            with self.subTest(late=late, scalar=scalar):
                run, player = self.fixture()
                enemy = run.players[1]
                x, y, z = player.mover.position
                enemy.mover.position, enemy.mover.body = [x, y, z + 15], None
                fight = run.match.combat
                target = self.target(run, enemy)
                observed = []
                fly = type(fight.brigitte)._fly_whips

                def observe(bridge, *, match_fight=fight, victim=target, source_fly=fly,
                            velocities=observed, actor=enemy):
                    before = match_fight.vitals_of(victim).total
                    result = source_fly(bridge)
                    if match_fight.vitals_of(victim).total < before:
                        velocities.append(actor.mover.velocity)
                    return result

                if not late:
                    player.body_script.component.set(14131, scalar)
                with patch.object(type(fight.brigitte), "_fly_whips", observe):
                    self.whip(run, player, 14)
                    self.assertEqual(len(fight.brigitte.whips), 1)
                    self.assertEqual(fight.vitals_of(target).total, 200)
                    if late:
                        player.body_script.component.set(14131, scalar)
                    for _ in range(30):
                        run.step({})
                self.assertEqual(fight.vitals_of(target).total, 130)
                self.assertEqual(len(observed), 1)
                self.assertEqual(observed[0], (0.0, 3.0 * scalar, 25.0 * scalar))

    def test_whip_range_miss_and_allied_target_filter(self):
        for dx, dz, allied in ((0, 22, False), (4, 12, False), (0, 12, True)):
            with self.subTest(dx=dx, dz=dz, allied=allied):
                run, player = self.fixture()
                enemy = run.players[1]
                run.match.game_map = run.match.game_map.__class__(**{
                    **run.match.game_map.__dict__, "free_for_all": False,
                })
                enemy.team = player.team if allied else 1 - player.team
                x, y, z = player.mover.position
                enemy.mover.position = [x + dx, y, z + dz]
                self.whip(run, player)
                self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)
                self.assertFalse(run.match.combat.brigitte.whips)

    def test_whip_cooldown_blocks_recast_and_allows_after_expiry(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 12]
        enemy.mover.body = None
        self.whip(run, player)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1259)
        self.assertGreater(instance.vars[61].value(), 3)
        original = instance.states[6].counter
        self.whip(run, player)
        self.assertEqual(instance.states[6].counter, original)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)
        for _ in range(220):
            run.step({})
        self.assertEqual(instance.vars[61].value(), 0)
        enemy.mover.position = [x, y, z + 12]
        self.whip(run, player)
        self.assertGreater(instance.states[6].counter, original)
        self.assertEqual(len(player.client.messages(combat.HIT)), 2)

    def test_whip_does_not_launch_after_source_dies_during_windup(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 15]
        run.step({player: (0, 0, 8)})
        fight = run.match.combat
        fight._damage(enemy, self.target(run, player), 1000, False, None, run.frame)
        for _ in range(60):
            run.step({player: (0, 0, 8)})
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 200)
        self.assertFalse(fight.brigitte.whips)

    def test_whip_contact_is_occluded_by_static_wall(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 12]
        wall = World([
            (x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
            (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50),
            (x - 5, y, z + 5, x + 5, y + 5, z + 5, x + 5, y, z + 5),
            (x - 5, y, z + 5, x - 5, y + 5, z + 5, x + 5, y + 5, z + 5),
        ])
        run.match.collision_provider = StaticCollisionProvider(wall)
        run.match.collision_world = wall
        self.whip(run, player)
        self.assertEqual(run.match.combat.vitals_of(self.target(run, enemy)).total, 200)
        self.assertFalse(player.client.messages(combat.HIT))
        self.assertFalse(run.match.combat.brigitte.whips)

    def test_whip_death_during_outbound_flight_cancels_contact(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 19]
        self.whip(run, player, 14)
        fight = run.match.combat
        self.assertEqual(len(fight.brigitte.whips), 1)
        fight._damage(enemy, self.target(run, player), 1000, False, None, run.frame)
        for _ in range(40):
            run.step({})
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 200)
        self.assertFalse(fight.brigitte.whips)

    def test_whip_accepts_a_press_inside_authored_early_window(self):
        run, player = self.fixture()
        enemy = run.players[1]
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 12]
        self.whip(run, player)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1259)
        original = instance.states[6].counter
        while instance.vars[61].value() > 0.09:
            run.step({})
        self.assertGreater(instance.vars[61].value(), 0)
        enemy.mover.position, enemy.mover.velocity = [x, y, z + 12], (0, 0, 0)
        enemy.mover.body = None
        self.whip(run, player)
        self.assertEqual(instance.states[6].counter, original + 1)
        self.assertEqual(len(player.client.messages(combat.HIT)), 2)

    def test_whip_lethal_generic_npc_preserves_corpse(self):
        run, player = self.fixture(("Brigitte",))
        x, y, z = player.mover.position
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": 1 - player.team, "position": (x, y, z + 12),
            "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Generic combat fixture", "body": "0x0400000000001403", "health": 70}, {})
        bot.retain_corpse = True
        run.match.bots.append(bot)
        self.whip(run, player)
        fight = run.match.combat
        self.assertEqual(fight.vitals_of(self.target(run, bot)).total, 0)
        self.assertTrue(bot.dead)
        self.assertFalse(any(who is bot for _, who in fight.respawns))


if __name__ == "__main__":
    unittest.main()
