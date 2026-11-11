"""Actual Ana Sleep Dart against Torbjorn combat and recovery."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn_defenses as defenses
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, molten_core, npc, npc_control, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs prepared combat inputs")
class TorbjornSleepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self, distance=6):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, ana, other = fixture.arena("Ana", distance)
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        return fixture, run, torb, ana

    def cast(self, fixture, run, torb, ana, *, fire=0):
        fight = run.match.combat
        fixture.step(run, torb, ana, fire=fire, defense=8)
        for _ in range(70):
            if torb.body in fight.sleep:
                return fight.sleep[torb.body]
            fixture.step(run, torb, ana, fire=fire)
        self.fail("actual Sleep Dart did not apply")

    def quick_melee_hit_delay(self):
        fixture, control, attacker, target = self.arena(distance=1.5)
        control_hp = fixture.health(control, target)
        fixture.step(control, attacker, target, fire=0x800)
        start = control.frame
        for _ in range(30):
            if control_hp.total < 200:
                break
            fixture.step(control, attacker, target)
        self.assertEqual(control_hp.total, 170)
        return control.frame - start

    def test_quick_melee_uses_first_admitted_input_after_natural_or_damage_wake(self):
        delay = self.quick_melee_hit_delay()

        for damage_wake in (False, True):
            for held in (False, True):
                with self.subTest(damage_wake=damage_wake, held=held):
                    fixture, run, torb, ana = self.arena(distance=1.5)
                    fight = run.match.combat
                    torb.body_script.ult_seconds = 0
                    torb.body_script.set_ult_charge(0)
                    rifle = next(i for i in torb.body_script.component.instances.values()
                                 if i.graph.index == 0x1683)
                    ana_hp = fixture.health(run, ana)
                    wake, earliest = self.cast(fixture, run, torb, ana)
                    if damage_wake:
                        fixture.advance(run, torb, ana, 45, fire=0x800)
                        hp = fixture.health(run, torb)
                        before = hp.total
                        fixture.step(run, torb, ana, fire=0x800, defense=1)
                        for _ in range(30):
                            if hp.total < before:
                                break
                            fixture.step(run, torb, ana, fire=0x800)
                        self.assertLess(hp.total, before)
                        shortened, _ = fight.sleep[torb.body]
                        self.assertEqual(shortened, max(earliest, fight.tick + math.ceil(.5 / combat.TICK)))
                        self.assertLess(shortened, wake)
                        wake = shortened
                    while run.frame < wake - 1:
                        button = 0x800 if held or run.frame < wake - 3 else 0
                        fixture.step(run, torb, ana, fire=button)
                        self.assertTrue(fight.is_stunned(torb))
                        self.assertEqual(ana_hp.total, 200)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(rifle.vars[53].value(), 18)

                    if not held:
                        fixture.advance(run, torb, ana, 12)
                        self.assertEqual(ana_hp.total, 200)
                    fixture.step(run, torb, ana, fire=0x800)
                    self.assertFalse(fight.is_stunned(torb))
                    admitted = run.frame
                    if held:
                        self.assertEqual(admitted, wake)
                    while run.frame < admitted + delay:
                        self.assertEqual(ana_hp.total, 200)
                        fixture.step(run, torb, ana)
                    self.assertEqual(ana_hp.total, 170)
                    fixture.advance(run, torb, ana, 12)
                    self.assertEqual(ana_hp.total, 170)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 30)
                    self.assertEqual(torb.body_script.component.entity_var(342).value(), 30)
                    self.assertEqual(rifle.vars[53].value(), 18)

    def test_sleep_denies_weapon_select_without_stopping_holstered_rifle_refill(self):
        fixture, run, torb, ana = self.arena(distance=1.5)
        fight = run.match.combat
        rifle = next(instance for instance in torb.body_script.component.instances.values()
                     if instance.graph.index == 0x1683)
        ammo = rifle.vars[53]
        run.step({torb: (16384, 0, 1)})
        fixture.advance(run, torb, ana, 50)
        self.assertEqual(ammo.value(), 17)

        def select(action, fire=0):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            yaw, pitch = fixture.body_aim(run, torb, ana)
            torb.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, action=action, buttons=fire)])
            ana.take_commands([Command(run.frame)])

        select(2)
        wait = rifle.state(25)
        self.assertTrue(wait.active)
        self.assertEqual(wait.ms, 2000)
        counter = wait.counter
        deadline = wait.start + wait.ms
        fixture.advance(run, torb, ana, 35)
        wake, _ = self.cast(fixture, run, torb, ana)
        ana_hp = fixture.health(run, ana)
        self.assertEqual(ana_hp.total, 200)
        while wait.active and torb.body_script.component.now < deadline:
            select(1, fire=1)
            self.assertTrue(fight.is_stunned(torb))
            self.assertEqual(wait.counter, counter)
            self.assertEqual(ana_hp.total, 200)
            if torb.body_script.component.now < deadline:
                self.assertEqual(ammo.value(), 17)
        self.assertFalse(wait.active)
        self.assertEqual(wait.ended_at, deadline)
        self.assertEqual(ammo.value(), 18)
        self.assertTrue(fight.is_stunned(torb))

        while run.frame < wake:
            run.step({})
        self.assertFalse(fight.is_stunned(torb))
        fixture.step(run, torb, ana, fire=1)
        for _ in range(35):
            if ana_hp.total < 200:
                break
            fixture.step(run, torb, ana)
        self.assertEqual(ana_hp.total, 145)
        self.assertEqual(ammo.value(), 18)
        select(1)
        fixture.advance(run, torb, ana, 45)
        fixture.step(run, torb, ana, fire=1)
        for _ in range(25):
            if ana_hp.total < 145:
                break
            fixture.step(run, torb, ana)
        self.assertEqual(ana_hp.total, 75)
        self.assertEqual(ammo.value(), 17)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 125)


    def sleep_flashbang_transition(self, order, *, quick_melee=False):
        mccree = heroes()[0x02E0000000000042]
        fixture, run, torb, ana = self.arena(distance=1.5 if quick_melee else 6)
        caster = run.players[2]
        fire = 0x800 if quick_melee else 0
        if quick_melee:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            ana.team = caster.team = 1 - torb.team
            torb.body_script.ult_seconds = 0
            torb.body_script.set_ult_charge(0)
        self.assertTrue(run.match.switch_hero(caster, mccree))
        x, y, z = torb.mover.position
        fixture.place(caster, (x + 3, y, z))
        fixture.advance(run, torb, ana, 35)
        fight = run.match.combat
        hp = fixture.health(run, torb)

        if order != "dart_after_flash":
            _, earliest = self.cast(fixture, run, torb, ana)
            if order == "late_flash":
                fixture.advance(run, torb, ana, 110, fire=fire)
        before = hp.total
        flash_aim = aim(fight._eye(caster), (x, y + 1, z))
        held = {}
        if quick_melee and order != "dart_after_flash":
            held[torb] = (*fixture.body_aim(run, torb, ana), fire)
        run.step({caster: (*flash_aim, 16), **held})
        for _ in range(45):
            if hp.total < before:
                break
            run.step(held)
        self.assertEqual(before - hp.total, 20)
        impact = fight.tick

        if order == "early_flash":
            self.assertGreater(earliest - impact, 50)
            self.assertNotIn(torb.body, fight.stuns)
            self.assertEqual(fight.sleep[torb.body], (earliest, earliest))
            end = earliest
        elif order == "late_flash":
            self.assertGreater(impact, earliest)
            self.assertNotIn(torb.body, fight.sleep)
            end = fight.stuns[torb.body]
            self.assertEqual(end, impact + 50)
        else:
            flash_end = fight.stuns[torb.body]
            end, _ = self.cast(fixture, run, torb, ana, fire=fire)
            self.assertLess(fight.tick, flash_end)
            self.assertNotIn(torb.body, fight.stuns)
            self.assertEqual(end - fight.tick, math.ceil(5 / combat.TICK))
        return fixture, run, torb, ana, end

    def test_sleep_and_flashbang_keep_only_the_longer_control(self):
        for order in ("early_flash", "late_flash", "dart_after_flash"):
            with self.subTest(order=order):
                fixture, run, torb, ana, end = self.sleep_flashbang_transition(order)
                fight = run.match.combat
                rifle = next(instance for instance in torb.body_script.component.instances.values()
                             if instance.graph.index == 0x1683)
                while run.frame < end - 1:
                    fixture.step(run, torb, ana, fire=1)
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertEqual(rifle.vars[53].value(), 18)
                ana_hp = fixture.health(run, ana)
                self.assertEqual(ana_hp.total, 200)
                fixture.step(run, torb, ana, fire=1)
                self.assertFalse(fight.is_stunned(torb))
                fixture.advance(run, torb, ana, 30)
                self.assertEqual(rifle.vars[53].value(), 17)
                self.assertEqual(ana_hp.total, 130)

    def test_quick_melee_waits_through_sleep_and_flashbang_handoffs(self):
        delay = self.quick_melee_hit_delay()
        for order in ("early_flash", "late_flash", "dart_after_flash"):
            with self.subTest(order=order):
                fixture, run, torb, ana, end = self.sleep_flashbang_transition(order, quick_melee=True)
                fight = run.match.combat
                rifle = next(i for i in torb.body_script.component.instances.values()
                             if i.graph.index == 0x1683)
                ana_hp = fixture.health(run, ana)
                while run.frame < end - 1:
                    fixture.step(run, torb, ana, fire=0x800)
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertEqual(ana_hp.total, 200)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(rifle.vars[53].value(), 18)

                fixture.step(run, torb, ana, fire=0x800)
                self.assertEqual(run.frame, end)
                self.assertFalse(fight.is_stunned(torb))
                while run.frame < end + delay:
                    self.assertEqual(ana_hp.total, 200)
                    fixture.step(run, torb, ana)
                self.assertEqual(ana_hp.total, 170)
                fixture.advance(run, torb, ana, 12)
                self.assertEqual(ana_hp.total, 170)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 30)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 30)
                self.assertEqual(rifle.vars[53].value(), 18)

    def test_dart_denies_weapons_and_skills_for_full_duration_then_recovers(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        hp = fixture.health(run, torb)
        charge = torb.body_script.component.entity_var(338).value()
        torb.body_script.set_ult_charge(charge)
        deadline, earliest = self.cast(fixture, run, torb, ana)
        self.assertEqual(hp.total, 247.5)
        self.assertEqual(deadline - fight.tick, math.ceil(5 / combat.TICK))
        self.assertEqual(earliest - fight.tick, math.ceil(1.5 / combat.TICK))
        ana_hp = fixture.health(run, ana)
        before = ana_hp.total
        while run.frame < deadline - 1:
            fixture.step(run, torb, ana, fire=1 | 8 | 16 | 32)
            self.assertTrue(fight.is_stunned(torb))
            self.assertFalse(torb.body_script.component.held)
        fixture.step(run, torb, ana)
        self.assertFalse(fight.is_stunned(torb))
        self.assertNotIn(torb.body, fight.sleep)
        self.assertEqual(ana_hp.total, before)
        self.assertFalse(fight.turrets or fight.molten_pools)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), charge)
        fixture.step(run, torb, ana, fire=1)
        fixture.advance(run, torb, ana, 35)
        self.assertEqual(ana_hp.total, before - 70)
        fixture.step(run, torb, ana, fire=16)
        fixture.advance(run, torb, ana, 25)
        self.assertEqual(hp.total, 347.5)

    def test_sleep_dart_spends_overload_without_moving_its_expiry(self):
        expiry_age = None
        for sleeping in (False, True):
            with self.subTest(sleeping=sleeping):
                fixture, run, torb, ana = self.arena()
                fight = run.match.combat
                hp = fixture.health(run, torb)
                base = dict(hp.now)
                fixture.step(run, torb, ana, fire=16)
                activated = fight.tick
                source = hp.over_armour.source
                self.assertEqual(hp.over_armour.amount, 100)
                fixture.advance(run, torb, ana, 45)
                if sleeping:
                    deadline, _ = self.cast(fixture, run, torb, ana)
                    self.assertEqual(hp.over_armour.amount, 97.5)
                    self.assertEqual(hp.now, base)
                    self.assertTrue(fight.is_stunned(torb))
                remaining = hp.over_armour.amount
                for _ in range(330):
                    self.assertEqual(hp.over_armour.source, source)
                    self.assertEqual(hp.over_armour.amount, remaining)
                    if sleeping:
                        self.assertTrue(fight.is_stunned(torb))
                    fixture.step(run, torb, ana, fire=16 if sleeping else 0)
                    if hp.over_armour.source is None:
                        break
                self.assertIsNone(hp.over_armour.source)
                self.assertEqual(hp.total, 250)
                self.assertEqual(hp.now, base)
                elapsed = fight.tick - activated
                if sleeping:
                    self.assertEqual(elapsed, expiry_age)
                    self.assertLess(fight.tick, deadline)
                    self.assertTrue(fight.is_stunned(torb))
                else:
                    expiry_age = elapsed

    def test_real_rifle_damage_wakes_after_floor_and_recovery(self):
        for elapsed in (45, 112):
            with self.subTest(elapsed=elapsed):
                fixture, run, torb, ana = self.arena()
                fight = run.match.combat
                deadline, earliest = self.cast(fixture, run, torb, ana)
                fixture.advance(run, torb, ana, elapsed)
                hp = fixture.health(run, torb)
                before = hp.total
                fixture.step(run, torb, ana, defense=1)
                for _ in range(30):
                    if hp.total < before:
                        break
                    fixture.step(run, torb, ana)
                self.assertLess(hp.total, before)
                wake, _ = fight.sleep[torb.body]
                self.assertEqual(wake, max(earliest, fight.tick + math.ceil(.5 / combat.TICK)))
                self.assertLess(wake, deadline)
                while run.frame < wake - 1:
                    fixture.step(run, torb, ana)
                    self.assertTrue(fight.is_stunned(torb))
                fixture.step(run, torb, ana)
                self.assertFalse(fight.is_stunned(torb))

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_all_five_npc_attacks_wake_torbjorn_and_allow_counterfire(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        damage = {"Trooper": 15, "Enforcer": 25, "Sniper": 95, "Assassin": 20, "Heavy Assault": 2.5}
        for definition in catalog.values():
            name = definition.name
            with self.subTest(npc=name):
                fixture, run, torb, ana = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                ana.team = 1 - torb.team
                torb.body_script.ult_seconds = 0
                torb.body_script.set_ult_charge(0)
                x, y, z = torb.mover.position
                fixture.place(ana, (x + 6, y, z))
                distance = 2 if name == "Assassin" else 5
                bot = npc.spawn(match, definition, (x, y, z + distance), team=ana.team, yaw=-32768)
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                deadline, earliest = self.cast(fixture, run, torb, ana)
                target = next(t for t in fight._targets() if t.owner is bot)
                direction = aim(fight._eye(torb), (x, y + target.height * .5, z + distance))
                run.step({torb: (*direction, 0)})
                self.assertEqual(npc_control.start(match, torb), bot.entity)
                hp = fixture.health(run, torb)
                before = hp.total
                for _ in range(180):
                    run.step({torb: (*direction, 0)})
                    if hp.total < before:
                        break
                self.assertEqual(hp.total, before - damage[name])
                wake = max(earliest, fight.tick + math.ceil(combat.ANA_SLEEP_WAKE_RECOVERY / combat.TICK))
                self.assertLess(wake, deadline)
                self.assertEqual(fight.sleep[torb.body], (wake, earliest))
                self.assertEqual(wake == earliest, name in ("Trooper", "Assassin", "Heavy Assault"))
                self.assertEqual(fight.pins.get(bot.entity), torb.body if name == "Assassin" else None)
                npc_control.stop(match)

                rifle = next(i for i in torb.body_script.component.instances.values()
                             if i.graph.index == 0x1683)
                ammo = rifle.vars[53]
                bot_hp = fight.vitals[bot.entity]
                bot_before = bot_hp.total
                run.step({torb: (*direction, 1)})
                self.assertFalse(bot.attack_input)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertTrue(fight.is_stunned(torb))
                self.assertEqual(ammo.value(), 18)
                self.assertFalse(fight.rockets)
                while run.frame < wake - 1:
                    run.step({torb: (*direction, 0)})
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertEqual(hp.total, before - damage[name])
                self.assertEqual(bot_hp.total, bot_before)
                run.step({torb: (*direction, 1)})
                self.assertEqual(run.frame, wake)
                self.assertFalse(fight.is_stunned(torb))
                self.assertNotIn(torb.body, fight.sleep)
                self.assertEqual(ammo.value(), 17)
                fixture.advance(run, torb, ana, 20)
                self.assertEqual(bot_hp.total, bot_before - 70)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 70)

    def attacking_npc_dart(self, definition):
        fixture, run, torb, ana = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        ana.team = torb.team
        x, y, z = torb.mover.position
        fixture.place(ana, (x + 3, y, z))
        distance = 2 if definition.name == "Assassin" else 5
        bot = npc.spawn(match, definition, (x, y, z + distance), team=1 - torb.team, yaw=-32768)
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})

        torb_hp = fixture.health(run, torb)
        bot_hp = fight.vitals[bot.entity]
        torb_start, bot_start = torb_hp.total, bot_hp.total
        torb_aim = fixture.body_aim(run, torb, bot)
        ana_aim = fixture.body_aim(run, ana, bot)
        run.step({torb: (*torb_aim, 0)})
        self.assertEqual(npc_control.start(match, torb), bot.entity)
        for _ in range(180):
            run.step({})
            if torb_hp.total < torb_start:
                break
        self.assertLess(torb_hp.total, torb_start)
        if definition.name == "Assassin":
            self.assertEqual(fight.pins.get(bot.entity), torb.body)

        run.step({ana: (*ana_aim, 8)})
        for _ in range(80):
            if bot_hp.total < bot_start:
                break
            run.step({ana: (*ana_aim, 0)})
        self.assertEqual(bot_start - bot_hp.total, 5)
        self.assertEqual(fight.sleep[bot.entity], (run.frame + 313, run.frame + 94))
        self.assertTrue(fight.is_stunned(bot))
        self.assertFalse(bot.attack_input)
        self.assertNotIn(bot.entity, fight.pins)
        return run, torb, bot, torb_hp, bot_hp, torb_aim

    def test_all_five_attacking_npcs_sleep_and_resume_after_dart(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for definition in catalog.values():
            for early in (False, True):
                with self.subTest(npc=definition.name, early=early):
                    run, torb, bot, torb_hp, bot_hp, direction = self.attacking_npc_dart(definition)
                    fight = run.match.combat
                    deadline, minimum = fight.sleep[bot.entity]
                    settled = torb_hp.total
                    if early:
                        before = bot_hp.total
                        run.step({torb: (*direction, 1)})
                        for _ in range(30):
                            if bot_hp.total < before:
                                break
                            run.step({torb: (*direction, 0)})
                        self.assertEqual(before - bot_hp.total, 70)
                        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)
                        self.assertEqual(torb.body_script.component.entity_var(342).value(), 70)
                        self.assertEqual(fight.sleep[bot.entity], (minimum, minimum))
                        deadline = minimum

                    while run.frame < deadline - 1:
                        run.step({})
                        self.assertTrue(fight.is_stunned(bot))
                        self.assertFalse(bot.attack_input)
                    self.assertEqual(torb_hp.total, settled)
                    run.step({})
                    self.assertNotIn(bot.entity, fight.sleep)
                    self.assertTrue(bot.attack_input)
                    for _ in range(180):
                        if torb_hp.total < settled:
                            break
                        run.step({})
                    self.assertLess(torb_hp.total, settled)
                    if definition.name == "Assassin":
                        self.assertEqual(fight.pins.get(bot.entity), torb.body)

    def test_sleep_retires_on_lethal_rivet_or_npc_removal(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        trooper = next(definition for definition in catalog.values() if definition.name == "Trooper")
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, torb, bot, torb_hp, bot_hp, direction = self.attacking_npc_dart(trooper)
                fight = run.match.combat
                if lethal:
                    bot_hp.now[combat.HEALTH_POOL] = 1
                    fight._sync_body_health(bot, bot_hp)
                    run.step({torb: (*direction, 1)})
                    for _ in range(30):
                        if bot.dead:
                            break
                        run.step({torb: (*direction, 0)})
                    self.assertTrue(bot.dead)
                    self.assertEqual(bot_hp.killer, torb.body)
                else:
                    self.assertEqual(npc.remove_spawned(run.match, entities={bot.entity}), (bot.entity,))
                self.assertNotIn(bot.entity, fight.sleep)
                self.assertFalse(bot.attack_input)
                settled = torb_hp.total
                run.step({})
                self.assertEqual(torb_hp.total, settled)

    def test_graviton_does_not_wake_overload_until_projected_barrier_ends(self):
        for break_barrier in (True, False):
            with self.subTest(break_barrier=break_barrier):
                fixture, run, torb, ana = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                ana.team = 1 - torb.team
                zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
                projector = run.players[2]
                self.assertTrue(match.switch_hero(projector, zarya_hero))
                projector.team = torb.team
                source = match.add_player(8, "Graviton caster", zarya_hero.guid, ana.team, False)
                in_world(match, source)
                run.players.append(source)
                self.assertTrue(match.switch_hero(source, zarya_hero))
                source.team = ana.team
                source.body_script.ult_seconds = 0
                x, y, z = torb.mover.position
                fixture.place(ana, (x + 9, y, z))
                fixture.place(projector, (x - 9, y, z))
                fixture.place(source, (x, y, z + 5))
                fixture.advance(run, torb, ana, 35)
                hp = fixture.health(run, torb)
                base = dict(hp.now)
                fixture.step(run, torb, ana, fire=16)
                armour_source = hp.over_armour.source
                fixture.advance(run, torb, ana, 45)
                deadline, earliest = self.cast(fixture, run, torb, ana)
                self.assertEqual(hp.over_armour.amount, 97.5)
                run.step({projector: (*fixture.body_aim(run, projector, torb), 16)})
                bubble = fight.bubbles.for_target(torb)
                self.assertIsNotNone(bubble)
                self.assertIs(bubble.caster, projector)
                self.assertEqual(bubble.health, 200)
                if break_barrier:
                    bubble.health = 5.125

                source.body_script.set_ult_charge(source.body_script.component.entity_var(338).value())
                run.step({source: (*fixture.body_aim(run, source, torb), 32)})
                for _ in range(45):
                    if fight.gravitons.fields:
                        break
                    run.step({})
                field, = fight.gravitons.fields
                self.assertIs(field.owner, source)
                landed = run.frame
                self.assertAlmostEqual(bubble.health, .125 if break_barrier else 195)
                self.assertEqual(hp.over_armour.amount, 97.5)
                self.assertEqual(fight.sleep[torb.body], (deadline, earliest))

                while hp.over_armour.amount == 97.5 and run.frame < field.end_tick:
                    self.assertEqual(fight.sleep[torb.body], (deadline, earliest))
                    self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)
                    fixture.step(run, torb, ana, fire=1)
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertIn(torb.body, fight.gravitons.pulled)
                first_damage = run.frame
                self.assertLess(first_damage, field.end_tick)
                self.assertIsNone(fight.bubbles.for_target(torb))
                self.assertAlmostEqual(hp.over_armour.amount, 97.42)
                self.assertAlmostEqual(source.stats.value(stats.HERO_DAMAGE), .08)
                if break_barrier:
                    self.assertEqual(first_damage - landed, 3)
                    self.assertEqual(bubble.health, 0)
                else:
                    self.assertGreater(bubble.health, 0)
                    self.assertGreater(first_damage, earliest)

                wake = max(earliest, first_damage + math.ceil(combat.ANA_SLEEP_WAKE_RECOVERY / combat.TICK))
                self.assertEqual(fight.sleep[torb.body], (wake, earliest))
                self.assertLess(wake, deadline)
                ana_hp = fixture.health(run, ana)
                ana_health = ana_hp.total
                while run.frame < wake - 2:
                    fixture.step(run, torb, ana, fire=1)
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertEqual(fight.sleep[torb.body], (wake, earliest))
                fixture.step(run, torb, ana)
                self.assertTrue(fight.is_stunned(torb))
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                fixture.step(run, torb, ana, fire=1)
                self.assertEqual(run.frame, wake)
                self.assertFalse(fight.is_stunned(torb))
                self.assertIn(torb.body, fight.gravitons.pulled)
                fixture.advance(run, torb, ana, 35)
                self.assertEqual(ana_hp.total, ana_health - 70)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)
                self.assertEqual(hp.over_armour.source, armour_source)
                self.assertEqual(hp.now, base)

    def test_miss_friendly_and_static_cover_reject_dart(self):
        for obstacle in ("miss", "team", "wall"):
            with self.subTest(obstacle=obstacle):
                fixture, run, torb, ana = self.arena()
                if obstacle == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    ana.team = torb.team
                if obstacle == "wall":
                    x, y, z = torb.mover.position
                    run.match.collision_world = World(
                        box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                        + wall(x - 6, z + 3, x + 6, z + 3, y0=y, y1=y + 4)
                    )
                kwargs = {"defender_aim": (0, 0)} if obstacle == "miss" else {}
                fixture.step(run, torb, ana, defense=8, **kwargs)
                fixture.advance(run, torb, ana, 70, **kwargs)
                self.assertFalse(run.match.combat.sleep)
                self.assertEqual(fixture.health(run, torb).total, 250)

    def test_allied_winston_shell_consumes_sleep_dart(self):
        fixture, run, torb, ana = self.arena(8)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ana.team = 1 - torb.team
        hero = 0x02E0000000000009
        winston = run.match.add_player(8, "Winston", hero, torb.team, False)
        in_world(run.match, winston)
        run.players.append(winston)
        run.match.switch_hero(winston, heroes()[hero])
        x, y, z = torb.mover.position
        fixture.place(winston, (x + 2, y, z))
        fixture.advance(run, torb, ana, 25)
        run.step({winston: (0, 0, 16)})
        fixture.advance(run, torb, ana, 15)
        shell, = run.match.combat.world_bubbles.actors.values()
        fixture.step(run, torb, ana, defense=8)
        fixture.advance(run, torb, ana, 70)
        self.assertEqual(shell.health, 695)
        self.assertEqual(fixture.health(run, torb).total, 250)
        self.assertFalse(run.match.combat.sleep)

    def test_sleep_dart_clears_buffered_molten_press_on_passive_charge_cap_frame(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        script = torb.body_script
        script.ult_seconds = 0
        run.step({})
        script.ult_seconds = 60
        run.step({})
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost - 4)
        cap = run.frame + 63
        ultimate, = script.ultimates
        while run.frame < cap - 25:
            run.step({})
        fixture.step(run, torb, ana, defense=8)
        while run.frame < cap - 9:
            fixture.step(run, torb, ana)
        self.assertFalse(fight.is_stunned(torb))
        fixture.step(run, torb, ana, fire=32)
        self.assertEqual(run.frame, cap - 8)
        self.assertEqual(charge.value(), cost - 4)
        self.assertTrue(ultimate.presses)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)

        while run.frame < cap - 1:
            fixture.step(run, torb, ana)
        self.assertFalse(fight.is_stunned(torb))
        self.assertTrue(ultimate.presses)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIn(torb.body, fight.sleep)
        self.assertFalse(ultimate.presses)
        self.assertEqual(charge.value(), cost - 4)
        torb.take_commands([Command(run.frame)])
        self.assertEqual(charge.value(), cost)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.molten_pools)
        deadline, _ = fight.sleep[torb.body]
        self.assertEqual(deadline - math.ceil(5 / combat.TICK), cap)
        self.assertEqual(fixture.health(run, torb).total, 247.5)
        while run.frame < deadline:
            run.step({})
            self.assertEqual(charge.value(), cost)
            self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.is_stunned(torb))
        self.assertFalse(ultimate.presses)
        self.assertFalse(fight.molten_pools)

        x, y, z = torb.mover.position
        fixture.place(ana, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 32)})
        self.assertEqual(charge.value(), 0)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        fixture.advance(run, torb, ana, 45)
        run.step({torb: (0, 4000, 1)})
        fixture.advance(run, torb, ana, 65)
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, torb)

    def test_sleep_dart_denies_globule_on_molten_rivet_refill_frame(self):
        for impact_age in (45, 46):
            with self.subTest(impact_age=impact_age):
                fixture, run, torb, ana = self.arena()
                fight = run.match.combat
                script = torb.body_script
                rifle = next(item for item in script.component.instances.values()
                             if item.graph.index == 0x1683)
                ammo = rifle.vars[53]
                charge = script.component.entity_var(342)
                run.step({torb: (16384, 0, 1)})
                fixture.advance(run, torb, ana, 50)
                self.assertEqual(ammo.value(), 17)
                run.step({torb: (0, 0, 0x400)})
                fixture.advance(run, torb, ana, 19)
                self.assertTrue(rifle.state(8).active)
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({torb: (16384, 4000, 32)})
                cast = run.frame
                self.assertEqual(charge.value(), 0)
                self.assertFalse(rifle.state(8).active)
                fixture.advance(run, torb, ana, impact_age - 25)
                dart_aim = fixture.body_aim(run, ana, torb)
                run.step({ana: (*dart_aim, 8)})
                while run.frame < cast + 44:
                    run.step({ana: (*dart_aim, 0)})
                self.assertEqual(ammo.value(), 17)
                self.assertFalse(fight.is_stunned(torb))

                for age in (45, 46):
                    run.step({torb: (16384, 4000, 1 if age == 46 else 0),
                              ana: (*dart_aim, 0)})
                    self.assertEqual(run.frame, cast + age)
                    self.assertEqual(fight.is_stunned(torb), age >= impact_age)
                    self.assertEqual(ammo.value(), 17 if age == 45 else 18)
                    self.assertFalse(any(rocket.shooter is torb for rocket in fight.rockets))
                self.assertFalse(fight.molten_pools)
                self.assertEqual(fixture.health(run, torb).total, 247.5)
                _, earliest = fight.sleep[torb.body]
                self.assertEqual(fight.sleep[torb.body][0] - math.ceil(5 / combat.TICK), cast + impact_age)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)

                for _ in range(40):
                    run.step({torb: (16384, 4000, 1)})
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertFalse(fight.molten_pools)
                fixture.step(run, torb, ana, defense=1)
                for _ in range(20):
                    if fight.sleep[torb.body][0] == earliest:
                        break
                    fixture.step(run, torb, ana)
                self.assertEqual(fight.sleep[torb.body], (earliest, earliest))
                self.assertLess(fixture.health(run, torb).total, 247.5)
                while run.frame < earliest - 1:
                    run.step({torb: (16384, 4000, 1)})
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertFalse(fight.molten_pools)
                run.step({})
                self.assertFalse(fight.is_stunned(torb))
                self.assertFalse(fight.molten_pools)
                run.step({torb: (16384, 4000, 1)})
                fixture.advance(run, torb, ana, 55)
                pool, = fight.molten_pools.values()
                self.assertIs(pool.owner, torb)
                self.assertEqual(ammo.value(), 18)
                while run.frame < cast + 411:
                    run.step({})
                fixture.step(run, torb, ana, fire=1)
                ana_hp = fixture.health(run, ana)
                for _ in range(25):
                    if ana_hp.total < 200:
                        break
                    fixture.step(run, torb, ana)
                self.assertEqual(ana_hp.total, 130)
                self.assertEqual(ammo.value(), 17)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)


    def test_molten_times_out_during_sleep_and_only_fresh_rivet_fires_after_wake(self):
        for damage_wake in (False, True):
            with self.subTest(damage_wake=damage_wake):
                fixture, run, torb, ana = self.arena()
                fight = run.match.combat
                script = torb.body_script
                rifle = next(item for item in script.component.instances.values()
                             if item.graph.index == 0x1683)
                ammo = rifle.vars[53]
                ultimate, = script.ultimates
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({torb: (16384, 4000, 32)})
                cast = run.frame
                self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
                molten = next(item for item in script.component.instances.values()
                              if item.graph.index == 0x16DA)
                duration = molten.state(11)
                while run.frame < cast + 320:
                    run.step({})
                run.step({torb: (16384, 4000, 1)})
                fixture.advance(run, torb, ana, 4)
                fixture.step(run, torb, ana, defense=8)
                while run.frame < cast + 350:
                    fixture.step(run, torb, ana)
                self.assertTrue(fight.is_stunned(torb))
                full_wake, earliest = fight.sleep[torb.body]
                self.assertEqual(full_wake - math.ceil(5 / combat.TICK), cast + 350)
                self.assertGreater(earliest, cast + 412)
                self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
                self.assertTrue(duration.active)
                original_start, original_counter = duration.start, duration.counter
                pool, = fight.molten_pools.values()
                self.assertIs(pool.owner, torb)
                self.assertEqual(ammo.value(), 18)

                for _ in range(45):
                    run.step({torb: (16384, 4000, 1 | 2 | 32)})
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertEqual(list(fight.molten_pools.values()), [pool])
                if damage_wake:
                    fixture.step(run, torb, ana, defense=1)
                for _ in range(25):
                    fixture.step(run, torb, ana, fire=1)
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertEqual(ammo.value(), 18)
                    self.assertEqual(list(fight.molten_pools.values()), [pool])
                    self.assertFalse(any(rocket.shooter is torb for rocket in fight.rockets))
                self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
                self.assertFalse(duration.active)
                self.assertFalse(molten.state(26).active)
                self.assertEqual((duration.start, duration.counter), (original_start, original_counter))
                wake = earliest if damage_wake else full_wake
                self.assertEqual(fight.sleep[torb.body][0], wake)
                self.assertFalse(ultimate.presses)
                ana_hp = fixture.health(run, ana)
                self.assertEqual(ana_hp.total, 200)

                while run.frame < wake + 4:
                    run.step({})
                self.assertFalse(fight.is_stunned(torb))
                self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
                self.assertEqual(ammo.value(), 18)
                self.assertEqual(ana_hp.total, 200)
                self.assertEqual(list(fight.molten_pools.values()), [pool])
                fixture.step(run, torb, ana, fire=1)
                self.assertEqual(ammo.value(), 17)
                for _ in range(25):
                    if ana_hp.total < 200:
                        break
                    fixture.step(run, torb, ana)
                self.assertEqual(ana_hp.total, 130)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)
                self.assertEqual(list(fight.molten_pools.values()), [pool])


    def test_existing_molten_stops_firing_during_sleep_and_resumes_after_wake(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
        run.step({torb: (0, 4000, 32)})
        fixture.advance(run, torb, ana, 45)
        run.step({torb: (0, 4000, 1)})
        fixture.advance(run, torb, ana, 35)
        pool, = fight.molten_pools.values()
        _, earliest = self.cast(fixture, run, torb, ana)
        for _ in range(45):
            run.step({torb: (0, 4000, 1)})
            self.assertTrue(fight.is_stunned(torb))
            self.assertEqual(list(fight.molten_pools.values()), [pool])
        fixture.step(run, torb, ana, defense=1)
        while run.frame < earliest - 1:
            run.step({torb: (0, 4000, 1)})
        run.step({})
        self.assertFalse(fight.is_stunned(torb))
        self.assertEqual(list(fight.molten_pools.values()), [pool])
        run.step({})
        run.step({torb: (0, 4000, 1)})
        fixture.advance(run, torb, ana, 40)
        self.assertEqual(len(fight.molten_pools), 2)

    def test_airborne_molten_lands_and_burns_while_caster_is_asleep(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
        run.step({torb: (0, -6000, 32)})
        fixture.advance(run, torb, ana, 45)
        run.step({torb: (0, -6000, 1)})
        for _ in range(15):
            if any(rocket.spawn == molten_core.POOL for rocket in fight.rockets):
                break
            run.step({})
        globule, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
        self.assertIs(globule.shooter, torb)
        self.assertFalse(fight.molten_pools)

        sleep = self.cast(fixture, run, torb, ana)
        self.assertIn(globule, fight.rockets)
        self.assertFalse(fight.molten_pools)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            for _ in range(160):
                self.assertEqual(fight.sleep[torb.body], sleep)
                if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                    break
                run.step({torb: (0, -6000, 1)})
            pool, = fight.molten_pools.values()
            self.assertEqual(pool.phase, 1)
            self.assertIs(pool.owner, torb)
            self.assertNotIn(globule, fight.rockets)
            hp = fixture.health(run, ana)
            self.assertEqual(hp.total, 200)
            fixture.place(ana, pool.position)
            for _ in range(8):
                run.step({torb: (0, -6000, 1)})
                self.assertTrue(fight.is_stunned(torb))
                self.assertEqual(fight.sleep[torb.body], sleep)
                self.assertEqual(list(fight.molten_pools.values()), [pool])
            self.assertAlmostEqual(hp.total, 179.52)
            self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 20.48)
            self.assertIn((torb.entity, ana.body), fight.molten_burns)
            x, y, z = pool.position
            fixture.place(ana, (x + 20, y, z))
            deadline, _ = sleep
            while run.frame < deadline - 1:
                run.step({torb: (0, -6000, 1)})
                self.assertTrue(fight.is_stunned(torb))
                self.assertEqual(fight.sleep[torb.body], sleep)
            self.assertFalse(any(call.args[0] is torb for call in shots.call_args_list))
            run.step({})
            self.assertEqual(fight.tick, deadline)
            self.assertFalse(fight.is_stunned(torb))
            self.assertNotIn(torb.body, fight.sleep)
            run.step({torb: (0, 4000, 1)})
            launches = [call for call in shots.call_args_list if call.args[0] is torb]
            self.assertEqual(len(launches), 1)
            self.assertEqual(launches[0].args[2].instance.graph.index, 0x16DA)
        fixture.advance(run, torb, ana, 40)
        self.assertEqual(len(fight.molten_pools), 2)
        self.assertIn(pool.entity, fight.molten_pools)

    def test_landed_molten_kills_ana_without_waking_sleeping_caster(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
        run.step({torb: (0, 4000, 32)})
        fixture.advance(run, torb, ana, 45)
        run.step({torb: (0, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            fixture.step(run, torb, ana)
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)

        deadline, earliest = self.cast(fixture, run, torb, ana)
        hp = fixture.health(run, ana)
        initial_health = hp.total
        self.assertEqual(initial_health, hp.maximum)
        fixture.place(ana, pool.position)
        for _ in range(160):
            run.step({torb: (0, 4000, 1)})
            self.assertTrue(fight.is_stunned(torb))
            self.assertEqual(fight.sleep[torb.body], (deadline, earliest))
            self.assertEqual(list(fight.molten_pools.values()), [pool])
            if hp.dead:
                break
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, torb.body)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), initial_health)
        self.assertNotIn((torb.entity, ana.body), fight.molten_burns)

        while run.frame < deadline - 1:
            run.step({})
            self.assertTrue(fight.is_stunned(torb))
        run.step({})
        self.assertFalse(fight.is_stunned(torb))
        self.assertNotIn(torb.body, fight.sleep)

    def test_deployed_turret_kills_ana_without_waking_sleeping_owner(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        ana_position = tuple(ana.mover.position)
        x, y, z = torb.mover.position
        fixture.place(ana, (x + 100, y, z + 100))
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, ana, 225)
        turret = fight.turrets[torb.entity]
        self.assertEqual(fight.vitals[turret.actor.entity].total, 250)
        self.assertEqual(turret.shots, 0)

        fixture.place(ana, ana_position)
        hp = fixture.health(run, ana)
        self.assertEqual(hp.total, 200)
        deadline, earliest = self.cast(fixture, run, torb, ana)
        shots = turret.shots
        before = hp.total
        self.assertFalse(hp.dead)
        while fight.tick < deadline - 1 and not hp.dead:
            fixture.step(run, torb, ana, fire=1 | 8 | 16 | 32)
            self.assertTrue(fight.is_stunned(torb))
            self.assertEqual(fight.sleep[torb.body], (deadline, earliest))
            self.assertFalse(torb.body_script.component.held)
            self.assertIs(fight.turrets[torb.entity], turret)
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, torb.body)
        self.assertEqual(turret.shots - shots, math.ceil(before / 14))
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 200)
        self.assertEqual(fixture.health(run, torb).total, 247.5)
        while fight.tick < deadline - 1:
            run.step({})
            self.assertTrue(fight.is_stunned(torb))
        run.step({})
        self.assertNotIn(torb.body, fight.sleep)
        self.assertFalse(fight.is_stunned(torb))

    def test_turret_kill_fills_charge_in_sleep_and_held_molten_starts_at_wake(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        ana_position = tuple(ana.mover.position)
        x, y, z = torb.mover.position
        fixture.place(ana, (x + 100, y, z + 100))
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, ana, 225)
        turret = fight.turrets[torb.entity]
        self.assertEqual(turret.shots, 0)
        fixture.place(ana, ana_position)
        wake, earliest = self.cast(fixture, run, torb, ana)
        hp = fixture.health(run, ana)
        self.assertGreater(hp.total, 14)
        hp.damage(hp.total - 14, armour_reduction=False)
        script = torb.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(338).value()
        charge = script.component.entity_var(342)
        ultimate, = script.ultimates
        script.set_ult_charge(cost - 14)
        shots = turret.shots
        credit = torb.stats.value(stats.HERO_DAMAGE)
        for _ in range(24):
            if hp.dead:
                break
            run.step({torb: (0, 4000, 32)})
            self.assertEqual(fight.sleep[torb.body], (wake, earliest))
            self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
            if not hp.dead:
                self.assertEqual(charge.value(), cost - 14)
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, torb.body)
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), credit + 14)
        self.assertEqual(charge.value(), cost)
        self.assertLess(run.frame, earliest)

        while run.frame < wake - 1:
            run.step({torb: (0, 4000, 32)})
            self.assertEqual(fight.sleep[torb.body], (wake, earliest))
            self.assertEqual(charge.value(), cost)
            self.assertFalse(ultimate.presses or ultimate.flags & nodes.Ability.RUNNING)
            self.assertFalse(fight.molten_pools)
        run.step({torb: (0, 4000, 32)})
        self.assertEqual(run.frame, wake)
        self.assertNotIn(torb.body, fight.sleep)
        self.assertEqual(charge.value(), 0)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        for _ in range(45):
            run.step({})
        run.step({torb: (0, 4000, 1)})
        for _ in range(65):
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, torb)
        self.assertIs(fight.turrets[torb.entity], turret)

    def test_sleep_dart_hits_deployed_turret_without_sleeping_it_or_its_owner(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        fixture.place(ana, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, ana, 225)
        turret = fight.turrets[torb.entity]
        turret_hp = fight.vitals[turret.actor.entity]
        self.assertEqual(turret_hp.total, 250)
        self.assertEqual(turret.shots, 0)
        x, y, z = turret.actor.position
        fixture.place(torb, (x, y, z - 2))
        fixture.place(ana, (x, y, z + 6))
        owner_hp = fixture.health(run, torb)
        ana_hp = fixture.health(run, ana)
        direction = aim(fight._eye(ana), (x, y + .5, z))
        run.step({ana: (*direction, 8)})
        for _ in range(70):
            if turret_hp.total < 250:
                break
            run.step({ana: (*direction, 0)})
        self.assertEqual(turret_hp.total, 245)
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 5)
        self.assertEqual(owner_hp.total, 250)
        self.assertFalse(fight.sleep)
        self.assertFalse(fight.is_stunned(turret.actor) or fight.is_stunned(torb))
        shots = turret.shots
        before = ana_hp.total
        fixture.advance(run, torb, ana, 20)
        self.assertGreater(turret.shots, shots)
        self.assertEqual(before - ana_hp.total, 14 * (turret.shots - shots))
        self.assertIs(fight.turrets[torb.entity], turret)

    def test_sleep_dart_cancels_pending_repair_and_fresh_swing_recovers(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        fixture.place(ana, (x, y, z + 60))
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, ana, 225)
        turret = fight.turrets[torb.entity]
        x, y, z = turret.actor.position
        fixture.place(torb, (x - 1.6, y, z))
        fixture.place(ana, (x, y, z + 6))
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        rifle_aim = aim(fight._eye(ana), (x, y + .6, z))
        run.step({ana: (*rifle_aim, 1)})
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, ana, 58)
        self.assertLess(hp.total, 200)
        before = hp.total
        fixture.place(ana, (x - 1.6, y, z + 6))
        fixture.advance(run, torb, ana, 3)
        repair_aim = aim(fight._eye(torb), (x, y + .4, z))
        dart_aim = fixture.body_aim(run, ana, torb)
        run.step({ana: (*dart_aim, 8)})
        for _ in range(12):
            run.step({ana: (*dart_aim, 0)})
        self.assertFalse(fight.is_stunned(torb))
        run.step({torb: (*repair_aim, 1), ana: (*dart_aim, 0)})
        contact = fight.hammer_contacts[torb.body]
        self.assertLess(torb.body_script.component.now, contact.begin)
        for _ in range(70):
            if torb.body in fight.sleep:
                break
            run.step({ana: (*dart_aim, 0)})
        self.assertIn(torb.body, fight.sleep)
        deadline, _ = fight.sleep[torb.body]
        self.assertTrue(contact.cancelled)
        self.assertFalse(contact.repaired)
        self.assertEqual(hp.total, before)
        fixture.place(ana, (x, y, z + 60))
        while fight.tick < deadline - 1:
            run.step({torb: (*repair_aim, 1)})
            self.assertTrue(fight.is_stunned(torb))
            self.assertEqual(hp.total, before)
        run.step({})
        self.assertFalse(fight.is_stunned(torb))
        self.assertEqual(hp.total, before)
        run.step({torb: (*repair_aim, 1)})
        for _ in range(35):
            run.step({})
        self.assertIs(fight.turrets[torb.entity], turret)
        self.assertEqual(hp.total, before + 50)

    def test_lethal_rifle_hit_retires_sleep_before_wake(self):
        fixture, run, torb, ana = self.arena()
        fight = run.match.combat
        for _ in range(3):
            fixture.step(run, torb, ana, defense=1)
            fixture.advance(run, torb, ana, 58)
        hp = fixture.health(run, torb)
        self.assertGreater(hp.total, 5)
        self.assertLess(hp.total, 70)
        _, earliest = self.cast(fixture, run, torb, ana)
        fixture.advance(run, torb, ana, 35)
        for _ in range(50):
            if hp.dead:
                break
            fixture.step(run, torb, ana, defense=1)
        self.assertTrue(hp.dead, hp.total)
        self.assertLess(run.frame, earliest)
        self.assertNotIn(torb.body, fight.sleep)
