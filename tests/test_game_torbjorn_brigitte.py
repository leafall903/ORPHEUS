import os
import unittest
from dataclasses import replace

from ow174.game import stats
from ow174.game.collision import flat_world
from ow174.game.content import SOLDIER, heroes
from test_game_combat import aim, in_world
import test_game_torbjorn as projectile_fixture
import test_game_torbjorn_turret as turret_fixture


def target(run, actor):
    return next(item for item in run.match.combat._targets() if item.owner is actor)


def place(actor, z):
    actor.mover.position[:] = (0, 0, z)
    actor.mover.velocity = (0, 0, 0)
    actor.mover.body = None


def arena():
    run, torb, brig = projectile_fixture.TorbjornProjectileTests().arena(15)
    assert run.match.switch_hero(brig, heroes()[0x02E0000000000195])
    world = flat_world(0)
    run.match.collision_world = world
    for actor, z in ((torb, 15), (brig, 0)):
        actor.mover.use(world=world)
        place(actor, z)
    for _ in range(35):
        run.step({})
    fight = run.match.combat
    whip = next(i for i in brig.body_script.component.instances.values() if i.graph.index == 0x1259)
    return run, torb, brig, fight, whip


def press(run, brig, aim_in):
    run.step({brig: (*aim_in, 8)})
    for _ in range(45):
        run.step({brig: (*aim_in, 0)})


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared Torbjorn bundle")
class TorbjornBrigitteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        turret_fixture.TorbjornTurretTests.setUpClass()
        cls.addClassCleanup(turret_fixture.TorbjornTurretTests.tearDownClass)

    def test_primary_and_quick_melee_cleave_once_per_target_per_swing(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                run, torb, brig, fight, _ = arena()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                torb.team, brig.team = 1, 2
                place(torb, 3)
                others = {}
                for slot, (name, team, point) in enumerate((
                        ("side", 1, (2, 0, 3)), ("back", 1, (0, 0, -3)),
                        ("far", 1, (0, 0, 8)), ("ally", 2, (-2, 0, 3))), start=3):
                    actor = match.add_player(slot, name, SOLDIER, team, False)
                    in_world(match, actor)
                    run.players.append(actor)
                    assert match.switch_hero(actor, heroes()[SOLDIER])
                    actor.team = team
                    actor.mover.use(world=match.collision_world)
                    actor.mover.position[:] = point
                    actor.mover.body = None
                    others[name] = actor
                for _ in range(35):
                    run.step({})
                run.step({torb: (0, 0, 16)})
                hp = fight.vitals_of(target(run, torb))
                assert hp.total == 350
                pool = {name: fight.vitals_of(target(run, actor)) for name, actor in others.items()}
                initial = {name: health.total for name, health in pool.items()}
                weapon = next(i for i in brig.body_script.component.instances.values() if i.graph.index == 0x1248)
                for swing in (1, 2):
                    for elapsed in range(70):
                        held = button if elapsed == 0 else 0
                        run.step({brig: (0, 0, held)})
                    counters = {index: weapon.states[index].counter for index in (7, 8) if index in weapon.states}
                    assert sum(counters.values()) == swing
                    delta = {name: initial[name] - health.total for name, health in pool.items()}
                    assert delta == {"side": 35 * swing, "back": 0, "far": 0, "ally": 0}, (button, delta)
                    assert hp.over_armour.amount == 100 - 30 * swing and hp.total == 350 - 30 * swing
                    assert brig.stats.value(stats.HERO_DAMAGE) == 65 * swing

    def test_whip_shot_cooldown_rejects_early_repeat(self):
        run, torb, brig, fight, whip = arena()
        hp = fight.vitals_of(target(run, torb))
        aim_in = aim(fight._eye(brig), (0, .9, 15))
        press(run, brig, aim_in)
        assert hp.total == 185 and whip.states[6].counter == 1 and whip.vars[61].value() > 0
        place(torb, 15)
        press(run, brig, aim_in)
        assert hp.total == 185 and whip.states[6].counter == 1
        for _ in range(300):
            if whip.vars[61].value() == 0:
                break
            run.step({})
        assert whip.vars[61].value() == 0 and whip.states[6].counter == 1
        place(torb, 15)
        press(run, brig, aim_in)
        assert hp.total == 115 and whip.states[6].counter == 2

    def test_whip_shot_cancels_on_death_or_hero_switch(self):
        for case in ("windup death", "outbound death", "outbound switch"):
            with self.subTest(case=case):
                run, torb, brig, fight, whip = arena()
                hp = fight.vitals_of(target(run, torb))
                brig_hp = fight.vitals_of(target(run, brig))
                aim_in = aim(fight._eye(brig), (0, .9, 15))
                aim_out = aim(fight._eye(torb), (0, .9, 0))
                if case == "outbound death":
                    run.step({brig: (*aim_in, 8)})
                    for _ in range(5):
                        run.step({brig: (*aim_in, 0)})
                if case.endswith("death"):
                    brig_hp.damage(brig_hp.total - 1, armour_reduction=False)
                    assert brig_hp.total == 1
                    run.step({torb: (*aim_out, 1)})
                    assert any(r.shooter is torb for r in fight.rockets)
                    if case == "windup death":
                        for _ in range(10):
                            run.step({})
                    assert not brig_hp.dead
                run.step({brig: (*aim_in, 0 if case == 'outbound death' else 8), torb: (0, 0, 16)})
                assert hp.total == 350
                saw_flight = False
                for _ in range(35):
                    if fight.brigitte_shields.whips:
                        saw_flight = True
                        if case == "outbound switch":
                            old = brig.body
                            assert run.match.switch_hero(brig, heroes()[SOLDIER]) and brig.body != old
                            break
                    if brig_hp.dead:
                        break
                    run.step({brig: (*aim_in, 0)})
                if case.endswith("death"):
                    assert brig_hp.dead and torb.stats.value(stats.HERO_DAMAGE) == 1
                assert saw_flight == (case != "windup death"), (case, saw_flight, whip.states.get(6))
                for _ in range(40):
                    run.step({})
                assert hp.total == 350 and hp.over_armour.amount == 100 and torb.mover.position[2] == 15
                assert not fight.brigitte_shields.whips
