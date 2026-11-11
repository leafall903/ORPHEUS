"""Actual1157 manual pin input; declared target/contact/clock, native semantics open."""

import unittest

import test_game_moira_pve as pve
from test_game_combat import aim, in_world

from ow174.game import combat, moira, npc_control
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_COST


@unittest.skipUnless(pve.EXTERNAL and pve.MOVERS, 'requires actual Assassin external inputs')
class MoiraAssassinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pve.MoiraFiveNpcTests.setUpClass()
        cls.addClassCleanup(pve.MoiraFiveNpcTests.doClassCleanups)

    def fight(self):
        case = pve.MoiraFiveNpcTests()
        row = next(r for r in case.rows if r['name'] == 'Assassin')
        run, source, target, _health, _direction = case.fight(row)
        x, y, z = source.mover.position
        target.owner.position = (x, y, z + 2)
        direction = aim(run.match.combat._eye(source), (x, y + target.height / 2, z + 2))
        run.step({source: (*direction, 0)})
        source.body_script.component.set(12918, 0)
        own = next(t for t in run.match.combat._targets() if t.owner is source)
        health = run.match.combat.vitals_of(own)
        return run, source, target.owner, health

    def start(self, run, source, bot):
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        x, y, z = target.feet
        yaw, pitch = aim(run.match.combat._eye(source), (x, y + target.height / 2, z))
        run.step({source: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, source), bot.entity)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        for _ in range(5):
            run.step({})
            if instance.vars.get(536) is not None and instance.vars[536].value() == expr.Entity(source.body):
                break
        self.assertEqual(instance.vars[536].value(), expr.Entity(source.body))
        return instance

    def wait_for_recovery(self, run, source, bot, health, before):
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        recovery = instance.states[23]  # node31: declared duration from paired Wait37
        wait = instance.graph.node(37)
        duration = expr.to_float(instance.evaluate(wait.field('m_timeout')))
        self.assertEqual(duration, 2)
        deadline_ms = recovery.start + round(duration * 1000)
        while (run.frame + 1) * 16 + 15 < deadline_ms:
            run.step({})
            self.assertEqual(health.total, before)
            self.assertNotIn(source.body, run.match.combat.pins.values())
        for _ in range(5):
            run.step({})
            if source.body in run.match.combat.pins.values():
                break
        self.assertIn(source.body, run.match.combat.pins.values())
        self.assertEqual(health.total, before - 25)

    def test_actual_nano_modifies_assassin_entry_damage_without_blocking_pin(self):
        import test_game_moira_nano as nano_tests

        case = nano_tests.MoiraNanoTests()
        for boost_source, boost_victim, expected in ((True, False, 37.5), (False, True, 12.5),
                                                    (True, True, 18.75)):
            with self.subTest(source=boost_source, victim=boost_victim):
                run, source, bot, health = self.fight()
                for recipient, active in ((bot, boost_source), (source, boost_victim)):
                    if active:
                        caster = case.add_ana(run, recipient)
                        case.cast(run, caster, recipient)
                        fight = run.match.combat
                        deadline = fight.vitals_of(case.target(fight, recipient)).nano_until
                        self.assertGreater(deadline, run.match.combat.now)
                        caster.mover.position[0] += 30
                        caster.mover.body = None
                before = health.total
                self.start(run, source, bot)
                self.assertAlmostEqual(before - health.total, expected, places=4)
                self.assertTrue(run.match.combat.is_stunned(source))
                self.assertEqual(run.match.combat.pins, {bot.entity: source.body})

    def test_actual_target_initial_hit_wait_and_continuous_damage_keep_pin_control(self):
        run, source, bot, health = self.fight()
        instance = self.start(run, source, bot)
        self.assertEqual(health.total, 175)
        self.assertTrue(instance.states[18].active)
        self.assertEqual(run.match.combat.pins, {bot.entity: source.body})
        # One-shot damage already completed; the enclosing pin still owns the
        # victim during its authored1-second wait before damage/second begins.
        before = tuple(source.mover.position)
        for _ in range(20):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            source.take_commands([Command(run.frame, forward=127, buttons=8 | 2)])
        self.assertEqual(health.total, 175)
        self.assertEqual(tuple(source.mover.position), before)
        self.assertFalse(moira.fading(source))
        self.assertEqual(run.match.combat.pins, {bot.entity: source.body})
        for _ in range(70):
            run.step({})
        self.assertTrue(instance.states[13].active)
        component = bot.body_script.component
        stored = snapshot_digest(export_component_vm(component))
        instance.states[13].last_applied += 1
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), stored)
        instance.states[13].last_applied -= 1
        self.assertEqual(snapshot_digest(export_component_vm(component)), stored)
        before = health.total
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(before - health.total, 32, places=4)

    def test_first_pin_rejects_range_and_wall_then_hits_after_contact_returns(self):
        for reason in ('range', 'wall'):
            with self.subTest(reason=reason):
                run, source, bot, health = self.fight()
                match = run.match
                self.assertEqual(npc_control.start(match, source), bot.entity)
                original_position = bot.position
                original_collision = match.collision_world
                x, y, z = original_position
                if reason == 'range':
                    bot.position = (x + 10, y, z)
                else:
                    z -= 1
                    match.collision_world = World([
                        (x - 10, y - 5, z, x + 10, y - 5, z, x + 10, y + 5, z),
                        (x - 10, y - 5, z, x + 10, y + 5, z, x - 10, y + 5, z),
                    ])
                for _ in range(20):
                    run.step({})
                    self.assertEqual(health.total, 200)
                    self.assertFalse(match.combat.pins)
                    self.assertFalse(match.combat.is_stunned(source))
                    self.assertFalse(bot.attack_input)

                bot.position = original_position
                match.collision_world = original_collision
                for _ in range(10):
                    run.step({})
                    if match.combat.pins:
                        break
                self.assertEqual(health.total, 175)
                self.assertEqual(match.combat.pins, {bot.entity: source.body})
                self.assertTrue(match.combat.is_stunned(source))
                self.assertIsNone(bot.body_script.component.fault)

    def test_stop_range_wall_and_body_replacement_clear_damage_and_control(self):
        for reason in ('stop', 'range', 'wall', 'body'):
            with self.subTest(reason=reason):
                run, source, bot, health = self.fight()
                self.start(run, source, bot)
                old = source.body
                if reason == 'stop':
                    self.assertEqual(npc_control.stop(run.match), 1)
                elif reason == 'range':
                    x, y, z = bot.position
                    bot.position = (x + 10, y, z)
                elif reason == 'wall':
                    x, y, z = source.mover.position
                    z += 1
                    run.match.collision_world = World([
                        (x - 10, y - 5, z, x + 10, y - 5, z, x + 10, y + 5, z),
                        (x - 10, y - 5, z, x + 10, y + 5, z, x - 10, y + 5, z),
                    ])
                else:
                    self.assertTrue(run.match.switch_hero(source, find_hero('moira')))
                before = health.total
                for _ in range(20):
                    run.step({})
                self.assertNotIn(old, run.match.combat.pins.values())
                self.assertEqual(health.total, before)

    def test_already_active_fade_rejects_damage_and_pin_control(self):
        run, source, bot, health = self.fight()
        yaw, pitch = source.mover.yaw, source.mover.pitch
        run.step({source: (yaw, pitch, 8)})
        self.assertTrue(moira.fading(source))
        self.start(run, source, bot)
        for _ in range(25):
            run.step({})
            self.assertTrue(moira.fading(source))
            self.assertEqual(health.total, 200)
            self.assertNotIn(source.body, run.match.combat.pins.values())

    def test_actual_assassin_pin_respects_enemy_protection_windows(self):
        for hero, press in ((0x02E0000000000002, 8), (0x02E00000000000DD, 8),
                            (0x02E0000000000020, 32)):
            for protected in (False, True):
                with self.subTest(hero=hero, protected=protected):
                    run, source, bot, _ = self.fight()
                    match = run.match
                    fight = match.combat
                    position = list(source.mover.position)
                    self.assertTrue(match.switch_hero(source, heroes()[hero]))
                    source.mover.position = position
                    source.mover.body = None
                    for _ in range(30):
                        run.step({})
                    if protected:
                        if press == 32:
                            script = source.body_script
                            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                        run.step({source: (0, 0, press)})
                    self.assertEqual(fight.damage_immune(source), protected)
                    target = next(t for t in fight._targets() if t.owner is source)
                    health = fight.vitals_of(target)
                    before = health.total
                    self.start(run, source, bot)
                    for _ in range(10):
                        run.step({})
                    if protected:
                        self.assertEqual(health.total, before)
                        self.assertNotIn(source.body, fight.pins.values())
                    else:
                        self.assertEqual(before - health.total, 25)
                        self.assertIn(source.body, fight.pins.values())

    def test_actual_pin_kills_moira_with_npc_credit_and_releases_control(self):
        run, source, bot, health = self.fight()
        self.start(run, source, bot)
        for _ in range(220):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertNotIn(source.body, run.match.combat.pins.values())
        self.assertEqual(source.client.messages(combat.DEATH_NOTICE)[-1]['+0x7C']['+0x0'], bot.entity)

    def test_pin_interrupts_actual_coalescence_without_refund_or_resume(self):
        run, source, bot, health = self.fight()
        ultimate = pve.MoiraFiveNpcTests.start_ult(self, run, source)
        self.assertTrue(ultimate.states[20].active)
        point = (bot.position[0], bot.position[1] + 0.75, bot.position[2])
        yaw, pitch = aim(run.match.combat._eye(source), point)
        run.step({source: (yaw, pitch, 0)})
        self.start(run, source, bot)
        self.assertFalse(ultimate.states[20].active)
        self.assertEqual(source.body_script.component.entity_var(342).value(), 0)
        before = health.total
        for _ in range(20):
            run.step({source: (yaw, pitch, 32)})
        self.assertEqual(health.total, before)
        npc_control.stop(run.match)
        for _ in range(30):
            run.step({})
        self.assertFalse(ultimate.states[20].active)
        self.assertEqual(source.body_script.component.entity_var(342).value(), 0)

    def test_actual_flashbang_on_assassin_releases_pinned_moira(self):
        run, source, bot, health = self.fight()
        hero = heroes()[0x02E0000000000042]
        caster = run.match.add_player(2, 'Interrupt', hero.guid, 1, False)
        in_world(run.match, caster)
        caster.client = pve.NpcEventClient(caster)
        run.players.append(caster)
        self.assertTrue(run.match.switch_hero(caster, hero))
        caster.team = source.team
        x, y, z = bot.position
        caster.mover.position = [x + 2, y, z]
        caster.mover.body = None
        for _ in range(20):
            run.step({})
        self.start(run, source, bot)
        self.assertIn(source.body, run.match.combat.pins.values())
        yaw, pitch = aim(run.match.combat._eye(caster), (x, y + 0.75, z))
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(50):
            run.step({})
            if run.match.combat.is_stunned(bot):
                break
        self.assertIn(bot.entity, run.match.combat.stuns)
        self.assertNotIn(source.body, run.match.combat.pins.values())
        before = health.total
        for _ in range(10):
            run.step({})
        self.assertEqual(health.total, before)
        self.assertFalse(run.match.combat.is_stunned(source))
        deadline = run.match.combat.stuns[bot.entity]
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(health.total, before)
            self.assertNotIn(source.body, run.match.combat.pins.values())
        self.wait_for_recovery(run, source, bot, health, before)
        self.assertNotIn(bot.entity, run.match.combat.stuns)

    def test_actual_sleep_on_assassin_releases_pin_and_reacquires_after_expiry(self):
        import test_game_moira_nano as nano_tests
        import test_game_moira_sleep as sleep_tests

        case = nano_tests.MoiraNanoTests()
        run, source, bot, health = self.fight()
        caster = case.add_ana(run, source)
        caster.client = pve.NpcEventClient(caster)
        self.start(run, source, bot)
        self.assertIn(source.body, run.match.combat.pins.values())
        deadline = sleep_tests.MoiraSleepTests.cast_at(run, caster, bot)
        fight = run.match.combat
        self.assertNotIn(source.body, fight.pins.values())
        self.assertFalse(fight.is_stunned(source))
        self.assertIsNone(bot.body_script.component.world.manual_target)
        before = health.total
        while fight.now + combat.TICK < deadline:
            run.step({})
            self.assertEqual(health.total, before)
            self.assertNotIn(source.body, fight.pins.values())
        for _ in range(10):
            run.step({})
            if source.body in fight.pins.values():
                break
        self.assertFalse(fight.is_asleep(bot))
        self.assertIn(source.body, fight.pins.values())
        self.assertEqual(health.total, before - 25)

    def test_stopped_pin_stays_off_then_reselection_starts_fresh_without_catchup(self):
        run, source, bot, health = self.fight()
        self.start(run, source, bot)
        self.assertEqual(health.total, 175)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(300):
            run.step({})
        self.assertEqual(health.total, 175)
        self.assertFalse(bot.firing)
        self.assertNotIn(source.body, run.match.combat.pins.values())
        instance = self.start(run, source, bot)
        self.assertEqual(health.total, 150)
        self.assertTrue(instance.states[18].active)
        for _ in range(40):
            run.step({})
            self.assertEqual(health.total, 150)
        for _ in range(30):
            run.step({})
        self.assertTrue(instance.states[13].active)
        self.assertLess(health.total, 150)

    def test_allegiance_loss_cancels_pin_and_enemy_return_reacquires_selected_body(self):
        run, source, bot, health = self.fight()
        self.start(run, source, bot)
        enemy_team = bot.team
        bot.team = source.team
        for _ in range(80):
            run.step({})
        self.assertEqual(health.total, 175)
        self.assertNotIn(source.body, run.match.combat.pins.values())
        self.assertIsNone(bot.body_script.component.world.manual_target)
        bot.team = enemy_team
        self.wait_for_recovery(run, source, bot, health, 175)
        self.assertEqual(bot.body_script.component.world.manual_target, source.body)

    def test_real_second_moira_grasp_kills_wounded_assassin_and_releases_victim(self):
        run, source, bot, health = self.fight()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        wounded = fight.vitals_of(target)
        wounded.damage(wounded.total - 0.5)  # explicit wounded starting condition
        fight._sync_body_health(bot, wounded)
        healer_hero = find_hero('moira')
        rescuer = run.match.add_player(2, 'Rescuer', healer_hero.guid, 1, False)
        in_world(run.match, rescuer)
        rescuer.client = pve.NpcEventClient(rescuer)
        run.players.append(rescuer)
        self.assertTrue(run.match.switch_hero(rescuer, healer_hero))
        rescuer.team = source.team
        x, y, z = bot.position
        rescuer.mover.position = [x + 2, y, z]
        rescuer.mover.body = None
        for _ in range(20):
            run.step({})
        self.start(run, source, bot)
        self.assertIn(source.body, fight.pins.values())
        yaw, pitch = aim(fight._eye(rescuer), (x, y + target.height / 2, z))
        for _ in range(20):
            run.step({rescuer: (yaw, pitch, 2)})
            if wounded.dead:
                break
        self.assertTrue(wounded.dead)
        self.assertEqual(wounded.killer, rescuer.body)
        self.assertTrue(bot.dead)
        self.assertNotIn(source.body, fight.pins.values())
        before = health.total
        for _ in range(20):
            run.step({})
        self.assertEqual(health.total, before)


    def test_actual_ice_wall_interrupts_assassin_pin_before_next_damage(self):
        run, source, bot, health = self.fight()
        self.start(run, source, bot)
        fight = run.match.combat
        self.assertEqual(fight.pins, {bot.entity: source.body})
        self.assertEqual(health.total, 175)
        x, y, z = source.mover.position
        mei = find_hero("mei")
        caster = run.match.add_player(2, "Mei", mei.guid, source.team, False)
        in_world(run.match, caster)
        caster.client = pve.NpcEventClient(caster)
        run.players.append(caster)
        self.assertTrue(run.match.switch_hero(caster, mei))
        caster.team = source.team
        caster.mover.position = [x, y, z - 6]
        caster.mover.body = None
        for _ in range(8):
            run.step({})
        direction = aim(fight._eye(caster), (x, y, (z + bot.position[2]) / 2))
        for button in (16, 0, 1, 0):
            run.step({caster: (*direction, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        for _ in range(75):
            run.step({})
        self.assertNotIn(source.body, fight.pins.values())
        self.assertFalse(fight.is_stunned(source))
        self.assertEqual(health.total, 175)
