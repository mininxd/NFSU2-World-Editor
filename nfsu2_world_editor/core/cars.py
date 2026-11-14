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
CLASS_DULLPLASTIC = bin_hash('DULLPLASTIC')  # 0x0FEDEE40
CLASS_BOTTOM = bin_hash('BOTTOM')            # 0x52BF4C34
CLASS_DECAL = bin_hash('DECAL')              # 0x02A05578
CLASS_ALUMINUM = bin_hash('ALUMINUM')        # 0x2E65E067
CLASS_BRAKELIGHTGLASS = bin_hash('BRAKELIGHTGLASS')
CLASS_BRAKELIGHT = bin_hash('BRAKELIGHT')    # 0x05BC3A3C
CLASS_HEADLIGHT = bin_hash('HEADLIGHT')      # 0x9C645529
CLASS_CARBONFIBRE = bin_hash('CARBONFIBRE')  # 0x721AFF7C
# Hashes seen in shipped cars whose original name is not recovered yet.
CLASS_TEXTURED = 0x010CB64A   # textured diffuse (rim faces, driver sprite)
CLASS_TRIM = 0x12C9453C       # dark glossy trim / window surround
CLASS_DEFROSTER = 0x3ED70C43  # rear-window defroster (glass + detail)
CLASS_LIGHT_LODA = 0xF7FC7674 # extra brakelight/headlight shader
CLASS_LIGHT_LODB = 0x33A26CB6

CLASS_NAMES = {
    CLASS_CARSKIN: 'CARSKIN',
    CLASS_WINDSHIELD: 'WINDSHIELD',
    CLASS_RUBBER: 'RUBBER',
    CLASS_MAGSILVER: 'MAGSILVER',
    CLASS_CHROME: 'CHROME',
    CLASS_HEADLIGHTGLASS: 'HEADLIGHTGLASS',
    CLASS_CARBOTTOM: 'CARBOTTOM',
    CLASS_BLACK: 'BLACK',
    CLASS_DULLPLASTIC: 'DULLPLASTIC',
    CLASS_BOTTOM: 'BOTTOM',
    CLASS_DECAL: 'DECAL',
    CLASS_ALUMINUM: 'ALUMINUM',
    CLASS_BRAKELIGHTGLASS: 'BRAKELIGHTGLASS',
    CLASS_BRAKELIGHT: 'BRAKELIGHT',
    CLASS_HEADLIGHT: 'HEADLIGHT',
    CLASS_CARBONFIBRE: 'CARBONFIBRE',
    CLASS_TEXTURED: 'TEXTURED',
    CLASS_TRIM: 'TRIM',
    CLASS_DEFROSTER: 'DEFROSTER',
    CLASS_LIGHT_LODA: 'BRAKELIGHT',
    CLASS_LIGHT_LODB: 'HEADLIGHT',
}

# Material classes that should render as transmissive / blended glass.
GLASS_CLASSES = frozenset({
    'WINDSHIELD', 'HEADLIGHTGLASS', 'BRAKELIGHTGLASS', 'DEFROSTER',
})

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

def part_shader(part, mat_index, slot):
    """Material-class hash for one material record of ``part``.

    ``0x00134013`` is usually one class per texture slot, but when a single
    texture is used with several shaders (stock wheels, headlights) the list
    is one class per material record instead.
    """
    classes = getattr(part, 'classes', None) or []
    if not classes:
        return None
    textures = getattr(part, 'textures', None) or []
    mats = []
    mesh = getattr(part, 'mesh', None)
    if mesh is not None:
        mats = getattr(mesh, 'materials', None) or []
    ncls, ntex, nmat = len(classes), len(textures), len(mats) or len(classes)
    if ncls == nmat and ncls != ntex:
        return classes[mat_index] if mat_index < ncls else classes[-1]
    if slot is not None and 0 <= slot < ncls:
        return classes[slot]
    if 0 <= mat_index < ncls:
        return classes[mat_index]
    return classes[-1]


def uses_baked_model_space(matrix, verts):
    """True when verts are already in car space and ``matrix`` is only a
    leftover mirror/rotation flag (typical of paired L/R parts).

    Applying those matrices a second time folds right-side parts onto the
    left and hides headlights, doors and mirrors.
    """
    if not matrix:
        return True
    m = list(matrix)
    ident = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    if len(m) >= 16 and all(abs(a - b) < 1e-4 for a, b in zip(m[:16], ident)):
        return True
    # translation in row-major slot 12..14
    if len(m) >= 15 and (abs(m[12]) + abs(m[13]) + abs(m[14])) > 1e-3:
        return False
    if not verts:
        return False
    xs, ys, zs = zip(*verts)
    cx, cy, cz = (min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5, (min(zs) + max(zs)) * 0.5
    return max(abs(cx), abs(cy), abs(cz)) > 0.2




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

    The stock wheel mesh is a disc in the XZ (rolling) plane. The rim cap
    sits at y ≈ 0 and the tyre width extends toward +Y, so the outward
    normal of the rim points toward -Y. Wheels on the -Y (right) side are
    left unrotated; wheels on the +Y (left) side are rotated 180° about Z
    so the rim faces outward. Z is set so the tyre sits on z=0.
    """
    import math
    x0, y0, z0, x1, y1, z1 = wheel_part.bbox()
    y_center = 0.5 * (y0 + y1)
    z_center = 0.5 * (z0 + z1)
    out = []
    for w in layout:
        # Origin is the wheel centre (z_center ~ 0). Place that centre at
        # the authored radius so the tyre sits on z=0. (Using
        # radius - (z1 - z_center) would leave the centre near 0 and bury
        # the mesh halfway in the ground.)
        if w.get('radius'):
            z = w['radius'] - z_center
        else:
            z = -z0
        if w['y'] >= 0:
            # left side: flip so the rim cap (y≈0, normal -Y) faces +Y
            loc = (w['x'], w['y'] + y_center, z)
            rot = (0.0, 0.0, math.pi)
        else:
            loc = (w['x'], w['y'] - y_center, z)
            rot = (0.0, 0.0, 0.0)
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
