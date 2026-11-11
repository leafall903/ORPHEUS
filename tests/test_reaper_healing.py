"""Resource-backed healing arithmetic under declared world/target reconstruction.

These tests do not establish native filter flags, alternate modes, activation
restoration, healing boosts/antiheal or native healing-charge message behavior.
"""

import unittest

import test_game_reaper as reaper_tests

from ow174.game import combat, defenses, reaper, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class ReaperHealingTests(unittest.TestCase):
    def scene(self, hero, *, team_mode=True):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario(
            team_mode=team_mode, names=("Reaper", "Healer", "Enemy"),
        )
        enemy = run.players[2]
        run.match.switch_hero(caster, heroes()[hero])
        owner.team = caster.team = 0
        enemy.team = 1
        x, y, z = owner.mover.position
        caster.mover.position[:] = [x, y, z + 1.5]
        enemy.mover.position[:] = [x + 3, y, z]
        caster.mover.body = enemy.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        for actor in run.players:
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        for target in fight._targets():
            fight.vitals_of(target)
        return run, owner, caster, enemy

    def target(self, run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)

    def hurt(self, run, actor, source, amount):
        fight = run.match.combat
        fight._damage(source, self.target(run, actor), amount, False, None, run.frame)

    def health(self, run, actor):
        return run.match.combat.vitals[actor.body].total

    def cast(self, run, caster):
        zen = caster.hero.guid == defenses.ZENYATTA
        if zen:
            caster.body_script.set_ult_charge(caster.body_script.component.vars[338].value())
        run.step({caster: (0, 0, 32 if zen else 8)})
        self.assertTrue(defenses.invulnerable(caster))
        return run.match.combat.defense_heals[caster.body]

    def world(self, run, ticks, *, skip=False):
        for _ in range(1 if skip else ticks):
            run.frame += ticks if skip else 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)

    def test_mei_heals_actual_enemy_shotgun_damage_with_no_reaping_or_charge_feedback(self):
        run, owner, caster, enemy = self.scene(defenses.MEI)
        caster.team = 1
        self.hurt(run, owner, enemy, 100)
        reaper_tests.ReaperTests().fire(run, owner, caster)
        self.assertEqual(self.health(run, caster), 130)
        self.assertAlmostEqual(self.health(run, owner), 150 + 120 * reaper.REAPING_RATIO)
        record = self.cast(run, caster)
        self.assertAlmostEqual(record["duration"], 3.9, places=5)
        self.assertAlmostEqual(record["rate"] * record["duration"], 200)
        self.world(run, 63)
        given = record["rate"] * (63 * combat.TICK)
        self.assertAlmostEqual(self.health(run, caster), 130 + given)
        self.assertAlmostEqual(caster.stats.value(stats.HEALING), given)
        self.assertAlmostEqual(self.health(run, owner), 150 + 120 * reaper.REAPING_RATIO)
        self.assertEqual(owner.body_script.component.vars[342].value(), 120)
        self.world(run, 250)
        self.assertEqual(self.health(run, caster), 250)
        self.assertAlmostEqual(caster.stats.value(stats.HEALING), 120)
        self.assertFalse(defenses.invulnerable(caster))
        self.hurt(run, caster, owner, 30)
        self.world(run, 125)
        self.assertEqual(self.health(run, caster), 220)
        self.assertAlmostEqual(caster.stats.value(stats.HEALING), 120)

    def test_zen_heals_reaper_and_self_at_authored_rate_caps_and_syncs_health(self):
        run, owner, caster, enemy = self.scene(defenses.ZENYATTA)
        self.hurt(run, owner, enemy, 200)
        self.hurt(run, caster, enemy, 100)
        self.hurt(run, enemy, caster, 100)
        record = self.cast(run, caster)
        self.assertEqual(record["rate"], 300)
        self.assertEqual(record["duration"], 6)
        self.assertEqual(record["radius"], 10)
        self.assertEqual(self.health(run, owner), 50)
        self.world(run, 30)
        self.assertAlmostEqual(self.health(run, owner), 194)
        self.assertEqual(self.health(run, caster), 200)
        self.assertEqual(self.health(run, enemy), 100)
        self.assertAlmostEqual(caster.stats.value(stats.HEALING), 244)
        self.assertAlmostEqual(owner.body_script.component.vars[253].value(), 194)
        self.world(run, 30)
        self.assertEqual(self.health(run, owner), 250)
        self.assertAlmostEqual(caster.stats.value(stats.HEALING), 300)
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)
        self.assertEqual(caster.body_script.component.vars[342].value(), 0)

    def test_mei_self_only_and_zen_ffa_do_not_heal_other_players(self):
        for hero, team_mode in ((defenses.MEI, True), (defenses.ZENYATTA, False)):
            with self.subTest(hero=hero, team_mode=team_mode):
                run, owner, caster, enemy = self.scene(hero, team_mode=team_mode)
                self.hurt(run, owner, enemy, 100)
                self.cast(run, caster)
                self.world(run, 100)
                self.assertEqual(self.health(run, owner), 150)
                self.assertEqual(caster.stats.value(stats.HEALING), 0)

    def test_zen_range_exit_reentry_never_banks_healing_for_a_new_target(self):
        run, owner, caster, enemy = self.scene(defenses.ZENYATTA)
        self.hurt(run, owner, enemy, 200)
        x, y, z = owner.mover.position
        caster.mover.position[:] = [x, y, z + 20]
        caster.mover.body = None
        self.cast(run, caster)
        self.world(run, 60)
        self.assertEqual(self.health(run, owner), 50)
        caster.mover.position[:] = [x, y, z + 3]
        caster.mover.body = None
        self.world(run, 10)
        self.assertAlmostEqual(self.health(run, owner), 98)
        caster.mover.position[:] = [x, y, z + 20]
        caster.mover.body = None
        self.world(run, 60)
        self.assertAlmostEqual(self.health(run, owner), 98)
        caster.mover.position[:] = [x, y, z + 3]
        caster.mover.body = None
        self.world(run, 10)
        self.assertAlmostEqual(self.health(run, owner), 146)

    def test_zen_static_wall_blocks_actual_friendly_healing(self):
        run, owner, caster, enemy = self.scene(defenses.ZENYATTA)
        self.hurt(run, owner, enemy, 200)
        x, floor, z = owner.mover.position
        wall = z + 0.75
        run.match.collision_world = World([
            (x - 20, floor, z - 20, x + 20, floor, z + 20, x + 20, floor, z - 20),
            (x - 20, floor, z - 20, x - 20, floor, z + 20, x + 20, floor, z + 20),
            (x - 5, floor, wall, x + 5, floor + 5, wall, x - 5, floor + 5, wall),
            (x - 5, floor, wall, x + 5, floor, wall, x + 5, floor + 5, wall),
            (x - 5, floor, wall, x - 5, floor + 5, wall, x + 5, floor + 5, wall),
            (x - 5, floor, wall, x + 5, floor + 5, wall, x + 5, floor, wall),
        ], name="Transcendence static healing occluder")
        for actor in run.players:
            actor.mover.use(world=run.match.collision_world)
        self.cast(run, caster)
        self.world(run, 100)
        self.assertEqual(self.health(run, owner), 50)
        self.assertEqual(caster.stats.value(stats.HEALING), 0)

    def test_wraith_reaper_can_receive_friendly_healing(self):
        run, owner, caster, enemy = self.scene(defenses.ZENYATTA)
        self.hurt(run, owner, enemy, 200)
        self.cast(run, caster)
        run.step({owner: (0, 0, 8)})
        before = self.health(run, owner)
        self.assertTrue(reaper.intangible(owner))
        self.world(run, 30)
        self.assertTrue(reaper.intangible(owner))
        self.assertAlmostEqual(self.health(run, owner), before + 144)

    def test_cancelled_mei_healing_stops_and_a_new_cast_has_an_independent_interval(self):
        run, _, caster, enemy = self.scene(defenses.MEI)
        self.hurt(run, caster, enemy, 200)
        record = self.cast(run, caster)
        self.world(run, 63)
        run.step({caster: (0, 0, 1)})
        self.assertFalse(defenses.invulnerable(caster))
        stopped = self.health(run, caster)
        self.assertLess(stopped, 110)
        self.assertTrue(record["ended"])
        self.world(run, 800, skip=True)
        self.assertAlmostEqual(self.health(run, caster), stopped)
        new = self.cast(run, caster)
        self.assertNotEqual(new["activation"], record["activation"])
        self.world(run, 260, skip=True)
        self.assertEqual(self.health(run, caster), 250)
        self.assertAlmostEqual(caster.stats.value(stats.HEALING), 200)

    def test_dead_target_never_revives_and_caster_retirement_discards_the_interval(self):
        run, owner, caster, enemy = self.scene(defenses.ZENYATTA)
        self.cast(run, caster)
        body = owner.body
        self.hurt(run, owner, enemy, 1000)
        self.world(run, 125)
        self.assertEqual(run.match.combat.vitals[body].total, 0)
        self.assertEqual(caster.stats.value(stats.HEALING), 0)
        old_body = caster.body
        run.match.switch_hero(caster, heroes()[SOLDIER])
        self.assertNotIn(old_body, run.match.combat.defense_heals)

    def test_no_input_contiguous_and_skipped_world_windows_integrate_the_same_totals(self):
        for hero, total in ((defenses.MEI, 200), (defenses.ZENYATTA, 1800)):
            values = []
            for skip in (False, True):
                with self.subTest(hero=hero, skip=skip):
                    run, owner, caster, _ = self.scene(hero)
                    target = caster if hero == defenses.MEI else owner
                    fight = run.match.combat
                    # Declared enlarged pool isolates amount/duration arithmetic from the normal cap.
                    fight.vitals[target.body] = combat.Vitals(target.body, 2000)
                    fight.vitals[target.body].damage(1900)
                    fight._sync_body_health(target, fight.vitals[target.body])
                    record = self.cast(run, caster)
                    self.world(run, 1000, skip=skip)
                    values.append(self.health(run, target))
                    self.assertAlmostEqual(values[-1], 100 + total, places=5)
                    self.assertAlmostEqual(caster.stats.value(stats.HEALING), total, places=5)
                    self.assertTrue(record["ended"])
                    self.assertAlmostEqual(record["last"], record["end"])
            self.assertAlmostEqual(values[0], values[1], places=5)

    def test_duplicate_world_tick_and_late_input_cannot_heal_twice(self):
        run, owner, caster, enemy = self.scene(defenses.ZENYATTA)
        self.hurt(run, owner, enemy, 200)
        self.cast(run, caster)
        old_input = caster.commands.last_frame
        self.world(run, 10)
        health, healing = self.health(run, owner), caster.stats.value(stats.HEALING)
        run.match.combat.update(run.frame * combat.TICK, run.frame, frames=False)
        caster.take_commands([Command(old_input + 1, buttons=32)])
        self.assertEqual(self.health(run, owner), health)
        self.assertEqual(caster.stats.value(stats.HEALING), healing)
        self.world(run, 1000, skip=True)
        record = run.match.combat.defense_heals[caster.body]
        terminal = record["ended"], record["last"], record["end"]
        run.match.combat.update((run.frame - 1000) * combat.TICK, run.frame - 1000, frames=False)
        self.assertEqual((record["ended"], record["last"], record["end"]), terminal)

    def test_blossom_lethal_pulse_precedes_healing_independently_of_player_iteration_order(self):
        for healer_first in (False, True):
            with self.subTest(healer_first=healer_first):
                run, owner, caster, attacker = self.scene(defenses.ZENYATTA)
                run.match.switch_hero(attacker, heroes()[reaper.HERO])
                attacker.team = 1
                x, y, z = owner.mover.position
                attacker.mover.position[:] = [x, y, z + 20]
                attacker.mover.body = None
                self.cast(run, caster)
                attacker.body_script.set_ult_charge(2100)
                run.step({attacker: (0, 0, 32)})
                self.assertIsNotNone(reaper.blossom_state(attacker.body_script))
                self.hurt(run, owner, attacker, 220)
                self.assertEqual(self.health(run, owner), 30)
                owner.mover.position[:] = [x, y, z + 18.5]
                caster.mover.position[:] = [x, y, z + 17]
                owner.mover.body = caster.mover.body = None
                run.match.players[:] = ([caster, owner, attacker] if healer_first
                                        else [attacker, owner, caster])
                self.world(run, 1)
                self.assertTrue(run.match.combat.is_dead(owner))
                self.assertEqual(self.health(run, owner), 0)


if __name__ == "__main__":
    unittest.main()
