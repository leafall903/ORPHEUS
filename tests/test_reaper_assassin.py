"""Actual1157 manual pin versus Reaper; native controller/acquisition remains open."""

import unittest
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_reaper_control as control_tests
import test_reaper_npc as npc_tests
from test_game_combat import aim

from ow174.game import ana, assassin, combat, defenses, npc, npc_control, reaper
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, find_hero, heroes


@unittest.skipUnless(npc_tests.PACK, "set ORPHEUS_NPC_RUNTIME_DIR for actual Assassin")
class ReaperAssassinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_tests.ReaperNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        npc_tests.ReaperNpcTests.tearDownClass()

    def scene(self):
        fixture = npc_tests.ReaperNpcTests()
        return fixture, fixture.scene(fixture.catalog["4"])

    def pin(self, fixture, run, owner, bot):
        fixture.engage(run, owner, bot)
        for _ in range(100):
            run.step({})
            if bot.entity in run.match.combat.pins:
                break
        self.assertEqual(run.match.combat.pins.get(bot.entity), owner.body)

    def continuous(self, run, bot):
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        node = instance.graph.node(15)
        for _ in range(100):
            state = instance.states.get(node.state)
            if state is not None and state.active:
                return
            run.step({})
        self.fail("Authored delayed continuous health scope did not start")

    def test_actual_assassin_approaches_from_console_distance_and_pins_reaper(self):
        fixture, (run, owner, _, bot) = self.scene()
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 5)
        start = bot.position
        fixture.engage(run, owner, bot)
        fight = run.match.combat
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        for _ in range(140):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertLess(bot.position[2], start[2] - 2)
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(fight.vitals[owner.body].total, 225)
        npc_control.stop(run.match)
        stopped = bot.position
        for _ in range(20):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(fight.vitals[owner.body].total, 225)

    def test_actual_contact_clock_blocks_input_then_stop_restores_fire_and_reaping(self):
        fixture, (run, owner, victim, bot) = self.scene()
        fight = run.match.combat
        self.pin(fixture, run, owner, bot)
        self.assertEqual(fight.vitals[owner.body].total, 225)
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals[owner.body].total, 225)  # authored1s contact phase
        self.continuous(run, bot)
        rate_start = fight.vitals[owner.body].total
        start = tuple(owner.mover.position)
        for _ in range(20):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            owner.take_commands([Command(run.frame, yaw=0, pitch=0, forward=127,
                                          buttons=1 | 8 | 16 | 32 | 0x800)])
        self.assertAlmostEqual(rate_start - fight.vitals[owner.body].total, 32, delta=0.00001)
        self.assertEqual(tuple(owner.mover.position), start)
        self.assertEqual(fight.vitals[bot.entity].total, 500)
        self.assertFalse(reaper.intangible(owner))
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        npc_control.stop(run.match)
        before = fight.vitals[owner.body].total
        for _ in range(30):
            run.step({})
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(fight.vitals[owner.body].total, before)
        reaper_tests.ReaperTests().fire(run, owner, victim)
        self.assertEqual(fight.vitals[bot.entity].total, 380)
        self.assertAlmostEqual(fight.vitals[owner.body].total, before + 42, delta=0.00001)

    def test_actual_pin_crosses_continuous_damage_start_across_skipped_frames(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                fixture, (run, owner, _, bot) = self.scene()
                fight = run.match.combat
                own = fight.vitals[owner.body]
                self.pin(fixture, run, owner, bot)
                for _ in range(30):
                    run.step({})
                self.assertEqual(own.total, 225)
                if skipped:
                    run.frame += 55
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    for _ in range(55):
                        run.step({})
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                self.assertLess(own.total, 225)
                outcomes.append(own.total)
                before = own.total
                run.step({})
                self.assertAlmostEqual(own.total, before - 100 * combat.TICK, places=4)
        self.assertAlmostEqual(*outcomes, places=4)

    def test_actual_assassin_long_gap_keeps_pin_damage_and_death_timing(self):
        for health in (250, 1000):
            with self.subTest(health=health):
                continuous = self.pin_gap_trace(health, skipped=False)
                self.assertEqual(continuous, self.pin_gap_trace(health, skipped=True))

    def pin_gap_trace(self, health, *, skipped):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body] = combat.Vitals(owner.body, health)
        self.pin(fixture, run, owner, bot)
        self.assertEqual(own.total, health - 25)
        start = run.frame
        last_input = owner.commands.last_frame
        damage = fight._damage
        hits = []

        def record_hit(shooter, *args, **kwargs):
            before = own.total
            result = damage(shooter, *args, **kwargs)
            if shooter is bot and own.total < before:
                hits.append((fight.tick - start, before - own.total))
            return result

        with patch.object(fight, "_damage", side_effect=record_hit):
            for count in (270,) if skipped else (1,) * 270:
                run.frame += count
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(owner.commands.last_frame, last_input)
        self.assertTrue(hits)
        pinned = fight.pins.get(bot.entity) == owner.body
        dead = health == 250
        self.assertEqual(own.dead, dead)
        self.assertEqual(pinned, not dead)
        notices = owner.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(len(notices), int(dead))
        if dead:
            self.assertEqual(own.killer, bot.entity)
            self.assertEqual(own.death_tick, start + hits[-1][0])
        return hits, own.total, own.death_tick

    def test_actual_autonomous_assassin_resumes_after_wraith_in_long_gap(self):
        self.assertEqual(self.wraith_gap_trace(skipped=False), self.wraith_gap_trace(skipped=True))

    def wraith_gap_trace(self, *, skipped):
        _, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 5)
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        bot.autonomous = True
        weapons = fight._bot_weapons
        acquisitions = []

        def record_weapon(tick):
            old = fight.pins.get(bot.entity)
            weapons(tick)
            if old is None and fight.pins.get(bot.entity) == owner.body:
                self.assertFalse(reaper.intangible(owner))
                acquisitions.append(tick)

        with patch.object(fight, "_bot_weapons", side_effect=record_weapon):
            for count in (300,) if skipped else (1,) * 300:
                run.frame += count
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertGreater(z + 5 - bot.position[2], 2)
        self.assertEqual(len(acquisitions), 1)
        self.assertLess(fight.vitals[owner.body].total, 225)
        return acquisitions, tuple(bot.position), fight.vitals[owner.body].total

    def autonomous_projected_pin_trace(self, *, skipped):
        fixture = npc_tests.ReaperNpcTests()
        run, owner, zarya, bot = fixture.scene(
            fixture.catalog["4"], team_mode=True, names=("Reaper", "Zarya"),
        )
        owner.team = zarya.team = 0
        zarya.spawned = True
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        x, y, z = owner.mover.position
        zarya.mover.position[:] = (x + 7, y, z)
        zarya.mover.body = None
        bot.position = (x, y, z + 5)
        for _ in range(12):
            run.step({})
        fight = run.match.combat
        guard = aim(fight._eye(zarya), fight._eye(owner))
        run.step({zarya: (*guard, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = 28
        self.assertIsNone(bot.attack_target)
        bot.autonomous = True
        own = fight.vitals[owner.body]
        start = run.frame
        frame = owner.commands.last_frame
        contacts, acquisitions = [], []
        damage, weapons = fight._damage, fight._bot_weapons

        def observe_damage(source, target, amount, *args, **kwargs):
            shield_before, health_before = bubble.health, own.total
            result = damage(source, target, amount, *args, **kwargs)
            if source is bot and target.owner is owner:
                contacts.append((fight.tick, amount, shield_before, bubble.health, health_before, own.total))
            return result

        def observe_pin(tick):
            was_pinned = fight.pins.get(bot.entity)
            weapons(tick)
            pinned = fight.pins.get(bot.entity)
            if pinned is not None and pinned != was_pinned:
                acquisitions.append((tick, pinned))

        end = start + 155
        with patch.object(fight, "_damage", side_effect=observe_damage), patch.object(
            fight, "_bot_weapons", side_effect=observe_pin,
        ):
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        first, first_drain, *_ = contacts
        self.assertEqual(first[1:], (25, 28, 3, 250, 250))
        self.assertEqual(acquisitions, [(first[0], owner.body)])
        self.assertGreater(first_drain[0], first[0] + 20)
        self.assertEqual(first_drain[2], 3)
        self.assertGreater(first_drain[1], 0)
        self.assertLessEqual(first_drain[1], 100 * combat.TICK + 1e-6)
        self.assertAlmostEqual(first_drain[3], 3 - first_drain[1])
        break_index = next(i for i, event in enumerate(contacts) if event[2] > 0 and event[3] == 0)
        breaking, body_hit = contacts[break_index:break_index + 2]
        self.assertAlmostEqual(breaking[1], 100 * combat.TICK)
        self.assertEqual(breaking[3:], (0, 250, 250))
        self.assertEqual(body_hit[0], breaking[0] + 1)
        self.assertEqual(body_hit[2:5], (0, 0, 250))
        self.assertAlmostEqual(body_hit[5], 250 - 100 * combat.TICK)
        self.assertLess(bot.position[2], z + 3)
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(bot.attack_target, owner.entity)
        self.assertEqual(owner.commands.last_frame, frame)
        self.assertIsNone(fight.zarya_bubbles.for_target(owner))

        self.assertEqual(npc_control.stop(run.match), 1)
        health = own.total
        self.assertNotIn(bot.entity, fight.pins)
        run.step({})
        self.assertEqual(own.total, health)
        enemy = fight.vitals[bot.entity]
        before = enemy.total
        bx, by, bz = bot.position
        yaw, pitch = aim(fight._eye(owner), (bx, by + 1, bz))
        run.step({owner: (yaw, pitch, 1)})
        self.assertLess(enemy.total, before)
        self.assertAlmostEqual(own.total, min(250, health + (before - enemy.total) * reaper.REAPING_RATIO))
        return contacts, acquisitions, health, enemy.total, own.total

    def test_actual_autonomous_assassin_breaks_projection_before_body_damage(self):
        stepped = self.autonomous_projected_pin_trace(skipped=False)
        skipped = self.autonomous_projected_pin_trace(skipped=True)
        self.assertEqual(stepped, skipped)

    def test_actual_assassin_fresh_pin_after_reaper_body_replacement(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        self.pin(fixture, run, owner, bot)
        self.continuous(run, bot)
        run.step({})
        old = owner.body
        old_pool = fight.vitals[old]
        old_health = old_pool.total
        self.assertLess(old_health, 225)
        self.assertTrue(run.match.switch_hero(owner, heroes()[reaper.HERO], preserve_pose=True))
        self.assertNotEqual(owner.body, old)
        self.assertNotIn(old, fight.pins.values())
        run.frame += 40
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(old_pool.total, old_health)
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(bot.body_script.component.world.manual_target, owner.body)
        self.assertEqual(fight.vitals[owner.body].total, 225)
        self.continuous(run, bot)
        before = fight.vitals[owner.body].total
        run.step({})
        self.assertAlmostEqual(fight.vitals[owner.body].total, before - 100 * combat.TICK, places=4)

    def test_actual_autonomous_assassin_retargets_after_lethal_pin_in_long_gap(self):
        self.assertEqual(self.retarget_gap_trace(skipped=False), self.retarget_gap_trace(skipped=True))

    def retarget_gap_trace(self, *, skipped):
        _, (run, owner, second, bot) = self.scene()
        fight = run.match.combat
        first = fight.vitals[owner.body]
        first.damage(220)
        second.spawned = True
        self.assertTrue(run.match.switch_hero(second, heroes()[reaper.HERO]))
        x, y, z = bot.position
        second.mover.position[:] = (x + 2.5, y, z)
        second.mover.body = None
        bot.autonomous = True
        weapons = fight._bot_weapons
        pins = []

        def record_pin(tick):
            before = fight.pins.get(bot.entity)
            weapons(tick)
            after = fight.pins.get(bot.entity)
            if after is not None and after != before:
                pins.append((tick, after))

        with patch.object(fight, "_bot_weapons", side_effect=record_pin):
            for count in (180,) if skipped else (1,) * 180:
                run.frame += count
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(first.dead)
        self.assertEqual(first.killer, bot.entity)
        self.assertEqual(len(owner.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertEqual([body for _, body in pins], [owner.body, second.body])
        self.assertEqual(fight.pins.get(bot.entity), second.body)
        self.assertLess(fight.vitals[second.body].total, 250)
        return pins, first.death_tick, fight.vitals[second.body].total

    def test_actual_pin_sustains_damage_against_surviving_reaper_until_stop(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body] = combat.Vitals(owner.body, 1000)
        self.pin(fixture, run, owner, bot)
        self.assertEqual(own.total, 975)
        self.continuous(run, bot)
        continuous_start = own.total
        for _ in range(400):
            run.step({})
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertFalse(fight.is_dead(owner))
        self.assertEqual(bot.attack_target, owner.entity)
        self.assertAlmostEqual(own.total, continuous_start - 400 * 100 * combat.TICK, places=4)
        after_pin = own.total
        npc_control.stop(run.match)
        for _ in range(30):
            run.step({})
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(own.total, after_pin)
        enemy = fight.vitals[bot.entity]
        before = enemy.total
        x, y, z = bot.position
        heading = aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (*heading, 1)})
        self.assertLess(enemy.total, before)
        self.assertAlmostEqual(own.total, after_pin + (before - enemy.total) * 0.35, places=4)

    def test_actual_pin_kills_reaper_with_correct_attribution_and_releases_control(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        self.pin(fixture, run, owner, bot)
        for _ in range(300):
            run.step({})
            if fight.is_dead(owner):
                break
        self.assertTrue(fight.is_dead(owner))
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(fight.vitals[owner.body].killer, bot.entity)
        self.assertEqual(owner.client.messages(combat.DEATH_NOTICE)[-1],
                         {"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": bot.entity}})

    def test_actual_pin_kills_once_across_skipped_frames(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        self.pin(fixture, run, owner, bot)
        for _ in range(250):
            if own.total <= 30:
                break
            run.step({})
        self.assertGreater(own.total, 0)
        self.assertLessEqual(own.total, 30)
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        run.frame += 40
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(own.killer, bot.entity)
        self.assertNotIn(bot.entity, fight.pins)
        for _ in range(20):
            run.step({})
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(owner.client.messages(combat.DEATH_NOTICE),
                         [{"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": bot.entity}}])

    def test_existing_wraith_rejects_actual_manual_pin(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        fixture.engage(run, owner, bot)
        for _ in range(220):
            if not reaper.intangible(owner):
                break
            self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(own.total, 250)
            run.step({})
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual(bot.attack_target, owner.entity)
        for _ in range(100):
            if bot.entity in fight.pins:
                break
            run.step({})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertAlmostEqual(own.total, 250 - 100 * combat.TICK, places=4)
        run.step({})
        self.assertAlmostEqual(own.total, 250 - 200 * combat.TICK, places=4)

    def test_actual_assassin_reacquires_after_primary_cancels_wraith(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        enemy = fight.vitals[bot.entity]
        before = enemy.total
        run.step({owner: (0, 0, 8)})
        fixture.engage(run, owner, bot)
        for _ in range(30):
            run.step({})
        self.assertTrue(reaper.intangible(owner))
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(own.total, 250)
        x, y, z = bot.position
        heading = aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (*heading, 1)})
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual(enemy.total, before)
        for _ in range(100):
            if bot.entity in fight.pins:
                break
            run.step({})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        first_tick = own.total
        self.assertLess(first_tick, 250)
        self.assertGreaterEqual(first_tick, 250 - 100 * combat.TICK - 0.0001)
        run.step({})
        self.assertAlmostEqual(own.total, first_tick - 100 * combat.TICK, places=4)
        self.assertEqual(enemy.total, before)

    def test_actual_pin_refuses_wraith_then_fresh_wraith_rejects_next_pin(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        self.pin(fixture, run, owner, bot)
        own = fight.vitals[owner.body]
        for _ in range(30):
            run.step({owner: (0, 0, 8)})
            self.assertFalse(reaper.intangible(owner))
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(own.total, 225)
        run.step({})
        npc_control.stop(run.match)
        for _ in range(30):
            run.step({})
            self.assertFalse(reaper.intangible(owner))
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(own.total, 225)

        run.step({owner: (0, 0, 8)})
        self.assertTrue(reaper.intangible(owner))
        fixture.engage(run, owner, bot)
        for _ in range(80):
            run.step({})
            self.assertTrue(reaper.intangible(owner))
            self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(own.total, 225)

    def test_actual_pin_allows_started_reload_to_finish_but_blocks_fire(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x19)
        x, y, z = bot.position
        heading = aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (*heading, 1)})
        enemy = fight.vitals[bot.entity]
        self.assertLess(enemy.total, 500)
        after_first = enemy.total
        self.assertEqual(weapon.vars[53].value(), 7)
        run.step({owner: (*heading, 0x400)})
        for _ in range(15):
            run.step({})
        self.pin(fixture, run, owner, bot)
        self.assertEqual(weapon.vars[53].value(), 7)
        for _ in range(100):
            run.step({owner: (*heading, 1)})
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(weapon.vars[53].value(), 8)
        self.assertEqual(enemy.total, after_first)
        npc_control.stop(run.match)
        for _ in range(40):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 8)
        self.assertEqual(enemy.total, after_first)
        run.step({owner: (*heading, 1)})
        self.assertEqual(weapon.vars[53].value(), 7)
        self.assertLess(enemy.total, after_first)

    def test_actual_pin_rejects_new_reload_without_queuing_it(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x19)
        x, y, z = bot.position
        heading = aim(fight._eye(owner), (x, y + 1, z))
        run.step({owner: (*heading, 1)})
        self.assertEqual(weapon.vars[53].value(), 7)
        self.pin(fixture, run, owner, bot)
        for _ in range(100):
            run.step({owner: (*heading, 0x400)})
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(weapon.vars[53].value(), 7)
        run.step({})
        npc_control.stop(run.match)
        for _ in range(120):
            run.step({})
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(weapon.vars[53].value(), 7)
        run.step({owner: (*heading, 0x400)})
        for _ in range(120):
            run.step({})
            if weapon.vars[53].value() == 8:
                break
        self.assertEqual(weapon.vars[53].value(), 8)

    def test_actual_pin_rejects_quick_melee_without_queuing_it(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        self.pin(fixture, run, owner, bot)
        enemy = fight.vitals[bot.entity]
        before = enemy.total
        x, y, z = bot.position
        heading = aim(fight._eye(owner), (x, y + 1, z))
        for _ in range(40):
            run.step({owner: (*heading, 0x800)})
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(enemy.total, before)
        run.step({})
        npc_control.stop(run.match)
        own = fight.vitals[owner.body]
        before_melee = own.total
        for _ in range(40):
            run.step({})
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(enemy.total, before)
        self.assertEqual(own.total, before_melee)
        run.step({owner: (*heading, 0x800)})
        for _ in range(40):
            run.step({})
        self.assertEqual(enemy.total, before - 30)
        self.assertAlmostEqual(own.total, before_melee + 10.5, places=4)

    def test_actual_pin_refuses_blossom_without_spending_or_queuing_it(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        owner.body_script.set_ult_charge(2100)
        charge = owner.body_script.component.vars[342]
        self.pin(fixture, run, owner, bot)
        enemy = fight.vitals[bot.entity]
        own = fight.vitals[owner.body]
        before = enemy.total
        for _ in range(30):
            run.step({owner: (0, 0, 32)})
        self.assertTrue(fight.is_stunned(owner))
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(charge.value(), 2100)
        self.assertEqual(enemy.total, before)

        run.step({})
        npc_control.stop(run.match)
        for _ in range(30):
            run.step({})
        self.assertFalse(fight.is_stunned(owner))
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(charge.value(), 2100)
        self.assertEqual(enemy.total, before)
        health = own.total
        run.step({owner: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(charge.value(), 0)
        self.assertEqual(enemy.total, before - 34)
        self.assertAlmostEqual(own.total, health + 34 * 0.35, places=4)

    def test_actual_pin_interrupts_blossom_without_refund_or_late_damage(self):
        fixture, (run, owner, _, bot) = self.scene()
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.pin(fixture, run, owner, bot)
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)
        npc_control.stop(run.match)
        health = run.match.combat.vitals[bot.entity].total
        for _ in range(220):
            run.step({})
        self.assertEqual(run.match.combat.vitals[bot.entity].total, health)

    def test_actual_pin_refuses_shadow_selection_without_starting_cooldown(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        control = control_tests.ReaperControlTests()
        shadow = control.shadow(owner)
        self.pin(fixture, run, owner, bot)
        origin = tuple(owner.mover.position)
        for _ in range(30):
            run.step({owner: (16384, 5000, 16)})
        self.assertTrue(fight.is_stunned(owner))
        self.assertFalse(shadow.state(0).active)
        self.assertNotIn(owner.body, fight.reaper_shadows)
        self.assertEqual(shadow.vars[61].value(), 0)
        run.step({})
        npc_control.stop(run.match)
        for _ in range(30):
            run.step({})
        self.assertFalse(shadow.state(0).active)
        self.assertNotIn(owner.body, fight.reaper_shadows)
        self.assertEqual(tuple(owner.mover.position), origin)
        self.assertEqual(shadow.vars[61].value(), 0)
        record = control.start_shadow(run, owner)
        for _ in range(140):
            run.step({})
        self.assertTrue(record["moved"])
        self.assertNotEqual(tuple(owner.mover.position), origin)
        self.assertGreater(shadow.vars[61].value(), 0)

    def test_actual_pin_cancels_open_shadow_selection_without_cooldown(self):
        fixture, (run, owner, _, bot) = self.scene()
        control = control_tests.ReaperControlTests()
        record = control.start_shadow(run, owner, confirm=False)
        shadow = control.shadow(owner)
        origin = tuple(owner.mover.position)
        self.pin(fixture, run, owner, bot)
        self.assertFalse(shadow.state(0).active)
        self.assertFalse(shadow.state(32).active)
        self.assertEqual(shadow.vars[61].value(), 0)
        run.step({owner: (16384, 5000, 1)})
        run.step({})
        npc_control.stop(run.match)
        run.step({owner: (16384, 5000, 1)})
        for _ in range(140):
            run.step({})
        self.assertFalse(record["moved"])
        self.assertIsNone(record["cast"])
        self.assertEqual(tuple(owner.mover.position), origin)
        self.assertEqual(shadow.vars[61].value(), 0)
        fresh = control.start_shadow(run, owner)
        self.assertIsNot(fresh, record)
        for _ in range(140):
            run.step({})
        self.assertTrue(fresh["moved"])
        self.assertGreater(shadow.vars[61].value(), 0)

    def test_actual_shadow_immunity_rejects_pin_until_after_teleport(self):
        fixture, (run, owner, _, bot) = self.scene()
        fight = run.match.combat
        record = control_tests.ReaperControlTests().start_shadow(run, owner)
        own = fight.vitals[owner.body]
        for _ in range(75):
            run.step({})
            if reaper.intangible(owner):
                break
        self.assertTrue(reaper.intangible(owner))
        fixture.engage(run, owner, bot)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        contact_state = instance.graph.node(25).state
        for _ in range(35):
            if not reaper.intangible(owner):
                break
            run.step({})
            self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(own.total, 250)
        self.assertGreater(instance.state(contact_state).counter, 0)
        self.assertTrue(record["moved"])
        self.assertFalse(reaper.intangible(owner))
        npc_control.stop(run.match)
        for _ in range(40):
            run.step({})
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 1.5)
        self.pin(fixture, run, owner, bot)
        self.assertEqual(own.total, 225)

    def test_actual_pin_before_shadow_immunity_aborts_locked_cast_with_cooldown(self):
        fixture, (run, owner, _, bot) = self.scene()
        record = control_tests.ReaperControlTests().start_shadow(run, owner)
        start = tuple(owner.mover.position)
        self.pin(fixture, run, owner, bot)
        shadow = control_tests.ReaperControlTests().shadow(owner)
        self.assertFalse(shadow.state(19).active)
        self.assertFalse(shadow.state(32).active)
        self.assertGreater(shadow.vars[61].value(), 9)
        npc_control.stop(run.match)
        for _ in range(160):
            run.step({})
        self.assertEqual(tuple(owner.mover.position), start)
        self.assertNotEqual(tuple(owner.mover.position), record["target"])

    def test_explicit_npc_removal_and_recipient_body_switch_clear_old_pin(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                fixture, (run, owner, _, bot) = self.scene()
                self.pin(fixture, run, owner, bot)
                fight, old = run.match.combat, owner.body
                if remove:
                    npc.remove_spawned(run.match)
                else:
                    run.match.switch_hero(owner, heroes()[SOLDIER])
                self.assertNotIn(old, fight.pins.values())
                if remove:
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertFalse(fight.is_stunned(owner))

    def test_zen_mei_protection_rejects_damage_and_pin_without_interruption(self):
        for hero, button in [(defenses.ZENYATTA, 32), (defenses.MEI, 8)]:
            with self.subTest(hero=hero):
                fixture, (run, owner, _, bot) = self.scene()
                origin = tuple(owner.mover.position)
                run.match.switch_hero(owner, heroes()[hero])
                owner.mover.position[:] = origin
                owner.mover.body = None
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (0, 0, button)})
                self.assertTrue(defenses.invulnerable(owner))
                fixture.engage(run, owner, bot)
                before = run.match.combat.vitals_of(next(t for t in run.match.combat._targets()
                                                         if t.owner is owner)).total
                for _ in range(50):
                    run.step({})
                    self.assertNotIn(bot.entity, run.match.combat.pins)
                self.assertTrue(defenses.invulnerable(owner))
                self.assertEqual(run.match.combat.vitals[owner.body].total, before)
                self.assertEqual(assassin.PIN_RANGE, 3)

    def test_actual_support_reaper_shell_kills_pinner_and_releases_victim(self):
        fixture, (run, owner, victim, bot) = self.scene()
        for _ in range(4):
            reaper_tests.ReaperTests().fire(run, owner, victim)
            for _ in range(40):
                run.step({})
        self.assertEqual(run.match.combat.vitals[bot.entity].total, 20)
        victim.spawned = True
        self.assertTrue(run.match.switch_hero(victim, heroes()[reaper.HERO]))
        x, y, z = bot.position
        victim.mover.position[:] = [x + 3, y, z]
        victim.mover.body = None
        for _ in range(20):
            run.step({})
        self.pin(fixture, run, owner, bot)
        # The support's muzzle faces the actual NPC capsule, independently of the controlled owner.
        yaw, pitch = aim(run.match.combat._eye(victim), (x, y + 1, z))
        run.step({victim: (yaw, pitch, 0)})
        run.step({victim: (yaw, pitch, 1)})
        self.assertTrue(bot.dead)
        self.assertNotIn(bot.entity, run.match.combat.pins)
        before = run.match.combat.vitals[owner.body].total
        for _ in range(30):
            run.step({})
        self.assertEqual(run.match.combat.vitals[owner.body].total, before)
        self.assertFalse(run.match.combat.is_stunned(owner))

    def test_actual_sleep_dart_on_pinner_clears_control_and_stops_damage(self):
        fixture, (run, owner, caster, bot) = self.scene()
        caster.spawned = True
        self.assertTrue(run.match.switch_hero(caster, heroes()[ana.HERO]))
        x, y, z = bot.position
        caster.mover.position[:] = [x + 3, y, z]
        caster.mover.body = None
        for _ in range(20):
            run.step({})
        self.pin(fixture, run, owner, bot)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (yaw, pitch, 8)})
        for _ in range(40):
            run.step({caster: (yaw, pitch, 0)})
            if fight.is_stunned(bot):
                break
        self.assertTrue(fight.is_stunned(bot))
        self.assertNotIn(bot.entity, fight.pins)
        before = fight.vitals[owner.body].total
        for _ in range(40):
            run.step({})
        self.assertEqual(fight.vitals[owner.body].total, before)
        self.assertFalse(fight.is_stunned(owner))

    def test_contact_range_and_static_wall_break_actual_pin_without_late_damage(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                fixture, (run, owner, _, bot) = self.scene()
                self.pin(fixture, run, owner, bot)
                before = run.match.combat.vitals[owner.body].total
                x, y, z = owner.mover.position
                if wall:
                    mid = (z + bot.position[2]) / 2
                    triangles = [(-500,y,-500,-500,y,500,500,y,500),
                                 (-500,y,-500,500,y,500,500,y,-500),
                                 (x-500,y,mid,x+500,y,mid,x+500,y+5,mid),
                                 (x-500,y,mid,x+500,y+5,mid,x-500,y+5,mid),
                                 (x-500,y,mid,x+500,y+5,mid,x+500,y,mid),
                                 (x-500,y,mid,x-500,y+5,mid,x+500,y+5,mid)]
                    run.match.collision_world = World(triangles, name="actual pin LOS break")
                    owner.mover.use(world=run.match.collision_world)
                else:
                    bot.position = (x, y, z + 10)
                for _ in range(100 if wall else 10):
                    run.step({})
                self.assertNotIn(bot.entity, run.match.combat.pins)
                self.assertEqual(run.match.combat.vitals[owner.body].total, before)
                if not wall:
                    for _ in range(150):
                        run.step({})
                        if bot.entity in run.match.combat.pins:
                            break
                    self.assertEqual(run.match.combat.pins.get(bot.entity), owner.body)
                    self.assertEqual(run.match.combat.vitals[owner.body].total, before - 25)

    def test_allied_actual_assassin_cannot_engage_or_damage_reaper(self):
        fixture = npc_tests.ReaperNpcTests()
        run, owner, _, bot = fixture.scene(fixture.catalog["4"], team_mode=True)
        bot.team = owner.team
        with self.assertRaises(ValueError):
            fixture.engage(run, owner, bot)
        for _ in range(100):
            run.step({})
        self.assertEqual(run.match.combat.vitals[owner.body].total, 250)
        self.assertNotIn(bot.entity, run.match.combat.pins)
