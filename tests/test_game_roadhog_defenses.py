"""Actual defensive casts versus Roadhog; scoped immunity/collision reconstructed."""

import unittest
from unittest.mock import patch

import test_mccree_flashbang as flashbang_tests
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, defenses
from ow174.game.content import heroes
from ow174.game.script import runtime

ROADHOG = 0x02E0000000000040
CASTS = (("Reaper", 8, True), ("Moira", 8, True), ("Mei", 8, False), ("Zenyatta", 32, False))


class RoadhogDefenseTests(unittest.TestCase):
    def session(self, name, *, distance=2):
        run = Run(KANEZAKA_DM, ("Roadhog", name, "Behind"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, defender, behind = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        match.switch_hero(defender, next(h for h in heroes().values() if h.name == name))
        y = hog.mover.position[1]
        for actor, z in ((hog, 0), (defender, distance), (behind, distance + 2)):
            actor.mover.position[:] = (0, y, z)
            actor.mover.body = None
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        # Retain the actual body/protection graph for repeated and ultimate hits.
        match.combat.vitals[defender.body] = combat.Vitals(defender.body, 20000)
        match.combat.vitals[behind.body] = combat.Vitals(behind.body, 20000)
        for _ in range(5):
            self.step(run, hog, defender)
        return run, hog, defender, behind

    def health(self, run, actor):
        target = next(t for t in run.match.combat._targets() if t.owner is actor)
        return run.match.combat.vitals_of(target).total

    def step(self, run, hog, defender, *, fire=0, cast=0):
        x, y, z = defender.mover.position
        angles = aim(run.match.combat._eye(hog), (x, y + 1, z))
        run.step({hog: (*angles, fire), defender: (-32768, 0, cast)})

    def cast(self, run, hog, defender, button):
        if button == 32:
            defender.body_script.set_ult_charge(defender.body_script.component.entity_var(338).value())
        self.step(run, hog, defender, cast=button)
        for _ in range(5):
            self.step(run, hog, defender)
        self.assertTrue(defenses.protection(defender)[0])

    def test_primary_close_slug_and_expired_fragments_respect_live_protection(self):
        for name, button, ghost in CASTS:
            for attack, distance in ((1, 2), (2, 2), (2, 10)):
                with self.subTest(hero=name, attack=attack, distance=distance):
                    run, hog, defender, behind = self.session(name, distance=distance)
                    self.cast(run, hog, defender, button)
                    self.assertEqual(defenses.protection(defender), (True, ghost))
                    before = self.health(run, defender)
                    weapon = next(i for i in hog.body_script.component.instances.values()
                                  if i.graph.index == 0x01C6)
                    ammo = weapon.vars[53].value()
                    self.step(run, hog, defender, fire=attack)
                    for _ in range(25):
                        self.step(run, hog, defender)
                    self.assertEqual(self.health(run, defender), before)
                    self.assertEqual(weapon.vars[53].value(), ammo - 1)
                    charge = hog.body_script.component.entity_var(342).value()
                    if ghost:
                        self.assertLess(self.health(run, behind), 20000)
                        self.assertGreater(charge, 0)
                    else:
                        self.assertEqual(self.health(run, behind), 20000)
                        self.assertEqual(charge, 0)
                        self.assertFalse(hog.client.messages(combat.HIT))

    def test_melee_cannot_damage_or_fund_charge_against_mapped_protection(self):
        for name, button, _ in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name, distance=1.5)
                behind.mover.position[0] = 20
                self.cast(run, hog, defender, button)
                before = self.health(run, defender)
                self.step(run, hog, defender, fire=0x800)
                for _ in range(25):
                    self.step(run, hog, defender)
                self.assertEqual(self.health(run, defender), before)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(hog.client.messages(combat.HIT))

    def test_hook_ghost_passes_to_rear_but_solid_protection_intercepts_without_control(self):
        for name, button, ghost in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name)
                self.cast(run, hog, defender, button)
                self.step(run, hog, defender, fire=8)
                controlled = False
                for _ in range(30):
                    self.step(run, hog, defender)
                    self.assertFalse(run.match.combat.hooks.controlled(defender.body))
                    controlled |= run.match.combat.hooks.controlled(behind.body)
                self.assertEqual(self.health(run, defender), 20000)
                self.assertEqual(controlled, ghost)
                self.assertEqual(self.health(run, behind), 19970 if ghost else 20000)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 30 if ghost else 0)
                hook = next(s for i in hog.body_script.component.instances.values()
                            if i.graph.index == 0x01C8 for s in i.states.values() if s.node.pos == 11)
                self.assertGreater(hook.remaining(), 0)

    def test_projectile_launched_before_defensive_cast_uses_impact_time_protection(self):
        for name, button, ghost in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name, distance=10)
                self.step(run, hog, defender, fire=1)
                self.assertEqual(len(run.match.combat.rockets), 25)
                self.cast(run, hog, defender, button)
                for _ in range(20):
                    self.step(run, hog, defender)
                self.assertEqual(self.health(run, defender), 20000)
                self.assertEqual(self.health(run, behind) < 20000, ghost)

    def test_actual_mei_cancel_ends_protection_before_authored_expiry(self):
        run, hog, defender, _ = self.session("Mei")
        self.cast(run, hog, defender, 8)
        for _ in range(30):
            self.step(run, hog, defender)
        self.step(run, hog, defender, cast=1)
        self.assertEqual(defenses.protection(defender), (False, False))
        before = self.health(run, defender)
        self.step(run, hog, defender, fire=1)
        for _ in range(20):
            self.step(run, hog, defender)
        self.assertLess(self.health(run, defender), before)

    def test_actual_flashbang_does_not_interrupt_mapped_protection(self):
        for name, button, _ in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, caster = self.session(name)
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                caster.mover.position[:] = (2.5, hog.mover.position[1], 2)
                self.cast(run, hog, defender, button)
                before = self.health(run, defender)
                helper = flashbang_tests.FlashbangTests()
                helper.step(run, caster, defender, cast=16)
                for _ in range(25):
                    helper.step(run, caster, defender)
                    self.assertFalse(run.match.combat.is_stunned(defender))
                self.assertTrue(defenses.protection(defender)[0])
                self.assertEqual(self.health(run, defender), before)

    def test_active_protection_graphs_export_without_evaluation_or_reentering_callbacks(self):
        import test_server_snapshot as stored

        from ow174.game.server_snapshot import snapshot_digest

        for name, button, _ in CASTS:
            with self.subTest(hero=name):
                case = stored.ServerSnapshotTests()
                session, peers, players = case.scene()
                player = players[0]
                hero = next(h for h in heroes().values() if h.name == name)
                session.apply("switch_hero", {"account": player.account_lo, "hero": hero.guid})
                if button == 32:
                    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                peers[0].commands(player.body_script.frame + 1, 1, button)
                self.assertTrue(defenses.protection(player)[0])
                with (
                    patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
                    patch.object(defenses, "advance_entry", side_effect=AssertionError("no callback")),
                ):
                    first = snapshot_digest(case.capture(session))
                    self.assertEqual(snapshot_digest(case.capture(session)), first)

    def test_running_whole_hog_respects_active_phase_and_solid_invulnerability(self):
        for name, button, ghost in CASTS:
            with self.subTest(hero=name):
                run, hog, defender, behind = self.session(name)
                hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
                self.step(run, hog, defender, fire=32)
                for _ in range(80):
                    self.step(run, hog, defender)
                self.assertLess(self.health(run, defender), 20000)
                self.cast(run, hog, defender, button)
                before, rear = self.health(run, defender), self.health(run, behind)
                for _ in range(25):
                    self.step(run, hog, defender)
                self.assertTrue(defenses.protection(defender)[0])
                self.assertEqual(self.health(run, defender), before)
                self.assertEqual(self.health(run, behind) < rear, ghost)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_expiry_restores_damage_and_hero_replacement_cannot_keep_protection(self):
        for name, button, _ in CASTS:
            for replace in (False, True):
                with self.subTest(hero=name, replace=replace):
                    run, hog, defender, _ = self.session(name)
                    self.cast(run, hog, defender, button)
                    if replace:
                        old = defender.body_script
                        run.match.switch_hero(defender, heroes()[ROADHOG])
                        defender.mover.position[:] = (0, hog.mover.position[1], 2)
                        self.assertTrue(old.retired)
                    else:
                        for _ in range(420):
                            self.step(run, hog, defender)
                    self.assertEqual(defenses.protection(defender), (False, False))
                    before = self.health(run, defender)
                    self.step(run, hog, defender, fire=1)
                    for _ in range(20):
                        self.step(run, hog, defender)
                    self.assertLess(self.health(run, defender), before)


if __name__ == "__main__":
    unittest.main()
