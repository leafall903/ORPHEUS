"""Scoped 0B31 EMP target and input services for Wrecking Ball."""

import math

from ow174.game import ball, ball_mines, barriers
from ow174.game.script import expr, graph, play

SOMBRA = 0x02E000000000012E
EMP, CHILD, EFFECT = 0x0B31, 0x0B32, 0x0B1F
SKILLS = 0x02 | 0x08 | 0x10 | 0x20 | 0x80
DURATION = 0x0A4000000000002B
ORDINARY_HACK = 0x0B1B
MINE_DURATION_VARS = {CHILD: 567, EFFECT: 215}


def track_targets(world, state):
    if (world.hero, state.instance.graph.index, state.node.pos) != (SOMBRA, EMP, 40):
        return
    if world.match is None or state.instance.component.strict:
        return
    from ow174.game.combat import capsule_visible, segment_distance

    fight = world.match.combat
    caster = next((p for p in world.match.players if p.has_body and p.body == world.body), None)
    if caster is None or not barriers._recipient_ready(fight, caster):
        return
    origin = fight._eye(caster)
    radius = expr.to_float(state.evaluate(state.node.field("m_radius"), watch=False))
    selected = []
    for target in fight._enemies(caster):
        owner = target.owner
        if isinstance(owner, ball_mines.Mine):
            if owner.triggered or owner.removing:
                continue
        elif owner not in world.match.players or owner.hero.guid != ball.HERO:
            continue
        a, b = target.segment()
        if (segment_distance(origin, a, b) - target.radius <= radius
                and capsule_visible(world.match.collision_world, origin, a, b, target.radius)):
            selected.append(expr.Entity(target.entity))
            if not isinstance(owner, ball_mines.Mine):
                selected.extend(
                    expr.Entity(bubble.entity) for bubble in barriers.projected_on(fight, target.entity)
                )
    state.instance.write(expr.lvalue(state.node.field("m_8A2905A0")), tuple(selected))


def route(world, instance, node):
    signature = world.hero, instance.graph.index, node.pos
    ordinary = signature == (SOMBRA, ORDINARY_HACK, 141)
    if signature == (SOMBRA, EMP, 8):
        destination, source = CHILD, world.body
    elif ordinary:
        destination, source = EFFECT, world.body
    elif signature == (ball.HERO, CHILD, 6):
        destination, source = EFFECT, instance.source_entity()
    else:
        return
    if world.match is None or instance.component.strict:
        return
    resource = play.resolve_resource(instance, node)
    if resource is None or resource.guid != (0x0580000000000000 | destination):
        return
    definition = graph.graph(destination)
    if definition is None:
        return
    context = play.InvocationContext(source, source, source)
    request = play.resolve_request(instance, node, resource, context_provider=lambda: context)
    if request is None:
        return
    fight = world.match.combat
    impact = max(instance.component.now, fight.tick * instance.component.frame_ms)
    if destination == CHILD or isinstance(world, ball_mines.MineWorld):
        for target in request.targets:
            mine = fight.mines.entities.get(target)
            if mine is not None and mine.health > 0 and not mine.removing and not mine.triggered:
                load_target(mine.body_script.component, definition, resource, request, source, impact)
            elif (destination == CHILD and (bubble := fight.barriers.get(target)) is not None
                  and barriers.is_live_projected(fight, bubble)):
                load_target(bubble.component, definition, resource, request, source, impact)
        if isinstance(world, ball_mines.MineWorld):
            return
    locks_input = destination == CHILD or ordinary
    seconds = (instance.evaluate(definition.node(118).field("m_timeout"), None)
               if ordinary else dict(request.params).get(DURATION))
    duration = round(expr.to_float(seconds) * 1000)
    if locks_input and duration <= 0:
        return
    for player in world.match.players:
        if (player.body in request.targets and player.hero.guid == ball.HERO
                and barriers._recipient_ready(fight, player)):
            script = player.body_script
            child = load_target(script.component, definition, resource, request, source, impact)
            if child is None:
                continue
            deadlines = getattr(script, "emp_effect_deadlines", None)
            if not locks_input:
                if deadlines is not None and instance.id in deadlines:
                    deadlines[child.id] = deadlines[instance.id]
                continue
            if deadlines is None:
                deadlines = script.emp_effect_deadlines = {}
            impact_frame = impact // script.frame_ms
            start = impact_frame * script.frame_ms
            end = impact + duration
            deadlines[child.id] = end
            ball.interrupt_grapple(fight, player, impact_frame)
            if not ordinary:
                # Combat pools follow the pulse even if the recipient's
                # command clock hasn't reached the queued health state.
                amount = _shield_amount(child, child.graph.node(9))
                if amount:
                    previous_pulse = getattr(script, "emp_shield_pulse", None)
                    if previous_pulse is None or impact >= previous_pulse[0]:
                        script.emp_shield_pulse = impact, amount
                    _drain_ball_shields(fight, player, amount)
            prior = getattr(script, "emp_window", None)
            # Presentation expires on the world clock, but commands can still
            # arrive for frames the body hasn't simulated.
            if prior is not None:
                if start <= prior[1]:
                    start, end = prior[0], max(end, prior[1])
                elif script.frame * script.frame_ms < prior[1]:
                    script.emp_previous_windows = (*getattr(script, "emp_previous_windows", ()), prior)
            script.emp_window = (start, end)


def load_target(component, definition, resource, request, source, impact):
    previous = component.now
    # Stamp cross-body entries at impact without stepping the receiver.
    component.now = max(previous, impact)
    try:
        return component.play_loaded_graph(
            definition, source=source, params=request.params, overrides=resource.overrides,
            policy=request.policy, instigator=request.context.instigator,
        )
    finally:
        component.now = previous


def expire_mine_effects(component):
    removed = False
    for instance in tuple(component.instances.values()):
        key = MINE_DURATION_VARS.get(instance.graph.index)
        variable = instance.vars.get(key) if key is not None else None
        seconds = expr.to_float(variable.value()) if variable is not None else 0
        if seconds > 0 and component.frame * component.frame_ms >= instance.start + round(seconds * 1000):
            component.destroy(instance)
            removed = True
    if removed:
        component.run_now()


def end_health(world, state):
    instance = state.instance
    signature = world.hero, instance.graph.index, state.node.pos
    if instance.component.strict or signature != (ball.HERO, CHILD, 9):
        return
    source = instance.source_entity()
    for child in tuple(instance.component.instances.values()):
        flag = child.vars.get(10409) if child.graph.index == EFFECT else None
        if flag is not None and expr.truthy(flag.value()) and child.source_entity() == source:
            instance.component.destroy(child)


def modify_health(world, state, elapsed_ms):
    instance = state.instance
    signature = world.hero, instance.graph.index, state.node.pos
    if signature != (barriers.ZARYA, CHILD, 3):
        return NotImplemented
    if elapsed_ms or world.match is None or instance.component.strict:
        return True
    fight = world.match.combat
    source = instance.source_entity()
    if not any(p.has_body and p.body == source and p.hero.guid == SOMBRA for p in fight.match.players):
        return True
    amount = _shield_amount(instance, state.node)
    if not amount:
        return True
    bubble = fight.barriers.get(instance.component.entity)
    if (bubble is not None and bubble.component is instance.component
            and barriers.is_live_projected(fight, bubble)):
        barriers.damage(fight, bubble, amount)
    return True


def _shield_amount(instance, node):
    if node is None:
        return 0.0
    amount = expr.to_float(instance.evaluate(node.field("m_modifyHealth.m_amount")))
    return amount if math.isfinite(amount) and amount > 0 else 0.0


def _drain_ball_shields(fight, player, amount):
    vitals = fight.vitals.get(player.body)
    if vitals is not None and vitals.drain_shields(amount):
        fight._sync_body_health(player, vitals)


def native(world, cfg, instance):
    if (world.hero != barriers.ZARYA or world.match is None or instance is None
            or instance.component.strict or instance.graph.index != CHILD
            or cfg.get("$") != "STU_25ECB55F" or cfg.get("m_88B0050A") != "0x0D6000000000003A"):
        return NotImplemented
    fight = world.match.combat
    body = expr.to_entity(instance.evaluate(cfg.get("m_entity")))
    bubble = fight.barriers.get(body)
    return (bubble is not None and bubble.component is instance.component
            and barriers.is_live_projected(fight, bubble))


def locked(script, frame):
    when = frame * script.frame_ms
    start, end = getattr(script, "emp_window", (0, 0))
    return start <= when < end or any(
        start <= when < end for start, end in getattr(script, "emp_previous_windows", ())
    )


def first_lock_after(script, frame, tick):
    """Return the first already-applied lock after a historical command frame."""
    previous = getattr(script, "emp_previous_windows", ())
    current = getattr(script, "emp_window", (0, 0))
    for start, _ in (*previous, current):
        impact_frame = start // script.frame_ms
        if frame < impact_frame <= tick:
            return impact_frame
    return None


def expire(fight, previous_tick):
    for player in fight.match.players:
        script = player.body_script
        window = getattr(script, "emp_window", None)
        if window is None or previous_tick * script.frame_ms >= window[1]:
            continue
        now = fight.tick * script.frame_ms
        window_ended = window[1] <= now
        deadlines = getattr(script, "emp_effect_deadlines", None)
        for instance in tuple(script.component.instances.values()):
            if instance.graph.index not in (CHILD, EFFECT):
                continue
            deadline = deadlines.get(instance.id) if deadlines is not None else None
            if window_ended or (deadline is not None and deadline <= now):
                script.component.destroy(instance)
        if deadlines is not None:
            for instance_id in tuple(deadlines):
                if instance_id not in script.component.instances:
                    del deadlines[instance_id]
            if not deadlines:
                del script.emp_effect_deadlines


def admitted_buttons(script, frame, buttons, *, observed):
    if script.hero != ball.HERO:
        return buttons
    blocked = getattr(script, "emp_suppressed_buttons", 0)
    skills = buttons & SKILLS
    lock = bool(skills) and locked(script, frame)
    previous = getattr(script, "emp_previous_windows", ())
    when = frame * script.frame_ms
    if previous and previous[0][1] <= when:
        remaining = tuple(window for window in previous if when < window[1])
        if remaining:
            script.emp_previous_windows = remaining
        else:
            del script.emp_previous_windows
    if observed:
        blocked = skills if lock else blocked & skills
        if blocked:
            script.emp_suppressed_buttons = blocked
        elif hasattr(script, "emp_suppressed_buttons"):
            del script.emp_suppressed_buttons
    return buttons & ~(SKILLS if lock else blocked)
