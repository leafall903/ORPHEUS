"""Actual health consumers of original Ana effect graphs, with declared host adapters."""

import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_mercy_chains as chains
from test_game_combat import aim

from ow174.game import combat, cryo, npc, immunity, stats
from ow174.game.body_vm_snapshot import export_body_vm, export_component_vm, snapshot_digest
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script import healing_status
from ow174.game.script.driver import ULT_CHARGE

STATUS_BUNDLE = Path("D:/overwatch 1.7.4/verification/hero-lanes/0004/research") / (
    "game_graphs_support_status_174.json.gz"
)
ANA = 0x02E000000000013B


@unittest.skipUnless(STATUS_BUNDLE.is_file(), "explicit Ana/support status graph pack required")
class MercyHealingStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture = chains.MercyChainTests()
        # The existing chain fixture reads the same current composed provider.
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture
        self.match, self.run, self.fight = self.f.match, self.f.run, self.f.fight
        self.mercy, self.anchor, self.chain, self.ana = (
            self.f.mercy, self.f.anchor, self.f.chain, self.f.enemy)
        self.match.switch_hero(self.ana, heroes()[ANA])

    def target(self, actor):
        return next(t for t in self.fight._targets() if t.owner is actor)

    def apply(self, actor, *, friendly=False):
        self.ana.team = actor.team if friendly else 1 - actor.team
        self.fight.healing.impact(self.ana, self.target(actor))

    def test_hostile_original_effect_blocks_actual_staff_and_restores_it_after_four_seconds(self):
        self.apply(self.anchor)
        self.assertEqual(self.f.health(self.anchor), 40)
        self.f.step(200)
        self.assertEqual(self.f.health(self.anchor), 40)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)
        self.f.step(60)
        self.assertGreater(self.f.health(self.anchor), 40)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 1)

    def test_original_friendly_instant_heal_runs_once_and_amplifies_staff_only_once(self):
        self.apply(self.anchor, friendly=True)
        self.assertEqual(self.f.health(self.anchor), 200)
        self.assertEqual(self.ana.stats.value(stats.HEALING), 100)
        self.f.step(5, beam=0)  # original50ms gate enables the actual1.5 filter
        self.fight.vitals_of(self.target(self.anchor)).damage(100)
        self.fight._sync_body_health(self.anchor, self.fight.vitals_of(self.target(self.anchor)))
        self.f.step(50)
        self.assertAlmostEqual(self.f.health(self.anchor), 166, places=4)  #55*1.5*.8
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 66, places=4)
        self.assertEqual(self.ana.stats.value(stats.HEALING), 100)

    def test_antiheal_overrides_amp_from_another_original_source(self):
        self.apply(self.anchor, friendly=True)
        self.f.step(5, beam=0)
        other = self.chain
        other.team = 1
        self.fight.healing.impact(other, self.target(self.anchor))
        before = self.f.health(self.anchor)
        self.f.step(50)
        self.assertEqual(self.f.health(self.anchor), before)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 0)

    def test_hostile_effect_blocks_real_mercy_passive_regeneration(self):
        self.apply(self.mercy)
        self.assertEqual(self.f.health(self.mercy), 140)
        self.f.step(200, beam=0)
        self.assertEqual(self.f.health(self.mercy), 140)
        self.f.step(60, beam=0)
        self.assertGreater(self.f.health(self.mercy), 140)

    def test_late_passive_healing_respects_antiheal_that_expired_during_gap(self):
        self.f.step(35, beam=0)
        x, y, z = self.mercy.mover.position
        self.ana.spawn = ((x + 4, y, z), 0.0)
        self.ana.mover = self.ana.new_mover()
        yaw, pitch = aim(self.fight._eye(self.ana), (x, y + 0.8, z))
        self.run.step({self.ana: (yaw, pitch, 0x10)})
        for _ in range(60):
            self.run.step({})
            if self.fight.healing.scale(self.mercy.body) == 0:
                break
        self.assertEqual(self.f.health(self.mercy), 140)
        self.assertEqual(self.fight.healing.scale(self.mercy.body), 0)
        impact = self.run.frame
        for _ in range(260):
            self.run.frame += 1
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertEqual(self.mercy.body_script.frame, impact)
        self.assertEqual(self.f.health(self.mercy), 140)
        self.assertEqual(self.fight.healing.scale(self.mercy.body), 1)
        script = self.mercy.body_script
        self.assertEqual(script.world.passive_healing_scales, ((impact, 0.0), (impact + 250, 1.0)))
        snapshot = snapshot_digest(export_body_vm(script))
        self.assertEqual(snapshot_digest(export_body_vm(script)), snapshot)

        self.mercy.take_commands([Command(self.run.frame)])
        self.assertAlmostEqual(self.f.health(self.mercy), 143.52)
        self.assertFalse(hasattr(script.world, "passive_healing_scales"))
        self.mercy.take_commands([Command(self.run.frame - 1), Command(self.run.frame)])
        self.assertAlmostEqual(self.f.health(self.mercy), 143.52)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)

    def staff_across_grenade_expiry(self, *, valkyrie, late):
        if valkyrie:
            self.f.start()
        else:
            self.f.step(35, beam=0)
        script = self.mercy.body_script
        script.component.set(12918, 0.0)
        target = self.chain if valkyrie else self.anchor
        x, y, z = target.mover.position
        self.ana.spawn = ((x + 4, y, z), 0.0)
        self.ana.mover = self.ana.new_mover()
        yaw, pitch = aim(self.fight._eye(self.ana), (x, y + 0.8, z))
        self.run.step({self.ana: (yaw, pitch, 0x10)})
        for _ in range(60):
            self.run.step({})
            if self.fight.healing.scale(target.body) == 0:
                break
        self.assertEqual(self.f.health(target), 40)
        self.assertEqual(self.fight.healing.scale(target.body), 0)
        impact = self.run.frame
        self.run.frame += 244
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.mercy.take_commands([Command(self.run.frame)])
        end = impact + 255
        if late:
            self.match.update(end * combat.TICK, end)
            status = self.fight.healing
            self.assertEqual(status.scale(target.body, frame=impact + 249), 0)
            self.assertEqual(status.scale(target.body, (0x0D8000000000293E,), frame=impact + 249), 1)
            self.assertEqual(status.scale(target.body, frame=impact + 250), 1)
        x, y, z = self.anchor.mover.position
        yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
        trace = []
        for frame in range(self.run.frame + 1, end + 1):
            if not late:
                self.match.update(frame * combat.TICK, frame)
            self.mercy.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=FIRE)])
            trace.append((self.f.health(target), self.mercy.stats.value(stats.HEALING),
                          script.component.entity_var(ULT_CHARGE).value()))
        self.mercy.take_commands([Command(end)])
        self.assertEqual(trace[-1][0], self.f.health(target))
        self.match.update((end + 1) * combat.TICK, end + 1)
        self.assertNotIn(target.body, self.fight.healing.filter_history)
        return trace

    def test_late_staff_and_chain_respect_antiheal_through_sparse_world_expiry(self):
        for valkyrie in (False, True):
            with self.subTest(valkyrie=valkyrie):
                traces = []
                for late in (False, True):
                    fixture = MercyHealingStatusTests()
                    try:
                        fixture.setUp()
                        traces.append(fixture.staff_across_grenade_expiry(valkyrie=valkyrie, late=late))
                    finally:
                        fixture.doCleanups()
                timely, late = traces
                self.assertEqual([row[0] for row in timely[:5]], [40] * 5)
                self.assertAlmostEqual(timely[-1][0], 45.76 if valkyrie else 45.28)
                self.assertAlmostEqual(timely[-1][2], 0 if valkyrie else 5.28, places=4)
                for expected, actual in zip(timely, late, strict=True):
                    for left, right in zip(expected, actual, strict=True):
                        self.assertAlmostEqual(left, right, places=4)

    def test_target_replacement_destroys_status_vm_without_touching_new_body(self):
        self.apply(self.anchor)
        old = next(iter(self.fight.healing.applied.values())).component
        old_body = self.anchor.body
        self.match.switch_hero(self.anchor, self.anchor.hero)
        self.assertFalse(old.instances)
        self.assertNotEqual(self.anchor.body, old_body)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 1)

    def test_invulnerable_recipient_rejects_health_and_effect_then_accepts_after_expiry(self):
        vitals = self.fight.vitals_of(self.target(self.anchor))
        vitals.invulnerable_until = self.fight.now + 0.2
        self.apply(self.anchor)
        self.assertEqual(vitals.total, 100)
        self.assertFalse(self.fight.healing.applied)
        self.f.step(15, beam=0)
        self.apply(self.anchor)
        self.assertEqual(vitals.total, 40)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 0)

    def test_actual_status_vm_has_passive_causal_storage(self):
        self.apply(self.anchor)
        item = next(iter(self.fight.healing.applied.values()))
        before = snapshot_digest(export_component_vm(item.component))
        self.assertEqual(before, snapshot_digest(export_component_vm(item.component)))
        child = next(i for i in item.component.instances.values() if i.graph.index == 0x0C72)
        child.write((0, 476), 61)
        self.assertNotEqual(before, snapshot_digest(export_component_vm(item.component)))
        self.assertEqual(self.f.health(self.anchor), 40)

    def test_actual_ana_throw_hits_target_runs_original_effect_and_never_adds_outer_fifty(self):
        self.f.step(35, beam=0)
        before = self.ana.stats.value(stats.HERO_DAMAGE)
        point = self.anchor.mover.position
        yaw, pitch = aim(self.fight._eye(self.ana), (point[0], point[1] + 1, point[2]))
        self.run.step({self.ana: (yaw, pitch, 0x10)})
        self.assertTrue(any(r.shooter is self.ana for r in self.fight.rockets))
        self.f.step(30, beam=0)
        self.assertEqual(self.f.health(self.anchor), 40)
        self.assertAlmostEqual(self.ana.stats.value(stats.HERO_DAMAGE) - before, 60)
        self.assertEqual(self.f.health(self.chain), 100)  # five metres away, outside radius4
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 0)
        self.f.step(50, beam=FIRE)
        self.assertEqual(self.f.health(self.anchor), 40)

    def test_original_antiheal_blocks_valkyrie_secondary_without_blocking_primary(self):
        self.f.start()
        self.apply(self.chain)
        self.f.step(50)
        self.assertAlmostEqual(self.f.health(self.anchor), 148, places=4)
        self.assertEqual(self.f.health(self.chain), 40)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 48, places=4)

    def test_original_amp_applies_once_to_actual_valkyrie_secondary_healing(self):
        self.f.start()
        self.apply(self.chain, friendly=True)
        self.f.step(5, beam=0)
        vitals = self.fight.vitals_of(self.target(self.chain))
        vitals.damage(100)
        self.fight._sync_body_health(self.chain, vitals)
        self.f.step(50)
        self.assertAlmostEqual(self.f.health(self.anchor), 148, places=4)
        self.assertAlmostEqual(self.f.health(self.chain), 172, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 120, places=4)

    def test_amp_applies_to_actual_passive_without_granting_team_healing_credit(self):
        self.apply(self.mercy, friendly=True)
        self.f.step(5, beam=0)
        self.chain.team = 1
        self.fight._damage(self.chain, self.target(self.mercy), 50, False, None, self.run.frame)
        self.f.step(125, beam=0)
        self.assertAlmostEqual(self.f.health(self.mercy), 180.24, places=4)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)

    def test_repeated_impact_replaces_original_status_and_refreshes_its_timer(self):
        self.apply(self.anchor)
        item = next(iter(self.fight.healing.applied.values()))
        child = next(i for i in item.component.instances.values() if i.graph.index == 0x0C72)
        self.f.step(150, beam=0)
        vitals = self.fight.vitals_of(self.target(self.anchor))
        vitals.heal(160)  # fixture replenishment to survive the second real impact
        self.fight._sync_body_health(self.anchor, vitals)
        self.apply(self.anchor)
        self.assertTrue(child.stopped)
        self.assertEqual(vitals.total, 140)
        self.f.step(150, beam=0)
        self.assertEqual(vitals.total, 140)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 0)
        self.f.step(101, beam=0)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 1)

    def test_applied_status_lasts_until_its_timer_after_original_caster_dies(self):
        self.apply(self.anchor)
        self.fight._damage(self.mercy, self.target(self.ana), 500, False, None, self.run.frame)
        self.f.step(200)
        self.assertEqual(self.f.health(self.anchor), 40)
        self.f.step(60)
        self.assertGreater(self.f.health(self.anchor), 40)

    def test_target_death_retires_all_original_status_instances(self):
        self.apply(self.anchor)
        item = next(iter(self.fight.healing.applied.values()))
        self.fight._damage(self.ana, self.target(self.anchor), 100, False, None, self.run.frame)
        self.assertFalse(item.component.instances)
        self.assertFalse(self.fight.healing.applied)

    def test_missing_selected_effect_binding_cannot_invent_a_status(self):
        with patch.object(healing_status.Statuses, "available", return_value=False):
            self.apply(self.anchor)
        self.assertEqual(self.f.health(self.anchor), 100)
        self.assertFalse(self.fight.healing.applied)

    def test_all_five_npc_bodies_reject_staff_healing_during_original_antiheal_then_recover(self):
        catalog = npc.load_catalog(chains.npc_tests.NPC_DATA / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                bot = npc.spawn(self.match, definition, (0, 1, 8), team=0)
                target = next(t for t in self.fight._targets() if t.owner is bot)
                vitals = self.fight.vitals_of(target)
                vitals.damage(100)
                before = vitals.total
                self.fight.healing.impact(self.ana, target)
                self.assertEqual(vitals.total, before - 60)
                given = self.fight.heal_target(self.mercy, bot.entity, 50, self.run.frame)
                self.assertEqual(given, 0)
                self.f.step(251, beam=0)
                self.assertGreater(self.fight.heal_target(self.mercy, bot.entity, 50, self.run.frame), 0)
                item_bodies = {a.target for a in self.fight.healing.applied.values()}
                self.assertNotIn(bot.entity, item_bodies)
                npc.remove_spawned(self.match)

    def test_actual_ana_throw_heals_friend_once_and_enables_its_original_amp_filter(self):
        self.ana.team = 0
        self.f.step(35, beam=0)
        point = self.anchor.mover.position
        yaw, pitch = aim(self.fight._eye(self.ana), (point[0], point[1] + 1, point[2]))
        self.run.step({self.ana: (yaw, pitch, 0x10)})
        self.f.step(35, beam=0)
        self.assertEqual(self.f.health(self.anchor), 200)
        self.assertEqual(self.ana.stats.value(stats.HEALING), 100)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 1.5)

    def test_expiry_of_one_source_does_not_remove_another_sources_later_status(self):
        self.apply(self.anchor, friendly=True)
        self.f.step(50, beam=0)
        self.chain.team = 1
        self.fight.healing.impact(self.chain, self.target(self.anchor))
        self.f.step(201, beam=0)  # friendly effect expires, hostile still has49 frames
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 0)
        self.assertEqual(len(self.fight.healing.applied), 1)
        self.f.step(50, beam=0)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 1)

    def test_actual_boosted_grenade_keeps_launch_multiplier_after_mercy_releases(self):
        self.check_boosted_grenade(100, 78, 18)

    def test_boosted_grenade_overkill_counts_only_extra_health_removed(self):
        for remaining, award in ((55, 0), (70, 10)):
            with self.subTest(remaining=remaining):
                f = MercyHealingStatusTests()
                f.setUp()
                self.addCleanup(f.doCleanups)
                f.check_boosted_grenade(remaining, remaining, award)

    def check_boosted_grenade(self, remaining, damage, award):
        self.ana.team = 0
        self.anchor.team = 1
        health = self.fight.vitals_of(self.target(self.anchor))
        health.damage(health.total - remaining)
        self.ana.spawn = ((0, 1, 12), 0.0)
        self.ana.mover = self.ana.new_mover()
        component = self.mercy.body_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        before_charge = charge.value()
        boost_stats = (0x086000000000048A, 0x086000000000048C)
        before_boost = [self.mercy.stats.value(stat) for stat in boost_stats]
        self.f.step(35, beam=0)
        byaw, bpitch = aim(self.fight._eye(self.mercy), (0, 2, 12))
        ayaw, apitch = aim(self.fight._eye(self.ana), (0, 2, 5))
        for _ in range(10):
            self.run.step({self.mercy: (byaw, bpitch, SECONDARY)})
        before = self.ana.stats.value(stats.HERO_DAMAGE)
        self.run.step({self.mercy: (byaw, bpitch, SECONDARY), self.ana: (ayaw, apitch, 0x10)})
        rocket = next(r for r in self.fight.rockets if r.shooter is self.ana)
        self.assertAlmostEqual(rocket.boost_credit[1], 1.3, places=5)
        self.f.step(30, beam=0)
        self.assertAlmostEqual(health.total, remaining - damage, places=4)
        self.assertEqual(health.dead, remaining == damage)
        self.assertAlmostEqual(self.ana.stats.value(stats.HERO_DAMAGE) - before, damage, places=4)
        self.assertAlmostEqual(charge.value() - before_charge, award, places=4)
        for stat, prior in zip(boost_stats, before_boost, strict=True):
            self.assertAlmostEqual(self.mercy.stats.value(stat) - prior, award, places=4)
        self.f.step(30, beam=0)
        self.assertAlmostEqual(charge.value() - before_charge, award, places=4)
        for stat, prior in zip(boost_stats, before_boost, strict=True):
            self.assertAlmostEqual(self.mercy.stats.value(stat) - prior, award, places=4)

    def protected_grenade_encounter(self, hero, *, friendly=False):
        self.assertTrue(self.match.switch_hero(self.anchor, heroes()[hero]))
        self.ana.team = self.anchor.team if friendly else 1
        self.ana.mover.position[:] = (0, 1, 0)
        self.anchor.mover.position[:] = (0, 1, 4)
        self.mercy.mover.position[:] = (0, 1, 10)
        self.chain.mover.position[:] = (10, 1, 10)
        for player in self.run.players:
            player.mover.body = None
        for _ in range(35):
            self.run.step({})

    def throw_through_anchor(self):
        yaw, pitch = aim(self.fight._eye(self.ana), (0, 2, 10))
        self.run.step({self.ana: (yaw, pitch, 16)})
        self.assertTrue(any(rocket.shooter is self.ana for rocket in self.fight.rockets))
        for _ in range(30):
            self.run.step({})
        self.assertFalse(self.fight.rockets)

    def test_hostile_grenade_passes_through_phase_and_cannot_apply_antiheal(self):
        for hero in (0x02E0000000000002, 0x02E00000000001A2):
            with self.subTest(hero=hero):
                fixture = MercyHealingStatusTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.protected_grenade_encounter(hero)
                fixture.anchor.mover.position[2] = 7.5
                before = fixture.f.health(fixture.anchor)
                fixture.run.step({fixture.anchor: (0, 0, 8)})
                self.assertTrue(immunity.phased_out(fixture.anchor))
                fixture.throw_through_anchor()
                self.assertTrue(immunity.phased_out(fixture.anchor))
                self.assertEqual(fixture.f.health(fixture.anchor), before)
                self.assertEqual(fixture.fight.healing.scale(fixture.anchor.body), 1)
                self.assertEqual(fixture.f.health(fixture.mercy), 140)
                self.assertEqual(fixture.fight.healing.scale(fixture.mercy.body), 0)

    def test_hostile_grenade_detonates_on_cryo_without_applying_antiheal(self):
        self.protected_grenade_encounter(cryo.HERO)
        before = self.f.health(self.anchor)
        self.run.step({self.anchor: (0, 0, 8)})
        self.assertTrue(cryo.protected(self.anchor))
        self.throw_through_anchor()
        self.assertEqual(self.f.health(self.anchor), before)
        self.assertEqual(self.f.health(self.mercy), 200)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 1)
        self.assertEqual(self.fight.healing.scale(self.ana.body), 1.5)

    def test_friendly_grenade_heals_protected_ally(self):
        self.protected_grenade_encounter(cryo.HERO, friendly=True)
        self.fight.vitals_of(self.target(self.anchor)).damage(120)
        before = self.f.health(self.anchor)
        self.run.step({self.anchor: (0, 0, 8)})
        self.assertTrue(cryo.protected(self.anchor))
        self.throw_through_anchor()
        self.assertEqual(self.f.health(self.anchor), before + 100)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 1.5)

    def test_authored_protection_stacks_end_existing_antiheal_and_restore_staff(self):
        cases = [(hero, immunity.phased_out) for hero in (0x02E0000000000002, 0x02E00000000001A2)]
        cases.append((cryo.HERO, cryo.protected))
        for hero, protected in cases:
            with self.subTest(hero=hero):
                f = MercyHealingStatusTests()
                f.setUp()
                self.addCleanup(f.doCleanups)
                f.protected_grenade_encounter(hero)
                f.throw_through_anchor()
                self.assertEqual(f.fight.healing.scale(f.anchor.body), 0)
                item = f.fight.healing.applied[(f.ana.body, f.anchor.body)]
                antiheal = next(i for i in item.component.instances.values() if i.graph.index == 0x0C72)
                self.assertTrue(antiheal.state(5).active)
                before = f.f.health(f.anchor)
                f.run.step({f.anchor: (0, 0, 8)})
                self.assertTrue(protected(f.anchor))
                f.f.step(20)
                self.assertTrue(protected(f.anchor))
                self.assertTrue(antiheal.stopped)
                self.assertEqual(f.fight.healing.scale(f.anchor.body), 1)
                self.assertAlmostEqual(f.f.health(f.anchor) - before, 17.6)

    def test_phase_cleanse_preserves_friendly_amp_from_another_grenade(self):
        self.protected_grenade_encounter(0x02E0000000000002)  # Reaper Wraith
        self.fight.vitals_of(self.target(self.anchor)).damage(130)
        self.throw_through_anchor()
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 0)
        self.assertTrue(self.match.switch_hero(self.chain, heroes()[ANA]))
        self.chain.mover.position[:] = (0, 1, 8)
        self.chain.mover.body = None
        yaw, pitch = aim(self.fight._eye(self.chain), (0, 2, 4))
        self.run.step({self.chain: (yaw, pitch, 16)})
        self.assertTrue(any(rocket.shooter is self.chain for rocket in self.fight.rockets))
        for _ in range(20):
            self.run.step({})
        self.assertIn((self.chain.body, self.anchor.body), self.fight.healing.applied)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 0)

        before = self.f.health(self.anchor)
        self.run.step({self.anchor: (0, 0, 8)})
        self.f.step(20)
        self.assertTrue(immunity.phased_out(self.anchor))
        self.assertNotIn((self.ana.body, self.anchor.body), self.fight.healing.applied)
        self.assertEqual(self.fight.healing.scale(self.anchor.body), 1.5)
        self.assertAlmostEqual(self.f.health(self.anchor) - before, 26.4)

    def test_all_five_npc_bodies_receive_original_amp_and_manual_removal_retires_status(self):
        self.ana.team = 0
        catalog = npc.load_catalog(chains.npc_tests.NPC_DATA / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                bot = npc.spawn(self.match, definition, (0, 1, 8), team=0)
                target = next(t for t in self.fight._targets() if t.owner is bot)
                vitals = self.fight.vitals_of(target)
                vitals.damage(100)
                self.fight.healing.impact(self.ana, target)
                self.f.step(5, beam=0)
                self.assertEqual(vitals.total, sum(vitals.most.values()))
                vitals.damage(100)
                self.assertAlmostEqual(self.fight.heal_target(self.mercy, bot.entity, 10, self.run.frame), 15)
                item = self.fight.healing.applied[(self.ana.body, bot.entity)]
                npc.remove_spawned(self.match)
                self.assertFalse(item.component.instances)
                self.assertFalse(self.fight.healing.applied)


if __name__ == "__main__":
    unittest.main()
