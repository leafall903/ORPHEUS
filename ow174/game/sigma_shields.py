"""Host the selected 1D02/1D03 Grasp shield graph."""

from ow174.game.script import expr, graph
from ow174.game.world import SHIELDS_POOL

HERO = 0x02E000000000023B
SHIELD_TAG = 0x5847
RECOVERY_DELAY = 3.0
RECOVERY_RATE = 30.0


def _shield_kind(cfg):
    return (expr.guid_of((cfg or {}).get("m_identifier")) & 0xFFFF) == SHIELD_TAG


def pool_amount(fight, cfg, instance):
    if (cfg.get("$") != "STU_783B4CF0" or instance.graph.index != 0x1D03
            or (cfg.get("m_7E8B666C") or {}).get("m_7E8B666C") != 2
            or not _shield_kind(cfg.get("m_7BE02F89"))
            or instance.evaluate(cfg.get("m_entity")) != expr.Entity(instance.component.entity)):
        return NotImplemented
    vitals = fight.vitals.get(instance.component.entity)
    return expr.f32(vitals.temporary_shield_total) if vitals is not None else 0.0


def play(fight, instance, node):
    if (instance.graph.index != 0x1D02 or node.pos != 34
            or instance.evaluate(node.field("m_script")) != expr.Asset(0x0580000000001D03)
            or instance.evaluate(node.field("m_targets")) != expr.Entity(instance.component.entity)):
        return False
    entity = instance.component.entity
    target = next((target for target in fight._targets() if target.entity == entity), None)
    if target is None or fight.vitals_of(target).dead:
        return True
    definition = graph.graph(0x1D03)
    if definition is None:
        raise ValueError("Grasp conversion requires the original 1D03 graph")
    params = tuple((expr.guid_of(row["m_B5051BCE"]), instance.evaluate(row["m_value"]))
                   for row in node.field("m_params") or ())
    instance.component.play_loaded_graph(
        definition, source=entity, instigator=entity, params=params, policy=1,
    )
    return True


def update(fight, previous, tick_seconds):
    for target in fight._targets():
        owner = target.owner
        if getattr(getattr(owner, "hero", None), "guid", None) != HERO:
            continue
        script = owner.body_script
        if script is None or script.retired:
            continue
        vitals = fight.vitals_of(target)
        before = vitals.total, vitals.maximum
        component = script.component
        active = {}
        retire = set()
        decay = None
        for instance in list(component.instances.values()):
            if instance.graph.index == 0x1D02:
                state = instance.states.get(instance.graph.node(70).state)
                if state is not None and state.active:
                    decay = state
            if instance.graph.index != 0x1D03 or not instance.initialized:
                continue
            states = [s for s in instance.states.values() if s.active and not s.ending
                      and s.node.cls == "STUStatescriptStateHealthPool"
                      and s.node.field("m_7E8B666C") == 2
                      and _shield_kind(s.node.field("m_7BE02F89"))]
            if not states or vitals.dead:
                retire.add(instance)
            else:
                active.update({(instance.id, s.index, s.counter): s for s in states})

        for key, pool in vitals.temporary_shields.items():
            if key not in active and pool.maximum > 0:
                pool.current = pool.maximum = 0.0
                vitals.changed.add(SHIELDS_POOL)
        for key, state in active.items():
            if key not in vitals.temporary_shields:
                amount = expr.to_float(state.evaluate(state.node.field("m_amount"), watch=False))
                vitals.add_temporary_shield(key, amount)
        if decay is not None and vitals.temporary_shield_total > 0:
            seconds = max(0, fight.tick - max(previous, decay.start / script.frame_ms)) * tick_seconds
            cfg = (decay.node.field("m_modifyHealth") or {}).get("m_amount")
            rate = expr.to_float(decay.evaluate(cfg, watch=False))
            vitals.spend_temporary_shields(max(0.0, seconds * rate))
        for key, state in active.items():
            pool = vitals.temporary_shields.get(key)
            if pool is None or pool.maximum == 0:
                retire.add(state.instance)
        for instance in sorted(retire, key=lambda instance: instance.id):
            component.destroy(instance)
        if before != (vitals.total, vitals.maximum):
            fight._sync_body_health(owner, vitals)

def recover_base(fight, tick_seconds):
    if fight.match.ended:
        return
    for actor in fight.match.players:
        if actor.hero.guid != HERO or not fight.match._shown(actor):
            continue
        vitals = fight.vitals.get(actor.body)
        if vitals is None or vitals.dead:
            continue
        since = vitals.shield_recovery_tick
        if since is None or fight.tick <= since:
            continue
        missing = vitals.most[SHIELDS_POOL] - vitals.now[SHIELDS_POOL]
        restored = min(missing, (fight.tick - since) * tick_seconds * RECOVERY_RATE)
        vitals.shield_recovery_tick = None if restored >= missing else fight.tick
        if restored > 0:
            vitals.now[SHIELDS_POOL] += restored
            vitals.changed.add(SHIELDS_POOL)
            fight._sync_body_health(actor, vitals)
