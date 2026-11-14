# NFSU2 World Editor

Tools for reading and (eventually) editing **Need for Speed: Underground 2** world geometry with Blender.

> This repository contains source code only. No game files are included; you need your own legal copy of NFSU2.

## Status
Milestone 1: read-only import.
- Chunk walker and ID histogram for `.BUN` files
- Section table (`0x00034110`) parser from `L4R?.BUN`
- SolidObject (`0x80134010`) parser: vertices, triangles, UVs, vertex colours, materials
- Blender importer for chosen streaming sections, with scenery instances

Planned: real textures, write-back of edited meshes into `STREAML4RA.BUN`, collision, road network.

## Tools
| Script | Purpose |
|---|---|
| `tools/chunkdump.py FILE [--tree N]` | Walk chunks and print an ID histogram |
| `tools/nfsu2lib.py` | Section table + SolidObject parser |
| `tools/sectionmap.py` | Top-down labelled map of all sections (needs matplotlib) |
| `tools/import_world.py` | Blender importer |

## Usage
Point the scripts at your game's `TRACKS` folder, then:
```
blender -b --factory-startup --python tools/import_world.py -- \
  --sections C21,C22,B26 --instances --blend out/test_section.blend --obj out/test_section.obj --png out/preview.png
```
Tested with Blender 4.2 LTS.

## Format references
- [FRSWorlsEditor NFSU2 format docs](https://github.com/chrystianfarias/FRSWorlsEditor)
- [OpenUG2 format docs](https://github.com/whoismept/OpenUG2)
