"""Load the bundled addon only in a new Forma-launched Blender GUI process."""
import importlib.util
import os
from pathlib import Path
import sys
import bpy

folder = Path(__file__).resolve().parent
sys.path.insert(0, str(folder / 'addon-deps'))
port = int(os.environ['FORMA_BLENDER_PORT'])
if not 1024 <= port <= 65535:
    raise ValueError('Invalid Forma Blender port')
# A private module name avoids replacing the user's installed Blender MCP addon.
spec = importlib.util.spec_from_file_location('forma_blender_addon', folder / 'addon.py')
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
os.environ['BLENDERMCP_NO_UPDATE_CHECK'] = '1'
spec.loader.exec_module(addon)
addon.register()
# Forma owns startup and the selected port; cancel the upstream default-port timer.
addon._blendermcp_unregister_auto_start()
for scene in bpy.data.scenes:
    scene.blendermcp_auto_start_server = False


def start_bridge():
    if bpy.context.scene is None:
        return 0.5
    # Do not persist plugin preferences or change the user's existing startup file.
    scene = bpy.context.scene
    scene.name = os.environ['FORMA_BLENDER_TAG']
    scene.blendermcp_port = port
    scene.blendermcp_auto_start_server = False
    old = getattr(bpy.types, 'blendermcp_server', None)
    if old and old.running:
        old.stop()
    server = addon.BlenderMCPServer(host='127.0.0.1', port=port)
    bpy.types.blendermcp_server = server
    server.start()
    scene.blendermcp_server_running = bool(server.running)
    if not server.running:
        raise RuntimeError('Forma Blender listener failed to start')
    print('Forma Blender bridge ready on loopback port', port, flush=True)
    return None


bpy.app.timers.register(start_bridge, first_interval=0.5)
