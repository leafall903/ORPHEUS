"""Actual Sombra pistol input against authored Genji and Sigma windows."""

import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
from test_game_combat import aim, in_world

from ow174.game import combat, reinhardt_barrier, shot_guards, stats
from ow174.game.commands import FIRE, Command
from ow174.game.script import nodes
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL


class SombraShotGuardTests(unittest.TestCase):
    def arena(self, hero, *, rear=False, wall=False, distance=4):
        f = scenes.SombraCombatTests()
        f.setUp()
        self.assertTrue(f.run.match.switch_hero(f.b, scenes.heroes()[hero]))
        f.b.mover.position[:] = (0, 0, distance)
        f.b.mover.body = None
        f.press(0, 45)
        if wall:
            f.wall()
        for player in (f.a, f.b):
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        fight = f.run.match.combat
        facing = (0, 0) if rear else aim(fight._eye(f.b), (0, 1, 0))
        f.run.step({f.b: (*facing, 16 if hero == shot_guards.GENJI else 8)})
        for _ in range(4):
            f.run.step({f.b: (*facing, 0)})
        guard = shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000)
        self.assertIsNotNone(guard)
        return f, facing, guard

    def shoot(self, fixture, facing, *, frames=1):
        for _ in range(frames):
            fight = fixture.run.match.combat
            attack = aim(fight._eye(fixture.a), (0, 1, 4))
            fixture.run.step({fixture.a: (*attack, FIRE), fixture.b: (*facing, 0)})

    def test_genji_returns_pistol_damage_and_owns_the_hit(self):
        fixture, facing, _ = self.arena(shot_guards.GENJI)
        self.shoot(fixture, facing)
        self.assertEqual(fixture.health(fixture.b), 200)
        self.assertEqual(fixture.health(fixture.a), 192)
        self.assertEqual(fixture.weapon().vars[53].value(), 59)
        self.assertEqual(fixture.a.client.messages(combat.HIT), [])
        self.assertEqual(len(fixture.b.client.messages(combat.HIT)), 1)
        self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 0)

    def test_lethal_reflected_pistol_credits_genji_once(self):
        f, facing, _ = self.arena(shot_guards.GENJI)
        fight = f.run.match.combat
        f.health(f.a)
        fight.vitals[f.a.body].now[HEALTH_POOL] = 8

        self.shoot(f, facing)
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(fight.vitals[f.a.body].killer, f.b.body)
        self.assertEqual(f.health(f.b), 200)
        self.assertEqual(f.a.stats.value(stats.DEATHS), 1)
        self.assertEqual(f.b.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(f.a.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(f.a.client.messages(combat.HIT), [])
        self.assertEqual(f.a.client.messages(combat.KILL_NOTICE), [])
        hit, = f.b.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x4"], 8)
        kill, = f.b.client.messages(combat.KILL_NOTICE)
        self.assertEqual(kill["+0x80"]["+0x0"], f.b.body)
        self.assertEqual(kill["+0x84"]["+0x0"], f.a.body)
        death, = f.a.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(death["+0x78"], combat.KILLED_BY)
        self.assertEqual(death["+0x7C"]["+0x0"], f.b.body)

    def test_sigma_absorbs_into_authored_accumulator(self):
        fixture, facing, guard = self.arena(shot_guards.SIGMA)
        before = fixture.health(fixture.b)
        self.shoot(fixture, facing)
        self.assertEqual(fixture.health(fixture.b), before)
        self.assertEqual(guard.instance.vars[21312].value(), 8)
        self.assertEqual(fixture.weapon().vars[53].value(), 59)
        self.assertEqual(fixture.a.client.messages(combat.HIT), [])
        self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 0)

    def incoming_zarya(self, f, facing):
        match = f.run.match
        hero = scenes.heroes()[reinhardt_barrier.ZARYA]
        zarya = match.add_player(3, "Zarya", hero.guid, 2, False)
        in_world(match, zarya)
        f.run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team, zarya.team = 0, 0, 1
        f.b.mover.position[:] = (0, 0, -2)
        zarya.mover.position[:] = (0, 0, -5)
        for player in (f.b, zarya):
            player.mover.body = None
        for _ in range(35):
            f.run.step({f.b: (facing, 0, 0)})
        return zarya

    def test_actual_deflect_returns_zarya_projectiles_and_owns_graviton_field(self):
        for button, rear in ((2, False), (32, False), (32, True)):
            with self.subTest(button=button, rear=rear):
                f, _, guard = self.arena(shot_guards.GENJI)
                facing = 0 if rear else -32768
                zarya = self.incoming_zarya(f, facing)
                fight = f.run.match.combat
                self.assertTrue(guard.active)
                if button == 32:
                    script = zarya.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                f.run.step({f.b: (facing, 0, 0), zarya: (0, 0, button)})
                rocket, = fight.rockets
                for _ in range(20):
                    f.run.step({f.b: (facing, 0, 0)})
                self.assertNotIn(rocket, fight.rockets)
                if rear:
                    self.assertIs(rocket.shooter, zarya)
                    self.assertLess(f.health(f.a), 200)
                else:
                    self.assertIs(rocket.shooter, f.b)
                    self.assertEqual(rocket.team, f.a.team)
                    self.assertEqual(rocket.source_body, f.b.body)
                    self.assertIsNone(rocket.shot)
                    self.assertEqual(f.health(f.a), 200)
                    self.assertEqual(f.health(f.b), 200)
                    self.assertLess(f.health(zarya), 400)
                if button == 32:
                    field, = fight.gravitons.fields
                    self.assertIs(field.shooter, zarya if rear else f.b)
                    self.assertEqual(field.source_body, field.shooter.body)
                    self.assertEqual(field.team, field.shooter.team)
                else:
                    self.assertFalse(fight.gravitons.fields)
                    self.assertEqual(f.health(zarya), 353)

    def test_reflected_graviton_departure_follows_current_owner(self):
        for leaving in ("genji_in_flight", "genji_after_impact", "zarya_in_flight"):
            with self.subTest(leaving=leaving):
                f, _, _ = self.arena(shot_guards.GENJI)
                facing = -32768
                zarya = self.incoming_zarya(f, facing)
                match = f.run.match
                fight = match.combat
                script = zarya.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                f.run.step({f.b: (facing, 0, 0), zarya: (0, 0, 32)})
                rocket, = fight.rockets
                for _ in range(15):
                    f.run.step({f.b: (facing, 0, 0)})
                    if rocket.shooter is f.b:
                        break
                self.assertIs(rocket.shooter, f.b)
                self.assertIn(rocket, fight.rockets)
                if leaving == "genji_after_impact":
                    for _ in range(20):
                        f.press(0)
                        if fight.gravitons.pulling(zarya):
                            break
                    self.assertTrue(fight.gravitons.pulling(zarya))
                    self.assertLess(f.health(zarya), 400)
                player = zarya if leaving == "zarya_in_flight" else f.b
                match.leave(player)
                f.run.players.remove(player)
                if player is zarya:
                    self.assertIn(rocket, fight.rockets)
                    f.geometry((-4, 0, -8, 4, 0, -8, 4, 4, -8), (-4, 0, -8, 4, 4, -8, -4, 4, -8))
                    for _ in range(30):
                        f.press(0)
                        if fight.gravitons.fields:
                            break
                    field, = fight.gravitons.fields
                    self.assertIs(field.shooter, f.b)
                    self.assertEqual(field.source_body, f.b.body)
                else:
                    before = f.health(zarya)
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertFalse(fight.gravitons.fields)
                    self.assertFalse(fight.gravitons.pulling(zarya))
                    f.press(0, 20)
                    self.assertEqual(f.health(zarya), before)
                self.assertEqual(f.health(f.a), 200)

    def test_actual_grasp_absorbs_zarya_projectiles_and_converts_shields(self):
        for button, rear in ((2, False), (32, False), (32, True)):
            with self.subTest(button=button, rear=rear):
                f, _, guard = self.arena(shot_guards.SIGMA)
                facing = 0 if rear else -32768
                zarya = self.incoming_zarya(f, facing)
                fight = f.run.match.combat
                self.assertTrue(guard.active)
                if button == 32:
                    script = zarya.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                f.run.step({f.b: (facing, 0, 0), zarya: (0, 0, button)})
                rocket, = fight.rockets
                for _ in range(20):
                    f.run.step({f.b: (facing, 0, 0)})
                self.assertNotIn(rocket, fight.rockets)
                if rear:
                    self.assertEqual(guard.instance.vars[21312].value(), 0)
                    self.assertTrue(fight.gravitons.fields)
                    self.assertLess(f.health(f.a), 200)
                    continue
                amount = 47 if button == 2 else 5
                self.assertEqual(guard.instance.vars[21312].value(), amount)
                self.assertFalse(fight.gravitons.fields)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(f.health(f.b), 400)
                f.press(0, 145)
                vitals = fight.vitals[f.b.body]
                self.assertAlmostEqual(vitals.temporary_shield_total, amount * 0.6, places=5)
                self.assertEqual(vitals.now[SHIELDS_POOL], 100)

    def grasp_shields(self):
        fixture, facing, guard = self.arena(shot_guards.SIGMA)
        fight = fixture.run.match.combat
        self.shoot(fixture, facing, frames=16)
        rounds = 60 - fixture.weapon().vars[53].value()
        self.assertGreaterEqual(rounds, 4)
        self.assertEqual(guard.instance.vars[21312].value(), rounds * 8)
        for _ in range(145):
            fixture.run.step({fixture.b: (*facing, 0)})
        vitals = fight.vitals[fixture.b.body]
        self.assertAlmostEqual(vitals.temporary_shield_total, rounds * 8 * 0.6, places=5)
        self.assertEqual(vitals.now[SHIELDS_POOL], 100)
        self.assertTrue(any(i.graph.index == 0x1D03 for i in guard.instance.component.instances.values()))
        return fixture, facing, vitals

    def test_sigma_converts_absorbed_rounds_and_later_hits_spend_temporary_first(self):
        fixture, facing, vitals = self.grasp_shields()
        before = vitals.temporary_shield_total
        self.shoot(fixture, facing)
        self.assertAlmostEqual(vitals.temporary_shield_total, before - 8, places=5)
        self.assertEqual(vitals.now[SHIELDS_POOL], 100)
        self.assertEqual(vitals.now[HEALTH_POOL], 300)
        self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 8)

    def test_emp_removes_base_and_temporary_shields_without_charging(self):
        fixture, _, vitals = self.grasp_shields()
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(0x20, 50)
        self.assertTrue(fixture.hack_applications())
        self.assertEqual(vitals.now[SHIELDS_POOL], 0)
        self.assertEqual(vitals.temporary_shield_total, 0)
        self.assertEqual(vitals.now[HEALTH_POOL], 300)
        self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 0)

    def test_sigma_grasp_shields_decay_and_retire_without_base_shield_loss(self):
        fixture, facing, vitals = self.grasp_shields()
        before = vitals.temporary_shield_total
        for _ in range(450):
            fixture.run.step({fixture.b: (*facing, 0)})
            if vitals.temporary_shield_total < before:
                break
        after_grace = vitals.temporary_shield_total
        self.assertLess(after_grace, before)
        for _ in range(20):
            fixture.run.step({fixture.b: (*facing, 0)})
        self.assertAlmostEqual(vitals.temporary_shield_total, after_grace - 20 * combat.TICK * 5, places=5)
        for _ in range(450):
            if vitals.temporary_shield_total == 0:
                break
            fixture.run.step({fixture.b: (*facing, 0)})
        self.assertEqual(vitals.temporary_shield_total, 0)
        self.assertEqual(vitals.now[SHIELDS_POOL], 100)
        instances = fixture.b.body_script.component.instances.values()
        self.assertFalse(any(i.graph.index == 0x1D03 for i in instances))

    def test_guard_cooldown_and_fresh_cast_match_omitted_owner_frames(self):
        for hero in (shot_guards.GENJI, shot_guards.SIGMA):
            samples = []
            for gap in (False, True):
                with self.subTest(hero=hero, gap=gap):
                    f, facing, guard = self.arena(hero)
                    match = f.run.match
                    script = f.b.body_script
                    ability = next(s for s in guard.instance.states.values() if isinstance(s, nodes.Ability))
                    activation = guard.counter
                    if gap:
                        at = f.run.frame + 151
                        match.update(at * combat.TICK, at)
                    else:
                        f.press(0, 150)
                    self.assertFalse(guard.active)
                    self.assertGreater(ability.remaining(), 0)
                    samples.append((ability.last, ability.remaining()))
                    ready = (ability.last + round(ability.cur * 1000)) // script.frame_ms
                    button = 16 if hero == shot_guards.GENJI else 8
                    if gap:
                        match.update(ready * combat.TICK, ready)
                        self.assertGreater(ability.remaining(), 0)
                        f.b.take_commands([Command(ready, yaw=facing[0], pitch=facing[1], buttons=button)])
                    else:
                        f.press(0, ready - f.run.frame - 1)
                        self.assertGreater(ability.remaining(), 0)
                        f.run.step({f.b: (*facing, button)})
                    self.assertTrue(guard.active)
                    self.assertNotEqual(guard.counter, activation)
            self.assertEqual(samples[:1], samples[1:])

    def test_world_only_grasp_converts_and_decays_or_loses_shields_to_late_emp(self):
        for emp in (False, True):
            samples = []
            for gap in (False, True):
                with self.subTest(emp=emp, gap=gap):
                    f, facing, guard = self.arena(shot_guards.SIGMA)
                    match = f.run.match
                    vitals = match.combat.vitals[f.b.body]
                    self.shoot(f, facing, frames=10)
                    self.assertEqual(guard.instance.vars[21312].value(), 32)
                    if emp:
                        f.press(0, 99)
                        self.assertTrue(guard.active)
                        self.assertEqual(vitals.temporary_shield_total, 0)
                        f.a.body_script.set_ult_charge(1400)
                        f.press(32)
                    start = f.run.frame
                    outcomes = []
                    for offset in (60,) if emp else (150, 400, 700):
                        at = start + offset
                        if gap:
                            match.update(at * combat.TICK, at)
                        else:
                            f.press(0, at - f.run.frame)
                        self.assertFalse(guard.active)
                        outcomes.append(vitals.temporary_shield_total)
                    self.assertEqual(vitals.now[HEALTH_POOL], 300)
                    if emp:
                        self.assertEqual(vitals.now[SHIELDS_POOL], 0)
                        self.assertEqual(outcomes, [0])
                        self.assertAlmostEqual(f.a.stats.value(stats.HERO_DAMAGE), 119.2, places=5)
                    else:
                        self.assertAlmostEqual(outcomes[0], 19.2, places=5)
                        self.assertLess(outcomes[1], outcomes[0])
                        self.assertEqual(outcomes[-1], 0)
                        self.assertEqual(vitals.now[SHIELDS_POOL], 100)
                    samples.append(outcomes)
            for continuous, skipped in zip(*samples, strict=True):
                self.assertAlmostEqual(continuous, skipped, places=6)

    def test_wall_and_rear_attacks_keep_collision_order(self):
        for hero in (shot_guards.GENJI, shot_guards.SIGMA):
            for wall in (True, False):
                with self.subTest(hero=hero, wall=wall):
                    fixture, facing, _ = self.arena(hero, rear=not wall, wall=wall)
                    before = fixture.health(fixture.b)
                    self.shoot(fixture, facing)
                    self.assertEqual(fixture.health(fixture.b), before - (0 if wall else 8))
                    self.assertEqual(fixture.health(fixture.a), 200)

    def test_hack_and_emp_remove_guard_interception(self):
        for hero in (shot_guards.GENJI, shot_guards.SIGMA):
            for button in (2, 0x20):
                with self.subTest(hero=hero, button=button):
                    fixture, facing, guard = self.arena(hero)
                    fixture.a.body_script.set_ult_charge(1400)
                    frames = 85 if button == 2 else 42
                    for frame in range(frames):
                        self.assertTrue(guard.active)
                        fixture.press(button if button == 2 or frame == 0 else 0)
                        if fixture.hack_applications():
                            break
                    self.assertTrue(fixture.hack_applications())
                    self.assertFalse(guard.active)
                    fixture.press(0, frames - frame - 1)
                    before = fixture.health(fixture.b)
                    self.shoot(fixture, facing)
                    self.assertEqual(fixture.health(fixture.b), before - 8)
                    self.assertEqual(fixture.health(fixture.a), 200)

    def test_silence_interrupts_grasp_before_absorbed_damage_converts(self):
        for button in (2, 0x20):
            with self.subTest(button=button):
                fixture, facing, guard = self.arena(shot_guards.SIGMA)
                self.shoot(fixture, facing, frames=10)
                absorbed = guard.instance.vars[21312].value()
                self.assertGreater(absorbed, 0)
                fixture.a.body_script.set_ult_charge(1400)
                for frame in range(85 if button == 2 else 42):
                    self.assertTrue(guard.active)
                    fixture.press(button if button == 2 or frame == 0 else 0)
                    if fixture.hack_applications():
                        break
                self.assertTrue(fixture.hack_applications())
                self.assertFalse(guard.active)
                fixture.press(0, 145)
                vitals = fixture.run.match.combat.vitals[fixture.b.body]
                self.assertEqual(vitals.temporary_shield_total, 0)
                self.assertEqual(vitals.now[SHIELDS_POOL], 100 if button == 2 else 0)
                self.assertEqual(vitals.now[HEALTH_POOL], 300)
                instances = guard.instance.component.instances.values()
                self.assertFalse(any(i.graph.index == 0x1D03 for i in instances))

    def test_lethal_melee_during_grasp_prevents_shield_conversion(self):
        f, facing, guard = self.arena(shot_guards.SIGMA, distance=1.5)
        fight = f.run.match.combat
        f.press(FIRE)
        self.assertEqual(guard.instance.vars[21312].value(), 8)
        self.assertTrue(guard.active)
        body = f.b.body
        vitals = fight.vitals[body]
        vitals.now[SHIELDS_POOL] = 0
        vitals.now[HEALTH_POOL] = 30

        self.melee(f, facing)
        self.assertTrue(fight.is_dead(f.b))
        self.assertEqual(vitals.killer, f.a.body)
        self.assertIsNone(shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000))
        for _ in range(145):
            f.press(0)
            self.assertEqual(vitals.temporary_shield_total, 0)
            instances = guard.instance.component.instances.values()
            self.assertFalse(any(i.graph.index == 0x1D03 for i in instances))
        self.assertEqual(f.b.body, body)
        self.assertTrue(fight.is_dead(f.b))

    def test_genji_parries_frontal_melee_and_takes_rear_melee(self):
        for rear, wall, loss in ((False, False, 0), (True, False, 30), (True, True, 0)):
            with self.subTest(rear=rear, wall=wall):
                fixture, facing, _ = self.arena(shot_guards.GENJI, rear=rear, wall=wall, distance=1.5)
                self.melee(fixture, facing)
                self.assertEqual(fixture.health(fixture.b), 200 - loss)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertEqual(len(fixture.a.client.messages(combat.HIT)), int(bool(loss)))
                self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), loss)

    def test_sigma_grasp_allows_melee_without_absorption(self):
        fixture, facing, guard = self.arena(shot_guards.SIGMA, distance=1.5)
        before = fixture.health(fixture.b)
        self.melee(fixture, facing)
        self.assertEqual(fixture.health(fixture.b), before - 30)
        self.assertEqual(guard.instance.vars[21312].value(), 0)

    def test_genji_melee_parry_ends_on_expiry_or_hack(self):
        for hack in (False, True):
            with self.subTest(hack=hack):
                fixture, facing, guard = self.arena(shot_guards.GENJI, distance=1.5)
                yaw, pitch = aim(fixture.run.match.combat._eye(fixture.a), (0, 1, 1.5))
                for _ in range(85 if hack else 150):
                    fixture.run.step({fixture.a: (yaw, pitch, 2 if hack else 0),
                                      fixture.b: (*facing, 0)})
                if hack:
                    self.assertTrue(fixture.hack_applications())
                else:
                    self.assertFalse(guard.active)
                self.melee(fixture, facing)
                self.assertEqual(fixture.health(fixture.b), 170)
                self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 30)

    @staticmethod
    def melee(fixture, facing):
        fixture.run.step({fixture.a: (0, 0, scenes.MELEE), fixture.b: (*facing, 0)})
        for _ in range(28):
            fixture.run.step({fixture.b: (*facing, 0)})
