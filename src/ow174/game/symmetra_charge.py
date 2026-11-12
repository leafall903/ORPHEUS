"""Server context for Symmetra and Genji's shared 104319 ultimate charge graph."""

from contextlib import contextmanager

from ow174.game import bots
from ow174.game.deflection import GENJI
from ow174.game.script import expr, runtime

HERO = 0x02E0000000000016
AUTHORED_HEROES = frozenset((HERO, GENJI))
GRAPH = 0x0B8B
CONFIG_CHANGED = 0x024000000000002D
DAMAGE_DEALT = 0x0240000000000012

# Character bodies observed in the build's mover/003 context inventory.
# The scoped target queries below only ask about three of their direct tags.
# See the lane's charge evidence for the immutable XML inventory and 0407 supplement.
CHARACTER_BODIES = frozenset(0x0400000000000000 | index for index in (
    0x0001, 0x001C, 0x0022, 0x0024, 0x0032, 0x00E4, 0x0141, 0x01C0, 0x01FD,
    0x0216, 0x0225, 0x028F, 0x02A3, 0x0380, 0x03BF, 0x03CF, 0x03FC, 0x0407, 0x040A,
    0x08BA, 0x121C, 0x1235, 0x1403, 0x1424, 0x143E, 0x146E, 0x1498, 0x14A9,
    0x14D2, 0x14ED, 0x1B1B, 0x1C46, 0x200B, 0x251C, 0x2593, 0x26F0, 0x28B6,
    0x29A6, 0x2AB1, 0x1D40, 0x2279, 0x1E7D, 0x1EBD, 0x1EBB,
))
RELEVANT_TAGS = {
    0x11E: frozenset((0x040000000000028F,)),
    0x2ED: frozenset(0x0400000000000000 | i for i in (0x1403, 0x143E, 0x146E, 0x14A9, 0x14D2, 0x14ED)),
    0x3C3: frozenset(),
}


def _body(target):
    if target is None:
        return None
    actor = target.owner
    if isinstance(actor, bots.Bot):
        return actor.body
    return getattr(getattr(actor, 'hero', None), 'body', None)


def native(world, cfg, instance):
    if (world.hero != HERO or world.match is None or instance is None
            or instance.component.strict or instance.graph.index != GRAPH):
        return NotImplemented
    cls = cfg.get('$')
    if cls not in ('STU_F7262B36', 'STU_AC829876', 'STU_A58C1E63', 'STU_25ECB55F'):
        return NotImplemented
    owner = next((p for p in world.match.players if p.has_body and not p.gone and p.body == world.body), None)
    if cls == 'STU_F7262B36':
        if owner is not None and instance.evaluate(cfg.get('m_801B298F')) == expr.Entity(owner.body):
            return expr.Entity(owner.entity)
        return NotImplemented
    query = cfg.get('m_target') if cls == 'STU_A58C1E63' else cfg.get('m_entity')
    entity = expr.to_entity(instance.evaluate(query))
    target = next((t for t in world.match.combat._targets() if t.entity == entity), None)
    body = _body(target)
    if cls == 'STU_AC829876':
        flags = {k: v for k, v in (cfg.get('m_filter') or {}).items() if k != '$' and v not in (0, None)}
        if flags == {'m_84065FD1': 1}:
            return body in CHARACTER_BODIES
    if cls == 'STU_25ECB55F':
        tag = expr.guid_of(cfg.get('m_88B0050A')) & 0xFFFFFFFFFFFF
        if body in CHARACTER_BODIES and tag in RELEVANT_TAGS:
            return body in RELEVANT_TAGS[tag]
    if (cls == 'STU_A58C1E63' and owner is not None and body in CHARACTER_BODIES
            and instance.evaluate(cfg.get('m_source')) == expr.Entity(owner.body)):
        return target.owner is not owner and (world.match.game_map.free_for_all or target.team != owner.team)
    return NotImplemented


def _instance(component, index):
    return next((i for i in component.instances.values() if i.graph.index == index), None)


@contextmanager
def _at(component, now):
    command_time = component.now
    component.now = now
    try:
        yield
    finally:
        component.now = command_time


def _clock(script):
    # Older Genji snapshots used command-only charge and have no world clock.
    if not hasattr(script, "charge_clock"):
        script.charge_clock = script.world.match.combat.tick, script.component.now
    return script.charge_clock


def sync(script):
    """Synchronize the graph without moving a world-advanced timer backward."""
    component = script.component
    _, charged_through = _clock(script)
    with _at(component, max(component.now, charged_through)):
        _sync(script)


def advance_idle(player, tick, tick_ms, *, before_combat=False):
    """Service the authored charge graph without consuming a command or a pose."""
    script = player.body_script
    if (script is None or script.hero != HERO or script.retired
            or script.component.fault is not None or not player.spawned or player.gone):
        return
    previous_tick, previous_time = _clock(script)
    if tick <= previous_tick:
        return
    component = script.component
    target = max(component.now, previous_time + (tick - previous_tick) * tick_ms)
    charge = _instance(component, GRAPH)
    if charge is not None:
        # A skipped interval preceding combat still has its previous cover.
        # At an actual combat tick, gate changes take effect at its start.
        start = previous_time + 1 if before_combat else target - tick_ms + 1
        gate = component.entity_var(2426, create=False)
        if gate is None or gate.value() is None:
            start = previous_time
        with _at(component, max(component.now, start)):
            _sync(script)
        # Step at each authored deadline so recurring one-second waits retain
        # their cadence across a sparse update.
        with component.operation():
            while not charge.stopped and charge.queue and charge.queue[0].time <= target:
                with _at(component, max(component.now, charge.queue[0].time)):
                    charge.update()
    script.charge_clock = tick, target


def _sync(script):
    """Supply mode activation and pause while the authored ultimate is in use."""
    component = script.component
    gate = component.entity_var(2426, create=False)
    changed = False
    if script.ult_seconds <= 0 or component.owner_dead:
        if gate is not None and gate.value() is not None:
            component.set(2426, None)
            changed = True
    elif gate is None or gate.value() is None:
        config = _instance(component, 0x01C7)
        if config is not None and runtime.send_instance_message(config, CONFIG_CHANGED, {}, component.entity):
            config.update()
            changed = True
    # 156B/0 spends the charge and waits for deployment. The Pet at 17
    # covers the handoff to the world actor. The parent ability can remain
    # flagged RUNNING until another command even after the wall retires.
    if script.hero == HERO:
        wall_cast = any(
            i.graph.index == 0x156B
            and any(s.active and s.node.pos in (0, 17) for s in i.states.values())
            for i in component.instances.values()
        )
        deployed = any(w.owner.body == script.entity
                       for w in script.world.match.combat.photon_barriers.actors.values())
        suppress = wall_cast or deployed
        suppression = component.entity_var(842, create=False)
        if bool(suppression is not None and suppression.base) != suppress:
            component.set(842, suppress)
            changed = True
    if changed:
        charge = _instance(component, GRAPH)
        if charge is not None:
            charge.update()


def damage(shooter, target, dealt):
    if isinstance(shooter, bots.Bot):
        return
    script = getattr(shooter, 'body_script', None)
    if (script is None or script.hero != HERO or script.entity != shooter.body
            or script.retired or script.ult_seconds <= 0
            or script.component.owner_dead or script.component.fault is not None
            or _body(target) not in CHARACTER_BODIES):
        return
    # The combat host provides resolved pool loss. Native tagged
    # modifier buckets and combat-tag production are outside this static bridge.
    charge = _instance(script.component, GRAPH)
    if charge is None:
        return
    from ow174.game.combat import TICK

    component = script.component
    charged_tick, charged_through = _clock(script)
    elapsed_ticks = script.world.match.combat.tick - charged_tick
    tick_start = charged_through + (elapsed_ticks - 1) * round(TICK * 1000) + 1
    with _at(component, max(component.now, charged_through, tick_start)):
        _sync(script)
        runtime.send_instance_message(charge, DAMAGE_DEALT, {
            0x13: expr.f32(dealt), 0x15: expr.Entity(target.entity), 0x3D: (), 0x624: (),
        }, shooter.body)
        # Do not tick the weapon's pending TrackRay events outside its frame.
        charge.update()
