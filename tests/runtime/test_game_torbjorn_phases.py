"""Actual Torbjorn attacks against three command-driven phase abilities."""

import os
import unittest
from pathlib import Path

import test_game_torbjorn_turret as turret_tests
from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box

from ow174.game import hero_torbjorn, molten_core, npc, phase_defenses, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared Torbjorn data")
class TorbjornPhaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        turret_tests.TorbjornTurretTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        turret_tests.TorbjornTurretTests.tearDownClass()

    def arena(self, name, distance=2.0, behind_distance=3.0):
        run = Run(KANEZAKA_DM, names=("Torbjorn", name, "Behind"))
        owner, defender, behind = run.players
        by_name = {hero.name: hero for hero in heroes().values()}
        run.match.switch_hero(owner, heroes()[hero_torbjorn.HERO])
        run.match.switch_hero(defender, by_name[name])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = owner.mover.position
        defender.mover.position = [x, y, z + distance]
        behind.mover.position = [x, y, z + behind_distance]
        for player in run.players:
            player.mover.body = None
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        self.advance(run, 35)
        return run, owner, defender, behind

    @staticmethod
    def advance(run, ticks, commands=None):
        for _ in range(ticks):
            run.step(commands or {})

    @staticmethod
    def health(run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target)

    def cast(self, run, defender):
        run.step({defender: (-32768, 0, 8)})
        self.advance(run, 5)
        self.assertTrue(phase_defenses.phase(defender)[0])

    @staticmethod
    def body_aim(run, owner, target):
        capsule = next(t for t in run.match.combat._targets() if t.owner is target)
        return aim(run.match.combat._eye(owner),
                   (capsule.feet[0], capsule.feet[1] + capsule.height * .5, capsule.feet[2]))

    def fire(self, run, owner, target, button=1):
        yaw, pitch = self.body_aim(run, owner, target)
        run.step({owner: (yaw, pitch, button)})
        self.advance(run, 20)

    def test_primary_and_secondary_pass_ghosts_but_stop_on_solid_cryo(self):
        for name in ("Reaper", "Moira", "Mei"):
            for button, damage in ((1, 70), (2, 125)):
                with self.subTest(defense=name, button=button):
                    run, owner, defender, behind = self.arena(name)
                    before = self.health(run, defender).total
                    self.cast(run, defender)
                    self.fire(run, owner, behind, button)
                    self.assertEqual(self.health(run, defender).total, before)
                    loss = 0 if name == "Mei" else damage
                    self.assertEqual(self.health(run, behind).total, 200 - loss)
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), loss)
                    self.assertNotIn((owner.entity, defender.body), run.match.combat.torbjorn_marks)

    def test_airborne_rivet_uses_phase_at_impact_then_fresh_rivet_hits_after_exit(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, behind = self.arena(name, distance=10, behind_distance=14)
                fight = run.match.combat
                defender_hp = self.health(run, defender)
                behind_hp = self.health(run, behind)
                front_before, rear_before = defender_hp.total, behind_hp.total
                rifle = next(i for i in owner.body_script.component.instances.values()
                             if i.graph.index == 0x1683)
                charge = owner.body_script.component.entity_var(342)
                self.assertFalse(phase_defenses.phase(defender)[0])
                yaw, pitch = self.body_aim(run, owner, behind)
                run.step({owner: (yaw, pitch, 1)})
                rivet, = fight.rockets
                self.assertIs(rivet.shooter, owner)
                self.assertEqual(rivet.distance, 0)
                self.assertEqual(rifle.vars[53].value(), 17)
                self.cast(run, defender)
                self.assertIn(rivet, fight.rockets)
                self.assertEqual(defender_hp.total, front_before)
                self.assertEqual(behind_hp.total, rear_before)

                self.advance(run, 20)
                rear_damage = 0 if name == "Mei" else 70
                self.assertNotIn(rivet, fight.rockets)
                self.assertEqual(defender_hp.total, front_before)
                self.assertEqual(behind_hp.total, rear_before - rear_damage)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), rear_damage)
                self.assertEqual(charge.value(), rear_damage)
                self.assertNotIn((owner.entity, defender.body), fight.torbjorn_marks)
                self.assertEqual((owner.entity, behind.body) in fight.torbjorn_marks, bool(rear_damage))

                for _ in range(350):
                    if not phase_defenses.phase(defender)[0]:
                        break
                    run.step({})
                self.assertFalse(phase_defenses.phase(defender)[0])
                self.fire(run, owner, defender)
                self.assertEqual(defender_hp.total, front_before - 70)
                self.assertEqual(behind_hp.total, rear_before - rear_damage)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), rear_damage + 70)
                self.assertEqual(charge.value(), rear_damage + 70)
                self.assertEqual(rifle.vars[53].value(), 16)
                self.assertIn((owner.entity, defender.body), fight.torbjorn_marks)

    def test_airborne_secondary_volley_resolves_live_phase_for_each_pellet(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, behind = self.arena(name, distance=4, behind_distance=5)
                fight = run.match.combat
                defender_hp = self.health(run, defender)
                behind_hp = self.health(run, behind)
                front_before, rear_before = defender_hp.total, behind_hp.total
                rifle = next(i for i in owner.body_script.component.instances.values()
                             if i.graph.index == 0x1683)
                charge = owner.body_script.component.entity_var(342)
                self.assertFalse(phase_defenses.phase(defender)[0])
                yaw, pitch = self.body_aim(run, owner, behind)
                run.step({owner: (yaw, pitch, 2)})
                volley = tuple(fight.rockets)
                self.assertEqual(len(volley), 10)
                self.assertTrue(all(pellet.shooter is owner and pellet.distance == 0 for pellet in volley))
                self.assertEqual(rifle.vars[53].value(), 16)
                run.step({defender: (-32768, 0, 8)})
                self.assertTrue(phase_defenses.phase(defender)[0])
                self.assertEqual(tuple(fight.rockets), volley)
                self.assertEqual(defender_hp.total, front_before)
                self.assertEqual(behind_hp.total, rear_before)

                self.advance(run, 20)
                damage = 0 if name == "Mei" else 125
                self.assertFalse(fight.rockets)
                self.assertEqual(defender_hp.total, front_before)
                self.assertEqual(behind_hp.total, rear_before - damage)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(charge.value(), damage)
                self.assertEqual(rifle.vars[53].value(), 16)
                self.assertFalse(fight.torbjorn_marks)

    def test_transcendence_blocks_actual_rivet_and_secondary_then_releases_immunity(self):
        for idle in (False, True):
            with self.subTest(idle=idle):
                self.exercise_transcendence_weapon_contacts(idle=idle)

    def exercise_transcendence_weapon_contacts(self, *, idle):
        from ow174.game import combat

        run, owner, zen, behind = self.arena("Zenyatta", distance=8, behind_distance=12)
        fight = run.match.combat
        component = zen.body_script.component
        hp = self.health(run, zen)
        rear_hp = self.health(run, behind)
        instance, = (item for item in component.instances.values()
                     if item.graph.index == phase_defenses.TRANSCENDENCE)
        run.step({zen: (0, 0, 32)})
        self.assertFalse(phase_defenses.transcendence_invulnerable(zen, run.frame))
        run.step({})
        zen.body_script.set_ult_charge(component.entity_var(338).value())

        run.step({owner: (*self.body_aim(run, owner, zen), 1)})
        rivet, = fight.rockets
        run.step({zen: (0, 0, 32)})
        self.assertEqual(component.entity_var(342).value(), 0)
        self.assertIn(rivet, fight.rockets)
        self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))
        self.assertEqual(phase_defenses.phase(zen), (False, False))
        countdown = instance.state(3)
        deadline = (countdown.start + 6000 + component.frame_ms - 1) // component.frame_ms
        self.advance(run, 50)
        self.assertNotIn(rivet, fight.rockets)
        self.assertEqual(hp.total, 200)
        self.assertEqual(rear_hp.total, 200)

        x, y, z = owner.mover.position
        zen.mover.position = [x, y, z + 2]
        zen.mover.body = None
        run.step({owner: (*self.body_aim(run, owner, zen), 2)})
        self.assertEqual(len(fight.rockets), 10)
        self.advance(run, 35)
        self.assertFalse(fight.rockets)
        self.assertEqual(hp.total, 200)
        self.assertEqual(rear_hp.total, 200)
        self.assertFalse(owner.client.messages(combat.HIT))
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.assertNotIn((owner.entity, zen.body), fight.torbjorn_marks)

        def world_step():
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)

        while run.frame < deadline - 1:
            if idle:
                world_step()
            else:
                run.step({})
        if idle:
            self.assertTrue(countdown.active)
            self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))
        world_step()
        self.assertEqual(run.frame, deadline)
        self.assertFalse(phase_defenses.transcendence_invulnerable(zen, run.frame))
        yaw, pitch = self.body_aim(run, owner, zen)
        owner.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
        for _ in range(30):
            world_step()
        self.assertEqual(hp.total, 130)
        self.assertEqual(rear_hp.total, 200)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 70)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 70)

    def test_transcendence_keeps_airborne_orb_and_fresh_primary_hits_overload(self):
        from unittest.mock import patch

        run, torb, zen, _ = self.arena("Zenyatta", distance=8, behind_distance=100)
        fight = run.match.combat
        hp = self.health(run, torb)
        direction = self.body_aim(run, zen, torb)
        component = zen.body_script.component
        zen.body_script.set_ult_charge(component.entity_var(338).value())
        run.step({zen: (*direction, 1)})
        orb, = fight.rockets
        self.assertIs(orb.shooter, zen)
        self.assertEqual(hp.total, 250)

        run.step({zen: (*direction, 32)})
        self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))
        self.assertIn(orb, fight.rockets)
        for _ in range(30):
            if orb not in fight.rockets:
                break
            run.step({})
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(hp.total, 207)
        self.assertEqual(zen.stats.value(stats.HERO_DAMAGE), 43)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as dispatch:
            self.advance(run, 55, {zen: (*direction, 1)})
        self.assertFalse(any(call.args[0] is zen for call in dispatch.call_args_list))
        self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))
        self.assertEqual(hp.total, 207)

        for _ in range(420):
            if not phase_defenses.transcendence_invulnerable(zen, run.frame):
                break
            run.step({})
        self.assertFalse(phase_defenses.transcendence_invulnerable(zen, run.frame))
        run.step({torb: (0, 0, 16)})
        self.advance(run, 40)
        self.assertEqual(hp.over_armour.amount, 100)
        base = dict(hp.now)
        run.step({zen: (*direction, 1)})
        fresh, = fight.rockets
        self.assertIs(fresh.shooter, zen)
        self.assertIsNot(fresh, orb)
        for _ in range(30):
            if fresh not in fight.rockets:
                break
            run.step({})
        self.assertNotIn(fresh, fight.rockets)
        self.assertEqual(hp.now, base)
        self.assertEqual(hp.over_armour.amount, 57)
        self.assertEqual(hp.total, 264)
        self.assertEqual(zen.stats.value(stats.HERO_DAMAGE), 86)

    def test_transcendence_blocks_melee_and_hammer_until_expiry(self):
        for button, expected in ((0x800, 30), (1, 55)):
            with self.subTest(button=button):
                self.exercise_transcendence_melee(button, expected)

    def exercise_transcendence_melee(self, button, expected):
        from unittest.mock import patch

        run, owner, zen, _ = self.arena("Zenyatta", distance=1.5, behind_distance=100)
        fight = run.match.combat
        hp = self.health(run, zen)
        if button == 1:
            run.frame += 1
            owner.take_commands([Command(run.frame, action=2)])
            self.advance(run, 45)
        self.begin_transcendence(run, zen)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            run.step({owner: (0, 0, button)})
            self.advance(run, 40)
        attempted = [call.args[2] for call in damage.call_args_list if call.args[1].owner is zen]
        self.assertEqual(attempted, [expected])
        self.assertEqual(hp.total, 200)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.wait_for_transcendence_expiry(run, zen)
        run.step({owner: (0, 0, button)})
        self.advance(run, 40)
        self.assertEqual(hp.total, 200 - expected)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), expected)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), expected)

    def test_transcendence_blocks_actual_thrown_turret_damage_until_expiry(self):
        run, owner, zen, _ = self.arena("Zenyatta", distance=100, behind_distance=110)
        fight = run.match.combat
        hp = self.health(run, zen)
        run.step({owner: (0, 4000, 8)})
        self.advance(run, 225)
        turret = fight.turrets[owner.entity]
        self.assertEqual(fight.vitals[turret.actor.entity].total, 250)
        self.assertEqual(turret.shots, 0)

        self.begin_transcendence(run, zen)
        x, y, z = turret.actor.position
        zen.mover.position = [x, y, z + 3]
        zen.mover.body = None
        self.advance(run, 40)
        self.assertEqual(turret.shots, 3)
        self.assertEqual(hp.total, 200)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.wait_for_transcendence_expiry(run, zen)
        for _ in range(20):
            if hp.total < 200:
                break
            run.step({})
        self.assertIs(fight.turrets[owner.entity], turret)
        self.assertEqual(hp.total, 186)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 14)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 14)

    def test_transcendence_blocks_molten_direct_and_defers_landed_burn_damage(self):
        run, owner, zen, _ = self.arena("Zenyatta", distance=5, behind_distance=100)
        fight = run.match.combat
        hp = self.health(run, zen)
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        run.step({owner: (0, 0, 32)})
        self.advance(run, 45)
        run.step({owner: (*self.body_aim(run, owner, zen), 1)})
        globule, = fight.rockets
        self.assertEqual(globule.spawn, molten_core.POOL)
        self.begin_transcendence(run, zen)
        self.assertIn(globule, fight.rockets)
        self.advance(run, 35)
        self.assertEqual(hp.total, 200)
        self.assertFalse(fight.rockets or fight.molten_pools)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

        run.step({owner: (16384, 4000, 1)})
        for _ in range(90):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        self.assertIs(pool.owner, owner)
        zen.mover.position = list(pool.position)
        zen.mover.body = None
        self.advance(run, 16)
        self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))
        burn = owner.entity, zen.body
        self.assertIn(burn, fight.molten_burns)
        self.assertEqual(hp.total, 200)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.wait_for_transcendence_expiry(run, zen)
        for _ in range(3):
            if hp.total < 200:
                break
            run.step({})
        self.assertEqual(list(fight.molten_pools.values()), [pool])
        self.assertIn(burn, fight.molten_burns)
        self.assertAlmostEqual(hp.total, 197.44)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 2.56)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def begin_transcendence(self, run, zen):
        component = zen.body_script.component
        zen.body_script.set_ult_charge(component.entity_var(338).value())
        run.step({zen: (0, 0, 32)})
        self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))

    def wait_for_transcendence_expiry(self, run, zen):
        for _ in range(420):
            if not phase_defenses.transcendence_invulnerable(zen, run.frame):
                break
            run.step({})
        self.assertFalse(phase_defenses.transcendence_invulnerable(zen, run.frame))

    def test_transcendence_heals_npc_damage_only_if_torbjorn_survives(self):
        from dataclasses import replace
        from unittest.mock import patch

        from ow174.game import combat, npc_control

        def start_encounter(definition):
            run, torb, zen, _ = self.arena('Zenyatta', distance=20, behind_distance=100)
            match = run.match
            match.game_map = replace(match.game_map, free_for_all=False)
            zen.team = torb.team
            for viewer in match.players:
                viewer.client.bot_stream = lambda *args: None
            x, y, z = torb.mover.position
            distance = 1.5 if definition.name == 'Assassin' else 5
            bot = npc.spawn(match, definition, (x, y, z + distance), team=1 - torb.team, yaw=-32768)
            while run.frame < bot.records_from:
                run.step({})

            hp = self.health(run, torb)
            direction = self.body_aim(run, torb, bot)
            run.step({torb: (*direction, 16)})
            self.advance(run, 45)
            self.assertEqual(hp.total, 350)
            script = zen.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
            run.step({torb: (*direction, 0), zen: (0, 0, 32)})
            self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))
            self.assertEqual(npc_control.start(match, torb), bot.entity)
            return run, torb, zen, bot, hp

        catalog = npc.load_catalog(Path(os.environ['ORPHEUS_NPC_CATALOG_DIR']) / 'pve_npcs_174.json')
        self.assertEqual({item.name for item in catalog.values()},
                         {'Trooper', 'Enforcer', 'Sniper', 'Assassin', 'Heavy Assault'})
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, _, _, _, hp = start_encounter(definition)
                for first_attack_offset in range(1, 201):
                    run.step({})
                    if hp.total < 350:
                        break
                else:
                    self.fail('NPC did not damage Torbjorn')

                for lethal in (True, False):
                    with self.subTest(lethal=lethal):
                        run, torb, zen, bot, hp = start_encounter(definition)
                        self.advance(run, first_attack_offset - 1)
                        self.assertEqual(hp.total, 350)
                        self.assertEqual(zen.stats.value(stats.HEALING), 0)
                        original_body = torb.body
                        # Leave one point of temporary armour to test its consumption on impact.
                        hp.now[combat.HEALTH_POOL] = 1 if lethal else 100
                        hp.now[combat.ARMOUR_POOL] = 0
                        hp.over_armour.amount = 1
                        run.match.combat._sync_body_health(torb, hp)
                        x, y, z = torb.mover.position
                        zen.mover.position = [x + 3, y, z]
                        zen.mover.body = None

                        with patch.object(hp, 'damage', wraps=hp.damage) as damage:
                            run.step({})
                        self.assertTrue(damage.called)
                        self.assertTrue(phase_defenses.transcendence_invulnerable(zen, run.frame))
                        self.assertEqual(hp.over_armour.amount, 0)
                        if lethal:
                            self.assertTrue(hp.dead)
                            self.assertEqual(hp.killer, bot.entity)
                            self.assertEqual(hp.death_tick, run.frame)
                            self.assertEqual(hp.total, 0)
                            self.assertIsNone(hp.over_armour.source)
                            self.assertEqual(torb.body, original_body)
                            self.assertEqual(zen.stats.value(stats.HEALING), 0)
                        else:
                            self.assertFalse(hp.dead)
                            self.assertAlmostEqual(zen.stats.value(stats.HEALING), 4.8)

    def test_phase_end_restores_collision_damage_and_primary_marking(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, behind = self.arena(name)
                self.cast(run, defender)
                self.advance(run, 350)
                self.assertFalse(phase_defenses.phase(defender)[0])
                before = self.health(run, defender).total
                self.fire(run, owner, defender)
                self.assertEqual(self.health(run, defender).total, before - 70)
                self.assertEqual(self.health(run, behind).total, 200)
                self.assertIn((owner.entity, defender.body), run.match.combat.torbjorn_marks)

    def test_quick_melee_and_hammer_do_no_damage_or_charge_during_phase(self):
        for name in ("Reaper", "Moira", "Mei"):
            for hammer in (False, True):
                with self.subTest(defense=name, hammer=hammer):
                    run, owner, defender, _ = self.arena(name, distance=1.5, behind_distance=10)
                    if hammer:
                        run.frame += 1
                        owner.take_commands([Command(run.frame, action=2)])
                        self.advance(run, 45)
                    self.cast(run, defender)
                    hp = self.health(run, defender)
                    before = hp.total
                    run.step({owner: (0, 0, 1 if hammer else 0x800)})
                    self.advance(run, 35)
                    self.assertEqual(hp.total, before)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                    for _ in range(330):
                        if not phase_defenses.phase(defender)[0]:
                            break
                        run.step({})
                    self.assertFalse(phase_defenses.phase(defender)[0])
                    self.assertEqual(hp.total, before)
                    # Let the original attack recover, even after the shorter Fade.
                    self.advance(run, 50)
                    x, y, z = owner.mover.position
                    defender.mover.position = [x, y, z + 1.5]
                    defender.mover.body = None
                    run.step({})
                    direction = self.body_aim(run, owner, defender)
                    run.step({owner: (*direction, 1 if hammer else 0x800)})
                    self.advance(run, 35)
                    damage = 55 if hammer else 30
                    self.assertEqual(before - hp.total, damage)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), damage)

    def test_phase_activation_removes_existing_primary_mark_via_real_gate_flags(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, _ = self.arena(name)
                self.fire(run, owner, defender)
                fight = run.match.combat
                mark = fight.torbjorn_marks[owner.entity, defender.body]
                self.cast(run, defender)
                self.assertNotIn((owner.entity, defender.body), fight.torbjorn_marks)
                self.assertNotIn(mark.instance, mark.script.component.instances)

    def test_launched_pool_burn_stops_at_phase_and_remounts_after_exit(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, behind = self.arena(name, distance=5, behind_distance=20)
                fight = run.match.combat
                owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
                run.step({owner: (16384, 4000, 32)})
                self.advance(run, 45)
                run.step({owner: (16384, 4000, 1)})
                for _ in range(100):
                    if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                        break
                    run.step({})
                pool, = fight.molten_pools.values()
                self.assertEqual(pool.phase, 1)
                self.assertIs(pool.owner, owner)
                defender.mover.position = list(pool.position)
                defender.mover.body = None
                self.advance(run, 5)
                key = owner.entity, defender.body
                burn = fight.molten_burns[key]
                self.cast(run, defender)
                damage_before = owner.stats.value(stats.HERO_DAMAGE)
                self.assertGreater(damage_before, 0)
                self.assertNotIn(key, fight.molten_burns)
                self.assertNotIn(burn.instance, burn.script.component.instances)
                self.assertIn(pool.entity, fight.molten_pools)
                self.assertEqual(self.health(run, behind).total, 200)
                for _ in range(350):
                    if not phase_defenses.phase(defender)[0]:
                        break
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage_before)
                    self.assertNotIn(key, fight.molten_burns)
                    run.step({})
                self.assertFalse(phase_defenses.phase(defender)[0])
                self.advance(run, 5)
                self.assertGreater(owner.stats.value(stats.HERO_DAMAGE), damage_before)
                self.assertIsNot(fight.molten_burns[key], burn)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_ceiling_rebound_passes_ghosts_but_stops_on_cryo(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, _ = self.arena(name, distance=6.5, behind_distance=30)
                fight = run.match.combat
                x, y, z = owner.mover.position
                run.match.collision_world = World(
                    box(x - 35, x + 35, y - 1, y, z - 35, z + 35)
                    + box(x - 8, x + 8, y + 2.5, y + 3, z - 4, z + 12)
                )
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (0, -7000, 32)})
                self.advance(run, 45)
                self.cast(run, defender)
                hp = self.health(run, defender)
                before = hp.total
                run.step({owner: (0, -7000, 1)})
                flight, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
                for _ in range(20):
                    if flight.segment_start:
                        break
                    run.step({})
                self.assertIn(flight, fight.rockets)
                self.assertGreater(flight.segment_start, 0)
                self.assertFalse(fight.molten_pools)
                self.advance(run, 25)
                self.assertNotIn(flight, fight.rockets)
                self.assertTrue(phase_defenses.phase(defender)[0])
                self.assertEqual(hp.total, before)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                key = (owner.entity, defender.body)
                if name == "Mei":
                    self.assertFalse(fight.molten_pools)
                else:
                    pool, = fight.molten_pools.values()
                    defender.mover.position = list(pool.position)
                    defender.mover.body = None
                    self.advance(run, 5)
                    self.assertEqual(hp.total, before)
                    self.assertNotIn(key, fight.molten_burns)
                for _ in range(285):
                    if not phase_defenses.phase(defender)[0]:
                        break
                    run.step({})
                self.assertFalse(phase_defenses.phase(defender)[0])
                if name == "Mei":
                    run.step({owner: (0, -7000, 1)})
                    self.advance(run, 25)
                    self.assertEqual(hp.total, before - 25)
                    self.assertFalse(fight.molten_pools)
                else:
                    self.advance(run, 8)
                    self.assertLess(hp.total, before)
                    self.assertIn(key, fight.molten_burns)
                    self.assertIs(fight.molten_pools[pool.entity], pool)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_turret_fire_cannot_damage_phase_and_recovers_after_expiry(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, behind = self.arena(name, distance=8, behind_distance=100)
                x, y, z = owner.mover.position
                behind.mover.position = [x + 100, y, z + 100]
                turret = hero_torbjorn.deploy_turret(run.match.combat, owner, (x, y, z + 1.5))
                self.advance(run, 190)
                self.cast(run, defender)
                before = self.health(run, defender).total
                damage_before = owner.stats.value(stats.HERO_DAMAGE)
                owner.body_script.set_ult_charge(0)
                self.advance(run, 20)
                self.assertGreaterEqual(self.health(run, defender).total, before)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage_before)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                self.assertIn(owner.entity, run.match.combat.turrets)
                shots = turret.shots
                self.advance(run, 350)
                self.assertFalse(phase_defenses.phase(defender)[0])
                self.assertGreater(turret.shots, shots)
                self.assertLess(self.health(run, defender).total, before)

    def test_phase_source_cannot_melee_torbjorn_and_fresh_attack_recovers(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, _ = self.arena(name, distance=1.5, behind_distance=20)
                self.cast(run, defender)
                yaw, pitch = self.body_aim(run, defender, owner)
                run.step({defender: (yaw, pitch, 0x800)})
                self.advance(run, 25)
                self.assertEqual(self.health(run, owner).total, 250)
                self.advance(run, 350)
                self.assertEqual(self.health(run, owner).total, 250)
                self.assertFalse(phase_defenses.phase(defender)[0])
                run.step({defender: (yaw, pitch, 0x800)})
                self.advance(run, 25)
                self.assertEqual(self.health(run, owner).total, 225)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual five NPCs")
    def test_primary_passes_phasing_player_and_hits_each_actual_npc_behind(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, defender, behind = self.arena("Reaper", distance=2, behind_distance=100)
                x, y, z = owner.mover.position
                behind.mover.position = [x + 100, y, z + 100]
                actor = npc.spawn(run.match, definition, (x, y, z + 4), team=1-owner.team)
                self.cast(run, defender)
                target = next(t for t in run.match.combat._targets() if t.owner is actor)
                yaw, pitch = aim(run.match.combat._eye(owner),
                                 (x, y + target.height * .5, z + 4))
                hp = run.match.combat.vitals[actor.entity]
                before = hp.total
                immune_hp = self.health(run, defender).total
                run.step({owner: (yaw, pitch, 1)})
                self.advance(run, 20)
                self.assertEqual(before - hp.total, 70)
                self.assertEqual(self.health(run, defender).total, immune_hp)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 70)

    def test_shared_flag_alone_does_not_make_torbjorn_immune(self):
        run, owner, defender, _ = self.arena("Reaper")
        owner.body_script.component.set(10467, True)
        self.assertEqual(phase_defenses.phase(owner), (False, False))
        target = next(t for t in run.match.combat._targets() if t.owner is owner)
        run.match.combat._damage(defender, target, 20, False, None, run.frame)
        self.assertEqual(self.health(run, owner).total, 235)

    def test_actual_ultimate_projectile_passes_ghost_but_stops_on_cryo(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, behind = self.arena(name, behind_distance=5)
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (0, 0, 32)})
                self.advance(run, 45)
                self.cast(run, defender)
                before = self.health(run, defender).total
                self.fire(run, owner, behind)
                self.assertEqual(self.health(run, defender).total, before)
                self.assertEqual(self.health(run, behind).total, 200 if name == "Mei" else 175)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    def test_airborne_globule_resolves_live_phase_for_body_and_ground_contacts(self):
        for name in ("Reaper", "Moira", "Mei"):
            for ground in (False, True):
                with self.subTest(defense=name, ground=ground):
                    run, owner, defender, behind = self.arena(
                        name, distance=1.5 if ground else 2, behind_distance=30 if ground else 5,
                    )
                    fight = run.match.combat
                    hp = self.health(run, defender)
                    rear_hp = self.health(run, behind)
                    before = hp.total
                    charge = owner.body_script.component.entity_var(342)
                    owner.body_script.set_ult_charge(10000)
                    run.step({owner: (0, 0, 32)})
                    self.advance(run, 45)
                    self.assertEqual(charge.value(), 0)
                    self.assertFalse(phase_defenses.phase(defender)[0])
                    yaw, pitch = (0, 4000) if ground else self.body_aim(run, owner, behind)
                    run.step({owner: (yaw, pitch, 1)})
                    globule, = fight.rockets
                    self.assertIs(globule.shooter, owner)
                    self.assertEqual(globule.spawn, molten_core.POOL)
                    self.assertEqual(globule.distance, 0)
                    run.step({defender: (-32768, 0, 8)})
                    self.assertTrue(phase_defenses.phase(defender)[0])
                    self.assertIn(globule, fight.rockets)

                    for _ in range(30):
                        if globule not in fight.rockets:
                            break
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(hp.total, before)
                    self.assertEqual(charge.value(), 0)
                    self.assertFalse(fight.torbjorn_marks)
                    direct = 25 if not ground and name != "Mei" else 0
                    self.assertEqual(rear_hp.total, 200 - direct)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), direct)
                    if not ground or name == "Mei":
                        self.assertFalse(fight.molten_pools)
                        continue

                    pool, = fight.molten_pools.values()
                    self.assertIs(pool.owner, owner)
                    self.assertGreater(pool.position[2], defender.mover.position[2])
                    x, y, z = pool.position
                    defender.mover.position = [x + 30, y, z]
                    defender.mover.body = None
                    for _ in range(40):
                        if pool.phase == 1:
                            break
                        run.step({})
                    self.assertEqual(pool.phase, 1)
                    behind.mover.position = list(pool.position)
                    behind.mover.body = None
                    run.step({})
                    self.assertAlmostEqual(rear_hp.total, 197.44)
                    self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 2.56)
                    self.assertEqual(charge.value(), 0)
                    self.assertIn((owner.entity, behind.body), fight.molten_burns)

    def test_active_phase_snapshot_is_passive_and_timer_end_is_observable(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        defender = players[1]
        reaper = next(hero for hero in heroes().values() if hero.name == "Reaper")
        session.apply("switch_hero", {"account": defender.account_lo, "hero": reaper.guid})
        peers[1].commands(defender.body_script.frame + 1, 6, 8)
        self.assertTrue(phase_defenses.phase(defender)[0])
        before = snapshot_digest(case.capture(session))
        self.assertEqual(before, snapshot_digest(case.capture(session)))
        self.assertTrue(phase_defenses.phase(defender)[0])
        peers[1].commands(defender.body_script.frame + 1, 350, 0)
        self.assertFalse(phase_defenses.phase(defender)[0])
        self.assertNotEqual(before, snapshot_digest(case.capture(session)))

    def test_reappearance_is_vulnerable_while_the_broader_action_flag_is_still_set(self):
        for name in ("Reaper", "Moira", "Mei"):
            with self.subTest(defense=name):
                run, owner, defender, behind = self.arena(name)
                self.cast(run, defender)
                for _ in range(320):
                    if not phase_defenses.phase(defender)[0]:
                        break
                    run.step({})
                self.assertFalse(phase_defenses.phase(defender)[0])
                self.assertTrue(defender.body_script.component.entity_var(10467).value())
                before = self.health(run, defender).total
                self.fire(run, owner, defender)
                self.assertEqual(self.health(run, defender).total, before - 70)
                self.assertEqual(self.health(run, behind).total, 200)
                self.assertIn((owner.entity, defender.body), run.match.combat.torbjorn_marks)
