"""Host the authored Sombra EMP body chain for the Zenyatta combat lane."""

from ow174.game.script import expr, graph, play, runtime
from ow174.game.world import SHIELDS_POOL
from ow174.game.zenyatta import DISCORD, HARMONY
from ow174.game.zenyatta import HERO as ZENYATTA

SOMBRA = 0x02E000000000012E
EMP, CHILD, EFFECT = 0x0B31, 0x0B32, 0x0B1F
ROUTES = {(SOMBRA, EMP, 8): CHILD, (ZENYATTA, CHILD, 6): EFFECT}
SKILLS = HARMONY | DISCORD | 0x20


def protected(fight, target):
    return fight.zenyatta.ultimate(target.owner) or fight.barriers.bubble(target) is not None


def targets(world, state):
    if (world.hero, state.instance.graph.index, state.node.pos) != (SOMBRA, EMP, 40):
        return None
    if world.match is None or state.instance.component.strict:
        return None
    from ow174.game.combat import capsule_visible, segment_distance

    fight = world.match.combat
    caster = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    collision = fight.match.collision_world
    if caster is None or fight.is_dead(caster) or collision is None:
        return ()
    origin = fight._eye(caster)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    selected = []
    for target in fight._enemies(caster):
        player = target.owner
        if (getattr(getattr(player, "hero", None), "guid", None) != ZENYATTA
                or protected(fight, target)):
            continue
        a, b = target.segment()
        if (segment_distance(origin, a, b) - target.radius <= radius
                and capsule_visible(collision, origin, a, b, target.radius)):
            selected.append(expr.Entity(target.entity))
    return tuple(selected)


def route(world, instance, node, resource):
    destination = ROUTES.get((world.hero, instance.graph.index, node.pos))
    if (world.match is None or instance.component.strict or destination is None
            or resource.guid != (0x0580000000000000 | destination)):
        return False
    source = world.body if destination == CHILD else instance.source_entity()
    context = play.InvocationContext(source, source, source)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    definition = graph.graph(destination)
    if request is None or definition is None:
        return True
    fight = world.match.combat
    if destination == EFFECT:
        target = next((t for t in fight._targets() if t.entity == world.body), None)
        if target is None or protected(fight, target):
            return True
    for player in world.match.players:
        script = player.body_script
        if (player.body not in request.targets or not player.has_body or player.gone
                or player.hero.guid != ZENYATTA or script is None or script.retired
                or world.match.combat.is_dead(player)):
            continue
        script.component.play_loaded_graph(
            definition, source=source, params=request.params, overrides=resource.overrides,
            policy=request.policy, instigator=request.context.instigator,
        )
    return True


def modify_health(world, state, elapsed_ms):
    if ((world.hero, state.instance.graph.index, state.node.pos) != (ZENYATTA, CHILD, 9)
            or world.match is None or state.instance.component.strict):
        return NotImplemented
    # The exposed Zen branch strips base shields on entry. Use the ordinary
    # Zen damage clock for positive shield loss. Tag and credit rules are unhosted.
    if not elapsed_ms:
        fight = world.match.combat
        target = next((t for t in fight._targets() if t.entity == world.body), None)
        if target is None or protected(fight, target):
            state.request(runtime.ABORT)
            return True
        vitals = fight.vitals_of(target)
        if vitals.now[SHIELDS_POOL] > 0:
            vitals.now[SHIELDS_POOL] = 0.0
            vitals.changed.add(SHIELDS_POOL)
            fight.zenyatta.damaged(target)
            fight._sync_body_health(target.owner, vitals)
    return True


def end_health(world, state):
    from ow174.game import sombra_hack

    if (world.hero, state.instance.graph.index, state.node.pos) != (ZENYATTA, CHILD, 9):
        return
    component = state.instance.component
    if component.strict:
        return
    # The current 0B32 owns the local EMP presentation instances.
    for instance in tuple(component.instances.values()):
        if instance.graph.index == EFFECT and not sombra_hack.direct_effect(instance):
            component.destroy(instance)


def locked(script, frame):
    for instance in script.component.instances.values():
        if instance.graph.index != CHILD:
            continue
        chase = instance.states.get(instance.graph.node(4).state)
        # A routed start can be queued between two commands for this body.
        if chase is None and instance.queue:
            return True
        health = instance.states.get(instance.graph.node(9).state)
        if chase is not None and chase.active and health is not None and health.active:
            duration_ms = round(expr.to_float(instance.vars[567].value()) * 1000)
            if frame * script.frame_ms < chase.start + duration_ms:
                return True
    return False


def admitted_buttons(script, frame, buttons, *, observed):
    from ow174.game import sombra_hack

    if script.hero != ZENYATTA:
        return buttons
    blocked = getattr(script, "emp_suppressed_buttons", 0)
    skills = buttons & SKILLS
    lock = bool(skills) and (locked(script, frame) or sombra_hack.locked(script, frame))
    if observed:
        # A rejected press needs a real release before it can become a new edge.
        # Synthesized commands and other controls cannot release this latch.
        blocked = skills if lock else blocked & skills
        if blocked:
            script.emp_suppressed_buttons = blocked
        elif hasattr(script, "emp_suppressed_buttons"):
            del script.emp_suppressed_buttons
    return buttons & ~(SKILLS if lock else blocked)
