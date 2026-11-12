"""D.Va combat consumers for the 104319 graphs."""

HERO = 0x02E000000000007A
BOOSTERS = 0x047E
MECH = 0x0400000000000407
FUSION_CANNONS = 0x050A
LIGHT_GUN = 0x02B8
LIGHT_GUN_PROJECTILE = 0x0400000000001000


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
