"""Actual0B8B timer, damage events, charge denial and ultimate eligibility."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn as projectile_tests
import test_game_torbjorn_npcs as npc_tests
import test_game_torbjorn_turret as turret_tests
import test_game_torbjorn_ultimate as ultimate_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import hero_torbjorn, molten_core, npc, sigma_grasp, stats, torbjorn_charge
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared Torbjorn data")
class TorbjornChargeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        turret_tests.TorbjornTurretTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        turret_tests.TorbjornTurretTests.tearDownClass()

    @staticmethod
    def charge(owner):
        return owner.body_script.component.entity_var(342).value()

    @staticmethod
    def advance(run, ticks, owner=None, button=0, yaw=0, pitch=0):
        for _ in range(ticks):
            run.step({owner: (yaw, pitch, button)} if owner else {})

    def quiet(self, run, owner):
        owner.body_script.ult_seconds = 0
        self.advance(run, 1)
        owner.body_script.set_ult_charge(0.0)

    def arena(self, distance=1.5):
        run, owner, victim = projectile_tests.TorbjornProjectileTests().arena(distance)
        self.quiet(run, owner)
        target = next(t for t in run.match.combat._targets() if t.owner is victim)
        return run, owner, victim, run.match.combat.vitals_of(target)

    def select_hammer(self, run, owner):
        run.frame += 1
        owner.take_commands([Command(run.frame, action=2)])
        self.advance(run, 45)

    def test_four_actual_player_attacks_charge_by_actual_health_loss(self):
        for attack, button, loss in (("primary", 1, 70), ("secondary", 2, 125),
                                      ("quick-melee", 0x800, 30), ("hammer", 1, 55)):
            with self.subTest(attack=attack):
                run, owner, _, hp = self.arena()
                if attack == "hammer":
                    self.select_hammer(run, owner)
                run.step({owner: (0, 0, button)})
                self.advance(run, 40, owner if attack == "hammer" else None,
                             button if attack == "hammer" else 0)
                self.assertEqual(hp.total, 200 - loss)
                self.assertEqual(self.charge(owner), loss)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_four_actual_attacks_charge_against_each_of_five_npcs(self):
        fixture = npc_tests.TorbjornNpcTests()
        fixture.catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for key, definition in fixture.catalog.items():
            for attack, button, loss in (("primary", 1, 70), ("secondary", 2, 125),
                                          ("quick-melee", 0x800, 30), ("hammer", 1, 55)):
                with self.subTest(npc=definition.name, attack=attack):
                    run, owner, actor, hp = fixture.arena(key, 1.5)
                    self.quiet(run, owner)
                    if attack == "hammer":
                        self.select_hammer(run, owner)
                    before = hp.total
                    yaw, pitch = (fixture.body_aim(run, owner, actor)
                                  if attack in ("primary", "secondary") else (0, 0))
                    run.step({owner: (yaw, pitch, button)})
                    self.advance(run, 40, owner if attack == "hammer" else None,
                                 button if attack == "hammer" else 0)
                    self.assertEqual(before - hp.total, loss)
                    self.assertEqual(self.charge(owner), loss)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_all_five_npcs_blocked_by_wall_then_die_to_each_close_attack(self):
        fixture = npc_tests.TorbjornNpcTests()
        fixture.catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for key, definition in fixture.catalog.items():
            for attack, button in (("primary", 1), ("secondary", 2), ("quick-melee", 0x800), ("hammer", 1)):
                with self.subTest(npc=definition.name, attack=attack):
                    run, owner, actor, hp = fixture.arena(key, 1.5)
                    self.quiet(run, owner)
                    if attack == "hammer":
                        self.select_hammer(run, owner)
                    match = run.match
                    fight = match.combat
                    x, y, z = owner.mover.position
                    floor = box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    clear = World(floor)
                    covered = World(floor + wall(x - 4, z + .65, x + 4, z + .65, y0=y, y1=y + 4))
                    hp.damage(hp.total - 17, armour_reduction=False)
                    self.assertEqual(hp.total, 17)
                    yaw, pitch = fixture.body_aim(run, owner, actor)
                    for scene in (covered, clear):
                        match.collision_world = scene
                        run.step({owner: (yaw, pitch, button)})
                        if attack == "hammer":
                            self.advance(run, 36, owner, button, yaw, pitch)
                        self.advance(run, 55)
                        if scene is covered:
                            self.assertEqual(hp.total, 17)
                            self.assertFalse(actor.dead)
                            self.assertEqual(self.charge(owner), 0)
                            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

                    self.assertTrue(actor.dead)
                    self.assertEqual(hp.total, 0)
                    self.assertEqual(hp.killer, owner.body)
                    self.assertTrue(actor.body_script.component.owner_dead)
                    self.assertIsNotNone(actor.body_script.death_frame)
                    self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 17)
                    self.assertEqual(self.charge(owner), 17)

                    run.step({owner: (yaw, pitch, button)})
                    if attack == "hammer":
                        self.advance(run, 36, owner, button, yaw, pitch)
                    self.advance(run, 220)
                    self.assertIn(actor, match.bots)
                    self.assertFalse(any(candidate is actor for _, candidate in fight.respawns))
                    self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 17)
                    self.assertEqual(self.charge(owner), 17)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_quick_melee_rejects_then_recovers_at_each_npc_capsule_boundary(self):
        fixture = npc_tests.TorbjornNpcTests()
        fixture.catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for key, definition in fixture.catalog.items():
            with self.subTest(npc=definition.name):
                run, owner, actor, hp = fixture.arena(key, 1.5)
                self.quiet(run, owner)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is actor)
                eye = fight._eye(owner)
                lower, upper = target.segment()
                vertical = max(lower[1] - eye[1], eye[1] - upper[1], 0)
                # 0043 puts a radius-one sphere 1.5 m in front of the eye.
                reach = 1.5 + math.sqrt((1.0 + target.radius) ** 2 - vertical ** 2)
                x, y, z = owner.mover.position
                before = hp.total
                melee = next(instance for instance in owner.body_script.component.instances.values()
                             if instance.graph.index == 0x0043).state(4)
                for delta, yaw, loss in ((.01, 0, 0), (-.01, 16384, 0), (-.01, 0, 30), (.01, 0, 0)):
                    actor.position = (x, y, z + reach + delta)
                    self.advance(run, 4)
                    counter = melee.counter
                    charge = self.charge(owner)
                    run.step({owner: (yaw, 0, 0x800)})
                    self.advance(run, 65)
                    self.assertEqual(melee.counter, counter + 1)
                    before -= loss
                    self.assertEqual(hp.total, before)
                    self.assertEqual(self.charge(owner), charge + loss)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 30)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_rivet_misses_spend_ammo_without_charge_against_each_npc(self):
        fixture = npc_tests.TorbjornNpcTests()
        fixture.catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for key, definition in fixture.catalog.items():
            for button, cost, damage in ((1, 1, 70), (2, 2, 125)):
                with self.subTest(npc=definition.name, button=button):
                    run, owner, actor, hp = fixture.arena(key, 1.5)
                    self.quiet(run, owner)
                    rifle = next(instance for instance in owner.body_script.component.instances.values()
                                 if instance.graph.index == 0x1683)
                    before = hp.total
                    run.step({owner: (16384, 0, button)})
                    self.advance(run, 45)
                    self.assertEqual(rifle.vars[53].value(), 18 - cost)
                    self.assertEqual(hp.total, before)
                    self.assertEqual(self.charge(owner), 0)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                    yaw, pitch = fixture.body_aim(run, owner, actor)
                    if button == 2:
                        run.step({owner: (yaw, pitch, button)})
                        self.advance(run, 45)
                        self.assertEqual(rifle.vars[53].value(), 18 - cost)
                        self.assertEqual(hp.total, before)
                        self.assertEqual(self.charge(owner), 0)
                    run.step({owner: (yaw, pitch, button)})
                    self.advance(run, 45)
                    self.assertEqual(rifle.vars[53].value(), 18 - 2 * cost)
                    self.assertEqual(hp.total, before - damage)
                    self.assertEqual(self.charge(owner), damage)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_actual_secondary_spread_and_falloff_against_each_npc(self):
        fixture = npc_tests.TorbjornNpcTests()
        fixture.catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for key, definition in fixture.catalog.items():
            heavy = definition.name == "Heavy Assault"
            for distance, count in ((1.5, 10), (8, 10 if heavy else 7),
                                    (15, 5 if heavy else 3), (25, 3 if heavy else 1)):
                with self.subTest(npc=definition.name, distance=distance):
                    run, owner, actor, hp = fixture.arena(key, distance)
                    self.quiet(run, owner)
                    fight = run.match.combat
                    rifle = next(instance for instance in owner.body_script.component.instances.values()
                                 if instance.graph.index == 0x1683)
                    before = hp.total
                    yaw, pitch = fixture.body_aim(run, owner, actor)
                    with patch.object(fight, "_damage", wraps=fight._damage) as events:
                        run.step({owner: (yaw, pitch, 2)})
                        self.assertEqual(len(fight.rockets), 10)
                        self.assertEqual(len({rocket.direction for rocket in fight.rockets}), 10)
                        self.advance(run, 45)
                    amounts = [event.args[2] for event in events.call_args_list
                               if event.args[0] is owner and event.args[1].owner is actor]
                    self.assertEqual(len(amounts), count)
                    self.assertEqual(rifle.vars[53].value(), 16)
                    for amount in amounts:
                        if distance == 15:
                            self.assertGreater(amount, 8.3)
                            self.assertLess(amount, 8.8)
                        else:
                            self.assertAlmostEqual(amount, 3.75 if distance == 25 else 12.5, places=5)
                    self.assertAlmostEqual(before - hp.total, sum(amounts), places=5)
                    self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), sum(amounts), places=4)
                    self.assertAlmostEqual(self.charge(owner), sum(amounts), places=4)

    def test_armour_reduction_limits_charge_to_actual_loss(self):
        run, owner, victim, _ = self.arena()
        run.match.switch_hero(victim, heroes()[hero_torbjorn.HERO])
        self.advance(run, 35)
        x, y, z = victim.mover.position
        yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 0.7, z))
        run.step({owner: (yaw, pitch, 1)})
        self.advance(run, 25)
        self.assertEqual(run.match.combat.vitals[victim.body].total, 185)
        self.assertEqual(self.charge(owner), 65)

    def test_actual_adaptive_shield_cast_blocks_rivet_charge_and_expires(self):
        run, owner, ball, _ = self.arena(distance=100)
        match = run.match
        self.assertTrue(match.switch_hero(ball, heroes()[torbjorn_charge.WRECKING_BALL]))
        x, y, z = owner.mover.position
        ball.mover.position = [x, y, z + 100]
        ball.mover.body = None
        self.advance(run, 35)
        hp = match.combat.vitals[ball.body]
        base = hp.total
        run.step({ball: (0, 0, 16)})
        self.advance(run, 3)
        self.assertEqual(hp.total, base + 100)
        ball.mover.position = [x, y, z + 3]
        ball.mover.body = None
        self.advance(run, 3)
        direction = aim(match.combat._eye(owner), (x, y + .8, z + 3))
        run.step({owner: (*direction, 1)})
        self.advance(run, 20)
        self.assertEqual(hp.total, base + 30)
        self.assertEqual(self.charge(owner), 0)
        self.advance(run, 580)
        self.assertEqual(hp.total, base)
        instance = next(i for i in ball.body_script.component.instances.values() if i.graph.index == 0x1566)
        ability = instance.state(0)
        self.assertGreater(ability.remaining(), 5)
        ball.mover.position = [x, y, z + 100]
        ball.mover.body = None
        run.step({ball: (0, 0, 16)})
        self.advance(run, 20)
        self.assertEqual(hp.total, base)
        for _ in range(375):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(hp.total, base)
        run.step({ball: (0, 0, 16)})
        self.advance(run, 3)
        self.assertEqual(hp.total, base + 100)

    def test_actual_adaptive_shield_counts_only_nearby_enemies(self):
        for distance, friendly, expected in ((7.9, False, 175), (8.1, False, 100), (7.9, True, 100)):
            with self.subTest(distance=distance, friendly=friendly):
                run, owner, ball, _ = self.arena(distance=100)
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                self.assertTrue(match.switch_hero(ball, heroes()[torbjorn_charge.WRECKING_BALL]))
                ball.team = owner.team if friendly else 1 - owner.team
                x, y, z = owner.mover.position
                ball.mover.position = [x, y, z + distance]
                ball.mover.body = None
                self.advance(run, 35)
                hp = match.combat.vitals[ball.body]
                base = hp.total
                run.step({ball: (0, 0, 16)})
                self.advance(run, 3)
                self.assertEqual(hp.total, base + expected)
                if not friendly:
                    direction = aim(match.combat._eye(owner), (x, y + .8, z + distance))
                    run.step({owner: (*direction, 1)})
                    self.advance(run, 30)
                    self.assertEqual(hp.total, base + expected - 70)
                    self.assertEqual(self.charge(owner), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_actual_adaptive_shield_counts_each_npc_and_charges_only_rivet_spill(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, ball, _ = self.arena(distance=100)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                self.assertTrue(match.switch_hero(ball, heroes()[torbjorn_charge.WRECKING_BALL]))
                ball.team = 1 - owner.team
                x, y, z = owner.mover.position
                ball.mover.position = [x, y, z + 10]
                ball.mover.body = None
                npc.spawn(match, definition, (x + 7.9, y, z + 10), team=owner.team)
                npc.spawn(match, definition, (x - 7.9, y, z + 10), team=ball.team)
                self.advance(run, 35)
                hp = fight.vitals[ball.body]
                base = hp.total
                run.step({ball: (0, 0, 16)})
                self.advance(run, 3)
                self.assertEqual(hp.total, base + 175)
                direction = aim(fight._eye(owner), (x, y + .8, z + 10))
                for offset, charge in ((105, 0), (35, 0), (-30, 30)):
                    run.step({owner: (*direction, 1)})
                    self.advance(run, 48)
                    self.assertEqual(hp.total, base + offset)
                    self.assertEqual(self.charge(owner), charge)
                run.step({ball: (0, 0, 16)})
                self.advance(run, 20)
                self.assertEqual(hp.total, base - 30)

    def test_actual_rivet_deducts_adaptive_shield_loss_from_charge(self):
        for tag, amount, expected in (
            (torbjorn_charge.ADAPTIVE_SHIELD_TAG, 100, 0),
            (torbjorn_charge.ADAPTIVE_SHIELD_TAG, 50, 20),
            (sigma_grasp.SHIELD_TAG, 50, 70),
        ):
            with self.subTest(tag=tag, amount=amount):
                run, owner, _, hp = self.arena()
                self.assertEqual(hp.grant_temporary_shields(tag, amount, amount), amount)
                before = hp.total
                run.step({owner: (0, 0, 1)})
                self.advance(run, 40)
                self.assertEqual(before - hp.total, 70)
                self.assertEqual(hp.temporary_shields[tag].now, max(0, amount - 70))
                self.assertEqual(self.charge(owner), expected)

    def test_actual_secondary_melee_and_hammer_deduct_adaptive_shield_loss(self):
        for attack, button, damage in (("secondary", 2, 125), ("melee", 0x800, 30), ("hammer", 1, 55)):
            with self.subTest(attack=attack):
                run, owner, ball, _ = self.arena(distance=100)
                match = run.match
                self.assertTrue(match.switch_hero(ball, heroes()[torbjorn_charge.WRECKING_BALL]))
                x, y, z = owner.mover.position
                ball.mover.position = [x, y, z + 1.5]
                ball.mover.body = None
                self.advance(run, 35)
                if attack == "hammer":
                    self.select_hammer(run, owner)
                hp = match.combat.vitals[ball.body]
                base = hp.total
                run.step({ball: (0, 0, 16)})
                self.advance(run, 3)
                self.assertEqual(hp.total, base + 175)
                run.step({owner: (0, 0, button)})
                self.advance(run, 40, owner if attack == "hammer" else None,
                             button if attack == "hammer" else 0)
                self.assertEqual(hp.total, base + 175 - damage)
                self.assertEqual(self.charge(owner), 0)

    def test_miss_and_friendly_hits_give_no_charge(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, owner, victim, hp = self.arena()
                if friendly:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    victim.team = owner.team
                run.step({owner: (0 if friendly else 16384, 0, 1)})
                self.advance(run, 40)
                self.assertEqual(hp.total, 200)
                self.assertEqual(self.charge(owner), 0)

    def test_native_passive_timer_adds_five_per_one_second_wait(self):
        run, owner, _, _ = self.arena()
        owner.body_script.ult_seconds = 60.0
        self.advance(run, 1)
        owner.body_script.set_ult_charge(0.0)
        controller = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0xB8B)
        self.assertTrue(controller.states[1].active)
        self.advance(run, 62)
        self.assertEqual(self.charge(owner), 0)
        self.advance(run, 1)
        self.assertEqual(self.charge(owner), 5)
        self.advance(run, 63)
        self.assertEqual(self.charge(owner), 10)

    def test_same_hero_replacement_keeps_passive_charge_on_the_original_tick(self):
        for replacement in ("reselect", "skin", "respawn"):
            with self.subTest(replacement=replacement):
                control, original, _, _ = self.arena()
                original.body_script.ult_seconds = 60.0
                self.advance(control, 32)
                run, owner, enemy, _ = self.arena()
                owner.body_script.ult_seconds = 60.0
                self.advance(run, 32)
                self.assertEqual(self.charge(owner), 0)
                match = run.match
                old_body = owner.body
                if replacement == "reselect":
                    self.assertTrue(match.switch_hero(owner, owner.hero))
                elif replacement == "skin":
                    match.skin_of = lambda *_: (0, True)
                    self.assertTrue(match.skin_changed(owner, owner.hero.guid))
                else:
                    fight = match.combat
                    target = next(t for t in fight._targets() if t.owner is owner)
                    fight._damage(enemy, target, 1000, False, None, run.frame)
                    self.assertTrue(fight.is_dead(owner))
                    for _ in range(650):
                        if owner.body != old_body:
                            break
                        run.step({})
                        control.step({})
                        self.assertEqual(self.charge(owner), self.charge(original), run.frame)
                    self.assertFalse(fight.is_dead(owner))
                self.assertNotEqual(owner.body, old_body)
                before = self.charge(owner)
                for _ in range(126):
                    control.step({})
                    run.step({})
                    self.assertEqual(self.charge(owner), self.charge(original), run.frame)
                self.assertEqual(self.charge(owner) - before, 10)

    def test_assemble_countdown_gates_passive_and_actual_hit_charge_until_live(self):
        run, owner, _, hp = self.arena()
        match = run.match
        owner.body_script.ult_seconds = 60.0
        match.assemble_ends = match.combat.now + 2.5
        self.assertTrue(match.assembling())
        run.step({owner: (0, 0, 1)})
        self.advance(run, 120)
        self.assertTrue(match.assembling())
        self.assertEqual(hp.total, 130)
        self.assertEqual(self.charge(owner), 0)
        for _ in range(60):
            if not match.assembling():
                break
            run.step({})
        self.assertFalse(match.assembling())
        self.assertEqual(self.charge(owner), 0)
        self.advance(run, 64)
        self.assertEqual(self.charge(owner), 5)
        run.step({owner: (0, 0, 1)})
        self.advance(run, 25)
        self.assertEqual(hp.total, 60)
        self.assertEqual(self.charge(owner), 75)

    def test_full_molten_charge_cannot_cast_during_assembly_and_casts_afterward(self):
        run, owner, _, _ = self.arena(distance=100)
        match = run.match
        script = owner.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        match.assemble_ends = match.combat.now + 1.5
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 55)
        self.assertTrue(match.assembling())
        self.assertEqual(self.charge(owner), cost)
        self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        self.advance(run, 55)
        self.assertFalse(match.assembling())
        self.assertEqual(self.charge(owner), cost)
        run.step({owner: (0, 4000, 32)})
        self.assertEqual(self.charge(owner), 0)
        self.assertTrue(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 80)
        self.assertEqual(len(match.combat.molten_pools), 1)

    def test_held_molten_input_starts_only_on_first_live_frame(self):
        run, owner, _, _ = self.arena(distance=100)
        match = run.match
        script = owner.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        match.assemble_ends = match.combat.now + 1.5
        for _ in range(120):
            run.step({owner: (0, 4000, 32)})
            if not match.assembling():
                break
            self.assertEqual(self.charge(owner), cost)
            self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        self.assertFalse(match.assembling())
        self.assertEqual(self.charge(owner), 0)
        self.assertTrue(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 80)
        self.assertEqual(len(match.combat.molten_pools), 1)

    def test_released_assembly_press_is_not_buffered_into_live_molten_cast(self):
        run, owner, _, _ = self.arena(distance=100)
        match = run.match
        script = owner.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        match.assemble_ends = match.combat.now + .05
        run.step({owner: (0, 4000, 32)})
        self.assertTrue(match.assembling())
        run.step({})
        self.assertTrue(match.assembling())
        self.advance(run, 12)
        self.assertFalse(match.assembling())
        self.assertEqual(self.charge(owner), cost)
        self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        run.step({owner: (0, 4000, 32)})
        self.assertEqual(self.charge(owner), 0)

    def test_native_combat_and_passive_modifiers_are_not_bypassed(self):
        run, owner, _, hp = self.arena()
        component = owner.body_script.component
        component.set(9532, 2.0)
        component.set(13022, 1.5)
        run.step({owner: (0, 0, 1)})
        self.advance(run, 25)
        self.assertEqual(hp.total, 130)
        self.assertEqual(self.charge(owner), 210)
        component.set(12918, 3.0)
        owner.body_script.ult_seconds = 60.0
        self.advance(run, 64)
        self.assertEqual(self.charge(owner), 240)

    def test_native_permission_and_enabled_links_reject_charge(self):
        for variable in (2426, 9530):
            with self.subTest(variable=variable):
                run, owner, _, hp = self.arena()
                owner.body_script.component.set(variable, False)
                run.step({owner: (0, 0, 1)})
                self.advance(run, 25)
                self.assertEqual(hp.total, 130)
                self.assertEqual(self.charge(owner), 0)

    def test_native_cap_then_actual_damage_completes_charge_and_ultimate_starts(self):
        run, owner, _, hp = self.arena(distance=5)
        script = owner.body_script
        cost = script.component.entity_var(338).value()
        self.assertEqual(cost, 2142)
        script.set_ult_charge(cost - 10)
        run.step({owner: (0, 0, 1)})
        self.advance(run, 25)
        self.assertEqual(hp.total, 130)
        self.assertEqual(self.charge(owner), cost)
        run.step({owner: (0, 4000, 32)})
        self.assertEqual(self.charge(owner), 0)
        self.assertTrue(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 80)
        self.assertGreater(len(run.match.combat.molten_pools), 0)
        self.assertEqual(self.charge(owner), 0)

    def test_adaptive_shield_spill_caps_charge_and_allows_molten_on_impact_frame(self):
        run, owner, ball, _ = self.arena(distance=100)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(ball, heroes()[torbjorn_charge.WRECKING_BALL]))
        x, y, z = owner.mover.position
        ball.mover.position = [x, y, z + 100]
        ball.mover.body = None
        self.advance(run, 35)
        hp = fight.vitals[ball.body]
        base = hp.total
        run.step({ball: (0, 0, 16)})
        self.advance(run, 3)
        self.assertEqual(hp.total, base + 100)
        ball.mover.position = [x, y, z + 12]
        ball.mover.body = None
        self.advance(run, 3)
        script = owner.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost - 10)
        direction = aim(fight._eye(owner), (x, y + .8, z + 12))
        run.step({owner: (*direction, 1)})
        self.advance(run, 20)
        self.assertEqual(hp.total, base + 30)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 70)
        self.assertEqual(self.charge(owner), cost - 10)
        run.step({owner: (0, 4000, 32)})
        self.assertEqual(self.charge(owner), cost - 10)
        self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        self.advance(run, 35)

        run.step({owner: (*direction, 1)})
        self.assertEqual(hp.total, base + 30)
        self.assertTrue(fight.rockets)
        for _ in range(25):
            run.frame += 1
            match.update(run.frame * .016, run.frame)
            if hp.total < base + 30:
                break
            for player in run.players:
                player.take_commands([Command(run.frame)])
        impact = run.frame
        self.assertEqual(hp.total, base - 35)
        self.assertEqual(hp.temporary_shields[torbjorn_charge.ADAPTIVE_SHIELD_TAG].now, 0)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 135)
        self.assertEqual(self.charge(owner), cost)
        self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        owner.take_commands([Command(impact, pitch=4000, buttons=32)])
        self.assertEqual(self.charge(owner), 0)
        self.assertTrue(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        ball.mover.position = [x + 100, y, z + 100]
        ball.mover.body = None
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 80)
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, owner)
        self.assertEqual(self.charge(owner), 0)

    def test_owned_turret_hits_credit_its_owner(self):
        run, owner, enemy, _ = turret_tests.TorbjornTurretTests().arena()
        self.advance(run, 190)
        self.quiet(run, owner)
        hp = run.match.combat.vitals[enemy.body]
        before = hp.total
        self.advance(run, 80)
        self.assertGreater(before - hp.total, 0)
        self.assertEqual(self.charge(owner), before - hp.total)

    def test_turret_hit_and_passive_tick_jointly_enable_molten_on_the_same_frame(self):
        for press in ("buffered", "ready_frame", "following_frame"):
            with self.subTest(press=press):
                self.check_turret_and_passive_charge(press)

    def test_molten_buffer_boundary_before_turret_and_passive_charge_cap(self):
        for frames, press in ((8, "buffered"), (9, "expired")):
            with self.subTest(frames=frames):
                self.check_turret_and_passive_charge(press, early_frames=frames)

    def check_turret_and_passive_charge(self, press, *, early_frames=1):
        run, owner, victim, hp = self.arena(distance=100)
        match = run.match
        fight = match.combat
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 225)
        turret = fight.turrets[owner.entity]
        self.assertEqual(turret.shots, 0)
        script = owner.body_script
        script.ult_seconds = 60.0
        self.advance(run, 1)
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost - 18)
        cap_frame = run.frame + 63
        if press in ("buffered", "expired"):
            self.advance(run, cap_frame - early_frames - run.frame - 1)
            run.step({owner: (0, 4000, 32)})
            self.assertEqual(run.frame, cap_frame - early_frames)
            self.assertEqual(self.charge(owner), cost - 18)
            self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        self.advance(run, cap_frame - run.frame - 1)
        self.assertEqual(self.charge(owner), cost - 18)
        self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))

        x, y, z = turret.actor.position
        victim.mover.position = [x, y, z + 8]
        victim.mover.body = None
        run.frame += 1
        match.update(run.frame * .016, run.frame)
        self.assertEqual(turret.shots, 1)
        self.assertEqual(hp.total, 186)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 14)
        self.assertEqual(self.charge(owner), cost - 4)
        owner.take_commands([Command(run.frame, pitch=4000,
                                     buttons=32 if press == "ready_frame" else 0)])
        needs_fresh_press = press in ("expired", "following_frame")
        if needs_fresh_press:
            self.assertEqual(self.charge(owner), cost)
            self.assertFalse(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        victim.mover.position = [x + 100, y, z + 100]
        victim.mover.body = None
        if needs_fresh_press:
            run.step({owner: (0, 4000, 32)})
        self.assertEqual(self.charge(owner), 0)
        self.assertTrue(any(state.flags & nodes.Ability.RUNNING for state in script.ultimates))
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 45)
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, owner)
        self.assertIs(fight.turrets[owner.entity], turret)
        self.assertEqual(turret.shots, 1)

    def test_turret_and_passive_charge_resume_after_molten_weapon_lock(self):
        run, owner, victim, pool, hp = ultimate_tests.area_tests.TorbjornMoltenTests().arena()
        fight = run.match.combat
        ultimate_tests.molten_core.end_pool(fight, pool)
        x, y, z = owner.mover.position
        distant = [x + 100, y, z + 100]
        victim.mover.position = distant.copy()
        victim.mover.body = None
        self.advance(run, 1, owner, 8, pitch=4000)
        self.advance(run, 240)
        turret = fight.turrets[owner.entity]
        self.assertEqual(turret.shots, 0)
        owner.body_script.set_ult_charge(10000)
        self.advance(run, 1, owner, 32)
        self.assertEqual(self.charge(owner), 0)
        x, y, z = turret.actor.position
        nearby = [x, y, z + 8]
        victim.mover.position = nearby.copy()
        victim.mover.body = None
        self.advance(run, 32)
        self.assertEqual(turret.shots, 3)
        self.assertEqual(hp.total, 158)
        self.assertEqual(self.charge(owner), 42)
        self.advance(run, 4)
        self.assertEqual(turret.shots, 3)
        self.assertEqual(self.charge(owner), 47)
        self.advance(run, 84)
        self.assertEqual(turret.shots, 8)
        self.assertEqual(hp.total, 88)
        self.assertEqual(self.charge(owner), 47)
        victim.mover.position = distant.copy()
        victim.mover.body = None
        self.advance(run, 291)
        self.assertEqual(turret.shots, 8)
        self.assertEqual(self.charge(owner), 47)
        victim.mover.position = nearby
        victim.mover.body = None
        self.advance(run, 1)
        self.assertEqual(turret.shots, 9)
        self.assertEqual(hp.total, 74)
        self.assertEqual(self.charge(owner), 61)
        victim.mover.position = distant
        victim.mover.body = None
        self.advance(run, 40)
        self.assertEqual(self.charge(owner), 61)
        self.advance(run, 1)
        self.assertEqual(turret.shots, 9)
        self.assertEqual(self.charge(owner), 66)

    def test_actual_turret_awards_only_base_armour_loss_after_adaptive_shield(self):
        run, owner, ball, _ = self.arena(distance=100)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(ball, heroes()[torbjorn_charge.WRECKING_BALL]))
        x, y, z = owner.mover.position
        ball.mover.position = [x, y, z + 100]
        ball.mover.body = None
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 240)
        turret = fight.turrets[owner.entity]
        self.assertEqual(turret.shots, 0)
        hp = fight.vitals[ball.body]
        base = hp.total
        run.step({ball: (0, 0, 16)})
        self.advance(run, 3)
        self.assertEqual(hp.total, base + 100)
        tx, ty, tz = turret.actor.position
        ball.mover.position = [tx + 4, ty, tz]
        ball.mover.body = None
        for _ in range(160):
            run.step({})
            if turret.shots == 8:
                break
            self.assertEqual(self.charge(owner), 0)
        self.assertEqual(turret.shots, 8)
        self.assertEqual(hp.total, base - 7)
        self.assertEqual(self.charge(owner), 7)

    def test_actual_molten_pool_spends_adaptive_shield_without_awarding_charge(self):
        run, owner, ball, _ = self.arena(distance=100)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(ball, heroes()[torbjorn_charge.WRECKING_BALL]))
        x, y, z = owner.mover.position
        ball.mover.position = [x, y, z + 100]
        ball.mover.body = None
        self.advance(run, 35)
        hp = fight.vitals[ball.body]
        base = hp.total
        run.step({ball: (0, 0, 16)})
        self.advance(run, 3)
        self.assertEqual(hp.total, base + 100)
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        ball.mover.position = [x, y, z + 3]
        ball.mover.body = None
        self.advance(run, 3)
        direction = aim(fight._eye(owner), (x, y + .8, z + 3))
        run.step({owner: (*direction, 1)})
        self.advance(run, 45)
        self.assertEqual(hp.total, base + 75)
        self.assertEqual(self.charge(owner), 0)
        self.assertFalse(fight.molten_pools)
        ball.mover.position = [x, y, z + 100]
        ball.mover.body = None
        self.advance(run, 3)
        run.step({owner: (0, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        ball.mover.position = list(pool.position)
        ball.mover.body = None
        self.advance(run, 8)
        self.assertAlmostEqual(hp.total, base + 43)
        self.assertAlmostEqual(hp.temporary_shields[torbjorn_charge.ADAPTIVE_SHIELD_TAG].now, 43)
        self.assertEqual(self.charge(owner), 0)

    def test_damage_to_deployable_and_repair_do_not_charge(self):
        for repairing in (False, True):
            with self.subTest(repairing=repairing):
                run, owner, victim, _ = self.arena(100)
                run.match.switch_hero(victim, heroes()[hero_torbjorn.HERO])
                if repairing:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    victim.team = owner.team
                x, y, z = owner.mover.position
                turret = hero_torbjorn.deploy_turret(run.match.combat, owner if repairing else victim,
                                                   (x, y, z + 1.5))
                self.advance(run, 190)
                hp = run.match.combat.vitals[turret.actor.entity]
                if repairing:
                    hp.damage(80)
                    run.match.combat._sync_body_health(turret.actor, hp)
                    self.select_hammer(run, owner)
                before = hp.total
                yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 0.9, z + 1.5))
                run.step({owner: (yaw, pitch, 1)})
                self.advance(run, 40, owner if repairing else None, 1 if repairing else 0)
                self.assertEqual(hp.total - before, 50 if repairing else -70)
                self.assertEqual(self.charge(owner), 0)

    def test_ultimate_direct_hit_and_periodic_pool_damage_do_not_charge(self):
        for direct in (False, True):
            with self.subTest(direct=direct):
                run, owner, victim, hp = ultimate_tests.TorbjornUltimateTests().arena()
                self.quiet(run, owner)
                before = hp.total
                yaw, pitch = (0, 4000)
                if direct:
                    x, y, z = victim.mover.position
                    yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 1, z))
                run.step({owner: (yaw, pitch, 1)})
                self.advance(run, 40)
                if direct:
                    self.assertEqual(before - hp.total, 25)
                else:
                    pool = next(iter(run.match.combat.molten_pools.values()))
                    victim.mover.position = list(pool.position)
                    self.advance(run, 40)
                    self.assertLess(hp.total, before)
                self.assertEqual(self.charge(owner), 0)

    def test_airborne_molten_direct_hit_after_weapon_recovery_does_not_charge(self):
        self.check_post_recovery_direct_hit()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_airborne_molten_direct_hit_after_recovery_against_each_npc(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                self.check_post_recovery_direct_hit(definition)

    def check_post_recovery_direct_hit(self, definition=None):
        run, owner, victim, hp = ultimate_tests.TorbjornUltimateTests().arena(ready=False)
        match = run.match
        fight = match.combat
        activated_at = run.frame
        self.quiet(run, owner)
        x, y, z = owner.mover.position
        victim.mover.position = [x, y, z + 18] if definition is None else [x + 100, y, z + 100]
        victim.mover.body = None
        if definition is None:
            direction = aim(fight._eye(owner), (x, y + .8, z + 18))
        else:
            match.game_map = replace(match.game_map, free_for_all=False)
            bot = npc.spawn(match, definition, (x, y, z + 18), team=1 - owner.team)
            self.assertIsNotNone(bot.body_script)
            hp = fight.vitals[bot.entity]
            direction = npc_tests.TorbjornNpcTests.body_aim(run, owner, bot)
        before = hp.total
        self.advance(run, 385 - (run.frame - activated_at))
        run.step({owner: (0, -1800, 1)})
        flight, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
        self.advance(run, 412 - (run.frame - activated_at))
        self.assertIn(flight, fight.rockets)
        self.assertEqual(hp.total, before)
        self.advance(run, 8)
        self.assertNotIn(flight, fight.rockets)
        self.assertFalse(fight.molten_pools)
        self.assertEqual(hp.total, before - 25)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 25)
        self.assertEqual(self.charge(owner), 0)
        run.step({owner: (*direction, 1)})
        self.advance(run, 25)
        self.assertEqual(hp.total, before - 95)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 95)
        self.assertEqual(self.charge(owner), 70)

    def test_landed_molten_keeps_damaging_without_charge_after_weapon_recovery(self):
        run, owner, victim, hp = ultimate_tests.TorbjornUltimateTests().arena(ready=False)
        fight = run.match.combat
        activated_at = run.frame
        self.quiet(run, owner)
        original = list(victim.mover.position)
        self.advance(run, 350 - (run.frame - activated_at))
        run.step({owner: (16384, 4000, 1)})
        self.advance(run, 412 - (run.frame - activated_at))
        pool, = fight.molten_pools.values()
        self.assertEqual(hp.total, 200)
        self.assertEqual(self.charge(owner), 0)
        victim.mover.position = list(pool.position)
        victim.mover.body = None
        self.advance(run, 30)
        self.assertGreater(hp.total, 70)
        self.assertLess(hp.total, 200)
        self.assertIn((owner.entity, victim.body), fight.molten_burns)
        self.assertEqual(self.charge(owner), 0)
        victim.mover.position = original
        victim.mover.body = None
        self.advance(run, 2)
        self.assertNotIn((owner.entity, victim.body), fight.molten_burns)
        before = hp.total
        x, y, z = original
        direction = aim(fight._eye(owner), (x, y + .8, z))
        run.step({owner: (*direction, 1)})
        self.advance(run, 20)
        self.assertAlmostEqual(before - hp.total, 70)
        self.assertEqual(self.charge(owner), 70)
        self.assertIn(pool.entity, fight.molten_pools)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_post_molten_pool_and_rivet_charge_separate_against_each_npc(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, other, _ = ultimate_tests.TorbjornUltimateTests().arena(ready=False)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                activated_at = run.frame
                self.quiet(run, owner)
                ox, oy, oz = owner.mover.position
                other.mover.position = [ox + 100, oy, oz + 100]
                other.mover.body = None
                self.advance(run, 350 - (run.frame - activated_at))
                run.step({owner: (16384, 4000, 1)})
                self.advance(run, 412 - (run.frame - activated_at))
                pool, = fight.molten_pools.values()
                x, y, z = pool.position
                bot = npc.spawn(match, definition, (x, y, z), team=1 - owner.team)
                hp = fight.vitals[bot.entity]
                self.assertIsNotNone(bot.body_script)
                before = hp.total
                self.advance(run, 8)
                self.assertAlmostEqual(before - hp.total, 20.48)
                self.assertEqual(self.charge(owner), 0)
                key = (owner.entity, bot.entity)
                self.assertIn(key, fight.molten_burns)
                bot.position = (ox, oy, oz + 5)
                self.advance(run, 2)
                self.assertNotIn(key, fight.molten_burns)
                before = hp.total
                yaw, pitch = npc_tests.TorbjornNpcTests.body_aim(run, owner, bot)
                run.step({owner: (yaw, pitch, 1)})
                self.advance(run, 20)
                self.assertAlmostEqual(before - hp.total, 70)
                self.assertEqual(self.charge(owner), 70)
                self.assertIn(pool.entity, fight.molten_pools)

    def test_overkill_dead_target_and_incoming_damage_never_overcredit(self):
        run, owner, victim, hp = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        fight._damage(owner, target, 1000, False, None, run.frame)
        self.assertEqual(hp.total, 0)
        self.assertEqual(self.charge(owner), 200)
        fight._damage(owner, target, 1000, False, None, run.frame)
        self.assertEqual(self.charge(owner), 200)
        own = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(victim, own, 20, False, None, run.frame)
        self.assertEqual(self.charge(owner), 200)

    def test_respawn_retains_unspent_charge_and_never_refunds_spent_ultimate(self):
        for spent in (False, True):
            with self.subTest(spent=spent):
                run, owner, victim, _ = self.arena(distance=5)
                script = owner.body_script
                script.set_ult_charge(script.component.entity_var(338).value() if spent else 123.0)
                if spent:
                    run.step({owner: (0, 4000, 32)})
                    self.assertEqual(self.charge(owner), 0)
                fight = run.match.combat
                old_body = owner.body
                target = next(t for t in fight._targets() if t.owner is owner)
                fight._damage(victim, target, 1000, False, None, run.frame)
                self.assertTrue(fight.is_dead(owner))
                for _ in range(650):
                    run.step({})
                    if owner.body != old_body:
                        break
                self.assertNotEqual(owner.body, old_body)
                self.assertFalse(fight.is_dead(owner))
                self.assertEqual(self.charge(owner), 0 if spent else 123)

    def test_reselect_and_skin_replacement_keep_earned_charge_without_refunding_a_cast(self):
        for skin_change in (False, True):
            with self.subTest(skin_change=skin_change):
                run, owner, _, hp = self.arena()
                match = run.match
                run.step({owner: (0, 0, 1)})
                self.advance(run, 40)
                self.assertEqual(hp.total, 130)
                self.assertEqual(self.charge(owner), 70)
                for expected in (70, 0):
                    old_body = owner.body
                    if skin_change:
                        skin = (0, not owner.skin[1])
                        match.skin_of = lambda *_args, value=skin: value
                        self.assertTrue(match.skin_changed(owner, owner.hero.guid))
                    else:
                        self.assertTrue(match.switch_hero(owner, owner.hero))
                    self.assertNotEqual(owner.body, old_body)
                    self.assertEqual(self.charge(owner), expected)
                    if expected:
                        script = owner.body_script
                        script.set_ult_charge(script.component.entity_var(338).value())
                        run.step({owner: (0, 4000, 32)})
                        self.assertEqual(self.charge(owner), 0)

    def test_changing_hero_does_not_transfer_charge(self):
        from ow174.game.content import SOLDIER

        run, owner, _, _ = self.arena()
        owner.body_script.set_ult_charge(123.0)
        run.match.switch_hero(owner, heroes()[SOLDIER])
        self.assertEqual(self.charge(owner), 0)

    def test_native_passive_timer_keeps_running_while_waiting_to_respawn(self):
        run, owner, victim, _ = self.arena()
        owner.body_script.ult_seconds = 60.0
        self.advance(run, 1)
        owner.body_script.set_ult_charge(123.0)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(victim, target, 1000, False, None, run.frame)
        self.advance(run, 126)
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(self.charge(owner), 133.0)
