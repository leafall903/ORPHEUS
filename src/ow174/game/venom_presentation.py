"""Bind original deployed-mine presentation to its existing 00AE actor.

The gameplay mine/poison reconstruction stays in venom.Mines. This projection
hosts the original 25B4 constructor and 0057 spawn graph for remote state delivery;
it does not supply a second target search, damage path, or native rendering proof.
"""

from ow174.game import observers
from ow174.game.script import expr, graph, runtime

CONSTRUCTOR = 0x25B4  # 00AE STUStatescriptComponent, original 104319 definition.
DEPLOYED = 0x0057  # 0056/1 m_49CBA3BC.m_410BF8BD.


class MineWorld(runtime.World):
    def __init__(self, fight, mine, entity):
        super().__init__(hero=0x02E000000000000A, mode=fight.match.game_map.mode_guid)
        self.fight, self.mine, self.entity = fight, mine, entity

    def position(self, entity):
        if entity == self.entity:
            return expr.Vec3(*self.mine.position)
        return self.fight.match.script_body_position(entity, self.entity)


def sync(manager, mine, entity, client):
    bind = getattr(client, "bind_actor_component", None)
    if bind is None:
        return
    generation = client.actor_generation(entity)
    if generation is None:
        return
    body = manager.presentations.get(mine.number)
    if body is None:
        born = mine.ready - manager.properties["arm"]
        component = runtime.Component(entity, world=MineWorld(manager.combat, mine, entity), seed=entity)
        component.frame, component.now = born, born * component.frame_ms
        for index in (CONSTRUCTOR, DEPLOYED):
            instance = component.create(graph.graph(index), init=False)
            instance.set_source_entity(mine.body)
            instance.init()
            instance.start_entries((), mine.body)
        component.run_now()
        body = manager.presentations[mine.number] = observers.SceneBody(component)
    component = body.component
    while component.frame < manager.combat.tick:
        component.run_frame(component.frame + 1)
    body.refresh()
    if bind(entity, body, generation=generation):
        client.queue_bot_live(entity, body.latest_live)
