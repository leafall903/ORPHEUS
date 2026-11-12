"""Real Rally input/cost/pulses with reconstructed temporary armor cohorts."""

import unittest

import test_game_brigitte as brigitte_tests
from test_game_health import bar

from ow174.game import world
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import Command


class RallyTests(unittest.TestCase):
    def fixture(self, *, ffa=False):
        run, player = brigitte_tests.BrigitteTests().fixture(("Brigitte", "Enemy", "Ally"))
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": ffa,
        })
        enemy, ally = run.players[1:]
        enemy.team, ally.team = 1 - player.team, player.team
        x, y, z = player.mover.position
        enemy.mover.position, ally.mover.position = [x + 5, y, z], [x, y, z + 4]
        player.body_script.ult_seconds = 0  # isolate event credit from the default passive clock
        return run, player, enemy, ally

    def vitals(self, run, player):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is player))

    def charge(self, player):
        return player.body_script.component.entity_var(342).value()

    def activate(self, run, player):
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})

    def advance(self, run, frames):
        for _ in range(frames):
            run.step({})

    def test_undercharged_input_is_rejected_and_full_cast_spends_live2800_cost(self):
        run, player, _enemy, _ally = self.fixture()
        cost = player.body_script.component.entity_var(338).value()
        self.assertEqual(cost, 2800)
        player.body_script.set_ult_charge(cost - 1)
        run.step({player: (0, 0, 32)})
        self.assertEqual(self.vitals(run, player).temporary_armour, 0)
        self.assertEqual(self.charge(player), cost - 1)
        self.advance(run, 12)  # release and expire early input before next press
        self.activate(run, player)
        self.assertEqual(self.charge(player), 0)
        self.assertEqual(self.vitals(run, player).temporary_armour, 15)

    def test_allied_pulses_cap100_and_enemy_receives_none(self):
        run, player, enemy, ally = self.fixture()
        self.activate(run, player)
        self.advance(run, 220)
        self.assertEqual(self.vitals(run, player).temporary_armour, 100)
        self.assertEqual(self.vitals(run, ally).temporary_armour, 100)
        self.assertEqual(self.vitals(run, enemy).temporary_armour, 0)
        self.assertEqual(self.vitals(run, player).now[world.ARMOUR_POOL], 50)
        self.assertEqual(self.charge(player), 0)

    def test_out_of_range_and_ffa_filter_allies(self):
        for ffa in (False, True):
            with self.subTest(ffa=ffa):
                run, player, _enemy, ally = self.fixture(ffa=ffa)
                if not ffa:
                    x, y, z = player.mover.position
                    ally.mover.position = [x, y, z + 10]
                self.activate(run, player)
                self.advance(run, 70)
                self.assertEqual(self.vitals(run, ally).temporary_armour, 0)
                self.assertGreater(self.vitals(run, player).temporary_armour, 0)

    def test_damage_consumes_bonus_before_base_and_healing_does_not_refill_it(self):
        run, player, enemy, _ally = self.fixture()
        self.activate(run, player)
        self.advance(run, 220)
        fight = run.match.combat
        vitals = self.vitals(run, player)
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, 40, False, None, run.frame)
        self.assertEqual(vitals.temporary_armour, 65)
        self.assertEqual(vitals.now[world.ARMOUR_POOL], 50)
        self.assertEqual(vitals.heal(1000), 0)
        self.assertEqual(vitals.temporary_armour, 65)

    def test_end_stops_grants_and_individual_cohorts_expire_without_removing_base_armor(self):
        run, player, _enemy, ally = self.fixture()
        self.activate(run, player)
        activated_at = run.frame * 0.016
        self.advance(run, 660)
        self.assertFalse(run.match.combat.brigitte.rally_active(player.body_script.component))
        self.assertEqual(self.vitals(run, player).temporary_armour, 100)
        # At30.2s the first15-point cohort expired; later cohorts remain.
        run.frame = round((activated_at + 30.2) / 0.016)
        run.match.update(run.frame * 0.016, run.frame, frames=False)
        self.assertEqual(self.vitals(run, player).temporary_armour, 85)
        self.assertEqual(self.vitals(run, player).now[world.ARMOUR_POOL], 50)
        # Then run beyond the latest issued cohort's30s life.
        run.frame += 1000
        run.match.update(run.frame * 0.016, run.frame, frames=False)
        self.assertEqual(self.vitals(run, player).temporary_armour, 0)
        self.assertEqual(self.vitals(run, ally).temporary_armour, 0)
        self.assertEqual(self.vitals(run, player).now[world.ARMOUR_POOL], 50)
        self.assertEqual(self.vitals(run, player).total, 200)
        parts = self.vitals(run, player).component((world.ARMOUR_POOL,))[51]
        self.assertEqual(parts[world.POOLS + 1][:4], bytes(4))

    def test_bonus_pool_is_explicit_and_does_not_change_base_pool_maxima(self):
        run, player, _enemy, ally = self.fixture()
        self.activate(run, player)
        self.advance(run, 220)
        vitals = self.vitals(run, ally)
        plain, over = bar(vitals.component(vitals.ids)[51])
        self.assertEqual(plain[world.HEALTH_POOL], [200, 200])
        self.assertEqual(over[world.ARMOUR_POOL], 100)
        self.assertEqual(vitals.most[world.ARMOUR_POOL], 0)
        self.assertEqual(vitals.total, 300)

    def test_native_movement_modifier_gives30_percent_speed_until_end(self):
        def speed(active):
            run, player, _enemy, _ally = self.fixture()
            if active:
                self.activate(run, player)
            for _ in range(70):
                run.frame += 1
                run.match.update(run.frame * 0.016, run.frame)
                player.take_commands([Command(run.frame, forward=127)])
            return player.mover.velocity[2]

        self.assertAlmostEqual(speed(True) / speed(False), 1.3, places=2)

    def test_actual_flail_and_repair_credit_only_effective_damage_and_healing(self):
        run, player, enemy, ally = self.fixture()
        x, y, z = player.mover.position
        enemy.mover.position = [x, y, z + 3]
        ally.mover.position = [x, y, z - 4]
        brigitte_tests.BrigitteTests().swing(run, player, 20)
        self.assertEqual(self.charge(player), 35)
        self.advance(run, 400)
        self.assertEqual(self.charge(player), 35)  # no overhealing credit from Inspire
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        fight._damage(enemy, target, 150, False, None, run.frame)
        x, y, z = player.mover.position
        ally.mover.position, ally.mover.body = [x, y, z + 10], None
        run.step({player: (0, 0, 16)})
        self.advance(run, 220)
        self.assertAlmostEqual(self.charge(player), 145, places=3)
        # Credit is clamped by the live cost, including overkill damage.
        player.body_script.set_ult_charge(2790)
        target = next(t for t in fight._targets() if t.owner is enemy)
        fight._damage(player, target, 1000, False, None, run.frame)
        self.assertEqual(self.charge(player), 2800)

    def test_active_aura_suppresses_damage_heal_and_legacy_passive_charge(self):
        run, player, enemy, _ally = self.fixture()
        self.activate(run, player)
        self.advance(run, 100)  # cast ability ended,10s aura still active
        player.body_script.ult_seconds = 60
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        fight._damage(player, target, 35, False, None, run.frame)
        self.advance(run, 80)
        self.assertEqual(self.charge(player), 0)

    def test_default_passive_uses_authored_one_second_five_point_pulses(self):
        run, player = brigitte_tests.BrigitteTests().fixture()
        self.advance(run, 62)
        self.assertEqual(self.charge(player), 0)
        self.advance(run, 1)
        self.assertEqual(self.charge(player), 5)
        self.advance(run, 62)
        self.assertEqual(self.charge(player), 10)

    def test_passive_reads_live_scalars_at_pulse_and_caps_at_actual_cost(self):
        run, player = brigitte_tests.BrigitteTests().fixture()
        component = player.body_script.component
        self.advance(run, 62)
        component.set(9532, 2.)
        component.set(12918, 3.)
        self.advance(run, 1)
        self.assertEqual(self.charge(player), 30)
        component.set(12918, 0.)
        self.advance(run, 63)
        self.assertEqual(self.charge(player), 30)
        component.set(12918, 1.)
        player.body_script.set_ult_charge(2798)
        self.advance(run, 63)
        self.assertEqual(self.charge(player), 2800)

    def test_passive_known_charge_gates_reset_pending_interval(self):
        for identifier, disabled, enabled in ((2426, False, True), (842, True, False)):
            with self.subTest(identifier=identifier):
                run, player = brigitte_tests.BrigitteTests().fixture()
                self.advance(run, 50)
                player.body_script.component.set(identifier, disabled)
                self.advance(run, 100)
                self.assertEqual(self.charge(player), 0)
                enemy = run.players[1]
                x, y, z = player.mover.position
                enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
                brigitte_tests.BrigitteTests().swing(run, player, 20)
                self.assertEqual(self.vitals(run, enemy).total, 165)
                self.assertEqual(self.charge(player), 0)
                player.body_script.component.set(identifier, enabled)
                self.advance(run, 62)
                self.assertEqual(self.charge(player), 0)
                self.advance(run, 1)
                self.assertEqual(self.charge(player), 5)

    def test_actual_damage_and_repair_use_authored_charge_scalars(self):
        run, player, enemy, ally = self.fixture()
        component = player.body_script.component
        component.set(9532, 2.)
        component.set(13022, 3.)
        x, y, z = player.mover.position
        enemy.mover.position, ally.mover.position = [x, y, z + 3], [x, y, z - 4]
        brigitte_tests.BrigitteTests().swing(run, player, 20)
        self.assertEqual(self.charge(player), 210)
        self.advance(run, 400)
        self.assertEqual(self.charge(player), 210)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        fight._damage(enemy, target, 150, False, None, run.frame)
        x, y, z = player.mover.position
        ally.mover.position, ally.mover.body = [x, y, z + 10], None
        run.step({player: (0, 0, 16)})
        self.advance(run, 220)
        self.assertAlmostEqual(self.charge(player), 870, delta=0.01)

    def test_default_passive_stops_on_actual_death_and_full_rally_aura(self):
        for dies in (False, True):
            with self.subTest(dies=dies):
                run, player, enemy, _ally = self.fixture()
                player.body_script.ult_seconds = None
                run.step({})  # synchronize combat clock before scheduling death/respawn
                if dies:
                    target = next(t for t in run.match.combat._targets() if t.owner is player)
                    run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
                else:
                    self.activate(run, player)
                self.advance(run, 100)
                self.assertEqual(self.charge(player), 0)

    def test_passive_command_gap_and_duplicate_do_not_double_count_time(self):
        _run, player = brigitte_tests.BrigitteTests().fixture()
        script = player.body_script
        frame = script.frame + 125
        script.command(frame, 0)
        self.assertEqual(self.charge(player), 10)
        pending = script.passive_charge_ms
        script.command(frame, 0)
        script.command(frame - 1, 0)
        self.assertEqual(self.charge(player), 10)
        self.assertEqual(script.passive_charge_ms, pending)

    def test_caster_death_stops_new_grants_but_ally_keeps_issued_cohort(self):
        run, player, enemy, ally = self.fixture()
        self.activate(run, player)
        fight = run.match.combat
        ally_vitals = self.vitals(run, ally)
        self.assertEqual(ally_vitals.temporary_armour, 15)
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        self.advance(run, 100)
        self.assertFalse(fight.brigitte.rally_active(player.body_script.component))
        self.assertEqual(ally_vitals.temporary_armour, 15)

    def test_reconstructed_aura_reaches_ally_through_static_wall(self):
        run, player, _enemy, ally = self.fixture()
        x, y, z = player.mover.position
        wall = World([
            (x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
            (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50),
            (x - 5, y, z + 2, x + 5, y + 5, z + 2, x + 5, y, z + 2),
            (x - 5, y, z + 2, x - 5, y + 5, z + 2, x + 5, y + 5, z + 2),
        ])
        run.match.collision_provider = StaticCollisionProvider(wall)
        run.match.collision_world = wall
        self.activate(run, player)
        self.assertEqual(self.vitals(run, ally).temporary_armour, 15)


if __name__ == "__main__":
    unittest.main()
