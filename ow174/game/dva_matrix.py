"""Scoped 104319 D.Va mech owner bindings and Matrix flight eligibility."""

from ow174.game.script import expr, graph

HERO = 0x02E000000000007A
MECH = 0x0400000000000407
MATRIX = 0x02CC

# Exact finite Pharah Volley states. 0023/state6 has no authored entity definition.
# The 010F and 0052 entity records lack the Matrix filter's 0145 exclusion tag.
PHARAH_VOLLEYS = frozenset({(0x23, 6), (0x7B, 0), (0x2B, 1)})

# The Sniper's two projectiles and the Enforcer's pellet projectile lack 0145.
NPC_PROJECTILES = frozenset({0x0400000000001E80, 0x04000000000023BB, 0x04000000000023F0})


def record():
    return graph.entity_body(MECH)


def owner(world, instance):
    if (instance.component.strict or world.hero != HERO or world.match is None
            or instance.graph.index not in (0x0276, MATRIX)):
        return None
    return next((p for p in world.match.players
                 if p.has_body and not p.gone and p.body == world.body == instance.component.entity
                 and p.hero.body == MECH), None)


def controller(world, instance, cfg):
    source = cfg.get("m_801B298F")
    if cfg.get("$") != "STU_F7262B36" or not isinstance(source, dict) or source.get("$") != "STU_B5A0CAF0":
        return NotImplemented
    player = owner(world, instance)
    return expr.Entity(player.entity) if player is not None else NotImplemented


def ruleset(context, cfg):
    instance = getattr(context, "instance", None)
    if instance is None or instance.graph.index != MATRIX:
        return NotImplemented
    value = cfg.get("m_value") or {}
    expression = value.get("m_expression") or {}
    variables = expression.get("m_dynamicVars") or ()
    if len(variables) != 1:
        return NotImplemented
    key = variables[0].get("m_identifier")
    configs = value.get("m_configVars") or ()
    if key not in (9743, 9777, 9778) or len(configs) != 1 or configs[0].get("$") != "STU_0AC15F78":
        return NotImplemented
    world = instance.component.world
    player = owner(world, instance)
    if player is None:
        return NotImplemented
    return world.ruleset(key)
