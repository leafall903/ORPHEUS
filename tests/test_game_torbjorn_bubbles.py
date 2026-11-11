"""Command-driven personal and projected Zarya protection against Torbjorn."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn_defenses as defenses
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import (
    combat,
    hero_torbjorn,
    molten_core,
    npc,
    npc_control,
    orisa_halt,
    phase_defenses,
    stats,
    zarya_bubbles,
)
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE")
                     and os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "needs prepared hero and projectile data")
class TorbjornBubbleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self, projected=False, distance=4):
        fixture = defenses.TorbjornDefenseTests()
        run, owner, zarya, ally = fixture.arena("Zarya", distance)
        if not projected:
            return fixture, run, owner, zarya, zarya
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        zarya.team = ally.team = 1 - owner.team
        x, y, z = zarya.mover.position
        fixture.place(ally, (x, y, z))
        fixture.place(zarya, (x - 4, y, z))
        fixture.advance(run, owner, ally, 10)
        return fixture, run, owner, zarya, ally

    def cast(self, fixture, run, zarya, protected):
        if zarya is protected:
            run.step({zarya: (0, 0, 8)})
        else:
            yaw, pitch = fixture.body_aim(run, zarya, protected)
            run.step({zarya: (yaw, pitch, 16)})
        bubble = run.match.combat.bubbles.for_target(protected)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, zarya)
        self.assertEqual(bubble.health, 200)
        return bubble

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "needs authored Halt data")
    def test_halt_pulls_through_personal_and_projected_barriers_without_spending_capacity(self):
        for projected in (False, True):
            for outside in (False, True):
                with self.subTest(projected=projected, outside=outside):
                    fixture = defenses.TorbjornDefenseTests()
                    run, torb, zarya, caster = fixture.arena("Zarya")
                    match = run.match
                    fight = match.combat
                    self.assertTrue(match.switch_hero(caster, heroes()[orisa_halt.HERO]))
                    match.game_map = replace(match.game_map, free_for_all=False)
                    caster.team, zarya.team = 0, 1
                    torb.team = zarya.team if projected else caster.team
                    x, y, z = caster.mover.position
                    target_z = z + (4.5 if outside else 7.4)
                    fixture.place(zarya, (x - 3 if projected else x, y, target_z))
                    fixture.place(torb, (x if projected else x + 8, y, target_z))
                    fixture.advance(run, torb, zarya, 35)
                    protected = torb if projected else zarya
                    hp = fixture.health(run, protected)
                    before = hp.total
                    caster_hp = fixture.health(run, caster)
                    start = tuple(protected.mover.position)
                    bubble = self.cast(fixture, run, zarya, protected)
                    for frame in range(26):
                        run.step({caster: (0, 0, 2 if frame in (0, 25) else 0)})
                    orb, = fight.halt.orbs.values()
                    self.assertEqual(math.dist(orb.position, bubble.centre()) > zarya_bubbles.RADIUS, outside)
                    run.step({})
                    self.assertTrue(orb.pulled)
                    pull = fight.halt.pulls[protected.body]
                    self.assertIsNotNone(pull.instance)
                    self.assertEqual(bubble.health, 200)
                    self.assertEqual(hp.total, before)
                    self.assertEqual(tuple(protected.mover.position), start)

                    target = caster if projected else protected
                    run.step({caster: (*fixture.body_aim(run, caster, protected), 1),
                              torb: (*fixture.body_aim(run, torb, target), 1)})
                    for _ in range(20):
                        if bubble.health == (189 if projected else 119) and (
                                not projected or caster_hp.total < 450):
                            break
                        run.step({})
                    self.assertEqual(bubble.health, 189 if projected else 119)
                    self.assertEqual(hp.total, before)
                    self.assertEqual(caster_hp.total, 385 if projected else 450)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 65 if projected else 0)
                    charge = torb.body_script.component.entity_var(342)
                    self.assertEqual(charge.value(), 65 if projected else 0)
                    self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
                    for _ in range(60):
                        if math.dist(protected.mover.position, start) > .2:
                            break
                        run.step({})
                    self.assertGreater(math.dist(protected.mover.position, start), .2)
                    self.assertIs(fight.halt.pulls[protected.body], pull)
                    self.assertIs(fight.bubbles.for_target(protected), bubble)
                    self.assertEqual(bubble.health, 189 if projected else 119)
                    self.assertEqual(hp.total, before)


    def test_personal_and_projected_barriers_consume_actual_weapons_and_charge_caster(self):
        for projected in (False, True):
            for button, damage in ((1, 70), (2, 125)):
                with self.subTest(projected=projected, button=button):
                    fixture, run, owner, zarya, protected = self.arena(projected)
                    bubble = self.cast(fixture, run, zarya, protected)
                    if projected:
                        vm = bubble.caster.body_script.component.instances[bubble.instance_id]
                        self.assertEqual(vm.vars[536].value(), expr.Entity(protected.body))
                    hp = fixture.health(run, protected)
                    before = hp.total
                    fixture.step(run, owner, protected, fire=button)
                    fixture.advance(run, owner, protected, 20)
                    self.assertAlmostEqual(bubble.health, 200 - damage)
                    self.assertEqual(hp.total, before)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                    energy = zarya.body_script.component.entity_var(87).value()
                    self.assertGreater(energy, damage * .2 - 2)
                    self.assertLessEqual(energy, damage * .2)
                    if projected:
                        self.assertIsNone(protected.body_script.component.entity_var(87, create=False))

    def test_personal_and_projected_barriers_cast_during_torbjorn_projectile_flight(self):
        for projected in (False, True):
            for weapon, button, count, damage in (
                ("rivet", 1, 1, 70), ("secondary", 2, 10, 125), ("globule", 1, 1, 25),
            ):
                with self.subTest(projected=projected, weapon=weapon):
                    fixture, run, owner, zarya, protected = self.arena(projected)
                    fight = run.match.combat
                    hp = fixture.health(run, protected)
                    before = hp.total
                    charge = owner.body_script.component.entity_var(342)
                    if weapon == "globule":
                        owner.body_script.set_ult_charge(10000)
                        fixture.step(run, owner, protected, fire=32)
                        fixture.advance(run, owner, protected, 45)

                    self.assertIsNone(fight.bubbles.for_target(protected))
                    fixture.step(run, owner, protected, fire=button)
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), count)
                    self.assertTrue(all(shot.shooter is owner and shot.distance == 0 for shot in volley))
                    if weapon == "globule":
                        self.assertEqual(volley[0].spawn, molten_core.POOL)
                    bubble = self.cast(fixture, run, zarya, protected)
                    self.assertEqual(tuple(fight.rockets), volley)
                    fixture.advance(run, owner, protected, 20)

                    self.assertIs(fight.bubbles.for_target(protected), bubble)
                    self.assertEqual(bubble.health, 200 - damage)
                    self.assertEqual(hp.total, before)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(charge.value(), 0)
                    self.assertNotIn((owner.entity, protected.body), fight.torbjorn_marks)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)
                    energy = zarya.body_script.component.entity_var(87).value()
                    self.assertGreater(energy, damage * .2 - 2)
                    self.assertLessEqual(energy, damage * .2)

                    for _ in range(140):
                        if fight.bubbles.for_target(protected) is None:
                            break
                        run.step({})
                    self.assertIsNone(fight.bubbles.for_target(protected))
                    fixture.step(run, owner, protected, fire=button)
                    self.assertEqual(len(fight.rockets), count)
                    fixture.advance(run, owner, protected, 20)
                    self.assertEqual(hp.total, before - damage)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                    self.assertEqual(charge.value(), 0 if weapon == "globule" else damage)
                    self.assertEqual((owner.entity, protected.body) in fight.torbjorn_marks,
                                     weapon == "rivet")
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)

    def test_melee_and_hammer_damage_bubble_once_without_body_damage(self):
        for hammer, damage in ((False, 30), (True, 55)):
            with self.subTest(hammer=hammer):
                fixture, run, owner, zarya, _ = self.arena(distance=1.8)
                if hammer:
                    run.frame += 1
                    owner.take_commands([Command(run.frame, action=2)])
                    fixture.advance(run, owner, zarya, 45)
                bubble = self.cast(fixture, run, zarya, zarya)
                hp = fixture.health(run, zarya)
                before = hp.total
                fixture.step(run, owner, zarya, fire=1 if hammer else 0x800)
                fixture.advance(run, owner, zarya, 35)
                self.assertEqual(bubble.health, 200 - damage)
                self.assertEqual(hp.total, before)

    def test_secondary_breaks_either_bubble_without_spill_from_the_breaking_pellet(self):
        for projected in (False, True):
            for capacity, blocked in ((5, 1), (12.5, 1), (12.75, 2)):
                with self.subTest(projected=projected, capacity=capacity):
                    fixture, run, owner, zarya, protected = self.arena(projected=projected, distance=3)
                    fight = run.match.combat
                    bubble = self.cast(fixture, run, zarya, protected)
                    bubble.health = capacity
                    hp = fixture.health(run, protected)
                    before = hp.total
                    owner.body_script.ult_seconds = 0
                    owner.body_script.set_ult_charge(0)
                    rifle = next(i for i in owner.body_script.component.instances.values()
                                 if i.graph.index == 0x1683)
                    with (
                        patch.object(fight, "_shield_damage", wraps=fight._shield_damage) as shield_hits,
                        patch.object(fight, "_damage", wraps=fight._damage) as body_hits,
                    ):
                        fixture.step(run, owner, protected, fire=2)
                        self.assertEqual(len(fight.rockets), 10)
                        self.assertEqual(rifle.vars[53].value(), 16)
                        fixture.advance(run, owner, protected, 12)
                    self.assertIsNone(fight.bubbles.for_target(protected))
                    self.assertEqual(bubble.health, 0)
                    self.assertEqual([call.args[2] for call in shield_hits.call_args_list], [12.5] * blocked)
                    self.assertEqual(
                        [call.args[2] for call in body_hits.call_args_list], [12.5] * (10 - blocked),
                    )
                    self.assertTrue(all(call.args[1].owner is protected for call in body_hits.call_args_list))
                    damage = 12.5 * (10 - blocked)
                    self.assertEqual(hp.total, before - damage)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), damage)
                    self.assertFalse(fight.rockets)
                    self.assertFalse(fight.torbjorn_marks)

    def test_break_consumes_last_rivet_without_spill_and_later_rivet_hits_body(self):
        fixture, run, owner, zarya, protected = self.arena(projected=True)
        fight = run.match.combat
        bubble = self.cast(fixture, run, zarya, protected)
        hp = fixture.health(run, protected)
        before = hp.total
        for expected in (130, 60, 0):
            fixture.step(run, owner, protected, fire=1)
            fixture.advance(run, owner, protected, 39)
            self.assertEqual(bubble.health, expected)
            self.assertEqual(hp.total, before)
        self.assertIsNone(fight.bubbles.for_target(protected))
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        fixture.step(run, owner, protected, fire=1)
        fixture.advance(run, owner, protected, 20)
        self.assertEqual(hp.total, before - 70)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs the five-NPC catalog")
    def test_talon_ranged_fire_spends_projected_barrier_then_overload(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        cases = {"Trooper": (20, 15), "Sniper": (100, 95), "Heavy Assault": (5, 2.5), "Enforcer": (50, 24)}
        for name, damage in cases.items():
            definition = next(row for row in catalog.values() if row.name == name)
            for breaking in (False, True):
                with self.subTest(npc=name, breaking=breaking):
                    self.check_talon_projected_barrier(definition, breaking, *damage)

    def check_talon_projected_barrier(self, definition, breaking, shield_damage, body_damage):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, other = fixture.arena("Zarya")
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya.team = torb.team
        x, y, z = torb.mover.position
        fixture.place(zarya, (x - 5, y, z))
        fixture.place(other, (x + 100, y, z + 100))
        bot = npc.spawn(match, definition, (x, y, z + 5), team=1 - torb.team)
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})
        target = next(target for target in fight._targets() if target.owner is bot)
        direction = aim(fight._eye(torb), (x, y + target.height * .5, z + 5))
        run.step({torb: (*direction, 0)})
        self.assertEqual(npc_control.start(match, torb), bot.entity)
        hp = fixture.health(run, torb)
        if definition.name == "Sniper":
            fixture.advance(run, torb, zarya, 50)
            self.assertEqual(hp.total, 250)
        run.step({torb: (*direction, 16)})
        bubble = self.cast(fixture, run, zarya, torb)
        initial = 1 if breaking else 200
        if breaking:
            bubble.health = initial
        self.assertEqual(hp.total, 350)
        base = dict(hp.now)
        source = hp.over_armour.source
        for _ in range(120):
            run.step({})
            if bubble.health < initial:
                break
        spent = min(initial, shield_damage)
        self.assertEqual(bubble.health, initial - spent)
        self.assertEqual(hp.over_armour.amount, 100)
        energy = zarya.body_script.component.entity_var(87)
        credited_energy = energy.value()
        self.assertAlmostEqual(credited_energy, spent * .2, places=5)
        if breaking:
            self.assertIsNone(fight.bubbles.for_target(torb))
            with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                for _ in range(150):
                    run.step({})
                    if hp.total < 350:
                        break
            if definition.name == "Enforcer":
                self.assertFalse(any(call.args[0] is bot for call in shots.call_args_list))
            self.assertEqual(hp.over_armour.amount, 100 - body_damage)
            self.assertLessEqual(energy.value(), credited_energy)
        else:
            self.assertIs(fight.bubbles.for_target(torb), bubble)
        self.assertEqual(hp.now, base)
        self.assertEqual(hp.over_armour.source, source)

    def test_particle_beam_uses_live_range_cover_team_and_projected_barrier(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, attacker, ally = fixture.arena("Zarya", 5)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        self.assertTrue(match.switch_hero(ally, attacker.hero))
        ally.team = torb.team
        attacker.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(ally, (x - 5, y, z))
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        run.step({torb: (0, 0, 16)})
        source = hp.over_armour.source
        self.assertEqual(hp.over_armour.amount, 100)
        fixture.advance(run, torb, attacker, 25, defense=1)
        self.assertAlmostEqual(hp.over_armour.amount, 79.0525)
        self.assertAlmostEqual(attacker.stats.value(stats.HERO_DAMAGE), 20.9475)
        before = hp.over_armour.amount
        fixture.advance(run, torb, attacker, 8)
        self.assertEqual(hp.over_armour.amount, before)

        attacker.team = torb.team
        fixture.advance(run, torb, attacker, 8, defense=1)
        self.assertEqual(hp.over_armour.amount, before)
        attacker.team = 1 - torb.team
        clear = match.collision_world
        match.collision_world = World(
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + wall(x - 4, z + 2, x + 4, z + 2, y0=y, y1=y + 4)
        )
        fixture.advance(run, torb, attacker, 8, defense=1)
        self.assertEqual(hp.over_armour.amount, before)
        match.collision_world = clear
        position = tuple(attacker.mover.position)
        fixture.place(attacker, (x, y, z + 20))
        fixture.advance(run, torb, attacker, 8, defense=1)
        self.assertEqual(hp.over_armour.amount, before)
        fixture.place(attacker, position)
        fixture.advance(run, torb, attacker, 8, defense=1)
        self.assertLess(hp.over_armour.amount, before)

        bubble = self.cast(fixture, run, ally, torb)
        before = hp.over_armour.amount
        credit = attacker.stats.value(stats.HERO_DAMAGE)
        fixture.advance(run, torb, attacker, 8, defense=1)
        self.assertLess(bubble.health, 200)
        capacity = bubble.health
        fixture.advance(run, torb, attacker, 8, defense=1)
        self.assertAlmostEqual(capacity - bubble.health, 9.6)
        self.assertGreater(ally.body_script.component.entity_var(87).value(), 0)
        self.assertEqual(hp.over_armour.amount, before)
        self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), credit)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)

    def test_particle_beam_scales_with_energy_earned_from_torbjorn_rivet(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, _ = fixture.arena("Zarya", 5)
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        fixture.step(run, torb, zarya, fire=16)
        fixture.advance(run, torb, zarya, 45)
        source = hp.over_armour.source
        bubble = self.cast(fixture, run, zarya, zarya)
        fixture.step(run, torb, zarya, fire=1)
        for _ in range(20):
            if bubble.health < 200:
                break
            fixture.step(run, torb, zarya)
        self.assertEqual(bubble.health, 130)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        energy = zarya.body_script.component.entity_var(87)
        self.assertAlmostEqual(energy.value(), 14)
        weapon = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x241)
        total = 0
        for frame in range(12):
            elapsed = 15 if frame == 0 else 16
            before = hp.over_armour.amount
            fixture.step(run, torb, zarya, defense=1)
            self.assertAlmostEqual(energy.value(), 14)
            expected = (75 + (170 - 75) * .14) * elapsed / 1000 * .7
            self.assertAlmostEqual(before - hp.over_armour.amount, expected, places=5)
            total += expected
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), total, places=5)
        self.assertEqual(bubble.health, 130)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)
        self.assertTrue(weapon.states[10].active)

        before = hp.over_armour.amount
        self.assertTrue(run.match.switch_hero(zarya, heroes()[SOLDIER]))
        fixture.advance(run, torb, zarya, 12)
        self.assertEqual(hp.over_armour.amount, before)
        self.assertIsNone(run.match.combat.bubbles.for_target(zarya))

    def test_particle_beam_empty_magazine_stops_damage_until_refill_and_recovery(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, _ = fixture.arena("Zarya", 5)
        hp = fixture.health(run, torb)
        weapon = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x241)
        ammo = weapon.vars[53]
        self.assertEqual(ammo.value(), 100)
        fixture.advance(run, torb, zarya, 301, defense=1, defender_aim=(0, 0))
        self.assertTrue(3 < ammo.value() < 4)
        self.assertEqual(hp.total, 250)
        fixture.step(run, torb, zarya, fire=16, defense=1, defender_aim=(0, 0))
        source = hp.over_armour.source
        base = dict(hp.now)
        channel = weapon.states[10]
        activation = channel.counter
        fixture.advance(run, torb, zarya, 10, defense=1)
        self.assertTrue(channel.active)
        self.assertTrue(0 < ammo.value() < 1)
        self.assertAlmostEqual(hp.over_armour.amount, 92.44)
        fixture.step(run, torb, zarya, defense=1)
        self.assertEqual(ammo.value(), 0)
        self.assertFalse(channel.active)
        self.assertAlmostEqual(hp.over_armour.amount, 91.9675)
        at_empty = hp.over_armour.amount
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 8.0325)

        for _ in range(72):
            fixture.step(run, torb, zarya, defense=1)
            self.assertEqual(ammo.value(), 0)
            self.assertFalse(channel.active)
            self.assertEqual(hp.over_armour.amount, at_empty)
        fixture.step(run, torb, zarya, defense=1)
        self.assertEqual(ammo.value(), 100)
        self.assertEqual(hp.over_armour.amount, at_empty)
        for _ in range(26):
            fixture.step(run, torb, zarya, defense=1)
            self.assertEqual(ammo.value(), 100)
            self.assertFalse(channel.active)
            self.assertEqual(hp.over_armour.amount, at_empty)
        fixture.step(run, torb, zarya, defense=1)
        self.assertTrue(channel.active)
        self.assertGreater(channel.counter, activation)
        self.assertLess(ammo.value(), 100)
        self.assertAlmostEqual(at_empty - hp.over_armour.amount, .21)
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 8.2425)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)

    def test_particle_beam_kills_full_health_turret_then_retargets_overload(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, other = fixture.arena("Zarya", 100)
        match = run.match
        fight = match.combat
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, zarya, 225)
        turret = fight.turrets[torb.entity]
        turret_hp = fight.vitals[turret.actor.entity]
        self.assertAlmostEqual(turret_hp.total, 250)
        x, y, z = turret.actor.position
        fixture.place(torb, (x, y, z - 1.25))
        fixture.place(zarya, (x, y, z + 5))
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        run.step({torb: (0, 0, 16)})
        source = hp.over_armour.source
        direction = aim(fight._eye(zarya), (x, y + .75, z))
        held = {zarya: (*direction, 1)}
        for _ in range(235):
            run.step(held)
            self.assertEqual(hp.over_armour.amount, 100)
            if turret_hp.dead:
                break
        self.assertTrue(turret_hp.dead)
        self.assertEqual(turret_hp.killer, zarya.body)
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 250)
        self.assertEqual(zarya.stats.value(stats.ELIMINATIONS), 1)
        self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 9.9)
        self.assertGreater(turret.shots, 0)

        run.step(held)
        self.assertNotIn(torb.entity, fight.turrets)
        self.assertEqual(hp.over_armour.amount, 100)
        run.step(held)
        self.assertAlmostEqual(hp.over_armour.amount, 99.16)
        for _ in range(7):
            run.step(held)
        self.assertAlmostEqual(hp.over_armour.amount, 93.28)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 256.72)
        self.assertEqual(zarya.stats.value(stats.ELIMINATIONS), 1)

        fixture.place(zarya, (x + 100, y, z + 100))
        fixture.advance(run, torb, zarya, 120)
        self.assertIsNone(hp.over_armour.source)
        self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 0)
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, zarya, 15)
        self.assertNotIn(torb.entity, fight.turrets)
        for _ in range(630):
            if hero_torbjorn.turret_cooldown_remaining(torb) == 0:
                break
            run.step({})
        self.assertEqual(hero_torbjorn.turret_cooldown_remaining(torb), 0)
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, zarya, 100)
        self.assertNotEqual(fight.turrets[torb.entity].actor.entity, turret.actor.entity)

    def test_particle_grenades_break_projected_barrier_and_spend_overload_without_wall_spill(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, attacker, ally = fixture.arena("Zarya", 5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        self.assertTrue(match.switch_hero(ally, attacker.hero))
        ally.team = torb.team
        attacker.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(ally, (x - 5, y, z))
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        run.step({torb: (0, 0, 16)})
        bubble = self.cast(fixture, run, ally, torb)
        source = hp.over_armour.source
        energy = ally.body_script.component.entity_var(87)
        direction = fixture.body_aim(run, attacker, torb)

        with patch.object(fight, "_blast", wraps=fight._blast) as impacts:
            for remaining in (153, 0):
                for _ in range(85):
                    if bubble.health == remaining:
                        break
                    run.step({attacker: (*direction, 2)})
                self.assertEqual(bubble.health, remaining)
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 0)
                self.assertGreater(energy.value(), 0)
                if remaining:
                    bubble.health = 1
            self.assertIsNone(fight.bubbles.for_target(torb))
            for _ in range(85):
                if hp.over_armour.amount < 100:
                    break
                run.step({attacker: (*direction, 2)})
            self.assertEqual(hp.over_armour.amount, 58)
            self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 42)
            self.assertEqual(len(impacts.call_args_list), 3)

            match.collision_world = World(
                box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                + wall(x - 4, z + 2, x + 4, z + 2, y0=y, y1=y + 4)
            )
            for _ in range(85):
                if len(impacts.call_args_list) == 4:
                    break
                run.step({attacker: (*direction, 2)})
            self.assertEqual(len(impacts.call_args_list), 4)
            self.assertTrue(all(call.args[0].shooter is attacker and call.args[0].blast.amount == 47
                                for call in impacts.call_args_list))
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.over_armour.amount, 58)
        self.assertEqual(hp.now, base)
        self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 42)

    def test_graviton_projectile_charge_launch_and_direct_hit_or_wall(self):
        for covered in (False, True):
            with self.subTest(covered=covered):
                self.check_graviton_projectile_contact(covered)

    def check_graviton_projectile_contact(self, covered):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, _ = fixture.arena("Zarya", 5)
        match = run.match
        fight = match.combat
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        script = zarya.body_script
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost - 1)
        fixture.step(run, torb, zarya, defense=32)
        fixture.advance(run, torb, zarya, 15)
        self.assertEqual(charge.value(), cost - 1)
        self.assertFalse(fight.rockets)
        self.assertEqual(hp.total, 250)

        if covered:
            x, y, z = torb.mover.position
            match.collision_world = World(
                box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                + wall(x - 4, z + 2, x + 4, z + 2, y0=y, y1=y + 4)
            )
        script.set_ult_charge(cost)
        fixture.step(run, torb, zarya, fire=16, defense=32)
        self.assertEqual(charge.value(), 0)
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, zarya)
        self.assertEqual(rocket.definition, 0x04000000000008CE)
        self.assertEqual(rocket.spawn, 0x04000000000003E2)
        self.assertEqual(rocket.speed, 25)
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertEqual(rocket.left, 4)
        self.assertEqual(rocket.direct.amount, 5)
        self.assertEqual(hp.over_armour.amount, 100)
        source = hp.over_armour.source
        with patch.object(fight, "_landed", wraps=fight._landed) as landed:
            for _ in range(45):
                if rocket not in fight.rockets:
                    break
                fixture.step(run, torb, zarya, defense=32)
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(landed.call_count, 1)
        self.assertIs(landed.call_args.args[0], rocket)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.over_armour.amount, 100 if covered else 97.5)
        self.assertEqual(hp.now, base)
        self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 0 if covered else 2.5)
        field, = fight.gravitons.fields
        self.assertIs(field.owner, zarya)
        self.assertEqual(field.radius, 6)
        self.assertEqual(field.end_tick - fight.tick, 250)
        if covered:
            fixture.advance(run, torb, zarya, 20)
            self.assertEqual(hp.over_armour.amount, 100)
            self.assertNotIn(torb.body, fight.gravitons.pulled)
            self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 0)

    def test_graviton_deflect_changes_field_team_and_grasp_prevents_landing(self):
        fixture = defenses.TorbjornDefenseTests()
        for name in ("Genji", "Sigma"):
            with self.subTest(defense=name):
                run, torb, zarya, defender = fixture.arena("Zarya", 5)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                torb.team = zarya.team
                defender.team = 1 - zarya.team
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(match.switch_hero(defender, hero))
                x, y, z = torb.mover.position
                fixture.place(torb, (x, y, z + 2.5))
                fixture.place(defender, (x, y, z))
                fixture.advance(run, torb, defender, 35)
                defender_hp = fixture.health(run, defender)
                defender_health = defender_hp.total
                caster_hp = fixture.health(run, zarya)
                caster_health = caster_hp.total
                hp = fixture.health(run, torb)
                forward = (0, 1600) if name == "Genji" else (0, 0)
                fixture.cast(run, torb, defender, defender_aim=forward)
                cast = fixture.body_aim(run, zarya, defender)
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                run.step({torb: (0, 0, 16), zarya: (*cast, 32), defender: (*forward, 0)})
                original, = fight.rockets
                self.assertIs(original.shooter, zarya)
                self.assertEqual(hp.over_armour.amount, 100)
                with patch.object(fight, "_projectile_hit", wraps=fight._projectile_hit) as hit:
                    for _ in range(45):
                        run.step({defender: (*forward, 0)})
                        if not fight.rockets:
                            break
                self.assertFalse(fight.rockets)
                self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(zarya.body_script.component.entity_var(342).value(), 0)
                if name == "Sigma":
                    grasp = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == 0x1D02)
                    self.assertEqual(grasp.vars[6584].value(), 5)
                    fixture.advance(run, torb, defender, 120, defender_aim=forward)
                    self.assertFalse(fight.gravitons.fields)
                    self.assertFalse(fight.gravitons.pulled)
                    self.assertEqual(hp.over_armour.amount, 100)
                    self.assertEqual(caster_hp.total, caster_health)
                    self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(defender_hp.total, defender_health + 3)
                    continue

                field, = fight.gravitons.fields
                self.assertIs(field.owner, defender)
                self.assertEqual(field.source_body, defender.body)
                self.assertEqual(field.team, defender.team)
                self.assertEqual(hit.call_count, 1)
                target = hit.call_args.args[1]
                self.assertIs(target.owner, torb)
                self.assertGreaterEqual(field.point[1], target.feet[1] + target.eye - combat.HEAD_BELOW)
                self.assertEqual(hit.call_args.args[3:5], (5, False))
                self.assertEqual(hp.over_armour.amount, 97.5)
                fixture.advance(run, torb, defender, 24, defender_aim=forward)
                self.assertIn(torb.body, fight.gravitons.pulled)
                self.assertIn(zarya.body, fight.gravitons.pulled)
                self.assertNotIn(defender.body, fight.gravitons.pulled)
                while run.frame < field.end_tick:
                    run.step({})
                self.assertFalse(fight.gravitons.fields)
                self.assertFalse(fight.gravitons.pulled)
                self.assertAlmostEqual(hp.over_armour.amount, 77.5)
                self.assertAlmostEqual(caster_hp.total, caster_health - 20)
                self.assertEqual(defender_hp.total, defender_health)
                self.assertAlmostEqual(defender.stats.value(stats.HERO_DAMAGE), 42.5)

    def test_graviton_projection_blocks_damage_until_break_but_allows_pull(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, ally = fixture.arena("Zarya", 5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        self.assertTrue(match.switch_hero(ally, zarya.hero))
        ally.team = torb.team
        zarya.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(ally, (x - 9, y, z))
        fixture.advance(run, torb, zarya, 35)
        hp = fixture.health(run, torb)
        bubble = self.cast(fixture, run, ally, torb)
        bubble.health = 5.125
        zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
        fixture.step(run, torb, zarya, fire=16, defense=32)
        for _ in range(45):
            if fight.gravitons.fields:
                break
            fixture.step(run, torb, zarya)
        field, = fight.gravitons.fields
        self.assertAlmostEqual(bubble.health, .125)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 0)
        run.step({})
        self.assertAlmostEqual(bubble.health, .045)
        self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertEqual(hp.over_armour.amount, 100)
        run.step({})
        self.assertIsNone(fight.bubbles.for_target(torb))
        self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 0)
        run.step({})
        self.assertAlmostEqual(hp.over_armour.amount, 99.92)
        self.assertIn(torb.body, fight.gravitons.pulled)
        while run.frame < field.end_tick:
            run.step({})
        self.assertAlmostEqual(hp.over_armour.amount, 80.16)
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 19.84)
        self.assertNotIn(torb.body, fight.gravitons.pulled)

    def test_graviton_projectile_crosses_ghosts_but_lands_on_cryo(self):
        fixture = defenses.TorbjornDefenseTests()
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(hero=name):
                run, torb, zarya, defender = fixture.arena("Zarya", 5)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                zarya.team = 1 - torb.team
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(match.switch_hero(defender, hero))
                defender.team = torb.team
                x, y, z = torb.mover.position
                fixture.place(defender, (x, y, z + 2.5))
                fixture.advance(run, torb, zarya, 35)
                hp = fixture.health(run, torb)
                defender_hp = fixture.health(run, defender)
                defender_health = defender_hp.total
                run.step({defender: (0, 0, 8)})
                self.assertEqual(phase_defenses.phase(defender), (True, name != "Mei"))
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                fixture.step(run, torb, zarya, fire=16, defense=32)
                for _ in range(45):
                    if fight.gravitons.fields:
                        break
                    fixture.step(run, torb, zarya)
                field, = fight.gravitons.fields
                direct = 0 if name == "Mei" else 2.5
                self.assertAlmostEqual(field.point[2], z + (2.5 if name == "Mei" else 0) + .5, places=2)
                self.assertAlmostEqual(hp.over_armour.amount, 100 - direct)
                fixture.advance(run, torb, zarya, 12)
                self.assertIn(torb.body, fight.gravitons.pulled)
                self.assertNotIn(defender.body, fight.gravitons.pulled)
                self.assertEqual(defender_hp.total, defender_health)
                self.assertAlmostEqual(hp.over_armour.amount, 100 - direct - .96)
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), direct + .96)

    def test_graviton_phase_releases_movement_and_reacquires_only_inside(self):
        fixture = defenses.TorbjornDefenseTests()
        cases = (("Reaper", False), ("Moira", False), ("Mei", False),
                 ("Reaper", True), ("Moira", True))
        for name, escape in cases:
            with self.subTest(hero=name, escape=escape):
                run, torb, zarya, defender = fixture.arena("Zarya", 5)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                zarya.team = 1 - torb.team
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(match.switch_hero(defender, hero))
                defender.team = torb.team
                x, y, z = torb.mover.position
                fixture.place(defender, (x + (5.8 if escape else 2.5), y, z + 1))
                fixture.advance(run, torb, zarya, 35)
                hp = fixture.health(run, torb)
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                fixture.step(run, torb, zarya, fire=16, defense=32)
                for _ in range(45):
                    if fight.gravitons.fields:
                        break
                    fixture.step(run, torb, zarya)
                field, = fight.gravitons.fields
                self.assertEqual(hp.over_armour.amount, 97.5)
                fixture.place(torb, (x - 12, y, z))
                fixture.advance(run, torb, zarya, 3)
                self.assertIn(defender.body, fight.gravitons.pulled)
                self.assertLess(defender.mover.velocity[0], 0)
                run.step({defender: (16384, 0, 8)})
                self.assertTrue(phase_defenses.phase(defender)[0])
                self.assertNotIn(defender.body, fight.gravitons.pulled)
                self.assertEqual(defender.mover.velocity, (0, 0, 0))
                credit = zarya.stats.value(stats.HERO_DAMAGE)
                self.assertGreater(credit, 2.5)
                if name != "Mei":
                    # A second accepted command can arrive before the next world update.
                    frame = run.frame + 1
                    defender.take_commands([Command(frame, yaw=16384, forward=127)])
                    velocity = defender.mover.velocity
                    self.assertGreater(velocity[0], 0)
                    pose = tuple(defender.mover.position)
                    run.frame = frame
                    match.update(frame * .016, frame)
                    self.assertNotIn(defender.body, fight.gravitons.pulled)
                    self.assertEqual(defender.mover.velocity, velocity)
                    self.assertEqual(tuple(defender.mover.position), pose)
                    self.assertFalse(defender.mover.driven)
                if escape:
                    for _ in range(30):
                        run.frame += 1
                        match.update(run.frame * .016, run.frame)
                        defender.take_commands([Command(run.frame, yaw=16384, forward=127)])
                    self.assertGreater(defender.mover.position[0], x + field.radius + .5)
                else:
                    fixture.advance(run, torb, zarya, 20)
                self.assertNotIn(defender.body, fight.gravitons.pulled)
                self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), credit)
                if name == "Mei":
                    run.step({defender: (0, 0, 1)})
                else:
                    while phase_defenses.phase(defender)[0] and run.frame < field.end_tick:
                        run.step({})
                self.assertFalse(phase_defenses.phase(defender)[0])
                self.assertEqual(fight.gravitons.fields, [field])
                run.step({})
                if escape:
                    self.assertNotIn(defender.body, fight.gravitons.pulled)
                    self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), credit)
                else:
                    self.assertIn(defender.body, fight.gravitons.pulled)
                    self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), credit + .08)

    def test_graviton_field_pulls_overload_and_releases_at_four_seconds(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, ally = fixture.arena("Zarya", 5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya.team = ally.team = 1 - torb.team
        hp = fixture.health(run, torb)
        ally_hp = fixture.health(run, ally)
        base = dict(hp.now)
        zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
        fixture.step(run, torb, zarya, fire=16, defense=32)
        source = hp.over_armour.source
        for _ in range(45):
            if fight.gravitons.fields:
                break
            fixture.step(run, torb, zarya)
        field, = fight.gravitons.fields
        self.assertEqual(hp.over_armour.amount, 97.5)
        x, y, z = torb.mover.position
        fixture.place(torb, (x + 3, y, z))
        fixture.place(ally, (x - 2, y, z))
        ally_start = tuple(ally.mover.position)
        for _ in range(24):
            run.frame += 1
            match.update(run.frame * .016, run.frame)
            torb.take_commands([Command(run.frame, yaw=16384, forward=127)])
            zarya.take_commands([Command(run.frame)])
            ally.take_commands([Command(run.frame)])
            self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertLess(torb.mover.position[0], x + 2)
        self.assertAlmostEqual(hp.over_armour.amount, 97.5 - 24 * .08)
        fixture.advance(run, torb, zarya, 20)
        self.assertIn(torb.body, fight.gravitons.pulled)
        fixture.step(run, torb, zarya, fire=1)
        fixture.advance(run, torb, zarya, 20)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)
        self.assertIn(torb.body, fight.gravitons.pulled)
        while run.frame < field.end_tick:
            run.step({})
        self.assertFalse(fight.gravitons.fields)
        self.assertNotIn(torb.body, fight.gravitons.pulled)
        self.assertEqual(torb.mover.velocity, (0, 0, 0))
        self.assertAlmostEqual(hp.over_armour.amount, 77.5)
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 22.5)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.now, base)
        self.assertEqual(tuple(ally.mover.position), ally_start)
        self.assertEqual(ally_hp.total, 200)
        released_x = torb.mover.position[0]
        for _ in range(12):
            run.frame += 1
            match.update(run.frame * .016, run.frame)
            torb.take_commands([Command(run.frame, yaw=16384, forward=127)])
        self.assertGreater(torb.mover.position[0], released_x)
        self.assertAlmostEqual(hp.over_armour.amount, 77.5)

    def test_overload_and_molten_can_start_and_hit_while_graviton_pulls(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, other = fixture.arena("Zarya", 5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya.team = 1 - torb.team
        other.team = torb.team
        hp = fixture.health(run, torb)
        enemy_hp = fixture.health(run, zarya)
        zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
        fixture.step(run, torb, zarya, defense=32)
        for _ in range(45):
            if torb.body in fight.gravitons.pulled:
                break
            run.step({})
        field, = fight.gravitons.fields
        self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertLess(hp.total, 250)
        self.assertEqual(hp.over_armour.amount, 0)

        fixture.step(run, torb, zarya, fire=16)
        source = hp.over_armour.source
        self.assertIsNotNone(source)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertIn(torb.body, fight.gravitons.pulled)
        base = dict(hp.now)
        fixture.advance(run, torb, zarya, 45)
        torb.body_script.set_ult_charge(10000)
        fixture.step(run, torb, zarya, fire=32)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
        self.assertIn(torb.body, fight.gravitons.pulled)
        fixture.advance(run, torb, zarya, 45)
        run.step({torb: (16384, 4000, 1)})
        globule, = fight.rockets
        self.assertIs(globule.shooter, torb)
        self.assertEqual(globule.spawn, molten_core.POOL)
        self.assertIn(torb.body, fight.gravitons.pulled)
        for _ in range(100):
            run.step({})
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        self.assertIs(pool.owner, torb)
        self.assertIn(torb.body, fight.gravitons.pulled)

        fixture.place(zarya, pool.position)
        before = enemy_hp.total
        run.step({})
        self.assertAlmostEqual(before - enemy_hp.total, 2.56)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 2.56)
        self.assertIn((torb.entity, zarya.body), fight.molten_burns)
        self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertEqual(hp.over_armour.source, source)
        self.assertLess(hp.over_armour.amount, 100)
        self.assertEqual(hp.now, base)

        while run.frame < field.end_tick - 1:
            run.step({})
        before = enemy_hp.total
        run.step({})
        self.assertNotIn(torb.body, fight.gravitons.pulled)
        self.assertFalse(fight.gravitons.fields)
        self.assertEqual(list(fight.molten_pools.values()), [pool])
        self.assertAlmostEqual(before - enemy_hp.total, 2.56)

    def test_graviton_moves_overload_across_landed_molten_in_both_directions(self):
        fixture = defenses.TorbjornDefenseTests()
        for entering in (True, False):
            with self.subTest(entering=entering):
                run, torb, zarya, molten_caster = fixture.arena("Zarya", 5)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                self.assertTrue(match.switch_hero(molten_caster, torb.hero))
                zarya.team = molten_caster.team = 1 - torb.team
                x, y, z = torb.mover.position
                fixture.place(molten_caster, (x + 10, y, z))
                fixture.advance(run, torb, zarya, 35)
                molten_caster.body_script.set_ult_charge(
                    molten_caster.body_script.component.entity_var(338).value())
                run.step({molten_caster: (0, 4000, 32)})
                fixture.advance(run, torb, zarya, 45)
                run.step({molten_caster: (0, 4000, 1)})
                for _ in range(100):
                    if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                        break
                    run.step({})
                pool, = fight.molten_pools.values()
                self.assertEqual(pool.phase, 1)
                self.assertIs(pool.owner, molten_caster)

                x, y, z = pool.position
                target = next(t for t in fight._targets() if t.owner is torb)
                edge = pool.config["radius"] + target.radius
                start_z = z - edge - .1 if entering else z + edge - .1
                run.step({torb: (0, 0, 16)})
                fixture.place(torb, (x, y, start_z))
                fixture.place(zarya, (x, y, start_z + 4))
                fixture.place(molten_caster, (x + 30, y, z))
                hp = fixture.health(run, torb)
                base = dict(hp.now)
                key = molten_caster.entity, torb.body
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                fixture.step(run, torb, zarya, defense=32)
                armour = hp.over_armour.source
                for _ in range(45):
                    if fight.gravitons.fields:
                        break
                    run.step({})
                field, = fight.gravitons.fields
                self.assertGreater(field.point[2], start_z + .25)
                self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 2.5)
                self.assertEqual(key in fight.molten_burns, not entering)
                old_burn = fight.molten_burns.get(key)
                burn_rate = pool.config["base_dps"] + pool.config["armour_bonus_dps"]
                self.assertEqual(burn_rate, 250)
                for _ in range(16):
                    old_armor = hp.over_armour.amount
                    prior_burn = molten_caster.stats.value(stats.HERO_DAMAGE)
                    prior_field = zarya.stats.value(stats.HERO_DAMAGE)
                    run.step({})
                    self.assertIn(torb.body, fight.gravitons.pulled)
                    self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE) - prior_field, .08)
                    if (key in fight.molten_burns) == entering:
                        break
                else:
                    self.fail("Graviton never pulled Torbjorn across the Molten boundary")
                self.assertGreater(torb.mover.position[2], start_z + .09)
                boundary_burn = burn_rate * .016 if entering else 0
                self.assertAlmostEqual(hp.over_armour.amount, old_armor - .08 - boundary_burn)
                self.assertAlmostEqual(molten_caster.stats.value(stats.HERO_DAMAGE) - prior_burn,
                                       boundary_burn)
                if entering:
                    burn = fight.molten_burns[key]
                    self.assertIn(burn.instance, burn.script.component.instances)
                    self.assertIn(torb.body, pool.targets)
                else:
                    self.assertNotIn(old_burn.instance, old_burn.script.component.instances)
                    self.assertNotIn(torb.body, pool.targets)

                before = hp.over_armour.amount
                burn_credit = molten_caster.stats.value(stats.HERO_DAMAGE)
                field_credit = zarya.stats.value(stats.HERO_DAMAGE)
                for _ in range(8):
                    run.step({})
                burned = burn_rate * .016 * 8 if entering else 0
                self.assertAlmostEqual(molten_caster.stats.value(stats.HERO_DAMAGE), burn_credit + burned)
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), field_credit + .64)
                self.assertAlmostEqual(hp.over_armour.amount, before - burned - .64)
                self.assertEqual(hp.over_armour.source, armour)
                self.assertEqual(hp.now, base)
                self.assertEqual(fight.gravitons.fields, [field])
                self.assertEqual(fight.molten_pools[pool.entity], pool)
                if entering:
                    hp.damage(hp.total - .12, armour_reduction=False)
                    fight._sync_body_health(torb, hp)
                    prior_molten = molten_caster.stats.value(stats.HERO_DAMAGE)
                    prior_graviton = zarya.stats.value(stats.HERO_DAMAGE)
                    run.step({})
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.killer, molten_caster.body)
                    self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE) - prior_graviton, .08)
                    self.assertAlmostEqual(molten_caster.stats.value(stats.HERO_DAMAGE) - prior_molten, .04)
                    self.assertEqual(molten_caster.stats.value(stats.ELIMINATIONS), 1)
                    self.assertEqual(zarya.stats.value(stats.ELIMINATIONS), 0)
                    self.assertNotIn(key, fight.molten_burns)
                    self.assertNotIn(torb.body, fight.gravitons.pulled)
                    self.assertEqual(torb.mover.velocity, (0, 0, 0))
                    self.assertFalse(torb.mover.driven)
                    self.assertEqual(fight.gravitons.fields, [field])

    def test_torbjorn_can_cast_molten_core_and_burn_zarya_while_pulled(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, other = fixture.arena("Zarya", 5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        zarya.team = other.team = 1 - torb.team
        torb_script = torb.body_script
        torb_charge = torb_script.component.entity_var(342)
        cost = torb_script.component.entity_var(338).value()
        torb_script.set_ult_charge(cost)
        zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
        fixture.step(run, torb, zarya, fire=16, defense=32)
        for _ in range(45):
            if torb.body in fight.gravitons.pulled:
                break
            run.step({})
        field, = fight.gravitons.fields
        self.assertIn(torb.body, fight.gravitons.pulled)
        fixture.advance(run, torb, zarya, 45)
        self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertEqual(torb_charge.value(), cost)
        run.step({torb: (16384, 4000, 32)})
        self.assertEqual(torb_charge.value(), 0)
        fixture.advance(run, torb, zarya, 45)
        self.assertIn(torb.body, fight.gravitons.pulled)
        run.step({torb: (16384, 4000, 1)})
        for _ in range(100):
            self.assertIn(torb.body, fight.gravitons.pulled)
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        self.assertIs(pool.owner, torb)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        hp = fixture.health(run, torb)
        zarya_hp = fixture.health(run, zarya)
        zarya_health = zarya_hp.total
        armour = hp.over_armour.amount
        fixture.place(zarya, pool.position)
        for _ in range(8):
            run.step({})
        self.assertAlmostEqual(zarya_hp.total, zarya_health - 20.48)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 20.48)
        self.assertAlmostEqual(hp.over_armour.amount, armour - .64)
        self.assertEqual(torb_charge.value(), 0)
        self.assertEqual(fight.gravitons.fields, [field])
        self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertIn((torb.entity, zarya.body), fight.molten_burns)

    def test_graviton_turret_contact_and_lethal_counterfire_while_owner_is_pulled(self):
        fixture = defenses.TorbjornDefenseTests()
        for contact in ("owner", "turret", "destroy"):
            with self.subTest(contact=contact):
                hits_turret = contact != "owner"
                run, torb, zarya, other = fixture.arena("Zarya", 5)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                zarya.team = other.team = 1 - torb.team
                x, y, z = torb.mover.position
                fixture.place(zarya, (x, y, z + 100))
                fixture.place(other, (x, y, z + 100))
                run.step({torb: (16384, 4000, 8)})
                fixture.advance(run, torb, zarya, 225)
                turret = fight.turrets[torb.entity]
                turret_hp = fight.vitals[turret.actor.entity]
                position = tuple(turret.actor.position)
                self.assertEqual(turret_hp.total, 250)
                self.assertEqual(turret.shots, 0)
                if contact == "destroy":
                    turret_hp.damage(247)
                x, y, z = position
                fixture.place(torb, (x + (0 if hits_turret else 2.5), y, z - 1.5))
                fixture.place(zarya, (x, y, z + 5))
                hp = fixture.health(run, torb)
                caster_hp = fixture.health(run, zarya)
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                direction = fixture.body_aim(run, zarya, turret.actor if hits_turret else torb)
                run.step({torb: (0, 0, 16), zarya: (*direction, 32)})
                for _ in range(45):
                    if fight.gravitons.fields:
                        break
                    run.step({})
                field, = fight.gravitons.fields
                if contact == "destroy":
                    self.assertTrue(turret.actor.dead)
                    self.assertEqual(turret_hp.killer, zarya.body)
                    self.assertNotIn(torb.entity, fight.turrets)
                    self.assertEqual(hp.over_armour.amount, 100)
                    self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 3)
                    self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 9.9)
                    fixture.advance(run, torb, zarya, 45)
                    self.assertIn(torb.body, fight.gravitons.pulled)
                    run.step({torb: (16384, 4000, 8)})
                    self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 9)
                    self.assertFalse(fight.rockets)
                    self.assertNotIn(torb.entity, fight.turrets)
                    while run.frame < field.end_tick:
                        run.step({})
                    self.assertAlmostEqual(hp.over_armour.amount, 80)
                    self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 23)
                    for _ in range(660):
                        if hero_torbjorn.turret_cooldown_remaining(torb) <= 0:
                            break
                        run.step({})
                    self.assertEqual(hero_torbjorn.turret_cooldown_remaining(torb), 0)
                    for frame in range(30):
                        run.step({torb: (16384, 4000, 8 if frame % 4 == 0 else 0)})
                        if fight.rockets:
                            break
                    rocket, = fight.rockets
                    self.assertIs(rocket.shooter, torb)
                    self.assertEqual(rocket.spawn, hero_torbjorn.TURRET)
                    for _ in range(45):
                        if torb.entity in fight.turrets:
                            break
                        run.step({})
                    replacement = fight.turrets[torb.entity]
                    self.assertIsNot(replacement, turret)
                    replacement_hp = fight.vitals[replacement.actor.entity]
                    self.assertEqual(replacement_hp.total, 75)
                    fixture.advance(run, torb, zarya, 225)
                    self.assertAlmostEqual(replacement_hp.total, 250)
                    continue
                turret_health = 245 if hits_turret else 250
                direct = 0 if hits_turret else 2.5
                self.assertEqual(turret_hp.total, turret_health)
                self.assertEqual(hp.over_armour.amount, 100 - direct)
                self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), 5 if hits_turret else 2.5)
                self.assertLess(abs(field.point[2] - position[2]), 2.5)
                shots, before = turret.shots, caster_hp.total
                for _ in range(30):
                    run.step({})
                    self.assertIn(torb.body, fight.gravitons.pulled)
                    if turret.shots > shots:
                        break
                self.assertEqual(turret.shots, shots + 1)
                self.assertEqual(caster_hp.total, before - 14)
                self.assertNotIn(turret.actor.entity, fight.gravitons.pulled)
                self.assertNotIn(turret.actor.entity, fight.gravitons.npc_movers)
                self.assertEqual(tuple(turret.actor.position), position)
                self.assertEqual(turret_hp.total, turret_health)

                caster_hp.damage(caster_hp.total - 1, armour_reduction=False)
                fight._sync_body_health(zarya, caster_hp)
                prior = torb.stats.value(stats.HERO_DAMAGE)
                for _ in range(30):
                    run.step({})
                    if caster_hp.dead:
                        break
                self.assertTrue(caster_hp.dead)
                self.assertEqual(caster_hp.killer, torb.body)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), prior + 1)
                self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                self.assertEqual(fight.gravitons.fields, [field])
                self.assertIn(torb.body, fight.gravitons.pulled)
                while run.frame < field.end_tick:
                    run.step({})
                self.assertFalse(fight.gravitons.fields)
                self.assertNotIn(torb.body, fight.gravitons.pulled)
                self.assertEqual(tuple(turret.actor.position), position)
                self.assertEqual(turret_hp.total, turret_health)
                self.assertAlmostEqual(hp.over_armour.amount, 80 - direct)
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 25 if hits_turret else 22.5)

    def test_graviton_pulls_a_pending_hammer_repair_out_of_range(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, other = fixture.arena("Zarya", 5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        other.team = torb.team
        zarya.team = 1 - torb.team
        x, y, z = torb.mover.position
        distant = (x, y, z + 100)
        fixture.place(zarya, distant)
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, zarya, 225)
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        hp.damage(75)
        self.assertEqual(hp.total, 175)
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, zarya, 45)
        x, y, z = turret.actor.position
        hammer = next(i for i in torb.body_script.component.instances.values() if i.graph.index == 0x168C)
        trace = hammer.graph.node(33)
        offset = hammer.evaluate(trace.field("m_05BE62DD"))
        reach = hammer.evaluate(trace.field("m_radius"))
        target = next(t for t in fight._targets() if t.owner is turret.actor)
        lower, upper = target.segment()
        height = max(lower[1] - y - offset.y, y + offset.y - upper[1], 0)
        edge = math.sqrt((reach + target.radius) ** 2 - height ** 2)
        start = (x - offset.x, y, z - offset.z + edge - .1)
        fixture.place(torb, start)
        fixture.place(zarya, (start[0], y, start[2] + 5))
        zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
        run.step({zarya: (*fixture.body_aim(run, zarya, torb), 32)})
        for _ in range(45):
            if fight.gravitons.fields:
                break
            run.step({})
        field, = fight.gravitons.fields
        fixture.place(zarya, distant)
        direction = aim(fight._eye(torb), (x, y + .4, z))
        run.step({torb: (*direction, 1)})
        contact = fight.hammer_contacts[torb.body]
        self.assertLess(torb.body_script.component.now, contact.begin)
        self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertLess(torb.mover.position[2], start[2] + .1)
        for _ in range(55):
            run.step({torb: (*direction, 0)})
        self.assertGreater(torb.mover.position[2], start[2] + .1)
        self.assertFalse(contact.repaired)
        self.assertEqual(hp.total, 175)
        while run.frame < field.end_tick:
            run.step({})
        self.assertNotIn(torb.body, fight.gravitons.pulled)
        for _ in range(12):
            run.frame += 1
            match.update(run.frame * .016, run.frame)
            torb.take_commands([Command(run.frame, yaw=-32768, forward=127)])
        self.assertLess(torb.mover.position[2], start[2])
        direction = aim(fight._eye(torb), (x, y + .4, z))
        run.step({torb: (*direction, 1)})
        for _ in range(55):
            run.step({torb: (*direction, 0)})
        self.assertEqual(hp.total, 225)

    def test_overlapping_gravitons_retire_lethal_pull_on_the_death_tick(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, first, other = fixture.arena("Zarya", 5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        first.team = other.team = 1 - torb.team
        second = match.add_player(8, "Second Zarya", first.hero.guid, first.team, False)
        in_world(match, second)
        run.players.append(second)
        self.assertTrue(match.switch_hero(second, first.hero))
        second.team = first.team
        second.body_script.ult_seconds = 0
        fixture.place(second, tuple(first.mover.position))
        fixture.advance(run, torb, first, 35)
        hp = fixture.health(run, torb)
        for caster in (first, second):
            caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
        run.step({torb: (0, 0, 16), first: (*fixture.body_aim(run, first, torb), 32),
                  second: (*fixture.body_aim(run, second, torb), 32)})
        for _ in range(45):
            if len(fight.gravitons.fields) == 2 and torb.body in fight.gravitons.pulled:
                break
            run.step({})
        self.assertEqual([field.owner for field in fight.gravitons.fields], [first, second])
        old_body, old_script = torb.body, torb.body_script
        self.assertIn(old_body, fight.gravitons.pulled)
        self.assertGreater(hp.over_armour.amount, 0)
        hp.damage(hp.total - .12, armour_reduction=False)
        fight._sync_body_health(torb, hp)
        prior = [caster.stats.value(stats.HERO_DAMAGE) for caster in (first, second)]
        run.step({torb: (0, 0, 1 | 16)})
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, second.body)
        self.assertAlmostEqual(first.stats.value(stats.HERO_DAMAGE) - prior[0], .08)
        self.assertAlmostEqual(second.stats.value(stats.HERO_DAMAGE) - prior[1], .04)
        self.assertEqual(first.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(second.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(torb.stats.value(stats.DEATHS), 1)
        self.assertIsNone(hp.over_armour.source)
        self.assertNotIn(old_body, fight.gravitons.pulled)
        self.assertFalse(torb.mover.driven)
        self.assertEqual(torb.mover.velocity, (0, 0, 0))
        self.assertEqual(len(fight.gravitons.fields), 2)
        corpse = tuple(torb.mover.position)
        for _ in range(45):
            run.step({torb: (0, 0, 1 | 16)})
            self.assertEqual(tuple(torb.mover.position), corpse)
            self.assertNotIn(old_body, fight.gravitons.pulled)
        for _ in range(660):
            if torb.body != old_body:
                break
            run.step({})
        self.assertNotEqual(torb.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertFalse(fight.gravitons.fields)
        self.assertFalse(fight.gravitons.pulled)
        self.assertEqual(fixture.health(run, torb).total, 250)

    def test_graviton_body_replacement_retires_old_pull_and_checks_new_range(self):
        fixture = defenses.TorbjornDefenseTests()
        for reselect in (True, False):
            with self.subTest(reselect=reselect):
                run, torb, zarya, _ = fixture.arena("Zarya", 5)
                match = run.match
                fight = match.combat
                old_body, old_script = torb.body, torb.body_script
                start = tuple(torb.mover.position)
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                fixture.step(run, torb, zarya, fire=16, defense=32)
                for _ in range(45):
                    if old_body in fight.gravitons.pulled:
                        break
                    run.step({})
                field, = fight.gravitons.fields
                self.assertIn(old_body, fight.gravitons.pulled)
                credit = zarya.stats.value(stats.HERO_DAMAGE)
                self.assertTrue(match.switch_hero(torb, torb.hero if reselect else heroes()[SOLDIER]))
                self.assertNotEqual(torb.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertNotIn(old_body, fight.gravitons.pulled)
                x, y, z = start
                fixture.place(torb, (x + 12, y, z))
                hp = fixture.health(run, torb)
                full = 250 if reselect else 200
                for _ in range(8):
                    run.step({})
                    self.assertEqual(hp.total, full)
                    self.assertFalse(torb.mover.driven)
                    self.assertNotIn(torb.body, fight.gravitons.pulled)
                self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), credit)
                fixture.place(torb, start)
                run.step({})
                self.assertEqual(fight.gravitons.fields, [field])
                self.assertIn(torb.body, fight.gravitons.pulled)
                self.assertNotIn(old_body, fight.gravitons.pulled)
                self.assertAlmostEqual(hp.total, full - .08)
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), credit + .08)

    def test_graviton_field_survives_caster_death_and_retires_on_hero_switch(self):
        fixture = defenses.TorbjornDefenseTests()
        for lifecycle in ("death", "switch", "airborne switch"):
            with self.subTest(lifecycle=lifecycle):
                run, torb, zarya, _ = fixture.arena("Zarya", 5)
                match = run.match
                fight = match.combat
                hp = fixture.health(run, torb)
                caster_body = zarya.body
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                fixture.step(run, torb, zarya, fire=16, defense=32)

                if lifecycle == "airborne switch":
                    rocket, = fight.rockets
                    self.assertIs(rocket.shooter, zarya)
                    self.assertFalse(fight.gravitons.fields)
                    self.assertTrue(match.switch_hero(zarya, heroes()[SOLDIER]))
                    self.assertNotEqual(zarya.body, caster_body)
                    self.assertFalse(fight.rockets)
                    for _ in range(45):
                        run.step({})
                    self.assertFalse(fight.gravitons.fields)
                    self.assertEqual(hp.over_armour.amount, 100)
                    continue

                for _ in range(45):
                    if fight.gravitons.fields:
                        break
                    fixture.step(run, torb, zarya)
                field, = fight.gravitons.fields
                self.assertEqual(field.source_body, caster_body)
                fixture.advance(run, torb, zarya, 45)
                self.assertIn(torb.body, fight.gravitons.pulled)
                if lifecycle == "switch":
                    armor = hp.over_armour.amount
                    credit = zarya.stats.value(stats.HERO_DAMAGE)
                    self.assertTrue(match.switch_hero(zarya, heroes()[SOLDIER]))
                    self.assertNotEqual(zarya.body, caster_body)
                    self.assertFalse(fight.gravitons.fields)
                    run.step({})
                    self.assertNotIn(torb.body, fight.gravitons.pulled)
                    self.assertEqual(torb.mover.velocity, (0, 0, 0))
                    self.assertEqual(hp.over_armour.amount, armor)
                    self.assertEqual(zarya.stats.value(stats.HERO_DAMAGE), credit)
                    continue

                caster_hp = fixture.health(run, zarya)
                caster_hp.damage(caster_hp.total - 1)
                fixture.step(run, torb, zarya, fire=1)
                for _ in range(20):
                    if caster_hp.dead:
                        break
                    run.step({})
                self.assertTrue(caster_hp.dead)
                self.assertEqual(caster_hp.killer, torb.body)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 1)
                self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                self.assertEqual(zarya.body, caster_body)
                self.assertEqual(fight.gravitons.fields, [field])
                armor = hp.over_armour.amount
                credit = zarya.stats.value(stats.HERO_DAMAGE)
                for _ in range(8):
                    run.step({})
                self.assertIn(torb.body, fight.gravitons.pulled)
                self.assertAlmostEqual(hp.over_armour.amount, armor - .64)
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), credit + .64)
                while run.frame < field.end_tick:
                    run.step({})
                self.assertFalse(fight.gravitons.fields)
                self.assertNotIn(torb.body, fight.gravitons.pulled)
                self.assertAlmostEqual(hp.over_armour.amount, 77.5)
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 22.5)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs the five-NPC catalog")
    def test_graviton_field_pulls_and_damages_all_five_talon_bodies(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        fixture = defenses.TorbjornDefenseTests()
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, torb, zarya, other = fixture.arena("Zarya", 5)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                zarya.team = other.team = 1 - torb.team
                x, y, z = torb.mover.position
                match.collision_world = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + wall(x + 3.5, z - 4, x + 3.5, z + 3, y0=y, y1=y + 4)
                )
                for player in run.players:
                    player.mover.body = None
                    player.client.bot_stream = lambda *args: None
                exposed = npc.spawn(match, definition, (x + 2.5, y, z - 1), team=torb.team)
                allied = npc.spawn(match, definition, (x - 2.5, y, z - 1), team=zarya.team)
                covered = npc.spawn(match, definition, (x + 4.5, y, z - 1), team=torb.team)
                bots = (exposed, allied, covered)
                self.assertTrue(all(bot.body_script is not None for bot in bots))
                while run.frame < max(bot.records_from for bot in bots):
                    run.step({})
                before = {bot.entity: (bot.position, fight.vitals[bot.entity].total) for bot in bots}
                hp = fixture.health(run, torb)
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                fixture.step(run, torb, zarya, fire=16, defense=32)
                for _ in range(45):
                    if fight.gravitons.fields:
                        break
                    fixture.step(run, torb, zarya)
                field, = fight.gravitons.fields
                self.assertEqual(hp.over_armour.amount, 97.5)
                exposed_hp = fight.vitals[exposed.entity]
                start, total = before[exposed.entity]
                self.assertEqual(exposed_hp.total, total)
                fixture.advance(run, torb, zarya, 24)
                self.assertIn(exposed.entity, fight.gravitons.pulled)
                self.assertIn(exposed.entity, fight.gravitons.npc_movers)
                self.assertLess(exposed.position[0], start[0] - .8)
                self.assertAlmostEqual(exposed_hp.total, total - 1.92)
                for bot in (allied, covered):
                    self.assertNotIn(bot.entity, fight.gravitons.pulled)
                    self.assertNotIn(bot.entity, fight.gravitons.npc_movers)
                while run.frame < field.end_tick:
                    run.step({})
                self.assertFalse(fight.gravitons.fields)
                self.assertFalse(fight.gravitons.pulled)
                self.assertFalse(fight.gravitons.npc_movers)
                self.assertEqual(exposed.velocity, (0, 0, 0))
                self.assertAlmostEqual(exposed_hp.total, total - 20)
                self.assertAlmostEqual(hp.over_armour.amount, 77.5)
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 42.5)
                final = exposed.position
                for _ in range(8):
                    run.step({})
                self.assertEqual(exposed.position, final)
                self.assertAlmostEqual(exposed_hp.total, total - 20)
                for bot in (allied, covered):
                    self.assertEqual((bot.position, fight.vitals[bot.entity].total), before[bot.entity])

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs the five-NPC catalog")
    def test_graviton_retires_all_five_npc_pulls_on_lethal_graviton_or_molten_tick(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        fixture = defenses.TorbjornDefenseTests()
        run, torb, zarya, molten_caster = fixture.arena("Zarya", 5)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        self.assertTrue(match.switch_hero(molten_caster, torb.hero))
        zarya.team = molten_caster.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(molten_caster, (x + 10, y, z))
        fixture.advance(run, torb, zarya, 35)
        molten_caster.body_script.set_ult_charge(molten_caster.body_script.component.entity_var(338).value())
        run.step({molten_caster: (0, 4000, 32)})
        fixture.advance(run, torb, zarya, 45)
        run.step({molten_caster: (0, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        self.assertIs(pool.owner, molten_caster)
        x, y, z = pool.position
        fixture.place(torb, (x - 4, y, z))
        fixture.place(zarya, (x - 4, y, z + 5))
        fixture.place(molten_caster, (x + 30, y, z))
        for player in run.players:
            player.client.bot_stream = lambda *args: None
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        deaths = []
        zarya.client.queue_bot_death = lambda entity, event: deaths.append(entity)

        targets = []
        for definition in catalog.values():
            outside = npc.spawn(match, definition, (x - 4, y, z - 2), team=torb.team)
            inside = npc.spawn(match, definition, (x - .25, y, z), team=torb.team)
            targets.extend(((outside, zarya, .04), (inside, molten_caster, .12)))
        while run.frame < max(bot.records_from for bot, _, _ in targets):
            run.step({})
        zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
        fixture.step(run, torb, zarya, fire=16, defense=32)
        entities = {bot.entity for bot, _, _ in targets}
        for _ in range(45):
            if entities <= fight.gravitons.pulled:
                break
            run.step({})
        field, = fight.gravitons.fields
        self.assertTrue(entities <= fight.gravitons.pulled)
        self.assertEqual(set(fight.gravitons.npc_movers), entities)
        for bot, killer, remaining in targets:
            burning = (molten_caster.entity, bot.entity) in fight.molten_burns
            self.assertEqual(burning, killer is molten_caster)
            hp = fight.vitals[bot.entity]
            hp.damage(hp.total - remaining, armour_reduction=False)
            fight._sync_body_health(bot, hp)

        graviton_before = zarya.stats.value(stats.HERO_DAMAGE)
        molten_before = molten_caster.stats.value(stats.HERO_DAMAGE)
        run.step({})
        self.assertCountEqual(deaths, entities)
        self.assertEqual(zarya.stats.value(stats.ELIMINATIONS), 5)
        self.assertEqual(molten_caster.stats.value(stats.ELIMINATIONS), 5)
        self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE) - graviton_before, .68)
        self.assertAlmostEqual(molten_caster.stats.value(stats.HERO_DAMAGE) - molten_before, .2)
        self.assertEqual(fight.gravitons.fields, [field])
        self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertTrue(entities.isdisjoint(fight.gravitons.pulled))
        self.assertFalse(fight.gravitons.npc_movers)
        poses = {}
        for bot, killer, _ in targets:
            hp = fight.vitals[bot.entity]
            self.assertTrue(bot.dead)
            self.assertTrue(hp.dead)
            self.assertEqual(hp.killer, killer.body)
            self.assertEqual(hp.death_tick, run.frame)
            self.assertTrue(bot.body_script.component.owner_dead)
            self.assertEqual(bot.velocity, (0, 0, 0))
            poses[bot.entity] = bot.position
        for _ in range(12):
            run.step({})
        self.assertCountEqual(deaths, entities)
        self.assertTrue(entities.isdisjoint(fight.gravitons.pulled))
        self.assertFalse(fight.gravitons.npc_movers)
        self.assertFalse(any(bot.entity in entities for _, bot in fight.respawns))
        for bot, _, _ in targets:
            self.assertIn(bot, match.bots)
            self.assertEqual(bot.position, poses[bot.entity])

    def test_projected_expiry_rejects_early_recast_and_recovers(self):
        fixture, run, owner, zarya, protected = self.arena(projected=True)
        fight = run.match.combat
        first = self.cast(fixture, run, zarya, protected)
        vm = zarya.body_script.component.instances[first.instance_id]
        fixture.advance(run, owner, protected, 145)
        self.assertIsNone(fight.bubbles.for_target(protected))
        aim = fixture.body_aim(run, zarya, protected)
        self.assertGreater(vm.vars[61].value(), 0)
        run.step({zarya: (*aim, 16)})
        self.assertIsNone(fight.bubbles.for_target(protected))
        fixture.advance(run, owner, protected, 530)
        second = self.cast(fixture, run, zarya, protected)
        self.assertNotEqual(second.entity, first.entity)
        self.assertGreater(second.counter, first.counter)

    def test_projected_input_rejects_wrong_team_miss_range_and_wall(self):
        for obstacle in ("team", "aim", "range", "wall"):
            with self.subTest(obstacle=obstacle):
                fixture, run, owner, zarya, protected = self.arena(projected=True)
                x, y, z = zarya.mover.position
                if obstacle == "team":
                    protected.team = owner.team
                elif obstacle == "range":
                    fixture.place(protected, (x + 31, y, z))
                elif obstacle == "wall":
                    front = x + 2
                    run.match.collision_world = World([
                        (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
                        (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
                        (front, y, z-3, front, y, z+3, front, y+4, z+3),
                        (front, y, z-3, front, y+4, z+3, front, y+4, z-3),
                    ], name="projected-bubble-cover")
                yaw, pitch = fixture.body_aim(run, zarya, protected)
                if obstacle == "aim":
                    yaw = yaw - 32768 if yaw >= 0 else yaw + 32768
                run.step({zarya: (yaw, pitch, 16)})
                vm = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x25C)
                self.assertGreater(vm.states[7].counter, 0)
                self.assertEqual(vm.vars[61].value(), 0)
                self.assertIsNone(run.match.combat.bubbles.for_target(protected))

    def test_thrown_turret_hits_personal_and_projected_bubbles_until_expiry(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                fixture, run, owner, zarya, protected = self.arena(projected, distance=100)
                x, y, z = owner.mover.position
                if not projected:
                    fixture.place(run.players[2], (x + 100, y, z + 100))
                fight = run.match.combat
                run.step({owner: (0, 4000, 8)})
                fixture.advance(run, owner, protected, 225)
                turret = fight.turrets[owner.entity]
                self.assertEqual(turret.shots, 0)
                hp = fixture.health(run, protected)
                before = hp.total
                bubble = self.cast(fixture, run, zarya, protected)
                x, y, z = turret.actor.position
                fixture.place(protected, (x, y, z + 7))
                for _ in range(140):
                    run.step({})
                    if fight.bubbles.for_target(protected) is None:
                        break
                    self.assertEqual(hp.total, before)
                    self.assertEqual(bubble.health, 200 - turret.shots * 14)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertIsNone(fight.bubbles.for_target(protected))
                self.assertGreater(turret.shots, 1)
                self.assertGreater(bubble.health, 0)
                for _ in range(30):
                    if hp.total < before:
                        break
                    run.step({})
                self.assertEqual(hp.total, before - 14)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 14)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 14)

    def test_globule_hits_bubble_without_creating_remote_pool(self):
        fixture, run, owner, zarya, protected = self.arena()
        owner.body_script.set_ult_charge(10000)
        fixture.step(run, owner, protected, fire=32)
        fixture.advance(run, owner, protected, 45)
        bubble = self.cast(fixture, run, zarya, protected)
        hp = fixture.health(run, protected)
        before = hp.total
        fixture.step(run, owner, protected, fire=1)
        fixture.advance(run, owner, protected, 20)
        self.assertEqual(bubble.health, 175)
        self.assertEqual(hp.total, before)
        self.assertEqual(run.match.combat.molten_pools, {})

    def test_ceiling_rebound_hits_personal_and_projected_barriers(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                fixture, run, owner, zarya, protected = self.arena(projected, distance=6.5)
                fight = run.match.combat
                x, y, z = owner.mover.position
                run.match.collision_world = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + box(x - 8, x + 8, y + 2.5, y + 3, z - 4, z + 12)
                )
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (0, -7000, 32)})
                fixture.advance(run, owner, protected, 45)
                bubble = self.cast(fixture, run, zarya, protected)
                hp = fixture.health(run, protected)
                before = hp.total
                run.step({owner: (0, -7000, 1)})
                flight, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
                for _ in range(20):
                    if flight.segment_start:
                        break
                    run.step({})
                self.assertIn(flight, fight.rockets)
                self.assertGreater(flight.segment_start, 0)
                self.assertEqual(bubble.health, 200)
                fixture.advance(run, owner, protected, 35)
                self.assertNotIn(flight, fight.rockets)
                self.assertEqual(bubble.health, 175)
                self.assertEqual(hp.total, before)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(fight.molten_pools or fight.molten_burns)
                energy = zarya.body_script.component.entity_var(87).value()
                self.assertGreater(energy, 4)
                self.assertLessEqual(energy, 5)

    def test_actual_landed_molten_damages_bubble_before_body_or_burn(self):
        fixture, run, owner, zarya, protected = self.arena(distance=15)
        fight = run.match.combat
        owner.body_script.set_ult_charge(10000)
        run.step({owner: (0, 4000, 32)})
        fixture.advance(run, owner, protected, 45)
        run.step({owner: (0, 4000, 1)})
        for _ in range(40):
            run.step({})
            if fight.molten_pools:
                break
        pool, = fight.molten_pools.values()
        fixture.place(protected, pool.position)
        bubble = self.cast(fixture, run, zarya, protected)
        hp = fixture.health(run, protected)
        before = hp.total
        key = owner.entity, protected.body
        for _ in range(120):
            run.step({})
            self.assertEqual(hp.total, before)
            self.assertNotIn(key, fight.molten_burns)
            if bubble.health == 0:
                break
        self.assertEqual(bubble.health, 0)
        fixture.advance(run, owner, protected, 5)
        self.assertLess(hp.total, before)
        self.assertIn(key, fight.molten_burns)

    def test_new_personal_and_projected_bubbles_replace_live_molten_burn_until_break(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                fixture, run, owner, zarya, protected = self.arena(projected, distance=15)
                fight = run.match.combat
                owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
                run.step({owner: (0, 4000, 32)})
                fixture.advance(run, owner, protected, 45)
                run.step({owner: (0, 4000, 1)})
                for _ in range(100):
                    if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                        break
                    run.step({})
                pool, = fight.molten_pools.values()
                self.assertEqual(pool.phase, 1)
                x, y, z = pool.position
                fixture.place(protected, pool.position)
                if projected:
                    fixture.place(zarya, (x - 4, y, z))
                hp = fixture.health(run, protected)
                initial_health = hp.total
                fixture.advance(run, owner, protected, 5)
                self.assertLess(hp.total, initial_health)
                key = owner.entity, protected.body
                old_burn = fight.molten_burns[key]

                bubble = self.cast(fixture, run, zarya, protected)
                before = hp.total
                damage_before = owner.stats.value(stats.HERO_DAMAGE)
                run.step({})
                self.assertAlmostEqual(bubble.health, 200 - pool.config["base_dps"] * .016)
                self.assertNotIn(old_burn.instance, old_burn.script.component.instances)
                for _ in range(120):
                    self.assertEqual(hp.total, before)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage_before)
                    self.assertNotIn(key, fight.molten_burns)
                    if bubble.health == 0:
                        break
                    run.step({})
                self.assertEqual(bubble.health, 0)
                self.assertIsNone(fight.bubbles.for_target(protected))
                self.assertGreater(zarya.body_script.component.entity_var(87).value(), 0)
                run.step({})
                self.assertLess(hp.total, before)
                self.assertIsNot(fight.molten_burns[key], old_burn)
                self.assertEqual(list(fight.molten_pools.values()), [pool])

    def test_projected_bubble_protects_torbjorn_from_actual_flashbang(self):
        fixture, run, owner, zarya, caster = self.arena(projected=True)
        zarya.team = owner.team
        caster.team = 1 - owner.team
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        x, y, z = owner.mover.position
        fixture.place(caster, (x, y, z))
        fixture.place(owner, (x, y, z + 3.5))
        for _ in range(40):
            run.step({})
        bubble = self.cast(fixture, run, zarya, owner)
        yaw, pitch = fixture.body_aim(run, caster, owner)
        run.step({caster: (yaw, pitch, 16)})
        fight = run.match.combat
        for _ in range(45):
            run.step({caster: (yaw, pitch, 0)})
            self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(bubble.health, 175)
        self.assertEqual(fixture.health(run, owner).total, 250)

    def test_projected_lifetime_survives_caster_death_and_retires_with_target(self):
        fixture, run, owner, zarya, protected = self.arena(projected=True)
        fight = run.match.combat
        for _ in range(5):
            fixture.step(run, owner, zarya, fire=1)
            fixture.advance(run, owner, zarya, 39)
        self.assertEqual(fixture.health(run, zarya).total, 50)
        bubble = self.cast(fixture, run, zarya, protected)
        fixture.step(run, owner, zarya, fire=1)
        fixture.advance(run, owner, zarya, 39)
        self.assertTrue(fight.is_dead(zarya))
        self.assertIs(fight.bubbles.for_target(protected), bubble)
        fixture.step(run, owner, protected, fire=1)
        fixture.advance(run, owner, protected, 20)
        self.assertEqual(bubble.health, 130)
        self.assertEqual(fixture.health(run, protected).total, 200)
        old_body = protected.body
        self.assertTrue(run.match.switch_hero(protected, protected.hero))
        self.assertNotEqual(protected.body, old_body)
        self.assertIsNone(fight.bubbles.for_target(protected))

    def test_actual_bubble_health_and_phase_identity_are_snapshot_state(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest
        from ow174.game.zarya_bubbles import HERO

        case = ServerSnapshotTests()
        session, _, players = case.scene()
        owner = players[0]
        session.apply("switch_hero", {"account": owner.account_lo, "hero": HERO})
        script = owner.body_script
        for _ in range(20):
            script.command(script.frame + 1, 8)
        bubble = owner.match.combat.bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        first = snapshot_digest(case.capture(session))
        self.assertEqual(first, snapshot_digest(case.capture(session)))
        bubble.health -= 1
        self.assertNotEqual(first, snapshot_digest(case.capture(session)))
        bubble.health += 1
        bubble.start += 1
        self.assertNotEqual(first, snapshot_digest(case.capture(session)))
