"""Real Flashbang input against Sombra; reconstructed control is not native immunity proof."""

import unittest

import test_game_sombra_combat as sombra
import test_game_sombra_npc as actual_npcs
import test_mccree_flashbang as flashbang

from ow174.game import combat, npc, npc_control
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import nodes

MCCREE = 0x02E0000000000042


class SombraControlTests(unittest.TestCase):
    def make(self, distance=3):
        f = sombra.SombraCombatTests()
        f.setUp()
        self.assertTrue(f.run.match.switch_hero(f.b, heroes()[MCCREE]))
        f.b.mover.position[:] = (0, 0, distance)
        f.b.mover.body = None
        f.press(0, 50)
        f.a.body_script.ult_seconds = 0
        return f

    def step(self, f, *, enemy=0, buttons=0, forward=0):
        flashbang.FlashbangTests.step(
            self, f.run, f.b, f.a, cast=enemy, target_buttons=buttons, forward=forward,
        )

    def stun(self, f, *, buttons=0):
        self.step(f, enemy=16, buttons=buttons)
        for _ in range(50):
            if f.run.match.combat.is_stunned(f.a):
                self.assertEqual(f.health(f.a), 175)
                return f.run.match.combat.stuns[f.a.body]
            self.step(f, buttons=buttons)
        self.fail("Actual enemy Flashbang did not stun Sombra")

    @staticmethod
    def emp(f):
        return next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0B31)

    @staticmethod
    def enemy_effects(f):
        return [i for i in f.b.body_script.component.instances.values() if i.graph.index in (0x0B1F, 0x0B32)]

    def test_stun_blocks_pistol_melee_and_movement_then_recovers_at_deadline(self):
        f = self.make()
        end = self.stun(f)
        position = tuple(f.a.mover.position)
        before = f.health(f.b)
        while f.run.frame < end - 1:
            self.step(f, buttons=FIRE | sombra.MELEE, forward=127)
            self.assertEqual(tuple(f.a.mover.position), position)
            self.assertEqual(f.health(f.b), before)
            self.assertEqual(f.weapon().vars[53].value(), 60)
            self.assertFalse(f.a.body_script.component.held)
        self.step(f, buttons=FIRE, forward=127)
        self.assertFalse(f.run.match.combat.is_stunned(f.a))
        self.assertNotEqual(tuple(f.a.mover.position), position)
        self.assertLess(f.health(f.b), before)
        self.assertLess(f.weapon().vars[53].value(), 60)

    def test_stun_rejects_emp_without_spending_charge_then_allows_actual_cast(self):
        f = self.make()
        end = self.stun(f)
        f.a.body_script.set_ult_charge(1400)
        while f.run.frame < end - 1:
            self.step(f, buttons=0x20)
            self.assertEqual(f.a.body_script.component.entity_var(342).value(), 1400)
            self.assertFalse(self.emp(f).state(1).flags & nodes.Ability.RUNNING)
            self.assertEqual(self.enemy_effects(f), [])
        self.step(f)  # exact expiry; release input before a new cast
        self.step(f, buttons=0x20)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        for _ in range(30):
            self.step(f)
        self.assertTrue(self.enemy_effects(f))
        self.assertIn(f.b.body, f.run.match.combat.hacks)

    def test_flashbang_interrupts_running_hack_without_application(self):
        f = self.make()
        self.step(f, buttons=2)
        self.assertTrue(f.weapon().state(53).flags & nodes.Ability.RUNNING)
        end = self.stun(f, buttons=2)
        self.assertFalse(f.weapon().state(53).flags & nodes.Ability.RUNNING)
        self.assertGreater(f.weapon().state(53).remaining(), 1.5)
        self.assertLessEqual(f.weapon().state(53).remaining(), 2)
        while f.run.frame < end:
            self.step(f, buttons=2)
        self.assertEqual(self.enemy_effects(f), [])

    def test_flashbang_ends_running_camo_and_preserves_authored_cooldown(self):
        f = self.make()
        self.step(f, buttons=8)
        for _ in range(60):
            self.step(f)
        ability = f.camo().state(13)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        end = self.stun(f)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        while f.run.frame < end:
            self.step(f, buttons=8)
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        for _ in range(round(ability.remaining() / combat.TICK) + 3):
            self.step(f)
        self.step(f, buttons=8)
        for _ in range(60):
            self.step(f)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_actual_flashbang_interrupts_emp_windup_without_refund_or_late_pulse(self):
        f = self.make()
        f.a.body_script.set_ult_charge(1400)
        self.step(f, enemy=16, buttons=0x20)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        for _ in range(20):
            if f.run.match.combat.is_stunned(f.a):
                break
            self.step(f)
        self.assertTrue(f.run.match.combat.is_stunned(f.a))
        self.assertFalse(self.emp(f).state(1).flags & nodes.Ability.RUNNING)
        for _ in range(80):
            self.step(f)
        self.assertEqual(self.enemy_effects(f), [])
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(f.health(f.b), 225)

    def test_hack_and_emp_prevent_actual_enemy_flashbang_until_silence_expires(self):
        for cast in (2, 0x20):
            with self.subTest(sombra_input=cast):
                f = self.make()
                f.a.body_script.set_ult_charge(1400)
                for _ in range(100 if cast == 2 else 30):
                    self.step(f, buttons=cast)
                fight = f.run.match.combat
                self.assertIn(f.b.body, fight.hacks)
                deadline = max(effect.until_tick for effect in fight.hacks[f.b.body])
                self.step(f, enemy=16)
                for _ in range(30):
                    self.step(f)
                self.assertEqual(f.health(f.a), 200)
                self.assertFalse(fight.is_stunned(f.a))
                while f.run.frame < deadline:
                    self.step(f)
                self.stun(f)


class SombraNpcControlOverlapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def make(self, definition):
        f = SombraControlTests().make(distance=20)
        for player in f.run.players:
            player.client.bot_stream = lambda body: None  # no observer projectile-wire claim
        bot = npc.spawn(f.run.match, definition, (0, 0, 3), team=1)
        bot.body_script.component.set(9948, 0)  # explicit authored profile, not retail difficulty
        f.press(0, 4)
        f.a.body_script.set_ult_charge(1400)
        f.press(0x20, 30)
        self.assertIn(bot.entity, f.run.match.combat.hacks)
        self.assertNotIn(f.b.body, f.run.match.combat.hacks)
        f.b.mover.position[:] = (0, 0, 6)
        f.b.mover.body = None
        return f, bot

    def stun(self, f, bot):
        flashbang.FlashbangTests.step(self, f.run, f.b, bot, cast=16)
        for _ in range(50):
            if f.run.match.combat.is_stunned(bot):
                return f.run.match.combat.stuns[bot.entity]
            flashbang.FlashbangTests.step(self, f.run, f.b, bot)
        self.fail("Actual Flashbang did not stun the NPC")

    def test_emp_hack_and_actual_flashbang_keep_independent_deadlines_for_all_five(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                f, bot = self.make(definition)
                fight = f.run.match.combat
                self.assertIn(bot.entity, fight.hacks)
                hack_end = max(effect.until_tick for effect in fight.hacks[bot.entity])
                before = fight.vitals[bot.entity].total
                # The caster started outside EMP range and is repositioned by the fixture.
                stun_end = self.stun(f, bot)
                self.assertTrue(fight.is_stunned(bot))
                self.assertEqual(fight.vitals[bot.entity].total, before - 25)
                self.assertLess(stun_end, hack_end)
                while f.run.frame < stun_end:
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                self.assertFalse(fight.is_stunned(bot))
                self.assertIn(bot.entity, fight.hacks)
                self.assertTrue(bot.body_script.component.entity_var(888).value())
                self.assertEqual(bot.body_script.component.entity_var(6396).value().id, f.a.body)
                while f.run.frame < hack_end:
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertFalse(bot.body_script.component.entity_var(888).value())
                self.assertFalse(bot.body_script.component.entity_var(6396).value())

    def test_four_actual_firearm_npcs_resume_primary_after_stun_while_emp_hack_remains(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = self.make(self.catalog[key])
                fight = f.run.match.combat
                npc_control.start(f.run.match, f.a)
                end = self.stun(f, bot)
                # Already emitted projectiles may still land; observe after that flight window.
                for _ in range(8):
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                before = f.health(f.a)
                while f.run.frame < end - 1:
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                    self.assertEqual(f.health(f.a), before)
                    self.assertNotIn(20, bot.body_script.component.held)
                hack_end = max(effect.until_tick for effect in fight.hacks[bot.entity])
                while f.run.frame < hack_end - 1 and f.health(f.a) == before:
                    flashbang.FlashbangTests.step(self, f.run, f.b, bot)
                self.assertLess(f.health(f.a), before)
                self.assertIn(bot.entity, fight.hacks)
                self.assertTrue(bot.body_script.component.entity_var(888).value())


if __name__ == "__main__":
    unittest.main()
