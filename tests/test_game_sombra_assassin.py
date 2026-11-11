"""Actual1157 and Sombra inputs; manual pin policy remains reconstructed."""

import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
import test_game_sombra_grenade as grenades
import test_game_sombra_npc as actual_npcs
import test_game_sombra_orisa_halt as halt_fixture

from ow174.game import combat, mei, npc, npc_control, phase_defenses, reinhardt_barrier, tracer_combat
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes

SOMBRA = 0x02E000000000012E


class SombraAssassinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def make(self, *, support=False):
        f, bot = actual_npcs.SombraActualNpcTests.make(self, "4", distance=2)
        if support:
            f.b.spawned = True
            f.run.match.switch_hero(f.b, heroes()[SOMBRA])
            f.b.mover.position[:] = (3, 0, 2)
            f.b.mover.body = None
            f.b.body_script.ult_seconds = 0
            f.press(0, 50)
        f.a.body_script.ult_seconds = 0
        return f, bot

    def step(self, f, bot, *, victim=0, support=0, forward=0):
        run, match = f.run, f.run.match
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            point = (bot.position[0], bot.position[1] + 1, bot.position[2])
            yaw, pitch = actual_npcs.aim(match.combat._eye(player), point)
            player.client.queue_entities(match.remote_movements(player))
            player.take_commands([Command(run.frame, yaw=yaw, pitch=pitch,
                                          buttons=victim if player is f.a else support,
                                          forward=forward if player is f.a else 0)])

    def engage(self, f, bot):
        self.step(f, bot)
        self.assertEqual(npc_control.start(f.run.match, f.a), bot.entity)
        for _ in range(8):
            self.step(f, bot)
            if bot.entity in f.run.match.combat.pins:
                return
        self.fail("Actual1157 did not reach pin contact")

    @staticmethod
    def pin_root(bot):
        return next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)

    def test_actual_contact_delay_continuous_damage_stop_and_restart(self):
        f, bot = self.make()
        self.engage(f, bot)
        fight, root = f.run.match.combat, self.pin_root(bot)
        self.assertEqual(f.health(f.a), 175)
        self.assertEqual(root.vars[536].value().id, f.a.body)
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertEqual(root.vars[13230].value(), 25)
        self.assertEqual(root.vars[11984].value(), 100)
        for _ in range(40):
            self.step(f, bot)
        self.assertEqual(f.health(f.a), 175)
        for _ in range(40):
            self.step(f, bot)
            if root.node_state(root.graph.node(15)).active:
                break
        self.assertTrue(root.node_state(root.graph.node(15)).active)
        before = f.health(f.a)
        for _ in range(10):
            self.step(f, bot)
        self.assertAlmostEqual(before - f.health(f.a), 16, places=4)
        npc_control.stop(f.run.match)
        before = f.health(f.a)
        for _ in range(15):
            self.step(f, bot)
        self.assertEqual(f.health(f.a), before)
        self.assertFalse(fight.pins)
        self.engage(f, bot)
        self.assertAlmostEqual(f.health(f.a), before - 25, places=4)

    def test_actual_pin_contact_preserves_damage_interruption_cooldown_of_hack(self):
        f, bot = self.make()
        for _ in range(20):
            self.step(f, bot, victim=2)
        ability = f.weapon().state(53)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(npc_control.start(f.run.match, f.a), bot.entity)
        self.step(f, bot, victim=2)
        self.assertEqual(f.health(f.a), 175)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 1.9)
        self.assertLessEqual(ability.remaining(), 2)
        self.assertIn(bot.entity, f.run.match.combat.pins)

    def test_actual_pin_interrupts_camo_and_emp_windup(self):
        for mode in ("camo", "emp"):
            with self.subTest(mode=mode):
                f, bot = self.make()
                if mode == "camo":
                    self.step(f, bot, victim=8)
                    for _ in range(60):
                        self.step(f, bot)
                    ability = f.camo().state(13)
                else:
                    f.a.body_script.set_ult_charge(1400)
                    self.step(f, bot, victim=32)
                    root = next(i for i in f.a.body_script.component.instances.values()
                                if i.graph.index == 0x0B31)
                    ability = root.state(1)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.engage(f, bot)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                for _ in range(30):
                    self.step(f, bot)
                if mode == "emp":
                    self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
                    self.assertEqual(f.run.match.combat.vitals[bot.entity].total, 500)
                    self.assertNotIn(bot.entity, f.run.match.combat.hacks)
                else:
                    self.assertGreater(ability.remaining(), 0)

    def test_actual_pin_blocks_movement_attacks_and_skill_resources_then_releases(self):
        f, bot = self.make()
        f.a.body_script.set_ult_charge(1400)
        self.engage(f, bot)
        position = tuple(f.a.mover.position)
        for _ in range(20):
            self.step(f, bot, victim=FIRE | 0x800 | 32 | 16 | 8, forward=127)
        self.assertEqual(tuple(f.a.mover.position), position)
        self.assertEqual(f.weapon().vars[53].value(), 60)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 1400)
        self.assertFalse(f.run.match.combat.translocators.entries)
        self.assertEqual(f.run.match.combat.vitals[bot.entity].total, 500)
        npc_control.stop(f.run.match)
        self.step(f, bot)
        for _ in range(5):
            self.step(f, bot, victim=FIRE, forward=127)
        self.assertGreater(f.a.mover.position[2], position[2])
        self.assertLess(f.run.match.combat.vitals[bot.entity].total, 500)

    def test_actual_hack_rejects_pin_and_allows_new_contact_after_expiry(self):
        f, bot = self.make()
        for _ in range(100):
            self.step(f, bot, victim=2)
        fight = f.run.match.combat
        self.assertIn(bot.entity, fight.hacks)
        end = max(e.until_tick for e in fight.hacks[bot.entity])
        self.assertEqual(npc_control.start(f.run.match, f.a), bot.entity)
        while f.run.frame < end - 1:
            self.step(f, bot)
            self.assertFalse(fight.pins)
            self.assertEqual(f.health(f.a), 200)
        for _ in range(3):
            self.step(f, bot)
        self.assertNotIn(bot.entity, fight.hacks)
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertEqual(f.health(f.a), 175)

    def test_actual_support_hack_or_emp_interrupts_pin_and_prevents_late_damage(self):
        for mode in ("hack", "emp"):
            with self.subTest(mode=mode):
                f, bot = self.make(support=True)
                self.engage(f, bot)
                fight = f.run.match.combat
                if mode == "emp":
                    f.b.body_script.set_ult_charge(1400)
                for n in range(120):
                    self.step(f, bot, support=2 if mode == "hack" else (32 if n == 0 else 0))
                    if bot.entity in fight.hacks:
                        break
                self.assertIn(bot.entity, fight.hacks)
                self.assertFalse(fight.pins)
                self.assertGreater(f.health(f.a), 0)
                before = f.health(f.a)
                for _ in range(20):
                    self.step(f, bot)
                self.assertEqual(f.health(f.a), before)
                self.assertFalse(self.pin_root(bot).node_state(self.pin_root(bot).graph.node(23)).active)

    def test_actual_allied_melee_rescues_pinned_sombra_without_killing_assassin(self):
        for attack in ("melee", "pistol", "miss", "wall", "range", "ffa"):
            with self.subTest(attack=attack):
                f, bot = self.make(support=True)
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=attack == "ffa")
                f.a.team = f.b.team = 0
                f.b.mover.position[:] = (5 if attack == "range" else 1.5, 0, 2)
                f.b.mover.body = None
                f.press(0, 10)
                self.engage(f, bot)
                if attack == "wall":
                    f.geometry((0.75, 0, 1, 0.75, 4, 1, 0.75, 4, 3),
                               (0.75, 0, 1, 0.75, 4, 3, 0.75, 0, 3))
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                for frame in range(34):
                    yaw, pitch = actual_npcs.aim(fight._eye(f.b), (0, 1, 2))
                    if attack == "miss":
                        yaw = 0
                    button = (FIRE if attack == "pistol" else 0x800) if frame == 0 else 0
                    f.run.step({f.b: (yaw, pitch, button)})
                    if vitals.total < before:
                        break

                expected = {"melee": 30, "pistol": 8, "ffa": 30}.get(attack, 0)
                self.assertEqual(vitals.total, before - expected)
                self.assertFalse(vitals.dead)
                if attack == "melee":
                    self.assertNotIn(bot.entity, fight.pins)
                    f.press(8)
                    f.press(0, 70)
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertTrue(f.camo().state(13).flags & nodes.Ability.RUNNING)
                    self.assertEqual(f.health(f.a), 175)
                    self.engage(f, bot)
                    self.assertEqual(fight.pins[bot.entity], f.a.body)
                    self.assertEqual(f.health(f.a), 150)
                else:
                    self.assertEqual(fight.pins[bot.entity], f.a.body)

    def test_actual_projected_bubble_absorbs_assassin_contact_without_pin(self):
        f, bot = self.make(support=True)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        self.assertTrue(match.switch_hero(f.b, heroes()[reinhardt_barrier.ZARYA]))
        f.b.mover.position[:] = (3, 0, 0)
        f.b.mover.body = None
        f.press(0, 50)
        yaw, pitch = actual_npcs.aim(fight._eye(f.b), (0, 1, 0))
        f.run.step({f.b: (yaw, pitch, 16)})
        f.press(0, 12)
        bubble, = fight.barriers.values()
        self.assertIs(bubble.protected, f.a)
        self.assertEqual(bubble.vitals.total, 200)
        self.step(f, bot)
        self.assertEqual(npc_control.start(match, f.a), bot.entity)
        for _ in range(20):
            self.step(f, bot)
            self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(bubble.vitals.total, 175)
        self.assertAlmostEqual(f.b.body_script.component.entity_var(87).value(), 5, places=4)
        f.press(0, 150)
        self.assertNotIn(bubble.entity, fight.barriers)
        self.assertNotIn(bot.entity, fight.pins)
        self.engage(f, bot)
        self.assertEqual(f.health(f.a), 175)

    def test_actual_phase_blocks_pin_then_allied_sombra_can_rescue_after_expiry(self):
        for hero in (0x02E0000000000002, 0x02E00000000001A2):
            with self.subTest(hero=hero):
                f, bot = self.make(support=True)
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = f.b.team = 0
                self.assertTrue(match.switch_hero(f.a, heroes()[hero]))
                f.a.mover.position[:] = (0, 0, 0)
                f.a.mover.body = None
                f.press(0, 50)
                before = f.health(f.a)
                self.step(f, bot, victim=8)
                self.assertTrue(phase_defenses.active(f.a))
                self.assertEqual(npc_control.start(match, f.a), bot.entity)
                for _ in range(12):
                    self.step(f, bot)
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(f.health(f.a), before)
                self.assertTrue(phase_defenses.active(f.a))
                npc_control.stop(match)
                for _ in range(230):
                    if not phase_defenses.active(f.a):
                        break
                    self.step(f, bot)
                self.assertFalse(phase_defenses.active(f.a))

                self.engage(f, bot)
                self.assertEqual(f.health(f.a), before - 25)
                f.b.body_script.set_ult_charge(1400)
                self.step(f, bot, support=32)
                for _ in range(35):
                    self.step(f, bot)
                self.assertNotIn(bot.entity, fight.pins)
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.b.body)
                self.assertNotIn(f.a.body, fight.hacks)
                self.assertEqual(f.health(f.a), before - 25)

    def test_actual_support_pistol_kill_releases_pin_and_preserves_npc_corpse(self):
        f, bot = self.make(support=True)
        fight = f.run.match.combat
        for _ in range(210):
            if fight.vitals[bot.entity].total <= 32:
                break
            self.step(f, bot, victim=FIRE)
        self.assertGreater(fight.vitals[bot.entity].total, 0)
        self.assertLessEqual(fight.vitals[bot.entity].total, 32)
        self.step(f, bot)
        self.engage(f, bot)
        for _ in range(30):
            self.step(f, bot, support=FIRE)
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertFalse(fight.pins)
        self.assertIn(bot, f.run.match.bots)
        before = f.health(f.a)
        for _ in range(20):
            self.step(f, bot)
        self.assertEqual(f.health(f.a), before)

    def test_actual_continuous_attack_kills_sombra_and_retires_pin(self):
        f, bot = self.make()
        self.engage(f, bot)
        for _ in range(210):
            self.step(f, bot)
            if f.health(f.a) == 0:
                break
        self.assertEqual(f.health(f.a), 0)
        self.assertFalse(f.run.match.combat.pins)
        self.assertTrue(f.run.match.combat.is_dead(f.a))

    def test_fatal_actual_contact_does_not_reinsert_pin_after_death_cleanup(self):
        f, bot = self.make(support=True)
        f.press(FIRE, 70, shooter=f.b, target=f.a)
        self.assertGreater(f.health(f.a), 0)
        self.assertLessEqual(f.health(f.a), 25)
        self.step(f, bot)
        self.assertEqual(npc_control.start(f.run.match, f.a), bot.entity)
        self.step(f, bot)
        self.assertEqual(f.health(f.a), 0)
        self.assertFalse(f.run.match.combat.pins)

    def test_actual_pin_vm_capture_is_passive_and_clock_context_sensitive(self):
        f, bot = self.make()
        self.engage(f, bot)
        root = self.pin_root(bot)
        for _ in range(80):
            self.step(f, bot)
            if root.node_state(root.graph.node(15)).active:
                break
        state = root.node_state(root.graph.node(15))
        self.assertTrue(state.active)
        component, world = bot.body_script.component, bot.body_script.component.world
        before_health, before_frame = f.health(f.a), component.frame
        first = snapshot_digest(export_component_vm(component))
        self.assertEqual(snapshot_digest(export_component_vm(component)), first)
        self.assertEqual(f.health(f.a), before_health)
        self.assertEqual(component.frame, before_frame)
        state.last_applied += 1
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), first)
        state.last_applied -= 1
        self.assertEqual(snapshot_digest(export_component_vm(component)), first)
        world.manual_target = None
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), first)

    def test_manual_pin_waits_for_protection_then_opens_before_sombra_rescues(self):
        cases = [(tracer_combat.HERO, 16), (mei.HERO, 8)]
        cases.extend((hero, 8) for hero in phase_defenses.PHASES)
        for hero, button in cases:
            with self.subTest(hero=f"{hero:016X}"):
                f, bot = self.make()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.b.spawned = True
                self.assertTrue(match.switch_hero(f.b, heroes()[hero]))
                f.a.team = f.b.team = 0
                bot.team = 1
                halt_fixture.place(f.b, (0, 0, 0.5))
                f.press(0, 205 if hero == tracer_combat.HERO else 50)
                before = f.health(f.b)
                self.step(f, bot, support=button)

                protected = mei.protected if hero == mei.HERO else phase_defenses.active
                self.assertTrue(protected(f.b))
                self.assertEqual(npc_control.start(match, f.b), bot.entity)
                for _ in range(350):
                    self.step(f, bot)
                    if not protected(f.b):
                        break
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(f.health(f.b), before)
                self.assertFalse(protected(f.b))
                for _ in range(2):
                    if bot.entity in fight.pins:
                        break
                    self.step(f, bot)
                self.assertEqual(fight.pins[bot.entity], f.b.body)
                self.assertEqual(f.health(f.b), before - 25)
                self.step(f, bot, victim=0x800)
                for _ in range(20):
                    self.step(f, bot)
                    if bot.entity not in fight.pins:
                        break
                self.assertNotIn(bot.entity, fight.pins)
                self.assertEqual(fight.vitals[bot.entity].total, 470)
                self.assertEqual(f.health(f.b), before - 25)

    def test_actual_pin_opens_after_protection_expiry_with_omitted_owner_frames(self):
        cases = [(tracer_combat.HERO, 16, 80), (mei.HERO, 8, 250)]
        cases += [(0x02E0000000000002, 8, 188), (0x02E00000000001A2, 8, 47)]
        for hero, button, frames in cases:
            for gap in (False, True):
                with self.subTest(hero=f"{hero:016X}", gap=gap):
                    f, bot = self.make()
                    match = f.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.b.spawned = True
                    self.assertTrue(match.switch_hero(f.b, heroes()[hero]))
                    f.a.team = f.b.team = 0
                    bot.team = 1
                    halt_fixture.place(f.b, (0, 0, 0.5))
                    f.press(0, 205 if hero == tracer_combat.HERO else 50)
                    before = f.health(f.b)
                    self.step(f, bot, support=button)
                    protected = mei.protected if hero == mei.HERO else phase_defenses.active
                    self.assertTrue(protected(f.b))
                    self.assertEqual(npc_control.start(match, f.b), bot.entity)
                    endpoint = f.run.frame + frames
                    if gap:
                        match.update(endpoint * combat.TICK, endpoint)
                    else:
                        for _ in range(frames - 1):
                            self.step(f, bot)
                        self.assertNotIn(bot.entity, fight.pins)
                        self.assertEqual(f.health(f.b), before)
                        self.step(f, bot)
                    self.assertFalse(protected(f.b))
                    self.assertTrue(bot.attack_input)
                    self.assertEqual(fight.pins.get(bot.entity), f.b.body)
                    self.assertEqual(f.health(f.b), before - 25)

    def test_autonomous_pin_skips_cryo_and_starts_with_opening_contact(self):
        for alternative in (False, True):
            with self.subTest(alternative=alternative):
                f = scenes.SombraCombatTests()
                f.setUp()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                self.assertTrue(match.switch_hero(f.b, heroes()[mei.HERO]))
                f.a.team = f.b.team = 0
                halt_fixture.place(f.a, (1.8 if alternative else 4, 0, 0))
                halt_fixture.place(f.b, (0, 0, 0.55))
                victim = f.b
                if alternative:
                    victim = grenades.SombraGrenadeTests().join_ally(f, SOLDIER, (0, 0, 1.55))
                for player in f.run.players:
                    player.client.bot_stream = lambda body: None
                f.press(0, 50)
                before = f.health(victim)
                f.press(8, shooter=f.b, target=f.a)
                self.assertTrue(mei.protected(f.b))
                bot = npc.spawn(match, self.catalog["4"], (0, 0, 0), team=1, autonomous=True)
                f.press(0, 4)
                if alternative:
                    self.assertTrue(mei.protected(f.b))
                    self.assertEqual(f.health(f.b), 250)
                else:
                    for _ in range(300):
                        if not mei.protected(f.b):
                            break
                        self.assertIsNone(bot.attack_target)
                        self.assertFalse(bot.attack_input)
                        self.assertNotIn(bot.entity, fight.pins)
                        self.assertEqual(f.health(f.b), before)
                        f.press(0)
                    self.assertFalse(mei.protected(f.b))
                    f.press(0)
                self.assertEqual(bot.attack_target, victim.entity)
                self.assertEqual(fight.pins[bot.entity], victim.body)
                self.assertEqual(f.health(victim), before - 25)
                halt_fixture.place(f.a, (1.8, 0, 0))
                self.step(f, bot, victim=0x800)
                for _ in range(20):
                    self.step(f, bot)
                    if bot.entity not in fight.pins:
                        break
                self.assertNotIn(bot.entity, fight.pins)
                self.assertEqual(fight.vitals[bot.entity].total, 470)
                self.assertEqual(f.health(victim), before - 25)

    def test_autonomous_assassin_keeps_valid_pin_until_closer_sombra_rescues(self):
        f = scenes.SombraCombatTests()
        f.setUp()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        halt_fixture.place(f.a, (0, 0, 4))
        halt_fixture.place(f.b, (0, 0, 1.6))
        for player in f.run.players:
            player.client.bot_stream = lambda body: None
        f.press(0, 35)
        bot = npc.spawn(match, self.catalog["4"], (0, 0, 0), team=1, autonomous=True)
        f.press(0, 4)
        self.assertEqual(fight.pins[bot.entity], f.b.body)
        self.assertEqual(f.health(f.b), 175)
        for _ in range(45):
            self.step(f, bot, forward=127)
            self.assertEqual(fight.pins[bot.entity], f.b.body)
            if f.a.mover.position[2] < 1.35:
                break
        self.assertLess(f.a.mover.position[2], 1.35)
        self.assertEqual(bot.attack_target, f.b.entity)
        self.assertEqual(f.health(f.a), 200)
        self.step(f, bot, victim=0x800)
        for _ in range(20):
            self.step(f, bot)
            if bot.entity not in fight.pins:
                break
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(fight.vitals[bot.entity].total, 470)
        self.assertEqual(f.health(f.b), 175)

    def test_actual_pin_releases_on_static_wall_range_team_and_body_replacement(self):
        for reason in ("wall", "range", "team", "body"):
            with self.subTest(reason=reason):
                f, bot = self.make()
                self.engage(f, bot)
                before = f.health(f.a)
                if reason == "wall":
                    f.wall()
                elif reason == "range":
                    bot.position = (0, 0, 20)
                elif reason == "team":
                    f.run.match.game_map = f.run.match.game_map.__class__(**{
                        **f.run.match.game_map.__dict__, "free_for_all": False,
                    })
                    bot.team = f.a.team
                else:
                    f.run.match.switch_hero(f.a, f.a.hero)
                    before = 200
                for _ in range(20):
                    self.step(f, bot)
                self.assertEqual(f.health(f.a), before)
                self.assertFalse(f.run.match.combat.pins)


if __name__ == "__main__":
    unittest.main()
