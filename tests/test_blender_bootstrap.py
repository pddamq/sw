"""Exercise bootstrap scheduling with a synthetic bpy module, not a Blender runtime."""
import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import pytest


@pytest.mark.parametrize('has_scene', [True, False])
def test_bootstrap_owns_startup_port_and_disables_updates(monkeypatch, has_scene):
    scene = NS(name='Scene', blendermcp_auto_start_server=True)
    scheduled, events = [], []
    bpy = ModuleType('bpy')
    bpy.context = NS(scene=scene if has_scene else None)
    bpy.data = NS(scenes=[scene])
    bpy.types = NS()
    bpy.app = NS(timers=NS(register=lambda callback, **kwargs: scheduled.append(callback)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setenv('FORMA_BLENDER_PORT', '12345')
    monkeypatch.setenv('FORMA_BLENDER_TAG', 'Forma_fixture')
    monkeypatch.delenv('BLENDERMCP_NO_UPDATE_CHECK', raising=False)
    # run_path imports pkgutil before loading our synthetic addon spec.
    import pkgutil
    class Server:
        def __init__(self, host, port):
            self.host, self.port, self.running = host, port, False
        def start(self):
            self.running = True
            events.append('start')
    class Loader:
        def create_module(self, spec):
            return None
        def exec_module(self, addon):
            addon.register = lambda: events.append('register')
            addon._blendermcp_unregister_auto_start = lambda: events.append('cancel-default-start')
            addon.BlenderMCPServer = Server
    spec = importlib.util.spec_from_loader('forma_blender_addon', Loader())
    monkeypatch.setattr(importlib.util, 'spec_from_file_location', lambda *a, **kw: spec)
    monkeypatch.setitem(sys.modules, 'forma_blender_addon', ModuleType('forma_blender_addon'))
    monkeypatch.setattr(sys, 'path', sys.path[:])
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'packaging' / 'blender_bootstrap.py'))
    assert events == ['register', 'cancel-default-start']
    assert scene.blendermcp_auto_start_server is False
    assert len(scheduled) == 1
    if not has_scene:
        assert scheduled[0]() == 0.5
        assert events == ['register', 'cancel-default-start']
        bpy.context.scene = scene
    assert scheduled[0]() is None
    assert scene.name == 'Forma_fixture' and scene.blendermcp_server_running
    assert bpy.types.blendermcp_server.host == '127.0.0.1'
    assert bpy.types.blendermcp_server.port == 12345
    import os
    assert os.environ['BLENDERMCP_NO_UPDATE_CHECK'] == '1'
    assert events == ['register', 'cancel-default-start', 'start']
