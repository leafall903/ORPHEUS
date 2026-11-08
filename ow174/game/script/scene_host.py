"""Match-owned strict scene components sharing actual entity bindings.

The owner supplies created entities, component definitions and the frame epoch.
This installs no guessed engine consumers or auxiliary scope/rewind phases.
"""

from dataclasses import dataclass

from ow174.game.script import graph, play_queue, runtime, scene, startup


@dataclass
class Unit:
    component: runtime.Component
    startup: startup.ComponentStartup


class SceneHost:
    def __init__(self, world: scene.SceneWorld, *, frame_ms: int = 16, seed: int = 1,
                 graph_loader=graph.graph):
        if not isinstance(world, scene.SceneWorld):
            raise ValueError('scene host needs its shared SceneWorld')
        if type(frame_ms) is not int or frame_ms <= 0 or type(seed) is not int:
            raise ValueError('invalid scene host clock or seed')
        self.world, self.frame_ms, self.seed = world, frame_ms, seed
        self.graph_loader = graph_loader
        self.units: dict[int, Unit] = {}
        self.frame: int | None = None
        self.prepared = False
        self.closed = False
        self.error: dict | None = None
        self.play_requests_configured = False

    def add(self, entity: int, definition: dict, placed: dict | None = None, *, values=None):
        """Bind every component before evaluating any component's startup schema."""
        if self.prepared or self.closed or self.error is not None or self.play_requests_configured:
            raise ValueError('initial scene component catalog is already sealed')
        if entity in self.units:
            raise ValueError('scene already has this component lifecycle')
        component = runtime.Component(entity, self.frame_ms, self.world, self.seed ^ entity, strict=True)
        self.world.bind_script_component(entity, component)
        for key, value in (values or {}).items():
            component.set(key, value)
        boot = startup.ComponentStartup(component, definition, placed, graph_loader=self.graph_loader)
        self.units[entity] = Unit(component, boot)
        return component

    @classmethod
    def from_map(cls, data, *, entity_start, server_placeables, kept_identifiers=(), mode,
                 mode_component=None, mode_values=None, frame_ms=16, seed=1, graph_loader=graph.graph):
        world = scene.SceneWorld.from_map(data, entity_start=entity_start,
            server_placeables=server_placeables, kept_identifiers=kept_identifiers, mode=mode)
        host = cls(world, frame_ms=frame_ms, seed=seed, graph_loader=graph_loader)
        if mode_component is not None:
            world.bind(runtime.GAME_MODE_ENTITY)
            host.add(runtime.GAME_MODE_ENTITY, mode_component, values=mode_values)
        for entity in tuple(world.entities.values()):
            if entity.placement is None:
                continue
            placement = entity.placement
            entries = world.definitions[placement.definition]['components']['entries']
            definitions = [row['value'] for row in entries if row['value']['$'] == 'STUStatescriptComponent']
            placed = [row['fields'] for row in placement.source['components']
                      if row['class'] == 'STUStatescriptComponentInstanceData']
            if len(definitions) > 1 or len(placed) > 1:
                raise ValueError('scene has ambiguous script component ownership')
            if definitions:
                host.add(entity.id, definitions[0], placed[0] if placed else None)
            else:
                world.bind_script_component(entity.id, None)
        return host

    def advance(self, frame: int) -> bool:
        """Advance the supplied ordinary frame; never synthesize skipped frames."""
        if self.closed or self.error is not None:
            return False
        if type(frame) is not int or not 0 <= frame < 0x80000000:
            raise ValueError('scene frame must be a nonnegative signed32 frame')
        if frame == self.frame:
            return True
        if self.frame is not None and frame != self.frame + 1:
            raise ValueError('scene frame gap or rewind needs a new explicit lifecycle')
        self.frame = frame
        try:
            for unit in self.units.values():
                unit.component.frame = frame
                unit.component.now = frame * self.frame_ms
            if not self.prepared:
                for unit in self.units.values():
                    unit.startup.start()
                self.prepared = True
            for unit in self.units.values():
                if (queue := self.world.play_queues.get(unit.component.entity)) is not None:
                    queue.flush()
                unit.component.run_frame(frame)
        except (runtime.UnsupportedScriptError, runtime.ScriptBudgetError, ValueError) as error:
            self.error = {'error': str(error), 'frame': frame}
            if isinstance(error, runtime.UnsupportedScriptError):
                self.error['diagnostic'] = error.diagnostic
            return False
        return True

    def close(self) -> None:
        """Stop scheduling, destroy live VMs, then retire this world's bindings."""
        if self.closed:
            return
        self.closed = True
        for queue in tuple(self.world.play_queues.values()):
            queue.retire()
        for unit in reversed(tuple(self.units.values())):
            component = unit.component
            if component.fault is not None:
                continue
            try:
                for instance in list(component.instances.values()):
                    component.destroy(instance)
            except (runtime.UnsupportedScriptError, runtime.ScriptBudgetError, ValueError) as error:
                if self.error is None:
                    self.error = {'error': str(error), 'phase': 'close'}
        for entity in tuple(self.world.entities):
            self.world.unbind(entity)

    def snapshot(self):
        """Status only; not a restorable shared-world storage artifact."""
        return {'frame': self.frame, 'prepared': self.prepared, 'closed': self.closed,
                'client_ready': self.client_ready,
                'error': self.error, 'components': len(self.units),
                'instances': sum(len(unit.component.instances) for unit in self.units.values())}

    def configure_play_requests(self, acquire) -> None:
        """Install an explicit resource provider for this initial component catalog."""
        if self.prepared or self.closed or self.error is not None or not callable(acquire):
            raise ValueError('PlayScript resources must be configured before scene startup')
        if self.play_requests_configured or any(entity in self.world.play_queues for entity in self.units):
            raise ValueError('scene already has configured PlayScript queues')
        if any(self.world.script_components.get(entity) is not unit.component
               or unit.component.fault is not None
               for entity, unit in self.units.items()):
            raise ValueError('scene component lifecycle changed before PlayScript configuration')
        for entity, unit in self.units.items():
            self.world.set_play_queue(entity, play_queue.Queue(unit.component, acquire))
        self.play_requests_configured = True

    @property
    def client_ready(self):
        # Live scene replication/readiness has not been installed. A successful
        # Python VM pass cannot authorize the prepared entrance readiness frame.
        return False
