"""Target predicates and damage conversion for the authored 0B8B graph."""

from ow174.game import bots
from ow174.game.content import SOLDIER
from ow174.game.script import expr, runtime

GRAPH = 0x0B8B
PVE_TAG = 0x0D600000000003C3
ROADHOG_TAG = 0x0D6000000000011E
ROADHOG_BODY = 0x040000000000028F
TARGET_CHARGE_FIELDS = (602, 1363, 6442, 20682)
CHARGE_HEROES = (0x02E0000000000002, 0x02E0000000000009, SOLDIER)


class _DamageChargeContext(runtime.InstanceContext):
    def __init__(self, instance, amount, target, damage_tags):
        super().__init__(instance, None)
        self.amount = amount
        self.target = expr.Entity(target.entity)
        self.damage_tags = damage_tags
        owner = target.owner
        body = owner.body if isinstance(owner, bots.Bot) else owner.hero.body
        self.tags = {ROADHOG_TAG} if body == ROADHOG_BODY else set()
        script = owner.body_script
        self.target_component = script.component if script is not None else None
        if script is None:
            return
        for instance in script.component.instances.values():
            state = instance.states.get(7) if instance.graph.index == 0x0E62 else None
            if state is None or not state.active or state.node.cls != "STU_E56BA8D8":
                continue
            tag = state.evaluate(state.node.field("m_targetTags"), watch=False)
            subject = state.evaluate(state.node.field("m_31714AFC"), watch=False)
            if tag == expr.Asset(PVE_TAG) and expr.to_entity(subject) == target.entity:
                self.tags.add(PVE_TAG)

    def var(self, scope, var):
        if (scope, var) == (expr.INSTANCE, 336):
            return self.amount
        if (scope, var) == (expr.INSTANCE, 393):
            return self.target
        if (scope, var) == (expr.INSTANCE, 510):
            return self.damage_tags
        return super().var(scope, var)

    def member(self, entity, var):
        if entity == self.target.id and var in TARGET_CHARGE_FIELDS:
            component = self.target_component
            slot = component.entity_var(var, create=False) if component is not None else None
            return slot.value() if slot is not None else None
        return super().member(entity, var)

    def native(self, cfg):
        if (cfg.get("$") == "STU_A58C1E63"
                and cfg is self.instance.graph.node(28).field("m_condition")["m_configVars"][0]):
            # This bridge is entered only for an enemy contact admitted by Combat.
            return True
        if cfg.get("$") == "STU_25ECB55F":
            subject = expr.evaluate(cfg.get("m_entity"), self)
            return subject == self.target and expr.guid_of(cfg.get("m_88B0050A")) in self.tags
        return super().native(cfg)


def event_tags(state, modify):
    """Evaluate the authored ModifyHealth event tags for charge-capable sources."""
    if not modify.get("m_148F3152"):
        return ()
    if state.instance.component.world.hero not in CHARGE_HEROES:
        return ()
    return tuple(tag for item in modify["m_148F3152"]
                 if isinstance(item, dict) and (cfg := item.get("m_EE3055BD")) is not None
                 and isinstance((tag := state.evaluate(cfg, watch=False)), expr.Asset))


def admitted_source(script):
    """Resolve the supported 0B8B source gates for an actual health award."""
    if script is None or script.retired or script.component.owner_dead:
        return None
    component = script.component
    instance = next((i for i in component.instances.values() if i.graph.index == GRAPH), None)
    if instance is None:
        return None
    eligibility = component.entity_var(2426, create=False)
    if eligibility is not None and eligibility.value() is not None and not expr.truthy(eligibility.value()):
        return None
    if any((var := component.entity_var(key, create=False)) is not None and expr.truthy(var.value())
           for key in (842, 12321)):
        return None
    if all(expr.truthy(instance.evaluate(instance.graph.node(pos).field("m_condition")))
           for pos in (56, 61)):
        return instance
    return None


def _has_damage_tag(instance, pos, context):
    node = instance.graph.node(pos)
    tags = expr.evaluate(node.field("m_814F8EC3"), context)
    tag = expr.evaluate(node.field("m_value"), context)
    return expr.contains_value(tags, tag)


def apply(script, instance, amount, target, damage_tags=()):
    """Evaluate the matching authored branch without writing message input variables."""
    context = _DamageChargeContext(instance, amount, target, damage_tags)
    if any(_has_damage_tag(instance, pos, context) for pos in (30, 36)):
        return
    if any(expr.truthy(expr.evaluate(instance.graph.node(pos).field("m_condition"), context))
           for pos in (5, 17)):
        return
    if not expr.truthy(expr.evaluate(instance.graph.node(28).field("m_condition"), context)):
        return
    if (expr.truthy(expr.evaluate(instance.graph.node(87).field("m_condition"), context))
            and _has_damage_tag(instance, 86, context)):
        return
    for condition, action in ((59, 60), (97, 98)):
        if expr.truthy(expr.evaluate(instance.graph.node(condition).field("m_condition"), context)):
            context.amount = expr.evaluate(instance.graph.node(action).field("m_value"), context)
            break
    value = expr.evaluate(instance.graph.node(23).field("m_value"), context)
    script.credit_ult_charge(expr.to_float(value))
