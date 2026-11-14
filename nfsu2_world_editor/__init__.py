"""NFSU2 World Editor - Blender add-on and stdlib-only tools for Need for Speed:
Underground 2 world files.

The ``core`` and ``cli`` sub-packages do not need Blender, so e.g.
``python -m nfsu2_world_editor.cli.chunkdump FILE`` works in any Python 3.8+.
"""
bl_info = {
    'name': 'NFSU2 World Editor',
    'author': 'mininxd',
    'version': (0, 4, 0),
    'blender': (4, 2, 0),
    'location': 'File > Import / Export > NFSU2 World (.BUN) / NFSU2 Car (GEOMETRY.BIN)',
    'description': 'Import, edit and export Need for Speed: Underground 2 world and car geometry and textures',
    'category': 'Import-Export',
}

__version__ = '.'.join(map(str, bl_info['version']))


def register():
    from .blender import operators
    operators.register()


def unregister():
    from .blender import operators
    operators.unregister()
