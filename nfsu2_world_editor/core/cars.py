"""NFSU2 car converter: ``CARS/<NAME>/GEOMETRY.BIN`` + ``TEXTURES.BIN``.

Car geometry is the same ``0x80134010`` SolidObject family as the world, so
:mod:`meshcodec` decode/encode is lossless. A no-edit parse + re-encode of
every mesh, then :func:`bchunk.serialize`, reproduces the original
``GEOMETRY.BIN`` byte for byte.

Textures use the compressed (offset-slot) TPK variant; see
:class:`tpk.CarTexturePack`. Unmodified ``TEXTURES.BIN`` also serializes
byte-identically. Writing *edited* compressed textures back is not
implemented yet (decode/import works).

Stock kit assembly picks the highest LOD of ``KIT00`` + ``BASE`` parts,
hides internals (engine, undersides), and places four copies of the stock
wheel using axle data from ``GLOBAL/GLOBALB.BUN`` when available.
"""
import re
import struct
from pathlib import Path

from . import bchunk, meshcodec
from .files import read_bytes
from .meshcodec import SOLID_OBJECT, SOLID_HEADER, SOLID_TEXLIST
from .tpk import bin_hash, load_packs, iter_packs

# Material-class hashes (bin_hash of the class name) stored in 0x00134013.
SOLID_MATCLASS = 0x00134013
CLASS_CARSKIN = bin_hash('CARSKIN')          # 0xD6D6080A
CLASS_WINDSHIELD = bin_hash('WINDSHIELD')    # 0x471A1DCA
CLASS_RUBBER = bin_hash('RUBBER')            # 0x78743CA1
CLASS_MAGSILVER = bin_hash('MAGSILVER')      # 0x22719FA9
CLASS_CHROME = bin_hash('CHROME')            # 0x54949AFD
CLASS_HEADLIGHTGLASS = bin_hash('HEADLIGHTGLASS')
CLASS_CARBOTTOM = bin_hash('CARBOTTOM')      # 0xBD1F0E0A
CLASS_BLACK = bin_hash('BLACK')

CLASS_NAMES = {
    CLASS_CARSKIN: 'CARSKIN',
    CLASS_WINDSHIELD: 'WINDSHIELD',
    CLASS_RUBBER: 'RUBBER',
    CLASS_MAGSILVER: 'MAGSILVER',
    CLASS_CHROME: 'CHROME',
    CLASS_HEADLIGHTGLASS: 'HEADLIGHTGLASS',
    CLASS_CARBOTTOM: 'CARBOTTOM',
    CLASS_BLACK: 'BLACK',
}

# Hidden on a parked/stock car (hood and trunk stay closed).
HIDDEN_FAMILIES = (
    'ENGINE', 'HOOD_UNDER', 'TRUNK_UNDER', 'TRUNK_AUDIO', 'TRUNK_AUDIO_UNL',
    'DECAL',
)

_LOD_SUFFIX = re.compile(r'_([A-D])$')
_KIT = re.compile(r'(KIT(?:W)?\d+|STYLE\d+|WIDE\d+)')
GLOBAL_CAR_TABLE = 0x00034600
GLOBAL_CAR_RECORD = 2192


class CarPart:
    """One SolidObject in a car geometry file."""

    def __init__(self, node):
        self.node = node
        self.mesh = meshcodec.decode(node)
        if self.mesh is None:
            raise ValueError('solid object without a usable mesh')
        self.name = self.mesh.name
        self.hash = self.mesh.hash
        self.matrix = self.mesh.matrix
        self.textures = list(self.mesh.textures)
        self.classes = _mat_classes(node)
        self.lod = _lod_of(self.name)
        self.kit, self.family = _kit_family(self.name)

    @property
    def nverts(self):
        return len(self.mesh.verts)

    @property
    def nfaces(self):
        return len(self.mesh.indices) // 3

    def bbox(self):
        xs, ys, zs = zip(*self.mesh.verts)
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _mat_classes(node):
    c = node.find(SOLID_MATCLASS)
    if c is None or not c.data:
        return []
    raw = bytes(c.data)
    return [struct.unpack_from('<I', raw, q)[0] for q in range(0, len(raw) - 7, 8)]


def _lod_of(name):
    m = _LOD_SUFFIX.search(name)
    return m.group(1) if m else None


def _kit_family(name):
    """-> (kit_token or None, family key with kit/LOD stripped)."""
    base = _LOD_SUFFIX.sub('', name)
    m = _KIT.search(base)
    kit = m.group(1) if m else None
    family = _KIT.sub('', base).replace('__', '_').strip('_')
    return kit, family


def _is_hidden(part):
    fam = part.family.upper()
    return any(h in fam for h in HIDDEN_FAMILIES)


class CarGeometry:
    """Parsed ``GEOMETRY.BIN`` (solid list + trailing chunks preserved)."""

    def __init__(self, buf, path=None):
        self.src = bytes(buf)
        self.path = Path(path) if path else None
        self.chunks = bchunk.parse(self.src)
        self.parts = []
        for top in self.chunks:
            if top.children is None:
                continue
            for node in top.find_all(SOLID_OBJECT):
                try:
                    self.parts.append(CarPart(node))
                except ValueError:
                    continue
        if not self.parts:
            raise ValueError('no solid objects in car geometry')
        self.name = _car_name(self.parts[0].name)

    @classmethod
    def open(cls, path):
        return cls(read_bytes(path), path)

    def rebuild(self):
        """Serialize the current chunk tree (byte-identical if nothing changed)."""
        return bytes(bchunk.serialize(self.chunks))

    def reencode_meshes(self):
        """Decode + encode every mesh in place. Unedited meshes stay identical."""
        n = 0
        for p in self.parts:
            meshcodec.encode(p.node, p.mesh)
            n += 1
        return n

    def stock_parts(self, kit='KIT00', lod='A'):
        """Highest-quality stock parts for ``kit`` (plus BASE), one per family.

        ``lod`` is a preference: if a part has no letter suffix (truncated
        names), the copy with the most vertices is kept.
        """
        # candidate: BASE always, plus the requested kit, no STYLE/WIDE/other kits
        chosen = {}  # family -> part
        for p in self.parts:
            if _is_hidden(p):
                continue
            if p.kit and p.kit != kit:
                continue
            if not p.kit and 'BASE' not in p.family.upper() and kit not in (p.name or ''):
                # leftover parts without a kit token (rare); only keep BASE
                if not p.family.upper().endswith('BASE') and 'BASE' not in p.family.upper():
                    continue
            best = chosen.get(p.family)
            if best is None or _better_lod(p, best, lod):
                chosen[p.family] = p
        return list(chosen.values())

    def wheel_part(self, kit='KIT00', lod='A'):
        for p in self.stock_parts(kit, lod):
            if 'WHEEL' in p.family.upper():
                return p
        for p in self.parts:
            if 'WHEEL' in p.name.upper() and (p.lod == lod or p.lod is None):
                return p
        return None


def _better_lod(a, b, prefer='A'):
    # letter LODs: A > B > C > D; unknown: more verts wins
    order = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    if a.lod and b.lod:
        if a.lod != b.lod:
            return order.get(a.lod, 9) < order.get(b.lod, 9)
        return a.nverts > b.nverts
    if a.lod == prefer and b.lod != prefer:
        return True
    if b.lod == prefer and a.lod != prefer:
        return False
    return a.nverts > b.nverts


def _car_name(part_name):
    return part_name.split('_')[0] if part_name else 'CAR'


# ---------------------------------------------------------------------------
# GLOBALB.BUN wheel / axle table
# ---------------------------------------------------------------------------

def iter_global_cars(buf):
    """Yield dicts for each 2192-byte vehicle record in ``GLOBALB.BUN``."""
    from .chunks import walk
    hits = [(off, size) for kind, off, cid, size, depth, _ in walk(buf, 0, len(buf))
            if kind != 'BAD' and cid == GLOBAL_CAR_TABLE]
    if not hits:
        return
    off, size = hits[0]
    p = off + 8
    end = off + 8 + size
    while p < end and buf[p] == 0x11:
        p += 1
    rec = GLOBAL_CAR_RECORD
    while p + rec <= end:
        name = bytes(buf[p:p + 32]).split(b'\0')[0].decode('ascii', 'replace')
        path = bytes(buf[p + 0x40:p + 0x80]).split(b'\0')[0].decode('ascii', 'replace')
        wheels = []
        for k in range(4):
            base = p + 0x120 + 0x30 * k
            x, y = struct.unpack_from('<2f', buf, base)
            radius, width = struct.unpack_from('<2f', buf, base + 0x10)
            idx, = struct.unpack_from('<I', buf, base + 0x18)
            wheels.append(dict(x=x, y=y, radius=radius, width=width, index=idx))
        yield dict(name=name, path=path, wheels=wheels,
                   mass=struct.unpack_from('<f', buf, p + 0x220)[0],
                   size=struct.unpack_from('<3f', buf, p + 0x224))
        p += rec


def wheel_layout(global_dir, car_name):
    """Four wheel placements ``(x, y, radius, width)`` for ``car_name``, or None."""
    if not global_dir:
        return None
    d = Path(global_dir)
    path = None
    for cand in (d / 'GLOBALB.BUN', d / 'GlobalB.bun', d / 'GLOBALB.bun'):
        if cand.is_file():
            path = cand
            break
    if path is None:
        return None
    want = car_name.upper()
    for rec in iter_global_cars(read_bytes(path)):
        if rec['name'].upper() == want:
            return rec['wheels']
    return None


def estimate_wheel_layout(body_part, wheel_part):
    """Fallback axle positions from body / wheel bounding boxes."""
    bx0, by0, bz0, bx1, by1, bz1 = body_part.bbox()
    wx0, wy0, wz0, wx1, wy1, wz1 = wheel_part.bbox()
    radius = max(abs(wz0), abs(wz1), abs(wx0), abs(wx1))
    width = wy1 - wy0
    half = max(abs(by0), abs(by1)) - width * 0.5
    # front / rear at ~30% / 70% of body length
    span = bx1 - bx0
    fx = bx1 - span * 0.22
    rx = bx0 + span * 0.22
    return [
        dict(x=fx, y=half, radius=radius, width=width, index=0),
        dict(x=fx, y=-half, radius=radius, width=width, index=1),
        dict(x=rx, y=-half, radius=radius, width=width, index=2),
        dict(x=rx, y=half, radius=radius, width=width, index=3),
    ]


def wheel_transforms(wheel_part, layout):
    """World-space (location, euler_xyz radians) for each of the 4 wheels.

    The stock wheel mesh sits in the Y-up-outward, XZ rolling plane, with its
    inner face near y=0. +Y (right) wheels are unrotated; -Y (left) wheels are
    rotated 180° about Z so the outer face points left. Z is set so the tyre
    touches z=0.
    """
    import math
    x0, y0, z0, x1, y1, z1 = wheel_part.bbox()
    y_center = 0.5 * (y0 + y1)
    z_center = 0.5 * (z0 + z1)
    out = []
    for w in layout:
        # sit on the ground: bottom of the mesh at z=0, or use authored radius
        z = (w['radius'] - (z1 - z_center)) if w.get('radius') else -z0
        if w['y'] >= 0:
            loc = (w['x'], w['y'] - y_center, z)
            rot = (0.0, 0.0, 0.0)
        else:
            loc = (w['x'], w['y'] + y_center, z)
            rot = (0.0, 0.0, math.pi)
        out.append((loc, rot))
    return out


# ---------------------------------------------------------------------------
# Textures
# ---------------------------------------------------------------------------

def load_car_textures(textures_path, extra_files=()):
    """-> list of packs (car pack first, then optional GLOBAL fallbacks)."""
    packs, _ = load_packs(textures_path)
    for path in extra_files:
        try:
            more, _ = load_packs(path)
        except Exception:
            continue
        packs.extend(more)
    return packs


def texture_lookup(packs):
    """hash -> Texture, first pack wins."""
    by = {}
    for p in packs:
        for t in p.textures:
            by.setdefault(t.hash, t)
    return by


# ---------------------------------------------------------------------------
# Paths / convenience
# ---------------------------------------------------------------------------

def car_dir(path):
    """Accept a CARS/<NAME> folder, a GEOMETRY.BIN, or a parent CARS folder + name."""
    p = Path(path)
    if p.is_dir():
        geo = p / 'GEOMETRY.BIN'
        if not geo.is_file():
            # case-insensitive
            found = {c.name.lower(): c for c in p.iterdir() if c.is_file()}
            geo = found.get('geometry.bin')
        if geo is None or not Path(geo).is_file():
            raise FileNotFoundError('no GEOMETRY.BIN in %s' % p)
        return Path(geo).parent
    if p.is_file() and p.name.upper().startswith('GEOMETRY'):
        return p.parent
    raise FileNotFoundError('not a car folder or GEOMETRY.BIN: %s' % path)


def car_files(path):
    """-> (geometry_path, textures_path or None, car_dir)."""
    d = car_dir(path)
    files = {c.name.lower(): c for c in d.iterdir() if c.is_file()}
    geo = files.get('geometry.bin')
    tex = files.get('textures.bin')
    if geo is None:
        raise FileNotFoundError('no GEOMETRY.BIN in %s' % d)
    return geo, tex, d


def default_global_dir(car_folder):
    """Guess the game's GLOBAL folder from a CARS/<NAME> path."""
    # .../NFSU2/CARS/MIATA  ->  .../NFSU2/GLOBAL
    p = Path(car_folder)
    for up in (p.parent, p.parent.parent, p.parent.parent.parent):
        g = up / 'GLOBAL'
        if g.is_dir():
            return g
    return None
