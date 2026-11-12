"""Ana attacks against the authored Winston deployment and lifetime graphs."""
import unittest

import test_game_ana as fixture
import test_game_ana_orisa_barrier as helpers

from ow174.game import stats
from ow174.game.content import heroes


class AnaWinstonBarrierTests(unittest.TestCase):
    def deploy(self, *, ally=False):
        run, ana, winston, _ = fixture.AnaRifleTests().setup_fight(enemy=not ally)
        self.assertTrue(run.match.switch_hero(winston, heroes()[0x02E0000000000009]))
        x, y, z = ana.mover.position
        helpers.AnaOrisaBarrierTests.place(winston, (x + 8, y, z))
        for _ in range(30):
            run.step({})
        run.step({winston: (0, 0, 16)})
        manager = run.match.combat.winston_bubbles
        for _ in range(15):
            run.step({})
            if manager.actors:
                break
        [dome] = manager.actors.values()
        self.assertEqual(dome.health, 700)
        self.assertEqual(dome.request.resource.guid, 0x05800000000000FC)
        return run, ana, winston, dome

    def test_rifles_sleep_and_grenade_stop_at_enemy_shell(self):
        for button, scoped, damage in ((1, False, 70), (1, True, 70), (8, False, 5), (16, False, 50)):
            with self.subTest(button=button, scoped=scoped):
                arena = self.deploy()
                run, ana, winston, dome = arena
                vitals = helpers.AnaOrisaBarrierTests.vitals(run, winston)
                before = vitals.total
                helpers.AnaOrisaBarrierTests.fire(self, arena, button, scoped=scoped)
                self.assertEqual(dome.health, 700 - damage)
                self.assertEqual(vitals.total, before)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertFalse(run.match.combat.is_asleep(winston))
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_breaking_grenade_keeps_shell_occlusion_for_that_explosion(self):
        arena = self.deploy()
        run, _, winston, dome = arena
        # Put the victim just behind the near shell, inside the blast radius.
        x, y, z = dome.centre
        helpers.AnaOrisaBarrierTests.place(winston, (x - 4, y - .1, z))
        run.step({})
        vitals = helpers.AnaOrisaBarrierTests.vitals(run, winston)
        before = vitals.total
        dome.health = 40
        helpers.AnaOrisaBarrierTests.fire(self, arena, 16)
        self.assertEqual(dome.health, 0)
        self.assertEqual(vitals.total, before)
        self.assertEqual(vitals.healing_block_until, 0)
        self.assertNotIn(dome.body, run.match.combat.winston_bubbles.actors)

    def test_friendly_dome_allows_rifle_healing_and_nano(self):
        arena = self.deploy(ally=True)
        run, ana, winston, dome = arena
        vitals = helpers.AnaOrisaBarrierTests.vitals(run, winston)
        vitals.damage(200)
        before = vitals.total
        helpers.AnaOrisaBarrierTests.fire(self, arena, 1)
        self.assertEqual(vitals.total, before + 70)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        helpers.AnaOrisaBarrierTests.fire(self, arena, 32)
        self.assertGreater(vitals.nano_until, run.match.combat.now)
        self.assertEqual(dome.health, 700)

    def test_friendly_dome_passes_grenade_and_boosted_rifle_healing(self):
        arena = self.deploy(ally=True)
        run, ana, winston, dome = arena
        vitals = helpers.AnaOrisaBarrierTests.vitals(run, winston)
        vitals.damage(250, bypass_armour=True)
        before = vitals.total
        helpers.AnaOrisaBarrierTests.fire(self, arena, 16)
        self.assertEqual(vitals.total, before + 100)
        self.assertGreater(vitals.healing_boost_until, run.match.combat.now)
        self.assertEqual(vitals.healing_block_until, 0)
        helpers.AnaOrisaBarrierTests.fire(self, arena, 1)
        self.assertEqual(vitals.total, before + 205)
        self.assertEqual(ana.stats.value(stats.HEALING), 205)
        self.assertEqual(dome.health, 700)

    def test_melee_hits_shell_in_reach_and_vm_snapshot_tracks_health(self):
        from test_game_combat import aim

        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest

        run, ana, winston, dome = self.deploy()
        x, y, z = dome.centre
        helpers.AnaOrisaBarrierTests.place(ana, (x - 6, y - .1, z))
        run.step({})
        direction = aim(run.match.combat._eye(ana), (x - 4, y + 1, z))
        run.step({ana: (*direction, 0x800)})
        for _ in range(35):
            run.step({})
        self.assertEqual(dome.health, 670)
        self.assertEqual(helpers.AnaOrisaBarrierTests.vitals(run, winston).total, 500)
        before = snapshot_digest(export_component_vm(dome.component))
        run.match.combat.winston_bubbles.damage(dome, 5)
        self.assertNotEqual(snapshot_digest(export_component_vm(dome.component)), before)

    def test_dome_holds_position_then_expires_and_releases_fire(self):
        arena = self.deploy()
        run, _, winston, dome = arena
        centre = dome.centre
        for _ in range(550):
            run.step({})
        self.assertEqual(dome.centre, centre)
        self.assertIn(dome.body, run.match.combat.winston_bubbles.actors)
        for _ in range(25):
            run.step({})
        self.assertTrue(dome.retired)
        self.assertFalse(dome.component.instances)
        vitals = helpers.AnaOrisaBarrierTests.vitals(run, winston)
        before = vitals.total
        helpers.AnaOrisaBarrierTests.fire(self, arena, 1, scoped=True)
        self.assertEqual(vitals.total, before - 70)
