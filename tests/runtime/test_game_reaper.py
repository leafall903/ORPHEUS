"""Real body-graph commands through Combat; no client acceptance is implied."""

import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bots, combat, reaper
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.script import nodes


class ReaperTests(unittest.TestCase):
    def scenario(self, *, team_mode=False, names=("Reaper", "Target")):
        run = Run(PRACTICE_RANGE if team_mode else KANEZAKA_DM, names)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        owner, victim = run.players[:2]
        run.match.switch_hero(owner, heroes()[reaper.HERO])
        x, y, z = owner.mover.position
        victim.mover.position = [x, y, z + 1.5]
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        targets = {target.entity: target for target in fight._targets()}
        for target in targets.values():
            fight.vitals_of(target)
        return run, owner, victim, targets[victim.body]

    def fire(self, run, owner, victim):
        x, y, z = victim.mover.position
        yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 1.0, z))
        run.step({owner: (yaw, pitch, 1)})

    def test_one_shell_has_twenty_independent_damage_applications(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        self.fire(run, owner, victim)
        self.assertAlmostEqual(fight.vitals_of(target).total, 80.0)
        hits = owner.client.messages(combat.HIT)
        self.assertEqual(len(hits), 20)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [6] * 20)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x19)
        self.assertEqual(weapon.vars[53].value(), 7)

    def test_pellet_armour_reduction_and_actual_damage_lifesteal(self):
        run, owner, victim, _ = self.scenario()
        fight = run.match.combat
        fight.vitals[victim.body] = combat.Vitals(victim.body, 200, armour=100)
        fight.vitals[owner.body].damage(100)
        self.fire(run, owner, victim)
        self.assertAlmostEqual(fight.vitals[victim.body].total, 240.0)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 150 + 60 * reaper.REAPING_RATIO)

    def test_only_available_enemy_health_is_stolen_on_a_kill(self):
        run, owner, victim, _ = self.scenario()
        fight = run.match.combat
        fight.vitals[victim.body] = combat.Vitals(victim.body, 5)
        fight.vitals[owner.body].damage(100)
        self.fire(run, owner, victim)
        self.assertTrue(fight.is_dead(victim))
        self.assertAlmostEqual(fight.vitals[owner.body].total, 150 + 5 * reaper.REAPING_RATIO)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
        self.assertTrue(victim.body_script.component.owner_dead)

    def test_misses_and_map_occlusion_do_not_heal(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, owner, victim, target = self.scenario()
                fight = run.match.combat
                fight.vitals[owner.body].damage(100)
                with patch.object(combat, "ray_world", return_value=0.01 if wall else None):
                    if wall:
                        self.fire(run, owner, victim)
                    else:
                        run.step({owner: (-32768, 0, 1)})
                self.assertEqual(fight.vitals[owner.body].total, 150)
                self.assertEqual(fight.vitals_of(target).total, 200)

    def test_friendly_targets_cannot_fund_reaping(self):
        run, owner, victim, target = self.scenario(team_mode=True)
        victim.team = owner.team
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        fight.vitals[owner.body].damage(100)
        self.fire(run, owner, victim)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(fight.vitals[owner.body].total, 150)
        fight._reaping(owner, target, 100)
        self.assertEqual(fight.vitals[owner.body].total, 150)

    def test_melee_reaps_once_and_full_health_is_capped(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        fight.vitals[owner.body].damage(5)
        x, y, z = victim.mover.position
        yaw, pitch = aim(fight._eye(owner), (x, y + 1.0, z))
        run.step({owner: (yaw, pitch, 0x800)})
        for _ in range(40):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 170)
        self.assertEqual(fight.vitals[owner.body].total, 250)

    def test_dead_reaper_cannot_fire_or_be_revived_by_stale_damage(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        own_target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(victim, own_target, 1000, False, None, run.frame)
        self.fire(run, owner, victim)
        self.assertEqual(fight.vitals_of(target).total, 200)
        fight._damage(owner, target, 10, False, None, run.frame)
        self.assertEqual(fight.vitals[owner.body].total, 0)

    def test_npc_damage_uses_same_pellets_and_retained_death_policy(self):
        run, owner, victim, _ = self.scenario()
        victim.spawned = False
        fight = run.match.combat
        npc = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": owner.team + 1,
            "position": tuple(victim.mover.position), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Training Bot", "body": "0x0400000000001403", "health": 100}, {})
        npc.retain_corpse = True
        run.match.bots.append(npc)
        for _ in range(2):
            run.step({})
        fight.vitals[owner.body].damage(100)
        self.fire(run, owner, victim)
        self.assertTrue(npc.dead)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 150 + 100 * reaper.REAPING_RATIO)
        self.assertFalse(any(who is npc for _, who in fight.respawns))
        npc_target = next(t for t in fight._targets() if t.owner is npc)
        fight._damage(owner, npc_target, 100, False, None, run.frame)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 150 + 100 * reaper.REAPING_RATIO)

    def test_spread_reaches_off_axis_targets_and_uses_normalized_rings(self):
        run, owner, victim, target = self.scenario()
        state = next(i for i in owner.body_script.component.instances.values()
                     if i.graph.index == 0x19).state(6)
        rays = reaper.shotgun_directions(owner, state, (0, 0, 1))
        self.assertEqual(len(rays), 20)
        self.assertNotEqual(rays[0], (0, 0, 1))  # authored0.6-degree jitter also moves the centre pellet
        self.assertEqual(len(set(rays)), 20)
        self.assertTrue(all(abs(sum(v * v for v in ray) - 1) < 3e-7 for ray in rays))
        x, y, z = owner.mover.position
        victim.mover.position = [x + 0.9, y, z + 10]
        for _ in range(2):
            run.step({})
        # Central ray misses the body; at least one outer-ring pellet hits it.
        run.step({owner: (0, 0, 1)})
        self.assertLess(run.match.combat.vitals_of(target).total, 200)

    def test_other_heroes_keep_single_ray_and_do_not_reap(self):
        run, owner, victim, _ = self.scenario()
        run.match.switch_hero(owner, victim.hero)
        fight = run.match.combat
        fight.vitals_of(next(t for t in fight._targets() if t.owner is owner)).damage(100)
        self.fire(run, owner, victim)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
        self.assertEqual(fight.vitals[owner.body].total, 100)

    def test_wraith_immunity_duration_recovery_and_cooldown_reuse(self):
        run, owner, victim, _ = self.scenario()
        fight = run.match.combat
        own_target = next(t for t in fight._targets() if t.owner is owner)
        instance = owner.body_script.component.instances[1]
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.assertNotIn(owner.body, [t.entity for t in fight._enemies(victim)])
        fight._damage(victim, own_target, 100, False, None, run.frame)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        for _ in range(187):
            run.step({})
        self.assertTrue(reaper.intangible(owner))
        run.step({})
        self.assertFalse(reaper.intangible(owner))
        self.assertTrue(instance.state(7).active)  # 0.4-second weapon recovery
        fight._damage(victim, own_target, 10, False, None, run.frame)
        self.assertEqual(fight.vitals[owner.body].total, 240)
        for _ in range(25):
            run.step({})
        self.assertGreater(instance.vars[61].value(), 7.9)
        counter = instance.state(1).counter
        run.step({owner: (0, 0, 8)})
        for _ in range(510):
            run.step({})
        # A press made deep in cooldown must expire, not refresh its own timestamp
        # until it silently starts the next Wraith as soon as cooldown ends.
        self.assertEqual(instance.state(1).counter, counter)
        self.assertEqual(instance.vars[61].value(), 0)
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(instance.state(1).counter, counter + 1)

    def test_wraith_primary_cancel_after_minimum_time_starts_cooldown(self):
        run, owner, victim, target = self.scenario()
        run.step({owner: (0, 0, 8)})
        for _ in range(30):
            run.step({})
        self.assertTrue(reaper.intangible(owner))
        self.fire(run, owner, victim)
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        for _ in range(26):
            run.step({})
        self.assertGreater(owner.body_script.component.instances[1].vars[61].value(), 7.8)

    def test_wraith_cooldown_retires_on_short_mode_respawn(self):
        run, owner, victim, _ = self.scenario(team_mode=True)
        owner.team, victim.team = 0, 1
        fight = run.match.combat
        old = owner.body_script
        old_body = owner.body
        run.step({owner: (0, 0, 8)})
        for _ in range(30):
            run.step({})
        self.fire(run, owner, victim)
        self.assertFalse(reaper.intangible(owner))
        for _ in range(26):
            run.step({})
        wraith = next(i for i in old.component.instances.values() if i.graph.index == reaper.WRAITH_GRAPH)
        delay = combat.respawn_seconds(run.match)
        self.assertGreater(wraith.vars[61].value(), delay)
        fight.vitals[old_body].damage(249)
        self.fire(run, victim, owner)
        self.assertTrue(fight.is_dead(owner))
        for _ in range(round(delay / combat.TICK) + 2):
            if owner.body != old_body:
                break
            run.step({})
        self.assertNotEqual(owner.body, old_body)
        self.assertTrue(old.retired)
        x, y, z = owner.mover.position
        victim.mover.position[:] = (x, y, z + 1.5)
        victim.mover.body = None
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        self.fire(run, victim, owner)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        for _ in range(210):
            run.step({})
        self.assertFalse(reaper.intangible(owner))
        self.fire(run, victim, owner)
        self.assertLess(fight.vitals[owner.body].total, 250)

    def test_blossom_full_channel_deals_fifteen_ticks_and_reaps_actual_damage(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
        fight.vitals[owner.body].damage(200)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertEqual(fight.vitals_of(target).total, 966)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        for _ in range(190):
            run.step({})
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(fight.vitals_of(target).total, 490)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 50 + 510 * reaper.REAPING_RATIO)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 15)
        for _ in range(30):
            run.step({owner: (0, 0, 1)})
        self.assertGreater(fight.vitals_of(target).total, 0)  # the ultimate did not loop

    def test_blossom_needs_full_charge_and_cannot_charge_during_the_channel(self):
        run, owner, _, target = self.scenario()
        owner.body_script.set_ult_charge(0)
        run.step({owner: (0, 0, 32)})
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        run.step({})
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        for _ in range(20):
            run.step({owner: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_blossom_world_clock_expires_without_owner_input_or_duplicate_damage(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        at = run.frame + 250
        run.match.update(at * combat.TICK, at)
        self.assertEqual(fight.vitals_of(target).total, 490)
        self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
        self.assertIsNone(reaper.blossom_state(owner.body_script))  # missing windows expire the real scope
        fight.command(owner, Command(run.frame))
        run.match.update((at + 1) * combat.TICK, at + 1)
        self.assertEqual(fight.vitals_of(target).total, 490)

    def test_blossom_uses_target_los_range_and_faction(self):
        for reason in ("ally", "range", "wall"):
            with self.subTest(reason=reason):
                run, owner, victim, target = self.scenario(team_mode=reason == "ally")
                if reason == "ally":
                    victim.team = owner.team
                elif reason == "range":
                    victim.mover.position[2] += 20
                    victim.mover.body = None
                owner.body_script.set_ult_charge(2100)
                with patch.object(combat, "ray_world", return_value=0.01 if reason == "wall" else None):
                    run.step({owner: (0, 0, 32)})
                    for _ in range(40):
                        run.step({})
                self.assertEqual(run.match.combat.vitals_of(target).total, 200)

    def test_blossom_hits_multiple_enemies_without_critical_multiplier(self):
        run, owner, victim, _ = self.scenario(names=("Reaper", "Target", "Other"))
        other = run.players[2]
        x, y, z = owner.mover.position
        other.mover.position = [x - 2, y, z]
        other.mover.body = None
        fight = run.match.combat
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, -16000, 32)})
        self.assertEqual(fight.vitals[victim.body].total, 166)
        self.assertEqual(fight.vitals[other.body].total, 166)
        self.assertEqual([h["+0x78"]["+0x4"] for h in owner.client.messages(combat.HIT)], [34, 34])

    def test_blossom_interruption_and_death_stop_damage_and_body_retirement_clears_clock(self):
        for reason in ("interrupt", "death", "switch"):
            with self.subTest(reason=reason):
                run, owner, victim, target = self.scenario()
                fight = run.match.combat
                fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32)})
                body = owner.body
                if reason == "interrupt":
                    self.assertTrue(reaper.interrupt_blossom(owner))
                elif reason == "death":
                    own_target = next(t for t in fight._targets() if t.owner is owner)
                    fight._damage(victim, own_target, 1000, False, None, run.frame)
                else:
                    run.match.switch_hero(owner, victim.hero)
                    self.assertNotIn(body, fight.reaper_blossoms)
                for _ in range(40):
                    run.step({})
                self.assertEqual(fight.vitals_of(target).total, 966)

    def test_blossom_target_entry_and_reentry_do_not_backfill_missed_time(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
        original = list(victim.mover.position)
        victim.mover.position[2] += 20
        victim.mover.body = None
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 1000)
        victim.mover.position[:] = original
        victim.mover.body = None
        run.step({})
        self.assertEqual(fight.vitals_of(target).total, 966)
        victim.mover.position[2] += 20
        victim.mover.body = None
        run.step({})
        for _ in range(25):
            run.step({})
        victim.mover.position[:] = original
        victim.mover.body = None
        run.step({})
        self.assertEqual(fight.vitals_of(target).total, 932)

    def test_blossom_armour_applies_per_effect_pulse_and_personal_shields_take_damage(self):
        for pool in ("armour", "shields"):
            with self.subTest(pool=pool):
                run, owner, victim, target = self.scenario()
                fight = run.match.combat
                fight.vitals[victim.body] = combat.Vitals(victim.body, 1000, **{pool: 1000})
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32)})
                for _ in range(190):
                    run.step({})
                expected = 15 * (29 if pool == "armour" else 34)
                self.assertEqual(fight.vitals_of(target).total, 2000 - expected)

    def test_blossom_cannot_damage_an_enemy_in_wraith(self):
        run, owner, victim, target = self.scenario()
        run.match.switch_hero(victim, heroes()[reaper.HERO])
        target = next(t for t in run.match.combat._targets() if t.owner is victim)
        run.step({victim: (0, 0, 8)})
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertEqual(run.match.combat.vitals_of(target).total, 250)
        self.assertFalse(owner.client.messages(combat.HIT))

    def test_shotgun_headshots_use_scoped_two_times_default(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
        run.step({owner: (0, 0, 1)})
        self.assertEqual(fight.vitals_of(target).total, 760)
        hits = owner.client.messages(combat.HIT)
        self.assertEqual(len(hits), 20)
        self.assertTrue(all(h["+0x78"]["+0x8"] & combat.CRITICAL for h in hits))

    def test_shotgun_falloff_and_hundred_metre_weapon_range_affect_real_health(self):
        for distance, expected_pellet in ((5, 6), (22, 1.8), (102, None)):
            with self.subTest(distance=distance):
                run, owner, victim, target = self.scenario()
                x, y, z = owner.mover.position
                victim.mover.position[:] = [x, y, z + distance]
                victim.mover.body = None
                fight = run.match.combat
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    self.fire(run, owner, victim)
                if expected_pellet is None:
                    self.assertEqual(fight.vitals_of(target).total, 200)
                    self.assertFalse(damage.called)
                else:
                    self.assertTrue(damage.called)
                    for call in damage.call_args_list:
                        self.assertAlmostEqual(call.args[2], expected_pellet * (2 if call.args[3] else 1),
                                               places=5)
                    self.assertAlmostEqual(fight.vitals_of(target).total,
                                           200 - sum(call.args[2] for call in damage.call_args_list),
                                           places=4)

    def test_empty_clip_auto_reload_preserves_fire_lock_and_refills_eight_shells(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        fight.vitals[victim.body] = combat.Vitals(victim.body, 10000)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x19)
        for _ in range(250):
            self.fire(run, owner, victim)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(fight.vitals_of(target).total, 10000 - 8 * 120)
        for _ in range(60):
            self.fire(run, owner, victim)
        self.assertEqual(weapon.vars[53].value(), 8)
        self.assertEqual(fight.vitals_of(target).total, 10000 - 8 * 120)
        for _ in range(80):
            self.fire(run, owner, victim)
        self.assertLess(weapon.vars[53].value(), 8)
        self.assertLess(fight.vitals_of(target).total, 10000 - 8 * 120)

    def test_wraith_actual_movement_speed_and_collision_restore(self):
        run, owner, victim, _ = self.scenario()
        victim.spawned = False

        def move(buttons=0):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            for player in run.players:
                player.client.queue_entities(run.match.remote_movements(player))
                player.take_commands([Command(
                    run.frame, forward=127 if player is owner else 0,
                    buttons=buttons if player is owner else 0,
                )])

        for _ in range(30):
            move()
        self.assertAlmostEqual(owner.mover.velocity[2], 5.5)
        self.assertTrue(owner.mover.values.character_collision)
        start = owner.mover.position[2]
        move(8)
        for _ in range(30):
            move()
        self.assertAlmostEqual(owner.mover.velocity[2], 8.25)
        self.assertGreater(owner.mover.position[2] - start, 3.8)
        self.assertFalse(owner.mover.values.character_collision)
        for _ in range(210):
            move()
        self.assertFalse(reaper.intangible(owner))
        self.assertAlmostEqual(owner.mover.velocity[2], 5.5)
        self.assertTrue(owner.mover.values.character_collision)

    def test_manual_reload_refill_and_held_primary_fire_lock(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        fight.vitals[victim.body] = combat.Vitals(victim.body, 10000)
        self.fire(run, owner, victim)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x19)
        run.step({owner: (0, 0, 0x400)})
        self.assertEqual(weapon.vars[53].value(), 7)
        for _ in range(60):
            self.fire(run, owner, victim)
        self.assertEqual(weapon.vars[53].value(), 8)
        self.assertEqual(fight.vitals_of(target).total, 9880)
        for _ in range(80):
            self.fire(run, owner, victim)
        self.assertEqual(weapon.vars[53].value(), 7)
        self.assertAlmostEqual(fight.vitals_of(target).total, 9760)

    def test_lethal_enemy_shot_during_reload_cannot_refill_old_body(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        self.fire(run, owner, victim)
        script = owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x19)
        run.step({owner: (0, 0, 0x400)})
        for _ in range(15):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 7)
        own = fight.vitals[owner.body]
        own.damage(249)
        enemy_before = fight.vitals_of(target).total
        self.fire(run, victim, owner)
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(own.killer, victim.body)
        for _ in range(130):
            self.fire(run, owner, victim)
        self.assertEqual(weapon.vars[53].value(), 7)
        self.assertEqual(fight.vitals_of(target).total, enemy_before)
        old_body = owner.body
        for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK) + 2):
            if owner.body != old_body:
                break
            run.step({})
        self.assertNotEqual(owner.body, old_body)
        self.assertTrue(script.retired)
        self.assertFalse(fight.is_dead(owner))
        self.assertNotIn(old_body, fight.vitals)
        fresh_weapon = next(i for i in owner.body_script.component.instances.values()
                            if i.graph.index == 0x19)
        self.assertEqual(fresh_weapon.vars[53].value(), 8)
        x, y, z = owner.mover.position
        victim.mover.position[:] = (x, y, z + 1.5)
        victim.mover.body = None
        for _ in range(15):
            run.step({})
        self.assertEqual(fresh_weapon.vars[53].value(), 8)
        self.assertEqual(fight.vitals_of(target).total, enemy_before)
        self.fire(run, owner, victim)
        self.assertEqual(fresh_weapon.vars[53].value(), 7)
        self.assertLess(fight.vitals_of(target).total, enemy_before)

    def test_melee_during_reload_cannot_create_a_free_shell_or_shot(self):
        run, owner, victim, target = self.scenario()
        fight = run.match.combat
        self.fire(run, owner, victim)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x19)
        run.step({owner: (0, 0, 0x400)})
        for _ in range(15):
            run.step({})
        x, y, z = victim.mover.position
        yaw, pitch = aim(fight._eye(owner), (x, y + 1.0, z))
        run.step({owner: (yaw, pitch, 0x800)})
        self.assertEqual(weapon.vars[53].value(), 7)
        self.assertEqual(fight.vitals_of(target).total, 50)
        for _ in range(45):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 7)
        for _ in range(30):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 8)
        self.assertEqual(fight.vitals_of(target).total, 50)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 21)

    def test_wraith_refills_clip_without_firing_and_recovery_retains_it(self):
        run, owner, victim, target = self.scenario()
        self.fire(run, owner, victim)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x19)
        self.assertEqual(weapon.vars[53].value(), 7)
        run.step({owner: (0, 0, 8)})
        run.step({})
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(weapon.vars[53].value(), 8)
        for _ in range(220):
            run.step({})
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual(weapon.vars[53].value(), 8)
        self.assertEqual(run.match.combat.vitals_of(target).total, 80)


    def test_actual_wraith_near_expiry_buffer_and_held_press_start_when_ready(self):
        for held in (False, True):
            with self.subTest(held=held):
                run, owner, _, _ = self.scenario()
                run.step({owner: (0, 0, 8)})
                instance = owner.body_script.component.instances[1]
                ability = next(s for s in instance.states.values() if isinstance(s, nodes.Ability))
                early = ability.evaluate(ability.node.field("m_246438AD"), watch=False)
                self.assertAlmostEqual(early, 0.15)
                for _ in range(214):
                    run.step({})
                self.assertFalse(reaper.intangible(owner))
                self.assertGreater(ability.remaining(), 7)
                if not held:
                    while ability.remaining() > 0.08:
                        run.step({})
                counter = instance.state(1).counter
                run.step({owner: (0, 0, 8)})
                self.assertEqual(instance.state(1).counter, counter)
                for _ in range(510):
                    run.step({owner: (0, 0, 8 if held else 0)})
                    if instance.state(1).counter != counter:
                        break
                self.assertEqual(instance.state(1).counter, counter + 1)
                self.assertTrue(reaper.intangible(owner))



    def test_partial_and_empty_actual_clip_wraith_refill_releases_held_primary(self):
        for shells in (1, 8):
            with self.subTest(shells=shells):
                run, owner, victim, target = self.scenario()
                fight = run.match.combat
                fight.vitals[victim.body] = combat.Vitals(victim.body, 10000)
                weapon = next(i for i in owner.body_script.component.instances.values()
                              if i.graph.index == 0x19)
                for number in range(shells):
                    self.fire(run, owner, victim)
                    self.assertEqual(weapon.vars[53].value(), 7 - number)
                    if number + 1 < shells:
                        for _ in range(40):
                            run.step({})
                before = fight.vitals_of(target).total
                run.step({owner: (0, 0, 8)})
                self.assertTrue(reaper.intangible(owner))
                self.assertEqual(weapon.vars[53].value(), 8)
                x, y, z = victim.mover.position
                yaw, pitch = aim(fight._eye(owner), (x, y + 1, z))
                for _ in range(150):
                    run.step({owner: (yaw, pitch, 1)})
                    if fight.vitals_of(target).total < before:
                        break
                self.assertFalse(reaper.intangible(owner))
                self.assertEqual(weapon.vars[53].value(), 7)
                self.assertEqual(fight.vitals_of(target).total, before - 120)



if __name__ == "__main__":
    unittest.main()
