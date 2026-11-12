"""Actual NPC field pull reuses its sole mover and explicit prepared data."""
import unittest
from dataclasses import replace
import test_ashe_pve_npcs as fixtures
from ow174.game import npc, sigma_flux
from ow174.game.collision import MOVER
from ow174.game.content import heroes
from test_game_combat import in_world, aim
import test_zarya_flux_pull as flux_fixtures
from ow174.game.mover import loaded_mover_table, mover_data

class ZaryaNpcMoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AshePveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.AshePveNpcTests.tearDownClass()

    def test_five_actual_npcs_use_explicit_data_and_existing_mover(self):
        source = fixtures.AshePveNpcTests()
        for row in source.catalog.values():
            for existing in (False, True):
                with self.subTest(npc=row.name, existing=existing):
                    mover_data(row.body)  # load the one canonical prepared table
                    table = loaded_mover_table()
                    self.assertIn(row.body, table)
                    # Same original prepared values, distinct explicit input identity.
                    explicit = replace(table[row.body])
                    definition = replace(row, mover=explicit)
                    run, zarya, bot = source.spawn(definition, 5, ally=True)
                    bot.team = 1 - zarya.team
                    position = tuple(zarya.mover.position)
                    self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
                    zarya.mover.position[:] = position
                    zarya.mover.body = None
                    fight = run.match.combat
                    zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                    run.step({zarya: (0, 0, 32)})
                    for _ in range(100):
                        run.step({})
                        if fight.zarya_gravitons.fields:
                            break
                    self.assertTrue(fight.zarya_gravitons.fields)
                    old = bot.ensure_manual_mover(fight.tick, fight._collision(), MOVER) if existing else None
                    run.step({})
                    manager = fight.zarya_gravitons
                    self.assertIn(bot.entity, manager.pulled)
                    mover = manager.npc_movers[bot.entity]
                    self.assertIs(mover.data, explicit)
                    if old is not None:
                        self.assertIs(mover, old)
                        self.assertIs(bot.impulse_mover, old)
                    run.step({})
                    self.assertIs(manager.npc_movers[bot.entity], mover)
                    bot.team = zarya.team
                    run.step({})
                    self.assertNotIn(bot.entity, manager.pulled)
                    self.assertNotIn(bot.entity, manager.npc_movers)
                    self.assertEqual(mover.velocity, (0.0, 0.0, 0.0))
                    self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))

    def test_real_flux_npc_release_keeps_same_impulse_mover_and_vertical_velocity(self):
        source = fixtures.AshePveNpcTests()
        row = next(row for row in source.catalog.values() if row.name == "Trooper")
        mover_data(row.body)
        explicit = replace(loaded_mover_table()[row.body])
        run, sigma, bot = source.spawn(replace(row, mover=explicit), 5, ally=True)
        bot.team = 1 - sigma.team
        position = tuple(sigma.mover.position)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_flux.HERO]))
        sigma.mover.position[:] = position
        sigma.mover.body = None
        match, fight = run.match, run.match.combat
        zarya = match.add_player(999, "Zarya", 0x02E0000000000068, sigma.team, False)
        in_world(match, zarya)
        run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.mover.position[:] = (position[0] - 3, position[1], position[2])
        zarya.mover.body = None
        zarya.client.queue_bot_death = lambda *args: None
        zarya.client.queue_bot_death_update = lambda *args: None
        zarya.client.bot_stream = lambda entity: None
        flux_fixtures.ZaryaFluxPullTests().select(run, sigma, tuple(bot.position))
        zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
        angles = aim(fight._eye(zarya), (bot.position[0] + 2, bot.position[1], bot.position[2]))
        run.step({zarya: (*angles, 32)})
        manager = fight.zarya_gravitons
        for _ in range(120):
            run.step({})
            if (bot.entity in manager.pulled and sigma_flux.has_effect(bot)
                    and bot.impulse_mover is not None and bot.impulse_mover.velocity[1] > 0):
                break
        self.assertTrue(sigma_flux.has_effect(bot))
        self.assertIn(bot.entity, manager.pulled)
        mover = bot.impulse_mover
        self.assertIs(manager.npc_movers[bot.entity], mover)
        self.assertIs(mover.data, explicit)
        vertical = mover.velocity[1]
        self.assertGreater(vertical, 0)
        manager.release(bot)
        self.assertNotIn(bot.entity, manager.pulled)
        self.assertNotIn(bot.entity, manager.npc_movers)
        self.assertIs(bot.impulse_mover, mover)
        self.assertEqual(mover.velocity, (0.0, vertical, 0.0))
        self.assertEqual(bot.velocity, (0.0, vertical, 0.0))
        self.assertFalse(mover.driven)
        bot.team = zarya.team
        run.step({})
        self.assertNotIn(bot.entity, manager.pulled)
        self.assertTrue(sigma_flux.has_effect(bot))
        self.assertIs(bot.impulse_mover, mover)


class ZaryaOwnedMechStopTests(unittest.TestCase):
    def test_existing_booster_stop_uses_actual_owned_mech_component(self):
        import test_dva_delivery_created_body as fixtures
        from ow174.game import dva
        from ow174.game.script import nodes

        fixtures.CreatedBodyTests.setUpClass()
        case = fixtures.CreatedBodyTests()
        run, pilot, enemy, _, mech = case.scenario()
        enemy.spawned = False
        fight = run.match.combat
        script, mover = mech.body_script, mech.control_mover
        component = script.component
        for _ in range(80):
            run.step({})
        boosters = dva.boosters(mech)
        ability = boosters.states[0]
        for _ in range(30):
            run.step({pilot: (0, 0, 8)})
            if ability.flags & nodes.Ability.RUNNING:
                break
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertIs(pilot.controlled_mech, mech)
        fight.stop_boosters(mech)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertFalse(boosters.states[2].active)
        self.assertIs(mech.body_script, script)
        self.assertIs(script.component, component)
        self.assertIs(mech.control_mover, mover)
        self.assertIsNone(dva.boosters(pilot))
