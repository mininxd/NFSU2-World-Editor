"""File > Import > NFSU2 World (.BUN)."""
import os

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..core.files import region_from_filename, resolve_tracks_dir, list_regions
from . import importer


class IMPORT_SCENE_OT_nfsu2_world(bpy.types.Operator, ImportHelper):
    """Import Need for Speed: Underground 2 world sections (STREAML4Rx.BUN)"""
    bl_idname = 'import_scene.nfsu2_world'
    bl_label = 'Import NFSU2 World'
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    filename_ext = '.bun'
    filter_glob: StringProperty(default='*.bun;*.BUN', options={'HIDDEN'})

    tracks_dir: StringProperty(
        name='TRACKS Folder',
        description='Game TRACKS folder (or the game folder containing it). '
                    'Leave empty to use the folder of the selected file',
        subtype='DIR_PATH', default='')
    region: StringProperty(
        name='Region',
        description='Region name, e.g. L4RA (Bayview). Leave empty to detect it from the '
                    'selected file name, falling back to L4RA',
        default='')
    sections: StringProperty(
        name='Sections',
        description='Comma-separated streaming section tags, e.g. C21,C22,B26. '
                    'Use "all" (or leave empty) to import the whole region',
        default='C21,C22,B26')
    instances: BoolProperty(
        name='Include Scenery Instances',
        description='Also place the scenery props (trees, poles, signs...) of each section',
        default=True)
    clear: BoolProperty(
        name='Clear Scene First',
        description='Delete all existing objects before importing',
        default=False)

    def _resolve(self):
        src = self.tracks_dir.strip() or (os.path.dirname(self.filepath) if self.filepath else '')
        tracks = resolve_tracks_dir(bpy.path.abspath(src)) if src else None
        region = self.region.strip().upper() or region_from_filename(self.filepath or '')
        if not region and tracks is not None:
            found = list_regions(tracks)
            region = 'L4RA' if 'L4RA' in found or not found else found[0]
        return tracks, region or 'L4RA'

    def execute(self, context):
        tracks, region = self._resolve()
        if tracks is None:
            self.report({'ERROR'}, 'Pick a .BUN file inside TRACKS or set the TRACKS folder')
            return {'CANCELLED'}
        sections = importer.parse_sections(self.sections)  # [] = whole region
        if self.clear:
            importer.clear_scene()
        try:
            stats = importer.import_world(tracks, region, sections, self.instances, context=context)
        except (FileNotFoundError, KeyError) as ex:
            self.report({'ERROR'}, str(ex).strip('"\''))
            return {'CANCELLED'}
        solids = sum(s[0] for s in stats.values())
        inst = sum(s[2] for s in stats.values())
        self.report({'INFO'}, 'NFSU2: imported %d section(s) of %s: %d solids, %d instances'
                    % (len(stats), region, solids, inst))
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, 'tracks_dir')
        layout.prop(self, 'region')
        layout.prop(self, 'sections')
        layout.prop(self, 'instances')
        layout.prop(self, 'clear')


def menu_func_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_nfsu2_world.bl_idname, text='NFSU2 World (.BUN)')


classes = (IMPORT_SCENE_OT_nfsu2_world,)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
