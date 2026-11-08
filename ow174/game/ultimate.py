"""Tracer's authored0B8B charge graph, with explicit reconstructed host facts.

The graph supplies5 points/second, gain multipliers, excluded damage tags,
special-target gain factors, cooldown/busy gates and clamping. Typed combat-actor
classification, live-owner eligibility and hostile-team queries below replace
missing engine services; they are not native filter emulation.
"""

from ow174.game.script import expr, runtime

TRACER = 0x02E0000000000003
GRAPH = 0x0B8B
DAMAGE = 0x0240000000000012
TRAINING_BODIES = frozenset((0x0400000000001403, 0x040000000000143E, 0x040000000000146E,
                             0x04000000000014A9, 0x04000000000014D2, 0x04000000000014ED))


def active(script):
    return script.hero == TRACER and script.world.match is not None


def prepare(script):
    """Evaluate the actual common-body capability assignment once it has a host."""
    if not active(script):
        return
    enabled = script.component.entity_var(2426, create=False)
    if enabled is not None and enabled.value() is not None:
        return
    common = next((i for i in script.component.instances.values() if i.graph.index == 0x01C7), None)
    if common is not None:
        common.run_node(common.graph.node(39), None)


def native(world, cfg, instance):
    if world.hero != TRACER or world.match is None or instance.graph.index != GRAPH:
        return NotImplemented
    targets = {t.entity: t for t in world.match.combat._targets()}

    def entity(field):
        return expr.to_entity(instance.evaluate(cfg.get(field)))

    cls = cfg.get("$")
    if cls == "STU_F7262B36":
        target = targets.get(entity("m_801B298F"))
        script = getattr(target.owner, "body_script", None) if target is not None else None
        return (target is not None and target.entity == world.body and script is not None
                and script.component is instance.component and not script.component.owner_dead
                and script.ult_seconds == 60.0)
    if cls == "STU_AC829876":
        target = targets.get(entity("m_entity"))
        config = cfg.get("m_filter") or {}
        # The exact actor-bit query used as the first alternative by nodes53/54.
        # Other native masks/conditions remain unimplemented, not globally true.
        if (config.get("m_84065FD1") == 1 and not config.get("m_66747E7D")
                and config.get("m_condition") is None):
            return target is not None
    if cls == "STU_25ECB55F" and expr.guid_of(cfg.get("m_88B0050A")) == 0x0D600000000002ED:
        target = targets.get(entity("m_entity"))
        return target is not None and getattr(target.owner, "body", None) in TRAINING_BODIES
    if cls == "STU_A58C1E63":
        target, source = targets.get(entity("m_target")), targets.get(entity("m_source"))
        return (target is not None and source is not None and target.entity != source.entity
                and (world.match.game_map.free_for_all or target.team != source.team))
    if cls == "STU_A6B80C63":
        records = instance.evaluate(cfg.get("m_814F8EC3"))
        if records == ():
            return 0.0  # this damage bridge explicitly supplies no excluded-damage records
    return NotImplemented


def credit(shooter, target, amount, tags):
    """Deliver actual resolved pool loss through the unchanged0B8B damage entry."""
    script = getattr(shooter, "body_script", None)
    if (script is None or not active(script) or shooter is target.owner or amount <= 0
            or (not script.world.match.game_map.free_for_all and shooter.team == target.team)):
        return
    instance = next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)
    if instance is None:
        return
    prepare(script)
    params = {0x0013: float(amount), 0x0015: expr.Entity(target.entity),
              0x003D: tuple(expr.Asset(tag) for tag in tags), 0x0624: ()}
    runtime.send_instance_message(instance, DAMAGE, params, shooter.body)
    script.component.run_now()
