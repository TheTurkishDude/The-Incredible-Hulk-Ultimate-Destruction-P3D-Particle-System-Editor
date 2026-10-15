#!/usr/bin/env python3
"""
p3d_particle_tool.py - Pure3D (.p3d) particle system viewer / editor / importer / exporter
Target: Radical Entertainment "The Incredible Hulk: Ultimate Destruction" (little-endian .p3d)

The Tool Is Made By TheTurkishDude

Only needs the Python 3.8+ standard library (tkinter for the GUI).

USAGE
  python p3d_particle_tool.py                       open the GUI
  python p3d_particle_tool.py gui c00m01.p3d        open the GUI with a file
  python p3d_particle_tool.py list c00m01.p3d       list particle systems
  python p3d_particle_tool.py dump c00m01.p3d [-s NAME]    print decoded tree
  python p3d_particle_tool.py roundtrip c00m01.p3d  verify byte-exact load/save
  python p3d_particle_tool.py export c00m01.p3d out.p3d -s NAME [-s NAME2 ...] [--deps]
  python p3d_particle_tool.py import dst.p3d src.p3d out.p3d [-s NAME ...] [--deps]
  python p3d_particle_tool.py shaders global.p3d [--params] [--textures] [-n SHADER]
  python p3d_particle_tool.py check c00m01.p3d -p global_nonchar.p3d    unresolved shaders/textures
  python p3d_particle_tool.py import-shaders dst.p3d src.p3d out.p3d -n SHADER [-n ...]
  python p3d_particle_tool.py to-json c00m01.p3d out.json [-s NAME ...]
  python p3d_particle_tool.py from-json dst.p3d in.json out.p3d

FORMAT NOTES (reverse engineered from c00m01.p3d; see also the notes file)
  File      : 'P3D\\xff', u32 header_size(=12), u32 file_size, then chunks.
  Chunk     : u32 id, u32 header_size (incl. these 12 bytes), u32 total_size
              (incl. children). Children follow the header bytes.
  Strings   : u8 length (already padded to a multiple of 4) + chars + NUL padding.

  0x1580C  ParticleSystem   ver, name, f32 frame_rate, u32 num_frames, 4*u32 ?, f32 ?(-1.0),
                            3*u32 ?, u32 emitter_count
     0x15900  Emitter       ver, name, shader name, 11*u32 params
        0x121000 Animation 'SPAR'  per-particle channels sampled over particle life
        0x121000 Animation 'SPEM'  emitter channels sampled over system time
        0x15B00 / 0x15B01  generator (angles, in radians) + 'PPTG' animation
        0x10003 / 0x10004  small fixed chunks (kept raw)
     0x122000  f32 parameter
     0x121201  frame controller ('PSYSANIM') linking the animation to the system
  Shader 0x11000 (top level): name, ver, pddi shader ('simple'), 4*u32 (last = param count,
     rebuilt on save). Children: 0x11002 texture (tag 'TEX', texture name), 0x11003 int
     (FIMD filter, BLMD blend, ATST/ACMP alpha test, UVMD, ...), 0x11004 float, 0x11005 colour.
  Texture 0x19000 (top level): name, ver(14000), width, height, bpp, alpha_depth, mips, type,
     usage, priority; children 0x19001 hold the image data (kept raw).
  Animation 0x121000: ver, name, kind tag, f32 num_frames, f32 frame_rate, u32 flags
     0x121006 group table: one 0x121007 per channel type = [ver, channel_chunk_id,
              n_channels, u16 key_count per channel]   (REBUILT automatically on save)
     0x121002 -> 0x121001 channel set (ver, name, flags, channel_count)
        0x121100 Float1 | 0x121104 Vector3 | 0x121105 Quaternion | 0x121109 Color RGBA8 |
        0x12110E Int :   ver, 4-char tag, u32 n, u16 frames[n], values[n]
           0x121110 child (ver, u32 mode)
        0x121108 Bool: ver, 4-char tag, u16 start_state, u32 n, u16 frames[n]
"""
import argparse
import collections
import copy
import json
import math
import os
import random
import struct
import sys

MAGIC = b'P3D\xff'
CREDIT = 'The Tool Is Made By TheTurkishDude'

ID_PSYS = 0x1580C
ID_EMITTER = 0x15900
ID_GEN_A = 0x15B00
ID_GEN_B = 0x15B01
ID_SYS_PARAM = 0x122000
ID_FRAMECTRL = 0x121201
ID_ANIM = 0x121000
ID_CHSET = 0x121001
ID_CHLIST = 0x121002
ID_GROUPLIST = 0x121006
ID_GROUP = 0x121007
ID_CH_F1 = 0x121100
ID_CH_V3 = 0x121104
ID_CH_Q = 0x121105
ID_CH_BOOL = 0x121108
ID_CH_COL = 0x121109
ID_CH_INT = 0x12110E
ID_CH_MODE = 0x121110
ID_SHADER = 0x11000
ID_SHADER_TEX = 0x11002
ID_SHADER_INT = 0x11003
ID_SHADER_FLT = 0x11004
ID_SHADER_CLR = 0x11005
ID_TEXTURE = 0x19000
SHADER_PARAM_IDS = (ID_SHADER_TEX, ID_SHADER_INT, ID_SHADER_FLT, ID_SHADER_CLR)

CHANNEL_INFO = {  # id: (struct char, components, label)
    ID_CH_F1: ('f', 1, 'Float1'),
    ID_CH_V3: ('f', 3, 'Vector3'),
    ID_CH_Q: ('f', 4, 'Quaternion'),
    ID_CH_BOOL: ('', 0, 'Bool/visibility'),
    ID_CH_COL: ('B', 4, 'ColorRGBA8'),
    ID_CH_INT: ('i', 1, 'Int'),
}

CHUNK_NAMES = {
    ID_PSYS: 'ParticleSystem', ID_EMITTER: 'Emitter', ID_GEN_A: 'GeneratorA',
    ID_GEN_B: 'GeneratorB', ID_SYS_PARAM: 'SystemParam', ID_FRAMECTRL: 'FrameController',
    ID_ANIM: 'Animation', ID_CHSET: 'ChannelSet', ID_CHLIST: 'ChannelList',
    ID_GROUPLIST: 'GroupList', ID_GROUP: 'Group', ID_CH_F1: 'Float1Channel',
    ID_CH_V3: 'Vector3Channel', ID_CH_Q: 'QuaternionChannel', ID_CH_BOOL: 'BoolChannel',
    ID_CH_COL: 'ColorChannel',
    ID_CH_INT: 'IntChannel', ID_CH_MODE: 'ChannelMode', ID_SHADER: 'Shader',
    ID_TEXTURE: 'Texture', ID_SHADER_TEX: 'ShaderTexParam', ID_SHADER_INT: 'ShaderIntParam',
    ID_SHADER_FLT: 'ShaderFloatParam', ID_SHADER_CLR: 'ShaderColourParam',
    0x19001: 'TextureImage', 0x10003: 'Chunk10003', 0x10004: 'Chunk10004',
    0x121402: 'Chunk121402',
}

# Channel tag meanings are INFERRED from the tag names + value ranges (not from engine code).
TAG_INFO = {
    'TRA': 'translation', 'ROT': 'rotation (quat xyzw)', 'CLR': 'colour', 'CLRV': 'colour variance',
    'ALP': 'alpha', 'ALPV': 'alpha variance', 'SPD': 'speed', 'SPDV': 'speed variance',
    'WGT': 'weight', 'WGTV': 'weight variance', 'GRA': 'gravity', 'DRA': 'drag',
    'RAT': 'emission rate', 'LIF': 'life (frames)', 'LIFV': 'life variance',
    'SPI': 'spin', 'SPIV': 'spin variance', 'SIZ': 'size', 'SIZV': 'size variance',
    # SPAR animations (per particle, x axis = 0..num_frames of normalised life)
    'SZOL': 'size over life', 'ALOL': 'alpha over life (0-255)', 'COOL': 'colour over life',
    'SPOL': 'speed over life', 'WEOL': 'weight over life', 'SNOL': 'spin over life',
    'TEOL': 'texture frame over life',
    # generator animations
    'HOSP': 'horizontal spread (rad)', 'VESP': 'vertical spread (rad)', 'RDVA': 'radius variance',
    'WDT': 'width', 'HGT': 'height',
}

# Shader parameter meanings are INFERRED from the 4-char names; values are raw engine enums.
SHADER_PARAM_INFO = {
    'TEX': 'texture name', 'FIMD': 'filter mode', 'BLMD': 'blend mode', 'UVMD': 'UV mode',
    'ATST': 'alpha test', 'ACMP': 'alpha compare', 'LIT': 'lighting', '2SID': 'two-sided',
    'SHMD': 'shade mode', 'PLMD': 'polygon mode', 'CLRE': 'colour enable?', 'MMIN': 'mip min?',
    'MMAX': 'mip max?', 'MMEX': 'mip exp?', 'MCBV': 'cbv mask?', 'CBVM': 'cbv mode?',
    'CBVP': 'cbv ?', 'CBVY': 'cbv ?', 'CBVV': 'cbv ?', 'SPEC': 'specular colour',
    'DIFF': 'diffuse colour', 'AMBI': 'ambient colour', 'EMIS': 'emissive colour',
    'TINT': 'tint colour', 'ACTH': 'alpha threshold?', 'SHIN': 'shininess', 'MSHP': 'mask/shape?',
    'SAT': 'saturation', 'BRIT': 'brightness', 'CONT': 'contrast', 'MDPT': 'mid point?',
}

# ----------------------------------------------------------------------------------------
# Chunk tree
# ----------------------------------------------------------------------------------------


class Chunk:
    __slots__ = ('id', 'header', 'children', 'tail')

    def __init__(self, cid, header=b'', children=None, tail=b''):
        self.id = cid
        self.header = bytes(header)
        self.children = children if children is not None else []
        self.tail = bytes(tail)

    def size(self):
        return 12 + len(self.header) + sum(c.size() for c in self.children) + len(self.tail)

    def write(self, out):
        out += struct.pack('<III', self.id, 12 + len(self.header), self.size())
        out += self.header
        for c in self.children:
            c.write(out)
        out += self.tail


def parse_chunks(buf, off, end):
    res = []
    while off < end:
        if off + 12 > end:
            raise ValueError('truncated chunk')
        cid, hs, ts = struct.unpack_from('<III', buf, off)
        if hs < 12 or ts < hs or off + ts > end:
            raise ValueError('bad chunk sizes')
        c = Chunk(cid, buf[off + 12:off + hs])
        try:
            c.children = parse_chunks(buf, off + hs, off + ts)
        except ValueError:
            c.children = []
            c.tail = bytes(buf[off + hs:off + ts])
        res.append(c)
        off += ts
    return res


def walk(chunks):
    for c in chunks:
        yield c
        yield from walk(c.children)


class P3DFile:
    def __init__(self, chunks=None, path=None):
        self.chunks = chunks if chunks is not None else []
        self.path = path
        self.extra = b''

    @classmethod
    def load(cls, path):
        with open(path, 'rb') as f:
            buf = f.read()
        if buf[:4] == b'\xffP3D':
            raise ValueError('big-endian P3D is not supported')
        if buf[:4] == b'P3DZ':
            raise ValueError('compressed P3DZ file: decompress first')
        if buf[:4] != MAGIC:
            raise ValueError('not a little-endian Pure3D file')
        hs, ts = struct.unpack_from('<II', buf, 4)
        end = min(ts, len(buf))
        o = cls(parse_chunks(buf, hs, end), path)
        o.extra = bytes(buf[end:])
        return o

    def to_bytes(self):
        body = bytearray()
        for c in self.chunks:
            c.write(body)
        return MAGIC + struct.pack('<II', 12, 12 + len(body)) + bytes(body) + self.extra

    def save(self, path=None):
        fix_structure(self.chunks)
        path = path or self.path
        with open(path, 'wb') as f:
            f.write(self.to_bytes())
        self.path = path

    def systems(self):
        return [c for c in self.chunks if c.id == ID_PSYS]

# ----------------------------------------------------------------------------------------
# Typed header fields
# ----------------------------------------------------------------------------------------


def _u32s(prefix, n):
    return [('%s%d' % (prefix, i), 'u32') for i in range(n)]


SCHEMAS = {
    ID_PSYS: [('version', 'u32'), ('name', 'pstr'), ('frame_rate', 'f32'), ('num_frames', 'u32'),
              ('unk_a', 'u32'), ('unk_b', 'u32'), ('unk_c', 'u32'), ('unk_d', 'u32'),
              ('unk_f', 'f32'), ('unk_e', 'u32'), ('unk_g', 'u32'), ('unk_h', 'u32'),
              ('emitter_count', 'u32')],
    ID_EMITTER: [('version', 'u32'), ('name', 'pstr'), ('shader', 'pstr')] + _u32s('param_', 11),
    ID_GEN_A: [('version', 'u32'), ('angle_a', 'f32'), ('angle_b', 'f32'), ('param', 'u32')],
    ID_GEN_B: [('version', 'u32'), ('f0', 'f32'), ('f1', 'f32'), ('angle_a', 'f32'),
               ('angle_b', 'f32'), ('param', 'u32')],
    ID_SYS_PARAM: [('version', 'u32'), ('value', 'f32')],
    ID_FRAMECTRL: [('version', 'u32'), ('name', 'pstr'), ('kind', 'tag8'), ('unk0', 'u32'),
                   ('unk1', 'u32'), ('target', 'pstr')],
    ID_ANIM: [('version', 'u32'), ('name', 'pstr'), ('kind', 'tag4'), ('num_frames', 'f32'),
              ('frame_rate', 'f32'), ('flags', 'u32')],
    ID_CHSET: [('version', 'u32'), ('name', 'pstr'), ('flags', 'u32'), ('channel_count', 'u32')],
    ID_CHLIST: [('version', 'u32'), ('count', 'u32')],
    ID_GROUPLIST: [('version', 'u32'), ('param', 'u32')],
    ID_CH_MODE: [('version', 'u32'), ('mode', 'u32')],
    ID_SHADER: [('name', 'pstr'), ('version', 'u32'), ('pddi_shader', 'pstr'),
                ('has_translucency', 'u32'), ('vertex_needs', 'u32'), ('vertex_mask', 'u32'),
                ('num_params', 'u32')],
    ID_SHADER_TEX: [('param', 'tag4'), ('texture', 'pstr')],
    ID_SHADER_INT: [('param', 'tag4'), ('value', 'u32')],
    ID_SHADER_FLT: [('param', 'tag4'), ('value', 'f32')],
    ID_SHADER_CLR: [('param', 'tag4')],       # + 4 colour bytes in 'rest'
    ID_TEXTURE: [('name', 'pstr'), ('version', 'u32'), ('width', 'u32'), ('height', 'u32'),
                 ('bpp', 'u32'), ('alpha_depth', 'u32'), ('num_mips', 'u32'),
                 ('texture_type', 'u32'), ('usage', 'u32'), ('priority', 'u32')],
}

_FMT = {'u32': '<I', 'i32': '<i', 'f32': '<f', 'u16': '<H'}


def unpack_fields(schema, buf):
    o = 0
    out = []
    for name, kind in schema:
        if kind in _FMT:
            v = struct.unpack_from(_FMT[kind], buf, o)[0]
            o += struct.calcsize(_FMT[kind])
        elif kind == 'pstr':
            n = buf[o]
            raw = buf[o + 1:o + 1 + n]
            if len(raw) != n:
                raise ValueError('short string')
            v = raw.rstrip(b'\0').decode('latin1')
            o += 1 + n
        elif kind == 'tag4':
            v = buf[o:o + 4].rstrip(b'\0').decode('latin1')
            o += 4
        elif kind == 'tag8':
            v = buf[o:o + 8].rstrip(b'\0').decode('latin1')
            o += 8
        else:
            raise ValueError(kind)
        out.append({'name': name, 'kind': kind, 'value': v})
    if o > len(buf):
        raise ValueError('short header')
    out.append({'name': 'rest', 'kind': 'hex', 'value': bytes(buf[o:]).hex()})
    return out


def pack_pstr(s):
    raw = s.encode('latin1')
    n = (len(raw) + 3) // 4 * 4
    if n > 255:
        raise ValueError('string too long')
    return bytes([n]) + raw.ljust(n, b'\0')


def pack_fields(fields):
    out = bytearray()
    for f in fields:
        k, v = f['kind'], f['value']
        if k in _FMT:
            out += struct.pack(_FMT[k], v)
        elif k == 'pstr':
            out += pack_pstr(v)
        elif k == 'tag4':
            out += v.encode('latin1').ljust(4, b'\0')[:4]
        elif k == 'tag8':
            out += v.encode('latin1').ljust(8, b'\0')[:8]
        elif k == 'hex':
            out += bytes.fromhex(v)
    return bytes(out)


def get_fields(c):
    sch = SCHEMAS.get(c.id)
    if not sch:
        return None
    try:
        return unpack_fields(sch, c.header)
    except (struct.error, IndexError, ValueError):
        return None


def fget(c, name, default=None):
    f = get_fields(c)
    if f:
        for x in f:
            if x['name'] == name:
                return x['value']
    return default


def fset(c, name, value):
    f = get_fields(c)
    if not f:
        return False
    for x in f:
        if x['name'] == name:
            x['value'] = value
    c.header = pack_fields(f)
    return True


def parse_value(kind, text):
    text = text.strip()
    if kind in ('u32', 'u16'):
        return int(text, 0)
    if kind == 'i32':
        return int(text, 0)
    if kind == 'f32':
        return float(text)
    if kind == 'hex':
        return bytes.fromhex(text.replace(' ', '')).hex()
    return text

# ----------------------------------------------------------------------------------------
# Channels
# ----------------------------------------------------------------------------------------


def _value_bytes(cid):
    fmt, n, _ = CHANNEL_INFO[cid]
    return n * (1 if fmt == 'B' else 4)


def decode_channel(c):
    """-> dict(version, tag, keys=[(frame, tuple)]) or None if the layout is unexpected."""
    if c.id not in CHANNEL_INFO:
        return None
    h = c.header
    if len(h) < 12:
        return None
    fmt, n, _ = CHANNEL_INFO[c.id]
    ver = struct.unpack_from('<I', h, 0)[0]
    tag = h[4:8].rstrip(b'\0').decode('latin1')
    if c.id == ID_CH_BOOL:
        if len(h) < 14:
            return None
        start = struct.unpack_from('<H', h, 8)[0]
        cnt = struct.unpack_from('<I', h, 10)[0]
        if len(h) != 14 + 2 * cnt:
            return None
        fr = struct.unpack_from('<%dH' % cnt, h, 14)
        return {'version': ver, 'tag': tag, 'start': start, 'keys': [(x, ()) for x in fr]}
    cnt = struct.unpack_from('<I', h, 8)[0]
    vb = _value_bytes(c.id)
    if len(h) != 12 + cnt * (2 + vb):
        return None
    frames = struct.unpack_from('<%dH' % cnt, h, 12)
    o = 12 + 2 * cnt
    keys = []
    for i in range(cnt):
        v = struct.unpack_from('<%d%s' % (n, fmt), h, o + i * vb)
        keys.append((frames[i], tuple(v)))
    return {'version': ver, 'tag': tag, 'keys': keys}


def channel_key_count(c):
    return struct.unpack_from('<I', c.header, 10 if c.id == ID_CH_BOOL else 8)[0]


def encode_channel(c, version, tag, keys, start=0):
    fmt, n, _ = CHANNEL_INFO[c.id]
    keys = sorted(keys, key=lambda k: k[0])
    out = bytearray(struct.pack('<I', version))
    out += tag.encode('latin1').ljust(4, b'\0')[:4]
    if c.id == ID_CH_BOOL:
        out += struct.pack('<HI', start, len(keys))
        out += struct.pack('<%dH' % len(keys), *[k[0] for k in keys])
        c.header = bytes(out)
        return
    out += struct.pack('<I', len(keys))
    out += struct.pack('<%dH' % len(keys), *[k[0] for k in keys])
    for _, v in keys:
        if fmt == 'f':
            out += struct.pack('<%df' % n, *[float(x) for x in v])
        elif fmt == 'B':
            out += struct.pack('<4B', *[max(0, min(255, int(round(x)))) for x in v])
        else:
            out += struct.pack('<%di' % n, *[int(round(x)) for x in v])
    c.header = bytes(out)


def format_value(cid, v):
    fmt = CHANNEL_INFO[cid][0]
    if cid == ID_CH_BOOL:
        return 'toggle'
    if fmt == 'B':
        return '#%02X%02X%02X%02X' % tuple(v)
    if fmt == 'i':
        return str(int(v[0]))
    return ', '.join('%.6g' % x for x in v)


def parse_channel_value(cid, text):
    fmt, n, _ = CHANNEL_INFO[cid]
    text = text.strip()
    if cid == ID_CH_BOOL:
        return ()
    if fmt == 'B':
        if text.startswith('#'):
            h = text[1:]
            if len(h) == 6:
                h += 'FF'
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
        vals = [int(float(x)) for x in text.replace(',', ' ').split()]
        if len(vals) == 3:
            vals.append(255)
        if len(vals) != 4:
            raise ValueError('need r,g,b[,a]')
        return tuple(vals)
    vals = [float(x) for x in text.replace(',', ' ').split()]
    if len(vals) != n:
        raise ValueError('need %d value(s)' % n)
    return tuple(int(v) for v in vals) if fmt == 'i' else tuple(vals)


def eval_keys(keys, f):
    if not keys:
        return None
    if f <= keys[0][0]:
        return keys[0][1]
    for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
        if f <= f1:
            t = (f - f0) / (f1 - f0) if f1 != f0 else 0.0
            return tuple(a + (b - a) * t for a, b in zip(v0, v1))
    return keys[-1][1]


def anim_channels(anim):
    """{tag: (chunk, decoded)} for every channel of an Animation chunk."""
    res = {}
    for lst in anim.children:
        if lst.id != ID_CHLIST:
            continue
        for cs in lst.children:
            if cs.id != ID_CHSET:
                continue
            for ch in cs.children:
                d = decode_channel(ch)
                if d:
                    res[d['tag']] = (ch, d)
    return res

# ----------------------------------------------------------------------------------------
# Structure maintenance (counts + channel group tables)
# ----------------------------------------------------------------------------------------


def fix_structure(chunks):
    """Keep counts and channel group tables consistent (particle systems and shaders)."""
    for sh in chunks:
        if sh.id == ID_SHADER and get_fields(sh):
            n = sum(1 for k in sh.children if k.id in SHADER_PARAM_IDS)
            if fget(sh, 'num_params') != n:
                fset(sh, 'num_params', n)
    for ps in walk(chunks):
        if ps.id != ID_PSYS:
            continue
        n = sum(1 for k in ps.children if k.id == ID_EMITTER)
        if get_fields(ps) and fget(ps, 'emitter_count') != n:
            fset(ps, 'emitter_count', n)
        for c in walk([ps]):
            if c.id == ID_ANIM:
                _fix_anim(c)


def _fix_anim(a):
    sets = []
    for lst in (k for k in a.children if k.id == ID_CHLIST):
        for cs in (k for k in lst.children if k.id == ID_CHSET):
            n = sum(1 for k in cs.children if k.id in CHANNEL_INFO)
            if get_fields(cs) and fget(cs, 'channel_count') != n:
                fset(cs, 'channel_count', n)
            sets.append(cs)
    if len(sets) != 1:
        return
    chans = [k for k in sets[0].children if k.id in CHANNEL_INFO]
    types = sorted({k.id for k in chans})
    for gl in (k for k in a.children if k.id == ID_GROUPLIST):
        old = [g for g in gl.children if g.id == ID_GROUP]
        ver = struct.unpack_from('<I', old[0].header)[0] if old else 0
        new = []
        for t in types:
            counts = [channel_key_count(k) for k in chans if k.id == t]
            hdr = struct.pack('<III', ver, t, len(counts)) + struct.pack('<%dH' % len(counts), *counts)
            new.append(Chunk(ID_GROUP, hdr))
        if len(new) != len(old) or any(n.header != o.header for n, o in zip(new, old)):
            gl.children = new

# ----------------------------------------------------------------------------------------
# Particle system operations
# ----------------------------------------------------------------------------------------


def chunk_label(c):
    t = CHUNK_NAMES.get(c.id, '0x%X' % c.id)
    nm = None
    if c.id in CHANNEL_INFO:
        d = decode_channel(c)
        if d:
            nm = '%s  (%d keys)' % (d['tag'], len(d['keys']))
    elif c.id in SHADER_PARAM_IDS:
        pr = fget(c, 'param')
        v = fget(c, 'texture') if c.id == ID_SHADER_TEX else fget(c, 'value', fget(c, 'rest'))
        nm = '%s = %s' % (pr, v) if pr else None
    else:
        nm = fget(c, 'name')
        k = fget(c, 'kind') if c.id == ID_ANIM else None
        if k and nm:
            nm = '%s [%s]' % (nm, k)
    return '%s "%s"' % (t, nm) if nm else t


def system_names(f):
    return [fget(s, 'name') for s in f.systems()]


def find_system(f, name):
    for s in f.systems():
        if fget(s, 'name') == name:
            return s
    raise KeyError('no particle system named %r' % name)


def rename_system(ps, new):
    old = fget(ps, 'name')
    for c in walk([ps]):
        if c.id == ID_PSYS:
            fset(c, 'name', new)
        elif c.id == ID_FRAMECTRL:
            if fget(c, 'target') == old:
                fset(c, 'target', new)
            if fget(c, 'name') == 'PSYS_' + old:
                fset(c, 'name', 'PSYS_' + new)


def unique_name(existing, base):
    if base not in existing:
        return base
    i = 2
    while '%s_%d' % (base, i) in existing:
        i += 1
    return '%s_%d' % (base, i)


def _top_name(c):
    """Shader (0x11000) and texture (0x19000) chunks start with their name string."""
    try:
        n = c.header[0]
        return c.header[1:1 + n].rstrip(b'\0').decode('latin1')
    except IndexError:
        return None


def _serialize(c):
    b = bytearray()
    c.write(b)
    return bytes(b)


def _find_top(f, cid, name):
    return next((c for c in f.chunks if c.id == cid and _top_name(c) == name), None)


def _insert_point(dst):
    return next((i for i, c in enumerate(dst.chunks) if c.id == ID_PSYS), len(dst.chunks))


def copy_textures(dst, src, names):
    """Copy texture chunks by name. Returns (count, missing names)."""
    have = {_top_name(c) for c in dst.chunks if c.id == ID_TEXTURE}
    add, missing = [], []
    for n in names:
        if n in have:
            continue
        tc = _find_top(src, ID_TEXTURE, n)
        if tc is None:
            missing.append(n)
        else:
            add.append(copy.deepcopy(tc))
            have.add(n)
    i = _insert_point(dst)
    dst.chunks[i:i] = add
    return len(add), missing


def copy_shaders(dst, src, names):
    """Copy shaders (+ the textures they reference) by name. Returns (n_tex, n_shaders, missing)."""
    have = {(c.id, _top_name(c)) for c in dst.chunks}
    tex, shaders, missing = [], [], []
    for sh in names:
        if (ID_SHADER, sh) in have:
            continue
        sc = _find_top(src, ID_SHADER, sh)
        if sc is None:
            missing.append(sh)
            continue
        for k in sc.children:
            if k.id == ID_SHADER_TEX:
                tn = fget(k, 'texture')
                if tn and (ID_TEXTURE, tn) not in have:
                    tc = _find_top(src, ID_TEXTURE, tn)
                    if tc is None:
                        missing.append('texture ' + tn)
                    else:
                        tex.append(copy.deepcopy(tc))
                        have.add((ID_TEXTURE, tn))
        shaders.append(copy.deepcopy(sc))
        have.add((ID_SHADER, sh))
    i = _insert_point(dst)
    dst.chunks[i:i] = tex + shaders
    return len(tex), len(shaders), missing


def copy_dependencies(dst, src, systems):
    """Copy the shaders (and textures) used by `systems` from src into dst if missing."""
    names = []
    for ps in systems:
        for e in ps.children:
            if e.id == ID_EMITTER:
                sh = fget(e, 'shader')
                if sh and sh not in names:
                    names.append(sh)
    return copy_shaders(dst, src, names)


def import_systems(dst, src, names=None, deps=False):
    """Copy particle systems from src P3DFile to dst. Returns (new names, dep info)."""
    chosen = [s for s in src.systems() if names is None or fget(s, 'name') in names]
    existing = set(system_names(dst))
    copies = []
    for s in chosen:
        c = copy.deepcopy(s)
        nn = unique_name(existing, fget(c, 'name'))
        if nn != fget(c, 'name'):
            rename_system(c, nn)
        existing.add(nn)
        copies.append(c)
    info = (0, 0, [])
    if deps:
        info = copy_dependencies(dst, src, chosen)
    last = max((i for i, c in enumerate(dst.chunks) if c.id == ID_PSYS), default=len(dst.chunks) - 1)
    dst.chunks[last + 1:last + 1] = copies
    fix_structure(dst.chunks)
    return [fget(c, 'name') for c in copies], info


def missing_shaders(f, systems=None):
    have = {_top_name(c) for c in f.chunks if c.id == ID_SHADER}
    out = set()
    for ps in systems or f.systems():
        for e in ps.children:
            if e.id == ID_EMITTER and fget(e, 'shader') not in have:
                out.add(fget(e, 'shader'))
    return sorted(out)

# ----------------------------------------------------------------------------------------
# Shaders / textures
# ----------------------------------------------------------------------------------------


def shader_info(sh):
    """-> (texture name, {param: value}) for a shader chunk."""
    tex, params = None, {}
    for k in sh.children:
        if k.id == ID_SHADER_TEX:
            tex = fget(k, 'texture')
        elif k.id in SHADER_PARAM_IDS:
            v = fget(k, 'rest') if k.id == ID_SHADER_CLR else fget(k, 'value')
            params[fget(k, 'param')] = v
    return tex, params


def shader_users(f):
    used = collections.defaultdict(list)
    for ps in f.systems():
        for e in ps.children:
            if e.id == ID_EMITTER:
                n = fget(ps, 'name')
                if n not in used[fget(e, 'shader')]:
                    used[fget(e, 'shader')].append(n)
    return used


def shader_table(f):
    texs = {_top_name(c) for c in f.chunks if c.id == ID_TEXTURE}
    used = shader_users(f)
    rows = []
    for c in f.chunks:
        if c.id != ID_SHADER:
            continue
        tex, params = shader_info(c)
        name = fget(c, 'name')
        rows.append({'name': name, 'pddi': fget(c, 'pddi_shader'), 'texture': tex,
                     'tex_present': tex in texs if tex else None, 'blend': params.get('BLMD'),
                     'filter': params.get('FIMD'), 'used_by': used.get(name, []), 'chunk': c})
    return rows


def set_shader_param(sh, param, text):
    """Set a parameter from text. Returns True when the parameter exists."""
    for k in sh.children:
        if k.id in SHADER_PARAM_IDS and fget(k, 'param') == param:
            if k.id == ID_SHADER_TEX:
                fset(k, 'texture', text)
            elif k.id == ID_SHADER_CLR:
                h = text.strip().lstrip('#').replace(' ', '')
                if len(h) != 8:
                    raise ValueError('colour needs 8 hex digits (4 bytes)')
                fset(k, 'rest', bytes.fromhex(h).hex())
            else:
                fset(k, 'value', parse_value('f32' if k.id == ID_SHADER_FLT else 'u32', text))
            return True
    return False


def rename_shader(f, old, new):
    """Rename a shader and every emitter reference to it. Returns number of references updated."""
    sh = _find_top(f, ID_SHADER, old)
    if sh is None:
        raise KeyError('no shader named %r' % old)
    fset(sh, 'name', new)
    n = 0
    for ps in f.systems():
        for e in ps.children:
            if e.id == ID_EMITTER and fget(e, 'shader') == old:
                fset(e, 'shader', new)
                n += 1
    return n


def check_deps(f, providers=()):
    """Report unresolved shaders/textures. providers: [(label, P3DFile)] searched for fixes."""
    shaders = {_top_name(c) for c in f.chunks if c.id == ID_SHADER}
    texs = {_top_name(c) for c in f.chunks if c.id == ID_TEXTURE}
    used = shader_users(f)
    miss_sh = {n: u for n, u in used.items() if n not in shaders}
    miss_tex = {}
    for c in f.chunks:
        if c.id == ID_SHADER:
            tex, _ = shader_info(c)
            if tex and tex not in texs:
                miss_tex.setdefault(tex, []).append(fget(c, 'name'))
    found = {}
    for label, pf in providers:
        for n in list(miss_sh) + list(miss_tex):
            if n in found:
                continue
            if _find_top(pf, ID_SHADER, n) or _find_top(pf, ID_TEXTURE, n):
                found[n] = label
    unused = sorted(n for n in shaders if n not in used)
    return {'missing_shaders': miss_sh, 'missing_textures': miss_tex, 'found_in': found,
            'unused_shaders': unused}


# ----------------------------------------------------------------------------------------
# JSON export / import
# ----------------------------------------------------------------------------------------


def chunk_to_json(c):
    d = {'id': '0x%X' % c.id, 'type': CHUNK_NAMES.get(c.id, '?'), 'header_hex': c.header.hex()}
    f = get_fields(c)
    if f:
        d['fields'] = {x['name']: x['value'] for x in f if x['name'] != 'rest' or x['value']}
    ch = decode_channel(c)
    if ch:
        d['channel'] = {'tag': ch['tag'], 'version': ch['version'], 'start': ch.get('start', 0),
                        'keys': [{'frame': fr, 'value': list(v)} for fr, v in ch['keys']]}
    if c.tail:
        d['tail_hex'] = c.tail.hex()
    d['children'] = [chunk_to_json(k) for k in c.children]
    return d


def chunk_from_json(d):
    cid = int(d['id'], 0)
    c = Chunk(cid, bytes.fromhex(d.get('header_hex', '')), tail=bytes.fromhex(d.get('tail_hex', '')))
    if 'fields' in d and cid in SCHEMAS:
        f = get_fields(c) or unpack_fields(SCHEMAS[cid], bytes(64))
        for x in f:
            if x['name'] in d['fields']:
                x['value'] = d['fields'][x['name']]
        c.header = pack_fields(f)
    if 'channel' in d and cid in CHANNEL_INFO:
        ch = d['channel']
        encode_channel(c, ch['version'], ch['tag'],
                       [(k['frame'], tuple(k['value'])) for k in ch['keys']], ch.get('start', 0))
    c.children = [chunk_from_json(k) for k in d.get('children', [])]
    return c


def systems_to_json(f, names=None):
    return {'format': 'p3d-particle-systems', 'version': 1,
            'systems': [chunk_to_json(s) for s in f.systems()
                        if names is None or fget(s, 'name') in names]}


def import_json(dst, data):
    existing = set(system_names(dst))
    last = max((i for i, c in enumerate(dst.chunks) if c.id == ID_PSYS), default=len(dst.chunks) - 1)
    new = []
    for sd in data['systems']:
        c = chunk_from_json(sd)
        nn = unique_name(existing, fget(c, 'name'))
        if nn != fget(c, 'name'):
            rename_system(c, nn)
        existing.add(nn)
        new.append(c)
    dst.chunks[last + 1:last + 1] = new
    fix_structure(dst.chunks)
    return [fget(c, 'name') for c in new]

# ----------------------------------------------------------------------------------------
# Approximate simulation used by the preview (semantics of channels are inferred!)
# ----------------------------------------------------------------------------------------


class EmitterModel:
    """Reads the SPEM (emitter), SPAR (per-particle over-life) and generator animations."""

    def __init__(self, em):
        self.name = fget(em, 'name')
        self.shader = fget(em, 'shader')
        self.spem, self.spar = {}, {}
        self.spar_len = 100.0
        self.hspread = self.vspread = math.pi
        self.width = self.height = 0.0
        for k in em.children:
            if k.id == ID_ANIM:
                kind = fget(k, 'kind')
                ch = {t: d['keys'] for t, (_, d) in anim_channels(k).items()}
                if kind == 'SPEM':
                    self.spem = ch
                elif kind == 'SPAR':
                    self.spar = ch
                    self.spar_len = fget(k, 'num_frames') or 100.0
                elif kind in ('PPTG', 'PPLG'):
                    self._gen(ch)
            elif k.id == ID_GEN_A:
                self.hspread = fget(k, 'angle_a') or self.hspread
                self.vspread = fget(k, 'angle_b') or self.vspread
            elif k.id == ID_GEN_B:
                self.hspread = fget(k, 'angle_a') or 0.05
                self.vspread = fget(k, 'angle_b') or 0.05
                for sub in k.children:
                    if sub.id == ID_ANIM:
                        self._gen({t: d['keys'] for t, (_, d) in anim_channels(sub).items()})

    def _gen(self, ch):
        def first(tag, cur):
            v = eval_keys(ch.get(tag, []), 0)
            return v[0] if v else cur
        self.hspread = first('HOSP', self.hspread)
        self.vspread = first('VESP', self.vspread)
        self.width = first('WDT', self.width)
        self.height = first('HGT', self.height)

    def em(self, tag, f, default=0.0):
        v = eval_keys(self.spem.get(tag, []), f)
        return default if v is None else v[0]

    def ol(self, tag, u, default):
        v = eval_keys(self.spar.get(tag, []), u * self.spar_len)
        return default if v is None else v


class SystemSim:
    """Approximate preview. Channel semantics are inferred from tag names and value ranges:
    SIZ/SPD/WGT * (over-life curve), GRA*WGT as gravity, RAT = particles per frame."""

    def __init__(self, ps):
        self.ems = [EmitterModel(e) for e in ps.children if e.id == ID_EMITTER]
        self.fps = fget(ps, 'frame_rate') or 30.0
        self.length = fget(ps, 'num_frames') or 50
        self.reset()

    def reset(self):
        self.t = 0
        self.rng = random.Random(7)
        self.parts = []
        self.acc = [0.0] * len(self.ems)

    def _rv(self, v):
        return (self.rng.random() * 2 - 1) * v

    def step(self):
        f = self.t
        for i, e in enumerate(self.ems):
            self.acc[i] += max(0.0, e.em('RAT', f))
            while self.acc[i] >= 1.0 and len(self.parts) < 4000:
                self.acc[i] -= 1.0
                tr = list(eval_keys(e.spem.get('TRA', []), f) or (0, 0, 0))
                tr[0] += self._rv(e.width * 0.5)
                tr[2] += self._rv(e.height * 0.5)
                th = math.acos(1 - self.rng.random() * (1 - math.cos(min(max(e.vspread, 0.0), math.pi))))
                ph = (self.rng.random() * 2 - 1) * min(max(e.hspread, 0.0), math.pi)
                d = (math.sin(th) * math.cos(ph), math.cos(th), math.sin(th) * math.sin(ph))
                self.parts.append({
                    'e': i, 'p': tr, 'd': d, 'g': 0.0, 'age': 0.0,
                    'spd': max(0.0, e.em('SPD', f) + self._rv(e.em('SPDV', f))),
                    'life': max(1.0, e.em('LIF', f, 20) + self._rv(e.em('LIFV', f))),
                    'size': max(0.001, e.em('SIZ', f, 0.05) + self._rv(e.em('SIZV', f))),
                    'wgt': e.em('WGT', f), 'gra': e.em('GRA', f), 'dra': e.em('DRA', f, 1.0),
                })
        alive = []
        for p in self.parts:
            e = self.ems[p['e']]
            u = p['age'] / p['life']
            sp = p['spd'] * e.ol('SPOL', u, (1.0,))[0]
            p['g'] = (p['g'] + p['gra'] * p['wgt'] * e.ol('WEOL', u, (1.0,))[0]) \
                * (p['dra'] if 0 < p['dra'] <= 1 else 1.0)
            p['p'] = [p['p'][0] + p['d'][0] * sp, p['p'][1] + p['d'][1] * sp + p['g'],
                      p['p'][2] + p['d'][2] * sp]
            p['age'] += 1
            if p['age'] < p['life']:
                alive.append(p)
        self.parts = alive
        self.t += 1

    def size_of(self, p):
        e = self.ems[p['e']]
        return p['size'] * e.ol('SZOL', p['age'] / p['life'], (1.0,))[0]

    def color_of(self, p):
        e = self.ems[p['e']]
        u = p['age'] / p['life']
        c = eval_keys(e.spar.get('COOL', []), u * e.spar_len) or eval_keys(e.spem.get('CLR', []), self.t) \
            or (255, 255, 255, 255)
        a = eval_keys(e.spar.get('ALOL', []), u * e.spar_len)
        a = a[0] if a else (eval_keys(e.spem.get('ALP', []), self.t) or (255,))[0]
        return tuple(max(0, min(255, int(x))) for x in c[:3]), max(0.0, min(1.0, a / 255.0))


# ----------------------------------------------------------------------------------------
# Text dump
# ----------------------------------------------------------------------------------------


def dump_chunk(c, depth=0, out=None):
    out = out if out is not None else []
    pad = '  ' * depth
    out.append('%s%s [0x%X]' % (pad, chunk_label(c), c.id))
    ch = decode_channel(c)
    f = get_fields(c)
    if ch:
        info = TAG_INFO.get(ch['tag'], '')
        out.append('%s  channel %s %s' % (pad, ch['tag'], ('- ' + info) if info else ''))
        for fr, v in ch['keys']:
            out.append('%s    frame %-4d %s' % (pad, fr, format_value(c.id, v)))
    elif f:
        for x in f:
            if x['name'] == 'rest' and not x['value']:
                continue
            out.append('%s  %s = %s' % (pad, x['name'], x['value']))
    elif c.header:
        out.append('%s  raw: %s' % (pad, c.header.hex()))
    for k in c.children:
        dump_chunk(k, depth + 1, out)
    return out

# ----------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------


def cmd_list(a):
    f = P3DFile.load(a.file)
    for s in f.systems():
        ems = [e for e in s.children if e.id == ID_EMITTER]
        print('%-40s frames=%-4s fps=%-5s emitters=%d' % (
            fget(s, 'name'), fget(s, 'num_frames'), fget(s, 'frame_rate'), len(ems)))
        for e in ems:
            print('    %-30s shader=%s' % (fget(e, 'name'), fget(e, 'shader')))
    miss = missing_shaders(f)
    if miss:
        print('\nshaders referenced but not defined in this file (see the check command):')
        print('   ' + ', '.join(miss))


def cmd_dump(a):
    f = P3DFile.load(a.file)
    for s in f.systems():
        if not a.system or fget(s, 'name') in a.system:
            print('\n'.join(dump_chunk(s)))


def cmd_roundtrip(a):
    with open(a.file, 'rb') as fh:
        orig = fh.read()
    f = P3DFile.load(a.file)
    ok = f.to_bytes() == orig
    print('byte-exact load/save :', 'OK' if ok else 'MISMATCH')
    fix_structure(f.chunks)
    ok2 = f.to_bytes() == orig
    print('after fix_structure  :', 'OK (tables regenerate identically)' if ok2 else 'MISMATCH')
    bad = 0
    for c in walk(f.chunks):
        fl = get_fields(c)
        if fl and pack_fields(fl) != c.header:
            bad += 1
        if c.id in CHANNEL_INFO:
            d = decode_channel(c)
            if d is None:
                bad += 1
            else:
                t = Chunk(c.id)
                encode_channel(t, d['version'], d['tag'], d['keys'], d.get('start', 0))
                if t.header != c.header:
                    bad += 1
    print('field/channel re-encode mismatches:', bad)
    return 0 if (ok and ok2 and not bad) else 1


def cmd_export(a):
    f = P3DFile.load(a.file)
    names = a.system or system_names(f)
    out = P3DFile([copy.deepcopy(find_system(f, n)) for n in names])
    if a.deps:
        n = copy_dependencies(out, f, out.systems())
        print('copied %d texture(s), %d shader(s)' % n[:2])
    out.save(a.out)
    print('wrote', a.out, '(%d system(s))' % len(names))


def cmd_import(a):
    dst = P3DFile.load(a.dst)
    src = P3DFile.load(a.src)
    new, info = import_systems(dst, src, set(a.system) if a.system else None, a.deps)
    dst.save(a.out)
    print('imported:', ', '.join(new))
    if a.deps:
        print('copied %d texture(s), %d shader(s)' % info[:2])
    miss = missing_shaders(dst, [s for s in dst.systems() if fget(s, 'name') in new])
    if miss:
        print('WARNING shaders missing in output (use --deps or add them):', ', '.join(miss))


def cmd_shaders(a):
    f = P3DFile.load(a.file)
    print('%-44s %-9s %-26s %-3s %-5s %s' % ('shader', 'type', 'texture', 'BL', 'FI', 'used by'))
    for r in shader_table(f):
        if a.name and r['name'] not in a.name:
            continue
        tex = (r['texture'] or '-') + ('' if r['tex_present'] in (True, None) else ' (MISSING)')
        print('%-44s %-9s %-26s %-3s %-5s %s' % (r['name'], r['pddi'], tex, r['blend'], r['filter'],
                                                 ', '.join(r['used_by'][:3]) + (' ...' if len(r['used_by']) > 3 else '')))
        if a.params:
            for k in r['chunk'].children:
                print('      %-5s %-20s %s' % (fget(k, 'param'), SHADER_PARAM_INFO.get(fget(k, 'param'), ''),
                                               shader_info(r['chunk'])[1].get(fget(k, 'param'), fget(k, 'texture'))))
    for t in (c for c in f.chunks if c.id == ID_TEXTURE):
        if a.textures:
            print('texture %-34s %sx%s bpp=%s mips=%s' % (fget(t, 'name'), fget(t, 'width'), fget(t, 'height'),
                                                          fget(t, 'bpp'), fget(t, 'num_mips')))


def cmd_check(a):
    f = P3DFile.load(a.file)
    provs = [(os.path.basename(p), P3DFile.load(p)) for p in (a.provider or [])]
    r = check_deps(f, provs)
    print('missing shaders  :', len(r['missing_shaders']))
    for n, u in sorted(r['missing_shaders'].items()):
        print('   %-40s used by %s%s' % (n, ', '.join(u[:3]), '   -> in ' + r['found_in'][n] if n in r['found_in'] else ''))
    print('missing textures :', len(r['missing_textures']))
    for n, u in sorted(r['missing_textures'].items()):
        print('   %-40s needed by %s%s' % (n, ', '.join(u[:3]), '   -> in ' + r['found_in'][n] if n in r['found_in'] else ''))
    print('shaders not used by any particle system:', len(r['unused_shaders']))


def cmd_import_shaders(a):
    dst = P3DFile.load(a.dst)
    src = P3DFile.load(a.src)
    nt, ns, miss = copy_shaders(dst, src, a.name)
    dst.save(a.out)
    print('copied %d shader(s), %d texture(s)' % (ns, nt))
    if miss:
        print('not found in source:', ', '.join(miss))


def cmd_tojson(a):
    f = P3DFile.load(a.file)
    with open(a.out, 'w') as fh:
        json.dump(systems_to_json(f, set(a.system) if a.system else None), fh, indent=1)
    print('wrote', a.out)


def cmd_fromjson(a):
    dst = P3DFile.load(a.dst)
    with open(a.json) as fh:
        new = import_json(dst, json.load(fh))
    dst.save(a.out)
    print('imported:', ', '.join(new))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[1], epilog=CREDIT)
    p.add_argument('--version', action='version', version='p3d_particle_tool - ' + CREDIT)
    sp = p.add_subparsers(dest='cmd')
    g = sp.add_parser('gui')
    g.add_argument('file', nargs='?')
    for n, fn, args in (('list', cmd_list, ['file']), ('dump', cmd_dump, ['file']),
                        ('roundtrip', cmd_roundtrip, ['file']),
                        ('export', cmd_export, ['file', 'out']),
                        ('import', cmd_import, ['dst', 'src', 'out']),
                        ('to-json', cmd_tojson, ['file', 'out']),
                        ('shaders', cmd_shaders, ['file']), ('check', cmd_check, ['file']),
                        ('import-shaders', cmd_import_shaders, ['dst', 'src', 'out']),
                        ('from-json', cmd_fromjson, ['dst', 'json', 'out'])):
        s = sp.add_parser(n)
        for x in args:
            s.add_argument(x)
        if n in ('dump', 'export', 'import', 'to-json'):
            s.add_argument('-s', '--system', action='append')
        if n in ('export', 'import'):
            s.add_argument('--deps', action='store_true', help='also copy missing shaders/textures')
        if n == 'shaders':
            s.add_argument('-n', '--name', action='append')
            s.add_argument('--params', action='store_true', help='print every shader parameter')
            s.add_argument('--textures', action='store_true', help='also list texture chunks')
        if n == 'check':
            s.add_argument('-p', '--provider', action='append',
                           help='another .p3d searched for the missing shaders/textures')
        if n == 'import-shaders':
            s.add_argument('-n', '--name', action='append', required=True)
        s.set_defaults(fn=fn)
    a = p.parse_args(argv)
    if a.cmd in (None, 'gui'):
        run_gui(getattr(a, 'file', None))
        return 0
    return a.fn(a) or 0

# ----------------------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------------------


def run_gui(path=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title('P3D Particle Tool  |  ' + CREDIT)
            self.geometry('1250x780')
            self.f = P3DFile()
            self.node = {}
            self.sim = None
            self.sim_ps = None
            self.playing = False
            self.cur = None
            self.cur_list = None
            self.show_all = tk.BooleanVar(value=False)
            self.loop = tk.BooleanVar(value=True)
            self._menu()
            self._layout()
            if path:
                self.open_file(path)

        # ---- layout
        def _menu(self):
            m = tk.Menu(self)
            fm = tk.Menu(m, tearoff=0)
            fm.add_command(label='Open...', command=self.cmd_open)
            fm.add_command(label='Save', command=self.cmd_save)
            fm.add_command(label='Save As...', command=self.cmd_saveas)
            fm.add_separator()
            fm.add_command(label='Import systems from .p3d...', command=self.cmd_import)
            fm.add_command(label='Import systems from JSON...', command=self.cmd_import_json)
            fm.add_command(label='Export selected system to .p3d...', command=self.cmd_export)
            fm.add_command(label='Export selected system to JSON...', command=self.cmd_export_json)
            fm.add_separator()
            fm.add_command(label='Quit', command=self.destroy)
            m.add_cascade(label='File', menu=fm)
            em = tk.Menu(m, tearoff=0)
            em.add_command(label='Rename system...', command=self.cmd_rename)
            em.add_command(label='Duplicate system', command=self.cmd_duplicate)
            em.add_command(label='Delete selected chunk', command=self.cmd_delete)
            m.add_cascade(label='Edit', menu=em)
            tm = tk.Menu(m, tearoff=0)
            tm.add_command(label='Shader browser / editor...', command=self.cmd_shaders)
            tm.add_command(label='Check shader && texture dependencies...', command=self.cmd_check)
            m.add_cascade(label='Tools', menu=tm)
            vm = tk.Menu(m, tearoff=0)
            vm.add_checkbutton(label='Show all chunks', variable=self.show_all, command=self.refresh_tree)
            m.add_cascade(label='View', menu=vm)
            hm = tk.Menu(m, tearoff=0)
            hm.add_command(label='About', command=lambda: messagebox.showinfo(
                'About', 'P3D Particle Tool\n\n' + CREDIT))
            m.add_cascade(label='Help', menu=hm)
            self.config(menu=m)

        def _layout(self):
            ttk.Label(self, text=CREDIT, anchor='e').pack(side='bottom', fill='x', padx=6)
            pw = ttk.PanedWindow(self, orient='horizontal')
            pw.pack(fill='both', expand=True)
            left = ttk.Frame(pw)
            self.tree = ttk.Treeview(left, show='tree', selectmode='browse')
            sb = ttk.Scrollbar(left, command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')
            self.tree.bind('<<TreeviewSelect>>', self.on_select)
            self.tree.bind('<<TreeviewOpen>>', self.on_open)
            pw.add(left, weight=1)
            self.nb = ttk.Notebook(pw)
            pw.add(self.nb, weight=3)

            pf = ttk.Frame(self.nb)
            self.nb.add(pf, text='Properties')
            self.ptitle = ttk.Label(pf, text='', font=('TkDefaultFont', 10, 'bold'))
            self.ptitle.pack(anchor='w', padx=6, pady=4)
            self.ftree = ttk.Treeview(pf, columns=('kind', 'value', 'alt'), height=14)
            self.ftree.heading('#0', text='Field')
            self.ftree.heading('kind', text='Type')
            self.ftree.heading('value', text='Value')
            self.ftree.heading('alt', text='As float / note')
            self.ftree.column('#0', width=160)
            self.ftree.column('kind', width=60)
            self.ftree.column('value', width=260)
            self.ftree.column('alt', width=160)
            self.ftree.pack(fill='x', padx=6)
            self.ftree.bind('<Double-1>', self.edit_field)
            ttk.Label(pf, text='Raw header bytes (hex) - edit and press Apply:').pack(anchor='w', padx=6, pady=(8, 0))
            self.hex = tk.Text(pf, height=6, wrap='word')
            self.hex.pack(fill='x', padx=6)
            ttk.Button(pf, text='Apply hex', command=self.apply_hex).pack(anchor='w', padx=6, pady=4)

            cf = ttk.Frame(self.nb)
            self.nb.add(cf, text='Keyframes')
            self.ctitle = ttk.Label(cf, text='Select a channel chunk', font=('TkDefaultFont', 10, 'bold'))
            self.ctitle.pack(anchor='w', padx=6, pady=4)
            self.ktree = ttk.Treeview(cf, columns=('frame', 'value'), show='headings', height=16)
            self.ktree.heading('frame', text='Frame')
            self.ktree.heading('value', text='Value')
            self.ktree.column('frame', width=80)
            self.ktree.column('value', width=380)
            self.ktree.pack(fill='x', padx=6)
            bf = ttk.Frame(cf)
            bf.pack(anchor='w', padx=6, pady=6)
            ttk.Button(bf, text='Edit value', command=self.key_edit).pack(side='left')
            ttk.Button(bf, text='Add key', command=self.key_add).pack(side='left', padx=4)
            ttk.Button(bf, text='Delete key', command=self.key_del).pack(side='left')
            ttk.Label(cf, text='Colour: #RRGGBB[AA] or r,g,b[,a].  Group tables are rebuilt on every edit.').pack(anchor='w', padx=6)

            vf = ttk.Frame(self.nb)
            self.nb.add(vf, text='Preview')
            top = ttk.Frame(vf)
            top.pack(fill='x')
            ttk.Button(top, text='Play/Pause', command=self.toggle_play).pack(side='left', padx=4, pady=4)
            ttk.Button(top, text='Restart', command=self.restart).pack(side='left')
            ttk.Checkbutton(top, text='Loop', variable=self.loop).pack(side='left', padx=6)
            self.yaw = tk.DoubleVar(value=30)
            self.pitch = tk.DoubleVar(value=20)
            self.zoom = tk.DoubleVar(value=120)
            for lab, var, lo, hi in (('Yaw', self.yaw, -180, 180), ('Pitch', self.pitch, -89, 89),
                                     ('Zoom', self.zoom, 20, 500)):
                ttk.Label(top, text=lab).pack(side='left', padx=(10, 0))
                ttk.Scale(top, from_=lo, to=hi, variable=var, length=110).pack(side='left')
            self.canvas = tk.Canvas(vf, bg='#14161a', highlightthickness=0)
            self.canvas.pack(fill='both', expand=True)
            self.status = ttk.Label(vf, text='Approximate preview: channel semantics are inferred, no textures/blending.')
            self.status.pack(anchor='w')

        # ---- tree
        def refresh_tree(self):
            self.tree.delete(*self.tree.get_children())
            self.node.clear()
            for c in self.f.chunks:
                if self.show_all.get() or c.id == ID_PSYS:
                    self._add('', c, self.f.chunks)

        def _add(self, parent, c, siblings):
            iid = self.tree.insert(parent, 'end', text=chunk_label(c))
            self.node[iid] = (c, siblings)
            if c.children:
                self.tree.insert(iid, 'end', text='...', tags=('dummy',))
            return iid

        def on_open(self, _e):
            iid = self.tree.focus()
            kids = self.tree.get_children(iid)
            if len(kids) == 1 and 'dummy' in self.tree.item(kids[0], 'tags'):
                self.tree.delete(kids[0])
                c = self.node[iid][0]
                for k in c.children:
                    self._add(iid, k, c.children)

        def selected(self):
            s = self.tree.selection()
            return self.node.get(s[0]) if s else None

        def system_of_selection(self):
            s = self.tree.selection()
            while s:
                c = self.node[s[0]][0]
                if c.id == ID_PSYS:
                    return c
                s = (self.tree.parent(s[0]),) if self.tree.parent(s[0]) else ()
            return None

        def on_select(self, _e):
            sel = self.selected()
            if not sel:
                return
            c = sel[0]
            self.cur, self.cur_list = c, sel[1]
            self.ptitle.config(text='%s   (0x%X, header %d bytes, %d children)' % (
                chunk_label(c), c.id, len(c.header), len(c.children)))
            self.ftree.delete(*self.ftree.get_children())
            fl = get_fields(c)
            for x in (fl or []):
                if x['name'] == 'rest' and not x['value']:
                    continue
                alt = ''
                if x['kind'] == 'u32':
                    alt = '%.6g' % struct.unpack('<f', struct.pack('<I', x['value']))[0]
                self.ftree.insert('', 'end', text=x['name'], values=(x['kind'], x['value'], alt))
            self.hex.delete('1.0', 'end')
            self.hex.insert('1.0', c.header.hex(' '))
            self.fill_keys()
            note = self.emitter_note()
            if note:
                self.status.config(text=note)
            ps = self.system_of_selection()
            if ps is not None and ps is not self.sim_ps:
                self.sim_ps = ps
                self.sim = SystemSim(ps)
                if not self.playing:
                    self.toggle_play()

        # ---- property editing
        def edit_field(self, _e):
            sel = self.ftree.selection()
            if not sel or not self.cur:
                return
            name = self.ftree.item(sel[0], 'text')
            kind = self.ftree.item(sel[0], 'values')[0]
            old = self.ftree.item(sel[0], 'values')[1]
            v = simpledialog.askstring('Edit %s' % name, '%s (%s):' % (name, kind), initialvalue=str(old))
            if v is None:
                return
            try:
                fset(self.cur, name, parse_value(kind, v))
            except Exception as ex:
                messagebox.showerror('Invalid value', str(ex))
                return
            self.after_edit()

        def apply_hex(self):
            if not self.cur:
                return
            try:
                self.cur.header = bytes.fromhex(self.hex.get('1.0', 'end').replace('\n', ' ').replace(' ', ''))
            except ValueError as ex:
                messagebox.showerror('Invalid hex', str(ex))
                return
            self.after_edit()

        def after_edit(self):
            fix_structure(self.f.chunks)
            keep = self.cur
            self.refresh_tree()
            self.sim_ps = None
            self.cur = keep
            self.set_title(True)

        def set_title(self, dirty=False):
            self.title('P3D Particle Tool - %s%s  |  %s' % (self.f.path or '(new)', ' *' if dirty else '', CREDIT))

        # ---- keyframes
        def fill_keys(self):
            self.ktree.delete(*self.ktree.get_children())
            c = self.cur
            d = decode_channel(c) if c else None
            if not d:
                self.ctitle.config(text='Select a channel chunk')
                return
            self.ctitle.config(text='%s - %s  (%s)' % (d['tag'], TAG_INFO.get(d['tag'], 'unknown meaning'),
                                                      CHANNEL_INFO[c.id][2]))
            for i, (fr, v) in enumerate(d['keys']):
                self.ktree.insert('', 'end', iid=str(i), values=(fr, format_value(c.id, v)))

        def _commit_keys(self, d):
            encode_channel(self.cur, d['version'], d['tag'], d['keys'], d.get('start', 0))
            self.after_edit()
            self.fill_keys()

        def key_edit(self):
            sel = self.ktree.selection()
            d = decode_channel(self.cur) if self.cur else None
            if not sel or not d:
                return
            i = int(sel[0])
            t = simpledialog.askstring('Edit key', 'Value:', initialvalue=format_value(self.cur.id, d['keys'][i][1]))
            if t is None:
                return
            try:
                v = parse_channel_value(self.cur.id, t)
            except Exception as ex:
                messagebox.showerror('Invalid value', str(ex))
                return
            d['keys'][i] = (d['keys'][i][0], v)
            self._commit_keys(d)

        def key_add(self):
            d = decode_channel(self.cur) if self.cur else None
            if not d:
                return
            fr = simpledialog.askinteger('Add key', 'Frame (0-65535):', minvalue=0, maxvalue=65535)
            if fr is None:
                return
            base = d['keys'][-1][1] if d['keys'] else (0,) * CHANNEL_INFO[self.cur.id][1]
            if any(k[0] == fr for k in d['keys']):
                messagebox.showerror('Add key', 'A key already exists at that frame')
                return
            d['keys'].append((fr, base))
            d['keys'].sort(key=lambda k: k[0])
            self._commit_keys(d)

        def key_del(self):
            sel = self.ktree.selection()
            d = decode_channel(self.cur) if self.cur else None
            if not sel or not d:
                return
            if len(d['keys']) <= 1:
                messagebox.showerror('Delete key', 'A channel needs at least one key')
                return
            del d['keys'][int(sel[0])]
            self._commit_keys(d)

        # ---- file commands
        def open_file(self, p):
            try:
                self.f = P3DFile.load(p)
            except Exception as ex:
                messagebox.showerror('Open failed', str(ex))
                return
            self.sim = self.sim_ps = None
            self.refresh_tree()
            self.set_title()
            n = len(self.f.systems())
            self.status.config(text='%d particle systems. Approximate preview (channel semantics inferred).' % n)

        def cmd_open(self):
            p = filedialog.askopenfilename(filetypes=[('Pure3D', '*.p3d'), ('All', '*.*')])
            if p:
                self.open_file(p)

        def cmd_save(self):
            if not self.f.path:
                return self.cmd_saveas()
            self.f.save()
            self.set_title()

        def cmd_saveas(self):
            p = filedialog.asksaveasfilename(defaultextension='.p3d', filetypes=[('Pure3D', '*.p3d')])
            if p:
                self.f.save(p)
                self.set_title()

        def cmd_import(self):
            p = filedialog.askopenfilename(title='Import from', filetypes=[('Pure3D', '*.p3d')])
            if not p:
                return
            try:
                src = P3DFile.load(p)
            except Exception as ex:
                messagebox.showerror('Import failed', str(ex))
                return
            names = system_names(src)
            win = tk.Toplevel(self)
            win.title('Choose systems to import')
            lb = tk.Listbox(win, selectmode='extended', width=60, height=18)
            for n in names:
                lb.insert('end', n)
            lb.pack(padx=8, pady=8)
            deps = tk.BooleanVar(value=True)
            ttk.Checkbutton(win, text='Also copy missing shaders / textures', variable=deps).pack()

            def go():
                chosen = {names[i] for i in lb.curselection()} or None
                new, info = import_systems(self.f, src, chosen, deps.get())
                win.destroy()
                self.after_edit()
                miss = missing_shaders(self.f, [s for s in self.f.systems() if fget(s, 'name') in new])
                messagebox.showinfo('Imported', 'Imported: %s\nCopied %d texture(s), %d shader(s)%s' % (
                    ', '.join(new), info[0], info[1],
                    ('\nStill missing shaders: ' + ', '.join(miss)) if miss else ''))
            ttk.Button(win, text='Import', command=go).pack(pady=6)

        def cmd_export(self):
            ps = self.system_of_selection()
            if not ps:
                return messagebox.showinfo('Export', 'Select a particle system first')
            p = filedialog.asksaveasfilename(defaultextension='.p3d', filetypes=[('Pure3D', '*.p3d')])
            if not p:
                return
            out = P3DFile([copy.deepcopy(ps)])
            if messagebox.askyesno('Export', 'Also include the shaders/textures it uses?'):
                copy_dependencies(out, self.f, out.systems())
            out.save(p)

        def cmd_export_json(self):
            ps = self.system_of_selection()
            if not ps:
                return messagebox.showinfo('Export', 'Select a particle system first')
            p = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON', '*.json')])
            if p:
                with open(p, 'w') as fh:
                    json.dump(systems_to_json(self.f, {fget(ps, 'name')}), fh, indent=1)

        def cmd_import_json(self):
            p = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
            if not p:
                return
            try:
                with open(p) as fh:
                    new = import_json(self.f, json.load(fh))
            except Exception as ex:
                return messagebox.showerror('Import failed', str(ex))
            self.after_edit()
            messagebox.showinfo('Imported', ', '.join(new))

        def cmd_rename(self):
            ps = self.system_of_selection()
            if not ps:
                return
            n = simpledialog.askstring('Rename', 'New system name:', initialvalue=fget(ps, 'name'))
            if n:
                rename_system(ps, n)
                self.after_edit()

        def cmd_duplicate(self):
            ps = self.system_of_selection()
            if not ps:
                return
            c = copy.deepcopy(ps)
            rename_system(c, unique_name(set(system_names(self.f)), fget(ps, 'name')))
            self.f.chunks.insert(self.f.chunks.index(ps) + 1, c)
            self.after_edit()

        def cmd_delete(self):
            sel = self.selected()
            if not sel:
                return
            c, sib = sel
            if messagebox.askyesno('Delete', 'Delete %s?' % chunk_label(c)):
                sib.remove(c)
                self.cur = None
                self.after_edit()

        def cmd_check(self):
            r = check_deps(self.f)
            ms, mt = r['missing_shaders'], r['missing_textures']
            if not ms and not mt:
                return messagebox.showinfo('Dependencies', 'Every shader and texture is defined in this file.')
            lines = ['Missing shaders (%d):' % len(ms)]
            lines += ['  %s  (used by %s)' % (n, ', '.join(u[:2])) for n, u in sorted(ms.items())[:12]]
            lines += ['', 'Missing textures (%d):' % len(mt)]
            lines += ['  %s  (needed by %s)' % (n, ', '.join(u[:2])) for n, u in sorted(mt.items())[:12]]
            if messagebox.askyesno('Dependencies', '\n'.join(lines) + '\n\nPick a .p3d to copy them from?'):
                self.import_missing_from(list(ms), list(mt))

        def import_missing_from(self, shaders, textures):
            p = filedialog.askopenfilename(title='Copy shaders/textures from', filetypes=[('Pure3D', '*.p3d')])
            if not p:
                return
            try:
                src = P3DFile.load(p)
            except Exception as ex:
                return messagebox.showerror('Open failed', str(ex))
            nt, ns, miss = copy_shaders(self.f, src, shaders)
            nt2, miss2 = copy_textures(self.f, src, textures)
            self.after_edit()
            left = [m for m in miss + miss2]
            messagebox.showinfo('Copied', 'Copied %d shader(s) and %d texture(s).%s' % (
                ns, nt + nt2, ('\nNot found in that file: ' + ', '.join(left[:12])) if left else ''))

        def cmd_shaders(self):
            win = tk.Toplevel(self)
            win.title('Shader browser / editor')
            win.geometry('1100x640')
            cols = ('pddi', 'texture', 'blend', 'filter', 'used')
            tv = ttk.Treeview(win, columns=cols, height=14)
            for c_, t_, w_ in (('#0', 'Shader', 300), ('pddi', 'Type', 80), ('texture', 'Texture', 200),
                               ('blend', 'BLMD', 50), ('filter', 'FIMD', 50), ('used', 'Used by', 340)):
                tv.heading(c_, text=t_)
                tv.column(c_, width=w_)
            tv.pack(fill='both', expand=True, padx=6, pady=6)
            pv = ttk.Treeview(win, columns=('value', 'note'), height=9)
            pv.heading('#0', text='Parameter')
            pv.heading('value', text='Value (double-click to edit)')
            pv.heading('note', text='Meaning (inferred)')
            pv.column('#0', width=120)
            pv.column('value', width=300)
            pv.column('note', width=300)
            pv.pack(fill='x', padx=6)
            state = {'rows': [], 'cur': None}

            def fill():
                tv.delete(*tv.get_children())
                pv.delete(*pv.get_children())
                state['rows'] = shader_table(self.f)
                for i, r in enumerate(state['rows']):
                    tex = (r['texture'] or '-') + ('' if r['tex_present'] in (True, None) else '  (MISSING)')
                    tv.insert('', 'end', iid=str(i), text=r['name'],
                              values=(r['pddi'], tex, r['blend'], r['filter'], ', '.join(r['used_by'][:4])))

            def fill_params(_e=None):
                sel = tv.selection()
                pv.delete(*pv.get_children())
                if not sel:
                    state['cur'] = None
                    return
                sh = state['rows'][int(sel[0])]['chunk']
                state['cur'] = sh
                for j, k in enumerate(sh.children):
                    pr = fget(k, 'param')
                    if k.id == ID_SHADER_TEX:
                        v = fget(k, 'texture')
                    elif k.id == ID_SHADER_CLR:
                        v = '#' + str(fget(k, 'rest')).upper()
                    else:
                        v = fget(k, 'value')
                    pv.insert('', 'end', iid=str(j), text=pr, values=(v, SHADER_PARAM_INFO.get(pr, '')))

            def edit_param(_e=None):
                sel = pv.selection()
                sh = state['cur']
                if not sel or sh is None:
                    return
                k = sh.children[int(sel[0])]
                pr = fget(k, 'param')
                old = pv.item(sel[0], 'values')[0]
                t = simpledialog.askstring('Edit %s' % pr, '%s:' % pr, initialvalue=str(old), parent=win)
                if t is None:
                    return
                try:
                    set_shader_param(sh, pr, t)
                except Exception as ex:
                    return messagebox.showerror('Invalid value', str(ex), parent=win)
                keep = tv.selection()
                self.after_edit()
                fill()
                if keep:
                    tv.selection_set(keep)
                    fill_params()

            def rename():
                sh = state['cur']
                if sh is None:
                    return
                old = fget(sh, 'name')
                n = simpledialog.askstring('Rename shader', 'New name (emitter references are updated):',
                                           initialvalue=old, parent=win)
                if not n or n == old:
                    return
                if _find_top(self.f, ID_SHADER, n):
                    return messagebox.showerror('Rename', 'A shader with that name already exists', parent=win)
                cnt = rename_shader(self.f, old, n)
                self.after_edit()
                fill()
                messagebox.showinfo('Rename', '%d emitter reference(s) updated' % cnt, parent=win)

            def import_shaders():
                p = filedialog.askopenfilename(title='Import shaders from', filetypes=[('Pure3D', '*.p3d')], parent=win)
                if not p:
                    return
                try:
                    src = P3DFile.load(p)
                except Exception as ex:
                    return messagebox.showerror('Open failed', str(ex), parent=win)
                names = [r['name'] for r in shader_table(src)]
                dlg = tk.Toplevel(win)
                dlg.title('Choose shaders to import')
                lb = tk.Listbox(dlg, selectmode='extended', width=70, height=22)
                for n in names:
                    lb.insert('end', n)
                lb.pack(padx=8, pady=8)

                def go():
                    chosen = [names[i] for i in lb.curselection()]
                    nt, ns, miss = copy_shaders(self.f, src, chosen)
                    dlg.destroy()
                    self.after_edit()
                    fill()
                    messagebox.showinfo('Imported', 'Copied %d shader(s), %d texture(s)%s' % (
                        ns, nt, ('\nNot found: ' + ', '.join(miss[:10])) if miss else ''), parent=win)
                ttk.Button(dlg, text='Import selected', command=go).pack(pady=6)

            def delete_shader():
                sh = state['cur']
                if sh is None:
                    return
                n = fget(sh, 'name')
                users = shader_users(self.f).get(n, [])
                if users and not messagebox.askyesno('Delete', '%s is used by %d system(s). Delete anyway?' % (n, len(users)), parent=win):
                    return
                self.f.chunks.remove(sh)
                state['cur'] = None
                self.after_edit()
                fill()

            tv.bind('<<TreeviewSelect>>', fill_params)
            pv.bind('<Double-1>', edit_param)
            bf = ttk.Frame(win)
            bf.pack(fill='x', padx=6, pady=6)
            for t_, cmd_ in (('Rename shader...', rename), ('Import shaders from .p3d...', import_shaders),
                             ('Delete shader', delete_shader), ('Close', win.destroy)):
                ttk.Button(bf, text=t_, command=cmd_).pack(side='left', padx=3)
            fill()

        def emitter_note(self):
            s_ = self.tree.selection()
            while s_:
                c = self.node[s_[0]][0]
                if c.id == ID_EMITTER:
                    name = fget(c, 'shader')
                    row = next((r for r in shader_table(self.f) if r['name'] == name), None)
                    if row is None:
                        return 'Emitter shader %s is not defined in this file (Tools > Check dependencies)' % name
                    return 'Emitter shader %s: texture %s%s, BLMD=%s FIMD=%s' % (
                        name, row['texture'], '' if row['tex_present'] in (True, None) else ' (MISSING)',
                        row['blend'], row['filter'])
                par = self.tree.parent(s_[0])
                s_ = (par,) if par else ()
            return None

        # ---- preview
        def toggle_play(self):
            self.playing = not self.playing
            if self.playing:
                self.tick()

        def restart(self):
            if self.sim:
                self.sim.reset()

        def tick(self):
            if not self.playing:
                return
            s = self.sim
            if s:
                if s.t >= s.length + 60:
                    if self.loop.get():
                        s.reset()
                else:
                    s.step()
                self.draw()
            self.after(int(1000 / (s.fps if s else 30)), self.tick)

        def draw(self):
            cv = self.canvas
            cv.delete('all')
            w, h = cv.winfo_width(), cv.winfo_height()
            cx, cy = w / 2, h * 0.72
            z = self.zoom.get()
            ya, pa = math.radians(self.yaw.get()), math.radians(self.pitch.get())
            cv.create_line(0, cy, w, cy, fill='#2a2e36')
            for p in sorted(self.sim.parts, key=lambda q: q['p'][2]):
                x, y, zz = p['p']
                x1 = x * math.cos(ya) - zz * math.sin(ya)
                z1 = x * math.sin(ya) + zz * math.cos(ya)
                y1 = y * math.cos(pa) - z1 * math.sin(pa)
                sx, sy = cx + x1 * z, cy - y1 * z
                (r, g, b), a = self.sim.color_of(p)
                bg = (20, 22, 26)
                col = '#%02x%02x%02x' % tuple(int(bg[i] + ((r, g, b)[i] - bg[i]) * a) for i in range(3))
                rad = max(1.5, self.sim.size_of(p) * z * 0.5)
                cv.create_oval(sx - rad, sy - rad, sx + rad, sy + rad, fill=col, outline='')
            cv.create_text(8, 8, anchor='nw', fill='#8a93a0',
                           text='frame %d   particles %d' % (self.sim.t, len(self.sim.parts)))

    App().mainloop()


if __name__ == '__main__':
    sys.exit(main())
