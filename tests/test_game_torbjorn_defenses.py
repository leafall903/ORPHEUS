"""Actual Torbjorn attacks against authored Genji/Sigma defensive states."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import test_game_torbjorn_phases as phase_tests
import test_game_torbjorn_turret as turret_tests
from test_game_combat import in_world
from test_game_mover import box

from ow174.game import (
    combat,
    hero_torbjorn,
    molten_core,
    npc,
    npc_control,
    projectile_defenses,
    sigma_grasp,
    stats,
    world,
)
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE")
                     and os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "needs prepared graphs and tags")
class TorbjornDefenseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        turret_tests.TorbjornTurretTests.setUpClass()
        cls.old_tags = projectile_defenses.TAGS_PATH
        projectile_defenses.TAGS_PATH = Path(os.environ["ORPHEUS_PROJECTILE_TAGS"])
        projectile_defenses.tags.cache_clear()

    @classmethod
    def tearDownClass(cls):
        projectile_defenses.TAGS_PATH = cls.old_tags
        projectile_defenses.tags.cache_clear()
        turret_tests.TorbjornTurretTests.tearDownClass()

    health = staticmethod(phase_tests.TorbjornPhaseTests.health)
    body_aim = staticmethod(phase_tests.TorbjornPhaseTests.body_aim)

    @staticmethod
    def place(player, position):
        player.mover.position = list(position)
        player.mover.body = None  # rebuild the physics capsule at the explicit test pose

    def arena(self, name, distance=3):
        run, owner, defender, behind = phase_tests.TorbjornPhaseTests().arena(
            name, distance=distance, behind_distance=30)
        return run, owner, defender, behind

    def step(self, run, owner, defender, *, fire=0, defense=0, defender_aim=None):
        yaw, pitch = self.body_aim(run, owner, defender)
        dyaw, dpitch = defender_aim or self.body_aim(run, defender, owner)
        run.step({owner: (yaw, pitch, fire), defender: (dyaw, dpitch, defense)})

    def advance(self, run, owner, defender, ticks, **kwargs):
        for _ in range(ticks):
            self.step(run, owner, defender, **kwargs)

    def cast(self, run, owner, defender, **kwargs):
        self.step(run, owner, defender, defense=16 if defender.hero.name == "Genji" else 8, **kwargs)
        self.advance(run, owner, defender, 2, **kwargs)
        self.assertTrue(any(
            state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"
            for instance in defender.body_script.component.instances.values()
            for state in instance.states.values()))

    def ranged_talon_at_overload(self, defender_name, definition):
        run, torb, defender, other = self.arena(defender_name)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        defender.team = torb.team
        x, y, z = torb.mover.position
        self.place(other, (x + 100, y, z + 100))
        self.place(defender, (x + 8, y, z))
        bot = npc.spawn(match, definition, (x, y, z + 5), team=1 - torb.team)
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})
        run.step({torb: (*self.body_aim(run, torb, bot), 16)})
        self.assertEqual(npc_control.start(match, torb), bot.entity)
        if definition.name == "Sniper":
            self.advance(run, torb, defender, 50)
        self.place(defender, (x, y, z + 1.25))
        return run, torb, defender, bot

    def test_actual_rivet_and_ten_pellets_reflect_to_new_owner_enemy(self):
        for button, loss in ((1, 65), (2, 90)):
            with self.subTest(button=button):
                run, owner, defender, behind = self.arena("Genji")
                hp = self.health(run, owner)
                before = self.health(run, defender).total
                self.cast(run, owner, defender)
                self.step(run, owner, defender, fire=button)
                self.advance(run, owner, defender, 20)
                self.assertAlmostEqual(hp.total, 250 - loss)
                self.assertEqual(self.health(run, defender).total, before)
                self.assertEqual(self.health(run, behind).total, 200)
                self.assertAlmostEqual(defender.stats.value(stats.HERO_DAMAGE), loss)
                self.assertEqual(defender.body_script.component.entity_var(342).value(), loss)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(run.match.combat.rockets, [])

    def test_deflect_entered_after_launch_uses_facing_and_transfers_rivet_credit(self):
        for button, ammo_cost, count, raw in ((1, 1, 1, 70), (2, 2, 10, 125)):
            for frontal in (False, True):
                with self.subTest(button=button, frontal=frontal):
                    run, owner, genji, behind = self.arena("Genji", distance=4)
                    fight = run.match.combat
                    owner_hp = self.health(run, owner)
                    genji_hp = self.health(run, genji)
                    base = dict(owner_hp.now)
                    self.step(run, owner, genji, fire=16)
                    self.advance(run, owner, genji, 60)
                    source = owner_hp.over_armour.source
                    self.assertEqual(owner_hp.over_armour.amount, 100)
                    rifle = next(i for i in owner.body_script.component.instances.values()
                                 if i.graph.index == 0x1683)
                    self.step(run, owner, genji, fire=button)
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), count)
                    self.assertTrue(all(shot.shooter is owner and shot.distance == 0 for shot in volley))
                    direction = self.body_aim(run, genji, owner) if frontal else (0, 0)
                    self.step(run, owner, genji, defense=16, defender_aim=direction)
                    self.assertTrue(any(
                        state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"
                        for instance in genji.body_script.component.instances.values()
                        for state in instance.states.values()
                    ))
                    self.assertEqual(tuple(fight.rockets), volley)
                    self.assertEqual(genji_hp.total, 200)
                    reflected = {}
                    for _ in range(30):
                        self.step(run, owner, genji, defender_aim=direction)
                        reflected.update((id(shot), shot) for shot in fight.rockets if shot.shooter is genji)
                    self.assertTrue(all(shot.team == genji.team for shot in reflected.values()))
                    damage = (65 if button == 1 else 75) if frontal else 0
                    original_credit = 0 if frontal else raw
                    self.assertEqual(len(reflected), count if frontal else 0)
                    self.assertEqual(owner_hp.over_armour.source, source)
                    self.assertEqual(owner_hp.over_armour.amount, 100 - damage)
                    self.assertEqual(owner_hp.now, base)
                    self.assertEqual(genji_hp.total, 200 - original_credit)
                    self.assertEqual(self.health(run, behind).total, 200)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
                    self.assertEqual(genji.body_script.component.entity_var(342).value(), damage)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), original_credit)
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), original_credit)
                    self.assertEqual(rifle.vars[53].value(), 18 - ammo_cost)
                    marks = {(owner.entity, genji.body)} if button == 1 and not frontal else set()
                    self.assertEqual(set(fight.torbjorn_marks), marks)

    def test_secondary_splits_at_deflect_edge_and_credits_both_attackers(self):
        run, owner, genji, protected = self.arena("Genji")
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        zarya = match.add_player(8, "Zarya", zarya_hero.guid, 1 - owner.team, False)
        in_world(match, zarya)
        run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        genji.team = protected.team = zarya.team = 1 - owner.team
        x, y, z = owner.mover.position
        self.place(genji, (x + .80, y, z + 2.9))
        self.place(protected, (x, y, z + 4))
        self.place(zarya, (x - 4, y, z + 4))
        run.step({owner: (0, 0, 16)})
        self.advance(run, owner, protected, 55)
        owner_hp = self.health(run, owner)
        self.assertEqual(owner_hp.over_armour.amount, 100)
        owner_base = dict(owner_hp.now)
        genji_hp = self.health(run, genji)
        protected_hp = self.health(run, protected)
        run.step({genji: (-32768, 0, 16), zarya: (*self.body_aim(run, zarya, protected), 16)})
        pose = {genji: (-32768, 0, 0)}
        run.step(pose)
        run.step(pose)
        bubble = fight.bubbles.for_target(protected)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = 5
        for player in (owner, genji):
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        gate = projectile_defenses.projectile_gate
        caught = []

        def record(*args, **kwargs):
            hit = gate(*args, **kwargs)
            if hit is not None:
                self.assertIs(hit[1].owner, genji)
                self.assertEqual(hit[2], 0)
                caught.append(hit)
            return hit

        with (
            mock.patch.object(projectile_defenses, "projectile_gate", side_effect=record),
            mock.patch.object(fight, "_shield_damage", wraps=fight._shield_damage) as shield_hits,
            mock.patch.object(fight, "_damage", wraps=fight._damage) as body_hits,
        ):
            run.step({**pose, owner: (*self.body_aim(run, owner, protected), 2)})
            self.assertEqual(len(fight.rockets), 10)
            for _ in range(16):
                run.step(pose)
        self.assertEqual(len(caught), 4)
        self.assertEqual([call.args[1:] for call in shield_hits.call_args_list], [(bubble, 12.5)])
        self.assertIsNone(fight.bubbles.for_target(protected))
        returns = [call for call in body_hits.call_args_list if call.args[0] is genji]
        outgoing = [call for call in body_hits.call_args_list if call.args[0] is owner]
        self.assertEqual([call.args[2] for call in returns], [12.5] * 4)
        self.assertTrue(all(call.args[1].owner is owner for call in returns))
        self.assertEqual([call.args[2] for call in outgoing], [12.5] * 5)
        self.assertTrue(all(call.args[1].owner is protected for call in outgoing))
        self.assertEqual(owner_hp.over_armour.amount, 70)
        self.assertEqual(owner_hp.now, owner_base)
        self.assertEqual(genji_hp.total, 200)
        self.assertEqual(protected_hp.total, 137.5)
        for player, damage in ((owner, 62.5), (genji, 30)):
            self.assertEqual(player.stats.value(stats.HERO_DAMAGE), damage)
            self.assertEqual(player.body_script.component.entity_var(342).value(), damage)
        self.assertFalse(fight.rockets or fight.torbjorn_marks)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs the five-NPC catalog")
    def test_genji_reflects_talon_ranged_fire_away_from_overload(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        cases = (("Trooper", 20, 15), ("Sniper", 100, 95), ("Heavy Assault", 5, 2.5), ("Enforcer", 50, 25))
        for name, returned, direct in cases:
            with self.subTest(npc=name):
                definition = next(row for row in catalog.values() if row.name == name)
                run, torb, genji, bot = self.ranged_talon_at_overload("Genji", definition)
                fight = run.match.combat
                x, y, z = torb.mover.position
                torb_hp = self.health(run, torb)
                base = dict(torb_hp.now)
                source = torb_hp.over_armour.source
                self.assertIsNotNone(source)
                genji_hp = self.health(run, genji)
                bot_hp = fight.vitals[bot.entity]
                before = bot_hp.total
                direction = self.body_aim(run, genji, bot)
                self.cast(run, torb, genji, defender_aim=direction)
                for _ in range(120):
                    if bot_hp.total < before or torb_hp.total < 350 or genji_hp.total < 200:
                        break
                    run.step({genji: (*direction, 0)})
                self.assertEqual(torb_hp.total, 350)
                self.assertEqual(genji_hp.total, 200)
                self.assertEqual(before - bot_hp.total, returned)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), returned)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), returned)
                hits = genji.client.messages(combat.HIT)
                self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == bot.entity for hit in hits))
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits],
                                 [2] * 25 if name == "Enforcer" else [returned])
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)

                self.place(genji, (x + 8, y, z))
                for _ in range(150):
                    if torb_hp.total < 350:
                        break
                    run.step({})
                self.assertEqual(torb_hp.over_armour.source, source)
                self.assertEqual(torb_hp.over_armour.amount, 100 - direct)
                self.assertEqual(torb_hp.now, base)
                self.assertEqual(before - bot_hp.total, returned)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), returned)

    def test_reflected_primary_and_secondary_spend_overload_before_base_pools(self):
        for button, damage in ((1, 65), (2, 75)):
            with self.subTest(button=button):
                run, owner, genji, behind = self.arena("Genji")
                hp = self.health(run, owner)
                base = dict(hp.now)
                self.step(run, owner, genji, fire=16)
                self.advance(run, owner, genji, 60)
                source = hp.over_armour.source
                self.assertEqual(hp.over_armour.amount, 100)
                self.cast(run, owner, genji)
                for _ in range(24):
                    self.step(run, owner, genji, fire=button)
                    if run.match.combat.rockets or hp.over_armour.amount < 100:
                        break
                self.advance(run, owner, genji, 20)
                self.assertEqual(hp.over_armour.source, source)
                self.assertEqual(hp.over_armour.amount, 100 - damage)
                self.assertEqual(hp.now, base)
                self.assertEqual(self.health(run, genji).total, 200)
                self.assertEqual(self.health(run, behind).total, 200)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), damage)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_reflected_rivets_break_allied_projected_barrier_before_spending_overload(self):
        run, torb, genji, zarya = self.arena("Genji")
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        zarya.team = torb.team
        genji.team = 1 - torb.team
        x, y, z = torb.mover.position
        self.place(zarya, (x - 5, y, z))
        hp = self.health(run, torb)
        base = dict(hp.now)
        run.step({torb: (0, 0, 16)})
        self.advance(run, torb, genji, 45)
        source = hp.over_armour.source
        run.step({zarya: (*self.body_aim(run, zarya, torb), 16)})
        bubble = fight.bubbles.for_target(torb)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, zarya)
        self.assertEqual(bubble.health, 200)
        self.assertEqual(hp.over_armour.amount, 100)
        self.cast(run, torb, genji)
        weapon = next(i for i in torb.body_script.component.instances.values() if i.graph.index == 0x1683)
        ammo = weapon.vars[53]
        energy = zarya.body_script.component.entity_var(87)
        for remaining, spent in ((130, 1), (60, 2), (0, 3)):
            for _ in range(40):
                if bubble.health == remaining:
                    break
                self.step(run, torb, genji, fire=1)
            self.assertEqual(bubble.health, remaining)
            self.assertEqual(ammo.value(), 18 - spent)
            self.assertEqual(hp.over_armour.amount, 100)
            self.assertEqual(hp.now, base)
            self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
            self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
            self.assertTrue(0 < energy.value() <= (200 - remaining) * .2)
        self.assertIsNone(fight.bubbles.for_target(torb))
        energy_after_break = energy.value()
        for _ in range(40):
            if hp.over_armour.amount < 100:
                break
            self.step(run, torb, genji, fire=1)
        self.assertEqual(ammo.value(), 14)
        self.assertEqual(hp.over_armour.amount, 35)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 65)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 65)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertLessEqual(energy.value(), energy_after_break)
        self.assertFalse(fight.torbjorn_marks)

    def test_particle_beam_reaches_overload_through_allied_deflect_and_grasp(self):
        for hero, mode in (("Genji", 0), ("Sigma", 1)):
            with self.subTest(defense=hero):
                self.check_particle_beam_through_projectile_defense(hero, mode)

    def check_particle_beam_through_projectile_defense(self, hero, mode):
        run, torb, protector, zarya = self.arena(hero)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya_hero = next(row for row in heroes().values() if row.name == "Zarya")
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        protector.team = torb.team
        zarya.team = 1 - torb.team
        x, y, z = torb.mover.position
        self.place(zarya, (x, y, z + 5))
        offset = .7 if hero == "Genji" else 1.25
        self.place(protector, (x - offset, y, z + 1.25))
        hp = self.health(run, torb)
        protector_hp = self.health(run, protector)
        protector_before = protector_hp.total
        base = dict(hp.now)
        run.step({torb: (0, 0, 16)})
        source = hp.over_armour.source
        toward_zarya = self.body_aim(run, protector, zarya)
        self.cast(run, torb, protector, defender_aim=toward_zarya)
        beam_aim = self.body_aim(run, zarya, torb)
        # Confirm the same lane crosses the authored projectile defense volume.
        gate = projectile_defenses.projectile_gate(
            fight._eye(zarya), combat.aim(*beam_aim), 5, fight._enemies(zarya),
            0x0400000000002A66, collision=match.collision_world,
        )
        self.assertIsNotNone(gate)
        self.assertIs(gate[1].owner, protector)
        self.assertEqual(gate[2], mode)

        for _ in range(25):
            run.step({zarya: (*beam_aim, 1), protector: (*toward_zarya, 0)})
            self.assertTrue(gate[4].active)
        self.assertAlmostEqual(hp.over_armour.amount, 79.0525)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 20.9475)
        self.assertEqual(protector_hp.total, protector_before)
        self.assertEqual(protector.stats.value(stats.HERO_DAMAGE), 0)
        self.advance(run, torb, protector, 145, defender_aim=toward_zarya)
        self.assertNotIn(sigma_grasp.SHIELD_TAG, protector_hp.temporary_shields)

    def test_reflected_ana_rifle_wounds_overload_and_credits_genji(self):
        ana_hero = next(hero for hero in heroes().values() if hero.name == "Ana")
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                run, torb, genji, ana = self.arena("Genji", distance=7)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                ana.team = torb.team = 1 - genji.team
                self.assertTrue(match.switch_hero(ana, ana_hero))
                x, y, z = torb.mover.position
                self.place(ana, (x + 1.8, y, z))
                torb_hp = self.health(run, torb)
                base = dict(torb_hp.now)
                run.step({torb: (0, 0, 16)})
                scope = 2 if scoped else 0
                self.advance(run, ana, genji, 40, fire=scope)
                self.assertEqual(torb_hp.total, 350)
                genji_aim = self.body_aim(run, genji, torb)
                genji_hp = self.health(run, genji).total
                ana_hp = self.health(run, ana).total
                self.cast(run, ana, genji, fire=scope, defender_aim=genji_aim)
                self.step(run, ana, genji, fire=scope | 1, defender_aim=genji_aim)
                for _ in range(35):
                    if fight.rifle_wounds.active:
                        break
                    self.step(run, ana, genji, fire=scope, defender_aim=genji_aim)
                wound, = fight.rifle_wounds.active
                self.assertIs(wound.source, genji)
                self.assertEqual(wound.source_body, genji.body)
                self.assertEqual(wound.target.entity, torb.body)
                self.assertEqual(wound.amount, 70)
                self.advance(run, ana, genji, 12, fire=scope, defender_aim=genji_aim)
                self.assertGreater(torb_hp.over_armour.amount, 30)
                self.assertLess(torb_hp.over_armour.amount, 100)
                self.advance(run, ana, genji, 35, fire=scope, defender_aim=genji_aim)
                self.assertFalse(fight.rifle_wounds.active)
                self.assertAlmostEqual(torb_hp.over_armour.amount, 30)
                self.assertEqual(torb_hp.now, base)
                self.assertEqual(self.health(run, genji).total, genji_hp)
                self.assertEqual(self.health(run, ana).total, ana_hp)
                self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 70, places=4)
                self.assertAlmostEqual(genji.body_script.component.entity_var(342).value(), 70, places=4)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_genji_blocks_front_melee_and_hammer_until_deflect_ends(self):
        for hammer in (False, True):
            for facing in (False, True):
                with self.subTest(hammer=hammer, facing=facing):
                    run, owner, defender, _ = self.arena("Genji", distance=1.5)
                    if hammer:
                        run.frame += 1
                        owner.take_commands([Command(run.frame, action=2)])
                        self.advance(run, owner, defender, 45)
                    dyaw, dpitch = self.body_aim(run, defender, owner)
                    if not facing:
                        dyaw = (dyaw + 32768 + 32768) % 65536 - 32768
                        dpitch = -dpitch
                    pose = {"defender_aim": (dyaw, dpitch)}
                    self.cast(run, owner, defender, **pose)
                    hp = self.health(run, defender)
                    before = hp.total
                    button = 1 if hammer else 0x800
                    damage = 55 if hammer else 30
                    self.step(run, owner, defender, fire=button, **pose)
                    if hammer and facing:
                        self.advance(run, owner, defender, 23, **pose)
                        contact = run.match.combat.hammer_contacts[owner.body]
                        self.assertIn(defender.body, contact.hit)
                        self.assertLess(owner.body_script.component.now, contact.end)
                        rear = ((dyaw + 65536) % 65536 - 32768, -dpitch)
                        self.advance(run, owner, defender, 12, defender_aim=rear)
                    else:
                        self.advance(run, owner, defender, 35, **pose)
                    self.assertEqual(hp.total, before if facing else before - damage)
                    self.assertEqual(self.health(run, owner).total, 250)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0 if facing else damage)
                    self.advance(run, owner, defender, 120, **pose)
                    before = hp.total
                    self.step(run, owner, defender, fire=button, **pose)
                    self.advance(run, owner, defender, 35, **pose)
                    self.assertEqual(hp.total, before - damage)

    def test_grasp_allows_front_quick_melee_and_hammer_without_granting_shields(self):
        for hammer, button, damage in ((False, 0x800, 30), (True, 1, 55)):
            with self.subTest(hammer=hammer):
                run, owner, sigma, _ = self.arena("Sigma", distance=1.5)
                if hammer:
                    run.frame += 1
                    owner.take_commands([Command(run.frame, action=2)])
                    self.advance(run, owner, sigma, 45)
                hp = self.health(run, sigma)
                before = hp.total
                self.cast(run, owner, sigma)
                self.step(run, owner, sigma, fire=button)
                self.advance(run, owner, sigma, 35)
                self.assertEqual(before - hp.total, damage)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), damage)
                self.advance(run, owner, sigma, 150)
                self.assertFalse(hp.temporary_shields)
                self.assertEqual(before - hp.total, damage)

    def test_actual_rivet_and_ten_pellets_are_consumed_by_grasp(self):
        for button in (1, 2):
            with self.subTest(button=button):
                run, owner, defender, behind = self.arena("Sigma")
                before = self.health(run, defender).total
                self.cast(run, owner, defender)
                self.step(run, owner, defender, fire=button)
                self.advance(run, owner, defender, 20)
                self.assertEqual(self.health(run, defender).total, before)
                self.assertEqual(self.health(run, owner).total, 250)
                self.assertEqual(self.health(run, behind).total, 200)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(run.match.combat.rockets, [])

    def test_actual_expiry_restores_damage_and_torbjorn_charge(self):
        for name in ("Genji", "Sigma"):
            with self.subTest(defense=name):
                run, owner, defender, _ = self.arena(name)
                self.cast(run, owner, defender)
                self.advance(run, owner, defender, 150)
                before = self.health(run, defender).total
                self.step(run, owner, defender, fire=1)
                self.advance(run, owner, defender, 20)
                self.assertEqual(self.health(run, defender).total, before - 70)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 70)

    def test_turret_hitscan_uses_the_same_gates(self):
        for name in ("Genji", "Sigma"):
            with self.subTest(defense=name):
                run, owner, defender, behind = self.arena(name, distance=8)
                x, y, z = owner.mover.position
                self.place(behind, (x + 100, y, z + 100))
                turret = hero_torbjorn.deploy_turret(run.match.combat, owner, (x, y, z + 1.5))
                self.advance(run, owner, defender, 185)
                self.assertEqual(turret.shots, 0)
                before = self.health(run, defender).total
                # Aim back along the incoming turret ray. The turret capsule
                # stands in front of its owner and receives the reflected shot.
                flat = (-32768, 0)
                self.cast(run, owner, defender, defender_aim=flat)
                self.advance(run, owner, defender, 20, defender_aim=flat)
                self.assertGreater(turret.shots, 0)
                self.assertEqual(self.health(run, defender).total, before)
                expected = 250 - turret.shots * 14 if name == "Genji" else 250
                self.assertAlmostEqual(run.match.combat.vitals[turret.actor.entity].total, expected)
                self.assertEqual(self.health(run, owner).total, 250)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_reflected_thrown_turret_fire_spends_owners_overload(self):
        run, owner, genji, behind = self.arena("Genji", distance=100)
        fight = run.match.combat
        x, y, z = owner.mover.position
        self.place(behind, (x + 100, y, z + 100))
        run.step({owner: (0, 4000, 8)})
        self.advance(run, owner, genji, 225)
        turret = fight.turrets[owner.entity]
        turret_hp = fight.vitals[turret.actor.entity]
        self.assertEqual(turret_hp.total, 250)
        self.assertEqual(turret.shots, 0)
        x, y, z = turret.actor.position
        self.place(owner, (x + 2, y, z - 1))
        hp = self.health(run, owner)
        base = dict(hp.now)
        run.step({owner: (0, 0, 16)})
        self.advance(run, owner, genji, 60)
        self.assertEqual(hp.over_armour.amount, 100)
        original = tuple(genji.mover.position)
        contact = (x, y, z + 7)
        self.place(genji, contact)
        direction = self.body_aim(run, genji, owner)
        self.place(genji, original)
        self.cast(run, owner, genji, defender_aim=direction)
        self.assertEqual(turret.shots, 0)
        self.place(genji, contact)
        self.advance(run, owner, genji, 20, defender_aim=direction)
        self.assertEqual(turret.shots, 2)
        damage = 9 * turret.shots
        self.assertEqual(hp.over_armour.amount, 100 - damage)
        self.assertEqual(hp.now, base)
        self.assertEqual(turret_hp.total, 250)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), damage)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

        # A reflected rivet leaves enough temporary armour for one full turret hit.
        for _ in range(12):
            self.step(run, owner, genji, fire=1, defender_aim=direction)
            if fight.rockets:
                break
        self.assertEqual(len(fight.rockets), 1)
        for shots, remaining in ((3, 8), (4, 0)):
            for _ in range(22):
                self.step(run, owner, genji, defender_aim=direction)
                if turret.shots == shots and hp.over_armour.amount == remaining:
                    break
            self.assertEqual(turret.shots, shots)
            self.assertEqual(hp.over_armour.amount, remaining)
            if remaining:
                self.assertEqual(hp.now, base)
        base[combat.ARMOUR_POOL] -= 1
        self.assertEqual(hp.now, base)
        self.assertEqual(turret_hp.total, 250)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 101)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 101)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

    def test_reflected_turret_fire_destroys_full_health_turret_and_starts_cooldown(self):
        run, owner, genji, behind = self.arena("Genji", distance=100)
        fight = run.match.combat
        x, y, z = owner.mover.position
        self.place(behind, (x + 100, y, z + 100))
        run.step({owner: (0, 4000, 8)})
        self.advance(run, owner, genji, 225)
        turret = fight.turrets[owner.entity]
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        distant = tuple(genji.mover.position)
        flat = (-32768, 0)
        x, y, z = turret.actor.position
        for shot_limit in (6, 12, 18):
            self.cast(run, owner, genji, defender_aim=flat)
            self.place(genji, (x, y, z + 7))
            for _ in range(115):
                self.step(run, owner, genji, defender_aim=flat)
                if turret.shots >= shot_limit:
                    break
            self.assertEqual(turret.shots, shot_limit)
            self.assertEqual(hp.total, max(0, 250 - shot_limit * 14))
            self.assertEqual(self.health(run, genji).total, 200)
            self.place(genji, distant)
            if not hp.dead:
                self.advance(run, owner, genji, 650)
                self.assertEqual(turret.shots, shot_limit)
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, genji.body)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 250)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, owner).total, 250)
        ability = next(i for i in owner.body_script.component.instances.values()
                       if i.graph.index == 0x16C5).state(13)
        self.assertGreater(ability.remaining(), 9.9)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, owner, genji, 15)
        self.assertNotIn(owner.entity, fight.turrets)
        for _ in range(650):
            if ability.remaining() <= 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, owner, genji, 100)
        self.assertNotEqual(fight.turrets[owner.entity].actor.entity, turret.actor.entity)

    def test_reflected_secondary_destroys_own_turret_and_starts_full_cooldown(self):
        run, torb, genji, bait = self.arena("Genji", distance=100)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        genji.team = bait.team = 1 - torb.team
        x, y, z = torb.mover.position
        self.place(bait, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        self.advance(run, torb, genji, 225)
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        x, y, z = turret.actor.position
        self.place(torb, (x + 1.5, y, z))
        far = tuple(genji.mover.position)
        contact = (x + 1.5, y, z + 3)
        self.place(genji, contact)
        direction = self.body_aim(run, genji, turret.actor)
        self.place(genji, far)
        bait_far = tuple(bait.mover.position)
        rifle = next(i for i in torb.body_script.component.instances.values() if i.graph.index == 0x1683)
        with mock.patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for frames, remaining in ((115, 62.5), (20, 0)):
                self.cast(run, torb, genji, defender_aim=direction)
                # The closer enemy draws turret fire away from the reflected volley.
                self.place(bait, (x - 2, y, z))
                self.place(genji, contact)
                for _ in range(frames):
                    self.step(run, torb, genji, fire=2, defender_aim=direction)
                    if hp.dead:
                        break
                self.assertEqual(hp.total, remaining)
                self.place(genji, far)
                self.place(bait, bait_far)
                if not hp.dead:
                    self.advance(run, torb, genji, 650)
                    self.assertEqual(hp.total, remaining)
        hits = [call for call in damage.call_args_list if call.args[1].owner is turret.actor]
        self.assertTrue(hp.dead, (hp.total, rifle.vars[53].value(), [call.args[2] for call in hits]))
        self.assertEqual(len(hits), 20)
        self.assertTrue(all(call.args[0] is genji and call.args[2] == 12.5 for call in hits))
        self.assertEqual(rifle.vars[53].value(), 10)
        self.assertEqual(hp.killer, genji.body)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 250)
        self.assertEqual(genji.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, torb).total, 250)
        self.assertGreater(turret.shots, 0)
        self.assertEqual(self.health(run, bait).total, 200 - 14 * turret.shots)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 14 * turret.shots)
        self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 9.9)
        run.step({torb: (0, 4000, 8)})
        self.advance(run, torb, genji, 15)
        self.assertNotIn(torb.entity, fight.turrets)
        for _ in range(650):
            if hero_torbjorn.turret_cooldown_remaining(torb) == 0:
                break
            run.step({})
        self.assertEqual(hero_torbjorn.turret_cooldown_remaining(torb), 0)
        run.step({torb: (0, 4000, 8)})
        self.advance(run, torb, genji, 100)
        self.assertNotEqual(fight.turrets[torb.entity].actor.entity, turret.actor.entity)

    def test_unknown_or_excluded_metadata_does_not_silently_enable_interception(self):
        run, owner, defender, _ = self.arena("Sigma")
        self.cast(run, owner, defender)
        fight = run.match.combat
        before = self.health(run, defender).total
        self.step(run, owner, defender, fire=1)
        self.assertEqual(len(fight.rockets), 1)
        rocket = fight.rockets[0]
        self.assertEqual(rocket.definition, 0x0400000000002A66)
        rocket.definition = None
        self.advance(run, owner, defender, 15)
        self.assertEqual(self.health(run, defender).total, before - 70)
        self.assertFalse(projectile_defenses.eligible(None))
        self.assertFalse(projectile_defenses.eligible(-1))
        excluded = next(key for key, tags in projectile_defenses.tags().items()
                        if projectile_defenses.EXCLUDED_TAG in tags)
        self.assertFalse(projectile_defenses.eligible(excluded))

    def test_wall_and_nearer_body_win_before_the_defense_volume(self):
        for name in ("Genji", "Sigma"):
            for obstacle in ("wall", "body"):
                with self.subTest(defense=name, obstacle=obstacle):
                    run, owner, defender, blocker = self.arena(name, distance=8)
                    self.cast(run, owner, defender)
                    x, y, z = owner.mover.position
                    if obstacle == "wall":
                        front = z + 3
                        run.match.collision_world = World([
                            (x-100, y, z-100, x-100, y, z+100, x+100, y, z+100),
                            (x-100, y, z-100, x+100, y, z+100, x+100, y, z-100),
                            (x-3, y, front, x+3, y, front, x+3, y+4, front),
                            (x-3, y, front, x+3, y+4, front, x-3, y+4, front),
                        ], name="defense-wall-and-floor")
                    else:
                        self.place(blocker, (x, y, z + 2))
                    before = self.health(run, defender).total
                    self.step(run, owner, defender, fire=1)
                    self.advance(run, owner, defender, 20)
                    self.assertEqual(self.health(run, defender).total, before)
                    self.assertEqual(self.health(run, owner).total, 250)
                    loss = 0 if obstacle == "wall" else 70
                    self.assertEqual(self.health(run, blocker).total, 200 - loss)
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), loss)

    def test_ally_defense_does_not_intercept_a_shot_to_enemy_behind(self):
        for name in ("Genji", "Sigma"):
            with self.subTest(defense=name):
                run, owner, defender, behind = self.arena(name)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                defender.team = owner.team
                behind.team = 1 - owner.team
                self.cast(run, owner, defender)
                x, y, z = owner.mover.position
                self.place(behind, (x, y, z + 4))
                before = self.health(run, defender).total
                self.step(run, owner, defender, fire=1)
                self.advance(run, owner, defender, 20)
                self.assertEqual(self.health(run, defender).total, before)
                self.assertEqual(self.health(run, behind).total, 130)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 70)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_reflected_primary_damages_each_actual_npc_under_new_ownership(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, defender, _ = self.arena("Genji", distance=8)
                self.cast(run, owner, defender)
                self.step(run, owner, defender, fire=1)
                self.advance(run, owner, defender, 2)
                x, y, z = owner.mover.position
                self.place(owner, (x + 20, y, z))
                actor = npc.spawn(run.match, definition, (x, y, z), team=1-owner.team)
                hp = run.match.combat.vitals[actor.entity]
                before = hp.total
                target_aim = self.body_aim(run, defender, actor)
                self.advance(run, owner, defender, 25, defender_aim=target_aim)
                self.assertEqual(hp.total, before - 70)
                self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 70)
                self.assertEqual(defender.body_script.component.entity_var(342).value(), 70)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_reflected_secondary_damages_each_actual_npc_under_new_ownership(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, genji, _ = self.arena("Genji")
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                genji.team = 1 - owner.team
                run.players[2].team = owner.team
                x, y, z = owner.mover.position
                actor = npc.spawn(match, definition, (x + 1, y, z - 1), team=owner.team)
                hp = fight.vitals[actor.entity]
                before = hp.total
                target_aim = self.body_aim(run, genji, actor)
                self.cast(run, owner, genji, defender_aim=target_aim)
                self.step(run, owner, genji, fire=2, defender_aim=target_aim)
                self.advance(run, owner, genji, 25, defender_aim=target_aim)
                self.assertEqual(before - hp.total, 50)  # Four unarmoured 12.5-damage pellets.
                self.assertEqual(self.health(run, owner).total, 205)  # Six armour-reduced pellets.
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 95)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 95)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_reflected_thrown_turret_fire_hits_each_original_allied_npc(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, genji, other = self.arena("Genji", distance=100)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                genji.team = 1 - owner.team
                other.team = owner.team
                run.step({owner: (0, 4000, 8)})
                self.advance(run, owner, genji, 225)
                turret = fight.turrets[owner.entity]
                self.assertEqual(turret.shots, 0)
                x, y, z = turret.actor.position
                actor = npc.spawn(match, definition, (x + 2, y, z - 1), team=owner.team)
                hp = fight.vitals[actor.entity]
                before = hp.total
                distant = tuple(genji.mover.position)
                contact = (x, y, z + 7)
                self.place(genji, contact)
                direction = self.body_aim(run, genji, actor)
                self.place(genji, distant)
                self.cast(run, owner, genji, defender_aim=direction)
                self.place(genji, contact)
                for _ in range(25):
                    self.step(run, owner, genji, defender_aim=direction)
                    if turret.shots:
                        break
                self.assertEqual(turret.shots, 1)
                self.assertEqual(hp.total, before - 14)
                self.assertEqual(fight.vitals[turret.actor.entity].total, 250)
                self.assertEqual(self.health(run, genji).total, 200)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 14)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 14)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_molten_contact_is_absorbed_or_reflected_without_original_charge(self):
        for name, loss in (("Genji", 20), ("Sigma", 0)):
            with self.subTest(defense=name):
                run, owner, defender, _ = self.arena(name)
                owner.body_script.set_ult_charge(10000)
                self.step(run, owner, defender, fire=32)
                self.advance(run, owner, defender, 45)
                self.cast(run, owner, defender)
                before = self.health(run, defender).total
                self.step(run, owner, defender, fire=1)
                self.advance(run, owner, defender, 20)
                self.assertEqual(self.health(run, defender).total, before)
                self.assertEqual(self.health(run, owner).total, 250 - loss)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(run.match.combat.molten_pools, {})
                self.assertEqual(defender.body_script.component.entity_var(342).value(), 0)

    def test_defenses_activated_during_globule_flight_settle_pool_or_shield(self):
        for name in ("Genji", "Sigma"):
            with self.subTest(defense=name):
                run, owner, defender, victim = self.arena(name, distance=8)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                defender.team = 1 - owner.team
                victim.team = owner.team
                hp = self.health(run, defender)
                before = hp.total
                owner.body_script.set_ult_charge(10000)
                self.step(run, owner, defender, fire=32)
                self.advance(run, owner, defender, 45)
                self.step(run, owner, defender, fire=1)
                globule, = fight.rockets
                self.assertIs(globule.shooter, owner)
                self.assertEqual(globule.spawn, molten_core.POOL)
                self.assertEqual(globule.distance, 0)
                direction = (-32768, 4000 if name == "Genji" else 0)
                self.cast(run, owner, defender, defender_aim=direction)
                self.assertIn(globule, fight.rockets)
                self.assertEqual(hp.total, before)
                for _ in range(25):
                    if globule not in fight.rockets:
                        break
                    self.step(run, owner, defender, defender_aim=direction)
                self.assertNotIn(globule, fight.rockets)
                self.assertEqual(hp.total, before)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(defender.body_script.component.entity_var(342).value(), 0)
                if name == "Sigma":
                    self.assertFalse(fight.rockets or fight.molten_pools)
                    grasp = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == sigma_grasp.GRASP)
                    self.assertEqual(grasp.vars[21312].value(), 25)
                    for _ in range(150):
                        if sigma_grasp.SHIELD_TAG in hp.temporary_shields:
                            break
                        run.step({})
                    self.assertAlmostEqual(hp.temporary_shields[sigma_grasp.SHIELD_TAG].now, 15, places=5)
                    self.assertAlmostEqual(hp.total, before + 15, places=5)
                    self.assertFalse(fight.molten_pools or fight.molten_burns)
                    continue

                reflected, = fight.rockets
                self.assertIs(reflected.shooter, defender)
                self.assertEqual(reflected.team, defender.team)
                self.assertEqual(reflected.spawn, molten_core.POOL)
                for _ in range(45):
                    if fight.molten_pools:
                        break
                    self.step(run, owner, defender, defender_aim=direction)
                pool, = fight.molten_pools.values()
                self.assertIs(pool.owner, defender)
                x, y, z = pool.position
                self.place(owner, (x + 30, y, z))
                for _ in range(40):
                    if pool.phase == 1:
                        break
                    run.step({})
                self.assertEqual(pool.phase, 1)
                self.place(victim, pool.position)
                self.place(defender, pool.position)
                run.step({})
                self.assertAlmostEqual(self.health(run, victim).total, 197.44)
                self.assertEqual(hp.total, before)
                self.assertIn((defender.entity, victim.body), fight.molten_burns)
                self.assertAlmostEqual(defender.stats.value(stats.HERO_DAMAGE), 2.56)
                self.assertEqual(defender.body_script.component.entity_var(342).value(), 0)

    def test_deflect_and_grasp_intercept_a_globule_after_ceiling_rebound(self):
        for name in ("Genji", "Sigma"):
            with self.subTest(defense=name):
                run, owner, defender, victim = self.arena(name, distance=6.5)
                fight = run.match.combat
                x, y, z = owner.mover.position
                run.match.collision_world = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + box(x - 8, x + 8, y + 2.5, y + 3, z - 4, z + 12)
                )
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (0, -7000, 32)})
                self.advance(run, owner, defender, 45)
                defense_aim = (-32768, 4000) if name == "Genji" else (-32768, 0)
                self.cast(run, owner, defender, defender_aim=defense_aim)
                hp = self.health(run, defender)
                before = hp.total
                run.step({owner: (0, -7000, 1), defender: (*defense_aim, 0)})
                flight, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
                for _ in range(20):
                    if flight.segment_start:
                        break
                    self.step(run, owner, defender, defender_aim=defense_aim)
                self.assertIn(flight, fight.rockets)
                self.assertGreater(flight.segment_start, 0)
                self.assertFalse(fight.molten_pools)
                rebound_distance = flight.distance
                for _ in range(25):
                    if flight not in fight.rockets:
                        break
                    self.step(run, owner, defender, defender_aim=defense_aim)
                self.assertNotIn(flight, fight.rockets)
                self.assertEqual(hp.total, before)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                if name == "Sigma":
                    self.assertFalse(fight.rockets or fight.molten_pools)
                    grasp = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == sigma_grasp.GRASP)
                    self.assertEqual(grasp.vars[21312].value(), 25)
                    for _ in range(150):
                        if sigma_grasp.SHIELD_TAG in hp.temporary_shields:
                            break
                        self.step(run, owner, defender)
                    self.assertAlmostEqual(hp.temporary_shields[sigma_grasp.SHIELD_TAG].now, 15, places=5)
                else:
                    reflected, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
                    self.assertIs(reflected.shooter, defender)
                    self.assertGreater(reflected.distance, rebound_distance)
                    self.assertLessEqual(reflected.left, flight.left)
                    self.advance(run, owner, defender, 45, defender_aim=defense_aim)
                    pool, = fight.molten_pools.values()
                    self.assertIs(pool.owner, defender)
                    hp = self.health(run, victim)
                    self.place(victim, pool.position)
                    self.advance(run, owner, defender, 8)
                    self.assertAlmostEqual(hp.total, 179.52)
                    self.assertIn((defender.entity, victim.body), fight.molten_burns)
                    self.assertEqual(defender.body_script.component.entity_var(342).value(), 0)

    def launch_reflected_molten(self):
        run, owner, defender, victim = self.arena("Genji", distance=8)
        owner.body_script.set_ult_charge(10000)
        self.step(run, owner, defender, fire=32)
        self.advance(run, owner, defender, 45)
        ground_aim = (-32768, 4000)
        self.cast(run, owner, defender, defender_aim=ground_aim)
        self.step(run, owner, defender, fire=1, defender_aim=ground_aim)
        return run, owner, defender, victim

    def reflected_molten_pool(self):
        run, owner, defender, victim = self.launch_reflected_molten()
        ground_aim = (-32768, 4000)
        self.advance(run, owner, defender, 50, defender_aim=ground_aim)
        fight = run.match.combat
        self.assertEqual(len(fight.molten_pools), 1)
        pool = next(iter(fight.molten_pools.values()))
        self.assertIs(pool.owner, defender)
        self.assertEqual(pool.owner_hero, defender.hero.guid)
        return run, owner, defender, victim, pool

    def test_airborne_reflected_molten_uses_current_owner_for_switch_cleanup(self):
        soldier = next(hero for hero in heroes().values() if hero.name == "Soldier: 76")
        for switch_reflector in (False, True):
            with self.subTest(switch_reflector=switch_reflector):
                run, owner, genji, victim = self.launch_reflected_molten()
                match = run.match
                fight = match.combat
                for _ in range(50):
                    rockets = [r for r in fight.rockets if r.shooter is genji and r.spawn == molten_core.POOL]
                    if rockets:
                        break
                    self.step(run, owner, genji, defender_aim=(-32768, 4000))
                rocket, = rockets
                self.assertFalse(fight.molten_pools)
                self.assertTrue(match.switch_hero(genji if switch_reflector else owner, soldier))
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(rocket.spawn, None if switch_reflector else molten_core.POOL)
                self.advance(run, owner, genji, 50)
                self.assertNotIn(rocket, fight.rockets)
                if switch_reflector:
                    self.assertFalse(fight.molten_pools)
                    self.assertFalse(fight.molten_burns)
                else:
                    pool, = fight.molten_pools.values()
                    self.assertIs(pool.owner, genji)
                    hp = self.health(run, victim)
                    before = hp.total
                    self.place(victim, pool.position)
                    run.step({})
                    self.assertLess(hp.total, before)
                    self.assertIn((genji.entity, victim.body), fight.molten_burns)

    def test_airborne_reflected_molten_survives_genji_death_and_respawn(self):
        run, owner, genji, attacker = self.arena('Genji', distance=8)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(attacker, heroes()[hero_torbjorn.HERO]))
        owner.body_script.set_ult_charge(10000)
        self.step(run, owner, genji, fire=32)
        self.advance(run, owner, genji, 45)
        direction = (-32768, 1000)
        self.cast(run, owner, genji, defender_aim=direction)
        self.step(run, owner, genji, fire=1, defender_aim=direction)
        for _ in range(50):
            reflected = [r for r in fight.rockets if r.shooter is genji and r.spawn == molten_core.POOL]
            if reflected:
                break
            self.step(run, owner, genji, defender_aim=direction)
        rocket, = reflected
        hp = self.health(run, genji)
        old_body = genji.body
        hp.damage(hp.total - 1)
        fight._sync_body_health(genji, hp)
        x, y, z = genji.mover.position
        self.place(attacker, (x, y, z + 3))
        for frame in range(8):
            run.step({attacker: (*self.body_aim(run, attacker, genji), int(frame == 0)),
                      genji: (*direction, 0)})
            if hp.dead:
                break
        self.assertTrue(hp.dead)
        self.assertIn(rocket, fight.rockets)
        self.assertFalse(fight.molten_pools)
        self.assertEqual(hp.killer, attacker.body)
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        x, y, z = pool.position
        self.place(owner, (x + 30, y, z))
        self.place(attacker, pool.position)
        attacker_hp = self.health(run, attacker)
        self.assertEqual(pool.phase, 1)
        self.assertIs(pool.owner, genji)
        self.assertEqual(attacker_hp.total, 250)
        for _ in range(8):
            run.step({})
        self.assertTrue(fight.is_dead(genji))
        self.assertAlmostEqual(attacker_hp.total, 218)
        self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 32)
        self.place(attacker, (x - 30, y, z))
        for _ in range(700):
            if genji.body != old_body:
                break
            run.step({})
        self.assertNotEqual(genji.body, old_body)
        self.assertFalse(fight.is_dead(genji))
        self.assertEqual(list(fight.molten_pools.values()), [pool])
        self.assertAlmostEqual(attacker_hp.total, 218)
        self.place(attacker, pool.position)
        run.step({})
        self.assertIn((genji.entity, attacker.body), fight.molten_burns)
        self.assertAlmostEqual(attacker_hp.total, 214)
        self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 36)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

    def test_reflected_molten_preserves_landing_metadata_and_pool_owner(self):
        run, owner, defender, victim, pool = self.reflected_molten_pool()
        fight = run.match.combat
        self.place(victim, pool.position)
        self.advance(run, owner, defender, 35, defender_aim=(-32768, 4000))
        self.assertLess(self.health(run, victim).total, 200)
        self.assertIn((defender.entity, victim.body), fight.molten_burns)
        self.assertGreater(defender.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(defender.body_script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_reflected_molten_pool_filters_both_teams_for_each_actual_npc(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, genji, other, pool = self.reflected_molten_pool()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                genji.team = other.team = 1 - owner.team
                x, y, z = pool.position
                enemy = npc.spawn(match, definition, (x - .6, y, z), team=owner.team)
                ally = npc.spawn(match, definition, (x + .6, y, z), team=genji.team)
                enemy_hp, ally_hp = (fight.vitals[bot.entity] for bot in (enemy, ally))
                enemy_before, ally_before = enemy_hp.total, ally_hp.total
                self.assertEqual(pool.phase, 1)
                for _ in range(5):
                    run.step({})
                damage = pool.config["base_dps"] * combat.TICK * 5
                self.assertAlmostEqual(enemy_before - enemy_hp.total, damage)
                self.assertEqual(ally_hp.total, ally_before)
                self.assertIn(enemy.entity, pool.targets)
                self.assertNotIn(ally.entity, pool.targets)
                self.assertIn((genji.entity, enemy.entity), fight.molten_burns)
                self.assertNotIn((genji.entity, ally.entity), fight.molten_burns)
                self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
                enemy_key = (genji.entity, enemy.entity)
                ally_key = (genji.entity, ally.entity)
                old_burn = fight.molten_burns[enemy_key]
                enemy.team, ally.team = ally.team, enemy.team
                run.step({})
                tick_damage = pool.config["base_dps"] * combat.TICK
                self.assertAlmostEqual(enemy_before - enemy_hp.total, damage)
                self.assertAlmostEqual(ally_before - ally_hp.total, tick_damage)
                self.assertNotIn(enemy.entity, pool.targets)
                self.assertIn(ally.entity, pool.targets)
                self.assertNotIn(enemy_key, fight.molten_burns)
                self.assertNotIn(old_burn.instance, old_burn.script.component.instances)
                self.assertIn(ally_key, fight.molten_burns)
                self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), damage + tick_damage)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
                if definition.name == "Heavy Assault":
                    ally_hp.now[combat.HEALTH_POOL] = 5  # 4000 HP exceeds one pool's lifetime damage.
                remaining = ally_hp.total
                credited = genji.stats.value(stats.HERO_DAMAGE)
                lethal_burn = fight.molten_burns[ally_key]
                for _ in range(300):
                    run.step({})
                    if ally.dead:
                        break
                self.assertTrue(ally.dead)
                self.assertEqual(ally_hp.killer, genji.body)
                self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE) - credited, remaining)
                self.assertNotIn(ally_key, fight.molten_burns)
                self.assertNotIn(lethal_burn.instance, lethal_burn.script.component.instances)
                run.step({})
                self.assertNotIn(ally.entity, pool.targets)
                self.assertAlmostEqual(enemy_before - enemy_hp.total, damage)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_reflected_pool_kills_overloaded_original_caster_and_spares_genjis_ally(self):
        run, owner, genji, ally, pool = self.reflected_molten_pool()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        genji.team = ally.team = 1 - owner.team
        self.advance(run, owner, genji, 320)
        self.assertIn(pool.entity, fight.molten_pools)
        run.step({owner: (0, 0, 16)})
        hp = self.health(run, owner)
        self.assertEqual(hp.total, 350)
        self.assertEqual(hp.over_armour.amount, 100)
        ally_hp = self.health(run, ally)
        self.place(owner, pool.position)
        x, y, z = pool.position
        self.place(ally, (x + 1, y, z))
        run.step({})
        config = pool.config
        tick_damage = (config["base_dps"] + config["armour_bonus_dps"]) * combat.TICK
        self.assertAlmostEqual(hp.over_armour.amount, 100 - tick_damage)
        self.assertAlmostEqual(hp.total, 350 - tick_damage)
        self.assertIn((genji.entity, owner.body), fight.molten_burns)
        for _ in range(220):
            run.step({})
            self.assertEqual(ally_hp.total, 200)
            self.assertNotIn(ally.body, pool.targets)
            if hp.dead:
                break
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, genji.body)
        self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 350)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertNotIn((genji.entity, owner.body), fight.molten_burns)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_landed_reflected_pool_kills_after_genji_dies(self):
        run, owner, genji, attacker, pool = self.reflected_molten_pool()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        genji.team = 1 - owner.team
        attacker.team = owner.team
        match.switch_hero(attacker, owner.hero)
        x, y, z = genji.mover.position
        self.place(attacker, (x + 3, y, z))
        genji_hp = self.health(run, genji)
        self.assertEqual(genji_hp.total, 200)
        for _ in range(240):
            yaw, pitch = self.body_aim(run, attacker, genji)
            run.step({attacker: (yaw, pitch, 1)})
            if genji_hp.dead:
                break
        self.assertTrue(genji_hp.dead)
        self.assertEqual(genji_hp.killer, attacker.body)
        self.assertIs(fight.molten_pools[pool.entity], pool)
        self.assertIs(pool.owner, genji)

        owner_hp = self.health(run, owner)
        self.assertEqual(owner_hp.total, 250)
        self.place(owner, pool.position)
        run.step({})
        self.assertIn((genji.entity, owner.body), fight.molten_burns)
        for _ in range(220):
            run.step({})
            if owner_hp.dead:
                break
        self.assertTrue(genji_hp.dead)
        self.assertTrue(owner_hp.dead)
        self.assertEqual(owner_hp.killer, genji.body)
        self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 250)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_reflected_pool_retires_only_when_its_current_owner_switches_hero(self):
        run, owner, genji, victim, pool = self.reflected_molten_pool()
        match = run.match
        fight = match.combat
        soldier = next(hero for hero in heroes().values() if hero.name == "Soldier: 76")
        self.place(victim, pool.position)
        run.step({})
        key = (genji.entity, victim.body)
        burn = fight.molten_burns[key]
        hp = self.health(run, victim)

        self.assertTrue(match.switch_hero(owner, soldier))
        self.assertIs(fight.molten_pools[pool.entity], pool)
        self.assertIs(fight.molten_burns[key], burn)
        before = hp.total
        run.step({})
        self.assertLess(hp.total, before)

        self.assertTrue(match.switch_hero(genji, soldier))
        self.assertTrue(pool.ended)
        self.assertNotIn(pool.entity, fight.molten_pools)
        self.assertNotIn(key, fight.molten_burns)
        self.assertNotIn(burn.instance, burn.script.component.instances)
        before = hp.total
        run.step({})
        self.assertEqual(hp.total, before)

    def test_reflected_kill_charges_new_owner_only_for_remaining_health(self):
        run, owner, defender, _ = self.arena("Genji")
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(defender, target, 225, False, None, run.frame)
        self.assertEqual(self.health(run, owner).total, 30)
        defender.body_script.set_ult_charge(0)
        self.cast(run, owner, defender)
        self.step(run, owner, defender, fire=1)
        self.advance(run, owner, defender, 20)
        hp = self.health(run, owner)
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, defender.body)
        self.assertTrue(defender.client.messages(combat.KILL_NOTICE))
        self.assertEqual(defender.body_script.component.entity_var(342).value(), 30)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_reflector_native_charge_modifiers_permission_and_cap_are_retained(self):
        for setting, value, expected in (("modifier", None, 195), ("permission", False, 0),
                                         ("cap", None, 10)):
            with self.subTest(setting=setting):
                run, owner, defender, _ = self.arena("Genji")
                component = defender.body_script.component
                before = 0
                if setting == "modifier":
                    component.set(9532, 2.0)
                    component.set(13022, 1.5)
                elif setting == "permission":
                    component.set(2426, value)
                else:
                    before = component.entity_var(338).value() - 10
                    defender.body_script.set_ult_charge(before)
                self.cast(run, owner, defender)
                self.step(run, owner, defender, fire=1)
                self.advance(run, owner, defender, 20)
                self.assertEqual(self.health(run, owner).total, 185)
                self.assertEqual(component.entity_var(342).value() - before, expected)

    def test_reflection_carries_ballistics_and_consumes_the_entire_retained_gap(self):
        results = []
        for advances in ((1,) * 10, (10,)):
            run, owner, defender, _ = self.arena("Genji", distance=8)
            self.cast(run, owner, defender)
            self.step(run, owner, defender, fire=1)
            fight = run.match.combat
            original, = fight.rockets
            original.last_tick = fight.tick
            lifetime = original.left
            with mock.patch.object(fight.projectiles, "flown", wraps=fight.projectiles.flown) as flown:
                for ticks in advances:
                    current, = fight.rockets
                    fight.tick += ticks
                    fight._fly(current)
                self.assertEqual(flown.call_count, 1)
                self.assertTrue(flown.call_args.kwargs["intercepted"])
            reflected, = fight.rockets
            self.assertIs(reflected.shooter, defender)
            self.assertIsNot(reflected, original)
            self.assertEqual(reflected.definition, original.definition)
            self.assertEqual(reflected.gravity, original.gravity)
            self.assertIsNone(reflected.shot)
            self.assertTrue(original.shot.intercepted)
            self.assertIsNotNone(original.shot.release_at)
            self.assertAlmostEqual(reflected.left, lifetime - .16)
            self.assertAlmostEqual(reflected.flown + original.flown, .16)
            velocity = [v * original.speed for v in original.direction]
            velocity[1] -= original.gravity * original.flown
            self.assertAlmostEqual(reflected.speed, math.sqrt(sum(v * v for v in velocity)))
            results.append((*reflected.position, reflected.speed, reflected.left,
                            reflected.flown, reflected.distance))
        for a, b in zip(*results, strict=True):
            self.assertAlmostEqual(a, b)

    def test_intercepted_prediction_retires_without_owner_or_observer_explosion(self):
        for name in ("Genji", "Sigma"):
            with self.subTest(defense=name):
                run, owner, defender, _ = self.arena(name, distance=8)
                self.cast(run, owner, defender)
                self.step(run, owner, defender, fire=1)
                fight = run.match.combat
                original, = fight.rockets
                shot = original.shot
                self.assertIsNotNone(shot)
                self.advance(run, owner, defender, 120)
                self.assertTrue(shot.intercepted)
                self.assertNotIn(shot, fight.projectiles.shots)
                self.assertTrue(shot.created)
                destroyed = [record for record in owner.client.entities
                             if record.op == world.OP_DESTROY and record.entity in shot.created]
                self.assertTrue(destroyed)
                self.assertTrue(all(record.ch2 is None for record in destroyed))
                self.assertTrue(all(delivery.finished and not delivery.explode
                                    for delivery in shot.observers))

    def test_grasp_full_pitch_blocks_an_overhead_shot_only_while_facing_it(self):
        for facing in (True, False):
            with self.subTest(facing_up=facing):
                run, owner, defender, _ = self.arena("Sigma", distance=8)
                x, y, z = defender.mover.position
                self.place(owner, (x, y + 7, z))
                look = (0, -16384) if facing else (0, 0)
                self.cast(run, owner, defender, defender_aim=look)
                before = self.health(run, defender).total
                self.step(run, owner, defender, fire=1, defender_aim=look)
                self.advance(run, owner, defender, 15, defender_aim=look)
                loss = 0 if facing else 70
                self.assertEqual(self.health(run, defender).total, before - loss)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), loss)

    def test_projectile_definition_and_interception_flag_are_passive_snapshot_state(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.projectiles import Shot
        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        owner = players[0]
        fight = owner.match.combat
        shot = Shot(owner, owner.body, 0x0400000000002A66, 1, 1, 1, (1, 16))
        rocket = combat.Rocket(owner, owner.team, fight._eye(owner), (0, 0, 1), 70, 1, None, None,
                               shot, definition=shot.definition)
        fight.rockets.append(rocket)
        baseline = snapshot_digest(case.capture(session))
        self.assertEqual(baseline, snapshot_digest(case.capture(session)))
        rocket.definition = 0x04000000000013ED
        self.assertNotEqual(baseline, snapshot_digest(case.capture(session)))
        rocket.definition = shot.definition
        self.assertEqual(baseline, snapshot_digest(case.capture(session)))
        shot.intercepted = True
        self.assertNotEqual(baseline, snapshot_digest(case.capture(session)))

    def test_reflection_reverses_team_eligibility_for_the_return_path(self):
        for ally_of_reflector in (True, False):
            with self.subTest(ally_of_reflector=ally_of_reflector):
                run, owner, defender, blocker = self.arena("Genji", distance=8)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                self.cast(run, owner, defender)
                self.step(run, owner, defender, fire=1)
                self.advance(run, owner, defender, 8)
                reflected, = run.match.combat.rockets
                self.assertIs(reflected.shooter, defender)
                x, y, z = owner.mover.position
                self.place(blocker, (x, y, z + 2))
                blocker.team = defender.team if ally_of_reflector else owner.team
                self.advance(run, owner, defender, 15)
                self.assertEqual(self.health(run, blocker).total, 200 if ally_of_reflector else 130)
                self.assertEqual(self.health(run, owner).total, 185 if ally_of_reflector else 250)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
