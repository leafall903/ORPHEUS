"""Actual Sound Barrier casts and Roadhog combat acceptance."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as fixtures

from ow174.game import lucio_barrier, npc_control, stats
from ow174.game.collision import World
from ow174.game.content import heroes


class RoadhogSoundBarrierTests(unittest.TestCase):
    def scene(self, distance=2):
        run, hog, lucio, enemy = fixtures.RoadhogDefenseTests().session("Mei", distance=distance)
        self.assertTrue(run.match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
        x, y, z = hog.mover.position
        lucio.mover.position[:] = (x, y, z + distance)
        lucio.mover.body = None
        lucio.body_script.ult_seconds = 0
        lucio.body_script.set_ult_charge(0)
        for _ in range(12):
            run.step({})
        return run, hog, lucio, enemy

    def vitals(self, run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

    def cast(self, run, lucio):
        fight = run.match.combat
        script = lucio.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({lucio: (0, 0, 32)})
        for _ in range(80):
            run.step({})
            if lucio.body in fight.sound_barriers.leases:
                break
        self.assertIn(lucio.body, fight.sound_barriers.leases)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(self.vitals(run, lucio).temporary_shields[lucio_barrier.POOL].now, 750)

    def test_roadhog_weapons_spend_only_sound_barrier_without_damage_charge(self):
        for button in (1, 2, 0x800, 8, 32):
            with self.subTest(button=button):
                run, hog, lucio, _ = self.scene(distance=1.7 if button == 0x800 else 2)
                fight = run.match.combat
                pool = self.vitals(run, lucio)
                before = pool.total
                self.cast(run, lucio)
                charge = hog.body_script.component.entity_var(342)
                if button == 32:
                    hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
                x, y, z = lucio.mover.position
                angles = fixtures.aim(fight._eye(hog), (x, y + 1.0, z))
                run.step({hog: (*angles, button)})
                cast_tick = fight.tick - 1
                caught = False
                damage = 0
                for _ in range(60):
                    run.step({hog: (*angles, 0)})
                    caught |= fight.hooks.controlled(lucio.body)
                    shield = pool.temporary_shields[lucio_barrier.POOL].now
                    damage = 750 - (fight.tick - cast_tick) * 2 - shield
                    if damage > 1 and (button != 8 or caught):
                        break
                self.assertGreater(damage, 0)
                self.assertEqual(sum(pool.now.values()), before)
                self.assertEqual(charge.value(), 0)
                self.assertEqual(caught, button == 8)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_sound_barrier_absorbs_all_roadhog_forms_on_each_npc(self):
        import test_game_roadhog_npcs as actual

        case = actual.RoadhogFiveNpcTests()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        attacks = (
            ("primary", 1, 4),
            ("slug", 2, 4),
            ("fragments", 2, 12),
            ("melee", 0x800, 1.5),
            ("hook", 8, 4),
            ("whole hog", 32, 4),
        )
        for definition in case.definitions.values():
            for attack, button, distance in attacks:
                with self.subTest(npc=definition.name, attack=attack):
                    self.check_sound_barrier_on_npc(case, definition, attack, button, distance)

    def check_sound_barrier_on_npc(self, case, definition, attack, button, distance):
        run, hog, bot, pool, look = case.session(definition, distance=distance, caster=True)
        lucio = run.players[1]
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, lucio.team = 0, bot.team
        self.assertTrue(match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
        x, y, z = hog.mover.position
        lucio.mover.position[:] = (x + 5, y, z)
        lucio.mover.body = None
        script = hog.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        for _ in range(12):
            run.step({})
        self.cast(run, lucio)
        shield = pool.temporary_shields[lucio_barrier.POOL]
        self.assertEqual(shield.now, 750)
        before = pool.permanent_total
        self.assertEqual(pool.ult_healing_deficit, 0)
        if button == 32:
            script.set_ult_charge(script.component.entity_var(338).value())

        cast_tick = fight.tick
        run.step({hog: (*look, button)})
        fragments_released = False
        for _ in range(95):
            fragments_released |= len(fight.rockets) > 1
            spent = 750 - 2 * (fight.tick - cast_tick) - shield.now
            if spent > 0:
                break
            run.step({hog: (*look, 0)})
        self.assertGreater(spent, 0)
        self.assertAlmostEqual(hog.stats.value(stats.HERO_DAMAGE), spent)
        self.assertEqual(pool.permanent_total, before)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        if attack == "fragments":
            self.assertTrue(fragments_released)
        if button == 8:
            self.assertTrue(fight.hooks.controlled(bot.entity))
            before_z = bot.position[2]
            for _ in range(35):
                run.step({hog: (*look, 0)})
                if bot.position[2] < before_z:
                    break
            self.assertLess(bot.position[2], before_z)
        elif button == 32:
            self.assertGreater(fight.impulses.velocity(bot.entity)[2], 0)
            before_z = bot.position[2]
            run.step({hog: (*look, 0)})
            self.assertGreater(bot.position[2], before_z)
        self.assertEqual(pool.permanent_total, before)
        self.assertGreater(shield.now, 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_sound_barrier_protects_roadhog_from_all_five_manual_npcs(self):
        import test_game_roadhog_npcs as actual

        case = actual.RoadhogFiveNpcTests()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        for definition in case.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_sound_barrier_against_npc(case, definition)

    def manual_npc_sound_scene(self, case, definition):
        from test_game_roadhog_npcs import NpcClient

        assassin = definition.name == "Assassin"
        run, hog, bot, _, look = case.session(definition, distance=2 if assassin else 10, caster=True)
        lucio = run.players[1]
        lucio.client = NpcClient(lucio)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = lucio.team = 0
        self.assertTrue(match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
        x, y, z = hog.mover.position
        lucio.mover.position[:] = (x + 5, y, z)
        lucio.mover.body = None
        self.cast(run, lucio)
        pool = self.vitals(run, hog)
        shield = pool.temporary_shields[lucio_barrier.POOL]
        self.assertEqual(shield.now, 750)
        hog.mover.yaw, hog.mover.pitch = look
        return run, hog, bot, look, pool, shield

    def check_sound_barrier_against_npc(self, case, definition):
        run, hog, bot, look, pool, shield = self.manual_npc_sound_scene(case, definition)
        match = run.match
        fight = match.combat
        before = pool.permanent_total
        self.assertEqual(npc_control.start(match, hog), bot.entity)
        cast_tick = fight.tick
        for _ in range(200):
            run.step({hog: (*look, 0)})
            spent = 750 - 2 * (fight.tick - cast_tick) - shield.now
            if spent > 0:
                break
        self.assertGreater(spent, 0)
        self.assertEqual(pool.permanent_total, before)
        self.assertEqual(pool.ult_healing_deficit, 0)
        if definition.name == "Assassin":
            self.assertEqual(fight.pins.get(bot.entity), hog.body)
            self.assertEqual(spent, 25)
        npc_control.stop(match)
        self.assertNotIn(bot.entity, fight.pins)

    def test_actual_slug_credits_only_permanent_pool_loss_after_sound_barrier_spill(self):
        for remaining, expected_loss in ((100, 0), (54, 0), (40, 14)):
            with self.subTest(remaining=remaining):
                run, hog, lucio, _ = self.scene()
                fight = run.match.combat
                pool = self.vitals(run, lucio)
                before = pool.permanent_total
                self.cast(run, lucio)
                shield = pool.temporary_shields[lucio_barrier.POOL]
                for _ in range(375):
                    if shield.now <= remaining:
                        break
                    run.step({})
                self.assertEqual(shield.now, remaining)
                script = hog.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                charge = script.component.entity_var(342)
                x, y, z = lucio.mover.position
                angles = fixtures.aim(fight._eye(hog), (x, y + 1, z))
                contact = []
                damage = fight._damage

                def observe(shooter, target, amount, *args, **kwargs):
                    before_hit = shield.now
                    damage(shooter, target, amount, *args, **kwargs)
                    if shooter is hog and target.owner is lucio:
                        contact.append((amount, before_hit, shield.now))

                with patch.object(fight, "_damage", side_effect=observe):
                    run.step({hog: (*angles, 2)})
                    for _ in range(8):
                        if contact:
                            break
                        run.step({hog: (*angles, 0)})
                self.assertEqual(contact, [(50, remaining - 4, max(0, remaining - 4 - 50))])
                self.assertEqual(pool.permanent_total, before - expected_loss)
                self.assertEqual(pool.ult_healing_deficit, expected_loss)
                self.assertEqual(charge.value(), expected_loss)

    def test_ally_shield_absorbs_damage_and_decays_without_healing_it(self):
        run, hog, lucio, enemy = self.scene(distance=5)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        lucio.team = hog.team = 0
        enemy.team = 1
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x + 4, y, z)
        enemy.mover.body = None
        before = self.vitals(run, hog).total
        self.cast(run, lucio)
        pool = self.vitals(run, hog)
        self.assertEqual(pool.total, before + 750)
        self.assertNotIn(enemy.body, fight.sound_barriers.leases)
        angles = fixtures.aim(fight._eye(enemy), (x, y + 1.2, z))
        run.step({hog: (0, 0, 16)})
        for _ in range(65):
            run.step({})
            if fight.incoming_damage_scale(hog) == 0.5:
                break
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        shield = pool.temporary_shields[lucio_barrier.POOL]
        remaining = shield.now
        run.step({enemy: (*angles, 1)})
        for _ in range(4):
            run.step({})
        self.assertAlmostEqual(shield.now, remaining - 10 - 10)
        self.assertEqual(sum(pool.now.values()), before)
        for _ in range(375):
            run.step({})
        self.assertNotIn(hog.body, fight.sound_barriers.leases)
        self.assertEqual(pool.total, before)
        self.assertEqual(shield.now, 0)

    def test_breather_heals_only_damage_that_spills_past_decaying_sound_barrier(self):
        self.check_breather_sound_barrier_spill()

    def test_roadhog_passive_scales_enemy_charge_after_breather_and_sound_barrier(self):
        self.check_breather_sound_barrier_spill(mirror=True)

    def check_breather_sound_barrier_spill(self, *, mirror=False):
        run, hog, lucio, enemy = self.scene(distance=5)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = lucio.team = 0
        enemy.team = 1
        if mirror:
            self.assertTrue(run.match.switch_hero(enemy, hog.hero))
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x + 4, y, z)
        enemy.mover.body = None
        self.cast(run, lucio)
        pool = self.vitals(run, hog)
        shield = pool.temporary_shields[lucio_barrier.POOL]
        charge = hog.body_script.component.entity_var(342)
        for _ in range(360):
            if shield.now <= 40:
                break
            run.step({})
        self.assertEqual(shield.now, 40)
        run.step({hog: (0, 0, 16)})
        shield_at_fire = 18 if mirror else 8
        for _ in range(30):
            if shield.now <= shield_at_fire:
                break
            run.step({})
        self.assertEqual(shield.now, shield_at_fire)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        self.assertEqual(pool.permanent_total, 600)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertEqual(charge.value(), 0)
        if mirror:
            enemy.body_script.ult_seconds = 0
            enemy.body_script.set_ult_charge(0)

        damage = fight._damage
        at_contact = []

        def observe(shooter, target, amount, *args, **kwargs):
            if shooter is enemy and target.owner is hog:
                at_contact.append((amount, shield.now))
            return damage(shooter, target, amount, *args, **kwargs)

        angles = fixtures.aim(fight._eye(enemy), (x, y + 1.2, z))
        with patch.object(fight, "_damage", side_effect=observe):
            run.step({enemy: (*angles, 2 if mirror else 1)})
            for _ in range(8):
                if at_contact:
                    break
                run.step({})
        if mirror:
            (amount, shield_at_contact), = at_contact
            self.assertEqual(amount, 50)
            self.assertGreater(shield_at_contact, 0)
            self.assertLess(shield_at_contact, 25)
            spill = 25 - shield_at_contact
            self.assertEqual(enemy.body_script.component.entity_var(342).value(), .75 * spill)
        else:
            self.assertEqual(at_contact, [(20, 6)])
            spill = 4
        self.assertEqual(shield.now, 0)
        self.assertEqual(pool.permanent_total, 600 - spill)
        self.assertEqual(pool.ult_healing_deficit, spill)

        for _ in range(155):
            run.step({})
        self.assertNotIn(hog.body, fight.sound_barriers.leases)
        self.assertEqual(pool.total, 600)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertAlmostEqual(charge.value(), spill, places=4)
        self.assertEqual(fight.incoming_damage_scale(hog), 1.0)

    def test_breather_primary_spill_after_projected_and_sound_barrier(self):
        from ow174.game.script.expr import f32

        for remaining in (5, f32(6.6)):
            with self.subTest(remaining=remaining):
                self.check_breather_primary_spill_after_two_barriers(remaining)

    def check_breather_primary_spill_after_two_barriers(self, remaining):
        import test_game_roadhog_zarya as zarya_tests
        from test_game_combat import in_world

        from ow174.game.content import SOLDIER

        run, hog, lucio, enemy = self.scene(distance=5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = lucio.team = 0
        enemy.team = 1
        self.assertTrue(match.switch_hero(enemy, hog.hero))
        zarya = match.add_player(4, "Zarya", SOLDIER, 3, False)
        in_world(match, zarya)
        run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, heroes()[zarya_tests.ZARYA]))
        zarya.team = hog.team
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x + 2, y, z)
        zarya.mover.position[:] = (x - 4, y, z)
        enemy.mover.body = zarya.mover.body = None
        enemy.body_script.ult_seconds = 0
        enemy.body_script.set_ult_charge(0)
        self.cast(run, lucio)
        pool = self.vitals(run, hog)
        shield = pool.temporary_shields[lucio_barrier.POOL]
        for _ in range(375):
            if shield.now <= 24:
                break
            run.step({})
        self.assertEqual(shield.now, 24)
        zarya_look = fixtures.aim(fight._eye(zarya), (x, y + 1, z))
        run.step({zarya: (*zarya_look, 0)})
        bubble = zarya_tests.RoadhogZaryaTests().cast(run, hog, zarya, hog, zarya_look, projected=True)
        bubble.health = remaining
        run.step({hog: (0, 0, 16)})
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertEqual(pool.permanent_total, 600)
        self.assertEqual(pool.ult_healing_deficit, 0)

        events = []
        damage = fight._damage
        damage_barrier = fight._damage_barrier

        def observe_barrier(*args, **kwargs):
            before = bubble.health
            sound_before = shield.now
            result = damage_barrier(*args, **kwargs)
            if bubble.health < before:
                events.append(("bubble", before - bubble.health, sound_before, shield.now, pool.permanent_total))
            return result

        def observe_body(shooter, target, amount, *args, **kwargs):
            if shooter is not enemy or target.owner is not hog:
                return damage(shooter, target, amount, *args, **kwargs)
            sound_before = shield.now
            health_before = pool.permanent_total
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
            result = damage(shooter, target, amount, *args, **kwargs)
            events.append(("body", amount, sound_before, shield.now, health_before - pool.permanent_total))
            return result

        look = fixtures.aim(fight._eye(enemy), (x, y + 1, z))
        with (patch.object(fight, "_damage_barrier", side_effect=observe_barrier),
              patch.object(fight, "_damage", side_effect=observe_body)):
            run.step({enemy: (*look, 1)})
            for _ in range(12):
                run.step({})
        kind, spent, sound_before, sound_after, permanent = events[0]
        self.assertEqual(kind, "bubble")
        self.assertAlmostEqual(spent, remaining, places=4)
        self.assertGreater(sound_before, 0)
        self.assertEqual(sound_after, sound_before)
        self.assertEqual(permanent, 600)
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(hog))
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), remaining * .2, places=4)
        temporary_loss = 0
        spill = 0
        for kind, amount, sound_before, sound_after, permanent_loss in events[1:]:
            self.assertEqual(kind, "body")
            expected_absorbed = min(sound_before, amount * .5)
            self.assertAlmostEqual(sound_before - sound_after, expected_absorbed, places=4)
            self.assertAlmostEqual(permanent_loss, amount * .5 - expected_absorbed, places=4)
            temporary_loss += sound_before - sound_after
            spill += permanent_loss
        self.assertGreater(temporary_loss, 0)
        self.assertGreater(spill, 0)
        self.assertEqual(shield.now, 0)
        self.assertAlmostEqual(pool.permanent_total, 600 - spill, places=4)
        self.assertAlmostEqual(pool.ult_healing_deficit, spill, places=4)
        self.assertAlmostEqual(enemy.body_script.component.entity_var(342).value(), spill * .75, places=4)

        for _ in range(155):
            run.step({})
        self.assertEqual(pool.permanent_total, 600)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertAlmostEqual(hog.body_script.component.entity_var(342).value(), spill, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_manual_npc_attacks_break_projected_barrier_before_reaching_sound_barrier(self):
        import test_game_roadhog_npcs as actual

        case = actual.RoadhogFiveNpcTests()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        for definition in case.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_manual_npc_breaks_projected_barrier(case, definition, actual.NpcClient)

    def check_manual_npc_breaks_projected_barrier(self, case, definition, client):
        import test_game_roadhog_zarya as zarya_tests
        from test_game_combat import in_world

        from ow174.game.content import SOLDIER

        assassin = definition.name == "Assassin"
        sniper = definition.name == "Sniper"
        run, hog, bot, _, look = case.session(definition, distance=2 if assassin else 10, caster=True)
        lucio = run.players[1]
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = lucio.team = 0
        self.assertTrue(match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
        zarya = match.add_player(4, "Zarya", SOLDIER, 3, False)
        in_world(match, zarya)
        run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, heroes()[zarya_tests.ZARYA]))
        zarya.team = hog.team
        lucio.client = client(lucio)
        zarya.client = client(zarya)
        x, y, z = hog.mover.position
        lucio.mover.position[:] = (x + 5, y, z)
        zarya.mover.position[:] = (x - 4, y, z)
        lucio.mover.body = zarya.mover.body = None
        hog.body_script.ult_seconds = 0
        hog.body_script.set_ult_charge(0)
        self.cast(run, lucio)
        pool = self.vitals(run, hog)
        shield = pool.temporary_shields[lucio_barrier.POOL]
        cast_aim = fixtures.aim(fight._eye(zarya), (x, y + 1, z))
        aim_hog = {hog: (*look, 0)}
        run.step({**aim_hog, zarya: (*cast_aim, 0)})
        if sniper:
            self.assertEqual(npc_control.start(match, hog), bot.entity)
            for _ in range(240):
                run.step({**aim_hog, zarya: (*cast_aim, 0)})
                if any(shot.shooter is bot for shot in fight.rockets):
                    break
            self.assertTrue(any(shot.shooter is bot for shot in fight.rockets))

        run.step({hog: (*look, 16), zarya: (*cast_aim, 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertEqual(pool.permanent_total, 600)
        bubble.health = 1
        if not sniper:
            self.assertEqual(npc_control.start(match, hog), bot.entity)

        events = []
        damage = fight._damage
        damage_bubble = fight.zarya_bubbles.damage

        def observe_bubble(target, amount, shooter):
            sound_before = shield.now
            result = damage_bubble(target, amount, shooter)
            if target is bubble and shooter is bot:
                events.append("break")
                self.assertGreater(amount, 1)
                self.assertEqual(result, 1)
                self.assertEqual(shield.now, sound_before)
                self.assertEqual(pool.permanent_total, 600)
            return result

        def observe_body(shooter, target, amount, *args, **kwargs):
            before = shield.now
            result = damage(shooter, target, amount, *args, **kwargs)
            if shooter is bot and target.owner is hog and shield.now < before:
                events.append("sound")
                self.assertEqual(fight.incoming_damage_scale(hog), .5)
                self.assertAlmostEqual(before - shield.now, amount * .5, places=4)
            return result

        with (patch.object(fight.zarya_bubbles, "damage", side_effect=observe_bubble),
              patch.object(fight, "_damage", side_effect=observe_body)):
            for _ in range(100):
                run.step(aim_hog)
                if events and (sniper or assassin or "sound" in events):
                    break
        self.assertTrue(events)
        self.assertEqual(events[0], "break")
        self.assertEqual(events.count("break"), 1)
        self.assertEqual(events.count("sound") > 0, not (sniper or assassin))
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(hog))
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), .2, places=4)
        self.assertEqual(pool.permanent_total, 600)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
        if assassin:
            self.assertIsNotNone(bot.body_script.component.world.rejected_pin_attempt)
            sound_before = shield.now
            for _ in range(65):
                run.step(aim_hog)
                self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(shield.now, sound_before - 130)
            self.assertEqual(pool.permanent_total, 600)
        npc_control.stop(match)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_breather_healing_charge_tracks_permanent_spill_from_each_npc(self):
        import test_game_roadhog_npcs as actual

        case = actual.RoadhogFiveNpcTests()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        for definition in case.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_npc_spill_healing(case, definition)

    def check_npc_spill_healing(self, case, definition):
        from ow174.game.script import nodes

        # Locate the first actual impact for each graph before arranging the shield boundary.
        run, hog, bot, look, _, shield = self.manual_npc_sound_scene(case, definition)
        self.assertEqual(npc_control.start(run.match, hog), bot.entity)
        first = run.frame
        for _ in range(240):
            run.step({hog: (*look, 0)})
            if shield.now < 750 - 2 * (run.frame - first):
                break
        self.assertLess(shield.now, 750 - 2 * (run.frame - first))
        impact_offset = run.frame - first

        run, hog, bot, look, pool, shield = self.manual_npc_sound_scene(case, definition)
        match = run.match
        fight = match.combat
        assassin = definition.name == "Assassin"
        hog.body_script.ult_seconds = 0
        hog.body_script.set_ult_charge(0)
        if assassin:
            run.step({hog: (*look, 16)})
        self.assertEqual(npc_control.start(match, hog), bot.entity)
        impact = run.frame + impact_offset
        if not assassin:
            self.assertGreater(impact_offset, 2)
            while run.frame < impact - 2:
                run.step({hog: (*look, 0)})
            run.step({hog: (*look, 16)})
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertEqual(pool.permanent_total, 600)
        pool.decay_temporary_shields(lucio_barrier.POOL, shield.now - 4)
        self.assertEqual(shield.now, 4)

        contacts = []
        damage = fight._damage

        def observe(shooter, target, amount, *args, **kwargs):
            sound_before = shield.now
            hp_before = pool.permanent_total
            scalar = fight.incoming_damage_scale(hog)
            result = damage(shooter, target, amount, *args, **kwargs)
            if shooter is bot and target.owner is hog:
                contacts.append((amount, scalar, sound_before, hp_before - pool.permanent_total))
            return result

        with patch.object(fight, "_damage", side_effect=observe):
            run.step({hog: (*look, 0)})
        self.assertEqual(run.frame, impact)
        self.assertTrue(contacts)
        self.assertIn(contacts[0][2], (2, 4))
        self.assertEqual(shield.now, 0)
        spill = 0
        for amount, scalar, sound_before, hp_loss in contacts:
            self.assertEqual(scalar, 1 if assassin else .5)
            self.assertAlmostEqual(hp_loss, max(0, amount * scalar - sound_before), places=4)
            spill += hp_loss
        self.assertGreater(spill, 0)
        self.assertAlmostEqual(pool.ult_healing_deficit, spill, places=4)
        self.assertAlmostEqual(pool.permanent_total, 600 - spill, places=4)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
        if assassin:
            self.assertEqual(fight.pins.get(bot.entity), hog.body)
            self.assertEqual(fight.incoming_damage_scale(hog), 1)
        npc_control.stop(match)

        if assassin:
            breather = next(state for i in hog.body_script.component.instances.values() if i.graph.index == 0x0240
                            for state in i.states.values() if isinstance(state, nodes.Ability))
            self.assertGreater(breather.remaining(), 0)
            for _ in range(650):
                if breather.remaining() == 0:
                    break
                run.step({})
            self.assertEqual(breather.remaining(), 0)
            self.assertAlmostEqual(pool.permanent_total, 600 - spill, places=4)
            self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
            run.step({hog: (*look, 16)})

        for _ in range(155):
            run.step({})
        self.assertEqual(pool.permanent_total, 600)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertAlmostEqual(hog.body_script.component.entity_var(342).value(), spill, places=4)

    def test_ally_shield_rejects_cover_range_and_enemy(self):
        for mode in ("wall", "range", "enemy"):
            with self.subTest(mode=mode):
                run, hog, lucio, _ = self.scene(distance=38 if mode == "range" else 5)
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                lucio.team, hog.team = 0, 1 if mode == "enemy" else 0
                if mode == "wall":
                    _, y, z = hog.mover.position
                    wall = z + 2.5
                    run.match.collision_world = World([
                        (-30, y, -30, -30, y, 80, 30, y, 80),
                        (-30, y, -30, 30, y, 80, 30, y, -30),
                        (-15, y - 2, wall, 15, y + 8, wall, 15, y - 2, wall),
                        (-15, y - 2, wall, -15, y + 8, wall, 15, y + 8, wall),
                    ])
                self.cast(run, lucio)
                self.assertNotIn(hog.body, fight.sound_barriers.leases)
                self.assertNotIn(lucio_barrier.POOL, self.vitals(run, hog).temporary_shields)

    def test_hook_interrupts_sound_barrier_windup_without_refunding_charge(self):
        run, hog, lucio, _ = self.scene()
        fight = run.match.combat
        before = self.vitals(run, lucio).total
        script = lucio.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        x, y, z = lucio.mover.position
        angles = fixtures.aim(fight._eye(hog), (x, y + 1.1, z))
        run.step({hog: (*angles, 8), lucio: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        caught = False
        for _ in range(115):
            run.step({hog: (*angles, 0)})
            caught |= fight.hooks.controlled(lucio.body)
            self.assertNotIn(lucio.body, fight.sound_barriers.leases)
        self.assertTrue(caught)
        self.assertEqual(self.vitals(run, lucio).total, before - 30)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_airborne_cast_delays_shield_until_landing_then_charge_recovers_after_expiry(self):
        run, hog, lucio, _ = self.scene(distance=4)
        fight = run.match.combat
        script = lucio.body_script
        run.step({lucio: (0, 0, 32)})
        for _ in range(55):
            run.step({})
        self.assertNotIn(lucio.body, fight.sound_barriers.leases)
        x, y, z = lucio.mover.position
        lucio.mover.position[:] = (x, y + 18, z)
        lucio.mover.body = None
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({lucio: (0, 0, 32)})
        for _ in range(50):
            run.step({})
        self.assertGreater(lucio.mover.air_ticks, 0)
        self.assertNotIn(lucio.body, fight.sound_barriers.leases)
        for _ in range(145):
            run.step({})
            if lucio.body in fight.sound_barriers.leases:
                break
        self.assertEqual(lucio.mover.air_ticks, 0)
        self.assertIn(lucio.body, fight.sound_barriers.leases)
        for _ in range(375):
            run.step({})
        self.assertNotIn(lucio.body, fight.sound_barriers.leases)
        pool = self.vitals(run, lucio)
        before = pool.total
        x, y, z = lucio.mover.position
        angles = fixtures.aim(fight._eye(hog), (x, y + 1.1, z))
        run.step({hog: (*angles, 2)})
        for _ in range(30):
            run.step({})
        self.assertEqual(pool.total, before - 50)
        self.assertGreater(hog.body_script.component.entity_var(342).value(), 0)

    def test_two_real_casts_refresh_same_pool_without_stacking(self):
        run, hog, first, second = self.scene(distance=5)
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(second, heroes()[lucio_barrier.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = first.team = second.team = 0
        x, y, z = hog.mover.position
        second.mover.position[:] = (x + 5, y, z)
        second.mover.body = None
        self.cast(run, first)
        pool = self.vitals(run, hog)
        shield = pool.temporary_shields[lucio_barrier.POOL]
        first_lease = fight.sound_barriers.leases[hog.body]
        for _ in range(50):
            run.step({})
        self.assertEqual(shield.now, 650)
        script = second.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({second: (0, 0, 32)})
        for _ in range(85):
            run.step({})
            if fight.sound_barriers.leases[hog.body] is not first_lease:
                break
        self.assertIsNot(fight.sound_barriers.leases[hog.body], first_lease)
        self.assertIs(pool.temporary_shields[lucio_barrier.POOL], shield)
        self.assertEqual(shield.now, 750)
        self.assertEqual(len(pool.temporary_shields), 1)
