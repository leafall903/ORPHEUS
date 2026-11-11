"""World host for Symmetra's authored 2658 Sentry Turret."""

from dataclasses import dataclass

from ow174.game import pools, sigma, symmetra_beam
from ow174.game.script import expr, graph, runtime

BODY = 0x0400000000002658
LAUNCH, ATTACH, BEAM, SLOW = 0x157E, 0x157F, 0x1590, 0x01E7


@dataclass
class Sentry:
    owner: object
    bot: object
    direction: tuple
    speed: float
    last_tick: int
    attached_at: float | None = None
    target: int | None = None
    slow: tuple | None = None


def launch(fight, owner, command, state):
    """Consume the actual turret volley, which creates a summon instead of a hit."""
    from ow174.game import npc
    from ow174.game.combat import aim

    record = graph.entity_body(BODY)
    if record is None or any(graph.graph(index) is None for index in (ATTACH, BEAM, SLOW)):
        raise ValueError("Sentry Turret requires its authored actor and graph bundle")
    row = next(item for item in record["graphs"] if item["graph"] == pools.BODY_GRAPH)
    values = pools._numbers(graph.graph(pools.BODY_GRAPH).fields["m_publicSchema"]["m_entries"])
    values.update(pools._numbers(row.get("m_1EB5A024")))
    definition = npc.Definition(
        symmetra_beam.HERO, BODY, "Sentry Turret", values[pools.HEALTH_VAR],
        values.get(pools.ARMOUR_VAR, 0), values.get(pools.SHIELDS_VAR, 0),
    )
    speed = expr.to_float(state.evaluate(state.node.field("m_projectileSpeed"), watch=False))
    maximum = expr.to_int(state.instance.find_var(graph.INSTANCE, 181).value())
    if speed <= 0 or maximum <= 0:
        raise ValueError("invalid Sentry Turret flight or ownership limit")
    bot = npc.spawn(fight.match, definition, fight._eye(owner), team=owner.team, summoned=True)
    fight.sentries.append(Sentry(owner, bot, aim(*command.aim()), speed, fight.tick))
    owned = [item for item in fight.sentries if item.owner is owner]
    for old in owned[:-maximum]:
        retire(fight, old)


def _clear_target(sentry):
    if sentry.slow is not None:
        component, aura = sentry.slow
        if component.instances.get(aura.id) is aura:
            component.destroy(aura)
            component.run_now()
        sentry.slow = None
    sentry.target = None


def retire(fight, sentry):
    from ow174.game import npc

    _clear_target(sentry)
    npc.remove_summoned(fight.match, sentry.bot)
    fight.sentries.remove(sentry)


def release_target(fight, entity):
    for sentry in fight.sentries:
        if sentry.target == entity:
            _clear_target(sentry)


def _fly(fight, sentry, elapsed):
    from ow174.game.combat import TICK, _along, _dot

    bot = sentry.bot
    duration = elapsed * TICK if bot.moved_tick is not None else TICK
    distance = sentry.speed * duration
    contact = fight._collision().cast_ray(bot.position, sentry.direction, distance)
    if contact is None:
        bot.position = _along(bot.position, sentry.direction, distance)
        bot.velocity = tuple(axis * sentry.speed for axis in sentry.direction)
    else:
        side = -1 if _dot(contact.normal, sentry.direction) > 0 else 1
        bot.position = _along(contact.point, contact.normal, side * .01)
        bot.velocity = (0, 0, 0)
        sentry.attached_at = fight.now - duration + contact.distance / sentry.speed
        bot.body_script.component.set(478, True)
    bot.moved_tick = fight.tick


def _target(fight, sentry, reach):
    from ow174.game.combat import segment_distance

    origin = sentry.bot.position
    best = None
    for target in fight._enemies(sentry.owner):
        if sigma.body_protection(target.owner):
            continue
        distance = max(0.0, segment_distance(origin, *target.segment()) - target.radius)
        if (distance <= reach and (best is None or distance < best[0])
                and fight._unobstructed(origin, target)
                and fight._blast_passes_barriers(sentry.owner, origin, target, None)):
            best = distance, target
    return None if best is None else best[1]


def _slow(sentry, target, beam):
    script = target.owner.body_script
    if (script is None or script.component.strict or sigma.particle_barrier(target.owner) is not None
            or sigma.fortify(target.owner) is not None):
        _clear_target(sentry)
        sentry.target = target.entity
        return
    component = script.component
    if sentry.target != target.entity or sentry.slow is None or sentry.slow[0] is not component:
        _clear_target(sentry)
        sentry.target = target.entity
        aura = component.create(graph.graph(SLOW))
        percent = beam.evaluate(beam.graph.state(9).field("m_params")[0]["m_value"])
        if runtime.send_instance_message(aura, 0x024000000000000F,
                                         {0x002A: percent}, sentry.bot.entity) != 1:
            component.destroy(aura)
            raise ValueError("Sentry slow aura has no authored activation entry")
        component.run_now()
        sentry.slow = component, aura


def update(fight, *, endpoint):
    from ow174.game.combat import TICK

    for sentry in tuple(fight.sentries):
        bot, owner = sentry.bot, sentry.owner
        if (fight.match.ended or bot.dead or bot not in fight.match.bots or owner.gone
                or owner.hero.guid != symmetra_beam.HERO or bot.body_script is None):
            retire(fight, sentry)
            continue
        if bot.moved_tick is None and not endpoint:
            continue
        elapsed = fight.tick - sentry.last_tick
        if elapsed <= 0:
            continue
        sentry.last_tick = fight.tick
        if sentry.attached_at is None:
            _fly(fight, sentry, elapsed)
        script = bot.body_script
        snapshot = script.advance_live(fight.tick, bot.position)
        if snapshot is not None:
            for viewer in fight.match.players:
                if viewer.client is not None and viewer.spawned:
                    viewer.client.queue_bot_live(bot.entity, snapshot)
        if sentry.attached_at is None:
            continue
        attach = next(i for i in script.component.instances.values() if i.graph.index == ATTACH)
        charge = attach.state(4)
        delay = expr.to_float(charge.evaluate(charge.node.field("m_5BEFF040"), watch=False))
        ready = sentry.attached_at + delay
        if fight.now <= ready:
            continue
        beam = next(i for i in script.component.instances.values() if i.graph.index == BEAM)
        target = _target(fight, sentry, expr.to_float(beam.find_var(graph.INSTANCE, 1648).value()))
        if target is None:
            _clear_target(sentry)
            continue
        _slow(sentry, target, beam)
        modify = beam.graph.state(1).field("m_modifyHealth")
        amount = expr.to_float(beam.evaluate(modify["m_amount"])) * min(TICK, fight.now - ready)
        loadout = beam.evaluate(modify["m_loadout"])
        fight._damage(owner, target, amount, False, loadout.guid if isinstance(loadout, expr.Asset) else None,
                      fight.tick, world_tick=fight.tick, beam=True)
        if fight.vitals_of(target).dead:
            _clear_target(sentry)
