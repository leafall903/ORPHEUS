"""Declared manual combat-controller policy; native controller allocation is open."""

from ow174.game.script import expr

ENFORCER = 0x02E00000000001CE
ENFORCER_BODY = 0x0400000000002279
ENFORCER_CONTROLLER = 0x040000000000227A
CONTROLLER_GRAPH = 0x13EA
WEAPON = 0x13EB
MOVEMENT = 0x13EF
ANIMATION_MOTION = 0x1506


def controller_definition(body):
    from ow174.game import assassin
    from ow174.game.script import graph

    definition = {ENFORCER_BODY: ENFORCER_CONTROLLER, assassin.BODY: assassin.CONTROLLER_BODY}.get(body)
    return graph.entity_body(definition) if definition is not None else None


def manual_components(bot):
    """Reconstruct a manual combat controller with4B present and5C absent."""
    eligible = bot.hero in (ENFORCER, 0x02E00000000001BB) and bot.spawner.get('manual_spawn') is True
    return (0x4B,) if eligible else None


def controller_predicate(components):
    """B96D3A98's CC9F61 tail checks4B present, then5C absent."""
    return components is not None and 0x4B in components and 0x5C not in components


def native(world, cfg, instance):
    if instance is not None and instance.graph.index == CONTROLLER_GRAPH:
        return enforcer_query(world, cfg, instance)
    if (world.hero != ENFORCER or world.controller_components is None or instance is None
            or instance.graph.index not in (WEAPON, MOVEMENT, ANIMATION_MOTION)):
        return NotImplemented
    kind = cfg.get('$')
    if kind != 'STU_F7262B36' and instance.graph.index != WEAPON:
        return NotImplemented
    target = cfg.get('m_801B298F' if kind == 'STU_F7262B36' else 'm_entity')
    if not isinstance(target, dict) or instance.evaluate(target) != expr.Entity(instance.component.entity):
        return NotImplemented
    if kind == 'STU_F7262B36' and world.controller is not None:
        return expr.Entity(0 if world.controller.owner_dead else world.controller.entity)
    if kind == 'STU_B96D3A98':
        return world.controller is None and controller_predicate(world.controller_components)
    if kind == 'STU_9B7648C3':
        # ReplicatedBody.set_health supplies the authoritative aggregate here.
        from ow174.game.script.driver import OWNER_HEALTH

        slot = instance.component.entity_var(OWNER_HEALTH, create=False)
        return expr.f32(slot.value()) if slot is not None else 0.0
    return NotImplemented


def enforcer_query(world, cfg, instance):
    """Resolve 13EA's manual selection and live geometry inputs."""
    kind = cfg.get("$")
    if world.match is None or kind not in (
        "STU_A37A910D", "STU_D379F515", "STU_783B4CF0", "STU_71A87E92", "STU_0AC15F78",
    ):
        return NotImplemented
    fight = world.match.combat
    identity = instance.component.entity
    bot = next(
        (b for b in world.match.bots if b.spawner.get("controller_entity") == identity and not b.dead), None
    )
    if bot is None or instance.component.owner_dead:
        return expr.Entity(0) if kind == "STU_A37A910D" else NotImplemented
    if kind == "STU_A37A910D":
        owner = expr.to_entity(instance.evaluate(cfg.get("m_152B6961")))
        slot = instance.evaluate(cfg.get("m_A60150E2"))
        if owner == identity and slot == 0 and world.manual_target:
            for target in fight._enemies(bot):
                if target.entity == world.manual_target:
                    return expr.Entity(target.entity)
        return expr.Entity(0)
    if kind == "STU_D379F515":
        owner = expr.to_entity(instance.evaluate(cfg.get("m_7826B991")))
        entity = expr.to_entity(instance.evaluate(cfg.get("m_target")))
        target = next((t for t in fight._targets() if t.entity == entity), None)
        if owner != identity or target is None or world.match.collision_world is None:
            return False
        return controller_line_clear(fight, bot, target)
    entity = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    if kind == "STU_0AC15F78":
        return expr.Entity(bot.entity if entity == identity else 0)
    if kind == "STU_783B4CF0":
        vitals = fight.vitals.get(entity) if entity == bot.entity else None
        return expr.f32(vitals.total) if vitals is not None else 0.0
    return world.position(entity)


def controller_line_clear(fight, bot, target):
    """Reconstruct controller reachability with map and Ice Wall occlusion."""
    from ow174.game import combat

    x, y, z = bot.position
    start = (x, y + combat.EYE, z)
    x, y, z = target.feet
    end = (x, y + target.eye, z)
    return (combat.capsule_visible(fight._collision(), start, end, end, 0.0)
            and not fight.walls.blocked(start, end))


def advance_controller(controller, frame):
    instance = next((i for i in controller.instances.values() if i.graph.index == CONTROLLER_GRAPH), None)
    if instance is not None:
        state = instance.states.get(instance.graph.node(1).state)
        world = controller.world
        selected = bool(state and state.active and world.manual_target and all(
            expr.truthy(instance.evaluate(config)) for config in state.node.field("m_AAAC951E") or ()
        ))
        if selected != world.enforcer_selected:
            world.enforcer_selected = selected
            if selected:
                instance.follow(state.node, "m_6F92F4BD", state)
            elif state is not None:
                instance.exit_subgraph(state.node, "m_6F92F4BD")
    controller.run_frame(frame)
    if instance is not None:
        distance = instance.states.get(instance.graph.node(69).state)
        if distance is not None and distance.active:
            distance.dependency_changed()
        state = instance.states.get(instance.graph.node(63).state)
        if state is not None and state.active:
            state.try_complete()
        visibility = instance.states.get(instance.graph.node(67).state)
        if visibility is not None and visibility.active:
            visibility.reevaluate()
        controller.run_now()


def movement_scale(bot):
    script = bot.body_script
    if bot.body != ENFORCER_BODY or script is None or script.controller is None:
        return 1.0
    for instance in script.component.instances.values():
        if instance.graph.index == MOVEMENT:
            state = instance.states.get(instance.graph.node(0).state)
            if state is not None and state.active:
                config = state.node.field("m_BC5E91CF")[0]["m_9B7A63EA"]
                return expr.to_float(state.evaluate(config, watch=False))
    return 1.0


def clocked(bot):
    if bot.body_script is None or bot.body_script.controller is None:
        return False
    if bot.body != ENFORCER_BODY:
        return not bot.body_script.controller.owner_dead
    world = bot.body_script.controller.world
    return bool(world.manual_target or world.enforcer_selected or movement_scale(bot) != 1.0)
