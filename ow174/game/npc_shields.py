"""Host authored 0E62 recovery for manual NPCs with shield pools."""

from ow174.game.npc_context import ENFORCER
from ow174.game.script import expr, runtime
from ow174.game.world import SHIELDS_POOL

GRAPH = 0x0E62
SHIELDED_HEROES = frozenset((ENFORCER, 0x02E00000000001B8, 0x02E00000000001BB))
SHIELD_MARKER = 1764
RECEIVED_DAMAGE = 0x0240000000000011
CURRENT_POOL, MAX_POOL = "STU_783B4CF0", "STU_68883EAC"


def scoped(world, instance):
    return (world.hero in SHIELDED_HEROES and world.match is not None and instance is not None
            and instance.graph.index == GRAPH and not instance.component.strict)


def recovery_pending(bot):
    script = bot.body_script
    if (bot.dead or bot.hero not in SHIELDED_HEROES or script is None
            or bot.spawner.get("manual_spawn") is not True):
        return False
    component = script.component
    return any(scoped(component.world, instance)
               and any(state.active and state.node.pos in (51, 52) for state in instance.states.values())
               for instance in component.instances.values())


def native(world, cfg, instance):
    cls = cfg.get("$")
    if cls not in (CURRENT_POOL, MAX_POOL) or not scoped(world, instance):
        return NotImplemented
    if (cfg.get("m_7E8B666C") or {}).get("m_7E8B666C") != SHIELDS_POOL:
        return NotImplemented
    if instance.evaluate(cfg.get("m_entity")) != expr.Entity(instance.component.entity):
        return NotImplemented
    vitals = world.match.combat.vitals.get(instance.component.entity)
    if vitals is None:
        return NotImplemented
    read = vitals.pool_total if cls == CURRENT_POOL else vitals.pool_maximum
    return expr.f32(read(SHIELDS_POOL))


def received_damage(fight, bot, vitals, source):
    if (bot.hero not in SHIELDED_HEROES or bot.body_script is None
            or bot.spawner.get("manual_spawn") is not True):
        return
    script = bot.body_script
    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
    maximum = vitals.pool_maximum(SHIELDS_POOL)
    if not scoped(component.world, instance) or maximum <= 0:
        return
    # Reconstruct the positive shield-capability input when no writer supplied it.
    if component.entity_var(SHIELD_MARKER, create=False) is None:
        component.set(SHIELD_MARKER, maximum)
    # Combat has no recovered damage-tag transport for these hits.
    runtime.send_instance_message(instance, RECEIVED_DAMAGE, {0x3D: ()}, source)
    if script.frame >= fight.tick:
        component.run_now()


def modify_health(world, state, elapsed_ms):
    if state.node.pos != 51 or not scoped(world, state.instance):
        return NotImplemented
    if not elapsed_ms:
        return True
    fight = world.match.combat
    target = next((t for t in fight._targets() if t.entity == state.instance.component.entity), None)
    if target is None:
        state.request(runtime.ABORT)
        return True
    vitals = fight.vitals_of(target)
    if vitals.dead or vitals.pool_total(SHIELDS_POOL) >= vitals.pool_maximum(SHIELDS_POOL):
        state.request(runtime.ABORT)
        return True
    amount = state.node.field("m_modifyHealth")["m_amount"]
    rate = expr.to_float(state.evaluate(amount, watch=False))
    seconds = elapsed_ms / 1000
    given = rate * seconds * fight.healing.factor(target, seconds)
    if given > 0 and vitals.restore_pool(SHIELDS_POOL, given):
        fight._sync_body_health(target.owner, vitals)
    return True
