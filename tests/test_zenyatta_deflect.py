"""Actual Genji input/state and Zenyatta projectiles, with reconstructed deflection hosting."""

import unittest
from dataclasses import replace

import test_zenyatta_combat as weapon_tests
from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.flashbang import HERO as MCCREE

GENJI = 0x02E0000000000029


class ZenyattaDeflectTests(unittest.TestCase):
    def test_reflected_flashbang_retains_gravity_and_respects_transcendence(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                self.check_reflected_flashbang(ultimate)

    def check_reflected_flashbang(self, ultimate):
        run = Run(names=("McCree", "Genji", "Zen"))
        caster, genji, zen = run.players
        run.match.bots.clear()
        x, y, z = caster.mover.position
        for player, hero, team, offset in (
            (caster, MCCREE, 0, (0, 0)),
            (genji, 0x02E0000000000029, 1, (0, 3)),
            (zen, weapon_tests.ZENYATTA, 0, (1, 0)),
        ):
            run.match.switch_hero(player, heroes()[hero])
            player.team = team
            player.mover.position = [x+offset[0], y, z+offset[1]]
            player.mover.body = None
        guard = {genji: (-32768, 0, 0x10)}
        for _ in range(20):
            run.step(guard)
        self.assertTrue(self.active(genji))
        fight = run.match.combat
        if ultimate:
            zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
            run.step({**guard, zen: (0, 0, 0x20)})
            self.assertTrue(fight.zenyatta.ultimate(zen))
        run.step({**guard, caster: (0, 0, 16)})
        rocket, = fight.rockets
        self.assertGreater(rocket.velocity[2], 0)
        for _ in range(10):
            if rocket.shooter is genji:
                break
            run.step(guard)
        self.assertIs(rocket.shooter, genji)
        self.assertLess(rocket.velocity[2], 0)
        self.assertEqual(rocket.gravity, 20)
        vertical = rocket.velocity[1]
        run.step(guard)
        self.assertLess(rocket.velocity[1], vertical)
        for _ in range(20):
            run.step(guard)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, zen), 200 if ultimate else 175)
        self.assertEqual(fight.is_stunned(zen), not ultimate)
        self.assertEqual(fight.zenyatta.ultimate(zen), ultimate)
        self.assertFalse(caster.client.messages(combat.HIT))
        targets = [hit["+0x78"]["+0x0"]["+0x0"] for hit in genji.client.messages(combat.HIT)]
        self.assertIn(caster.body, targets)
        self.assertEqual(zen.body in targets, not ultimate)

    def arena(self, yaw=-32768):
        run, zen, genji, _ = weapon_tests.ZenyattaCombatTests().arena()
        run.match.switch_hero(genji, heroes()[0x02E0000000000029])
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z+8]
        genji.mover.body = None
        for _ in range(10):
            run.step({genji: (yaw, 0, 0x10)})
        self.assertTrue(self.active(genji))
        return run, zen, genji

    def active(self, genji):
        return any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                   for i in genji.body_script.component.instances.values() for s in i.states.values())

    def health(self, run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target).total

    def test_front_deflect_returns_orb_and_assigns_real_damage_and_hit_notice_to_genji(self):
        run, zen, genji = self.arena()
        run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
        for _ in range(30):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, zen), 104)
        self.assertFalse(zen.client.messages(combat.HIT))
        hit = genji.client.messages(combat.HIT)[0]["+0x78"]
        self.assertEqual(hit["+0x0"]["+0x0"], zen.body)
        self.assertEqual(hit["+0x4"], 96)
        self.assertEqual(hit["+0x8"], combat.CRITICAL)

    def test_last_deflector_owns_real_hit(self):
        run = Run(names=("Zen", "Enemy Genji", "Ally Genji", "Enemy Soldier"))
        zen, first, second, target = run.players
        run.match.bots.clear()
        x, y, z = zen.mover.position
        for actor, hero, team, offset in (
            (zen, weapon_tests.ZENYATTA, 0, (0, 0)),
            (first, GENJI, 1, (0, 8)),
            (second, GENJI, 0, (4, 4)),
            (target, target.hero.guid, 1, (1.2, 10)),
        ):
            self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
            actor.team = team
            actor.mover.position = [x+offset[0], y, z+offset[1]]
            actor.mover.body = None
        fight = run.match.combat
        # The second reflection starts at contact, offset from the aiming hero.
        guard = {
            first: (*aim(fight._eye(first), fight._eye(second)), 16),
            second: (*aim(fight._eye(second), (x+2, y+1, z+10)), 16),
        }
        for _ in range(20):
            run.step(guard)
        self.assertTrue(self.active(first))
        self.assertTrue(self.active(second))
        run.step({**guard, zen: (0, 0, FIRE)})
        orb, = fight.rockets
        owners = [orb.shooter]
        for _ in range(40):
            run.step(guard)
            if orb.shooter is not owners[-1]:
                owners.append(orb.shooter)
            if orb not in fight.rockets:
                break
        self.assertEqual(owners, [zen, first, second])
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertFalse(first.client.messages(combat.HIT))
        notice, = second.client.messages(combat.HIT)
        self.assertEqual(notice["+0x78"]["+0x0"]["+0x0"], target.body)
        self.assertEqual(notice["+0x78"]["+0x4"], 48)
        self.assertEqual(self.health(run, target), 152)
        for actor in (zen, first, second):
            self.assertEqual(self.health(run, actor), 200)

    def test_deflect_does_not_protect_the_back(self):
        run, zen, genji = self.arena(yaw=0)
        run.step({zen: (0, 600, FIRE), genji: (0, 0, 0x10)})
        for _ in range(20):
            run.step({genji: (0, 0, 0x10)})
        self.assertEqual(self.health(run, genji), 152)
        self.assertEqual(self.health(run, zen), 200)

    def test_native_deflect_end_restores_normal_orb_hits(self):
        run, zen, genji = self.arena()
        for _ in range(150):
            run.step({genji: (-32768, 0, 0)})
        self.assertFalse(self.active(genji))
        run.step({zen: (0, 600, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, genji), 152)
        self.assertEqual(self.health(run, zen), 200)

    def test_wall_intercepts_before_deflect(self):
        run, zen, genji = self.arena()
        x, y, z = zen.mover.position
        run.match.collision_world = World([
            (x-5, y-1, z+4, x+5, y-1, z+4, x+5, y+5, z+4),
            (x-5, y-1, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
        ])
        run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
        for _ in range(20):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, zen), 200)
        self.assertEqual(self.health(run, genji), 200)
        self.assertFalse(run.match.combat.rockets)

    def test_reflected_orb_cannot_damage_transcendent_zen(self):
        run, zen, genji = self.arena()
        run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20), genji: (-32768, 0, 0x10)})
        for _ in range(30):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, zen), 200)
        self.assertEqual(self.health(run, genji), 200)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertFalse(run.match.combat.rockets)

    def test_skipped_world_frames_preserve_deflection_residual_travel(self):
        health = []
        for advances in (range(1, 21), (20,)):
            run, zen, genji = self.arena()
            run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
            run.step({genji: (-32768, 0, 0x10)})  # admit the player's projectile into the world clock
            for advance in advances:
                tick = run.frame + advance
                run.match.update(tick*combat.TICK, tick)
            health.append(self.health(run, zen))
            self.assertEqual(self.health(run, genji), 200)
            self.assertFalse(run.match.combat.rockets)
        self.assertEqual(health, [104, 104])

    def test_discord_targeting_is_not_itself_reflected(self):
        run, zen, genji = self.arena()
        run.step({zen: (0, 0, 0x08), genji: (-32768, 0, 0x10)})
        owner = run.match.combat.zenyatta.owners[zen.body]
        self.assertEqual(owner.discord.target, genji.body)
        self.assertEqual(self.health(run, zen), 200)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_charged_reflected_burst_kills_zen_with_genji_credit_and_cleans_up(self):
        run, zen, genji, _ = weapon_tests.ZenyattaCombatTests().arena()
        run.match.switch_hero(genji, heroes()[0x02E0000000000029])
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z+8]
        genji.mover.body = None
        run.step({zen: (0, 0, 0x08), genji: (-32768, 0, 0)})
        for i in range(140):
            run.step({zen: (0, 0, 0x02), genji: (-32768, 0, 0x10 if i >= 130 else 0)})
        self.assertTrue(self.active(genji))
        run.step({zen: (0, 0, 0), genji: (-32768, 0, 0x10)})
        for _ in range(150):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, zen), 0)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in genji.client.messages(combat.HIT)], [96, 96, 8])
        self.assertEqual(len(genji.client.messages(combat.KILL_NOTICE)), 1)
        self.assertNotIn(zen.body, run.match.combat.zenyatta.owners)
        self.assertFalse(run.match.combat.rockets)

    def test_actual_front_deflect_blocks_quick_melee_without_return_damage_or_charge(self):
        run, zen, genji = self.arena()
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z+1.5]
        genji.mover.body = None
        before = zen.body_script.component.entity_var(342).value()
        frame = run.frame
        with self.assertNoLogs('ow174.game', level='ERROR'):
            run.step({zen: (0, 0, 0x800), genji: (-32768, 0, 0x10)})
            for _ in range(30):
                run.step({genji: (-32768, 0, 0x10)})
        self.assertTrue(self.active(genji))
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, zen), 200)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertAlmostEqual(zen.body_script.component.entity_var(342).value()-before,
                               (run.frame-frame)*combat.TICK*5, places=4)

    def test_actual_back_or_expired_deflect_allows_quick_melee_damage(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                yaw = -32768 if expired else 0
                run, zen, genji = self.arena(yaw=yaw)
                if expired:
                    for _ in range(150):
                        run.step({genji: (yaw, 0, 0)})
                    self.assertFalse(self.active(genji))
                x, y, z = zen.mover.position
                genji.mover.position = [x, y, z+1.5]
                genji.mover.body = None
                run.step({zen: (0, 0, 0x800), genji: (yaw, 0, 0)})
                for _ in range(30):
                    run.step({genji: (yaw, 0, 0)})
                self.assertEqual(self.health(run, genji), 170)
                self.assertEqual(self.health(run, zen), 200)
                self.assertEqual(len(zen.client.messages(combat.HIT)), 1)

    def test_actual_melee_filter_uses_positive_front_dot_instead_of_projectile_hand_boxes(self):
        for degrees, health in ((80, 200), (90, 170), (100, 170)):
            with self.subTest(degrees=degrees):
                yaw = -32768+round(degrees*65536/360)
                run, zen, genji = self.arena(yaw=yaw)
                x, y, z = zen.mover.position
                genji.mover.position = [x, y, z+1.5]
                genji.mover.body = None
                run.step({zen: (0, 0, 0x800), genji: (yaw, 0, 0x10)})
                for _ in range(30):
                    run.step({genji: (yaw, 0, 0x10)})
                self.assertEqual(self.health(run, genji), health)

    def test_actual_melee_filter_does_not_change_team_range_or_static_wall_rules(self):
        for case in ('ally', 'ffa', 'range', 'wall'):
            with self.subTest(case=case):
                run, zen, genji = self.arena(yaw=0)  # exposed back, so the filter cannot mask a hit
                x, y, z = zen.mover.position
                genji.mover.position = [x, y, z+(5 if case == 'range' else 1.5)]
                genji.mover.body = None
                if case in ('ally', 'ffa'):
                    genji.team = zen.team
                if case == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                if case == 'wall':
                    run.match.collision_world = World([
                        (x-5, y-1, z+.75, x+5, y-1, z+.75, x+5, y+5, z+.75),
                        (x-5, y-1, z+.75, x+5, y+5, z+.75, x-5, y+5, z+.75),
                    ])
                run.step({zen: (0, 0, 0x800), genji: (0, 0, 0x10)})
                for _ in range(30):
                    run.step({genji: (0, 0, 0x10)})
                self.assertEqual(self.health(run, genji), 170 if case == 'ffa' else 200)
                self.assertEqual(len(zen.client.messages(combat.HIT)), 1 if case == 'ffa' else 0)

    def test_body_replacement_cannot_keep_old_melee_guard(self):
        run, zen, genji = self.arena()
        old = genji.body
        run.match.switch_hero(genji, genji.hero)
        self.assertNotEqual(genji.body, old)
        self.assertFalse(self.active(genji))
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z+1.5]
        genji.mover.body = None
        run.step({zen: (0, 0, 0x800), genji: (-32768, 0, 0)})
        for _ in range(30):
            run.step({genji: (-32768, 0, 0)})
        self.assertEqual(self.health(run, genji), 170)


if __name__ == "__main__":
    unittest.main()
