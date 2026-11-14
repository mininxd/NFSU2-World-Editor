"""File > Import > NFSU2 World (.BUN)."""
import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper

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
    textures: BoolProperty(
        name='Load Textures',
        description='Decode the game textures (DXT1/DXT3/P8) and build image materials',
        default=True)
    skip_panoramas: BoolProperty(
        name='Skip Backdrops (PAN_*)',
        description='Leave out the distant panorama/skydome models around the map',
        default=False)
    collision: BoolProperty(
        name='Show Collision',
        description='Add a wireframe COLLISION_<section> mesh of the drivable surfaces and walls (read only)',
        default=False)
    lighting: EnumProperty(
        name='Lighting',
        description='How the materials use the baked (night-time) prelight vertex colours',
        items=(('scene', 'Scene Lights', 'Texture is the albedo; your sun/sky lights it'),
               ('game', 'Game Prelight', 'Texture x baked prelight x 2, like the game (dark under a sun)')),
        default='scene')
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
            stats = importer.import_world(tracks, region, sections, self.instances, context=context,
                                          textures=self.textures, skip_panoramas=self.skip_panoramas,
                                          collision=self.collision, lighting=self.lighting)
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
        layout.prop(self, 'textures')
        layout.prop(self, 'skip_panoramas')
        layout.prop(self, 'lighting')
        layout.prop(self, 'collision')
        layout.prop(self, 'clear')


class EXPORT_SCENE_OT_nfsu2_world(bpy.types.Operator, ExportHelper):
    """Write edited NFSU2 sections back to STREAML4Rx.BUN + L4Rx.BUN in a new folder"""
    bl_idname = 'export_scene.nfsu2_world'
    bl_label = 'Export NFSU2 World'
    bl_options = {'REGISTER'}

    filename_ext = '.BUN'
    filter_glob: StringProperty(default='*.bun;*.BUN', options={'HIDDEN'})
    tracks_dir: StringProperty(
        name='Source TRACKS Folder',
        description='Original files the sections were imported from (empty = remembered at import)',
        subtype='DIR_PATH', default='')
    place_scenery: BoolProperty(
        name='Place New Objects',
        description='Add a scenery placement for every new object so the game draws it',
        default=True)
    rebuild_collision: BoolProperty(
        name='Collision for New Objects',
        description='Add drivable collision from the upward faces of new objects '
                    '(custom property nfs_collision = 0 skips an object, nfs_surface sets the surface id)',
        default=True)
    collision_walls: BoolProperty(
        name='Steep Faces as Walls',
        description='Turn steep faces of new objects into barrier walls',
        default=False)
    move_instances: BoolProperty(
        name='Write Moved Props',
        description='Write back moved/rotated scenery instances (trees, signs, poles...)',
        default=True)

    def invoke(self, context, event):
        from .exporter import section_collections
        colls = section_collections(context.scene)
        region = (colls[0].get('nfs_region') if colls else None) or 'L4RA'
        if not self.filepath:
            self.filepath = 'STREAM%s.BUN' % region
        return super().invoke(context, event)

    def execute(self, context):
        from . import exporter
        out_dir = os.path.dirname(bpy.path.abspath(self.filepath))
        try:
            st = exporter.export_world(out_dir, context,
                                       tracks=bpy.path.abspath(self.tracks_dir) if self.tracks_dir else None,
                                       place_scenery=self.place_scenery,
                                       rebuild_collision=self.rebuild_collision,
                                       collision_walls=self.collision_walls,
                                       move_instances=self.move_instances)
        except (RuntimeError, ValueError, FileNotFoundError, KeyError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, 'NFSU2: %d edited, %d new object(s), %d moved prop(s), +%d collision polygons -> %s'
                    % (st['edited'], st['added'], st['moved'], st['collision_polys'], st['stream']))
        return {'FINISHED'}



class IMPORT_SCENE_OT_nfsu2_car(bpy.types.Operator, ImportHelper):
    """Import a Need for Speed: Underground 2 car (GEOMETRY.BIN + TEXTURES.BIN)"""
    bl_idname = 'import_scene.nfsu2_car'
    bl_label = 'Import NFSU2 Car'
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    filename_ext = '.bin'
    filter_glob: StringProperty(default='*.bin;*.BIN', options={'HIDDEN'})
    kit: StringProperty(name='Kit', default='KIT00',
                        description='Body kit to assemble (KIT00 is stock)')
    lod: StringProperty(name='LOD', default='A',
                        description='Preferred detail level (A is the highest)')
    paint: StringProperty(
        name='Paint (R,G,B)', default='0.75,0.06,0.07',
        description='CARSKIN colour as 0-1 RGB, applied to the paintable body')
    textures: BoolProperty(name='Load Textures', default=True)
    global_dir: StringProperty(
        name='GLOBAL Folder', description='Game GLOBAL folder for wheel axles and shared textures',
        subtype='DIR_PATH', default='')
    clear: BoolProperty(name='Clear Scene First', default=False)

    def execute(self, context):
        from . import car_importer, importer
        if self.clear:
            importer.clear_scene()
        try:
            paint = [float(x) for x in self.paint.split(',')]
            if len(paint) == 3:
                paint.append(1.0)
            root = car_importer.import_car(
                self.filepath, kit=self.kit.strip() or 'KIT00', lod=self.lod.strip() or 'A',
                paint=tuple(paint), textures=self.textures,
                global_dir=self.global_dir.strip() or None, context=context)
        except (FileNotFoundError, ValueError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, 'NFSU2: imported car %s' % root.name)
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, 'kit')
        layout.prop(self, 'lod')
        layout.prop(self, 'paint')
        layout.prop(self, 'textures')
        layout.prop(self, 'global_dir')
        layout.prop(self, 'clear')


class EXPORT_SCENE_OT_nfsu2_car(bpy.types.Operator, ExportHelper):
    """Write an edited NFSU2 car back to a new GEOMETRY.BIN (never overwrites the source)"""
    bl_idname = 'export_scene.nfsu2_car'
    bl_label = 'Export NFSU2 Car'
    bl_options = {'REGISTER'}

    filename_ext = '.BIN'
    filter_glob: StringProperty(default='*.bin;*.BIN', options={'HIDDEN'})
    source: StringProperty(
        name='Source GEOMETRY.BIN',
        description='Original file the car was imported from (empty = remembered at import)',
        subtype='FILE_PATH', default='')

    def execute(self, context):
        from . import car_importer
        try:
            st = car_importer.export_car(self.filepath, context,
                                         source=bpy.path.abspath(self.source) if self.source else None)
        except (RuntimeError, ValueError, FileNotFoundError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, 'NFSU2: exported car %s (%d part(s) re-encoded) -> %s'
                    % (st['name'], st['edited'], st['path']))
        return {'FINISHED'}



def menu_func_export(self, context):
    self.layout.operator(EXPORT_SCENE_OT_nfsu2_world.bl_idname, text='NFSU2 World (.BUN)')
    self.layout.operator(EXPORT_SCENE_OT_nfsu2_car.bl_idname, text='NFSU2 Car (GEOMETRY.BIN)')


def menu_func_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_nfsu2_world.bl_idname, text='NFSU2 World (.BUN)')
    self.layout.operator(IMPORT_SCENE_OT_nfsu2_car.bl_idname, text='NFSU2 Car (GEOMETRY.BIN)')


classes = (IMPORT_SCENE_OT_nfsu2_world, EXPORT_SCENE_OT_nfsu2_world,
           IMPORT_SCENE_OT_nfsu2_car, EXPORT_SCENE_OT_nfsu2_car)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
