"""Actual Sombra Hack against Torbjorn skills and return fire."""

import math
import os
import unittest
from dataclasses import replace

import test_game_torbjorn_defenses as defenses
import test_game_torbjorn_orisa as orisa_tests
from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box, wall

from ow174.game import combat, hero_torbjorn, molten_core, reinhardt_barrier, sigma_barrier, sombra_control
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs prepared combat inputs")
class TorbjornHackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self, distance=3):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, sombra, other = fixture.arena("Sombra", distance)
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        return fixture, run, torb, sombra

    def deployed_turret_arena(self, shield_hero):
        fixture = defenses.TorbjornDefenseTests()
        run = Run(KANEZAKA_DM, names=("Torbjorn", "Sombra", "Barrier owner", "Target"))
        torb, sombra, shield_owner, bait = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        for player, hero in ((torb, hero_torbjorn.HERO), (sombra, sombra_control.HERO),
                             (shield_owner, shield_hero)):
            self.assertTrue(match.switch_hero(player, heroes()[hero]))
        match.bots.clear()
        match._pending_bot_scripts.clear()
        bait.team = sombra.team = 1 - torb.team
        shield_owner.team = torb.team
        x, floor, z = torb.mover.position
        for player in (sombra, shield_owner, bait):
            fixture.place(player, (x + 100, floor, z + 100))
        fixture.advance(run, torb, sombra, 35)
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sombra, 225)
        turret = match.combat.turrets[torb.entity]
        return fixture, run, turret

    @staticmethod
    def ability(sombra):
        instance = next(i for i in sombra.body_script.component.instances.values()
                        if i.graph.index == sombra_control.HACK)
        return instance.state(53)

    def cast(self, fixture, run, torb, sombra):
        fight = run.match.combat
        for _ in range(65):
            fixture.step(run, torb, sombra, defense=2)
            if fight.sombra_hacks.silenced(torb.body, fight.tick):
                return fight.sombra_hacks.active[torb.body][-1][2]
        self.fail("actual Hack did not apply")

    def launch_molten_pool(self, fixture, run, torb, sombra):
        fight = run.match.combat
        torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
        run.step({torb: (0, 4000, 32)})
        fixture.advance(run, torb, sombra, 45)
        run.step({torb: (0, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            fixture.step(run, torb, sombra)
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        return pool

    def test_molten_burn_interrupts_actual_hack_channel(self):
        fixture, run, torb, sombra = self.arena(8)
        fight = run.match.combat
        pool = self.launch_molten_pool(fixture, run, torb, sombra)
        fixture.advance(run, torb, sombra, 10, defense=2)
        ability = self.ability(sombra)
        self.assertTrue(ability.flags & ability.RUNNING)
        hp = fixture.health(run, sombra)
        before = hp.total
        fixture.place(sombra, pool.position)
        for _ in range(25):
            self.assertTrue(ability.flags & ability.RUNNING)
            fixture.step(run, torb, sombra, defense=2)
            if hp.total < before:
                break
        self.assertLess(hp.total, before)
        self.assertIn((torb.entity, sombra.body), fight.molten_burns)
        fixture.advance(run, torb, sombra, 4, defense=2)
        self.assertGreater(ability.remaining(), 0)
        self.assertFalse(fight.sombra_hacks.active)

        for _ in range(45):
            fixture.step(run, torb, sombra, defense=2)
            self.assertFalse(fight.sombra_hacks.active)
        self.assertFalse(ability.flags & ability.RUNNING)

    def test_applied_hack_allows_active_molten_primary_but_blocks_new_skill(self):
        fixture, run, torb, sombra = self.arena(8)
        fight = run.match.combat
        pool = self.launch_molten_pool(fixture, run, torb, sombra)
        end = self.cast(fixture, run, torb, sombra)
        run.step({torb: (0, 4000, 1 | 8 | 16 | 32)})
        for _ in range(70):
            if len(fight.molten_pools) == 2:
                break
            run.step({})
        self.assertLess(fight.tick, end)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.assertEqual(len(fight.molten_pools), 2)
        self.assertIn(pool.entity, fight.molten_pools)
        self.assertFalse(fight.turrets)
        self.assertEqual(fixture.health(run, torb).total, 250)

    def test_hack_blocks_skills_but_rivet_fires_and_overload_recovers(self):
        fixture, run, torb, sombra = self.arena()
        fight = run.match.combat
        end = self.cast(fixture, run, torb, sombra)
        self.assertEqual(end - fight.tick, 313)
        charge = torb.body_script.component.entity_var(338).value()
        torb.body_script.set_ult_charge(charge)
        hp = fixture.health(run, sombra)
        before = hp.total
        fixture.step(run, torb, sombra, fire=1 | 8 | 16 | 32)
        fixture.advance(run, torb, sombra, 45)
        self.assertEqual(hp.total, before - 70)
        self.assertFalse(fight.turrets or fight.molten_pools)
        self.assertEqual(fixture.health(run, torb).total, 250)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), charge)
        while run.frame < end + 6:
            fixture.step(run, torb, sombra, fire=16)
        self.assertNotIn(torb.body, fight.sombra_hacks.active)
        self.assertEqual(fixture.health(run, torb).total, 250)
        fixture.step(run, torb, sombra)
        fixture.step(run, torb, sombra, fire=16)
        fixture.advance(run, torb, sombra, 30)
        self.assertEqual(fixture.health(run, torb).total, 350)

    def test_rivet_and_passive_charge_reach_molten_cap_during_hack(self):
        fixture, run, torb, sombra = self.arena()
        fight = run.match.combat
        script = torb.body_script
        script.ult_seconds = 0
        end = self.cast(fixture, run, torb, sombra)
        cost = script.component.entity_var(338).value()
        charge = script.component.entity_var(342)
        script.ult_seconds = 60
        fixture.step(run, torb, sombra, fire=32)
        controller = next(i for i in script.component.instances.values() if i.graph.index == 0xB8B)
        wait = controller.states[1]
        self.assertTrue(wait.active)
        remaining = wait.start + wait.ms - script.component.now
        cap_frame = run.frame + (remaining + 15) // 16
        script.set_ult_charge(cost - 75)
        hp = fixture.health(run, sombra)
        before = hp.total
        fixture.step(run, torb, sombra, fire=33)
        for _ in range(24):
            if hp.total < before:
                break
            fixture.step(run, torb, sombra, fire=32)
        self.assertEqual(before - hp.total, 70)
        self.assertEqual(charge.value(), cost - 5)
        while run.frame < cap_frame - 1:
            fixture.step(run, torb, sombra, fire=32)
            self.assertEqual(charge.value(), cost - 5)
        fixture.step(run, torb, sombra, fire=32)
        self.assertEqual(run.frame, cap_frame)
        self.assertEqual(charge.value(), cost)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.assertEqual(fight.sombra_hacks.suppressed[torb.body], 32)
        while run.frame < end + 8:
            fixture.step(run, torb, sombra, fire=32)
            self.assertEqual(charge.value(), cost)
            self.assertFalse(fight.rockets or fight.molten_pools)
        self.assertNotIn(torb.body, fight.sombra_hacks.active)
        self.assertEqual(fight.sombra_hacks.suppressed[torb.body], 32)

        run.step({})
        self.assertNotIn(torb.body, fight.sombra_hacks.suppressed)
        run.step({torb: (16384, 4000, 32)})
        self.assertEqual(charge.value(), 0)
        fixture.advance(run, torb, sombra, 45)
        run.step({torb: (16384, 4000, 1)})
        globule, = fight.rockets
        self.assertIs(globule.shooter, torb)
        self.assertEqual(globule.spawn, molten_core.POOL)
        for _ in range(80):
            if fight.molten_pools:
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, torb)
        self.assertEqual(charge.value(), 0)

    def damaged_turret_with_hammer_equipped(self):
        fixture, run, torb, sombra = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        distant = (x + 100, y, z + 100)
        fixture.place(sombra, distant)
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, sombra, 225)
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        x, y, z = turret.actor.position
        fixture.place(torb, (x - 1.6, y, z))
        fixture.place(sombra, (x, y, z + 6))
        direction = aim(fight._eye(sombra), (x, y + .6, z))
        for _ in range(60):
            run.step({sombra: (*direction, 1)})
            if hp.total <= 195:
                break
        self.assertLessEqual(hp.total, 195)
        fixture.place(sombra, distant)
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, sombra, 45)
        return fixture, run, torb, sombra, turret, hp, distant

    def test_equipped_hammer_repairs_during_applied_hack(self):
        fixture, run, torb, sombra, turret, hp, distant = self.damaged_turret_with_hammer_equipped()
        match = run.match
        fight = match.combat
        before = hp.total
        x, y, z = turret.actor.position
        clear = match.collision_world
        divider = x - .8
        match.collision_world = World(
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + wall(divider, z - 15, divider, z + 15, y0=y, y1=y + 4)
        )
        fixture.place(sombra, (x - 1.6, y, z + 6))
        deadline = self.cast(fixture, run, torb, sombra)
        fixture.place(sombra, distant)
        match.collision_world = clear
        repair_aim = aim(fight._eye(torb), (x, y + .4, z))
        run.step({torb: (*repair_aim, 1 | 16)})
        for _ in range(40):
            run.step({torb: (*repair_aim, 0)})
        self.assertEqual(hp.total, before + 50)
        self.assertIs(fight.turrets[torb.entity], turret)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.assertEqual(fight.sombra_hacks.active[torb.body][-1][2], deadline)
        self.assertEqual(fixture.health(run, torb).over_armour.amount, 0)

    def test_actual_rivet_interrupts_hack_and_early_retry_fails(self):
        fixture, run, torb, sombra = self.arena()
        fight = run.match.combat
        fixture.advance(run, torb, sombra, 10, defense=2)
        ability = self.ability(sombra)
        self.assertTrue(ability.flags & ability.RUNNING)
        fixture.step(run, torb, sombra, fire=1, defense=2)
        fixture.advance(run, torb, sombra, 10, defense=2)
        self.assertGreater(ability.remaining(), 0)
        self.assertFalse(fight.sombra_hacks.active)
        fixture.step(run, torb, sombra)
        fixture.advance(run, torb, sombra, 45, defense=2)
        self.assertFalse(fight.sombra_hacks.active)
        self.assertFalse(ability.flags & ability.RUNNING)
        for _ in range(250):
            if ability.remaining() == 0:
                break
            fixture.step(run, torb, sombra)
        self.assertEqual(ability.remaining(), 0)
        self.cast(fixture, run, torb, sombra)

    def test_thrown_turret_interrupts_held_hack_and_cooldown_rejects_retry(self):
        fixture, run, torb, sombra = self.arena()
        match = run.match
        fight = match.combat
        clear = match.collision_world
        x, y, z = torb.mover.position
        position = tuple(sombra.mover.position)
        fixture.place(sombra, (x + 100, y, z + 100))
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, sombra, 225)
        turret = fight.turrets[torb.entity]
        divider = (x + turret.actor.position[0]) / 2
        cover = World(box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                      + wall(divider, z - 15, divider, z + 15, y0=y, y1=y + 4))
        match.collision_world = cover
        fixture.place(sombra, position)
        hp = fixture.health(run, sombra)
        before = hp.total
        fixture.advance(run, torb, sombra, 10, defense=2)
        ability = self.ability(sombra)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(turret.shots, 0)
        self.assertEqual(hp.total, before)
        match.collision_world = clear
        for _ in range(15):
            fixture.step(run, torb, sombra, defense=2)
            if turret.shots:
                break
        self.assertEqual(turret.shots, 1)
        self.assertEqual(hp.total, before - 14)
        self.assertGreater(ability.remaining(), 0)
        match.collision_world = cover
        for _ in range(45):
            fixture.step(run, torb, sombra, defense=2)
            self.assertFalse(fight.sombra_hacks.active)
        self.assertFalse(ability.flags & ability.RUNNING)
        fixture.step(run, torb, sombra)
        fixture.advance(run, torb, sombra, 20, defense=2)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertFalse(fight.sombra_hacks.active)
        self.assertEqual(turret.shots, 1)
        for _ in range(250):
            if ability.remaining() == 0:
                break
            fixture.step(run, torb, sombra)
        self.assertEqual(ability.remaining(), 0)
        deadline = self.cast(fixture, run, torb, sombra)
        match.collision_world = clear
        for _ in range(20):
            fixture.step(run, torb, sombra)
            if turret.shots > 1:
                break
        self.assertEqual(turret.shots, 2)
        self.assertEqual(hp.total, before - 28)
        self.assertIs(fight.turrets[torb.entity], turret)
        self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.assertEqual(fight.sombra_hacks.active[torb.body][-1][2], deadline)

    def test_aimed_hack_disables_only_the_turret_then_fire_recovers(self):
        fixture, run, torb, sombra = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        bait = run.players[2]
        bait.team = sombra.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(sombra, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sombra, 225)
        turret = fight.turrets[torb.entity]
        entity = turret.actor.entity
        x, y, z = turret.actor.position
        floor = torb.mover.position[1]
        fixture.place(bait, (x + 2, floor, z))
        fixture.place(sombra, (x, floor, z + 5))
        direction = aim(fight._eye(sombra), (x, y + turret.actor.combat_geometry[2], z))
        clear = match.collision_world
        match.collision_world = World(
            box(x - 25, x + 25, floor - 1, floor, z - 25, z + 25)
            + wall(x - 4, z + 3, x + 4, z + 3, y0=floor, y1=floor + 4)
        )
        for _ in range(45):
            run.step({sombra: (*direction, 2)})
        self.assertFalse(fight.sombra_hacks.active)
        self.assertEqual(self.ability(sombra).remaining(), 0)
        run.step({})
        match.collision_world = clear
        for _ in range(65):
            run.step({sombra: (*direction, 2)})
            if fight.sombra_hacks.silenced(entity, fight.tick):
                break
        (component, effect, deadline), = fight.sombra_hacks.active[entity]
        self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
        self.assertFalse(fight.sombra_hacks.silenced(torb.body, fight.tick))
        self.assertGreater(turret.shots, 0)
        turret_hp = fight.vitals[entity]
        self.assertEqual(turret_hp.total, 250)
        shots = turret.shots
        fixture.place(bait, (x + 100, floor, z))
        for _ in range(60):
            run.step({sombra: (*direction, 1)})
            if turret_hp.total <= 195:
                break
        self.assertLessEqual(turret_hp.total, 195)
        self.assertGreater(turret_hp.total, 0)
        damaged = turret_hp.total
        fixture.place(torb, (x - 1.6, floor, z))
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, sombra, 45)
        repair_aim = aim(fight._eye(torb), (x, y + .4, z))
        run.step({torb: (*repair_aim, 1)})
        fixture.advance(run, torb, sombra, 40)
        self.assertEqual(turret_hp.total, damaged + 50)
        self.assertEqual(fight.sombra_hacks.active[entity][-1][2], deadline)
        self.assertEqual(turret.shots, shots)
        fixture.advance(run, torb, sombra, deadline - fight.tick - 1)
        self.assertEqual(turret.shots, shots)
        hp = fixture.health(run, sombra)
        self.assertEqual(hp.total, 200)
        run.step({})
        self.assertNotIn(effect, component.instances)
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(hp.total, 186)

    def test_turret_hack_rejects_invalid_targets_and_recovers_after_channel_break(self):
        for obstacle in ("ally", "range", "aim", "new wall", "release"):
            with self.subTest(obstacle=obstacle):
                fixture, run, torb, sombra = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                bait = run.players[2]
                bait.team = sombra.team = 1 - torb.team
                x, y, z = torb.mover.position
                fixture.place(sombra, (x + 100, y, z + 100))
                run.step({torb: (0, 4000, 8)})
                fixture.advance(run, torb, sombra, 225)
                turret = fight.turrets[torb.entity]
                entity = turret.actor.entity
                x, y, z = turret.actor.position
                floor = torb.mover.position[1]
                fixture.place(bait, (x + 2, floor, z))
                valid_position = (x, floor, z + 5)
                fixture.place(sombra, (x, floor, z + 30) if obstacle == "range" else valid_position)
                if obstacle == "ally":
                    sombra.team = torb.team
                target = (x, y + turret.actor.combat_geometry[2], z)
                yaw, pitch = aim(fight._eye(sombra), target)
                if obstacle == "aim":
                    yaw = (yaw + 32768) % 65536
                ability = self.ability(sombra)
                selected = ability.instance.find_var(expr.INSTANCE, 375)
                locked = ability.instance.find_var(expr.INSTANCE, 8909)
                started = obstacle in ("new wall", "release")
                clear = match.collision_world
                if started:
                    for _ in range(10):
                        run.step({sombra: (yaw, pitch, 2)})
                    self.assertTrue(ability.flags & ability.RUNNING)
                    self.assertEqual(expr.to_entity(locked.value()), entity)
                    if obstacle == "new wall":
                        match.collision_world = World(
                            box(x - 25, x + 25, floor - 1, floor, z - 25, z + 25)
                            + wall(x - 4, z + 3, x + 4, z + 3, y0=floor, y1=floor + 4)
                        )
                bait_hp = fixture.health(run, bait)
                before = bait_hp.total
                shots = turret.shots
                for _ in range(65):
                    run.step({sombra: (yaw, pitch, 0 if obstacle == "release" else 2)})
                    self.assertFalse(fight.sombra_hacks.active)
                    if not started:
                        self.assertFalse(selected.value())
                if not started:
                    self.assertEqual(ability.remaining(), 0)
                self.assertGreater(turret.shots, shots)
                self.assertEqual(bait_hp.total, before - 14 * (turret.shots - shots))

                for _ in range(45):
                    run.step({sombra: (yaw, pitch, 0)})
                    self.assertFalse(fight.sombra_hacks.active)
                    if not ability.flags & ability.RUNNING:
                        break
                self.assertFalse(ability.flags & ability.RUNNING)
                match.collision_world = clear
                sombra.team = bait.team
                fixture.place(sombra, valid_position)
                for _ in range(250):
                    if ability.remaining() == 0:
                        break
                    run.step({})
                self.assertEqual(ability.remaining(), 0)
                direction = aim(fight._eye(sombra), target)
                for _ in range(65):
                    run.step({sombra: (*direction, 2)})
                    if fight.sombra_hacks.silenced(entity, fight.tick):
                        break
                (component, effect, deadline), = fight.sombra_hacks.active[entity]
                self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
                self.assertGreater(deadline, fight.tick)
                self.assertFalse(fight.sombra_hacks.silenced(torb.body, fight.tick))
                self.assertIs(fight.turrets[torb.entity], turret)
                shots, before = turret.shots, bait_hp.total
                fixture.advance(run, torb, bait, 20)
                self.assertEqual(turret.shots, shots)
                self.assertEqual(bait_hp.total, before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "needs authored barrier mesh")
    def test_reinhardt_barrier_blocks_turret_hack_only_when_hostile_to_sombra(self):
        for protection in ("already raised", "raised during channel", "allied to Sombra"):
            with self.subTest(protection=protection):
                fixture, run, turret = self.deployed_turret_arena(reinhardt_barrier.HERO)
                torb, sombra, rein, bait = run.players
                fight = run.match.combat
                friendly = protection == "allied to Sombra"
                rein.team = sombra.team if friendly else torb.team
                floor = torb.mover.position[1]
                entity = turret.actor.entity
                x, y, z = turret.actor.position
                fixture.place(bait, (x - 1.7, floor, z))
                fixture.place(rein, (x + 1.4, floor, z + 2.3))
                fixture.place(sombra, (x, floor, z + 5))
                target = (x, y + turret.actor.combat_geometry[2], z)
                direction = aim(fight._eye(sombra), target)
                ability = self.ability(sombra)
                raised_during_channel = protection == "raised during channel"
                if raised_during_channel:
                    for _ in range(10):
                        run.step({sombra: (*direction, 2)})
                    locked = ability.instance.find_var(expr.INSTANCE, 8909)
                    self.assertEqual(expr.to_entity(locked.value()), entity)
                    self.assertFalse(fight.sombra_hacks.active)
                for _ in range(5):
                    run.step({rein: (0, 0, 2), sombra: (*direction, 2 if raised_during_channel else 0)})
                shield = fight.barriers.states[rein.body]
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, 1600)
                hp = fixture.health(run, bait)
                before, shots = hp.total, turret.shots
                for _ in range(65):
                    run.step({sombra: (*direction, 2), rein: (0, 0, 2)})
                    if friendly and fight.sombra_hacks.silenced(entity, fight.tick):
                        break
                    self.assertFalse(fight.sombra_hacks.active)
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, 1600)
                self.assertGreater(turret.shots, shots)
                self.assertEqual(hp.total, before - 14 * (turret.shots - shots))
                if not friendly:
                    if protection == "already raised":
                        self.assertEqual(ability.remaining(), 0)
                    for _ in range(45):
                        run.step({sombra: (*direction, 0), rein: (0, 0, 2)})
                        if not ability.flags & ability.RUNNING:
                            break
                    self.assertFalse(ability.flags & ability.RUNNING)
                    for _ in range(250):
                        run.step({rein: (0, 0, 0)})
                        if not shield.active and ability.remaining() == 0:
                            break
                    self.assertFalse(shield.active)
                    self.assertEqual(ability.remaining(), 0)
                    for _ in range(65):
                        run.step({sombra: (*direction, 2)})
                        if fight.sombra_hacks.silenced(entity, fight.tick):
                            break
                (component, effect, deadline), = fight.sombra_hacks.active[entity]
                self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
                self.assertGreater(deadline, fight.tick)
                self.assertFalse(fight.sombra_hacks.silenced(torb.body, fight.tick))
                self.assertIs(fight.turrets[torb.entity], turret)
                before, shots = hp.total, turret.shots
                for _ in range(20):
                    run.step({rein: (0, 0, 2 if friendly else 0)})
                self.assertEqual(turret.shots, shots)
                self.assertEqual(hp.total, before)

    @unittest.skipUnless(all(os.environ.get(key) for key in (
        "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_ORISA_COLLISION",
    )), "needs authored deployed barrier meshes")
    def test_deployed_barriers_block_turret_hack_until_recall_or_destruction(self):
        for hero, capacity in ((sigma_barrier.HERO, 700), (0x02E000000000013E, 600)):
            with self.subTest(hero=hero):
                fixture, run, turret = self.deployed_turret_arena(hero)
                torb, sombra, owner, bait = run.players
                match = run.match
                fight = match.combat
                entity = turret.actor.entity
                x, y, z = turret.actor.position
                floor = torb.mover.position[1]
                fixture.place(owner, (x, floor, z + 8))
                sigma = hero == sigma_barrier.HERO
                if sigma:
                    for _ in range(15):
                        run.step({owner: (-32768, 0, 2)})
                    run.step({owner: (-32768, 0, 0)})
                    barriers = fight.sigma_barriers
                    shield = barriers.actors[owner.body]
                    self.assertEqual(shield.mode, 3)
                else:
                    shield = orisa_tests.TorbjornOrisaTests().deploy(run, owner)
                    barriers = fight.orisa_barriers
                self.assertTrue(shield.active())
                self.assertEqual(shield.health, capacity)
                fixture.place(owner, (x + 12, floor, z))
                bait_position = (x - 1.7, floor, z)
                fixture.place(bait, bait_position)
                fixture.place(sombra, (x, floor, shield.position[2] + 3))
                target = (x, y + turret.actor.combat_geometry[2], z)
                origin = fight._eye(sombra)
                direction = aim(origin, target)
                hit = barriers.trace(sombra, origin, combat.aim(*direction), math.dist(origin, target))
                self.assertIsNotNone(hit)
                self.assertIs(hit[1], shield)
                ability = self.ability(sombra)
                hp = fixture.health(run, bait)
                before, shots = hp.total, turret.shots
                for _ in range(55):
                    run.step({sombra: (*direction, 2)})
                    self.assertFalse(fight.sombra_hacks.active)
                self.assertEqual(ability.remaining(), 0)
                self.assertEqual(shield.health, capacity)
                self.assertGreater(turret.shots, shots)
                self.assertEqual(hp.total, before - 14 * (turret.shots - shots))
                for _ in range(45):
                    run.step({sombra: (*direction, 0)})
                    if not ability.flags & ability.RUNNING:
                        break
                self.assertFalse(ability.flags & ability.RUNNING)

                if sigma:
                    run.step({owner: (-32768, 0, 2)})
                else:
                    # Protect the stationary test players from return fire while Sombra breaks the mesh.
                    clear = match.collision_world
                    cover = (z + shield.position[2]) / 2
                    match.collision_world = World(
                        box(x - 25, x + 25, floor - 1, floor, z - 25, z + 25)
                        + wall(x - 10, cover, x + 10, cover, y0=floor, y1=floor + 4)
                    )
                    fixture.place(bait, (x - 100, floor, z))
                    for _ in range(550):
                        run.step({sombra: (*direction, 1)})
                        if not shield.active():
                            break
                    self.assertEqual(shield.health, 0)
                    run.step({sombra: (*direction, 0)})
                    match.collision_world = clear
                    fixture.place(bait, bait_position)
                self.assertFalse(shield.active())
                self.assertFalse(fight.sombra_hacks.active)
                for _ in range(65):
                    run.step({sombra: (*direction, 2)})
                    if fight.sombra_hacks.silenced(entity, fight.tick):
                        break
                (component, effect, deadline), = fight.sombra_hacks.active[entity]
                self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
                self.assertGreater(deadline, fight.tick)
                self.assertFalse(fight.sombra_hacks.silenced(torb.body, fight.tick))
                self.assertIs(fight.turrets[torb.entity], turret)
                before, shots = hp.total, turret.shots
                fixture.advance(run, torb, bait, 20)
                self.assertEqual(turret.shots, shots)
                self.assertEqual(hp.total, before)

    def test_turret_hack_keeps_its_deadline_after_caster_death_or_hero_switch(self):
        for exit_kind in ("death", "hero switch"):
            with self.subTest(exit_kind=exit_kind):
                fixture, run, torb, sombra = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                bait = run.players[2]
                bait.team = sombra.team = 1 - torb.team
                x, y, z = torb.mover.position
                fixture.place(sombra, (x + 100, y, z + 100))
                run.step({torb: (0, 4000, 8)})
                fixture.advance(run, torb, sombra, 225)
                turret = fight.turrets[torb.entity]
                entity = turret.actor.entity
                x, _, z = turret.actor.position
                floor = torb.mover.position[1]
                bait_position = (x + 2, floor, z)
                fixture.place(bait, bait_position)
                fixture.place(sombra, (x, floor, z + 5))
                turret_eye = (x, turret.actor.position[1] + turret.actor.combat_geometry[2], z)
                for _ in range(65):
                    direction = aim(fight._eye(sombra), turret_eye)
                    run.step({sombra: (*direction, 2)})
                    if fight.sombra_hacks.silenced(entity, fight.tick):
                        break
                applied, = fight.sombra_hacks.active[entity]
                component, effect, deadline = applied
                self.assertEqual(component.instances[effect].graph.index, sombra_control.EFFECT)
                self.assertFalse(fight.sombra_hacks.silenced(torb.body, fight.tick))
                self.assertGreater(turret.shots, 0)
                shots = turret.shots
                fixture.place(bait, (x + 100, floor, z))

                old_body = sombra.body
                if exit_kind == "death":
                    fixture.place(torb, (x - 4, floor, z + 5))
                    hp = fixture.health(run, sombra)
                    self.assertEqual(hp.total, 200)
                    for expected in (130, 60, 0):
                        fixture.step(run, torb, sombra, fire=1)
                        fixture.advance(run, torb, sombra, 48)
                        self.assertEqual(hp.total, expected)
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.killer, torb.body)
                else:
                    self.assertTrue(match.switch_hero(sombra, heroes()[SOLDIER]))
                    self.assertNotEqual(sombra.body, old_body)
                    fixture.place(sombra, (x + 100, floor, z))

                self.assertLess(fight.tick, deadline - 1)
                self.assertEqual(fight.sombra_hacks.active[entity], [applied])
                self.assertIs(fight.turrets[torb.entity], turret)
                self.assertEqual(fight.vitals[entity].total, 250)
                fixture.place(bait, bait_position)
                bait_hp = fixture.health(run, bait)
                before = bait_hp.total
                fixture.advance(run, torb, bait, deadline - fight.tick - 1)
                self.assertTrue(fight.sombra_hacks.silenced(entity, fight.tick))
                self.assertEqual(turret.shots, shots)
                self.assertEqual(bait_hp.total, before)
                run.step({})
                self.assertNotIn(effect, component.instances)
                self.assertNotIn(entity, fight.sombra_hacks.active)
                self.assertEqual(turret.target, bait.body)
                self.assertEqual(turret.shots, shots + 1)
                self.assertEqual(bait_hp.total, before - 14)

    def test_killing_hacked_turret_retires_effect_and_preserves_redeploy_cooldown(self):
        fixture, run, torb, sombra = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        bait = run.players[2]
        bait.team = sombra.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(sombra, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sombra, 225)
        turret = fight.turrets[torb.entity]
        entity = turret.actor.entity
        x, y, z = turret.actor.position
        fixture.place(bait, (x + 2, y, z))
        fixture.place(sombra, (x, y, z + 5))
        target = (x, y + turret.actor.combat_geometry[2], z)
        for _ in range(65):
            run.step({sombra: (*aim(fight._eye(sombra), target), 2)})
            if fight.sombra_hacks.silenced(entity, fight.tick):
                break
        (component, effect, deadline), = fight.sombra_hacks.active[entity]
        shots = turret.shots
        hp = fight.vitals[entity]
        self.assertEqual(hp.total, 250)
        fixture.place(bait, (x + 100, y, z))
        fixture.place(sombra, (x, y, z + 2.5))
        for _ in range(250):
            run.step({sombra: (*aim(fight._eye(sombra), target), 1)})
            if hp.dead:
                break
        self.assertTrue(hp.dead)
        self.assertLess(fight.tick, deadline)
        self.assertEqual(hp.killer, sombra.body)
        self.assertEqual(turret.shots, shots)
        self.assertIs(turret.actor.body_script.component, component)
        self.assertTrue(component.owner_dead)
        self.assertNotIn(effect, component.instances)
        self.assertNotIn(entity, fight.sombra_hacks.active)
        self.assertNotIn(entity, fight.sombra_hacks.bases)
        self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 9.9)
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sombra, 30)
        self.assertNotIn(torb.entity, fight.turrets)
        for _ in range(630):
            if hero_torbjorn.turret_cooldown_remaining(torb) <= 0:
                break
            run.step({})
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, sombra, 240)
        replacement = fight.turrets[torb.entity]
        self.assertNotEqual(replacement.actor.entity, entity)
        self.assertGreater(replacement.shots, 0)

    def test_wrong_team_range_and_new_wall_do_not_apply(self):
        for obstacle in ("team", "range", "wall", "release"):
            with self.subTest(obstacle=obstacle):
                fixture, run, torb, sombra = self.arena(30 if obstacle == "range" else 3)
                if obstacle == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sombra.team = torb.team
                if obstacle in ("wall", "release"):
                    fixture.advance(run, torb, sombra, 10, defense=2)
                    self.assertTrue(self.ability(sombra).flags & self.ability(sombra).RUNNING)
                if obstacle == "wall":
                    x, y, z = torb.mover.position
                    run.match.collision_world = World(
                        box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                        + wall(x - 6, z + 1.5, x + 6, z + 1.5, y0=y, y1=y + 4)
                    )
                fixture.advance(run, torb, sombra, 70, defense=0 if obstacle == "release" else 2)
                self.assertFalse(run.match.combat.sombra_hacks.active)
                fixture.advance(run, torb, sombra, 45)
                self.assertFalse(self.ability(sombra).flags & self.ability(sombra).RUNNING)

    def test_applied_hack_expires_without_extending_existing_overload(self):
        fixture, run, torb, sombra = self.arena()
        hp = fixture.health(run, torb)
        fixture.step(run, torb, sombra, fire=16)
        fixture.advance(run, torb, sombra, 24)
        self.assertEqual(hp.total, 350)
        end = self.cast(fixture, run, torb, sombra)
        self.assertEqual(hp.total, 350)
        for _ in range(360):
            if hp.total == 250:
                break
            fixture.step(run, torb, sombra, fire=16)
        self.assertEqual(hp.total, 250)
        self.assertLess(run.frame, end)
        self.assertTrue(run.match.combat.sombra_hacks.silenced(torb.body, run.match.combat.tick))

    def test_hack_spans_overload_cooldown_and_requires_a_fresh_press(self):
        for held in (False, True):
            with self.subTest(held=held):
                fixture, run, torb, sombra = self.arena()
                fight = run.match.combat
                hp = fixture.health(run, torb)
                fixture.step(run, torb, sombra, fire=16)
                source = hp.over_armour.source
                self.assertEqual(hp.over_armour.amount, 100)
                for _ in range(1000):
                    if hero_torbjorn.overload_cooldown_remaining(torb) <= 1.2:
                        break
                    fixture.step(run, torb, sombra)
                self.assertIsNone(hp.over_armour.source)
                end = self.cast(fixture, run, torb, sombra)
                self.assertEqual(end - fight.tick, 313)
                self.assertGreater(hero_torbjorn.overload_cooldown_remaining(torb), 0)

                while run.frame < end - 1:
                    button = 16 if held or run.frame < end - 3 else 0
                    fixture.step(run, torb, sombra, fire=button)
                    self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
                    self.assertIsNone(hp.over_armour.source)
                    self.assertEqual(hp.total, 250)
                self.assertEqual(hero_torbjorn.overload_cooldown_remaining(torb), 0)
                for _ in range(8):
                    fixture.step(run, torb, sombra, fire=16 if held else 0)
                    self.assertFalse(fight.sombra_hacks.silenced(torb.body, fight.tick))
                    self.assertNotIn(torb.body, fight.sombra_hacks.active)
                    self.assertIsNone(hp.over_armour.source)
                    self.assertEqual(hp.total, 250)
                self.assertEqual(fight.sombra_hacks.suppressed.get(torb.body, 0), 16 if held else 0)

                fixture.step(run, torb, sombra)
                self.assertNotIn(torb.body, fight.sombra_hacks.suppressed)
                fixture.step(run, torb, sombra, fire=16)
                self.assertEqual(hp.over_armour.source, (source[0], source[1] + 1))
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertEqual(hp.total, 350)

    def test_hack_spans_turret_dismantle_cooldown_and_blocks_held_throw(self):
        for held in (False, True):
            with self.subTest(held=held):
                fixture, run, torb, sombra = self.arena()
                fight = run.match.combat
                x, y, z = torb.mover.position
                fixture.place(sombra, (x + 100, y, z))
                run.step({torb: (0, 4000, 8)})
                fixture.advance(run, torb, sombra, 225)
                first = fight.turrets[torb.entity]
                run.step({torb: (0, 0, 4)})
                self.assertNotIn(first.actor, run.match.bots)
                self.assertFalse(fight.turrets)
                self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 9.9)
                run.step({torb: (0, 4000, 8)})
                for _ in range(660):
                    if hero_torbjorn.turret_cooldown_remaining(torb) <= 1.2:
                        break
                    run.step({})
                    self.assertFalse(fight.turrets or fight.rockets)

                fixture.place(sombra, (x + 3, y, z))
                end = self.cast(fixture, run, torb, sombra)
                self.assertEqual(end - fight.tick, 313)
                self.assertGreater(hero_torbjorn.turret_cooldown_remaining(torb), 0)
                while run.frame < end - 1:
                    button = 8 if held or run.frame < end - 3 else 0
                    run.step({torb: (0, 4000, button)})
                    self.assertTrue(fight.sombra_hacks.silenced(torb.body, fight.tick))
                    self.assertFalse(fight.turrets or fight.rockets)
                self.assertEqual(hero_torbjorn.turret_cooldown_remaining(torb), 0)
                for _ in range(35):
                    run.step({torb: (0, 4000, 8 if held else 0)})
                    self.assertFalse(fight.sombra_hacks.silenced(torb.body, fight.tick))
                    self.assertFalse(fight.turrets or fight.rockets)
                self.assertEqual(fight.sombra_hacks.suppressed.get(torb.body, 0), 8 if held else 0)

                run.step({})
                self.assertNotIn(torb.body, fight.sombra_hacks.suppressed)
                hp = fixture.health(run, sombra)
                before = hp.total
                run.step({torb: (0, 4000, 8)})
                for _ in range(35):
                    if fight.rockets:
                        break
                    run.step({})
                throw, = fight.rockets
                self.assertIs(throw.shooter, torb)
                self.assertEqual(throw.spawn, hero_torbjorn.TURRET)
                for _ in range(240):
                    run.step({})
                    turret = fight.turrets.get(torb.entity)
                    if turret is not None and turret.shots:
                        break
                replacement = fight.turrets[torb.entity]
                self.assertNotEqual(replacement.actor.entity, first.actor.entity)
                self.assertIs(replacement.owner, torb)
                self.assertEqual(replacement.target, sombra.body)
                self.assertEqual(replacement.shots, 1)
                self.assertEqual(hp.total, before - 14)

    def test_live_effect_and_channel_states_are_passively_exportable(self):
        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest

        fixture, run, torb, sombra = self.arena()
        fixture.advance(run, torb, sombra, 10, defense=2)
        caster = sombra.body_script.component
        first = snapshot_digest(export_component_vm(caster))
        self.assertEqual(first, snapshot_digest(export_component_vm(caster)))
        self.cast(fixture, run, torb, sombra)
        recipient = torb.body_script.component
        self.assertTrue(any(i.graph.index == sombra_control.EFFECT for i in recipient.instances.values()))
        before = snapshot_digest(export_component_vm(recipient))
        self.assertEqual(before, snapshot_digest(export_component_vm(recipient)))
        old_body = torb.body
        run.match.switch_hero(torb, torb.hero)
        self.assertNotEqual(torb.body, old_body)
        self.assertNotIn(old_body, run.match.combat.sombra_hacks.active)
        self.assertNotIn(old_body, run.match.combat.sombra_hacks.suppressed)
