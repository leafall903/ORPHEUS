"""Static Ana acceptance on placed 104319 health-pack graphs."""

import os
import unittest
from pathlib import Path

import test_game_ana_orisa_barrier as placement
from test_game_ana import set_static_wall
from test_game_combat import Run, aim

from ow174.game import stats
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import flat_world
from ow174.game.content import PRACTICE_RANGE, find_hero
from ow174.game.script import expr
from ow174.game.script.driver import ULT_COST
from ow174.game.server_snapshot import _Graph

SMALL, LARGE = 0x0400000000000FC3, 0x0400000000000FC2


@unittest.skipUnless(Path(os.environ.get("ORPHEUS_HEALTH_PACK_DATA", "")).is_file(),
                     "requires the extracted 104319 health-pack placement bundle")
class AnaHealthPackTests(unittest.TestCase):
    def arena(self, *, definition=SMALL, recipient="Soldier: 76", allied=True):
        run = Run(PRACTICE_RANGE, ("Ana", "Recipient", "Sombra"))
        ana, target, sombra = run.players
        for player, name in ((ana, "Ana"), (target, recipient), (sombra, "Sombra")):
            self.assertTrue(run.match.switch_hero(player, find_hero(name)))
        ana.team = 0
        target.team = 0 if allied else 1
        sombra.team = 0
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        fight = run.match.combat
        self.assertEqual(len(fight.health_packs), 4)
        pack = next(pack for pack in fight.health_packs.values() if pack.definition == definition)
        run.match.collision_world = flat_world(pack.position[1])
        self.place(ana, pack, -6, x_offset=-2)
        self.place(target, pack, -3)
        self.place(sombra, pack, -4, x_offset=2)
        for _ in range(45):
            run.step({})
        for body in fight._targets():
            fight.vitals_of(body)
        pickup = next(i for i in pack.component.instances.values() if i.graph.index == 0x46)
        self.assertEqual(self.ready(pickup), 1)
        return run, ana, target, sombra, pack, pickup

    @staticmethod
    def place(player, pack, z_offset, *, x_offset=0):
        x, y, z = pack.position
        placement.AnaOrisaBarrierTests.place(player, (x + x_offset, y, z + z_offset))

    @staticmethod
    def ready(pickup):
        return expr.to_float(pickup.vars[467].value())

    @staticmethod
    def refill(pickup):
        return expr.to_float(pickup.vars[384].value())

    @staticmethod
    def cast(run, player, point, button, *, held=False, frames=65):
        direction = aim(run.match.combat._eye(player), point)
        for frame in range(frames):
            run.step({player: (*direction, button if held or frame == 0 else 0)})

    def test_neutral_packs_apply_authored_amounts_and_refill(self):
        for definition, amount, refill in ((SMALL, 75, 10), (LARGE, 250, 15)):
            with self.subTest(definition=definition):
                run, ana, tank, _, pack, pickup = self.arena(definition=definition, recipient="Roadhog")
                fight = run.match.combat
                vitals = fight.vitals[tank.body]
                vitals.damage(300)
                before = vitals.total
                self.place(tank, pack, 0)
                run.step({})
                self.assertEqual(vitals.total, before + amount)
                self.assertEqual(ana.stats.value(stats.HEALING), 0)
                self.assertLess(self.ready(pickup), 1)
                self.assertEqual(self.refill(pickup), refill)
                self.place(tank, pack, -3)
                used = pickup.state(2).start / 16
                for offset in (-2, 2):
                    tick = int(used + refill / .016 + offset)
                    run.match.update(tick * .016, tick, frames=False)
                    self.assertEqual(self.ready(pickup) == 1, offset > 0)

    def test_actual_ana_grenade_blocks_pickup_and_restores_it_on_expiry(self):
        run, ana, target, _, pack, pickup = self.arena(allied=False)
        fight = run.match.combat
        vitals = fight.vitals[target.body]
        self.cast(run, ana, tuple(target.mover.position), 16)
        self.assertEqual(vitals.total, 140)
        deadline = fight.healing.anti[target.body].end
        self.assertGreater(deadline, fight.tick)
        self.place(target, pack, 0)
        run.step({})
        self.assertEqual(vitals.total, 140)
        self.assertEqual(self.ready(pickup), 1)
        while fight.tick < deadline:
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertLess(self.ready(pickup), 1)

    def test_actual_ana_grenade_amplifies_pickup_without_crediting_ana(self):
        run, ana, tank, _, pack, pickup = self.arena(recipient="Roadhog")
        fight = run.match.combat
        vitals = fight.vitals[tank.body]
        vitals.damage(300)
        before = vitals.total
        self.cast(run, ana, tuple(tank.mover.position), 16)
        self.assertEqual(vitals.total, before + 100)
        self.assertGreater(fight.healing.amp[tank.body].end, fight.tick)
        self.assertEqual(ana.stats.value(stats.HEALING), 100)
        self.place(tank, pack, 0)
        run.step({})
        self.assertAlmostEqual(vitals.total, before + 100 + 75 * 1.5)
        self.assertEqual(ana.stats.value(stats.HEALING), 100)
        self.assertLess(self.ready(pickup), 1)

    def test_actual_hack_and_emp_claim_pack_and_gate_ana_by_team(self):
        for button in (2, 32):
            with self.subTest(button=button):
                run, ana, _, sombra, pack, pickup = self.arena()
                fight = run.match.combat
                self.place(ana, pack, -6, x_offset=-5)
                if button == 32:
                    script = sombra.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                x, y, z = pack.position
                self.cast(run, sombra, (x, y + .35, z), button, held=button == 2)
                self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(sombra.body))
                applied, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
                self.assertEqual(applied.source_entity(), sombra.body)
                self.assertEqual(self.refill(pickup), 2.5)
                vitals = fight.vitals[ana.body]
                vitals.damage(100)
                self.place(ana, pack, 0)
                ana.team = 1
                run.step({})
                self.assertEqual(vitals.total, 100)
                self.assertEqual(self.ready(pickup), 1)
                ana.team = sombra.team
                run.step({})
                self.assertEqual(vitals.total, 175)
                self.assertLess(self.ready(pickup), 1)
                self.assertNotIn(pack.entity, fight.vitals)

    def test_enemy_emp_hits_ana_and_claims_the_pack_in_one_pulse(self):
        run, ana, _, sombra, pack, pickup = self.arena()
        fight = run.match.combat
        ana.team = 1
        script = sombra.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        x, y, z = pack.position
        self.cast(run, sombra, (x, y + .35, z), 32)
        lock, = fight.sombra_controls.locks[ana.body]
        self.assertEqual(lock.application.source_entity(), sombra.body)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(sombra.body))
        self.assertEqual(self.refill(pickup), 2.5)
        self.assertNotIn(pack.entity, fight.sombra_controls.locks)

    def test_pack_claim_expires_and_preserves_the_authored_refill(self):
        run, ana, rival, sombra, pack, pickup = self.arena()
        fight = run.match.combat
        x, y, z = pack.position
        point = (x, y + .35, z)
        self.cast(run, sombra, point, 2, held=True)
        applied, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        self.assertTrue(run.match.switch_hero(rival, sombra.hero))
        rival.team = 1
        self.place(rival, pack, -4)
        rival.body_script.set_ult_charge(rival.body_script.component.entity_var(ULT_COST).value())
        self.cast(run, rival, point, 32)
        self.assertIn(applied.id, pack.component.instances)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(sombra.body))
        self.assertEqual(self.refill(pickup), 2.5)
        self.place(ana, pack, 0)
        ana.team = 1
        vitals = fight.vitals[ana.body]
        vitals.damage(100)
        tick = run.frame + 3600
        run.match.update(tick * .016, tick, frames=False)
        self.assertEqual(vitals.total, 100)
        self.assertIn(applied.id, pack.component.instances)
        tick += 160
        run.match.update(tick * .016, tick, frames=False)
        self.assertFalse(pack.component.entity_var(6396).value())
        self.assertNotIn(applied.id, pack.component.instances)
        self.assertEqual(vitals.total, 175)
        self.assertEqual(self.refill(pickup), 10)

    def test_full_health_and_cover_do_not_consume_a_ready_pack(self):
        run, ana, _, _, pack, pickup = self.arena()
        self.place(ana, pack, 0)
        run.step({})
        self.assertEqual(self.ready(pickup), 1)
        vitals = run.match.combat.vitals[ana.body]
        vitals.damage(100)
        self.place(ana, pack, -.6)
        set_static_wall(run, ana, .3)
        run.step({})
        self.assertEqual(vitals.total, 100)
        self.assertEqual(self.ready(pickup), 1)

    def test_active_pack_vm_and_placement_digest_enter_stored_domain(self):
        run, _, _, sombra, pack, _ = self.arena()
        fight = run.match.combat
        x, y, z = pack.position
        self.cast(run, sombra, (x, y + .35, z), 2, held=True)
        active = snapshot_digest(export_component_vm(pack.component))
        owner = pack.component.entity_var(6396)
        self.assertEqual(owner.value(), expr.Entity(sombra.body))
        applied, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        pack.component.destroy(applied)
        self.assertNotEqual(snapshot_digest(export_component_vm(pack.component)), active)

        def stored_pack():
            capture = _Graph({"match": run.match, "fight": fight}, None, {})
            capture.value({"pack": pack, "source_digest": fight.health_pack_digest})
            return capture.finish()

        first = stored_pack()
        original = pack.position
        pack.position = (x + 1, y, z)
        self.assertNotEqual(stored_pack(), first)
        pack.position = original
        self.assertEqual(stored_pack(), first)

    def test_pack_cast_rejects_cover_range_and_hack_aim_miss(self):
        cases = ((2, "cover"), (32, "cover"), (2, "range"), (32, "range"), (2, "aim"))
        for button, gate in cases:
            with self.subTest(button=button, gate=gate):
                run, _, _, sombra, pack, pickup = self.arena()
                x, y, z = pack.position
                self.place(sombra, pack, -36 if gate == "range" else -4)
                if gate == "cover":
                    set_static_wall(run, sombra, 2)
                if button == 32:
                    script = sombra.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.cast(run, sombra, (x + (3 if gate == "aim" else 0), y + .35, z),
                          button, held=button == 2)
                self.assertFalse(pack.component.entity_var(6396).value())
                self.assertEqual(self.refill(pickup), 10)
                self.assertFalse(any(i.graph.index in (0x0B1F, 0x0B32)
                                     for i in pack.component.instances.values()))

    def test_hacked_large_pack_refills_once_and_heals_ana_again(self):
        run, ana, _, sombra, pack, pickup = self.arena(definition=LARGE)
        fight = run.match.combat
        x, y, z = pack.position
        self.cast(run, sombra, (x, y + .35, z), 2, held=True)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(sombra.body))
        self.assertEqual(self.refill(pickup), 3.75)
        vitals = fight.vitals[ana.body]
        vitals.damage(100)
        self.place(ana, pack, 0)
        run.step({})
        self.assertEqual(vitals.total, 200)
        first = run.frame
        vitals.damage(100)
        before_ready = first + 200
        run.match.update(before_ready * .016, before_ready, frames=False)
        self.assertEqual(vitals.total, 100)
        self.assertLess(self.ready(pickup), 1)
        refill_tick = first + 240
        run.match.update(refill_tick * .016, refill_tick, frames=False)
        self.assertEqual(vitals.total, 200)
        self.assertLess(self.ready(pickup), 1)
        run.match.update(refill_tick * .016, refill_tick, frames=False)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(sombra.stats.value(stats.HEALING), 0)
