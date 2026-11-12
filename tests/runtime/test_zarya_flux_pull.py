"""Original0068 actual Flux/Graviton overlap assertions on the single movers."""
import unittest
from test_game_combat import Run, aim
from ow174.game import combat, sigma_flux
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

class ZaryaFluxPullTests(unittest.TestCase):
    def arena(self, *, projected=False):
        run = Run(names=("Sigma", "Target", "Other"))
        sigma, target, other = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        caster = other if projected else target
        self.assertTrue(match.switch_hero(sigma, heroes()[sigma_flux.HERO]))
        self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000068]))
        sigma.team, target.team, other.team = 0, 1, 1 if projected else 0
        for player, position in ((sigma, (0, 1, 0)), (target, (0, 1, 5)), (other, (10, 1, 5))):
            player.mover.position[:] = position
            player.mover.body = None
        return run, sigma, target, other, caster

    @staticmethod
    def effect(target):
        return next((i for i in target.body_script.component.instances.values()
                     if i.graph.index == sigma_flux.FLUX_EFFECT), None)

    def select(self, run, sigma, point, *, charge=True):
        component = sigma.body_script.component
        if charge:
            sigma.body_script.set_ult_charge(component.entity_var(ULT_COST).value())
        for frame in range(81):
            angles = aim(run.match.combat._eye(sigma), point)
            run.step({sigma: (*angles, 32 if frame == 0 else 1 if frame == 80 else 0)})
        return next(i for i in component.instances.values() if i.graph.index == sigma_flux.FLUX)

    def start_graviton_flux_overlap(self):
        run, sigma, target, zarya, _ = self.arena()
        self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.mover.position[:] = [-3, 1, 0]
        fight = run.match.combat
        point = tuple(target.mover.position)
        flux = self.select(run, sigma, point)
        zarya.body_script.set_ult_charge(2100)
        angles = aim(fight._eye(zarya), (2, 1, 5))
        run.step({zarya: (*angles, 32)})
        return run, sigma, target, zarya, flux, point

    def test_graviton_preserves_flux_lift_and_grounded_slam_on_same_player(self):
        run, sigma, target, zarya, flux, point = self.start_graviton_flux_overlap()
        fight = run.match.combat
        peak = point[1]
        lifted_while_pulled = False
        for _ in range(420):
            previous_x = target.mover.position[0]
            run.step({})
            x, y, _ = target.mover.position
            peak = max(peak, y)
            if y > point[1] + 1 and fight.zarya_gravitons.fields:
                lifted_while_pulled |= x > previous_x
                self.assertTrue(target.mover.airborne)
                self.assertEqual(sigma.stats.values.get(combat.stats.HERO_DAMAGE, 0), 50)
        self.assertEqual(flux.vars[375].value(), (expr.Entity(target.body),))
        self.assertTrue(lifted_while_pulled)
        self.assertGreater(peak, point[1] + 1)
        self.assertAlmostEqual(target.mover.position[1], point[1], delta=0.01)
        self.assertEqual(sigma.stats.values[combat.stats.HERO_DAMAGE], 250)
        field_damage = zarya.stats.values[combat.stats.HERO_DAMAGE]
        self.assertGreater(field_damage, 0)
        self.assertLess(field_damage, 20)
        self.assertIsNone(self.effect(target))
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertFalse(fight.zarya_gravitons.pulled)
