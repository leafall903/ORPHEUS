"""Priority predicates depend on link structure even when the scalar stays true."""
import unittest
from unittest.mock import patch

from test_game_graph_analysis import make_graph, node

from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.script import runtime


class PriorityDependencyTests(unittest.TestCase):
    def scene(self, *, stacked=False):
        variable={'$':'STUConfigVarDynamic','m_identifier':33,'m_60DB8F99':1}
        priority={'$':'STU_3ACE35FB','m_E50CF556':variable,
                  'm_012B7AF7':{'$':'STU_8A491388','m_priority':'0x0C40000000000002'}}
        definition=make_graph(1,[
            node(0,'STUStatescriptStateBooleanSwitch',state=0,fields={'m_condition':priority}),
            node(1,'STUStatescriptStateBooleanSwitch',state=1,fields={'m_condition':variable}),
            node(2,'STUStatescriptStateStack',state=2,fields={
                'm_out_Var':variable,'m_value':{'$':'STUConfigVarBool','m_value':1},
                'm_priority':{'$':'STU_8A491388','m_priority':'0x0C40000000000015'}}),
        ],states=[0,1,2])
        component=runtime.Component(1)
        component.set(33,True)
        instance=component.create(definition,init=False)
        instance.initialized=True
        candidate,ordinary,blocker=(instance.state(index) for index in range(3))
        if stacked:
            blocker.begin()
        candidate.begin()
        ordinary.begin()
        component.run_now()
        return component,instance,candidate,ordinary,blocker

    def test_insert_and_remove_equal_value_link_recompute_priority_condition(self):
        component,_,candidate,ordinary,blocker=self.scene()
        self.assertTrue(candidate.current)
        blocker.begin()
        component.run_now()
        self.assertIs(component.entity_var(33).value(),True)
        self.assertFalse(candidate.current)
        self.assertTrue(ordinary.current)
        blocker.end(False)
        component.run_now()
        self.assertTrue(candidate.current)

    def test_watcher_registered_under_existing_blocker_recovers_on_unlink(self):
        component,_,candidate,_,blocker=self.scene(stacked=True)
        self.assertFalse(candidate.current)
        blocker.end(False)
        component.run_now()
        self.assertTrue(candidate.current)

    def test_scalar_only_observer_gets_no_extra_equal_value_notifications(self):
        component,_,_,ordinary,blocker=self.scene()
        with patch.object(ordinary,'dependency_changed',wraps=ordinary.dependency_changed) as changed:
            blocker.begin()
            component.run_now()
            blocker.end(False)
            component.run_now()
        changed.assert_not_called()

    def test_priority_dependency_mode_is_stored_in_existing_watch_maps(self):
        component,_,candidate,_,_=self.scene()
        variable=component.entity_var(33)
        self.assertIs(candidate.watched[variable],True)
        before=snapshot_digest(export_component_vm(component))
        candidate.watched[variable]=None
        variable.watchers[candidate]=None
        self.assertNotEqual(before,snapshot_digest(export_component_vm(component)))


if __name__=='__main__':
    unittest.main()
