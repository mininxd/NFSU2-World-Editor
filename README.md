# NFSU2 World Editor

Blender add-on and command-line tools for **converting Need for Speed: Underground 2 worlds to editable formats and back**: geometry and textures. Works on **Windows and Linux** (macOS should work too, untested).

> This repository contains source code only. No game files are included or ever committed; you need your own legal copy of NFSU2.

[![CI](https://github.com/mininxd/NFSU2-World-Editor/actions/workflows/ci.yml/badge.svg)](https://github.com/mininxd/NFSU2-World-Editor/actions/workflows/ci.yml)

## Status
A **two-way converter** for world geometry and textures: game files → editable formats (Blender scene, PNG/TGA) → back into game files.

| | Game → editable | Editable → game |
|---|---|---|
| Geometry (`0x80134010` SolidObjects) | Blender import of any sections, with scenery instances | Blender export: edited meshes (positions, UVs, prelight colours, any vertex/triangle count ≤ 65535 verts) and **new objects** |
| Textures (`0xB3300000` packs) | PNG/TGA export; real UV-mapped image materials in Blender (alpha only where the game uses it) | Re-import of edited PNG/TGA: re-encoded to the original format (DXT1/DXT3/DXT5/P8/ARGB8), same size, mips regenerated |
| Section layout | parsed | sections stay on 2 KiB boundaries; `L4Rx.BUN` section table (offset / size / size-without-pixels) updated |

**Round trip is lossless.** Parsing and rebuilding produces byte-identical `STREAM*.BUN` and `L4R*.BUN` for all eight shipped regions. Exporting without edits (from Blender or the texture tool) reproduces the original files byte for byte, because only edited objects or textures are re-encoded. Re-encoding every one of the 10,735 L4RA objects from decoded data gives identical bytes for all but one (a shipped object whose stored material bounds don't match its vertices).

> ⚠️ **Collision is not rebuilt yet.** The collision pack (`0x80034130`) isn't updated. Edited or new geometry draws in game, but cars still drive on the old collision surface: they'll fall through a raised road or hit invisible walls. Rebuilding collision is the next milestone. Brand-new objects get added to the section's solid list, but the game may also need a scenery placement or preculler entry before it draws them. That part is still unverified in game.

Other limitations: textures can't be resized or added yet (same size and format only). Scenery-instance transforms aren't written back. Textures from `GLOBAL\*.BUN` (a few effect sprites) aren't loaded. The game, not this tool, decides whether it accepts an edit, so keep backups and test in game.

## Requirements
- Blender **4.2 or newer**
- Python 3.8+ for the command-line tools (stdlib only; `numpy` is used automatically if installed for faster texture work, and `matplotlib` is needed only for `sectionmap`)
- Your NFSU2 `TRACKS` folder, e.g. `C:\Program Files (x86)\EA GAMES\Need for Speed Underground 2\TRACKS` on Windows, or wherever your copy lives on Linux (a Wine/Proton prefix or copied files). File-name case does not matter.

## Install the Blender add-on
1. Download `nfsu2_world_editor-<version>.zip` from the latest [CI run](https://github.com/mininxd/NFSU2-World-Editor/actions) (Artifacts), or build it yourself:
   ```
   python build_addon.py          # Windows: py build_addon.py
   ```
   The zip is written to `dist/`.
2. In Blender go to **Edit > Preferences > Get Extensions**, open the **⌄** menu (top right), choose **Install from Disk…** and pick the zip. Older-style install also works: **Preferences > Add-ons > ⌄ > Install from Disk…**.
3. Command-line install (same on both OSes):
   ```
   blender --command extension install-file -r user_default -e dist/nfsu2_world_editor-0.3.0.zip
   ```

## Use it in Blender
**File > Import > NFSU2 World (.BUN)**, then select `STREAML4RA.BUN` (or `L4RA.BUN`) inside `TRACKS`. Options:

| Option | Meaning |
|---|---|
| TRACKS Folder | Optional override. You can give the `TRACKS` folder or the game folder that contains it |
| Region | e.g. `L4RA` (Bayview). Leave empty to detect it from the file name |
| Sections | Comma-separated section tags, e.g. `C21,C22,B26`, or `all` for the whole region (list them with the `sections` tool below) |
| Include Scenery Instances | Also place props such as trees, walls, and signs |
| Load Textures | Decode the game textures and build image materials (texture × baked light × 2, like the game) |
| Clear Scene First | Delete existing objects before importing |

Each object keeps `nfs_*` custom properties (section, hash, file offset, and a checksum) that the exporter uses. The game uses Z-up, and coordinates are kept 1:1. To see textures in the viewport, switch Solid shading to **Texture** colour, or use Material Preview.

### Round-trip workflow (edit → game)
1. **Import** the sections you want to edit (File > Import > NFSU2 World).
2. **Edit**:
   - move, scale or rotate objects, or edit vertices, UVs and the `prelight` colour attribute. Adding or removing faces is fine too;
   - assign any imported `tex_<HASH>` material to faces to use that game texture;
   - **add new geometry**: create a mesh object and link it **directly** into a `SECTION_<tag>` collection. It's exported as a new object named after the Blender object (world-space vertices).
3. **Export** with **File > Export > NFSU2 World (.BUN)** and pick an **output folder** (not your game folder; the tool refuses to overwrite the originals). It writes `STREAML4RA.BUN` + `L4RA.BUN` there, re-encoding only what changed.
4. Back up your game's `TRACKS\STREAML4RA.BUN` and `TRACKS\L4RA.BUN`, copy both new files in, and test.

Headless alternative: `blender -b edited.blend --python tools/export_world.py -- --out OUTDIR`.

Want to check the whole chain on your own files first? `tools/selftest_roundtrip.py` does a no-edit export (must be byte-identical), lifts a road by 3 m, subdivides a mesh, adds an object, re-imports the result and checks that every other section is unchanged:
```
blender -b --factory-startup --python tools/selftest_roundtrip.py -- --tracks "C:/Games/NFSU2/TRACKS" --out OUTDIR
```

### Textures (edit → game)
```
# 1. export every texture of the region (or --sections C22,B26) to PNG (+ textures.json)
python3 -m nfsu2_world_editor.cli.textures export --tracks "/path/to/TRACKS" --out tex
#    list without writing:  ... textures export --tracks "/path/to/TRACKS" --list
# 2. edit any PNG in a paint program (keep its size), then re-import into a new BUN
python3 -m nfsu2_world_editor.cli.textures import --tracks "/path/to/TRACKS" --dir tex --out mod
```
Files are named `<HASH>_<NAME>.png`. Only images whose pixels changed get re-encoded, into every pack that holds that texture. DXT uses a built-in pure-Python encoder. For P8, the texture's palette is kept when it still fits, otherwise a new 256-colour palette is built. In L4RA there are 1,611 unique textures (1,836 records in 206 packs): DXT1 1,417, DXT3 171, P8 23. Alpha only means transparency where the texture header says so (`+0x55` usage, `+0x56` blend). For road textures, alpha is a wet-gloss mask, so it's exported as-is but ignored by the Blender materials.

## Command-line tools
Run these from the repository folder. On Windows, use `py` instead of `python3` if needed, and either kind of slash works in paths.

```
# list streaming sections of a region
python3 -m nfsu2_world_editor.cli.sections --tracks "/path/to/NFSU2/TRACKS" --region L4RA

# chunk histogram / tree of any .BUN or .BIN
python3 -m nfsu2_world_editor.cli.chunkdump --tracks "/path/to/TRACKS" --file STREAML4RA.BUN --tree 2

# verify the converter on your files: rebuild must be byte-identical (+ mesh re-encode & texture census)
python3 -m nfsu2_world_editor.cli.roundtrip --tracks "/path/to/TRACKS" --region L4RA --meshes --textures

# top-down map of all sections (pip install matplotlib)
python3 -m nfsu2_world_editor.cli.sectionmap --tracks "/path/to/TRACKS" --out section_map.png

# headless Blender import without installing the add-on: save .blend/.obj and render a preview
blender -b --factory-startup --python tools/import_world.py -- --tracks "C:/Games/NFSU2/TRACKS" --sections C21,C22,B26 --instances --blend out.blend --png preview.png   # textured top-down render
blender -b --factory-startup --python tools/import_world.py -- --tracks "C:/Games/NFSU2/TRACKS" --sections all --instances --blend bayview_full.blend
```

## Development
```
python -m pip install pytest numpy   # numpy optional: tests cover both code paths
python -m pytest            # synthetic test data only, no game files needed
python build_addon.py       # build the add-on zip into dist/
```
Layout:
- `nfsu2_world_editor/core/`: pure Python, no `bpy`; numpy is only an optional fast path.
  - `bchunk`: byte-preserving chunk tree. Rebuilds alignment filler and padding when sizes change
  - `stream`: region read/write with 2 KiB section relocation and L4 table update
  - `meshcodec`, `solidlist`: lossless SolidObject decode/encode, adding objects
  - `tpk`, `dxt`, `image`: texture packs, DXT1/3/5, P8 and ARGB codecs, PNG/TGA
  - `chunks`, `world`, `files`, `writer`: readers used by the importer, plus synthetic test data
- `nfsu2_world_editor/blender/`: importer, textures/materials, exporter, File > Import/Export operators
- `tools/`: headless Blender import/export and the round-trip self-test
- `nfsu2_world_editor/cli/`: command-line tools
- `tools/import_world.py`: headless Blender wrapper
- `tests/`: pytest suite. CI runs it on Ubuntu and Windows and builds the zip.

**Never commit game files** or anything derived from them (`.BUN`, `.BIN`, `.blend`, `.obj`, renders, dumps, exported textures). `.gitignore` blocks them.

**Next steps:** collision rebuild (`0x80034130`: polygons, 32-unit grid, cell references, following the FRS docs). After that, scenery placement records for new objects, texture add/resize, and writing back scenery-instance transforms.

## Bahasa Indonesia (ringkas)
Add-on Blender dan tool command-line buat membaca world NFSU2. Bisa jalan di **Windows dan Linux**. Repo ini cuma berisi source code, file game **tidak** disertakan.

**Instal:**
1. Ambil zip add-on dari tab *Actions* (bagian Artifacts), atau buat sendiri dengan `python build_addon.py` (di Windows: `py build_addon.py`). Hasilnya ada di folder `dist/`.
2. Di Blender 4.2+, buka **Edit > Preferences > Get Extensions > ⌄ > Install from Disk…**, lalu pilih file zip-nya.
3. Buka **File > Import > NFSU2 World (.BUN)**, pilih `STREAML4RA.BUN` di folder `TRACKS` game kamu, lalu isi *Sections* (contoh `C21,C22,B26`). Besar-kecil huruf nama file nggak masalah.

Daftar section bisa dilihat dengan `python -m nfsu2_world_editor.cli.sections --tracks "<folder TRACKS>"`.

**Konversi dua arah (edit → balik ke game):**
- **Geometri:** import section-nya, terus edit di Blender (geser atau naikin objek, edit vertex/UV, atau tambah mesh baru langsung ke koleksi `SECTION_<tag>`). Habis itu **File > Export > NFSU2 World (.BUN)** dan pilih folder output **selain** folder game. Hasilnya `STREAML4RA.BUN` + `L4RA.BUN` baru. Kalau diekspor tanpa edit, hasilnya identik byte per byte dengan file asli.
- **Tekstur:** `python -m nfsu2_world_editor.cli.textures export --tracks "<TRACKS>" --out tex` buat dapetin PNG-nya. Edit PNG-nya (ukurannya jangan diubah), terus `... textures import --tracks "<TRACKS>" --dir tex --out mod` buat bikin BUN baru (DXT1/DXT3/P8 di-encode ulang otomatis).
- Backup dulu file aslinya, baru salin file hasil ke folder `TRACKS` game.
- ⚠️ **Collision belum di-update**, jadi geometri yang diedit kelihatan di game tapi mobil masih jalan di permukaan lama. Ini langkah berikutnya.

## Format references
- [FRSWorlsEditor NFSU2 format docs](https://github.com/chrystianfarias/FRSWorlsEditor)
- [OpenUG2 format docs](https://github.com/whoismept/OpenUG2)
