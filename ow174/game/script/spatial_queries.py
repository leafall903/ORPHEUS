"""104319 spatial scalar interfaces used by D.Va creation and placement.

The engine owns the distinct D16460/D22D50/D20320/D1B5E0/D18780 helpers.
Their outputs must not be replaced by one guessed position/rotation query.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

# Class -> original scalar and engine helper RVAs. 021D01CD takes the third
# basis vector (+20) of D1B5E0's four-vector transform, not a quaternion.
INTERFACES = {
    "STU_8CBDD81C": (0xCE6430, 0xD16460),
    "STU_0BA27A07": (0xCE62F0, 0xD22D50),
    "STU_2EDEE064": (0xCE1710, 0xD20320),
    "STU_021D01CD": (0xCCDD80, 0xD1B5E0),
    "STU_2200A66D": (0xCC94D0, 0xD18780),
}


@dataclass
class Context:
    owner: int
    instance_present: bool
    namespace_present: bool
    query: object = NotImplemented  # (class, uint32 target) -> Vec3, or known helper failure None.
    retired: bool = False


def evaluate(evaluation, cfg):
    cls = cfg.get("$")
    if cls not in INTERFACES:
        return NotImplemented
    instance, component = evaluation.instance, evaluation.instance.component
    context = component.world.spatial_query_context(component.entity)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, cls)

    def check():
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or component.world.spatial_query_context(component.entity) is not context):
            unsupported("spatial query retired context")
        if type(context.instance_present) is not bool or type(context.namespace_present) is not bool:
            unsupported("spatial query availability")

    check()
    if not context.instance_present or not context.namespace_present:
        return expr.EVALUATION_FAILED
    # 1B7FD20 marks a present watcher before evaluating even a failed selector.
    evaluation.poll_native(cfg)
    check()
    value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0),
                                 watcher=evaluation.watcher)
    check()
    identifier = expr.to_entity(value) & 0xFFFFFFFF
    if not callable(context.query):
        unsupported("spatial engine helper")
    result = context.query(cls, identifier)
    check()
    if result is None:
        return expr.EVALUATION_FAILED
    if type(result) is not expr.Vec3:
        unsupported("spatial engine vector result")
    return result
