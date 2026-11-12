"""Real Ana/Baptiste inputs under explicit ordinary-source status/pose hosts."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_baptiste_combat as fixture
from test_baptiste_matrix import deploy_matrix
from test_game_combat import aim, in_world

from ow174.game import healing, mover, npc
from ow174.game.content import heroes

ANA = 0x02E000000000013B


class _Fixture:
    @classmethod
    def setUpClass(cls):
        if healing.Spec.load() is None:
            raise RuntimeError("requires explicit prepared0C71/0B99/0C72 status graphs")

    def arena(self, friendly=False):
        run, bap, ally, ana, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
        run.match.switch_hero(ana, heroes()[ANA])
        ana.team = 0 if friendly else 1
        for actor, point in ((bap, (-8, 1, 0)), (ally, (0, 1, 4)), (ana, (0, 1, 8))):
            actor.mover.position[:] = point
            actor.mover.body = None  # explicit static player-pose fixture, not movement acceptance
            actor.body_script.ult_seconds = 0
        self.run, self.bap, self.ally, self.ana = run, bap, ally, ana
        self.fight, self.targets = fight, targets
        self.addCleanup(patch.stopall)

    def step(self, commands=None):
        with patch.object(self.fight.projectiles, "fired", return_value=None):
            self.run.step(commands or {})

    def health(self):
        return self.fight.vitals_of(self.targets[self.ally]).total

    def injure(self, amount):
        team = self.ana.team
        self.ana.team = 1
        self.fight._damage(self.ana, self.targets[self.ally], amount, False, None, self.run.frame)
        self.ana.team = team

    def apply_grenade(self, friendly=False):
        self.ana.team = 0 if friendly else 1
        table = self.fight.healing.amp if friendly else self.fight.healing.anti
        target = self.targets[self.ally]
        previous = table.get(target.entity)
        angles = aim(self.fight._eye(self.ana),
                     (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
        for frame in range(60):
            self.step({self.ana: (*angles, 0x10 if frame == 0 else 0)})
            if target.entity in table and table[target.entity] is not previous:
                return table[target.entity]
        self.fail("actual Ana grenade did not install its selected status")

    def bap_grenade(self):
        target = self.targets[self.ally]
        angles = aim(self.fight._eye(self.bap),
                     (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
        for _ in range(45):
            self.step({self.bap: (*angles, 2)})
        self.step()

    def charge(self):
        return self.bap.body_script.component.vars[342].value()

    def another_ana(self, team):
        actor = self.run.match.add_player(9, "Second Ana", ANA, team, False)
        in_world(self.run.match, actor)
        self.run.players.append(actor)
        self.run.match.switch_hero(actor, heroes()[ANA])
        actor.mover.position[:] = (1, 1, 8)
        actor.mover.body = None
        actor.body_script.ult_seconds = 0
        return actor

class BaptisteHealingStatusTests(_Fixture, unittest.TestCase):
    def test_baptiste_received_status_modifies_actual_self_burst_without_self_charge(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                self.arena(friendly=friendly)
                self.ally.mover.position[:] = (20, 1, 4)
                self.ally = self.bap
                self.bap.mover.position[:] = (0, 1, 4)
                if friendly:
                    self.injure(180)
                effect = self.apply_grenade(friendly=friendly)
                self.injure(100)
                before = self.health()
                for _ in range(20):
                    self.step({self.bap: (0, 0, 8)})
                item = next(r for r in self.fight.baptiste.regeneration if r.recipient is self.bap)
                self.assertEqual(item.total, 150)
                overlap = max(0, min(item.start + item.duration / .016, effect.end)
                              - max(item.start, effect.start)) * .016
                expected = item.total + (0.5 if friendly else -1) * item.total / item.duration * overlap
                for _ in range(400):
                    self.step()
                self.assertAlmostEqual(self.health(), min(200, before + expected), places=4)
                self.assertEqual(self.charge(), 0)

    def test_actual_ana_grenade_crosses_allied_matrix_once_without_doubling_status_factor(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                self.arena(friendly=friendly)
                self.bap.team = self.ana.team
                matrix = deploy_matrix(self.run, self.bap)
                # Explicit static rectangle pose; native placement is a separate open gate.
                matrix.position = (0.0, 2.0, 6.0)
                matrix.rotation = (0.0, 0.0, 0.0, 1.0)
                if friendly:
                    self.injure(180)
                self.apply_grenade(friendly=friendly)
                self.assertEqual(self.health(), 200 if friendly else 80)
                for _ in range(5):
                    self.step()
                self.assertEqual(self.fight.healing.factor(self.targets[self.ally]),
                                 1.5 if friendly else 0)

    def test_actual_hostile_grenade_replaces_carrier_damage_and_blocks_grenade_healing_charge(self):
        self.arena()
        effect = self.apply_grenade()
        self.assertEqual(self.health(), 140)
        self.assertEqual(effect.end - effect.start, 250)
        self.bap_grenade()
        self.assertEqual(self.health(), 140)
        self.assertEqual(self.charge(), 0)
        while self.run.frame < effect.end:
            self.step()
        self.assertNotIn(self.ally.body, self.fight.healing.anti)
        self.bap_grenade()
        self.assertEqual(self.health(), 200)
        self.assertEqual(self.charge(), 60)

    def test_actual_friendly_grenade_delayed_multiplier_scales_effective_heal_and_charge(self):
        self.arena(friendly=True)
        self.injure(180)
        effect = self.apply_grenade(friendly=True)
        self.assertEqual(self.health(), 120)
        self.assertEqual(effect.start - self.fight.tick, 4)
        self.injure(100)
        self.bap_grenade()
        self.assertEqual(self.health(), 125)
        self.assertEqual(self.charge(), 105)

    def test_actual_burst_only_heals_unblocked_tail_and_late_update_agrees(self):
        results = []
        for skipped in (False, True):
            self.arena()
            self.injure(100)
            effect = self.apply_grenade()
            for _ in range(20):
                self.step({self.bap: (0, 0, 8)})
            item = next(r for r in self.fight.baptiste.regeneration if r.recipient is self.ally)
            expected = 40 + 15 * max(0, item.duration - (effect.end - item.start) * .016)
            if skipped:
                self.fight.update((self.run.frame + 400) * .016, self.run.frame + 400, frames=False)
            else:
                for _ in range(400):
                    self.step()
            results.append(self.health())
            self.assertAlmostEqual(self.health(), expected, places=4)
        self.assertAlmostEqual(results[0], results[1], places=4)

    def test_known_immunity_cleanses_negative_and_does_not_reappear_when_flag_clears(self):
        self.arena()
        self.apply_grenade()
        component = self.ally.body_script.component
        component.set(6604, True)
        self.step()
        self.assertNotIn(self.ally.body, self.fight.healing.anti)
        component.set(6604, False)
        self.bap_grenade()
        self.assertEqual(self.health(), 200)

    def test_negative_status_cleanup_flags_do_not_create_generic_damage_immunity(self):
        for flag in healing.Spec.load().immunity:
            with self.subTest(flag=flag):
                self.arena()
                self.ally.body_script.component.set(flag, True)
                target = self.targets[self.ally]
                angles = aim(self.fight._eye(self.ana), (0, 1 + target.height / 2, 4))
                for frame in range(60):
                    self.step({self.ana: (*angles, 0x10 if frame == 0 else 0)})
                self.assertEqual(self.health(), 140)
                self.assertNotIn(target.entity, self.fight.healing.anti)
                self.bap_grenade()
                self.assertEqual(self.health(), 200)

    def test_exempt_tag_293e_bypasses_negative_filter_without_changing_normal_grenade(self):
        self.arena()
        self.apply_grenade()
        given = self.fight._heal_target(self.bap, self.targets[self.ally], 10,
                                       tags=(0x0D8000000000293E,))
        self.assertEqual(given, 10)
        self.bap_grenade()
        self.assertEqual(self.health(), 150)
        self.assertEqual(self.charge(), 10)

    def test_actual_second_source_refreshes_amp_without_multiplying_it(self):
        self.arena(friendly=True)
        self.injure(180)
        first = self.apply_grenade(friendly=True)
        for _ in range(5):
            self.step()
        self.ana = self.another_ana(0)
        second = self.apply_grenade(friendly=True)
        self.assertGreater(second.end, first.end)
        self.assertEqual(second.start, first.start)
        self.injure(180)
        self.bap_grenade()
        self.assertEqual(self.health(), 125)
        self.assertEqual(self.charge(), 105)

    def test_negative_overrides_another_sources_amp_and_positive_survives_negative_expiry(self):
        self.arena()
        negative = self.apply_grenade()
        self.ana = self.another_ana(0)
        positive = self.apply_grenade(friendly=True)
        self.assertEqual(self.health(), 140)  # even the new100 instant heal is blocked
        self.bap_grenade()
        self.assertEqual(self.health(), 140)
        self.assertEqual(self.charge(), 0)
        while self.run.frame < negative.end:
            self.step()
        self.assertGreater(positive.end, self.run.frame)
        self.assertEqual(self.fight.healing.factor(self.targets[self.ally]), 1.5)

    def test_target_replacement_removes_status_and_caster_death_does_not(self):
        self.arena()
        self.apply_grenade()
        ana_target = next(t for t in self.fight._targets() if t.owner is self.ana)
        self.fight._damage(self.bap, ana_target, 1000, False, None, self.run.frame)
        before = self.charge()
        self.bap_grenade()
        self.assertEqual(self.health(), 140)
        self.assertEqual(self.charge(), before)
        old_body = self.ally.body
        self.run.match.switch_hero(self.ally, self.ally.hero)
        self.assertNotIn(old_body, self.fight.healing.anti)
        self.assertNotIn(self.ally.body, self.fight.healing.anti)

    def test_antiheal_blocks_field_topup_but_not_its_damage_floor(self):
        self.arena()
        effect = self.apply_grenade()
        self.injure(139)
        self.assertEqual(self.health(), 1)
        for frame in range(80):
            self.step({self.bap: (0, 8192, 0x10 if frame == 0 else 0)})
        self.assertTrue(self.fight.baptiste.fields)
        self.assertEqual(self.health(), 1)
        self.fight._damage(self.ana, self.targets[self.ally], 1000, False, None, self.run.frame)
        self.assertEqual(self.health(), 1)
        while self.run.frame <= effect.end:
            self.step()
        self.assertEqual(self.health(), 20)


class BaptisteNpcHealingStatusTests(_Fixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.environ.get("ORPHEUS_PVE_NPC_DATA")
        if not path:
            raise RuntimeError("requires explicit five-NPC catalog and movers")
        cls.catalog = npc.load_catalog(Path(path) / "pve_npcs_174.json")
        mover.mover_data(next(iter(cls.catalog.values())).body)
        if any(row.body not in mover.loaded_mover_table() for row in cls.catalog.values()):
            raise RuntimeError("status NPC acceptance requires explicit mover rows")

    def test_actual_ana_and_baptiste_inputs_apply_anti_and_amp_to_all_five_bodies(self):
        for definition in self.catalog.values():
            for friendly in (False, True):
                with self.subTest(npc=definition.name, friendly=friendly):
                    self.arena(friendly=friendly)
                    self.ally.mover.position[:] = (20, 1, 4)
                    self.ally = npc.spawn(self.run.match, definition, (0, 1, 4), team=0)
                    self.targets[self.ally] = next(t for t in self.fight._targets() if t.owner is self.ally)
                    if friendly:
                        self.injure(180)
                    effect = self.apply_grenade(friendly=friendly)
                    self.assertEqual(effect.end - (effect.start - (4 if friendly else 0)), 250)
                    if friendly:
                        self.injure(100)
                    before = self.health()
                    self.bap_grenade()
                    self.assertAlmostEqual(self.health() - before, 105 if friendly else 0, places=5)
                    self.assertAlmostEqual(self.charge(), 105 if friendly else 0, places=5)

    def test_actual_burst_integrates_anti_and_amp_expiry_on_all_five_bodies(self):
        for definition in self.catalog.values():
            for friendly in (False, True):
                with self.subTest(npc=definition.name, friendly=friendly):
                    self.arena(friendly=friendly)
                    self.ally.mover.position[:] = (20, 1, 4)
                    self.ally = npc.spawn(self.run.match, definition, (0, 1, 4), team=0)
                    self.targets[self.ally] = next(t for t in self.fight._targets() if t.owner is self.ally)
                    if friendly:
                        self.injure(180)
                    effect = self.apply_grenade(friendly=friendly)
                    self.injure(100)
                    before = self.health()
                    for _ in range(20):
                        self.step({self.bap: (0, 0, 8)})
                    item = next(r for r in self.fight.baptiste.regeneration if r.recipient is self.ally)
                    overlap = max(0, min(item.start + item.duration / .016, effect.end)
                                  - max(item.start, effect.start)) * .016
                    expected = item.total + (0.5 if friendly else -1) * item.total / item.duration * overlap
                    for _ in range(400):
                        self.step()
                    self.assertAlmostEqual(self.health() - before, expected, places=4)
                    # Authored charge updates round each incremental addition to float32.
                    self.assertAlmostEqual(self.charge(), expected, delta=1e-4)


if __name__ == "__main__":
    unittest.main()
