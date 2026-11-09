"""Actual Zen/Reinhardt commands with external manifest-verified barrier query mesh."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_zenyatta_combat as weapons
from test_game_combat import aim, in_world

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes
from ow174.game.script import graph


@unittest.skipUnless(os.environ.get('ORPHEUS_BARRIER_DATA'), 'requires prepared external barrier mesh')
class ZenyattaBarrierTests(unittest.TestCase):
    def arena(self, *, yaw=-32768, third=False, friendly=False):
        run = weapons.Run(PRACTICE_RANGE, ('Zen', 'Reinhardt', 'Behind') if third else ('Zen', 'Reinhardt'))
        zen, rein = run.players[:2]
        run.match.bots.clear()
        run.match.switch_hero(zen, heroes()[weapons.ZENYATTA])
        record = next(v for v in graph.bodies().values() if v.get('name') == 'Reinhardt')
        run.match.switch_hero(rein, heroes()[int(record['hero'], 16)])
        zen.team, rein.team = 0, 0 if friendly else 1
        x, y, z = zen.mover.position
        rein.mover.position = [x, y, z+8]
        rein.mover.body = None
        if third:
            other = run.players[2]
            other.team = 1
            other.mover.position = [x, y, z+11]
            other.mover.body = None
        for _ in range(30):
            run.step({rein: (yaw, 0, SECONDARY)})
        self.assertTrue(run.match.combat.barriers.owners[rein.body].raised)
        return run, zen, rein

    def shield(self, run, rein):
        return run.match.combat.barriers.owners[rein.body]

    def health(self, run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target).total

    def test_friendly_barrier_intercepts_only_the_enemy_reflected_orb(self):
        genji_hero = 0x02E0000000000029
        for active in (False, True):
            with self.subTest(raised=active):
                run, zen, rein = self.arena(yaw=0, friendly=True)
                match = run.match
                zen.team = rein.team = 0
                genji = match.add_player(3, "Genji", genji_hero, 1, False)
                in_world(match, genji)
                self.assertTrue(match.switch_hero(genji, heroes()[genji_hero]))
                run.players.append(genji)
                x, y, z = zen.mover.position
                rein.mover.position = [x, y, z+3]
                genji.mover.position = [x, y, z+13]
                rein.mover.body = genji.mover.body = None
                fight = match.combat
                guard = {
                    genji: (*aim(fight._eye(genji), (x, y+1, z+3)), 16),
                    rein: (0, 0, SECONDARY if active else 0),
                }
                for _ in range(20):
                    run.step(guard)
                shield = self.shield(run, rein)
                self.assertEqual(shield.raised, active)
                shield_hp = shield.hp
                health = self.health(run, rein)
                direction = aim(fight._eye(zen), (x, y+1, z+13))
                run.step({**guard, zen: (*direction, FIRE)})
                orb, = fight.rockets
                for _ in range(35):
                    run.step(guard)
                    if orb not in fight.rockets:
                        break
                self.assertIs(orb.shooter, genji)
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(self.health(run, rein), health-(0 if active else 43))
                self.assertEqual(self.health(run, zen), 200)
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(shield.hp, shield_hp-(48 if active else 0))
                self.assertEqual(len(genji.client.messages(combat.HIT)), 0 if active else 1)
                self.assertFalse(zen.client.messages(combat.HIT))

    def test_enemy_shield_rejects_discord_acquisition_and_preserves_existing_orb(self):
        run, zen, rein = self.arena()
        raised = {rein: (-32768, 0, SECONDARY)}
        fight = run.match.combat
        run.step({**raised, zen: (0, 0, 8)})
        owner = fight.zenyatta.owners[zen.body]
        self.assertIsNone(owner.discord)
        self.assertNotIn('discord', owner.ready)
        for _ in range(10):
            run.step({})
        run.step({zen: (0, 0, 8)})
        orb = owner.discord
        self.assertEqual(orb.target, rein.body)
        for _ in range(205):
            run.step(raised)
        self.assertTrue(self.shield(run, rein).raised)
        self.assertIs(owner.discord, orb)
        for _ in range(10):
            run.step({})
        run.step({zen: (0, 600, FIRE)})
        for _ in range(25):
            run.step({})
        self.assertEqual(self.health(run, rein), 445)

    def test_harmony_acquires_and_keeps_healing_through_hostile_shield(self):
        run, zen, rein = self.arena(third=True)
        ally = run.players[2]
        ally.team = zen.team
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        vitals = fight.vitals_of(target)
        vitals.damage(100)
        raised = {rein: (-32768, 0, SECONDARY)}
        run.step({**raised, zen: (0, 0, 16)})
        owner = fight.zenyatta.owners[zen.body]
        self.assertEqual(owner.harmony.target, ally.body)
        for _ in range(205):
            run.step(raised)
        self.assertTrue(self.shield(run, rein).raised)
        self.assertEqual(owner.harmony.target, ally.body)
        self.assertGreater(vitals.total, 195)

    def test_hostile_shield_blocks_transcendence_until_lowered(self):
        run, zen, rein = self.arena(third=True)
        ally = run.players[2]
        ally.team = zen.team
        ally.mover.position[2] -= 1.5
        ally.mover.body = None
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        vitals = fight.vitals_of(target)
        vitals.damage(100)
        raised = {rein: (-32768, 0, SECONDARY)}
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({**raised, zen: (0, 0, 32)})
        for _ in range(20):
            run.step(raised)
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(vitals.total, 100)
        for _ in range(24):
            run.step({})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(vitals.total, 200)

    def test_friendly_shield_admits_discord_and_transcendence(self):
        for button in (8, 32):
            with self.subTest(button=button):
                run, zen, rein = self.arena(third=True, friendly=True)
                target_player = run.players[2]
                fight = run.match.combat
                if button == 32:
                    target_player.team = zen.team
                    target_player.mover.position[2] -= 1.5
                    target_player.mover.body = None
                    target = next(t for t in fight._targets() if t.owner is target_player)
                    fight.vitals_of(target).damage(100)
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                raised = {rein: (-32768, 0, SECONDARY)}
                run.step({**raised, zen: (0, 0, button)})
                for _ in range(24):
                    run.step(raised)
                self.assertTrue(self.shield(run, rein).raised)
                if button == 8:
                    self.assertEqual(fight.zenyatta.owners[zen.body].discord.target, target_player.body)
                else:
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                    self.assertEqual(self.health(run, target_player), 200)

    def test_primary_body_and_head_aim_damage_only_barrier_without_crit_or_ult_damage_charge(self):
        for pitch in (0, 600):
            with self.subTest(pitch=pitch):
                run, zen, rein = self.arena()
                before = zen.body_script.component.entity_var(342).value()
                frame = run.frame
                run.step({zen: (0, pitch, FIRE), rein: (-32768, 0, SECONDARY)})
                for _ in range(25):
                    run.step({rein: (-32768, 0, SECONDARY)})
                self.assertEqual(self.shield(run, rein).hp, 1552)
                self.assertEqual(self.health(run, rein), 500)
                self.assertFalse(zen.client.messages(combat.HIT))
                self.assertAlmostEqual(zen.body_script.component.entity_var(342).value()-before,
                                       (run.frame-frame)*combat.TICK*5, places=4)

    def test_full_burst_hits_five_times_and_consumes_two_hundred_forty_shield_hp(self):
        run, zen, rein = self.arena()
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY), rein: (-32768, 0, SECONDARY)})
        for _ in range(60):
            run.step({rein: (-32768, 0, SECONDARY)})
        self.assertEqual(self.shield(run, rein).hp, 1360)
        self.assertEqual(self.health(run, rein), 500)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_lowered_or_back_exposes_body_instead_of_damaging_shield(self):
        for back in (False, True):
            with self.subTest(back=back):
                run, zen, rein = self.arena(yaw=0 if back else -32768)
                buttons = SECONDARY if back else 0
                for _ in range(20):
                    run.step({rein: (0 if back else -32768, 0, buttons)})
                run.step({zen: (0, 600, FIRE), rein: (0 if back else -32768, 0, buttons)})
                for _ in range(20):
                    run.step({rein: (0 if back else -32768, 0, buttons)})
                self.assertEqual(self.health(run, rein), 457)  #48 through actual200 armour
                self.assertEqual(self.shield(run, rein).hp, 1600)

    def test_enemy_barrier_protects_body_behind_it_and_friendly_barrier_passes_shots(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, zen, rein = self.arena(third=True, friendly=friendly)
                other = run.players[2]
                if not friendly:
                    other.mover.position[0] += 2  # aim misses Rein capsule but still meets the mesh
                from test_game_combat import aim
                x, y, z = other.mover.position
                angles = aim(run.match.combat._eye(zen), (x, y+1, z))
                before = self.health(run, other)
                run.step({zen: (*angles, FIRE), rein: (-32768, 0, SECONDARY)})
                for _ in range(25):
                    run.step({rein: (-32768, 0, SECONDARY)})
                self.assertEqual(self.health(run, other), before-48 if friendly else before)
                self.assertEqual(self.shield(run, rein).hp, 1600 if friendly else 1552)

    def test_static_wall_wins_before_barrier(self):
        run, zen, rein = self.arena()
        x, y, z = zen.mover.position
        run.match.collision_world = World([
            (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
            (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
            (x-5, y-1, z+4, x+5, y-1, z+4, x+5, y+5, z+4),
            (x-5, y-1, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
        ])
        run.step({zen: (0, 600, FIRE), rein: (-32768, 0, SECONDARY)})
        for _ in range(25):
            run.step({rein: (-32768, 0, SECONDARY)})
        self.assertEqual(self.shield(run, rein).hp, 1600)
        self.assertEqual(self.health(run, rein), 500)

    def test_lowering_starts_two_second_recovery_delay_then_restores_at_two_hundred_per_second(self):
        run, zen, rein = self.arena()
        run.step({zen: (0, 600, FIRE), rein: (-32768, 0, SECONDARY)})
        for _ in range(160):
            run.step({rein: (-32768, 0, SECONDARY)})
        shield = self.shield(run, rein)
        self.assertEqual(shield.hp, 1552)
        run.step({rein: (-32768, 0, 0)})
        run.step({rein: (-32768, 0, 0)})
        self.assertFalse(shield.raised)
        instance = rein.body_script.component.instances[shield.instance]
        wait = instance.states[instance.graph.node(17).state]
        chase = instance.states[instance.graph.node(16).state]
        self.assertTrue(wait.active)
        for _ in range(130):
            run.step({rein: (-32768, 0, 0)})
            self.assertEqual(shield.hp, 1552)
            if chase.active:
                break
        self.assertTrue(chase.active)
        self.assertFalse(wait.active)
        self.assertEqual(chase.start - wait.start, 2000)
        run.step({rein: (-32768, 0, 0)})
        first_recovery = shield.hp
        self.assertGreater(first_recovery, 1552)
        self.assertLessEqual(first_recovery, 1552 + 200 * combat.TICK)
        for _ in range(10):
            run.step({rein: (-32768, 0, 0)})
        self.assertAlmostEqual(shield.hp, first_recovery + 10 * 200 * combat.TICK, places=2)
        for _ in range(30):
            run.step({rein: (-32768, 0, 0)})
        self.assertEqual(shield.hp, 1600)

    def test_actual_repeated_fire_breaks_barrier_without_overflow_then_body_takes_next_hit(self):
        run, zen, rein = self.arena()
        shield = self.shield(run, rein)
        for _ in range(2000):
            run.step({zen: (0, 600, FIRE), rein: (-32768, 0, SECONDARY)})
            if shield.hp == 0:
                break
        self.assertEqual(shield.hp, 0)
        self.assertEqual(self.health(run, rein), 500)
        self.assertFalse(zen.client.messages(combat.HIT))
        broken_at = run.match.combat.now
        self.assertAlmostEqual(shield.blocked_until-broken_at, 5)
        for _ in range(35):
            run.step({zen: (0, 600, FIRE), rein: (-32768, 0, SECONDARY)})
        self.assertLess(self.health(run, rein), 500)
        self.assertFalse(shield.raised)
        while run.match.combat.now < shield.blocked_until:
            run.step({rein: (-32768, 0, 0)})
        self.assertAlmostEqual(shield.hp, 600, delta=4)
        for _ in range(30):
            run.step({rein: (-32768, 0, SECONDARY)})
        self.assertTrue(shield.raised)

    def test_zen_melee_bypasses_barrier_and_replacement_removes_old_collider(self):
        run, zen, rein = self.arena()
        x, y, z = zen.mover.position
        rein.mover.position = [x, y, z+1.5]
        rein.mover.body = None
        run.step({zen: (0, 0, 0x800), rein: (-32768, 0, SECONDARY)})
        for _ in range(30):
            run.step({rein: (-32768, 0, SECONDARY)})
        self.assertEqual(self.health(run, rein), 475)
        self.assertEqual(self.shield(run, rein).hp, 1600)
        old = rein.body
        run.match.switch_hero(rein, heroes()[weapons.ZENYATTA])
        self.assertNotIn(old, run.match.combat.barriers.owners)
        rein.mover.position = [x, y, z+8]
        rein.mover.body = None
        for _ in range(50):
            run.step({})  # the attacking Zen still has its authored melee recovery
        run.step({zen: (0, 600, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, rein), 152)

    def test_flashbang_impact_damages_barrier_without_stunning_body_behind_mesh(self):
        run, caster, rein = self.arena()
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        x, y, z = rein.mover.position
        caster.mover.position = [x, y, z-3]
        caster.mover.body = None
        run.step({caster: (0, 600, 0x10), rein: (-32768, 0, SECONDARY)})
        for _ in range(60):
            run.step({rein: (-32768, 0, SECONDARY)})
            self.assertFalse(run.match.combat.is_stunned(rein))
        self.assertEqual(self.health(run, rein), 500)
        self.assertEqual(self.shield(run, rein).hp, 1575)

    def test_modified_or_missing_explicit_mesh_is_rejected(self):
        from ow174.game.barriers import load_mesh

        data = json.loads(Path(os.environ['ORPHEUS_BARRIER_DATA']).read_text(encoding='utf-8'))
        data['vertices'][0][0] += .01
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'modified.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            with patch.dict(os.environ, ORPHEUS_BARRIER_DATA=str(path)), self.assertRaises(ValueError):
                load_mesh()
            with (patch.dict(os.environ, ORPHEUS_BARRIER_DATA=str(Path(folder)/'missing.json')),
                  self.assertRaises(FileNotFoundError)):
                load_mesh()

    def test_enemy_soldier_hitscan_is_blocked_before_reaching_zen_behind_allied_barrier(self):
        from test_game_combat import aim

        run, shooter, rein = self.arena(third=True)
        zen = run.players[2]
        run.match.switch_hero(shooter, heroes()[SOLDIER])
        run.match.switch_hero(zen, heroes()[weapons.ZENYATTA])
        shooter.team, rein.team, zen.team = 0, 1, 1
        x, y, z = rein.mover.position
        shooter.mover.position = [x, y, z-8]
        shooter.mover.body = None
        zen.mover.position = [x+2, y, z+3]
        zen.mover.body = None
        angles = aim(run.match.combat._eye(shooter), (x+2, y+1, z+3))
        run.step({shooter: (*angles, FIRE), rein: (-32768, 0, SECONDARY)})
        for _ in range(10):
            run.step({rein: (-32768, 0, SECONDARY)})
        self.assertEqual(self.shield(run, rein).hp, 1580)
        self.assertEqual(self.health(run, zen), 200)
        self.assertFalse(shooter.client.messages(combat.HIT))


if __name__ == '__main__':
    unittest.main()
