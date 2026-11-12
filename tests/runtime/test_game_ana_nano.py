"""Nano Boost through actual targeting, authored cast/release and combat effects."""

import unittest
from unittest.mock import patch

from test_game_ana import ANA, TEAM_MAP, set_static_wall
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr, nodes
from ow174.game.stats import HEALING


class AnaNanoTests(unittest.TestCase):
    def fight(self, *, ally_hero="Soldier: 76", enemy_hero="Soldier: 76", distance=6, ffa=False):
        run = Run(KANEZAKA_DM if ffa else TEAM_MAP, ("Ana", "Ally", "Enemy"))
        source, ally, enemy = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        self.assertTrue(match.switch_hero(source, heroes()[ANA]))
        self.assertTrue(match.switch_hero(ally, find_hero(ally_hero)))
        self.assertTrue(match.switch_hero(enemy, find_hero(enemy_hero)))
        source.team, ally.team, enemy.team = 0, 0, 1
        x, y, z = source.mover.position
        ally.mover.position = [x, y, z + distance]
        enemy.mover.position = [x + 12, y, z + distance]
        source.body_script.ult_seconds = 0
        source.body_script.set_ult_charge(0)
        for _ in range(25):
            run.step({})
        return run, source, ally, enemy

    @staticmethod
    def target(fight, actor):
        return next(target for target in fight._targets() if target.owner is actor)

    def direction(self, fight, actor, target):
        entry = self.target(fight, target)
        return aim(fight._eye(actor), (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))

    def cast(self, run, source, target, *, charged=True):
        if charged:
            source.body_script.set_ult_charge(source.body_script.component.vars[338].value())
        direction = self.direction(run.match.combat, source, target)
        run.step({source: (*direction, 32)})
        for _ in range(12):
            run.step({source: (*direction, 0)})

    def single_shot(self, run, source, target, *, wait=8):
        if source.hero.guid == ANA:
            wait = max(wait, 55)  # projectile flight plus the actual0.6s rifle dose
        direction = self.direction(run.match.combat, source, target)
        run.step({source: (*direction, 1)})
        for _ in range(wait):
            run.step({source: (*direction, 0)})

    def test_actual_cast_spends_charge_releases250_heal_and_ends_after8_seconds(self):
        run, source, ally, _enemy = self.fight(ally_hero="Roadhog")
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(fight, ally))
        self.assertEqual(vitals.total, 600)
        vitals.damage(400)
        source.body_script.set_ult_charge(source.body_script.component.vars[338].value())
        direction = self.direction(fight, source, ally)
        run.step({source: (*direction, 32)})
        self.assertEqual(source.body_script.component.vars[342].value(), 0)
        self.assertEqual(vitals.total, 200)  # the authored0.13s release has not occurred
        for _ in range(12):
            run.step({source: (*direction, 0)})
        self.assertEqual(vitals.total, 450)
        self.assertGreater(vitals.nano_until, fight.now + 7.8)
        for _ in range(90):
            run.step({})
        ultimate = source.body_script.ultimates[0]
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertGreater(vitals.nano_until, fight.now)
        for _ in range(450):
            run.step({})
        self.assertLessEqual(vitals.nano_until, fight.now)

    def test_actual_weapon_damage_is_boosted_and_reduced_then_normal_after_expiry(self):
        run, source, ally, enemy = self.fight()
        fight = run.match.combat
        self.cast(run, source, ally)
        ally_vitals = fight.vitals_of(self.target(fight, ally))
        enemy_vitals = fight.vitals_of(self.target(fight, enemy))
        self.single_shot(run, ally, enemy)
        self.assertEqual(enemy_vitals.total, 170)
        self.single_shot(run, enemy, ally)
        self.assertEqual(ally_vitals.total, 190)
        for _ in range(520):
            run.step({})
        self.single_shot(run, ally, enemy)
        self.assertEqual(enemy_vitals.total, 150)
        self.single_shot(run, enemy, ally)
        self.assertEqual(ally_vitals.total, 170)

    def test_enemy_out_of_range_and_ffa_targets_do_not_spend_charge(self):
        for distance, ffa, select_enemy in ((41, False, False), (6, False, True), (6, True, False)):
            with self.subTest(distance=distance, ffa=ffa, enemy=select_enemy):
                run, source, ally, enemy = self.fight(distance=distance, ffa=ffa)
                target = enemy if select_enemy else ally
                cost = source.body_script.component.vars[338].value()
                self.cast(run, source, target)
                self.assertEqual(source.body_script.component.vars[342].value(), cost)
                vitals = run.match.combat.vitals_of(self.target(run.match.combat, target))
                self.assertEqual(vitals.nano_until, 0)

    def test_uncharged_ally_does_not_receive_nano(self):
        run, source, ally, _enemy = self.fight()
        self.cast(run, source, ally, charged=False)
        self.assertEqual(run.match.combat.vitals_of(self.target(run.match.combat, ally)).nano_until, 0)
        self.assertEqual(source.body_script.component.vars[342].value(), 0)

    def test_wall_blocks_target_acquisition(self):
        run, source, ally, _enemy = self.fight()
        x, y, z = source.mover.position
        wall_z = z + 2.5
        floor = [(x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
                 (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50)]
        wall = [(x - 3, y, wall_z, x + 3, y, wall_z, x + 3, y + 4, wall_z),
                (x - 3, y, wall_z, x + 3, y + 4, wall_z, x - 3, y + 4, wall_z)]
        run.match.collision_world = World([*floor, *wall])
        cost = source.body_script.component.vars[338].value()
        self.cast(run, source, ally)
        self.assertEqual(source.body_script.component.vars[342].value(), cost)
        self.assertEqual(run.match.combat.vitals_of(self.target(run.match.combat, ally)).nano_until, 0)

    def test_buff_survives_caster_death_and_does_not_transfer_on_target_replacement(self):
        run, source, ally, enemy = self.fight()
        fight = run.match.combat
        self.cast(run, source, ally)
        ally_vitals = fight.vitals_of(self.target(fight, ally))
        fight._damage(enemy, self.target(fight, source), 10000, False, None, run.frame)
        self.assertTrue(fight.is_dead(source))
        self.single_shot(run, ally, enemy)
        self.assertEqual(fight.vitals_of(self.target(fight, enemy)).total, 170)
        self.assertGreater(ally_vitals.nano_until, fight.now)
        old_body = ally.body
        self.assertTrue(run.match.switch_hero(ally, ally.hero))
        self.assertNotIn(old_body, fight.vitals)
        self.assertEqual(fight.vitals_of(self.target(fight, ally)).nano_until, 0)

    def test_target_death_before_release_does_not_heal_or_boost_a_corpse(self):
        run, source, ally, enemy = self.fight()
        fight = run.match.combat
        source.body_script.set_ult_charge(source.body_script.component.vars[338].value())
        direction = self.direction(fight, source, ally)
        run.step({source: (*direction, 32)})
        vitals = fight.vitals_of(self.target(fight, ally))
        fight._damage(enemy, self.target(fight, ally), 10000, False, None, run.frame)
        for _ in range(20):
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.nano_until, 0)

    def test_paid_cast_releases_only_to_the_latched_live_allied_body(self):
        for change in ("aim", "cover", "target team", "target body", "caster body"):
            with self.subTest(change=change):
                run, source, ally, alternate = self.fight()
                fight = run.match.combat
                x, y, z = source.mover.position
                alternate.team = source.team
                alternate.spawn = (x + 2, y, z + 6), alternate.spawn[1]
                alternate.mover = alternate.new_mover()
                run.step({})
                original = fight.vitals_of(self.target(fight, ally))
                other = fight.vitals_of(self.target(fight, alternate))
                original.damage(100)
                other.damage(100)
                script = source.body_script
                cost = script.component.vars[338].value()
                script.set_ult_charge(cost)
                run.step({source: (*self.direction(fight, source, ally), 32)})
                instance = next(item for item in script.component.instances.values()
                                if item.graph.index == 0x0B9E)
                selected = instance.find_var(expr.INSTANCE, 8070, create=False)
                self.assertEqual(selected.value(), expr.Entity(ally.body))
                self.assertEqual(script.component.vars[342].value(), 0)
                self.assertEqual(original.total, 100)

                recipient = original
                if change == "target team":
                    ally.team = 1 - source.team
                elif change == "target body":
                    old_body = ally.body
                    position = tuple(ally.mover.position)
                    self.assertTrue(run.match.switch_hero(ally, ally.hero))
                    self.assertNotEqual(ally.body, old_body)
                    self.assertNotIn(old_body, fight.vitals)
                    ally.spawn = position, ally.spawn[1]
                    ally.mover = ally.new_mover()
                    recipient = fight.vitals_of(self.target(fight, ally))
                    recipient.damage(100)
                elif change == "cover":
                    set_static_wall(run, source, 2.5)
                elif change == "caster body":
                    self.assertTrue(run.match.switch_hero(source, source.hero))
                    self.assertIsNot(source.body_script, script)

                direction = self.direction(fight, source, alternate)
                for _ in range(18):
                    run.step({source: (*direction, 0)})

                released = change in ("aim", "cover")
                self.assertEqual(recipient.total, 200 if released else 100)
                self.assertEqual(recipient.nano_until > fight.now, released)
                self.assertEqual(original.total, 200 if released else 100)
                self.assertEqual(other.total, 100)
                self.assertEqual(other.nano_until, 0)
                self.assertEqual(source.stats.value(HEALING), 100 if released else 0)
                remaining_charge = source.body_script.component.vars[342].value()
                if change == "caster body":
                    self.assertLess(remaining_charge, cost)
                else:
                    self.assertEqual(remaining_charge, 0)

    def test_boosted_ana_rifle_damage_increases_but_healing_does_not(self):
        run, source, ally, enemy = self.fight(ally_hero="Ana")
        fight = run.match.combat
        source_vitals = fight.vitals_of(self.target(fight, source))
        source_vitals.damage(100)
        self.cast(run, source, ally)
        self.single_shot(run, ally, enemy, wait=60)
        self.assertEqual(fight.vitals_of(self.target(fight, enemy)).total, 95)
        self.single_shot(run, ally, source, wait=40)
        self.assertEqual(source_vitals.total, 170)

    def test_grenade_healing_amplification_applies_to_nano_heal(self):
        run, source, ally, _enemy = self.fight(ally_hero="Roadhog")
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(fight, ally))
        vitals.damage(550)
        direction = self.direction(fight, source, ally)
        run.step({source: (*direction, 16)})
        for _ in range(75):
            run.step({source: (*direction, 0)})
        self.assertEqual(vitals.total, 150)
        self.assertGreater(vitals.healing_boost_until, fight.now)
        self.cast(run, source, ally)
        self.assertEqual(vitals.total, 525)  # 250 *1.5 effective healing
        self.assertGreater(vitals.nano_until, fight.now)

    def test_enemy_grenade_blocks_nano_healing_but_not_damage_reduction(self):
        run, source, ally, enemy = self.fight(ally_hero="Roadhog", enemy_hero="Ana")
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(fight, ally))
        vitals.damage(400)
        direction = self.direction(fight, enemy, ally)
        run.step({enemy: (*direction, 16)})
        for _ in range(75):
            run.step({enemy: (*direction, 0)})
        self.assertEqual(vitals.total, 150)
        self.assertGreater(vitals.healing_block_until, fight.now)
        self.cast(run, source, ally)
        self.assertEqual(vitals.total, 150)
        self.assertGreater(vitals.nano_until, fight.now)
        self.single_shot(run, enemy, ally, wait=20)
        self.assertEqual(vitals.total, 115)  # actual enemy Ana70 reduced to35

    def test_caster_death_before_release_prevents_effect(self):
        run, source, ally, enemy = self.fight()
        fight = run.match.combat
        vitals = fight.vitals_of(self.target(fight, ally))
        vitals.damage(100)
        source.body_script.set_ult_charge(source.body_script.component.vars[338].value())
        run.step({source: (*self.direction(fight, source, ally), 32)})
        fight._damage(enemy, self.target(fight, source), 10000, False, None, run.frame)
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, 100)
        self.assertEqual(vitals.nano_until, 0)

    def test_uncharged_repeat_does_not_heal_and_new_charge_allows_next_cast(self):
        run, source, ally, _enemy = self.fight()
        fight = run.match.combat
        self.cast(run, source, ally)
        vitals = fight.vitals_of(self.target(fight, ally))
        first_deadline = vitals.nano_until
        vitals.damage(100)
        for _ in range(100):
            run.step({})
        self.cast(run, source, ally, charged=False)
        self.assertEqual(vitals.total, 100)
        self.assertEqual(source.body_script.component.vars[342].value(), 0)
        self.cast(run, source, ally)
        self.assertEqual(vitals.total, 200)
        self.assertGreater(vitals.nano_until, first_deadline)

    def test_target_and_effect_states_export_without_querying_live_world(self):
        run, source, ally, _enemy = self.fight()
        self.cast(run, source, ally)
        with patch.object(run.match.combat, "nano_targets",
                          side_effect=AssertionError("snapshot queried world")):
            first = snapshot_digest(export_body_vm(source.body_script))
            self.assertEqual(snapshot_digest(export_body_vm(source.body_script)), first)

    def test_actual_enemy_sleep_dart_interrupts_nano_before_release(self):
        run, source, ally, enemy = self.fight(enemy_hero="Ana")
        fight = run.match.combat
        ally_vitals = fight.vitals_of(self.target(fight, ally))
        ally_vitals.damage(100)
        dart_direction = self.direction(fight, enemy, source)
        run.step({enemy: (*dart_direction, 8)})
        # The dart is in flight before Nano starts; it lands inside the cast's
        # authored0.13s release window. These are real commands/projectile sweeps.
        for _ in range(28):
            run.step({enemy: (*dart_direction, 0)})
        self.cast(run, source, ally)
        self.assertTrue(fight.is_asleep(source))
        self.assertEqual(ally_vitals.total, 100)
        self.assertEqual(ally_vitals.nano_until, 0)


if __name__ == "__main__":
    unittest.main()
