"""Pure-Python (stdlib only) readers for NFSU2 world files.

Nothing in this package imports ``bpy``; it works in plain CPython 3.8+ on any
OS and inside Blender's bundled Python.
"""
from .chunks import iter_chunks, walk, find, payload_start, ALIGN
from .world import (section_table, parse_object, section_objects, apply_matrix,
                    build_model_index, iter_scenery_instances, object_hash, is_preplaced,
                    SECTION_TABLE, SOLID_OBJECT, SCENERY_PACK)
from .files import find_file, resolve_tracks_dir, region_paths, read_bytes, list_regions

__all__ = [
    'iter_chunks', 'walk', 'find', 'payload_start', 'ALIGN',
    'section_table', 'parse_object', 'section_objects', 'apply_matrix',
    'build_model_index', 'iter_scenery_instances', 'object_hash', 'is_preplaced',
    'SECTION_TABLE', 'SOLID_OBJECT', 'SCENERY_PACK',
    'find_file', 'resolve_tracks_dir', 'region_paths', 'read_bytes', 'list_regions',
]
