# NFSU2 World Editor

Blender add-on and command-line tools for reading (and, later, editing) **Need for Speed: Underground 2** world geometry. Works on **Windows and Linux** (macOS should work too, untested).

> This repository contains source code only. No game files are included or ever committed; you need your own legal copy of NFSU2.

[![CI](https://github.com/mininxd/NFSU2-World-Editor/actions/workflows/ci.yml/badge.svg)](https://github.com/mininxd/NFSU2-World-Editor/actions/workflows/ci.yml)

## Status
Milestone 1, a **read-only import**:
- Chunk walker and ID histogram for `.BUN`/`.BIN` files
- Section table (`0x00034110`) parser for `L4R?.BUN`
- SolidObject (`0x80134010`) parser: vertices, triangles, UVs, vertex colours, materials
- Blender importer for selected streaming sections, with scenery instances (trees, walls, props)

Planned: real textures, writing edited meshes back into `STREAML4RA.BUN`, collision, and the road network.

## Requirements
- Blender **4.2 or newer**
- Python 3.8+ for the command-line tools (stdlib only; `matplotlib` is needed only for `sectionmap`)
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
   blender --command extension install-file -r user_default -e dist/nfsu2_world_editor-0.2.0.zip
   ```

## Use it in Blender
**File > Import > NFSU2 World (.BUN)**, then select `STREAML4RA.BUN` (or `L4RA.BUN`) inside `TRACKS`. Options:

| Option | Meaning |
|---|---|
| TRACKS Folder | Optional override. You can give the `TRACKS` folder or the game folder that contains it |
| Region | e.g. `L4RA` (Bayview). Leave empty to detect it from the file name |
| Sections | Comma-separated section tags, e.g. `C21,C22,B26` (list them with the `sections` tool below) |
| Include Scenery Instances | Also place props such as trees, walls, and signs |
| Clear Scene First | Delete existing objects before importing |

Each object keeps `nfs_*` custom properties (section, hash, file offset) for the future exporter. The game uses Z-up, and coordinates are kept 1:1.

## Command-line tools
Run these from the repository folder. On Windows, use `py` instead of `python3` if needed, and either kind of slash works in paths.

```
# list streaming sections of a region
python3 -m nfsu2_world_editor.cli.sections --tracks "/path/to/NFSU2/TRACKS" --region L4RA

# chunk histogram / tree of any .BUN or .BIN
python3 -m nfsu2_world_editor.cli.chunkdump --tracks "/path/to/TRACKS" --file STREAML4RA.BUN --tree 2

# top-down map of all sections (pip install matplotlib)
python3 -m nfsu2_world_editor.cli.sectionmap --tracks "/path/to/TRACKS" --out section_map.png

# headless Blender import without installing the add-on: save .blend/.obj and render a preview
blender -b --factory-startup --python tools/import_world.py -- --tracks "C:/Games/NFSU2/TRACKS" --sections C21,C22,B26 --instances --blend out.blend --png preview.png
```

## Development
```
python -m pip install pytest
python -m pytest            # synthetic test data only, no game files needed
python build_addon.py       # build the add-on zip into dist/
```
Layout:
- `nfsu2_world_editor/core/`: pure-Python parser (`chunks`, `world`, `files`, and `writer` for test data and the future exporter). No `bpy`, no numpy.
- `nfsu2_world_editor/blender/`: importer and File > Import operator
- `nfsu2_world_editor/cli/`: command-line tools
- `tools/import_world.py`: headless Blender wrapper
- `tests/`: pytest suite. CI runs it on Ubuntu and Windows and builds the zip.

**Never commit game files** or anything derived from them (`.BUN`, `.BIN`, `.blend`, `.obj`, renders, dumps). `.gitignore` blocks them.

## Bahasa Indonesia (ringkas)
Add-on Blender dan tool command-line buat membaca world NFSU2. Bisa jalan di **Windows dan Linux**. Repo ini cuma berisi source code, file game **tidak** disertakan.

**Instal:**
1. Ambil zip add-on dari tab *Actions* (bagian Artifacts), atau buat sendiri dengan `python build_addon.py` (di Windows: `py build_addon.py`). Hasilnya ada di folder `dist/`.
2. Di Blender 4.2+, buka **Edit > Preferences > Get Extensions > ⌄ > Install from Disk…**, lalu pilih file zip-nya.
3. Buka **File > Import > NFSU2 World (.BUN)**, pilih `STREAML4RA.BUN` di folder `TRACKS` game kamu, lalu isi *Sections* (contoh `C21,C22,B26`). Besar-kecil huruf nama file nggak masalah.

Daftar section bisa dilihat dengan `python -m nfsu2_world_editor.cli.sections --tracks "<folder TRACKS>"`.

## Format references
- [FRSWorlsEditor NFSU2 format docs](https://github.com/chrystianfarias/FRSWorlsEditor)
- [OpenUG2 format docs](https://github.com/whoismept/OpenUG2)
