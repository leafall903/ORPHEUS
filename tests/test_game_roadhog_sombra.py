"""Real Sombra Hack and EMP against Roadhog's authored skills."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_ana as ana
import test_game_roadhog_defenses as phases
import test_game_roadhog_winston as winston
import test_mccree_flashbang as flashbang
from test_game_combat import aim

from ow174.game import defenses, sombra
from ow174.game.collision import World
from ow174.game.combat import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class RoadhogSombraTests(unittest.TestCase):
    health = phases.RoadhogDefenseTests.health

    def session(self, distance=4):
        run, hog, caster, rear = phases.RoadhogDefenseTests().session("Sombra", distance=distance)
        rear.mover.position[0] = 30
        rear.mover.body = None
        for _ in range(35):
            self.step(run, hog, caster)
        return run, hog, caster

    def step(self, run, hog, caster, *, hack=0, fire=0, away=False):
        fight = run.match.combat
        x, y, z = hog.mover.position
        caster_aim = aim(fight._eye(caster), (x, y+1, z))
        x, y, z = caster.mover.position
        hog_aim = aim(fight._eye(hog), (x, y+0.75, z))
        if away:
            hog_aim = (hog_aim[0] % 65536 - 32768, 0)
        run.step({hog: (*hog_aim, fire), caster: (*caster_aim, hack)})

    def land(self, run, hog, caster, button, *, away=False):
        for offset in range(75):
            self.step(run, hog, caster, hack=button if button == 2 or offset == 0 else 0, away=away)
            if sombra.locked(hog):
                return
        self.fail("Actual Sombra input did not open the receiver")

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_actual_hack_and_emp_select_player_alongside_all_five_manual_npcs(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for button in (2, 32):
                with self.subTest(npc=definition.name, button=button):
                    run, hog, caster = self.session(distance=6)
                    match = run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    hog.team, caster.team = 1, 0
                    x, y, z = hog.mover.position
                    bot = npc.spawn(match, definition, (x, y, z + 3), team=1)
                    fight = match.combat
                    target = next(t for t in fight._targets() if t.owner is bot)
                    pool = fight.vitals_of(target)
                    before = pool.total
                    if button == 32:
                        component = caster.body_script.component
                        caster.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
                    self.land(run, hog, caster, button)
                    self.assertTrue(sombra.locked(hog))
                    self.assertEqual(pool.total, before)
                    self.assertFalse(any(i.graph.index == 0xB1F
                                         for i in bot.body_script.component.instances.values()))

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_emp_leaves_live_manual_npc_attacks_on_roadhog_running(self):
        import test_game_roadhog_npcs as actual

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        for definition in fixture.definitions.values():
            with self.subTest(npc=definition.name):
                baseline = self.manual_npc_attack_during_emp(actual, definition, cast=False)
                pulse = self.manual_npc_attack_during_emp(actual, definition, cast=True)
                self.assertEqual(pulse, baseline)

    def manual_npc_emp_scene(self, actual, definition, distance):
        from test_game_combat import in_world

        fixture = actual.RoadhogFiveNpcTests()
        run, hog, bot, npc_pool, look = fixture.session(definition, distance=distance, caster=True)
        match = run.match
        caster = run.players[1]
        witness = match.add_player(8, "EMP witness", caster.hero.guid, 1, False)
        in_world(match, witness)
        self.assertTrue(match.switch_hero(witness, caster.hero))
        run.players.append(witness)
        for player in (caster, witness):
            player.client = actual.NpcClient(player)
        self.assertTrue(match.switch_hero(caster, phases.heroes()[sombra.SOMBRA]))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = caster.team = 0
        bot.team = witness.team = 1
        x, y, z = hog.mover.position
        for player, offset in ((caster, 5), (witness, 8)):
            player.mover.position[:] = (x + offset, y, z)
            player.mover.body = None
        for _ in range(35):
            run.step({})
        return run, hog, caster, witness, bot, npc_pool, look

    def manual_npc_attack_during_emp(self, actual, definition, *, cast):
        from ow174.game import npc_control

        assassin = definition.name == "Assassin"
        distance = 2 if assassin else 10 if definition.name == "Sniper" else 4
        run, hog, caster, witness, bot, npc_pool, look = self.manual_npc_emp_scene(
            actual, definition, distance,
        )
        match = run.match
        fight = match.combat
        hog_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        hog.mover.yaw, hog.mover.pitch = look
        self.assertEqual(npc_control.start(match, hog), bot.entity)
        for _ in range(180):
            run.step({})
            if hog_pool.total < 600:
                break
        self.assertLess(hog_pool.total, 600)
        npc_health = npc_pool.total
        script = caster.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value() if cast else 0)
        receivers = sombra.eligible(fight, caster, reach=15, cone=-1, barrier_blocks=False)
        self.assertEqual([entity for _, _, entity in receivers], [witness.body])
        run.step({caster: (16384, 0, 32 if cast else 0)})
        samples = []
        locked_health = None
        for _ in range(125):
            run.step({})
            if locked_health is None and sombra.locked(witness):
                locked_health = hog_pool.total
            pinned = fight.pins.get(bot.entity) == hog.body
            self.assertEqual(pinned, assassin)
            self.assertTrue(bot.firing)
            self.assertEqual(npc_pool.total, npc_health)
            self.assertFalse(sombra.locked(hog))
            self.assertFalse(any(i.graph.index == 0xB1F
                                 for i in bot.body_script.component.instances.values()))
            samples.append((hog_pool.total, bot.attack_input, pinned))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(sombra.locked(witness), cast)
        if cast:
            self.assertIsNotNone(locked_health)
            self.assertLess(hog_pool.total, locked_health)
        self.assertFalse(hog_pool.dead)
        return samples

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graphs required")
    def test_emp_leaves_heavy_charge_and_sniper_reposition_running(self):
        import test_game_roadhog_npcs as actual

        fixture = actual.RoadhogFiveNpcTests
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        for name in ("Heavy Assault", "Sniper"):
            with self.subTest(npc=name):
                definition = next(d for d in fixture.definitions.values() if d.name == name)
                baseline = self.npc_special_during_emp(actual, definition, cast=False)
                pulse = self.npc_special_during_emp(actual, definition, cast=True)
                self.assertEqual(pulse, baseline)

    def npc_special_during_emp(self, actual, definition, *, cast):
        from test_game_mover import box

        from ow174.game import npc_control, reinhardt_charge, sniper_reposition

        charging = definition.name == "Heavy Assault"
        run, hog, caster, witness, bot, npc_pool, look = self.manual_npc_emp_scene(
            actual, definition, 6 if charging else 2,
        )
        match = run.match
        fight = match.combat
        if charging:
            x, y, z = hog.mover.position
            floor = box(x - 40, x + 40, y - 1, y, z - 25, z + 25)
            wall = box(x - 9, x + 9, y, y + 8, z - 15, z - 14)
            match.collision_world = World(floor + wall)
            hog.mover.yaw, hog.mover.pitch = look
            self.assertEqual(npc_control.start(match, hog, action="charge"), bot.entity)
            for _ in range(90):
                run.step({})
                if reinhardt_charge.pinned(fight, hog.body):
                    break
            self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        script = caster.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value() if cast else 0)
        run.step({caster: (16384, 0, 32 if cast else 0)})
        destination = None
        npc_health = npc_pool.total
        samples = []
        locked = False
        for offset in range(240):
            if not charging and offset == 17:
                hog.mover.yaw, hog.mover.pitch = look
                self.assertEqual(npc_control.start(match, hog), bot.entity)
                self.assertEqual(npc_control.start(match, hog, action="reposition"), bot.entity)
                state = sniper_reposition.move_state(bot)
                destination, speed = state.move_request()
                self.assertEqual(speed, 20)
            run.step({})
            active = (reinhardt_charge.pinned(fight, hog.body) if charging
                      else bot.entity in fight.sniper_repositions)
            if not locked and sombra.locked(witness):
                self.assertTrue(active)
                locked = True
            hp = self.health(run, hog)
            samples.append((active, tuple(hog.mover.position), bot.position,
                            bot.velocity, bot.attack_input, hp))
            self.assertFalse(sombra.locked(hog))
            self.assertEqual(npc_pool.total, npc_health)
            self.assertFalse(any(i.graph.index == 0xB1F
                                 for i in bot.body_script.component.instances.values()))
            if active and not charging:
                self.assertFalse(bot.attack_input)
                self.assertEqual(hp, 600)
            if not active and hp < 600:
                break
        self.assertFalse(active)
        self.assertEqual(locked, cast)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        if not charging:
            self.assertIsNotNone(destination)
            self.assertEqual(bot.position, tuple(destination))
            self.assertEqual(bot.velocity, (0, 0, 0))
            self.assertFalse(state.active)
            self.assertTrue(bot.attack_input)
        self.assertEqual(hp, 300 if charging else 500)
        return samples

    def test_hack_interrupts_breather_and_only_masks_ability_buttons(self):
        run, hog, caster = self.session()
        fight = run.match.combat
        self.step(run, hog, caster, fire=16)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        self.land(run, hog, caster, 2)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(self.health(run, hog), 600)
        script = hog.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        self.step(run, hog, caster, fire=8|16|32)
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.step(run, hog, caster, fire=2)
        for _ in range(30):
            self.step(run, hog, caster)
        self.assertEqual(self.health(run, caster), 19950)
        self.assertTrue(sombra.locked(hog))
        position = tuple(hog.mover.position)
        for _ in range(12):
            flashbang.FlashbangTests().step(run, caster, hog, forward=127)
        self.assertNotEqual(tuple(hog.mover.position), position)
        self.assertTrue(sombra.locked(hog))
        for _ in range(320):
            self.step(run, hog, caster)
        self.assertFalse(sombra.locked(hog))
        self.step(run, hog, caster, fire=8)
        for _ in range(35):
            self.step(run, hog, caster)
        self.assertEqual(self.health(run, caster), 19920)

    def test_hack_leaves_quick_melee_available(self):
        run, hog, caster = self.session(distance=1.5)
        self.land(run, hog, caster, 2)
        self.step(run, hog, caster, fire=0x800)
        self.assertTrue(sombra.locked(hog))
        self.assertEqual(self.health(run, caster), 19970)

    def test_replacing_target_during_hack_windup_rejects_the_old_body(self):
        run, hog, caster = self.session()
        self.step(run, hog, caster, hack=2)
        for _ in range(10):
            self.step(run, hog, caster, hack=2)
        windup = sombra.instance_for(caster, 0xB1B).states[54]
        self.assertTrue(windup.active)
        old_body, old_script = hog.body, hog.body_script
        position = tuple(hog.mover.position)
        self.assertTrue(run.match.switch_hero(hog, hog.hero))
        hog.mover.position[:] = position
        hog.mover.body = None
        self.assertNotEqual(hog.body, old_body)
        self.assertTrue(old_script.retired)
        self.step(run, hog, caster, hack=2)
        self.assertFalse(windup.active)
        self.assertFalse(sombra.locked(hog))
        self.step(run, hog, caster)
        self.land(run, hog, caster, 2)
        self.assertTrue(sombra.locked(hog))

    def ana_scene(self):
        run, hog, caster = self.session()
        sleeper = run.players[2]
        run.match.switch_hero(sleeper, next(h for h in phases.heroes().values() if h.name == "Ana"))
        x, y, z = hog.mover.position
        sleeper.mover.position[:] = (x+4, y, z)
        sleeper.mover.body = None
        for _ in range(10):
            self.step(run, hog, caster)
        return run, hog, caster, sleeper

    def test_lethal_grenade_retires_hacked_body_and_fresh_emp_can_reapply(self):
        run, hog, caster, sleeper = self.ana_scene()
        fight = run.match.combat
        self.land(run, hog, caster, 2)
        old_body, old_script = hog.body, hog.body_script
        position = tuple(hog.mover.position)
        target = next(t for t in fight._targets() if t.owner is hog)
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 50)
        fight._sync_body_health(hog, vitals)
        ana.RoadhogAnaTests().step(run, hog, sleeper, cast=16)
        for _ in range(70):
            if fight.is_dead(hog):
                break
            self.step(run, hog, caster)
        self.assertTrue(fight.is_dead(hog))
        self.assertFalse(sombra.locked(hog))
        self.step(run, hog, caster, fire=16)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        for _ in range(700):
            if hog.body != old_body:
                break
            self.step(run, hog, caster)
        self.assertNotEqual(hog.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertFalse(sombra.locked(hog))
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        hog.mover.position[:] = position
        hog.mover.body = None
        for _ in range(5):
            self.step(run, hog, caster)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.land(run, hog, caster, 32)
        self.assertEqual(self.health(run, hog), 600)

    def test_emp_outlasts_earlier_sleep_without_ability_replay(self):
        run, hog, caster, sleeper = self.ana_scene()
        fight = run.match.combat
        sleep_until = ana.RoadhogAnaTests().land(run, hog, sleeper, 8)
        for _ in range(60):
            self.step(run, hog, caster)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.land(run, hog, caster, 32)
        self.assertEqual(fight.sleep[hog.body], sleep_until)
        self.assertTrue(fight.is_stunned(hog))
        self.assertTrue(sombra.locked(hog))

        while run.frame < sleep_until - 1:
            self.step(run, hog, caster, fire=2|8|16)
            self.assertFalse(hog.body_script.component.held)
        self.step(run, hog, caster)
        self.assertFalse(fight.is_stunned(hog))
        self.assertTrue(sombra.locked(hog))
        self.assertEqual(self.health(run, caster), 20000)
        self.step(run, hog, caster, fire=2|8|16)
        for _ in range(24):
            self.step(run, hog, caster)
        self.assertEqual(self.health(run, caster), 19950)
        self.assertTrue(sombra.locked(hog))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        for _ in range(130):
            if not sombra.locked(hog):
                break
            self.step(run, hog, caster)
        self.assertFalse(sombra.locked(hog))
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.step(run, hog, caster, fire=16)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)

    def test_sleep_outlasts_earlier_emp_without_ability_replay(self):
        run, hog, caster, sleeper = self.ana_scene()
        fight = run.match.combat
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.land(run, hog, caster, 32)
        for _ in range(60):
            self.step(run, hog, caster)
        sleep_until = ana.RoadhogAnaTests().land(run, hog, sleeper, 8)
        self.assertTrue(sombra.locked(hog))
        while sombra.locked(hog) and run.frame < sleep_until - 1:
            self.step(run, hog, caster, fire=2|8|16)
            self.assertFalse(hog.body_script.component.held)
        self.assertFalse(sombra.locked(hog))
        self.assertTrue(fight.is_stunned(hog))
        while run.frame < sleep_until - 1:
            self.step(run, hog, caster, fire=2|8|16)
            self.assertFalse(hog.body_script.component.held)
        self.step(run, hog, caster)
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(self.health(run, caster), 20000)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.step(run, hog, caster, fire=16)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)

    def test_sleep_cancels_buffered_melee_in_either_emp_order(self):
        for emp_first in (False, True):
            with self.subTest(emp_first=emp_first):
                self.check_sleep_emp_melee_buffer(emp_first)

    def check_sleep_emp_melee_buffer(self, emp_first):
        run, hog, caster, sleeper = self.ana_scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, caster.team, sleeper.team = 0, 1, 1
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x, y, z - 1.5)
        caster.mover.body = None
        run.step({})
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(ULT_COST).value())
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        before = self.health(run, caster)
        held = {hog: (-32768, 0, 0x800)}
        run.step(held)
        first = run.frame
        run.step({})
        run.step(held)
        melee = next(s for i in hog.body_script.component.instances.values() if i.graph.index == 0x0043
                     for s in i.states.values() if isinstance(s, nodes.Ability))
        hit = melee.instance.state(4)
        self.assertEqual(self.health(run, caster), before - 30)
        self.assertEqual(hit.counter, 1)
        self.assertTrue(melee.presses)

        for emp in (emp_first, not emp_first):
            source, button = (caster, 32) if emp else (sleeper, 8)
            look = aim(fight._eye(source), (x, y + 1, z))
            run.step({**held, source: (*look, button)})
            while run.frame < first + 61:
                landed = sombra.locked(hog) if emp else hog.body in fight.sleep
                if landed:
                    break
                run.step({**held, source: (*look, 0)})
            if emp:
                self.assertTrue(sombra.locked(hog))
            else:
                self.assertIn(hog.body, fight.sleep)
            self.assertEqual(bool(melee.presses), hog.body not in fight.sleep)
        self.assertLess(run.frame, first + 62)
        self.assertTrue(sombra.locked(hog))
        self.assertTrue(fight.is_stunned(hog))
        while run.frame < first + 62:
            run.step(held)
        self.assertEqual(hit.counter, 1)
        self.assertEqual(self.health(run, caster), before - 30)
        for _ in range(480):
            if not (sombra.locked(hog) or fight.is_stunned(hog)):
                break
            run.step({})
        self.assertFalse(sombra.locked(hog))
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(hit.counter, 1)
        self.assertEqual(self.health(run, caster), before - 30)
        run.step(held)
        self.assertEqual(hit.counter, 2)
        self.assertEqual(self.health(run, caster), before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_emp_spends_exact_charge_and_releases_live_hook(self):
        run, hog, caster = self.session(distance=10)
        fight = run.match.combat
        self.step(run, hog, caster, fire=8)
        for _ in range(40):
            self.step(run, hog, caster)
            if fight.hooks.controlled(caster.body):
                break
        self.assertTrue(fight.hooks.controlled(caster.body))
        # A separate Sombra can cast while the hook victim is controlled.
        emp = run.players[2]
        run.match.switch_hero(emp, caster.hero)
        x, y, z = hog.mover.position
        emp.mover.position[:] = (x+3, y, z)
        emp.mover.body = None
        for _ in range(6):
            run.step({})
        script = emp.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(run, hog, emp, hack=32)
        for _ in range(70):
            self.step(run, hog, emp)
            if sombra.locked(hog):
                break
        self.assertTrue(sombra.locked(hog))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(caster.body))
        self.assertEqual(self.health(run, hog), 600)

    def test_emp_interrupts_breather_and_starts_cooldown_that_outlasts_lock(self):
        run, hog, caster = self.session()
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is hog)
        vitals = fight.vitals_of(target)
        vitals.damage(400)
        fight._sync_body_health(hog, vitals)
        breather = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x0240)
        ability = breather.state(breather.graph.node(1).state)
        heal = breather.state(breather.graph.node(18).state)
        self.step(run, hog, caster, fire=16)
        for _ in range(50):
            self.step(run, hog, caster)
        self.assertTrue(heal.active)
        self.assertEqual(heal.counter, 1)
        self.assertGreater(vitals.total, 200)
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertEqual(ability.remaining(), 0)

        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.land(run, hog, caster, 32)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(heal.active)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.assertAlmostEqual(ability.remaining(), 8, delta=.016)
        stopped = vitals.total
        self.step(run, hog, caster, fire=16)
        for _ in range(420):
            if not sombra.locked(hog):
                break
            self.step(run, hog, caster)
        self.assertFalse(sombra.locked(hog))
        self.assertEqual(vitals.total, stopped)
        self.assertGreater(ability.remaining(), .2)
        self.step(run, hog, caster, fire=16)
        self.assertEqual(heal.counter, 1)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        for _ in range(200):
            if ability.remaining() == 0:
                break
            self.step(run, hog, caster)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(vitals.total, stopped)
        self.step(run, hog, caster, fire=16)
        for _ in range(60):
            self.step(run, hog, caster)
        self.assertEqual(heal.counter, 2)
        self.assertGreater(vitals.total, stopped)

    def test_whole_hog_does_not_replay_when_scrap_fills_charge_at_hack_or_emp_expiry(self):
        for button in (2, 32):
            with self.subTest(sombra_input=button):
                run, hog, caster = self.session()
                if button == 32:
                    script = caster.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.land(run, hog, caster, button)
                start = run.frame
                for _ in range(400):
                    if not sombra.locked(hog):
                        break
                    self.step(run, hog, caster)
                self.assertFalse(sombra.locked(hog))
                self.check_earned_charge_at_lock_expiry(button, run.frame - start)

    def check_earned_charge_at_lock_expiry(self, button, duration):
        run, hog, caster = self.session()
        fight = run.match.combat
        script = hog.body_script
        ultimate, = script.ultimates
        charge = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 50)
        if button == 32:
            sombra_script = caster.body_script
            sombra_script.set_ult_charge(sombra_script.component.entity_var(ULT_COST).value())
        self.land(run, hog, caster, button)
        end = run.frame + duration

        while run.frame < end - 8:
            self.step(run, hog, caster)
        self.assertTrue(sombra.locked(hog))
        self.assertEqual(charge.value(), cost - 50)
        self.step(run, hog, caster, fire=2 | 32)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), cost - 50)
        while run.frame < end - 2:
            self.step(run, hog, caster)
            self.assertTrue(sombra.locked(hog))
            self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(self.health(run, caster), 19950)
        self.assertNotIn(caster.body, fight.impulses.live)

        # Press again at full charge with the lock still active and release at expiry.
        self.step(run, hog, caster, fire=32)
        self.assertTrue(sombra.locked(hog))
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.step(run, hog, caster)
        self.assertEqual(run.frame, end)
        self.assertFalse(sombra.locked(hog))
        for _ in range(20):
            self.step(run, hog, caster)
            self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
            self.assertEqual(charge.value(), cost)
        self.assertEqual(self.health(run, caster), 19950)
        self.assertNotIn(caster.body, fight.impulses.live)

        self.step(run, hog, caster, fire=32)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), 0)
        for _ in range(65):
            self.step(run, hog, caster)
            if caster.body in fight.impulses.live:
                break
        self.assertIn(caster.body, fight.impulses.live)
        self.assertLess(self.health(run, caster), 19950)
        self.assertEqual(charge.value(), 0)

    def test_emp_interrupts_running_whole_hog_without_refund(self):
        run, hog, caster = self.session()
        fight = run.match.combat
        for player in (hog, caster):
            script = player.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        ultimate, = hog.body_script.ultimates
        self.step(run, hog, caster, fire=32, away=True)
        for _ in range(55):
            self.step(run, hog, caster, away=True)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.land(run, hog, caster, 32, away=True)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        before = self.health(run, caster)
        self.step(run, hog, caster, fire=32)
        for _ in range(65):
            self.step(run, hog, caster)
        self.assertEqual(self.health(run, caster), before)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertNotIn(caster.body, fight.impulses.live)

    def test_actual_emp_locks_roadhog_with_immortality_field_still_active(self):
        from test_game_roadhog_baptiste import BaptisteFieldTests

        from ow174.game.content import heroes

        run, hog, caster = self.session(distance=6)
        match = run.match
        fight = match.combat
        bap = run.players[2]
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = bap.team = 1
        caster.team = 0
        match.switch_hero(bap, next(h for h in heroes().values() if h.name == "Baptiste"))
        x, y, z = hog.mover.position
        bap.mover.position[:] = (x + 2, y, z + 1)
        bap.mover.body = None
        device = BaptisteFieldTests.deploy(run, bap)
        target = next(t for t in fight._targets() if t.owner is hog)
        vitals = fight.vitals_of(target)
        self.assertEqual(fight.baptiste_fields.health_floor(target, vitals), 60)
        component = caster.body_script.component
        caster.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
        self.land(run, hog, caster, 32)
        self.assertTrue(sombra.locked(hog))
        self.assertIs(fight.baptiste_fields.devices[bap.body], device)
        self.assertEqual(device.health, 150)
        self.assertEqual(fight.baptiste_fields.health_floor(target, vitals), 60)

    def test_field_healing_and_emp_lock_end_independently(self):
        from test_game_roadhog_baptiste import BaptisteFieldTests

        from ow174.game.content import heroes

        run, hog, caster = self.session(distance=6)
        match = run.match
        fight = match.combat
        bap = run.players[2]
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = bap.team = 1
        caster.team = 0
        match.switch_hero(bap, next(h for h in heroes().values() if h.name == "Baptiste"))
        x, y, z = hog.mover.position
        bap.mover.position[:] = (x + 2, y, z + 1)
        bap.mover.body = None
        component = caster.body_script.component
        caster.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
        self.land(run, hog, caster, 32)
        target = next(t for t in fight._targets() if t.owner is hog)
        vitals = fight.vitals_of(target)
        vitals.now[HEALTH_POOL] = 45

        device = BaptisteFieldTests.deploy(run, bap)
        self.step(run, hog, caster, fire=16)
        self.assertTrue(sombra.locked(hog))
        self.assertEqual(vitals.total, 60)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        for _ in range(350):
            self.step(run, hog, caster)
            if not sombra.locked(hog):
                break

        self.assertFalse(sombra.locked(hog))
        self.assertIs(fight.baptiste_fields.devices[bap.body], device)
        self.assertEqual(vitals.total, 60)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.step(run, hog, caster, fire=16)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        for _ in range(160):
            self.step(run, hog, caster)
        self.assertNotIn(bap.body, fight.baptiste_fields.devices)
        self.assertAlmostEqual(vitals.total, 360)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)

    def test_emp_drains_only_target_shield_pool(self):
        run, hog, caster = self.session()
        fight = run.match.combat
        target = run.players[2]
        run.match.switch_hero(target, phases.heroes()[0x02E0000000000020])
        x, y, z = hog.mover.position
        target.mover.position[:] = (x+2, y, z)
        target.mover.body = None
        for _ in range(10):
            self.step(run, hog, caster)
        self.assertEqual(self.health(run, target), 200)
        vitals = fight.vitals[target.body]
        self.assertEqual(vitals.now[SHIELDS_POOL], 150)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.land(run, hog, caster, 32)
        self.assertTrue(sombra.locked(target))
        self.assertEqual(vitals.now[SHIELDS_POOL], 0)
        self.assertEqual(vitals.now[HEALTH_POOL], 50)
        self.assertEqual(vitals.now[ARMOUR_POOL], 0)
        self.assertEqual(self.health(run, hog), 600)

    def test_emp_cancels_pending_grasp_or_drains_existing_shields(self):
        for granted in (False, True):
            with self.subTest(granted=granted):
                self.check_sombra_grasp(granted=granted, emp=True)

    def test_hack_cancels_pending_grasp_and_preserves_existing_shields(self):
        for granted in (False, True):
            with self.subTest(granted=granted):
                self.check_sombra_grasp(granted=granted, emp=False)

    def check_sombra_grasp(self, *, granted, emp):
        run, hog, caster = self.session()
        fight = run.match.combat
        sigma = run.players[2]
        run.match.switch_hero(sigma, phases.heroes()[0x02E000000000023B])
        x, y, z = hog.mover.position
        sigma.mover.position[:] = (x, y, z + 5)
        caster.mover.position[:] = (x + 4, y, z + 3)
        sigma.mover.body = caster.mover.body = None
        idle = {sigma: (-32768, 0, 0)}
        for _ in range(35):
            run.step(idle)
        run.step({sigma: (-32768, 0, 8)})
        for _ in range(6):
            run.step(idle)
        angles = aim(fight._eye(hog), (x, y + 1, z + 5))
        run.step({hog: (*angles, 2), **idle})
        for _ in range(150 if granted else 45):
            run.step(idle)
        vitals = fight.vitals[sigma.body]
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertEqual(grasp.vars[21312].value(), 50)
        self.assertEqual(grasp.states[1].active, not granted)
        if granted:
            self.assertAlmostEqual(vitals.temporary_shields[defenses.SHIELD_TAG].now, 30, places=5)
        else:
            self.assertFalse(vitals.temporary_shields)
        self.assertGreater(vitals.now[SHIELDS_POOL], 0)
        base_health, base_armour = vitals.now[HEALTH_POOL], vitals.now[ARMOUR_POOL]
        base_shields = vitals.now[SHIELDS_POOL]
        if emp:
            script = caster.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            self.land(run, hog, caster, 32)
        else:
            hack_aim = aim(fight._eye(caster), (x, y + 1, z + 5))
            run.step({caster: (*hack_aim, 0), **idle})
            for _ in range(75):
                run.step({caster: (*hack_aim, 2), **idle})
                if sombra.locked(sigma):
                    break
            self.assertFalse(sombra.locked(hog))
        self.assertTrue(sombra.locked(sigma))
        self.assertFalse(grasp.states[1].active)
        self.assertEqual(vitals.now[SHIELDS_POOL], 0 if emp else base_shields)
        if granted:
            bonus = vitals.temporary_shields[defenses.SHIELD_TAG]
            self.assertAlmostEqual(bonus.now, 0 if emp else 30, places=5)
        self.assertEqual(vitals.now[HEALTH_POOL], base_health)
        self.assertEqual(vitals.now[ARMOUR_POOL], base_armour)
        self.assertEqual(vitals.ult_healing_deficit, 0)
        run.step({sigma: (-32768, 0, 8)})
        self.assertTrue(sombra.locked(sigma))
        self.assertFalse(grasp.states[1].active)
        for _ in range(120):
            run.step(idle)
        if not granted:
            self.assertFalse(vitals.temporary_shields)
        self.assertEqual(sigma.body in fight.grasp_shields, granted and not emp)

        if not granted and emp:
            for _ in range(1000):
                run.step(idle)
                self.assertFalse(grasp.states[1].active)
                ability = next((state for state in grasp.states.values()
                                if isinstance(state, nodes.Ability) and state.node.pos == 3), None)
                if not sombra.locked(sigma) and ability is not None and ability.remaining() <= 0:
                    break
            else:
                self.fail("Grasp never became ready after EMP")
            run.step({sigma: (-32768, 0, 8)})
            for _ in range(6):
                run.step(idle)
            self.assertTrue(grasp.states[1].active)
            self.assertEqual(grasp.vars[21312].value(), 0)
            run.step({hog: (*angles, 2), **idle})
            for _ in range(150):
                run.step(idle)
            self.assertEqual(grasp.vars[21312].value(), 50)
            self.assertAlmostEqual(vitals.temporary_shields[defenses.SHIELD_TAG].now, 30, places=5)

    def test_emp_pulses_against_deployed_barrier_without_a_body_target(self):
        for case in ("hit", "occluded", "out_of_range", "friendly"):
            with self.subTest(case=case):
                fixture = winston.RoadhogWinstonTests()
                run, hog, owner, caster = fixture.session()
                dome = fixture.cast(run, hog, owner)
                run.match.switch_hero(caster, phases.heroes()[sombra.SOMBRA])
                x, y, z = hog.mover.position
                caster.mover.position[:] = (x, y, z)
                hog.mover.position[:] = (x+22, y, z)
                owner.mover.position[:] = (x+22, y, z+4)
                if case == "out_of_range":
                    caster.mover.position[0] -= 25
                if case == "friendly":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    caster.team = dome.team
                for actor in (hog, owner, caster):
                    actor.mover.body = None
                for _ in range(10):
                    self.step(run, hog, caster)
                fight = run.match.combat
                candidates = sombra.eligible(fight, caster, reach=15, cone=-1, barrier_blocks=False)
                self.assertFalse(list(candidates))
                script = caster.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                wall = World([
                    (x-12, y-3, z+2, x+12, y-3, z+2, x+12, y+10, z+2),
                    (x-12, y-3, z+2, x+12, y+10, z+2, x-12, y+10, z+2),
                ])
                with patch.object(fight, "_collision",
                                  return_value=wall if case == "occluded" else fight._collision()):
                    self.step(run, hog, caster, hack=32)
                    for _ in range(45):
                        self.step(run, hog, caster)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(dome.health, 0 if case == "hit" else 700)
                self.assertEqual(dome.body in fight.winston_bubbles.actors, case != "hit")
                self.assertFalse(sombra.locked(hog))
                self.assertFalse(sombra.locked(owner))

    def test_emp_destroys_exposed_deployed_meshes_without_a_player_receiver(self):
        from test_game_roadhog_orisa_barrier import RoadhogOrisaBarrierTests
        from test_game_roadhog_sigma_barrier import RoadhogSigmaBarrierTests
        from test_game_roadhog_symmetra import RoadhogSymmetraTests

        for name in ("Orisa", "Sigma", "Symmetra"):
            with self.subTest(hero=name):
                if not os.environ.get(f"ORPHEUS_{name.upper()}_COLLISION"):
                    self.skipTest(f"actual {name} barrier mesh required")
                run, hog, caster = self.session()
                match = run.match
                fight = match.combat
                defender = run.players[2]
                hero = next(hero for hero in phases.heroes().values() if hero.name == name)
                self.assertTrue(match.switch_hero(defender, hero))
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team = defender.team = 0
                caster.team = 1
                x, y, z = caster.mover.position
                defender.mover.position[:] = (x, y, z + 9)
                defender.mover.body = None
                for _ in range(35):
                    run.step({})
                if name == "Orisa":
                    shield = RoadhogOrisaBarrierTests().deploy(run, hog, defender)
                elif name == "Sigma":
                    shield = fight.sigma_barriers.actors[defender.body]
                    RoadhogSigmaBarrierTests().deploy(run, hog, defender, shield)
                else:
                    shield = RoadhogSymmetraTests().cast(run, hog, defender)
                for player, offset in ((hog, 30), (defender, 35)):
                    player.mover.position[:] = (x + offset, y, z)
                    player.mover.body = None
                for _ in range(5):
                    run.step({})
                self.assertGreater(shield.health, 0)
                script = caster.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({caster: (0, 0, 32)})
                for _ in range(45):
                    run.step({})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(shield.health, 0)
                self.assertFalse(sombra.locked(hog))
                self.assertFalse(sombra.locked(defender))

    def test_emp_leaves_ice_wall_and_roadhog_behind_it_untouched(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                self.check_emp_ice_wall_cover(projected)

    def check_emp_ice_wall_cover(self, projected):
        from test_game_combat import in_world
        from test_game_roadhog_mei_wall import RoadhogMeiWallTests

        run, hog, caster = self.session()
        match = run.match
        fight = match.combat
        mei = run.players[2]
        hero = next(hero for hero in phases.heroes().values() if hero.name == "Mei")
        self.assertTrue(match.switch_hero(mei, hero))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = mei.team = 0
        caster.team = 1
        x, y, z = hog.mover.position
        for player, position in ((caster, (x, y, z)), (hog, (x, y, z + 8)), (mei, (x, y, z + 8))):
            player.mover.position[:] = position
            player.mover.body = None
        for _ in range(35):
            run.step({})
        pillar, _ = RoadhogMeiWallTests().place(run, mei)
        pieces = tuple(fight.walls.pieces.values())
        mei.mover.position[:] = (x + 8, y, z)
        mei.mover.body = None
        self.assertTrue(fight.walls.blocked(fight._eye(caster), fight._eye(hog)))
        bubble = None
        if projected:
            zarya_hero = next(hero for hero in phases.heroes().values() if hero.name == "Zarya")
            zarya = match.add_player(8, "Projected Barrier", zarya_hero.guid, hog.team, False)
            in_world(match, zarya)
            self.assertTrue(match.switch_hero(zarya, zarya_hero))
            run.players.append(zarya)
            zarya.mover.position[:] = (x + 3, y, z + 8)
            zarya.mover.body = None
            for _ in range(35):
                run.step({})
            bubble = ana.RoadhogAnaTests().project(run, hog, zarya)
        script = caster.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(run, hog, caster, hack=32)
        for _ in range(45):
            self.step(run, hog, caster)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertTrue(sombra.locked(mei))
        self.assertEqual(tuple(fight.walls.pieces.values()), pieces)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        self.assertFalse(sombra.locked(hog))
        self.assertEqual(self.health(run, hog), 600)
        if bubble is not None:
            self.assertIs(fight.zarya_bubbles.for_target(hog), bubble)
            self.assertEqual(bubble.health, 200)
        px, py, pz = pillar.feet
        look = aim(fight._eye(hog), (px, py + 1.4, pz))
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        caster_health = self.health(run, caster)
        run.step({hog: (*look, 2)})
        self.assertEqual(weapon.vars[53].value(), 4)
        self.assertTrue(any(rocket.shooter is hog for rocket in fight.rockets))
        for _ in range(20):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(pillar.health, 400)
        self.assertEqual(self.health(run, caster), caster_health)

    def test_emp_breaks_projected_bubble_and_preserves_roadhog_receiver_protection(self):
        run, hog, caster = self.session()
        zarya = run.players[2]
        run.match.switch_hero(zarya, phases.heroes()[0x02E0000000000068])
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = zarya.team = 0
        caster.team = 1
        x, y, z = hog.mover.position
        zarya.mover.position[:] = (x-4, y, z)
        zarya.mover.body = None
        for _ in range(10):
            self.step(run, hog, caster)
        bubble = ana.RoadhogAnaTests().project(run, hog, zarya)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(run, hog, caster, hack=32)
        for _ in range(45):
            self.step(run, hog, caster)
        fight = run.match.combat
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(hog))
        self.assertGreater(zarya.body_script.component.entity_var(87).value(), 30)
        self.assertTrue(sombra.locked(zarya))
        self.assertFalse(sombra.locked(hog))
        self.assertEqual(self.health(run, hog), 600)
        self.step(run, hog, caster, fire=16)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)

    def test_hack_range_wall_and_team_filter(self):
        for case in ("range", "wall", "ally"):
            with self.subTest(case=case):
                run, hog, caster = self.session(distance=16 if case == "range" else 4)
                fight = run.match.combat
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    caster.team = hog.team
                x, y, z = hog.mover.position
                wall = World([
                    (x-5, y-1, z+2, x+5, y-1, z+2, x+5, y+5, z+2),
                    (x-5, y-1, z+2, x+5, y+5, z+2, x-5, y+5, z+2),
                ])
                with patch.object(fight, "_collision", return_value=wall if case == "wall" else None):
                    for _ in range(70):
                        self.step(run, hog, caster, hack=2)
                self.assertFalse(sombra.locked(hog))

    def test_damage_during_hack_windup_cancels_this_attempt(self):
        run, hog, caster = self.session()
        self.step(run, hog, caster, hack=2)
        for _ in range(10):
            self.step(run, hog, caster, hack=2)
        instance = sombra.instance_for(caster, 0xB1B)
        self.assertTrue(instance.states[54].active)
        self.step(run, hog, caster, hack=2, fire=2)
        for _ in range(55):
            self.step(run, hog, caster)
        self.assertEqual(self.health(run, caster), 19950)
        self.assertFalse(sombra.locked(hog))
        self.assertFalse(instance.states[54].active)


    def test_hack_cooldown_refuses_repress_then_reuses_receiver(self):
        run, hog, caster = self.session()
        self.land(run, hog, caster, 2)
        ability = sombra.instance_for(caster, 0xB1B).states[53]
        self.assertGreater(ability.remaining(), 7)
        self.step(run, hog, caster)
        self.step(run, hog, caster, hack=2)
        for _ in range(35):
            self.step(run, hog, caster)
        self.assertGreater(ability.remaining(), 6)
        while ability.remaining() > 0:
            self.step(run, hog, caster)
        self.assertFalse(sombra.locked(hog))
        self.land(run, hog, caster, 2)

    def test_losing_visibility_during_windup_aborts_instead_of_retargeting(self):
        run, hog, caster = self.session()
        fight = run.match.combat
        self.step(run, hog, caster, hack=2)
        for _ in range(10):
            self.step(run, hog, caster, hack=2)
        instance = sombra.instance_for(caster, 0xB1B)
        self.assertTrue(instance.states[54].active)
        x, y, z = hog.mover.position
        wall = World([
            (x-5, y-1, z+2, x+5, y-1, z+2, x+5, y+5, z+2),
            (x-5, y-1, z+2, x+5, y+5, z+2, x-5, y+5, z+2),
        ])
        with patch.object(fight, "_collision", return_value=wall):
            for _ in range(45):
                self.step(run, hog, caster, hack=2)
        self.assertFalse(sombra.locked(hog))
        self.assertFalse(instance.states[54].active)


    def test_hack_interrupts_actual_whole_hog_without_refund(self):
        run, hog, caster = self.session()
        fight = run.match.combat
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(ULT_COST).value())
        self.step(run, hog, caster, fire=32, away=True)
        ultimate, = hog.body_script.ultimates
        self.assertTrue(ultimate.flags & 1)
        self.land(run, hog, caster, 2, away=True)
        self.assertFalse(ultimate.flags & 1)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        previous = self.health(run, caster)
        for _ in range(35):
            self.step(run, hog, caster)
        self.assertEqual(self.health(run, caster), previous)
        self.assertFalse(fight.impulses.live)
