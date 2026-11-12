"""Build-104319 Moira health-operation regressions, through real script input.

These are server tests, not original-client or full-hero acceptance.
"""

import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bots, combat, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

MOIRA = 0x02E00000000001A2


class MoiraSprayTests(unittest.TestCase):
    def setup_fight(self, *, ally=True, free_for_all=False, distance=3.0, offset=0.0):
        run = Run(KANEZAKA_DM if free_for_all else PRACTICE_RANGE, ("Moira", "Target"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, victim = run.players
        match.switch_hero(source, heroes()[MOIRA])
        victim.team = source.team if ally else 1 - source.team
        x, y, z = source.mover.position
        victim.mover.position = [x + offset, y, z + distance]
        for _ in range(20):
            run.step({})
        target = next(t for t in match.combat._targets() if t.owner is victim)
        vitals = match.combat.vitals_of(target)
        vitals.damage(100)
        return run, source, target, vitals

    def spray(self, run, source, direction=(0, 0)):
        for _ in range(60):
            run.step({source: (*direction, 1)})

    def test_authored_spray_heals_an_ally_without_damage_or_hit_notices(self):
        run, source, _target, vitals = self.setup_fight()
        self.spray(run, source)
        self.assertGreater(vitals.total, 100)
        self.assertLessEqual(vitals.total, 200)
        self.assertAlmostEqual(source.stats.value(stats.HEALING), vitals.total - 100)
        self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(source.client.messages(combat.HIT), [])
        self.assertIsNone(source.body_script.component.fault)
        self.assertFalse(vitals.dead)

    def test_authored_spray_does_not_damage_or_heal_an_enemy(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        self.spray(run, source)
        self.assertEqual(vitals.total, 100)
        self.assertEqual(source.stats.value(stats.HEALING), 0)
        self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)

    def test_free_for_all_has_no_healable_teammates(self):
        run, source, _target, vitals = self.setup_fight(free_for_all=True)
        self.spray(run, source)
        self.assertEqual(vitals.total, 100)

    def test_dead_ally_is_not_resurrected(self):
        run, source, _target, vitals = self.setup_fight()
        vitals.damage(100)
        vitals.died_at = run.frame * combat.TICK
        self.spray(run, source)
        self.assertEqual(vitals.total, 0)
        self.assertTrue(vitals.dead)

    def test_healing_impact_rechecks_team_and_caps_restoration(self):
        run, source, target, vitals = self.setup_fight()
        fight = run.match.combat
        definition = combat.HitDef(500, healing=True)
        target.team = 1 - source.team
        fight._apply_hit(source, target, definition, 500, False, run.frame)
        self.assertEqual(vitals.total, 100)
        target.team = source.team
        fight._apply_hit(source, target, definition, 500, False, run.frame)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(source.stats.value(stats.HEALING), 100)

    def test_authored_spray_preserves_npc_allegiance(self):
        for ally in (True, False):
            with self.subTest(ally=ally):
                run, source, _target, _vitals = self.setup_fight()
                victim = run.players.pop()
                run.match.players.remove(victim)
                x, y, z = source.mover.position
                # A stationary training body exercises the production Bot path.
                # The five requested PvE archetypes remain separate acceptance.
                bot = bots.Bot(
                    bots.BOT_ENTITY + 100,
                    {"hero": "0x02E000000000016B", "team": source.team if ally else 1 - source.team,
                     "position": (x, y, z + 3), "rotation": (0, 0, 0, 1), "graphs": []},
                    {"name": "Training Bot", "body": "0x0400000000001403", "health": 200}, {},
                )
                run.match.bots = [bot]
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                vitals = run.match.combat.vitals_of(target)
                vitals.damage(100)
                direction = aim(run.match.combat._eye(source), (x, y + target.height / 2, z + 3))
                self.spray(run, source, direction)
                self.assertEqual(vitals.total > 100, ally)
                self.assertGreaterEqual(vitals.total, 100)
                self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)

    def test_moira_takes_real_enemy_rifle_damage(self):
        run, source, target, _vitals = self.setup_fight(ally=False)
        attacker = target.owner
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        vitals = fight.vitals_of(own)
        for _ in range(35):
            run.step({attacker: (-32768, 0, 1)})
        self.assertLess(vitals.total, 200)
        self.assertGreater(attacker.stats.value(stats.HERO_DAMAGE), 0)

    def test_moira_quick_melee_damages_enemy_once(self):
        run, source, target, vitals = self.setup_fight(ally=False)
        x, y, z = source.mover.position
        target.owner.mover.position = [x, y, z + 1.5]
        run.step({source: (0, 0, 0x800)})
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, 70)
        self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 30)


class MoiraOpenAcceptanceTests(unittest.TestCase):
    """Incremental effect acceptance; passing these is not full hero acceptance."""

    setup_fight = MoiraSprayTests.setup_fight

    def start_ult(self, run, source):
        script = source.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({source: (0, 0, 32)})
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x1007)
        for _ in range(60):
            if instance.states.get(20) is not None and instance.states[20].active:
                return instance
            run.step({})
        self.fail("authored ultimate did not enter its effect phase")

    def test_reconstructed_secondary_grasp_deals_damage_to_enemy(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        for _ in range(60):
            run.step({source: (0, 0, 2)})
        self.assertAlmostEqual(vitals.total, 52, places=4)
        self.assertAlmostEqual(source.stats.value(stats.HERO_DAMAGE), 48, places=4)

    def test_reconstructed_secondary_grasp_self_heals_only_after_damage(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        health = fight.vitals_of(own)
        health.damage(100)
        for _ in range(20):
            run.step({source: (0, 0, 2)})
        self.assertAlmostEqual(vitals.total, 84, places=4)
        self.assertAlmostEqual(health.total, 107.68, places=4)
        before = vitals.total, health.total
        for _ in range(10):
            run.step({source: (16384, 0, 2)})
        self.assertEqual((vitals.total, health.total), before)
        for _ in range(10):
            run.step({})
        self.assertEqual((vitals.total, health.total), before)

    def test_reconstructed_secondary_respects_allies_range_and_wall(self):
        for ally, distance, wall in [(True, 3, False), (False, 21, False), (False, 3, True)]:
            with self.subTest(ally=ally, distance=distance, wall=wall):
                run, source, _target, vitals = self.setup_fight(ally=ally, distance=distance)
                fight = run.match.combat
                collision = fight._collision()
                if wall:
                    x, y, z = source.mover.position
                    z += 1.5
                    collision = World([(x - 10, y - 10, z, x + 10, y - 10, z, x + 10, y + 10, z),
                                       (x - 10, y - 10, z, x + 10, y + 10, z, x - 10, y + 10, z)])
                with patch.object(fight, "_collision", return_value=collision):
                    for _ in range(20):
                        run.step({source: (0, 0, 2)})
                self.assertEqual(vitals.total, 100)

    def test_reconstructed_fade_prevents_enemy_rifle_damage(self):
        run, source, target, _vitals = self.setup_fight(ally=False)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        vitals = fight.vitals_of(own)
        run.step({source: (0, 0, 8)})
        for _ in range(20):
            run.step({target.owner: (-32768, 0, 1)})
        self.assertEqual(vitals.total, 200, "Fade currently has no authoritative invulnerability")

    def test_reconstructed_fade_finishes_and_starts_cooldown(self):
        run, source, _target, _vitals = self.setup_fight()
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == 0x0F7C)
        ability = instance.states[9]
        run.step({source: (0, 0, 8)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(70):
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING,
                         "Fade remains stuck behind unimplemented state1")
        self.assertGreater(ability.remaining(), 0)

    def test_fade_held_and_buffered_inputs_respect_authored_readiness(self):
        for pattern in ("held", "buffered", "expired"):
            with self.subTest(pattern=pattern):
                run, source, _, _ = self.setup_fight()
                instance = next(i for i in source.body_script.component.instances.values()
                                if i.graph.index == 0x0F7C)
                ability = instance.states[9]
                window = ability.evaluate(ability.node.field("m_246438AD"), watch=False)
                self.assertAlmostEqual(window, 0.15)
                run.step({source: (0, 0, 8)})
                entry = instance.states[1]
                first = entry.counter
                for _ in range(70):
                    run.step({})
                self.assertGreater(ability.remaining(), 5)
                press_at = window / 2 if pattern == "buffered" else window * 2
                pressed = False
                quantum = source.quanta / 1e6
                for _ in range(420):
                    remaining = ability.remaining()
                    press = not pressed and remaining <= press_at
                    down = press or (pressed and pattern == "held")
                    run.step({source: (0, 0, 8 if down else 0)})
                    if press:
                        pressed = True
                        source.take_commands([source.mover.last_command])
                        self.assertEqual(entry.counter, first)
                    if entry.counter != first:
                        self.assertLessEqual(remaining, quantum + 0.001)
                        break
                    if remaining <= 0 and pattern == "expired":
                        break
                self.assertTrue(pressed)
                if pattern == "expired":
                    self.assertEqual(ability.remaining(), 0)
                    self.assertEqual(entry.counter, first)
                    run.step({source: (0, 0, 8)})
                self.assertEqual(entry.counter, first + 1)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                source.take_commands([source.mover.last_command])
                self.assertEqual(entry.counter, first + 1)

    def test_reconstructed_fade_cooldown_rejects_repeat_and_releases(self):
        run, source, _target, _vitals = self.setup_fight()
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == 0x0F7C)
        ability = instance.states[9]
        run.step({source: (0, 0, 8)})
        entry = instance.states[1]
        first = entry.counter
        for _ in range(70):
            run.step({})
        self.assertGreater(ability.remaining(), 5)
        # Leave enough time for the last short press to age out before
        # readiness. Continue checking the final frames with the key up.
        for frame in range(400):
            if ability.remaining() <= 0.2:
                break
            run.step({source: (0, 0, 8 if frame % 2 == 0 else 0)})
            self.assertGreater(ability.remaining(), 0)
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
            self.assertEqual(entry.counter, first)
        self.assertGreater(frame, 300)
        self.assertGreater(ability.remaining(), 0)
        self.assertLessEqual(ability.remaining(), 0.2)
        for _ in range(15):
            run.step({})
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
            self.assertEqual(entry.counter, first)
        self.assertEqual(ability.remaining(), 0)
        run.step({source: (0, 0, 8)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(entry.counter, first + 1)
        for _ in range(70):
            run.step({})
        self.assertGreater(ability.remaining(), 5)
        for _ in range(400):
            run.step({source: (0, 0, 8)})
            if ability.flags & nodes.Ability.RUNNING:
                break
            self.assertEqual(entry.counter, first + 1)
            self.assertFalse(run.match.combat.damage_immune(source))
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(entry.counter, first + 2)
        self.assertTrue(run.match.combat.damage_immune(source))

    def test_fade_readiness_survives_a_gap_longer_than_body_catch_up(self):
        def first_fade():
            run, source, _, _ = self.setup_fight()
            instance = next(i for i in source.body_script.component.instances.values()
                            if i.graph.index == 0x0F7C)
            run.step({source: (0, 0, 8)})
            self.assertTrue(instance.states[9].flags & nodes.Ability.RUNNING)
            return run, source, instance.states[9], instance.states[1]

        run, source, ability, entry = first_fade()
        start = run.frame
        for _ in range(500):
            run.step({})
            if not ability.flags & nodes.Ability.RUNNING and ability.remaining() == 0:
                break
        else:
            self.fail("Fade never became ready")
        delay = run.frame - start
        for elapsed in (delay - 3, delay + 2):
            with self.subTest(ready=elapsed > delay):
                run, source, ability, entry = first_fade()
                first = entry.counter
                run.frame += elapsed
                run.match.update(run.frame * combat.TICK, run.frame)
                command = Command(run.frame, buttons=8)
                source.take_commands([command, command])
                self.assertEqual(entry.counter, first + (elapsed > delay))
                self.assertEqual(bool(ability.flags & nodes.Ability.RUNNING), elapsed > delay)

    def test_fade_immunity_expires_while_only_the_enemy_sends_commands(self):
        run, source, target, _ = self.setup_fight(ally=False)
        fight, attacker = run.match.combat, target.owner
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
        yaw, pitch = aim(fight._eye(attacker), fight._eye(source))
        run.step({source: (0, 0, 8)})
        self.assertTrue(fight.damage_immune(source))
        before = health.total
        for frame in range(70):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            attacker.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
            if frame < 40:
                self.assertTrue(fight.damage_immune(source))
                self.assertEqual(health.total, before)
        self.assertFalse(fight.damage_immune(source))
        self.assertLess(health.total, before)

    def test_reconstructed_fade_immunity_expires_with_main_phase(self):
        run, source, target, _vitals = self.setup_fight(ally=False)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        vitals = fight.vitals_of(own)
        run.step({source: (0, 0, 8)})
        fight._damage(target.owner, own, 50, False, None, run.frame)
        self.assertEqual(vitals.total, 200)
        for _ in range(55):
            run.step({})
        fight._damage(target.owner, own, 50, False, None, run.frame)
        self.assertEqual(vitals.total, 150)

    def test_reconstructed_coalescence_deals_damage_after_charged_release(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        cost = source.body_script.component.entity_var(ULT_COST).value()
        source.body_script.set_ult_charge(cost)
        run.step({source: (0, 0, 32)})
        self.assertEqual(source.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(60):
            run.step({})
        self.assertLess(vitals.total, 100)

    def test_reconstructed_coalescence_enemy_and_self_rates(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        health = fight.vitals_of(own)
        health.damage(100)
        self.start_ult(run, source)
        before = vitals.total, health.total
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(before[0] - vitals.total, 22.4, places=4)
        self.assertAlmostEqual(health.total - before[1], 16, places=4)

    def test_reconstructed_coalescence_ally_rate(self):
        run, source, _target, vitals = self.setup_fight()
        self.start_ult(run, source)
        before = vitals.total
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(vitals.total - before, 44.8, places=4)
        self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)

    def test_coalescence_requires_charge_then_ends_without_repeating(self):
        run, source, _target, vitals = self.setup_fight()
        script = source.body_script
        script.set_ult_charge(0)
        run.step({source: (0, 0, 32)})
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x1007)
        self.assertFalse(instance.states[0].flags & nodes.Ability.RUNNING)
        self.assertEqual(vitals.total, 100)
        run.step({})
        self.start_ult(run, source)
        for _ in range(550):
            run.step({source: (0, 0, 32)})
        self.assertFalse(instance.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(instance.states[20].active)
        self.assertLess(script.component.entity_var(ULT_CHARGE).value(), 100)
        own = next(t for t in run.match.combat._targets() if t.owner is source)
        health = run.match.combat.vitals_of(own)
        health.damage(50)
        for _ in range(20):
            run.step({})
        self.assertEqual(health.total, 150)

    def test_coalescence_stops_dealing_damage_on_death(self):
        run, source, target, vitals = self.setup_fight(ally=False)
        self.start_ult(run, source)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        fight._damage(target.owner, own, 1000, False, None, run.frame)
        self.assertTrue(fight.is_dead(source))
        before = vitals.total
        for _ in range(20):
            run.step({source: (0, 0, 32)})
        self.assertEqual(vitals.total, before)
        self.assertLess(source.body_script.component.entity_var(ULT_CHARGE).value(), 100)

    def test_coalescence_ends_without_further_owner_commands(self):
        for updates in (range(1, 551), (250, 550), (550,)):
            with self.subTest(updates=updates):
                run, source, _, health = self.setup_fight(ally=False)
                script = source.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({source: (0, 0, 32)})
                instance = next(i for i in script.component.instances.values() if i.graph.index == 0x1007)
                ability = instance.states[0]
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertFalse(instance.state(20).active)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                before, start = health.total, run.frame
                for elapsed in updates:
                    run.frame = start + elapsed
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if elapsed == 250:
                        self.assertTrue(instance.states[20].active)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertFalse(instance.states[20].active)
                command = Command(run.frame, buttons=32)
                source.take_commands([command, command])
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(health.total, before)


if __name__ == "__main__":
    unittest.main()
