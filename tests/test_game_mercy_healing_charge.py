"""Actual tagged staff/chain health enters original0B8B healing charge.

Engine message production is reconstructed; original filters/arithmetic run.
Passive timer is neutralized through original12918, not mocked charge writes.
"""

import unittest
from unittest.mock import Mock, patch

import test_game_mercy_boost as boost_tests
import test_game_mercy_healing_status as status_tests
import test_game_mercy_npc as npc_tests
import test_game_mercy_npc_geometry as geometry_tests
from test_game_combat import aim
from test_game_mercy_blaster_critical import equip, shot

from ow174.game import combat, mover, npc, stats
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import expr, runtime
from ow174.game.script.projectile_defense import SHIELD_TAG
from ow174.game.script.regeneration import MERCY

HEAL_TAG = expr.Asset(0x0D800000000034BF)


@unittest.skipUnless(boost_tests.EFFECT_BUNDLE.is_file(), "selected support effect pack required")
class MercyHealingChargeTests(unittest.TestCase):
    def setUp(self):
        fixture = boost_tests.MercyBoostTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.f = fixture.fixture
        self.mercy, self.ally = self.f.mercy, self.f.ally
        self.script = self.mercy.body_script
        self.component = self.script.component
        self.component.set(12918, 0.0)  # original passive multiplier; actual heal awards remain enabled

    def charge(self):
        return self.component.entity_var(342).value()

    def step(self, count, **kwargs):
        self.f.step(count, **kwargs)

    def test_actual_staff_health_amount_and_authored_tag_reach_original_award(self):
        self.step(50)
        self.assertAlmostEqual(self.f.vitals.total, 144)
        self.assertAlmostEqual(self.charge(), 44, places=4)
        graph = next(i for i in self.component.instances.values() if i.graph.index == 0x0B8B)
        self.assertEqual(graph.find_var(0, 659).value(), (HEAL_TAG,))
        self.assertEqual(graph.find_var(0, 536).value(), expr.Entity(self.ally.body))
        self.assertAlmostEqual(graph.find_var(0, 499).value(), 0.88, places=6)

    def test_earlier_staff_healing_takes_cap_room_from_future_passive_credit(self):
        run = self.f.run
        self.component.set(12918, 1.0)
        self.script.set_ult_charge(1819.5)
        start = run.frame
        run.match.update((start + 65) * 0.016, start + 65)
        self.assertEqual(self.charge(), 1820)
        self.assertTrue(self.script.world.pending_ult_credits)

        x, y, z = self.f.ally.mover.position
        yaw, pitch = aim(self.f.fight._eye(self.mercy), (x, y + 1, z))
        self.mercy.take_commands([Command(start + 1, yaw=yaw, pitch=pitch, buttons=FIRE)])
        self.assertAlmostEqual(self.f.vitals.total, 100.88)
        self.assertEqual(self.charge(), 1820)
        self.assertFalse(hasattr(self.script.world, "pending_ult_credits"))
        self.mercy.take_commands([Command(start + 2, yaw=yaw, pitch=pitch, buttons=FIRE | 0x20)])
        self.assertTrue(self.component.entity_var(842).value())
        self.assertEqual(self.charge(), 0)

    def test_only_capped_health_is_awarded_and_full_target_stops_credit(self):
        self.f.vitals.heal(99.75)
        self.f.fight._sync_body_health(self.ally, self.f.vitals)
        self.step(50)
        self.assertEqual(self.f.vitals.total, 200)
        self.assertEqual(self.charge(), 0.25)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0.25)

    def test_staff_heals_sigma_base_shields_but_never_refills_grasp_shields(self):
        f = self.f
        sigma = self.ally
        self.assertTrue(f.match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[MERCY]))
        sigma.mover.position[:] = (0, 1, 5)
        f.enemy.mover.position[:] = (0, 1, 10)
        sigma.mover.body = f.enemy.mover.body = None
        equip(f.run, f.enemy)
        target = next(t for t in f.fight._targets() if t.owner is sigma)
        vitals = f.fight.vitals_of(target)
        body_health = vitals.now[combat.HEALTH_POOL]
        defense = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        grasp = defense.node_state(defense.graph.node(7))
        absorbed = defense.find_var(0, 21312)
        f.run.step({sigma: (0, 0, 0x08)})
        for _ in range(3):
            shot(f.run, f.enemy, sigma.mover.position, 0.8)
        self.assertTrue(grasp.active)
        self.assertEqual(absorbed.value(), 60)
        for _ in range(150):
            if not grasp.active:
                break
            f.run.step({})
        self.assertFalse(grasp.active)
        pool = vitals.temporary_shields[SHIELD_TAG]
        self.assertAlmostEqual(pool.now, 36, places=4)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)

        shot(f.run, f.enemy, sigma.mover.position, 0.8)
        self.assertAlmostEqual(pool.now, 16, places=4)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        with patch.object(f.fight, "heal_target", wraps=f.fight.heal_target) as heal:
            self.step(25)
        self.assertTrue(any(call.args[1] == sigma.body and call.kwargs["tags"] == (HEAL_TAG,)
                            for call in heal.call_args_list))
        self.assertAlmostEqual(pool.now, 16, places=4)
        self.assertEqual(self.charge(), 0)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)

        shot(f.run, f.enemy, sigma.mover.position, 0.8)
        self.assertEqual((pool.now, pool.most), (0, 0))
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 96, places=4)
        self.step(25)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], body_health)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertEqual((pool.now, pool.most), (0, 0))
        self.assertAlmostEqual(self.charge(), 4, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 4, places=4)

    def test_duplicate_input_and_another_consume_cannot_award_the_same_heal(self):
        self.step(20)
        before = self.charge()
        for _ in range(10):
            self.mercy.take_commands([Command(self.script.frame, buttons=FIRE)])
            self.f.fight.command(self.mercy, None)
        self.assertEqual(self.charge(), before)

    def test_original_award_multipliers_are_independent_of_passive_multiplier(self):
        self.component.set(9532, 2.0)
        self.component.set(13022, 0.25)
        self.step(50)
        self.assertAlmostEqual(self.charge(), 22, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 44, places=4)

    def test_healing_can_finish_charging_and_original_award_clamps_at_cost(self):
        self.script.set_ult_charge(1819.75)
        self.step(20)
        self.assertEqual(self.charge(), 1820)
        self.assertGreater(self.mercy.stats.value(stats.HEALING), 0.25)

    def test_excluded_292_and_25a_tags_heal_without_charge_then_allowed_tag_awards(self):
        # Explicit engine-event fixtures verify the original tag branches using
        # actual capped health; normal beam tags above come from the real graph.
        for tag in (0x292, 0x25A):
            given = self.f.fight.heal_target(self.mercy, self.ally.body, 10, self.f.run.frame,
                                            tags=(expr.Asset(0x0D80000000000000 | tag),))
            self.assertEqual(given, 10)
            self.assertEqual(self.charge(), 0)
        self.f.fight.heal_target(self.mercy, self.ally.body, 10, self.f.run.frame, tags=(HEAL_TAG,))
        self.assertEqual(self.charge(), 10)

    def test_target17625_blocks_only_charge_and_changes_are_read_on_next_heal(self):
        target_script = self.ally.body_script
        target_script.component.set(17625, True)
        self.step(20)
        self.assertAlmostEqual(self.f.vitals.total, 117.6, places=4)
        self.assertEqual(self.charge(), 0)
        target_script.component.set(17625, False)
        self.step(20)
        self.assertAlmostEqual(self.charge(), 17.6, places=4)

    def test_original_mode_eligibility_rejects_and_recovers_actual_heal_awards(self):
        runtime.send_message(self.component, 0x024000000000002D,
                             {0x29: expr.Asset(0x0D80000000000016)}, self.mercy.entity)
        self.step(50)
        self.assertAlmostEqual(self.f.vitals.total, 144, places=4)
        self.assertEqual(self.charge(), 0)
        runtime.send_message(self.component, 0x024000000000002D,
                             {0x29: expr.Asset(0)}, self.mercy.entity)
        self.step(50)
        self.assertAlmostEqual(self.charge(), 44, places=4)

    def test_passive_self_healing_never_emits_team_heal_charge(self):
        target = next(t for t in self.f.fight._targets() if t.owner is self.mercy)
        self.f.fight._damage(self.f.enemy, target, 50, False, None, self.f.run.frame)
        self.step(130, buttons=0)
        self.assertGreater(self.f.fight.vitals_of(target).total, 150)
        self.assertEqual(self.charge(), 0)

    def test_miss_hostile_and_dead_ally_have_no_healing_award(self):
        self.step(20, backwards=True)
        self.step(20, target=self.f.enemy)
        self.f.fight._damage(self.f.enemy, self.f.target, 500, False, None, self.f.run.frame)
        self.step(20)
        self.assertEqual(self.charge(), 0)

    def test_unbound_effect_pack_has_no_invented_tagged_award(self):
        # Explicitly absent binding preserves the earlier untagged healing bridge.
        with patch.object(boost_tests.graph, "effect_script", return_value=None):
            self.step(20)
        self.assertAlmostEqual(self.f.vitals.total, 117.6, places=4)
        self.assertEqual(self.charge(), 0)

    def test_actual_pending_tagged_report_is_stored_and_consumed_once(self):
        self.step(5)
        before_charge = self.charge()
        self.script.command(self.script.frame + 1, FIRE)
        report = self.script.support_healing[-1]
        self.assertEqual(report[3], (HEAL_TAG,))
        with patch.object(type(self.script.world), "native",
                          side_effect=AssertionError("capture ran native query")):
            before = snapshot_digest(export_body_vm(self.script))
            self.script.support_healing[-1] = (*report[:3], (expr.Asset(0x0D80000000000292),))
            changed = snapshot_digest(export_body_vm(self.script))
            self.script.support_healing[-1] = report
            restored = snapshot_digest(export_body_vm(self.script))
        self.assertNotEqual(before, changed)
        self.assertEqual(before, restored)
        self.f.fight.command(self.mercy, None)
        self.assertAlmostEqual(self.charge() - before_charge, report[2], places=5)
        self.f.fight.command(self.mercy, None)
        self.assertAlmostEqual(self.charge() - before_charge, report[2], places=5)


    def test_lethal_rifle_retires_beam_on_each_npc_before_later_input(self):
        f = self.f
        f.ally.mover.position[:] = (20, 1, 5)
        f.ally.mover.body = None
        f.run.players[:] = (f.enemy, f.mercy, f.ally)
        for player in f.run.players:
            player.client.queue_bot_death = Mock()
            player.client.queue_bot_death_update = Mock()
        catalog = npc.load_catalog(npc_tests.NPC_DATA / "pve_npcs_174.json")
        effects = f.fight.support_effects.attachments
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                self.step(20, buttons=0)
                bot = npc.spawn(f.match, definition, (0, 1, 8), team=f.mercy.team)
                target = next(t for t in f.fight._targets() if t.owner is bot)
                health = f.fight.vitals_of(target)
                health.damage(health.total - 5)
                f.fight._sync_body_health(bot, health)
                self.assertEqual(health.total, 5)
                x, y, z = bot.position
                beam = (*aim(f.fight._eye(f.mercy), (x, y + 1, z)), FIRE)
                for _ in range(8):
                    f.run.step({f.mercy: beam})
                self.assertGreater(health.total, 5)
                self.assertTrue(any(effect.target == bot.entity for effect in effects.values()))
                shot = (*aim(f.fight._eye(f.enemy), (x, y + 0.8, z)), FIRE)
                at_death = []
                died = f.fight._bot_died

                def capture(*args):
                    at_death.append((self.charge(), f.mercy.stats.value(stats.HEALING)))
                    return died(*args)

                with patch.object(f.fight, "_bot_died", side_effect=capture):
                    f.run.step({f.mercy: beam, f.enemy: shot})
                    for _ in range(6):
                        if health.dead:
                            break
                        f.run.step({f.mercy: beam})
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, f.enemy.body)
                self.assertEqual(health.death_tick, f.run.frame)
                self.assertEqual(at_death, [(self.charge(), f.mercy.stats.value(stats.HEALING))])
                self.assertFalse(any(effect.target == bot.entity for effect in effects.values()))
                for _ in range(20):
                    f.run.step({f.mercy: beam})
                self.assertEqual(health.total, 0)
                self.assertEqual(at_death, [(self.charge(), f.mercy.stats.value(stats.HEALING))])

                replacement = npc.spawn(f.match, definition, bot.position, team=f.mercy.team)
                self.assertNotEqual(replacement.entity, bot.entity)
                target = next(t for t in f.fight._targets() if t.owner is replacement)
                replacement_health = f.fight.vitals_of(target)
                replacement_health.damage(100)
                f.fight._sync_body_health(replacement, replacement_health)
                before = replacement_health.total
                for _ in range(20):
                    f.run.step({f.mercy: beam})
                healed = replacement_health.total - before
                self.assertGreater(healed, 0)
                self.assertAlmostEqual(self.charge() - at_death[0][0], healed, places=3)
                self.assertAlmostEqual(f.mercy.stats.value(stats.HEALING) - at_death[0][1],
                                       healed, places=3)
                self.assertTrue(any(effect.target == replacement.entity for effect in effects.values()))
                self.assertEqual(health.total, 0)
                npc.remove_spawned(f.match)


@unittest.skipUnless(status_tests.STATUS_BUNDLE.is_file(), "selected status/NPC support pack required")
class MercyStatusHealingChargeTests(unittest.TestCase):
    def setUp(self):
        fixture = status_tests.MercyHealingStatusTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.s = fixture
        self.f = fixture.f
        self.mercy = self.f.mercy
        self.component = self.mercy.body_script.component
        self.component.set(12918, 0.0)

    def charge(self):
        return self.component.entity_var(342).value()

    def test_antiheal_gives_no_health_or_charge(self):
        self.s.apply(self.s.anchor)
        self.f.step(50)
        self.assertEqual(self.f.health(self.s.anchor), 40)
        self.assertEqual(self.charge(), 0)

    def test_amplified_actual_health_amount_is_the_award(self):
        self.s.apply(self.s.anchor, friendly=True)
        self.f.step(5, beam=0)  # original50ms gate before the1.5 filter starts
        self.s.fight._damage(self.s.ana, self.s.target(self.s.anchor), 100, False, None, self.s.run.frame)
        self.f.step(50)
        self.assertAlmostEqual(self.f.health(self.s.anchor), 166, places=4)
        self.assertAlmostEqual(self.charge(), 66, places=4)

    def test_valkyrie_primary_and_chain_heal_without_charge_then_normal_beam_resumes(self):
        self.f.start()
        self.f.step(50)
        self.assertAlmostEqual(self.f.health(self.s.anchor), 148, places=4)
        self.assertAlmostEqual(self.f.health(self.s.chain), 148, places=4)
        self.assertEqual(self.charge(), 0)
        self.f.step(950, beam=0)
        self.assertFalse(self.component.entity_var(842).value())
        self.f.step(50)
        self.assertAlmostEqual(self.charge(), 44, places=4)

    @unittest.skipUnless(geometry_tests.NPC_MOVERS.is_file(), "prepared NPC mover table required")
    def test_actual_staff_heal_awards_on_all_five_extracted_npc_capsules(self):
        self.enterContext(patch.object(mover, "MOVERS_PATH", geometry_tests.NPC_MOVERS))
        mover._movers.cache_clear()
        self.addCleanup(mover._movers.cache_clear)
        for actor, x in ((self.s.anchor, 20), (self.s.chain, 30)):
            actor.mover.position[:] = [x, 1, 5]
        catalog = npc.load_catalog(npc_tests.NPC_DATA / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                self.f.step(10, beam=0)
                bot = npc.spawn(self.s.match, definition, (0, 1, 8), team=0)
                target = next(t for t in self.s.fight._targets() if t.owner is bot)
                vitals = self.s.fight.vitals_of(target)
                vitals.damage(100)
                self.s.fight._sync_body_health(bot, vitals)
                health, before = vitals.total, self.charge()
                self.assertEqual((target.height, target.radius), geometry_tests.EXPECTED[definition.name][:2])
                for _ in range(50):
                    x, y, z = bot.position
                    yaw, pitch = aim(self.s.fight._eye(self.mercy), (x, y + 1, z))
                    self.s.run.step({self.mercy: (yaw, pitch, FIRE)})
                self.assertAlmostEqual(vitals.total - health, 44, places=4)
                self.assertAlmostEqual(self.charge() - before, 44, places=3)
                npc.remove_spawned(self.s.match)

    def test_actual_antiheal_exclusion_channel_uses_the_supplied_health_tags(self):
        self.s.apply(self.s.anchor)
        target = self.s.anchor.body
        self.assertEqual(self.s.fight.heal_target(self.mercy, target, 10, self.s.run.frame,
                                                tags=(HEAL_TAG,)), 0)
        # Explicit engine-event fixture supplies the authored excluded293E tag.
        given = self.s.fight.heal_target(self.mercy, target, 10, self.s.run.frame,
                                        tags=(expr.Asset(0x0D8000000000293E),))
        self.assertEqual(given, 10)
        self.assertEqual(self.charge(), 10)


if __name__ == "__main__":
    unittest.main()
