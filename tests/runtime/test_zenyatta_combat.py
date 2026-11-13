"""Actual extracted Zenyatta weapon execution; offline evidence, not client acceptance."""

import unittest
from itertools import product
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL

ZENYATTA = 0x02E0000000000020


class ZenyattaCombatTests(unittest.TestCase):
    def test_held_primary_recovers_from_empty_magazine_without_stale_reload_switch(self):
        run, zen, enemy, _ = self.arena()
        vitals = combat.Vitals(enemy.body, 10000)
        run.match.combat.vitals[enemy.body] = vitals
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        saw_empty = False
        for _ in range(1600):
            run.step({zen: (0, 600, FIRE)})
            saw_empty |= weapon.vars[53].value() == 0
            if len(zen.client.messages(combat.HIT)) >= 21:
                break
        else:
            self.fail('held real primary never recovered after empty-magazine reload')
        self.assertTrue(saw_empty)
        self.assertLessEqual(weapon.vars[53].value(), 19)
        self.assertEqual(vitals.total, 10000-48*len(zen.client.messages(combat.HIT)))

    def test_held_primary_after_actual_full_burst_keeps_all_resource_consumption(self):
        run, zen, enemy, _ = self.arena()
        vitals = combat.Vitals(enemy.body, 10000)
        run.match.combat.vitals[enemy.body] = vitals
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        for _ in range(100):
            run.step({zen: (0, 600, FIRE)})
        for _ in range(40):
            run.step({})
        hits = zen.client.messages(combat.HIT)
        self.assertGreater(len(hits), 5)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertEqual(weapon.vars[53].value(), 20-len(hits))
        self.assertEqual(vitals.total, 10000-48*len(hits))

    def arena(self):
        run = Run(KANEZAKA_DM, ("Zenyatta", "Enemy"))
        zen, enemy = run.players
        run.match.bots.clear()
        run.match.switch_hero(zen, heroes()[ZENYATTA])
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z + 8.0]
        run.step({})  # establish the world clock before any death schedules a respawn
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        return run, zen, enemy, fight.vitals_of(target)

    def test_primary_head_and_body_damage_use_actual_weapon_and_hit_notices(self):
        for pitch, damage, flags in ((0, 96, combat.CRITICAL), (600, 48, 0)):
            with self.subTest(pitch=pitch):
                run, zen, _, vitals = self.arena()
                run.step({zen: (0, pitch, FIRE)})
                for _ in range(15):
                    run.step({})
                self.assertEqual(vitals.total, 200 - damage)
                hits = zen.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x4"], damage)
                self.assertEqual(hits[0]["+0x78"]["+0x8"], flags)

    def test_full_secondary_emits_five_independently_damaging_orbs(self):
        run, zen, enemy, _ = self.arena()
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        self.assertEqual(vitals.total, 1000)
        for _ in range(60):
            run.step({zen: (0, 600, 0)})
        self.assertEqual(vitals.total, 760)
        self.assertEqual(len(zen.client.messages(combat.HIT)), 5)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertEqual(weapon.vars[53].value(), 15)

    def test_five_critical_orbs_stack_discord_and_credit_effective_damage(self):
        for discord in (False, True):
            with self.subTest(discord=discord):
                run, zen, enemy, _ = self.arena()
                fight = run.match.combat
                hp = fight.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
                if discord:
                    run.step({zen: (0, 0, 8)})
                    self.assertEqual(fight.zenyatta.owners[zen.body].discord.target, enemy.body)
                script = zen.body_script
                weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0164)
                start_charge = script.component.entity_var(342).value()
                start_frame = run.frame
                for _ in range(130):
                    run.step({zen: (0, 0, SECONDARY)})
                self.assertEqual(hp.total, 1000)
                for _ in range(60):
                    run.step({zen: (0, 0, 0)})
                damage = 120 if discord else 96
                self.assertEqual(hp.total, 1000-5*damage)
                hits = [msg["+0x78"] for msg in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x4"], hit["+0x8"]) for hit in hits],
                                 [(damage, combat.CRITICAL)]*5)
                self.assertEqual(weapon.vars[53].value(), 15)
                earned = script.component.entity_var(342).value()-start_charge
                self.assertAlmostEqual(earned, 5*damage+(run.frame-start_frame)*.016*5, places=2)

    def test_critical_volley_straddles_enemy_transcendence_expiry(self):
        impact_offsets = []
        for schedule in ("unprotected", "continuous", "skipped"):
            with self.subTest(schedule=schedule):
                run, zen, enemy, _ = self.arena()
                self.assertTrue(run.match.switch_hero(enemy, heroes()[ZENYATTA]))
                x, y, z = zen.mover.position
                enemy.mover.position = [x, y, z + 24]
                enemy.mover.body = None
                fight = run.match.combat
                health = fight.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
                charge = zen.body_script.component.entity_var(342)
                expiry = None
                if schedule != "unprotected":
                    self.assertEqual(len(impact_offsets), 5)
                    script = enemy.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({enemy: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(enemy))
                    self.assertEqual(script.component.entity_var(342).value(), 0)
                    expiry = fight.zenyatta.ultimate_last_world_tick(enemy)
                    for _ in range(expiry - impact_offsets[2] - run.frame - 130):
                        run.step({})
                for _ in range(130):
                    run.step({zen: (0, 0, SECONDARY)})
                release = run.frame
                if expiry is not None:
                    self.assertEqual(expiry, release + impact_offsets[2])
                    self.assertTrue(fight.zenyatta.ultimate(enemy))

                attempts = []
                damage = fight._damage

                def observe(source, target, amount, critical, *args, **kwargs):
                    before = health.total
                    result = damage(source, target, amount, critical, *args, **kwargs)
                    if source is zen and target.owner is enemy:
                        attempts.append((fight.tick - release, amount, critical, before - health.total))
                    return result

                shots = {}
                start_charge = charge.value()
                jumped = False
                with patch.object(fight, "_damage", side_effect=observe):
                    for _ in range(75):
                        run.step({})
                        shots.update({id(orb): orb for orb in fight.rockets if orb.shooter is zen})
                        if schedule == "skipped" and not jumped and len(shots) == 5:
                            self.assertEqual(len(attempts), 2)
                            self.assertLess(run.frame, expiry)
                            run.frame = release + impact_offsets[3] - 1
                            run.step({})
                            jumped = True
                        if len(attempts) == 5:
                            break
                self.assertEqual(jumped, schedule == "skipped")
                self.assertEqual(len(shots), 5)
                self.assertTrue(all(orb.definition == 0x0400000000000261 for orb in shots.values()))
                self.assertFalse(fight.rockets)
                weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
                self.assertEqual(weapon.vars[53].value(), 15)
                if schedule == "unprotected":
                    impact_offsets = [at for at, _, _, _ in attempts]
                    self.assertEqual(len(impact_offsets), 5)
                    self.assertEqual(impact_offsets, sorted(set(impact_offsets)))
                losses = [96] * 5 if expiry is None else [0, 0, 0, 96, 96]
                self.assertEqual(attempts,
                                 [(at, 96, True, loss) for at, loss in zip(impact_offsets, losses, strict=True)])
                self.assertFalse(fight.zenyatta.ultimate(enemy))
                hits = [packet["+0x78"] for packet in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"], hit["+0x8"]) for hit in hits],
                                 [(enemy.body, 96, combat.CRITICAL)] * (sum(losses) // 96))
                self.assertEqual(health.total, 1000 - sum(losses))
                self.assertAlmostEqual(charge.value() - start_charge,
                                       sum(losses) + (run.frame - release) * combat.TICK * 5, places=3)

    def test_charged_headshots_respect_wall_range_and_team(self):
        from dataclasses import replace

        from test_game_mover import box, wall

        from ow174.game.collision import World

        for mode in ("wall", "range", "team"):
            with self.subTest(mode=mode):
                run, zen, enemy, health = self.arena()
                x, y, z = zen.mover.position
                if mode == "wall":
                    run.match.collision_world = World(
                        box(x-30, x+30, y-2, y, z-30, z+30) + wall(x-6, z+4, x+6, z+4, y, y+5)
                    )
                elif mode == "range":
                    enemy.mover.position = [x, y, z+182]
                    enemy.mover.body = None
                else:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = zen.team
                for _ in range(130):
                    run.step({zen: (0, 0, SECONDARY)})
                for _ in range(180):
                    run.step({})
                self.assertEqual(health.total, 200)
                fight = run.match.combat
                self.assertFalse(fight.rockets)
                self.assertFalse(zen.client.messages(combat.HIT))
                weapon = next(i for i in zen.body_script.component.instances.values()
                              if i.graph.index == 0x0164)
                self.assertEqual(weapon.vars[53].value(), 15)

    def test_two_actual_charged_headshots_use_orisa_armour(self):
        from ow174.game.content import heroes

        run, zen, enemy, _ = self.arena()
        orisa = next(hero for hero in heroes().values() if hero.name == "Orisa")
        self.assertTrue(run.match.switch_hero(enemy, orisa))
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z+8]
        enemy.mover.body = None
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        hp = fight.vitals[enemy.body]
        before = hp.total
        direction = aim(fight._eye(zen), fight._eye(enemy))
        for _ in range(50):
            run.step({zen: (*direction, SECONDARY)})
        for _ in range(55):
            run.step({zen: (*direction, 0)})
        hits = [msg["+0x78"] for msg in zen.client.messages(combat.HIT)]
        self.assertEqual([(h["+0x4"], h["+0x8"]) for h in hits], [(91, combat.CRITICAL)]*2)
        self.assertEqual(before-hp.total, 182)

    def test_lethal_volley_retires_discord_and_keeps_ammo_cost(self):
        run, zen, enemy, hp = self.arena()
        fight = run.match.combat
        run.step({zen: (0, 0, 8)})
        owner = fight.zenyatta.owners[zen.body]
        self.assertEqual(owner.discord.target, enemy.body)
        script = zen.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0164)
        start = script.component.entity_var(342).value()
        frame = run.frame
        for _ in range(130):
            run.step({zen: (0, 0, SECONDARY)})
        for _ in range(175):
            run.step({})
        hits = [msg["+0x78"] for msg in zen.client.messages(combat.HIT)]
        self.assertEqual([(h["+0x4"], h["+0x8"]) for h in hits],
                         [(120, combat.CRITICAL), (80, combat.CRITICAL | combat.KILLING)])
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, zen.body)
        self.assertIsNone(owner.discord)
        self.assertFalse(fight.rockets)
        self.assertEqual(weapon.vars[53].value(), 15)
        self.assertAlmostEqual(script.component.entity_var(342).value()-start,
                               200+(run.frame-frame)*.016*5, places=2)

    def test_partial_secondary_charge_releases_the_authored_orb_count(self):
        for frames, count in ((1, 1), (50, 2), (90, 3), (130, 5)):
            with self.subTest(frames=frames):
                run, zen, enemy, _ = self.arena()
                vitals = combat.Vitals(enemy.body, 1000)
                run.match.combat.vitals[enemy.body] = vitals
                for _ in range(frames):
                    run.step({zen: (0, 600, SECONDARY)})
                self.assertEqual(vitals.total, 1000)
                for _ in range(70):
                    run.step({zen: (0, 600, 0)})
                self.assertEqual(vitals.total, 1000-48*count)
                self.assertEqual(len(zen.client.messages(combat.HIT)), count)

    def test_secondary_cannot_charge_damage_beyond_available_rounds(self):
        for ammunition in (1, 3, 20):
            with self.subTest(ammunition=ammunition):
                run, zen, enemy, _ = self.arena()
                vitals = combat.Vitals(enemy.body, 1000)
                run.match.combat.vitals[enemy.body] = vitals
                weapon = next(
                    i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164
                )
                weapon.write((0, 53), ammunition)
                for _ in range(130):
                    run.step({zen: (0, 600, SECONDARY)})
                for _ in range(60):
                    run.step({zen: (0, 600, 0)})
                count = min(5, ammunition)
                self.assertEqual(vitals.total, 1000-48*count)
                self.assertEqual(len(zen.client.messages(combat.HIT)), count)
                self.assertEqual(weapon.vars[53].value(), ammunition-count)

    def test_secondary_forces_a_bounded_five_orb_release_when_held(self):
        run, zen, enemy, _ = self.arena()
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        for _ in range(250):
            run.step({zen: (0, 600, SECONDARY)})
        self.assertEqual(vitals.total, 760)
        self.assertEqual(len(zen.client.messages(combat.HIT)), 5)

    def test_secondary_budget_refreshes_after_reload(self):
        run, zen, enemy, _ = self.arena()
        vitals = combat.Vitals(enemy.body, 1000)
        run.match.combat.vitals[enemy.body] = vitals
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        weapon.write((0, 53), 1)
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        for _ in range(70):
            run.step({zen: (0, 600, 0)})
        self.assertEqual(vitals.total, 952)
        run.step({zen: (0, 600, 0x400)})
        for _ in range(160):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 20)
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        for _ in range(70):
            run.step({zen: (0, 600, 0)})
        self.assertEqual(vitals.total, 712)
        self.assertEqual(weapon.vars[53].value(), 15)

    def test_empty_secondary_cannot_shoot_during_automatic_reload(self):
        run, zen, _, vitals = self.arena()
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        weapon.write((0, 53), 0)
        for _ in range(50):
            run.step({zen: (0, 600, SECONDARY)})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_secondary_cancelled_by_melee_death_or_body_replacement_deals_no_orb_damage(self):
        for reason in ("melee", "death", "replacement"):
            with self.subTest(reason=reason):
                run, zen, enemy, _ = self.arena()
                fight = run.match.combat
                vitals = combat.Vitals(enemy.body, 1000)
                fight.vitals[enemy.body] = vitals
                for _ in range(90):
                    run.step({zen: (0, 600, SECONDARY)})
                if reason == "melee":
                    run.step({zen: (0, 600, SECONDARY | 0x800)})
                elif reason == "death":
                    target = next(t for t in fight._targets() if t.owner is zen)
                    fight._damage(enemy, target, 500, False, None, run.frame)
                else:
                    run.match.switch_hero(zen, zen.hero)
                for _ in range(80):
                    run.step({})
                self.assertEqual(vitals.total, 1000)
                self.assertFalse(zen.client.messages(combat.HIT))

    def test_primary_miss_spends_ammo_without_damage(self):
        run, zen, _, vitals = self.arena()
        run.step({zen: (16384, 0, FIRE)})
        for _ in range(15):
            run.step({})
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertEqual(weapon.vars[53].value(), 19)
        self.assertEqual(vitals.total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_primary_range_limit_retires_projectile_before_far_target(self):
        run, zen, enemy, vitals = self.arena()
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z + 182]
        enemy.mover.body = None  # recreate the physics capsule at the test's new feet
        run.step({zen: (0, 0, FIRE)})
        for _ in range(140):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_primary_does_not_damage_teammate(self):
        run, zen, enemy, vitals = self.arena()
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        enemy.team = zen.team
        run.step({zen: (0, 0, FIRE)})
        for _ in range(15):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_primary_stops_at_actual_static_collision(self):
        run, zen, _, vitals = self.arena()
        x, y, z = zen.mover.position
        run.match.collision_world = World([
            (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
            (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
            (x-5, y, z+4, x+5, y, z+4, x+5, y+5, z+4),
            (x-5, y, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
        ])
        run.step({zen: (0, 0, FIRE)})
        for _ in range(15):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(run.match.combat.rockets)

    def test_repeated_presses_cannot_bypass_primary_fire_interval(self):
        ammo = []
        for pulse in (False, True):
            run, zen, _, _ = self.arena()
            for i in range(40):
                run.step({zen: (16384, 0, FIRE if not pulse or i % 2 == 0 else 0)})
            weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
            ammo.append(weapon.vars[53].value())
        self.assertEqual(ammo, [18, 18])

    def test_reload_has_pending_window_and_restores_actual_twenty_round_magazine(self):
        run, zen, _, _ = self.arena()
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        for _ in range(40):
            run.step({zen: (16384, 0, FIRE)})
        self.assertEqual(weapon.vars[53].value(), 18)
        for i in range(30):
            run.step({zen: (16384, 0, 0x400 if i == 0 else FIRE)})
        self.assertEqual(weapon.vars[53].value(), 18)
        for _ in range(140):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 20)
        run.step({zen: (16384, 0, FIRE)})
        self.assertEqual(weapon.vars[53].value(), 19)

    def test_enemy_fire_drains_zen_shields_before_health(self):
        run, zen, enemy, _ = self.arena()
        target = next(t for t in run.match.combat._targets() if t.owner is zen)
        vitals = run.match.combat.vitals_of(target)
        run.step({enemy: (-32768, 0, FIRE)})
        self.assertEqual(vitals.now[SHIELDS_POOL], 110)
        self.assertEqual(vitals.now[HEALTH_POOL], 50)
        self.assertEqual(zen.body_script.component.owner_dead, False)

    def test_dead_zen_cannot_emit_weapon_damage(self):
        run, zen, enemy, vitals = self.arena()
        target = next(t for t in run.match.combat._targets() if t.owner is zen)
        run.match.combat._damage(enemy, target, 500, False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(zen))
        for _ in range(25):
            run.step({zen: (0, 600, FIRE)})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_only_already_launched_orb_hits_after_actual_zen_death(self):
        for attack, lethal in ((FIRE, False), (FIRE, True), (SECONDARY, False), (SECONDARY, True)):
            with self.subTest(attack=attack, lethal=lethal):
                run, zen, enemy, enemy_hp = self.arena()
                fight = run.match.combat
                if lethal:
                    enemy_hp.damage(152)
                enemy_before = enemy_hp.total
                source_body = zen.body
                x, y, z = zen.mover.position
                enemy.mover.position = [x, y, z+21]
                enemy.mover.body = None
                for _ in range(10):
                    run.step({})
                direction = aim(fight._eye(zen), (x, y+1, z+21))
                run.step({zen: (*direction, 8)})
                self.assertEqual(fight.zenyatta.owners[zen.body].discord.target, enemy.body)
                zen_hp = fight.vitals[zen.body]
                zen_hp.damage(190)
                script = zen.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(342)
                before = charge.value()
                if attack == SECONDARY:
                    for _ in range(130):
                        run.step({zen: (*direction, SECONDARY)})
                    for _ in range(30):
                        run.step({zen: (*direction, 0)})
                        if fight.rockets:
                            break
                else:
                    run.step({zen: (*direction, FIRE)})
                orb, = fight.rockets
                self.assertIs(orb.shooter, zen)
                self.assertEqual(enemy_hp.total, enemy_before)

                lethal_aim = aim(fight._eye(enemy), (x, y+1, z))
                run.step({enemy: (*lethal_aim, FIRE)})
                self.assertTrue(zen_hp.dead)
                self.assertIn(orb, fight.rockets)
                for _ in range(70):
                    run.step({zen: (*direction, FIRE)})
                self.assertEqual(enemy_hp.total, enemy_before-48)
                self.assertEqual(enemy_hp.dead, lethal)
                if lethal:
                    self.assertEqual(enemy_hp.killer, source_body)
                self.assertEqual(len(zen.client.messages(combat.KILL_NOTICE)), int(lethal))
                self.assertEqual(charge.value(), before)
                hit, = (event["+0x78"] for event in zen.client.messages(combat.HIT))
                self.assertEqual((hit["+0x0"]["+0x0"], hit["+0x4"], hit["+0x8"]),
                                 (enemy.body, 48, combat.KILLING if lethal else 0))

    def test_body_replacement_retires_an_actual_in_flight_orb(self):
        run, zen, enemy, hp = self.arena()
        fight = run.match.combat
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z+21]
        enemy.mover.body = None
        for _ in range(10):
            run.step({})
        direction = aim(fight._eye(zen), (x, y+1, z+21))
        run.step({zen: (*direction, FIRE)})
        orb, = fight.rockets
        self.assertIsNotNone(orb.shot)
        self.assertEqual(hp.total, 200)
        old_body = zen.body
        self.assertTrue(run.match.switch_hero(zen, zen.hero))
        self.assertNotEqual(zen.body, old_body)
        self.assertNotIn(orb, fight.rockets)
        for _ in range(45):
            run.step({})
        self.assertEqual(hp.total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_in_flight_orb_hits_replacement_body_without_old_discord(self):
        for replace_target in (False, True):
            with self.subTest(replace_target=replace_target):
                run, zen, enemy, hp = self.arena()
                fight = run.match.combat
                x, y, z = zen.mover.position
                position = [x, y, z+21]
                enemy.mover.position = position.copy()
                enemy.mover.body = None
                for _ in range(10):
                    run.step({})
                direction = aim(fight._eye(zen), (x, y+1, z+21))
                old_body = enemy.body
                run.step({zen: (*direction, 8)})
                self.assertEqual(fight.zenyatta.owners[zen.body].discord.target, old_body)
                run.step({zen: (*direction, FIRE)})
                orb, = fight.rockets
                self.assertEqual(hp.total, 200)
                if replace_target:
                    self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
                    self.assertNotEqual(enemy.body, old_body)
                    enemy.mover.position = position
                    enemy.mover.body = None
                    self.assertIn(orb, fight.rockets)
                for _ in range(45):
                    run.step({})
                notice, = zen.client.messages(combat.HIT)
                damage = 48 if replace_target else 60
                self.assertEqual(notice["+0x78"]["+0x0"]["+0x0"], enemy.body)
                self.assertEqual(notice["+0x78"]["+0x4"], damage)
                self.assertEqual(fight.vitals[enemy.body].total, 200-damage)
                if replace_target:
                    self.assertEqual(hp.total, 200)

    def test_delayed_view_rewinds_melee_but_not_primary_or_charged_orbs(self):
        for attack, old_on_line in product((FIRE, SECONDARY, 0x800), (False, True)):
            with self.subTest(attack=attack, old_on_line=old_on_line):
                run, zen, enemy, _ = self.arena()
                fight = run.match.combat
                hp = fight.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
                script = zen.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                charge = script.component.entity_var(342)
                weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0164)
                x, y, z = zen.mover.position
                melee = attack == 0x800
                depth = 1.5 if melee else 8
                pitch = 0 if melee else 600
                held = SECONDARY if attack == SECONDARY else 0
                old_x = x if old_on_line else x + 6
                live_x = x + 6 if old_on_line else x
                enemy.mover.position = [old_x, y, z + depth]
                enemy.mover.body = None
                for _ in range(130 if held else 20):
                    run.step({zen: (0, pitch, held)})
                old_tick = run.frame
                enemy.mover.position = [live_x, y, z + depth]
                enemy.mover.body = None
                for _ in range(10):
                    run.step({zen: (0, pitch, held)})
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                target = next(t for t in fight._targets() if t.owner is enemy)
                self.assertAlmostEqual(fight.seen(target, old_tick).feet[0], old_x)
                self.assertAlmostEqual(target.feet[0], live_x)
                command = Command(run.frame, yaw=0, pitch=pitch, buttons=0 if held else attack,
                                  extra=(run.frame - old_tick) * 16)
                zen.take_commands([command])

                launches = []
                for _ in range(65):
                    for orb in fight.rockets:
                        if orb.shooter is zen and all(orb is not seen for seen in launches):
                            launches.append(orb)
                    run.step({zen: (0, pitch, 0)})
                count = 0 if melee else (5 if held else 1)
                self.assertEqual(len(launches), count)
                self.assertTrue(all(orb.direct.amount == 48 for orb in launches))
                self.assertEqual(weapon.vars[53].value(), 20 - count)
                target_on_line = old_on_line if melee else not old_on_line
                hit_count = max(1, count) if target_on_line else 0
                amount = 30 if melee else 48
                hits = [notice["+0x78"] for notice in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                                 [(enemy.body, amount)] * hit_count)
                self.assertEqual(hp.total, 1000 - amount * hit_count)
                self.assertEqual(charge.value(), amount * hit_count)

    def test_quick_melee_deals_thirty_and_does_not_headshot(self):
        run, zen, enemy, vitals = self.arena()
        x, y, z = zen.mover.position
        enemy.mover.position = [x, y, z + 1.5]
        enemy.mover.body = None
        for i in range(30):
            run.step({zen: (0, 0, 0x800 if i == 0 else 0)})
        self.assertEqual(vitals.total, 170)
        hits = zen.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x8"], 0)

    def test_miss_and_overkill_charge_for_each_attack(self):
        for attack, ammo in ((FIRE, 19), (SECONDARY, 15), (0x800, 20)):
            for hit in (False, True):
                with self.subTest(attack=attack, hit=hit):
                    run, zen, enemy, hp = self.arena()
                    script = zen.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    charge = script.component.entity_var(342)
                    weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0164)
                    hp.damage(193)
                    if attack == 0x800:
                        x, y, z = zen.mover.position
                        enemy.mover.position = [x, y, z+1.5]
                        enemy.mover.body = None
                    yaw = 0 if hit else -32768
                    pitch = 0 if attack == 0x800 else 600
                    for _ in range(130 if attack == SECONDARY else 1):
                        run.step({zen: (yaw, pitch, attack)})
                    for _ in range(65):
                        run.step({zen: (yaw, pitch, 0)})
                    self.assertEqual(weapon.vars[53].value(), ammo)
                    self.assertEqual(hp.total, 0 if hit else 7)
                    self.assertEqual(charge.value(), 7 if hit else 0)
                    notices = [m["+0x78"] for m in zen.client.messages(combat.HIT)]
                    self.assertEqual([(m["+0x4"], m["+0x8"]) for m in notices],
                                     [(7, combat.KILLING)] if hit else [])

    def test_three_primary_headshots_kill_and_emit_killing_notice(self):
        run, zen, _, vitals = self.arena()
        for _ in range(65):
            run.step({zen: (0, 0, FIRE)})
        self.assertTrue(vitals.dead)
        hits = zen.client.messages(combat.HIT)
        self.assertEqual([h["+0x78"]["+0x4"] for h in hits], [96, 96, 8])
        self.assertEqual(hits[-1]["+0x78"]["+0x8"], combat.CRITICAL | combat.KILLING)

    def test_explicit_noncritical_projectile_stays_noncritical_at_head_height(self):
        run, zen, _, vitals = self.arena()
        fight = run.match.combat
        fight.rockets.append(combat.Rocket(
            zen, zen.team, fight._eye(zen), (0, 0, 1), 90, 1,
            combat.HitDef(40, critical=1), None,
        ))
        for _ in range(15):
            run.step({})
        self.assertEqual(vitals.total, 160)
        self.assertEqual(zen.client.messages(combat.HIT)[0]["+0x78"]["+0x8"], 0)

    def test_direct_projectile_falloff_precedes_armour_and_splash_does_not_crit(self):
        run, zen, enemy, _ = self.arena()
        fight = run.match.combat
        vitals = combat.Vitals(enemy.body, 200, armour=100)
        fight.vitals[enemy.body] = vitals
        rocket = combat.Rocket(
            zen, zen.team, fight._eye(zen), (0, 0, 1), 90, 1,
            combat.HitDef(48, falloff_start=0, falloff_end=1, falloff_floor=0.5, critical=2),
            combat.HitDef(10, critical=2, rings=((1, 1),)),
        )
        fight.rockets.append(rocket)
        for _ in range(15):
            run.step({})
        # Direct: 48 * .5 * 2 - 5 armour. Splash: 10 - 5 armour.
        self.assertEqual(vitals.total, 252)
        hits = zen.client.messages(combat.HIT)
        self.assertEqual([h["+0x78"]["+0x4"] for h in hits], [43, 5])
        self.assertEqual([h["+0x78"]["+0x8"] for h in hits], [combat.CRITICAL, 0])


if __name__ == "__main__":
    unittest.main()
