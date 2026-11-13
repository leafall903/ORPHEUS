"""D.Va combat consumers for the 104319 graphs."""

import math

HERO = 0x02E000000000007A
BOOSTERS = 0x047E
MECH = 0x0400000000000407
FUSION_CANNONS = 0x050A
LIGHT_GUN = 0x02B8
LIGHT_GUN_PROJECTILE = 0x0400000000001000


def health_impulse(state, context, origin, feet, distance, velocity_y):
    """The three DVa ModifyHealth producers; receiver/time policy is shared."""
    if (state.instance.graph.index, state.node.pos) not in ((BOOSTERS, 16), (0x0B2F, 22), (0x026A, 36)):
        return None
    from ow174.game.script import expr

    distance = expr.f32(max(0., distance))

    def scalar(config, default):
        if config is None:
            return default
        value = expr.evaluate(config, context)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('unsupported DVa force provider')
        return expr.f32(value)

    fields = state.node.field('m_modifyHealth')
    horizontal = scalar(fields.get('m_E33EFEE1'), 0.)
    upward = scalar(fields.get('m_10399AE7'), 0.)
    minimum = scalar(fields.get('m_583C208B'), None)
    # Original D6D87C..D6DA6A: interpolate the two independent force scales;
    # an empty array retains unit scale, and an outer boundary is exclusive.
    rings = fields.get('m_knockbackRings') or ()
    flat_scale = up_scale = 1.
    if rings:
        flat_scale = up_scale = 0.
        previous = None
        for ring in rings:
            radius, flat, up = (scalar(ring.get(key), 0.) for key in
                                ('m_radius', 'm_579D7CFF', 'm_10399AE7'))
            if distance < radius:
                if previous is None:
                    flat_scale, up_scale = flat, up
                else:
                    before, last_flat, last_up = previous
                    share = expr.f32(expr.f32(distance - before) / max(expr.f32(radius - before), .0001))
                    flat_scale = expr.f32(expr.f32(expr.f32(flat - last_flat) * share) + last_flat)
                    up_scale = expr.f32(expr.f32(expr.f32(up - last_up) * share) + last_up)
                break
            previous = radius, flat, up
    if not (flat_scale or up_scale):
        return None
    dx, dz = feet[0] - origin[0], feet[2] - origin[2]
    length = math.hypot(dx, dz)
    scale = horizontal * flat_scale / length if length else 0.
    lift = expr.f32(upward * up_scale)
    if up_scale > 0 and minimum is not None:
        lift = max(lift, expr.f32(minimum - velocity_y))
    return expr.Vec3(dx * scale, lift, dz * scale)


def fusion_view_tick(actor, state, command, quanta):
    """Query world target history from this owned body's creation epoch."""
    hero = getattr(actor, "hero", None)
    if (getattr(hero, "guid", hero) != HERO
            or (state.instance.graph.index, state.node.pos) != (FUSION_CANNONS, 6)):
        return None
    birth = getattr(actor.body_script, "dva_birth", None)
    if birth is None:
        return None
    from ow174.game.combat import TICK

    world, body = birth
    elapsed = (command.frame - body) * state.instance.component.frame_ms
    return world + (elapsed - command.view_delay(quanta) * 1000) / round(TICK * 1000)


def projectile_launch_tick(actor, state, time):
    """Map the emitting body's original event clock onto its world birth epoch."""
    hero = getattr(actor, "hero", None)
    if (getattr(hero, "guid", hero) != HERO
            or (state.instance.graph.index, state.node.pos) not in ((LIGHT_GUN, 10), (0x1128, 10))):
        return None
    script = actor.body_script
    birth = getattr(script, "dva_birth", None)
    if birth is None:
        return None
    from ow174.game.combat import TICK

    world_tick, body_frame = birth
    quantum = round(TICK * 1000)
    elapsed = time - body_frame * state.instance.component.frame_ms
    return world_tick + math.ceil(elapsed / quantum)


def direct_hit_critical(actor, state):
    """Reconstructed critical policy for D.Va's three weapon volleys."""
    hero = getattr(actor, "hero", None)
    if getattr(hero, "guid", hero) != HERO:
        return None
    site = state.instance.graph.index, state.node.pos
    if site in ((FUSION_CANNONS, 6), (LIGHT_GUN, 10)):
        return 2.0
    if site == (0x1128, 10):
        return 1.0
    return None


def self_destruct_running(component):
    from ow174.game.script import nodes

    return any(
        (state := instance.states.get(0)) is not None and state.flags & nodes.Ability.RUNNING
        for instance in component.instances.values() if instance.graph.index == 0x0B2F
    )


def charge_damage(player, shooter, damage):
    """Credit effective enemy damage to the active D.Va body's own meter."""
    if player.hero.guid != HERO:
        return
    mech = player.controlled_mech
    if shooter is not (mech or player):
        return
    script = shooter.body_script
    if script is None or script.component.owner_dead:
        return
    from ow174.game.script import expr, nodes
    from ow174.game.script.driver import ULT_CHARGE, ULT_COST

    component = script.component
    if mech is not None:
        if self_destruct_running(component):
            return
    elif (player.hero.body == MECH or script.retired
          or any(state.flags & nodes.Ability.RUNNING for state in script.ultimates)):
        return
    cost = expr.to_float(component.entity_var(ULT_COST).value())
    charge = expr.to_float(component.entity_var(ULT_CHARGE).value())
    if charge < cost:
        component.set(ULT_CHARGE, expr.f32(min(cost, charge + damage)))


def boosters(actor):
    script = actor.body_script
    if script is None or script.component.world.hero != HERO:
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == BOOSTERS), None)
