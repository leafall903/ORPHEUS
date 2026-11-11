"""Reconstructed manual Enforcer association hosting the authored 227A controller."""

from ow174.game import launch_ring
from ow174.game.script import entity_relation, expr, graph, runtime
from ow174.game.script.driver import create_initial_graphs

HERO = 0x02E00000000001CE
BODY = 0x0400000000002279
CONTROLLER = 0x040000000000227A


class ControllerWorld(runtime.World):
    def native(self, cfg, instance):
        if (cfg.get("$") == "STU_C3619A38"
                and cfg.get("m_F52607FF", {}).get("$") == "STU_B5A0CAF0"):
            # The manual sandbox declares its difficulty in the absent AI manager context.
            return expr.Asset(0x0D8000000000195F)
        return super().native(cfg, instance)


class ManualController:
    def __init__(self, entity, mode, frame, record):
        host_id = 0xE1000000 | (entity & 0xFFFFFF)
        self.host = runtime.Component(host_id, world=ControllerWorld(HERO, mode))
        self.host.frame = frame
        self.host.now = frame * self.host.frame_ms
        for instance in create_initial_graphs(self.host, record):
            instance.init()
        self.host.run_frame(frame)
        self.target = entity_relation.Target(entity, entity_relation.Relation(host_id))
        self.context = entity_relation.Context(
            entity, True, True, True, lookup=self.lookup,
            watch_environment=None, watch_relation=self.watch_relation,
        )

    def lookup(self, entity):
        return self.target if self.target.entity == entity else None

    def watch_relation(self, watcher, target, relation):
        # The association is immutable for this body generation.
        pass


def attach(body, entity, mode, frame):
    if body != BODY:
        return None
    record = graph.entity_body(CONTROLLER)
    return ManualController(entity, mode, frame, record) if record is not None else None


def launch_inputs(state, count):
    if (state.instance.graph.index, state.node.pos, count) != (0x13EB, 24, 25):
        return None
    # Explicit sandbox substitute for the unhosted engine launch table.
    words = tuple(((i * 2654435761) & 0xFFFFFFFF, ((i + 7) * 2246822519) & 0xFFFFFFFF)
                  for i in range(count))
    return launch_ring.Inputs(0.0, words)
