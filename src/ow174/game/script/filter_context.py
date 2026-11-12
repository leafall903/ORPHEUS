"""D0DA20 namespace/cache entry and missing-target observations.

The owner must bind namespace lookup, source/component data and dependency hubs.
This does not infer native categories/statuses or install a production body host.
Cache acquisition is an explicit backend service; native thread spinning and
allocator/encryption storage are not reconstructed by this logical cache view.
"""

from dataclasses import dataclass, fields

from ow174.game.script import filter_pipeline


def _u32(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError('filter lookup identity/lease must be uint32')
    return value


@dataclass
class Cache:
    """4096 low-key buckets of ordered (full identity, resolved object) records.

    locked selects the native +8 acquisition flag; word is its +C lock word.
    A successful acquire callback supplies the actual acquired lease. Its own
    exception cleanup is the backend's responsibility. The native release word
    update runs after a successful callback even if owner validation then fails.
    """

    buckets: object = NotImplemented
    locked: bool = False
    word: int = 0
    acquire: object = NotImplemented

    def lookup(self, identifier, *, check=lambda: None):
        identifier = _u32(identifier)
        if type(self.locked) is not bool:
            raise ValueError('filter cache acquisition flag must be explicit bool')
        unlock = self.locked
        if unlock:
            _u32(self.word)
            if not callable(self.acquire):
                raise ValueError('native cache acquisition service is unavailable')
            self.acquire(self)
            # D0DAB1 reloads the saved pre-acquisition flag from the stack;
            # a backend mutation of cache+8 does not cancel this lease release.
        try:
            check()
            if type(self.buckets) is not dict:
                raise ValueError('native cache bucket storage is unavailable')
            bucket = self.buckets.get(identifier & 0xFFF, ())
            if type(bucket) is not tuple:
                raise ValueError('filter cache bucket must be an ordered tuple')
            for entry in bucket:
                if type(entry) is not tuple or len(entry) != 2:
                    raise ValueError('invalid filter cache record')
                key, subject = entry
                if _u32(key) == identifier:
                    return subject  # a matching null pointer is a miss, not a later duplicate
            return None
        finally:
            if unlock:
                remaining = (_u32(self.word) - 0x1000) & 0xFFFFFFFF
                self.word = remaining if remaining & 0xFFFFF000 else 0


@dataclass
class Context:
    owner: int
    lookup: object = NotImplemented
    environment: object = NotImplemented
    instance_environment: object = NotImplemented
    dependency_hub: object = NotImplemented
    watch_environment: object = NotImplemented
    services: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        _u32(self.owner)

    def retire(self):
        self.retired = True


def accepts(evaluation, config, identifier, *, cache=None):
    """Explicit target argument used by TrackTargets' namespace virtual150.

    This is not wired to the bare STUConfigVarFilter evaluator: D25690's implicit
    subject selection is a separate boundary, not guessed from a candidate scope.
    """
    if config is None:
        return True
    if not isinstance(config, dict) or config.get('$') != 'STUConfigVarFilter':
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_filter_context(component.entity, component=component)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, 'STUConfigVarFilter')

    def check():
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity
                or type(context.retired) is not bool or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or component.world.entity_filter_context(
                    component.entity, component=component) is not context):
            unsupported('entity filter owner retired during callback')

    def call(callback, name, *args):
        check()
        if not callable(callback):
            unsupported(f'entity filter {name}')
        result = callback(*args)
        check()
        return result

    def wrap(callback, name):
        return lambda *args: call(callback, name, *args)

    with component.operation():
        check()
        try:
            _u32(identifier)
            if cache is None:
                target = call(context.lookup, 'namespace lookup', identifier)
            elif type(cache) is Cache:
                target = cache.lookup(identifier, check=check)
                check()
            else:
                unsupported('entity filter cache availability')
            if target is None:
                environment = cache if cache is not None else call(context.environment, 'environment lookup')
                if watcher is not None:
                    kind = 0
                    if environment is None:
                        environment = call(context.instance_environment, 'instance environment lookup',
                                           instance)
                        kind = 46
                    if environment is not None:
                        hub = call(context.dependency_hub, 'dependency hub lookup', environment)
                        if hub is not None:
                            call(context.watch_environment, 'environment subscription', hub, watcher, kind)
                return False
            if type(target) is not filter_pipeline.Subject or target.entity != identifier:
                unsupported('entity filter namespace result')
            services = context.services
            if type(services) is not filter_pipeline.Services:
                unsupported('entity filter resolved services')
            wrapped = filter_pipeline.Services(**{
                item.name: wrap(getattr(services, item.name), item.name) for item in fields(services)
            })

            def inventory(subject):
                result = call(services.inventory, 'inventory', subject)
                if type(result) is filter_pipeline.Inventory:
                    return filter_pipeline.Inventory(**{
                        item.name: wrap(getattr(result, item.name), 'inventory '+item.name)
                        for item in fields(result)
                    })
                return result

            wrapped.inventory = inventory
            result = filter_pipeline.resolved(instance, config, target, NotImplemented,
                                               wrapped, watcher=watcher)
            check()
            return result
        except ValueError as error:
            unsupported(str(error))
