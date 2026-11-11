"""104319 CE4A60/CEDBD0 entity queries with explicit native lookup services.

The owner supplies the active namespace/environment and both lookup registries.
Scene bindings and the reverse primary-possessor catalog do not imply these inputs.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

EXISTS = 'STU_E72F6348'
POSSESSED = 'STU_0AC15F78'


@dataclass
class Possession:
    current: int

    def __post_init__(self):
        if type(self.current) is not int or not 0 <= self.current <= 0xFFFFFFFF:
            raise ValueError('current possession must be a native uint32 identity')


@dataclass(frozen=True)
class Target:
    entity: int
    possession: object = NotImplemented  # None declares component44 absence.

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('query target must have a native uint32 identity')
        if (self.possession is not NotImplemented and self.possession is not None
                and type(self.possession) is not Possession):
            raise ValueError('expected current possession or known component44 absence')


@dataclass(frozen=True)
class TaggedCandidate:
    """One candidate with the host's effective uint32 tag keys."""

    entity: int
    tags: frozenset[int]
    suppressed: bool = False


def collect_tagged(instance, node, *, watcher=None):
    """CB8EC0/D3FE60 with explicit world services and no authored filter."""
    component = instance.component
    output = node.field("m_4AEEE6A1")
    if output is None:
        return
    if watcher is not None:
        present = component.world.tagged_entity_world_available(instance, node)
        _check_tag_owner(instance, node, watcher)
        if type(present) is not bool:
            component.unsupported(instance, "tag query world availability", node.cls, node)
        if not present:
            return
    target = expr.dynamic(output)
    if target is None:
        if isinstance(output, dict) and output.get("$") in expr.CONSTANTS:
            return
        component.unsupported(instance, "tag query output target", node.cls, node)
    if not target[1]:
        return
    variable = instance.find_var(*target)
    if variable.links:
        component.unsupported(instance, "tag query linked output", node.cls, node)
    if node.field("m_filter") is not None:
        component.unsupported(instance, "tag query filter", node.cls, node)
    before = variable.value()
    variable.base = ()
    configs = [node.field(field) for field in ("m_9C7BD8B8", "m_B486603E", "m_8B2A4DF3")]
    if any(cfg is not None for cfg in configs):
        any_tags, all_tags, excluded_tags = (
            frozenset(asset & 0xFFFFFFFF for item in play._iter_values(instance, cfg, watcher=watcher)
                      if (asset := play._asset(item)))
            for cfg in configs
        )
        candidates = component.world.tagged_entity_candidates(instance, node)
        _check_tag_owner(instance, node, watcher)
        if type(candidates) is not tuple:
            component.unsupported(instance, "tag query enumeration", node.cls, node)
        matches = []
        for candidate in candidates:
            component.consume_work("tag query candidate")
            if type(candidate) is not TaggedCandidate:
                component.unsupported(instance, "tag query candidate", node.cls, node)
            tags = candidate.tags
            if (not candidate.suppressed and (not any_tags or any_tags & tags)
                    and all_tags <= tags and not excluded_tags & tags):
                matches.append(expr.Entity(candidate.entity))
        variable.base = tuple(matches)
    instance.changed(variable, before)


def _check_tag_owner(instance, node, watcher):
    component = instance.component
    component.ensure_valid()
    if (instance.stopped or component.instances.get(instance.id) is not instance
            or (watcher is not None and not watcher.active)):
        component.unsupported(instance, "tag query owner retired", node.cls, node)


@dataclass
class Context:
    owner: int
    namespace_present: bool
    environment_present: bool
    possession_context_present: bool
    lookup: object = NotImplemented
    watch_environment: object = NotImplemented  # None declares a missing dependency hub.
    watch_possession: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError('invalid entity-query owner')
        for value in (self.namespace_present, self.environment_present, self.possession_context_present):
            if type(value) is not bool:
                raise ValueError('entity-query context availability must be known')

    def retire(self):
        self.retired = True


def evaluate(evaluation, cfg):
    """Return NotImplemented only when this world has not installed the service."""
    cls = cfg.get('$')
    if cls not in (EXISTS, POSSESSED):
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_query_context(component.entity)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, cls)

    def check():
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or component.world.entity_query_context(component.entity) is not context):
            unsupported('entity query owner retired during callback')
        if any(type(value) is not bool for value in (
                context.namespace_present, context.environment_present, context.possession_context_present)):
            unsupported('entity query context availability')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        value = callback(*args)
        check()
        return value

    def watch_environment(kind):
        if watcher is not None and context.watch_environment is not None:
            call(context.watch_environment, 'entity query environment subscription', watcher, kind)

    check()
    if cls == EXISTS:
        if not context.namespace_present:
            return expr.EVALUATION_FAILED
        if not context.environment_present:
            watch_environment(46)
            return False
        watch_environment(0)
    elif not context.possession_context_present:
        return expr.EVALUATION_FAILED

    # Native +20 selection uses the first config of 06610EBC, then F64C20
    # unwraps only the first member of nested arrays. Failure/non-entity => 0.
    value = play.evaluate_scalar(instance, cfg.get('m_entity'), initial=expr.Entity(0), watcher=watcher)
    check()
    identifier = expr.to_entity(value) & 0xFFFFFFFF
    if cls == EXISTS and not expr.valid_entity(identifier):
        return False
    target = call(context.lookup, 'entity query lookup', identifier)
    if target is not None and (type(target) is not Target or target.entity != identifier):
        unsupported('entity query lookup result')
    if cls == EXISTS:
        return target is not None
    if target is None:
        watch_environment(0 if context.environment_present else 46)
        return expr.EVALUATION_FAILED
    if target.possession is None:
        return expr.Entity(0)
    if type(target.possession) is not Possession:
        unsupported('entity query current possession')
    if watcher is not None:
        call(context.watch_possession, 'entity query possession subscription', watcher, target)
    if type(target.possession.current) is not int or not 0 <= target.possession.current <= 0xFFFFFFFF:
        unsupported('entity query current possession')
    return expr.Entity(target.possession.current)
