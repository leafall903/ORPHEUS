"""Bidirectional Pharah and Brigitte combat through the authored input paths."""

import unittest
from dataclasses import replace

import test_pharah_defenses as defense
from test_game_combat import aim

from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes


class PharahBrigitteTests(unittest.TestCase):
    health = defense.PharahDefenseTests.health

    def arena(self, distance=3):
        run, pharah, brigitte, third = defense.PharahDefenseTests().encounter("Brigitte")
        brigitte.mover.position[2] = distance
        third.mover.position[0] = 30
        for player in (brigitte, third):
            player.mover.body = None
        return run, pharah, brigitte

    def raise_shield(self, run, brigitte, frames=5):
        for _ in range(frames):
            run.step({brigitte: (-32768, 0, SECONDARY)})
        barrier = run.match.combat.brigitte_barriers[brigitte.body]
        self.assertTrue(barrier.raised())
        return barrier

    def bash(self, run, pharah, brigitte):
        run.step({brigitte: (-32768, 0, SECONDARY | FIRE)})
        for _ in range(14):
            if run.match.combat.is_stunned(pharah):
                return
            run.step({brigitte: (-32768, 0, SECONDARY), pharah: (-32768, 0, 0)})

    def test_actual_bash_interrupts_barrage_and_jet_recovers_on_expiry(self):
        run, pharah, brigitte = self.arena()
        fight = run.match.combat
        self.raise_shield(run, brigitte)
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({brigitte: (-32768, 0, SECONDARY), pharah: (-32768, 0, 32)})
        self.assertTrue(script.ultimate_running())
        self.bash(run, pharah, brigitte)
        self.assertEqual(self.health(run, pharah).total, 195)
        self.assertEqual(brigitte.body_script.component.entity_var(342).value(), 5)
        deadline = fight.stuns[pharah.body]
        self.assertEqual(deadline - fight.tick, 47)
        self.assertFalse(script.ultimate_running())
        for _ in range(8):
            run.step({brigitte: (-32768, 0, SECONDARY), pharah: (-32768, 0, 8 | 32)})
        self.assertLess(pharah.mover.position[2], -0.1)
        self.assertLess(pharah.mover.velocity[1], 10)
        while run.frame < deadline - 1:
            run.step({brigitte: (-32768, 0, SECONDARY), pharah: (-32768, 0, 8 | 32)})
        self.assertTrue(fight.is_stunned(pharah))
        run.step({brigitte: (-32768, 0, SECONDARY), pharah: (-32768, 0, 8)})
        self.assertFalse(fight.is_stunned(pharah))
        self.assertEqual(pharah.mover.velocity[1], 20)
        self.assertFalse(script.ultimate_running())
        self.assertEqual(self.health(run, pharah).total, 195)

    def test_shield_blocks_pharah_primary_and_concussion_until_lowered(self):
        run, pharah, brigitte = self.arena(distance=8)
        fight = run.match.combat
        barrier = self.raise_shield(run, brigitte)
        base = self.health(run, brigitte).total
        for buttons, shield_hp in ((FIRE, 130), (16, 130)):
            run.step({brigitte: (-32768, 0, SECONDARY), pharah: (0, 0, buttons)})
            self.raise_shield(run, brigitte, frames=25)
            self.assertEqual(barrier.health, shield_hp)
            self.assertEqual(self.health(run, brigitte).total, base)
            self.assertEqual(brigitte.mover.position[2], 8)
            self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)
        for _ in range(15):
            run.step({})
        self.assertFalse(barrier.raised())
        self.fire_primary(run, pharah)
        self.assertLess(self.health(run, brigitte).total, base)
        self.assertIs(fight.brigitte_barriers[brigitte.body], barrier)

    @staticmethod
    def fire_primary(run, pharah):
        run.step({pharah: (0, 0, FIRE)})
        for _ in range(25):
            run.step({})

    def test_bash_dashes_on_miss_and_rejects_the_same_body_when_allied(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, pharah, brigitte = self.arena(distance=11 if not ally else 3)
                if ally:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    pharah.team = brigitte.team = 0
                self.raise_shield(run, brigitte)
                start = brigitte.mover.position[2]
                self.bash(run, pharah, brigitte)
                self.assertLess(brigitte.mover.position[2], start - 2)
                self.assertEqual(self.health(run, pharah).total, 200)
                self.assertNotIn(pharah.body, run.match.combat.stuns)

    def test_winston_barrier_stops_bash_travel_before_it_reaches_pharah(self):
        run, pharah, brigitte = self.arena(distance=7)
        winston = run.players[2]
        self.assertTrue(run.match.switch_hero(winston, heroes()[0x02E0000000000009]))
        winston.mover.position = [1, pharah.mover.position[1], 0]
        winston.mover.body = None
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = winston.team = 0
        brigitte.team = 1
        run.step({winston: (0, 0, 16)})
        barrier = self.raise_shield(run, brigitte)
        fight = run.match.combat
        self.assertIn(winston.body, fight.winston_bubbles)
        self.bash(run, pharah, brigitte)
        self.assertGreater(brigitte.mover.position[2], 5)
        self.assertLess(brigitte.mover.position[2], 7)
        self.assertFalse(barrier.instance.states[14].active)
        self.assertEqual(self.health(run, pharah).total, 200)

    def test_primary_break_preserves_body_and_raised_input_recovers_after_lock(self):
        run, pharah, brigitte = self.arena(distance=8)
        barrier = self.raise_shield(run, brigitte)
        base = self.health(run, brigitte).total
        for expected in (130, 10, 1):
            run.step({pharah: (0, 0, FIRE), brigitte: (-32768, 0, SECONDARY)})
            for _ in range(55):
                run.step({brigitte: (-32768, 0, SECONDARY)})
            self.assertEqual(barrier.health, expected)
        self.assertEqual(self.health(run, brigitte).total, base)
        self.assertFalse(barrier.raised())
        self.assertGreater(barrier.instance.vars[61].value(), 0)
        for _ in range(300):
            run.step({})
        self.assertEqual(barrier.health, 250)
        self.assertEqual(barrier.instance.vars[61].value(), 0)
        self.raise_shield(run, brigitte)

    def test_bash_cooldown_rejects_fresh_press_then_allows_recast(self):
        run, pharah, brigitte = self.arena()
        barrier = self.raise_shield(run, brigitte)
        self.bash(run, pharah, brigitte)
        self.assertEqual(self.health(run, pharah).total, 195)
        cooldown = barrier.instance.vars[13062]
        for _ in range(10):
            run.step({brigitte: (-32768, 0, SECONDARY)})
        self.assertGreater(cooldown.value(), 6.8)
        brigitte.mover.position = [0, pharah.mover.position[1], pharah.mover.position[2] + 2]
        brigitte.mover.body = None
        run.step({brigitte: (-32768, 0, SECONDARY | FIRE)})
        for _ in range(445):
            run.step({brigitte: (-32768, 0, SECONDARY)})
            if cooldown.value() <= 0:
                break
        self.assertEqual(self.health(run, pharah).total, 195)
        self.assertEqual(cooldown.value(), 0)
        brigitte.mover.position = [0, pharah.mover.position[1], pharah.mover.position[2] + 2]
        brigitte.mover.body = None
        self.bash(run, pharah, brigitte)
        self.assertEqual(self.health(run, pharah).total, 190)

    def test_barrage_first_impacts_spend_shield_before_the_body(self):
        run, pharah, brigitte = self.arena(distance=8)
        barrier = self.raise_shield(run, brigitte)
        before = self.health(run, brigitte).total
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (0, 0, 32), brigitte: (-32768, 0, SECONDARY)})
        for _ in range(30):
            run.step({brigitte: (-32768, 0, SECONDARY)})
            if barrier.health < 250:
                break
        self.assertGreater(barrier.health, 1)
        self.assertLess(barrier.health, 250)
        self.assertEqual(self.health(run, brigitte).total, before)

    def test_live_raised_shield_health_is_causal_in_passive_snapshot(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene()
        player, = players
        self.assertTrue(session.apply("switch_hero", {
            "account": player.account_lo, "hero": 0x02E0000000000195,
        }))
        peers[0].commands(player.body_script.frame + 1, 5, SECONDARY)
        barrier = player.match.combat.brigitte_barriers[player.body]
        self.assertTrue(barrier.raised())
        before = stored.snapshot_digest(case.capture(session))
        barrier.damage(30)
        self.assertNotEqual(stored.snapshot_digest(case.capture(session)), before)

    def test_nearby_ground_blast_debits_the_visible_shield_edge(self):
        run, pharah, brigitte = self.arena(distance=8)
        fight = run.match.combat
        pharah.mover.position[0] = 1.25
        pharah.mover.body = None
        barrier = self.raise_shield(run, brigitte)
        before = self.health(run, brigitte).total
        y = pharah.mover.position[1]
        yaw, pitch = aim(fight._eye(pharah), (1.25, y, 7))
        run.step({pharah: (yaw, pitch, FIRE), brigitte: (-32768, 0, SECONDARY)})
        self.raise_shield(run, brigitte, frames=25)
        self.assertLess(barrier.health, 250)
        self.assertGreater(barrier.health, 130)
        self.assertEqual(self.health(run, brigitte).total, before)

    def test_quick_melee_crosses_brigitte_shield_and_respects_armor(self):
        run, pharah, brigitte = self.arena(distance=1.5)
        barrier = self.raise_shield(run, brigitte)
        before = self.health(run, brigitte).total
        run.step({pharah: (0, 0, 0x800), brigitte: (-32768, 0, SECONDARY)})
        self.raise_shield(run, brigitte, frames=30)
        self.assertEqual(barrier.health, 250)
        self.assertEqual(self.health(run, brigitte).total, before - 25)

    def test_primary_brackets_attached_shield_edge_outside_the_dynamic_hull(self):
        for x, blocked in ((0.69, True), (0.70, False)):
            with self.subTest(x=x):
                run, pharah, brigitte = self.arena(distance=8)
                if run.match.combat.brigitte_geometry is None:
                    self.skipTest("verified Brigitte 104319 shield model required")
                target = run.players[2]
                target.mover.position = [x, pharah.mover.position[1], 12]
                pharah.mover.position[0] = x
                pharah.mover.body = target.mover.body = None
                barrier = self.raise_shield(run, brigitte)
                baseline = self.health(run, brigitte).total
                run.step({pharah: (0, 0, FIRE), brigitte: (-32768, 0, SECONDARY)})
                self.raise_shield(run, brigitte, frames=35)
                self.assertEqual(barrier.health, 130 if blocked else 250)
                self.assertEqual(self.health(run, target).total, 200 if blocked else 80)
                if blocked:
                    self.assertEqual(self.health(run, brigitte).total, baseline)
