"""104319 CE5890/DB2D40/DB51F0 target-tag queries and membership."""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = 'STU_25ECB55F'


def _key(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError('expected native uint32 target-tag key')


@dataclass(frozen=True)
class Tags:
    base: tuple = ()
    added: tuple = ()
    removed: tuple = ()

    def __post_init__(self):
        for values in (self.base, self.added, self.removed):
            if type(values) is not tuple:
                raise ValueError('target-tag lists must be explicit tuples')
            for value in values:
                _key(value)

    def contains(self, key):
        """DB51F0: removal applies to a base tag; addition applies to a non-base tag."""
        _key(key)
        return key not in self.removed if key in self.base else key in self.added


@dataclass
class Target:
    entity: int
    tags: object = NotImplemented  # None declares component76 absence

    def __post_init__(self):
        _key(self.entity)
        if self.tags is not NotImplemented and self.tags is not None and type(self.tags) is not Tags:
            raise ValueError('expected target-tag component or known absence')


@dataclass
class Context:
    owner: int
    namespace_present: bool
    lookup: object = NotImplemented
    watch_tags: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        _key(self.owner)
        if not expr.valid_entity(self.owner) or type(self.namespace_present) is not bool:
            raise ValueError('expected known target-tag namespace/owner')

    def retire(self):
        self.retired = True


def evaluate(evaluation, cfg):
    if cfg.get('$') != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_tag_context(component.entity, component=component)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        bound = component.world.entity_tag_context(component.entity, component=component)
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or type(context.namespace_present) is not bool or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance or bound is not context):
            unsupported('target-tag owner/context retired during callback')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    def tags_of(target, identifier):
        if type(target) is not Target or target.entity != identifier:
            unsupported('target-tag lookup identity/result')
        if target.tags is not None and type(target.tags) is not Tags:
            unsupported('target-tag component availability')
        return target.tags

    with component.operation():
        check()
        value = play.evaluate_scalar(instance, cfg.get('m_entity'),
                                     initial=expr.Entity(0), watcher=watcher)
        check()
        identifier = expr.to_entity(value) & 0xFFFFFFFF
        if not context.namespace_present:
            # Native dereferences this service; no empty-world result is established.
            unsupported('target-tag namespace absent')
        target = call(context.lookup, 'target-tag entity lookup', identifier)
        if target is None:
            return False
        tags = tags_of(target, identifier)
        if tags is not None and watcher is not None:
            call(context.watch_tags, 'target-tag subscription', watcher, target)
        # DB2D40 obtains component76 afresh after the observation pass.
        tags = tags_of(target, identifier)
        return False if tags is None else tags.contains(expr.guid_of(cfg.get('m_88B0050A')) & 0xFFFFFFFF)
