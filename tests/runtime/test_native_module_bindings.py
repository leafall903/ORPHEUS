"""Qualified preloads keep original live module binding and query order."""
import builtins
import json
import subprocess
import sys
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from ow174 import game
from ow174.game import doomfist_helper
from ow174.game import script as package
from ow174.game.script import driver, graph, runtime

TYPED = ('body_owner', 'controller_heroes', 'controller_queries', 'controller_query',
         'npc_queries', 'entity_filter', 'entity_link', 'entity_queries', 'entity_team',
         'health_queries', 'pet_relation')
ORDER = ('health_queries', 'controller_query', 'controller_queries', 'pet_relation',
         'entity_link', 'body_owner', 'npc_queries', 'entity_queries', 'controller_heroes',
         'entity_team', 'entity_filter')
STARTUP_OBSERVATIONS = []


def clone(module, **changes):
    return types.SimpleNamespace(**(vars(module) | changes))


def context(*, strict=True, watcher=None, host=None):
    definition = graph.Graph({'index': 0xFEED, 'header': {}, 'items': 0,
                              'containers': {}, 'nodes': [], 'unknown_nodes': [],
                              'states': [], 'remote': [], 'entries': [],
                              'sync_vars': [], 'fields': {}})
    component = runtime.Component(0xA0000101, world=host, strict=strict)
    instance = component.create(definition, init=False)
    return runtime.InstanceContext(instance, watcher)


class NativeModuleBindingTests(unittest.TestCase):
    def test_driver_function_module_rebind_and_missing_attribute_fallback(self):
        cfg = {'$': 'TEST_DRIVER_BINDING'}
        with patch.object(doomfist_helper, 'native', return_value=11) as native:
            self.assertEqual(driver.BodyWorld.native(object(), cfg, None), 11)
            native.return_value = 12
            self.assertEqual(driver.BodyWorld.native(object(), cfg, None), 12)
            with patch.object(game, 'doomfist_helper', clone(doomfist_helper, native=lambda *_: 13)):
                self.assertEqual(driver.BodyWorld.native(object(), cfg, None), 13)
            del game.doomfist_helper
            try:
                with patch.object(builtins, '__import__', wraps=builtins.__import__) as imports:
                    self.assertEqual(driver.BodyWorld.native(object(), cfg, None), 12)
                self.assertTrue(any(call.args[0] == 'ow174.game' for call in imports.call_args_list))
            finally:
                game.doomfist_helper = doomfist_helper

    def test_spatial_alias_binds_before_live_constant_query_and_falls_back(self):
        spatial = package.spatial_queries
        cfg = {'$': next(iter(spatial.INTERFACES))}
        rebound = clone(spatial, evaluate=lambda *_: 22)

        class Host(runtime.World):
            def constant_native(self, cfg, instance):
                package.spatial_queries = rebound
                return NotImplemented

        try:
            evaluation = context(strict=False, host=Host())
            with patch.object(spatial, 'evaluate', return_value=21):
                self.assertEqual(evaluation.native(cfg), 21)
                self.assertEqual(evaluation.native(cfg), 22)
            package.spatial_queries = spatial
            evaluation = context(strict=True)
            with patch.object(spatial, 'evaluate', return_value=23):
                self.assertEqual(evaluation.native(cfg), 23)
                del package.spatial_queries
                self.assertEqual(evaluation.native(cfg), 23)
        finally:
            package.spatial_queries = spatial

    def test_typed_function_and_module_rebind_fallback_keep_poll_and_query_order(self):
        cfg = {'$': 'TEST_TYPED_BINDING'}
        modules = {name: getattr(package, name) for name in TYPED}
        trace = []

        class Watcher:
            def __init__(self):
                self.natives, self.polled = set(), False

            def native_dependency(self, value):
                trace.append('poll')

            def ticks(self):
                trace.append('ticks')

        class Host(runtime.World):
            def constant_native(self, cfg, instance):
                raise AssertionError('strict query must not read compatibility constants')

            def native(self, cfg, instance):
                trace.append('world')
                return 99

        def query(name, result=NotImplemented):
            def evaluate(*_):
                trace.append(name)
                return result
            return evaluate

        with ExitStack() as stack:
            for name, module in modules.items():
                field = 'scalar' if name == 'controller_heroes' else 'evaluate'
                stack.enter_context(patch.object(module, field, side_effect=query(name)))
            watcher = Watcher()
            evaluation = context(watcher=watcher, host=Host())
            self.assertEqual(evaluation.native(cfg), 99)
            self.assertEqual(trace, ['poll', 'ticks', *ORDER, 'world'])
            self.assertEqual(watcher.natives, {cfg['$']})
            for name in ORDER:
                field = 'scalar' if name == 'controller_heroes' else 'evaluate'
                index = ORDER.index(name)
                trace.clear()
                with patch.object(modules[name], field, side_effect=query(name, 31)):
                    self.assertEqual(evaluation.native(cfg), 31)
                self.assertEqual(trace, ['poll', *ORDER[:index + 1]])
                trace.clear()
                with patch.object(package, name, clone(modules[name], **{field: query(name, 32)})):
                    self.assertEqual(evaluation.native(cfg), 32)
                self.assertEqual(trace, ['poll', *ORDER[:index + 1]])
            for name in TYPED:
                trace.clear()
                delattr(package, name)
                try:
                    self.assertEqual(evaluation.native(cfg), 99)
                finally:
                    setattr(package, name, modules[name])
                self.assertEqual(trace, ['poll', *ORDER, 'world'])
            # The typed aliases must bind after the existing poll callback.
            trace.clear()
            watcher.native_dependency = lambda value: setattr(
                package, 'health_queries', clone(modules['health_queries'], evaluate=query('rebound', 33)))
            try:
                self.assertEqual(evaluation.native(cfg), 33)
                self.assertEqual(trace, ['rebound'])
            finally:
                package.health_queries = modules['health_queries']

    def test_fresh_import_orders_add_no_resource_reads_or_cycles(self):
        source = Path(__file__).resolve().parents[2]
        script = r'''
import importlib,json,pathlib,sys
sys.path.insert(0,str(pathlib.Path(sys.argv[1])/'src'))
reads=[]
qualified_reads=[]
qualified_files={name+'.py' for name in sys.argv[3].split(',')}|{'doomfist_helper.py','spatial_queries.py'}
def audit(event,args):
    if event=='open' and isinstance(args[0],str):
        suffix=pathlib.Path(args[0]).suffix.lower()
        if suffix in ('.json','.gz','.xml','.bin','.00c','.003'):
            reads.append(args[0])
            frame=sys._getframe(1)
            while frame:
                if pathlib.Path(frame.f_code.co_filename).name in qualified_files:
                    qualified_reads.append(args[0]);break
                frame=frame.f_back
sys.addaudithook(audit)
orders={
 'runtime-first':['ow174.game.script.runtime','ow174.game.script.driver'],
 'driver-first':['ow174.game.script.driver','ow174.game.script.runtime'],
 'query-first':['ow174.game.script.pet_relation','ow174.game.script.spatial_queries',
                'ow174.game.doomfist_helper','ow174.game.script.runtime','ow174.game.script.driver']}
for name in orders[sys.argv[2]]:importlib.import_module(name)
qualified=['ow174.game.doomfist_helper','ow174.game.script.spatial_queries']+[
 'ow174.game.script.'+name for name in sys.argv[3].split(',')]
assert all(name in sys.modules for name in qualified)
print(json.dumps({'order':sys.argv[2],'qualifiedDefinitions':sum(name in sys.modules for name in qualified),
                  'resourceReads':reads,'qualifiedResourceReads':qualified_reads}))
'''
        STARTUP_OBSERVATIONS.clear()
        for order in ('runtime-first', 'driver-first', 'query-first'):
            result = subprocess.run([sys.executable, '-B', '-c', script, str(source), order,
                                     ','.join(TYPED)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(result.stdout)
            STARTUP_OBSERVATIONS.append(record)
            self.assertEqual(record['qualifiedResourceReads'], [])
