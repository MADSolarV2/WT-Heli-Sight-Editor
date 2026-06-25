#!/usr/bin/env python3
import sys
sys.dont_write_bytecode = True   # keep the folder clean — no __pycache__

"""
WT Helicopter Sight Vector Editor
Freeform VECTOR_LINE / VECTOR_ELLIPSE reticle designer for War Thunder.
Author: MADSolar

Controls (canvas):
  Select  — click to select; drag element or endpoint handles to move
            drag on empty space to rubber-band select multiple elements
            drag any selected element to move all selected together
  Line    — click-drag to draw a new line segment
  Ellipse — click-drag from center outward to draw an ellipse
  Wheel   — zoom (toward cursor)
  Middle  — pan the scene
  Del/BS  — delete selected element(s)
  Esc     — cancel current drag / clear selection
"""

import os, re, struct, shutil, subprocess, threading, json
import tkinter as tk
from tkinter import ttk, colorchooser, filedialog, messagebox, simpledialog


# ── Auto-install zstandard on first run ───────────────────────────────────────

def _ensure_zstandard() -> bool:
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        pass

    splash = tk.Tk()
    splash.title("WT Sight Editor — First-time Setup")
    splash.resizable(False, False)
    try:
        splash.attributes('-topmost', True)
    except Exception:
        pass
    pad = ttk.Frame(splash, padding=28)
    pad.pack()
    ttk.Label(pad, text="First-time setup",
              font=('Segoe UI', 11, 'bold')).pack()
    ttk.Label(pad,
              text="Installing  zstandard  (needed to read War Thunder files)",
              font=('Segoe UI', 9)).pack(pady=6)
    bar = ttk.Progressbar(pad, mode='indeterminate', length=340)
    bar.pack(pady=8)
    bar.start(12)
    ttk.Label(pad, text="This only happens once.",
              font=('Segoe UI', 8), foreground='gray').pack()
    err: list = [None]

    def _run():
        try:
            r = subprocess.run([sys.executable, '-m', 'pip', 'install', 'zstandard'],
                               capture_output=True, text=True)
            if r.returncode != 0:
                err[0] = (r.stderr or r.stdout)[-600:]
        except Exception as exc:
            err[0] = str(exc)
        splash.after(0, splash.destroy)

    threading.Thread(target=_run, daemon=True).start()
    splash.mainloop()

    if err[0]:
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(
            "Setup failed",
            "Could not install zstandard automatically.\n\n"
            f"Open a command prompt and run:\n"
            f'    "{sys.executable}" -m pip install zstandard\n\n'
            f"Details:\n{err[0][:400]}", parent=root)
        root.destroy()
        return False
    return True


# ─── vromfs constants & helpers ───────────────────────────────────────────────

FILE_ORDER = [
    'gameuiskin/ccip_rocket_sight.svg',
    'gameuiskin/rocket_sight.svg',
    'reactivegui/airHudElems.nut',
    'reactivegui/airhudelems.nut',
    'ui/gameuiskin/ccip_rocket_sight.svg',
    'ui/gameuiskin/rocket_sight.svg',
]
_KEY1 = [0xAA55AA55, 0xF00FF00F, 0xAA55AA55, 0x12481248]
_KEY2 = [0x12481248, 0xAA55AA55, 0xF00FF00F, 0xAA55AA55]


def _xor_blocks(data: bytearray, key1, key2):
    n = len(data)
    if n < 32:
        return data
    result = bytearray(data)
    w1 = struct.unpack_from('<4L', result, 0)
    struct.pack_into('<4L', result, 0, *[a ^ b for a, b in zip(w1, key1)])
    mid = (n - 32) // 4 * 4
    w2 = struct.unpack_from('<4L', result, 16 + mid)
    struct.pack_into('<4L', result, 16 + mid, *[a ^ b for a, b in zip(w2, key2)])
    return result


def vromfs_unpack(path: str) -> dict:
    """Unpack a VRFs or VRFx vromfs file into {virtual_path: bytes}.

    VRFs (pkg_user format): 16-byte outer header, payload at offset 16.
    VRFx (root game format): 24-byte outer header, payload at offset 24.
    Both use identical XOR obfuscation + zstd on the payload, and the same
    internal file-table structure once decompressed.
    """
    import zstandard
    with open(path, 'rb') as f:
        raw = f.read()
    magic = raw[:4]
    if magic == b'VRFs':
        payload_offset = 16
    elif magic == b'VRFx':
        payload_offset = 24
    else:
        raise ValueError(f"Unrecognised vromfs magic {magic!r}: {path}")
    data = _xor_blocks(bytearray(raw[payload_offset:]), _KEY1, _KEY2)
    body = zstandard.ZstdDecompressor().decompress(bytes(data))
    fn_off = struct.unpack_from('<I', body, 0x00)[0]
    n      = struct.unpack_from('<I', body, 0x04)[0]
    fd_off = struct.unpack_from('<I', body, 0x10)[0]
    result = {}
    for i in range(n):
        str_off = struct.unpack_from('<I', body, fn_off + i * 8)[0]
        end     = body.index(b'\x00', str_off)
        name    = body[str_off:end].decode('utf-8', errors='replace')
        offset  = struct.unpack_from('<I', body, fd_off + i * 16)[0]
        size    = struct.unpack_from('<I', body, fd_off + i * 16 + 4)[0]
        result[name] = body[offset:offset + size]
    return result


def find_nut_in_game(wt_dir: str) -> bytes | None:
    """Search the game's root VRFx vromfs files for a fresh airHudElems.nut.

    gui.vromfs.bin is searched first — confirmed to contain the nut (3.9 MB,
    much faster than aces.vromfs.bin at 17 MB).  Both case variants of the
    filename are checked since the game only stores the lowercase path.
    Returns the raw nut bytes, or None if not found / unreadable.
    """
    candidates = ['gui.vromfs.bin', 'aces.vromfs.bin', 'game.vromfs.bin',
                  'char.vromfs.bin', 'wwdata.vromfs.bin']
    for fname in candidates:
        fpath = os.path.join(wt_dir, fname)
        if not os.path.exists(fpath):
            continue
        try:
            files = vromfs_unpack(fpath)
            nut = (files.get('reactivegui/airHudElems.nut') or
                   files.get('reactivegui/airhudelems.nut'))
            if nut and len(nut) > 1000:
                return nut
        except Exception:
            continue
    return None


def vromfs_repack(files_dict: dict) -> bytes:
    import zstandard
    filenames = FILE_ORDER
    n = len(filenames)
    FN_TBL = 0x20
    str_start = (FN_TBL + n * 8 + 7) & ~7
    fn_strings = b''
    fn_offsets = []
    for name in filenames:
        fn_offsets.append(str_start + len(fn_strings))
        fn_strings += name.encode() + b'\x00'
    while len(fn_strings) % 4:
        fn_strings += b'\x00'
    fn_table = b''.join(struct.pack('<II', off, 0) for off in fn_offsets)
    gap = b'\x00' * (str_start - FN_TBL - len(fn_table))
    fn_area = fn_table + gap + fn_strings
    fd_tbl  = (FN_TBL + len(fn_area) + 15) & ~15
    dat_off = (fd_tbl + n * 16 + 15) & ~15
    blob = b''
    records = []
    for i, name in enumerate(filenames):
        data = files_dict.get(name, b'')
        records.append((dat_off + len(blob), len(data)))
        blob += data
        if i < n - 1:
            while len(blob) % 16:
                blob += b'\x00'
    header = (struct.pack('<II', FN_TBL, n) + b'\x00' * 8 +
              struct.pack('<II', fd_tbl,  n) + b'\x00' * 8)
    fd_table = b''.join(struct.pack('<II', off, sz) + b'\x00' * 8
                        for off, sz in records)
    body = bytearray(header)
    body += fn_area
    body += b'\x00' * (fd_tbl  - len(body))
    body += fd_table
    body += b'\x00' * (dat_off - len(body))
    body += blob
    compressed = bytearray(zstandard.ZstdCompressor(level=3).compress(bytes(body)))
    compressed = _xor_blocks(compressed, _KEY1, _KEY2)
    outer = (b'VRFs' + b'\x00\x00PC' +
             struct.pack('<I', len(body)) +
             struct.pack('<I', len(compressed))[:3] + b'\x40')
    return outer + bytes(compressed)


# ─── Nut patching ─────────────────────────────────────────────────────────────

def patch_nut(nut: str, elements: list, r: int, g: int, b: int) -> str:
    elem_lines = ',\n'.join('    ' + e.squirrel() for e in elements)
    new_func = (
        'function helicopterRocketSightMode(sightMode) {\n'
        '  return [\n' + elem_lines + '\n  ]\n}')
    marker = 'function helicopterRocketSightMode(sightMode)'
    start = nut.find(marker)
    if start == -1:
        raise ValueError("helicopterRocketSightMode() not found in nut")
    depth = 0; i = start; end = -1
    while i < len(nut):
        if nut[i] == '{': depth += 1
        elif nut[i] == '}':
            depth -= 1
            if depth == 0: end = i + 1; break
        i += 1
    if end == -1:
        raise ValueError("Could not find end of helicopterRocketSightMode()")
    nut = nut[:start] + new_func + nut[end:]
    aim = 'let helicopterRocketAim'
    a0 = nut.find(aim)
    if a0 != -1:
        depth = 0; i = a0; a1 = -1; in_b = False
        while i < len(nut):
            if nut[i] == '{': depth += 1; in_b = True
            elif nut[i] == '}':
                depth -= 1
                if in_b and depth == 0: a1 = i + 1; break
            i += 1
        if a1 != -1:
            seg = nut[a0:a1]
            seg = re.sub(r'\bcolor\s*=\s*Color\(\d+,\s*\d+,\s*\d+,\s*\d+\)',
                         f'color = Color({r}, {g}, {b}, 255)', seg, count=1)
            seg = re.sub(r'\bfillColor\s*=\s*Color\(\d+,\s*\d+,\s*\d+,\s*\d+\)',
                         f'fillColor = Color({r}, {g}, {b}, 255)', seg, count=1)
            nut = nut[:a0] + seg + nut[a1:]
    return nut


# ─── Element classes ──────────────────────────────────────────────────────────

class VLine:
    kind = 'LINE'
    def __init__(self, x1, y1, x2, y2):
        self.x1=float(x1); self.y1=float(y1)
        self.x2=float(x2); self.y2=float(y2)
    def squirrel(self):
        return f"[VECTOR_LINE, {self.x1:.1f}, {self.y1:.1f}, {self.x2:.1f}, {self.y2:.1f}]"
    def fields(self):
        return [('x1',self.x1),('y1',self.y1),('x2',self.x2),('y2',self.y2)]
    def set_fields(self, d):
        self.x1=d['x1']; self.y1=d['y1']; self.x2=d['x2']; self.y2=d['y2']
    def label(self, i):
        return f"L{i}  ({self.x1:.0f},{self.y1:.0f})->({self.x2:.0f},{self.y2:.0f})"
    def translate(self, dx, dy):
        self.x1+=dx; self.y1+=dy; self.x2+=dx; self.y2+=dy
    def to_dict(self):
        return {'type':'LINE','x1':self.x1,'y1':self.y1,'x2':self.x2,'y2':self.y2}


class VEllipse:
    kind = 'ELLIPSE'
    def __init__(self, cx, cy, rx, ry):
        self.cx=float(cx); self.cy=float(cy)
        self.rx=float(rx); self.ry=float(ry)
    def squirrel(self):
        return f"[VECTOR_ELLIPSE, {self.cx:.1f}, {self.cy:.1f}, {self.rx:.1f}, {self.ry:.1f}]"
    def fields(self):
        return [('cx',self.cx),('cy',self.cy),('rx',self.rx),('ry',self.ry)]
    def set_fields(self, d):
        self.cx=d['cx']; self.cy=d['cy']; self.rx=d['rx']; self.ry=d['ry']
    def label(self, i):
        return f"E{i}  c=({self.cx:.0f},{self.cy:.0f}) r={self.rx:.1f},{self.ry:.1f}"
    def translate(self, dx, dy):
        self.cx+=dx; self.cy+=dy
    def to_dict(self):
        return {'type':'ELLIPSE','cx':self.cx,'cy':self.cy,'rx':self.rx,'ry':self.ry}


def _elem_from_dict(d):
    if d['type'] == 'LINE':
        return VLine(d['x1'], d['y1'], d['x2'], d['y2'])
    return VEllipse(d['cx'], d['cy'], d['rx'], d['ry'])


# ─── WT directory auto-detection ──────────────────────────────────────────────

def _find_wt_dir() -> str:
    wt_name = 'War Thunder'
    try:
        import winreg, re as _re
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'SOFTWARE\Valve\Steam')
        steam_path, _ = winreg.QueryValueEx(key, 'SteamPath')
        winreg.CloseKey(key)
        candidate = os.path.join(steam_path, 'steamapps', 'common', wt_name)
        if os.path.isdir(candidate):
            return candidate
        vdf = os.path.join(steam_path, 'steamapps', 'libraryfolders.vdf')
        if os.path.exists(vdf):
            with open(vdf, errors='replace') as f:
                text = f.read()
            for lib in _re.findall(r'"path"\s+"([^"]+)"', text):
                candidate = os.path.join(lib, 'steamapps', 'common', wt_name)
                if os.path.isdir(candidate):
                    return candidate
    except Exception:
        pass
    for drive in 'CDEFGH':
        for sub in [r'SteamLibrary', r'Steam',
                    r'Program Files (x86)\Steam', r'Program Files\Steam']:
            p = os.path.join(f'{drive}:\\', sub, 'steamapps', 'common', wt_name)
            if os.path.isdir(p):
                return p
    return r'C:\Program Files (x86)\Steam\steamapps\common\War Thunder'


# ─── Profiles ─────────────────────────────────────────────────────────────────

PROFILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profiles')


def profile_list():
    if not os.path.isdir(PROFILES_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(PROFILES_DIR) if f.endswith('.json'))


def profile_save(name: str, elements: list, color: tuple):
    os.makedirs(PROFILES_DIR, exist_ok=True)
    data = {'color': list(color), 'elements': [e.to_dict() for e in elements]}
    with open(os.path.join(PROFILES_DIR, f'{name}.json'), 'w') as f:
        json.dump(data, f, indent=2)


def profile_load(name: str):
    with open(os.path.join(PROFILES_DIR, f'{name}.json')) as f:
        data = json.load(f)
    return [_elem_from_dict(d) for d in data['elements']], tuple(data['color'])


def profile_delete(name: str):
    p = os.path.join(PROFILES_DIR, f'{name}.json')
    if os.path.exists(p):
        os.remove(p)


# ─── Editor ───────────────────────────────────────────────────────────────────

CW = CH = 620


class Editor:
    _vcx = 0.0; _vcy = 0.0; _scale = 0.5

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("WT Heli Sight Editor")

        self.elements: list       = []
        self.selected: int | None = None   # primary element for props panel
        self._sel_set: set        = set()  # all selected elements for multi-drag
        self.color: tuple         = (0, 255, 65)
        self.tool                 = tk.StringVar(value='select')
        self._snap                = tk.BooleanVar(value=False)
        self._snap_sz             = tk.StringVar(value='10')
        self._profile_var         = tk.StringVar()
        self.wt_dir               = tk.StringVar(value=_find_wt_dir())
        self._drag                = None
        self._pan_origin          = None
        self._prop_vars: dict     = {}

        self._build_ui()
        self._load_defaults()
        self._redraw()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        left   = ttk.Frame(self.root, width=180, padding=4)
        center = ttk.Frame(self.root)
        right  = ttk.Frame(self.root, width=250, padding=4)
        left.pack(side=tk.LEFT, fill=tk.Y);  left.pack_propagate(False)
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right.pack(side=tk.LEFT, fill=tk.Y);  right.pack_propagate(False)

        # ── Tools ──
        ttk.Label(left, text="Tool", font=('Segoe UI', 9, 'bold')).pack(anchor='w')
        for lbl, val in [('Select / Move', 'select'),
                         ('Add Line',      'line'),
                         ('Add Ellipse',   'ellipse')]:
            ttk.Radiobutton(left, text=lbl, variable=self.tool, value=val).pack(anchor='w')

        ttk.Separator(left).pack(fill='x', pady=4)
        snap_row = ttk.Frame(left)
        snap_row.pack(fill='x', pady=1)
        ttk.Checkbutton(snap_row, text="Snap to grid",
                        variable=self._snap).pack(side=tk.LEFT)
        ttk.Entry(snap_row, textvariable=self._snap_sz, width=5).pack(side=tk.RIGHT)
        ttk.Label(snap_row, text="step:").pack(side=tk.RIGHT)

        ttk.Separator(left).pack(fill='x', pady=4)
        ttk.Label(left, text="Reticle Color",
                  font=('Segoe UI', 9, 'bold')).pack(anchor='w')
        self._color_btn = tk.Button(left, text='        ', bg='#00ff41',
                                    relief='groove', bd=2, cursor='hand2',
                                    command=self._pick_color)
        self._color_btn.pack(anchor='w', pady=3)

        # ── Profiles ──
        ttk.Separator(left).pack(fill='x', pady=4)
        ttk.Label(left, text="Profiles",
                  font=('Segoe UI', 9, 'bold')).pack(anchor='w')
        self._prof_cb = ttk.Combobox(left, textvariable=self._profile_var,
                                      width=18, state='normal')
        self._prof_cb.pack(fill='x', pady=2)
        self._refresh_profile_list()
        pb = ttk.Frame(left)
        pb.pack(fill='x')
        ttk.Button(pb, text='Save',   command=self._prof_save,   width=6).pack(side=tk.LEFT, padx=1)
        ttk.Button(pb, text='Load',   command=self._prof_load,   width=6).pack(side=tk.LEFT, padx=1)
        ttk.Button(pb, text='Delete', command=self._prof_delete, width=7).pack(side=tk.LEFT, padx=1)

        # ── Elements list ──
        ttk.Separator(left).pack(fill='x', pady=4)
        ttk.Label(left, text="Elements",
                  font=('Segoe UI', 9, 'bold')).pack(anchor='w')
        lf = ttk.Frame(left)
        lf.pack(fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL)
        self._lb = tk.Listbox(lf, yscrollcommand=sb.set,
                               selectmode=tk.EXTENDED, exportselection=False,
                               font=('Courier New', 8), bg='#0d1117', fg='#c9d1d9',
                               selectbackground='#1f6feb', activestyle='none', bd=0)
        sb.configure(command=self._lb.yview)
        self._lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.LEFT, fill=tk.Y)
        self._lb.bind('<<ListboxSelect>>', self._lb_select)

        btns = ttk.Frame(left)
        btns.pack(fill='x', pady=2)
        for txt, cmd, w in [('Del',   self._delete_sel,          5),
                             ('Clear', self._clear_all,           5),
                             ('↑',     lambda: self._reorder(-1), 3),
                             ('↓',     lambda: self._reorder(1),  3)]:
            ttk.Button(btns, text=txt, command=cmd, width=w).pack(side=tk.LEFT, padx=1)

        # ── Canvas ──
        self._cv = tk.Canvas(center, width=CW, height=CH, bg='#0d1117',
                              highlightthickness=1, highlightbackground='#30363d',
                              cursor='crosshair')
        self._cv.pack(padx=4, pady=4)
        self._cv.bind('<Button-1>',        self._mb1)
        self._cv.bind('<B1-Motion>',       self._mm)
        self._cv.bind('<ButtonRelease-1>', self._mr)
        self._cv.bind('<Button-2>',        self._pan_start)
        self._cv.bind('<B2-Motion>',       self._pan_drag)
        self._cv.bind('<ButtonRelease-2>', self._pan_end)
        self._cv.bind('<MouseWheel>',      self._wheel)
        self._cv.bind('<Delete>',          lambda e: self._delete_sel())
        self._cv.bind('<BackSpace>',       lambda e: self._delete_sel())
        self._cv.bind('<Escape>',          lambda e: self._cancel())
        self._cv.focus_set()

        bar = ttk.Frame(center)
        bar.pack(fill='x', padx=4)
        self._coord_lbl = ttk.Label(bar, text="game: (0, 0)")
        self._coord_lbl.pack(side=tk.LEFT)
        ttk.Button(bar, text="Reset View", command=self._reset_view).pack(side=tk.RIGHT)
        ttk.Label(bar,
                  text="Grid: 100 gx | dashed = rockets aim-centre | Y units 2x X",
                  font=('Segoe UI', 7)).pack(side=tk.LEFT, padx=8)
        yt = tk.Label(bar, text="YouTube: @MADSolarV2",
                      font=('Segoe UI', 7), foreground='#ff4444',
                      cursor='hand2', bg=self._cv['bg'])
        yt.pack(side=tk.RIGHT, padx=4)
        yt.bind('<Button-1>', lambda e: __import__('webbrowser').open(
            'https://www.youtube.com/@MADSolarV2/videos'))

        # ── Properties ──
        ttk.Label(right, text="Properties",
                  font=('Segoe UI', 9, 'bold')).pack(anchor='w')
        self._prop_frame = ttk.Frame(right)
        self._prop_frame.pack(fill='x')
        self._refresh_props()

        # ── WT Directory / Version ──
        ttk.Separator(right).pack(fill='x', pady=6)
        ttk.Label(right, text="War Thunder Directory",
                  font=('Segoe UI', 9, 'bold')).pack(anchor='w')
        dr = ttk.Frame(right)
        dr.pack(fill='x')
        ttk.Entry(dr, textvariable=self.wt_dir,
                  font=('Segoe UI', 8)).pack(side=tk.LEFT, fill='x', expand=True)
        ttk.Button(dr, text='…', width=2, command=self._browse_dir).pack(side=tk.LEFT)
        self._ver_lbl = ttk.Label(right, text="Version: —", font=('Segoe UI', 8))
        self._ver_lbl.pack(anchor='w', pady=1)
        ttk.Button(right, text="Read Version", command=self._read_ver).pack(anchor='w')

        # ── Squirrel Preview ──
        ttk.Separator(right).pack(fill='x', pady=6)
        ttk.Label(right, text="Squirrel Output Preview",
                  font=('Segoe UI', 9, 'bold')).pack(anchor='w')
        self._sq = tk.Text(right, height=15, font=('Courier New', 7),
                           bg='#0d1117', fg='#e6edf3', wrap='none', bd=0,
                           insertbackground='white')
        self._sq.pack(fill='both', expand=True)

        # ── Export / Restore ──
        ttk.Separator(right).pack(fill='x', pady=6)
        self._status = ttk.Label(right, text="Ready.", wraplength=235,
                                  font=('Segoe UI', 8))
        self._status.pack(anchor='w')
        ttk.Button(right, text="Export → War Thunder",
                   command=self._export).pack(fill='x', pady=(4, 2))
        ttk.Button(right, text="Restore Game Default (Remove Mod)",
                   command=self._restore).pack(fill='x', pady=(0, 4))

    # ── Defaults ──────────────────────────────────────────────────────────────

    def _load_defaults(self):
        """Default green corner-bracket reticle centred at game origin (0, 0)."""
        self.elements = [
            VEllipse(   0,    0,  2.1,  2.1),   # centre dot
            VLine(-300, -150, -180, -150),        # top-left  horizontal
            VLine(-300, -150, -300,  -90),        # top-left  vertical
            VLine( 300, -150,  180, -150),        # top-right horizontal
            VLine( 300, -150,  300,  -90),        # top-right vertical
            VLine(-300,  150, -180,  150),        # bot-left  horizontal
            VLine(-300,  150, -300,   90),        # bot-left  vertical
            VLine( 300,  150,  180,  150),        # bot-right horizontal
            VLine( 300,  150,  300,   90),        # bot-right vertical
        ]
        self.color = (0, 255, 65)

    # ── Coordinate transforms ──────────────────────────────────────────────────

    def _g2s(self, gx, gy):
        return (CW/2 + (gx - self._vcx) * self._scale,
                CH/2 + (gy - self._vcy) * 2 * self._scale)

    def _s2g(self, sx, sy):
        return ((sx - CW/2) / self._scale       + self._vcx,
                (sy - CH/2) / (2*self._scale)   + self._vcy)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _redraw(self):
        self._cv.delete('all')
        self._draw_grid()
        r, g, b = self.color
        col = f'#{r:02x}{g:02x}{b:02x}'
        for i, elem in enumerate(self.elements):
            if i == self.selected:
                c, w = 'white', 2       # primary selection
            elif i in self._sel_set:
                c, w = '#ffcc44', 2     # multi-selection highlight
            else:
                c, w = col, 1
            self._draw_elem(elem, c, w)
        self._refresh_list()
        self._refresh_sq()

    def _draw_grid(self):
        gc = '#1e2a3a'
        gx0, gy0 = self._s2g(0,  0)
        gx1, gy1 = self._s2g(CW, CH)
        sx_step, sy_step = 100, 50
        x_lo = int(gx0 // sx_step) * sx_step - sx_step
        x_hi = int(gx1 // sx_step) * sx_step + sx_step
        for gx in range(x_lo, x_hi + 1, sx_step):
            px, _ = self._g2s(gx, 0)
            self._cv.create_line(px, 0, px, CH, fill=gc)
        y_lo = int(gy0 // sy_step) * sy_step - sy_step
        y_hi = int(gy1 // sy_step) * sy_step + sy_step
        for gy in range(y_lo, y_hi + 1, sy_step):
            _, py = self._g2s(0, gy)
            self._cv.create_line(0, py, CW, py, fill=gc)
        ax, ay = self._g2s(0, 0)
        if 0 <= ax <= CW: self._cv.create_line(ax, 0, ax, CH, fill='#cc3333', width=1)
        if 0 <= ay <= CH: self._cv.create_line(0, ay, CW, ay, fill='#cc3333', width=1)
        # dashed line at y=100 (where aim point sits in rockets-selected mode)
        _, aim_sy = self._g2s(0, 100)
        if 0 <= aim_sy <= CH:
            self._cv.create_line(0, aim_sy, CW, aim_sy, fill='#1a4a1a', dash=(4, 8))

    def _draw_elem(self, elem, color, width):
        r = 5
        if isinstance(elem, VLine):
            sx1, sy1 = self._g2s(elem.x1, elem.y1)
            sx2, sy2 = self._g2s(elem.x2, elem.y2)
            self._cv.create_line(sx1, sy1, sx2, sy2, fill=color, width=width)
            for px, py in [(sx1, sy1), (sx2, sy2)]:
                self._cv.create_oval(px-r, py-r, px+r, py+r,
                                      outline=color, fill='#0d1117', width=1)
        elif isinstance(elem, VEllipse):
            sx, sy = self._g2s(elem.cx, elem.cy)
            rx_s = elem.rx * self._scale
            ry_s = elem.ry * 2 * self._scale
            self._cv.create_oval(sx-rx_s, sy-ry_s, sx+rx_s, sy+ry_s,
                                  outline=color, fill='', width=width)
            self._cv.create_oval(sx-r, sy-r, sx+r, sy+r, fill=color, outline='')

    def _refresh_list(self):
        self._lb.delete(0, tk.END)
        for i, e in enumerate(self.elements):
            self._lb.insert(tk.END, e.label(i))
        self._lb.selection_clear(0, tk.END)
        for i in self._sel_set:
            if i < len(self.elements):
                self._lb.selection_set(i)
        if self.selected is not None and self.selected < len(self.elements):
            self._lb.selection_set(self.selected)
            self._lb.see(self.selected)

    def _refresh_sq(self):
        r, g, b = self.color
        lines = ',\n'.join('    ' + e.squirrel() for e in self.elements)
        code = (f"// Color({r},{g},{b},255)\n"
                f"function helicopterRocketSightMode(sightMode) {{\n"
                f"  return [\n{lines}\n  ]\n}}")
        self._sq.delete('1.0', tk.END)
        self._sq.insert('1.0', code)

    # ── Properties panel ──────────────────────────────────────────────────────

    def _refresh_props(self):
        for w in self._prop_frame.winfo_children():
            w.destroy()
        self._prop_vars.clear()
        multi = len(self._sel_set)
        elem  = self.elements[self.selected] if (
            self.selected is not None and self.selected < len(self.elements)) else None

        if elem is None and multi == 0:
            ttk.Label(self._prop_frame, text="No element selected.",
                      font=('Segoe UI', 8)).pack(anchor='w')
            return
        if multi > 1:
            ttk.Label(self._prop_frame,
                      text=f"{multi} elements selected.\nDrag any to move all.",
                      font=('Segoe UI', 8)).pack(anchor='w')
            return
        for name, val in elem.fields():
            row = ttk.Frame(self._prop_frame)
            row.pack(fill='x', pady=1)
            ttk.Label(row, text=f"{name}:", width=4,
                      font=('Courier New', 9)).pack(side=tk.LEFT)
            var = tk.StringVar(value=f"{val:.1f}")
            self._prop_vars[name] = var
            ent = ttk.Entry(row, textvariable=var, width=10, font=('Courier New', 9))
            ent.pack(side=tk.LEFT, fill='x', expand=True)
            ent.bind('<Return>',   self._apply_props)
            ent.bind('<FocusOut>', self._apply_props)
        ttk.Button(self._prop_frame, text="Apply [↵]",
                   command=self._apply_props).pack(anchor='w', pady=2)

    def _apply_props(self, _event=None):
        if self.selected is None or self.selected >= len(self.elements):
            return
        try:
            vals = {k: float(v.get()) for k, v in self._prop_vars.items()}
        except ValueError:
            return
        self.elements[self.selected].set_fields(vals)
        self._redraw()

    # ── Mouse events ──────────────────────────────────────────────────────────

    def _mb1(self, event):
        self._cv.focus_set()
        tool = self.tool.get()
        gx, gy = self._snap_coord(*self._s2g(event.x, event.y))

        if tool == 'select':
            ep = self._ep_hit(event.x, event.y)
            if ep:
                idx, which = ep
                self.selected = idx
                self._sel_set = {idx}
                self._drag = ('ep', gx, gy, idx, which)
                self._refresh_props()
            else:
                hit = self._hit(event.x, event.y)
                if hit is not None:
                    if hit not in self._sel_set:
                        # click on unselected element → replace selection
                        self._sel_set = {hit}
                        self.selected = hit
                        self._refresh_props()
                    # click on already-selected element → start multi-drag
                    self._drag = ('whole', gx, gy, hit)
                else:
                    # click on empty space → start rubber-band
                    self._sel_set.clear()
                    self.selected = None
                    self._refresh_props()
                    self._drag = ('rubber', event.x, event.y)
            self._redraw()

        elif tool == 'line':
            self._drag = ('new_line', gx, gy)

        elif tool == 'ellipse':
            self._drag = ('new_ell', gx, gy)

    def _mm(self, event):
        gx, gy = self._snap_coord(*self._s2g(event.x, event.y))
        self._coord_lbl.configure(text=f"game: ({gx:.1f}, {gy:.1f})")
        if not self._drag:
            return
        mode = self._drag[0]

        if mode == 'whole':
            _, pgx, pgy, _idx = self._drag
            dgx, dgy = gx - pgx, gy - pgy
            for i in (self._sel_set if self._sel_set else {_idx}):
                if i < len(self.elements):
                    self.elements[i].translate(dgx, dgy)
            self._drag = ('whole', gx, gy, _idx)
            self._redraw()

        elif mode == 'ep':
            _, _pgx, _pgy, idx, which = self._drag
            elem = self.elements[idx]
            if which == 'p1': elem.x1, elem.y1 = gx, gy
            else:              elem.x2, elem.y2 = gx, gy
            self._drag = ('ep', gx, gy, idx, which)
            self._refresh_props()
            self._redraw()

        elif mode == 'rubber':
            _, x0, y0 = self._drag
            self._redraw()   # clear previous rubber band
            self._cv.create_rectangle(x0, y0, event.x, event.y,
                                       outline='#4488ff', fill='', dash=(3, 3))

        elif mode in ('new_line', 'new_ell'):
            _, sgx, sgy = self._drag
            self._redraw()
            sx1, sy1 = self._g2s(sgx, sgy)
            r, g, b = self.color
            col = f'#{r:02x}{g:02x}{b:02x}'
            if mode == 'new_line':
                self._cv.create_line(sx1, sy1, event.x, event.y,
                                      fill=col, width=1, dash=(3, 3))
                for px, py in [(sx1, sy1), (event.x, event.y)]:
                    self._cv.create_oval(px-4, py-4, px+4, py+4,
                                          outline=col, fill='')
            else:
                rx = abs(event.x - sx1); ry = abs(event.y - sy1)
                self._cv.create_oval(sx1-rx, sy1-ry, sx1+rx, sy1+ry,
                                      outline=col, fill='', dash=(3, 3))
                self._cv.create_oval(sx1-3, sy1-3, sx1+3, sy1+3,
                                      fill=col, outline='')

    def _mr(self, event):
        if not self._drag:
            return
        mode = self._drag[0]
        gx, gy = self._snap_coord(*self._s2g(event.x, event.y))

        if mode == 'rubber':
            _, x0, y0 = self._drag
            hit = self._elements_in_rect(x0, y0, event.x, event.y)
            self._sel_set = hit
            if len(hit) == 1:
                self.selected = next(iter(hit))
            elif hit:
                self.selected = min(hit)
            else:
                self.selected = None
            self._refresh_props()

        elif mode == 'new_line':
            _, sgx, sgy = self._drag
            sx1, sy1 = self._g2s(sgx, sgy)
            if abs(event.x - sx1) > 3 or abs(event.y - sy1) > 3:
                self.elements.append(VLine(sgx, sgy, gx, gy))
                self.selected = len(self.elements) - 1
                self._sel_set = {self.selected}
                self._refresh_props()

        elif mode == 'new_ell':
            _, sgx, sgy = self._drag
            sx1, sy1 = self._g2s(sgx, sgy)
            rx_s = abs(event.x - sx1); ry_s = abs(event.y - sy1)
            if rx_s > 2 or ry_s > 2:
                self.elements.append(VEllipse(sgx, sgy,
                                               rx_s/self._scale,
                                               ry_s/(2*self._scale)))
                self.selected = len(self.elements) - 1
                self._sel_set = {self.selected}
                self._refresh_props()

        elif mode in ('whole', 'ep'):
            if self.selected is not None and self.selected < len(self.elements):
                self._refresh_props()

        self._drag = None
        self._redraw()

    def _cancel(self):
        self._drag = None
        self._sel_set.clear()
        self.selected = None
        self._refresh_props()
        self._redraw()

    # ── Hit testing ───────────────────────────────────────────────────────────

    def _ep_hit(self, sx, sy, r=8):
        for i in range(len(self.elements)-1, -1, -1):
            e = self.elements[i]
            if isinstance(e, VLine):
                for px, py, lbl in [(*self._g2s(e.x1, e.y1), 'p1'),
                                      (*self._g2s(e.x2, e.y2), 'p2')]:
                    if (sx-px)**2 + (sy-py)**2 < r*r:
                        return (i, lbl)
        return None

    def _hit(self, sx, sy, r=8):
        for i in range(len(self.elements)-1, -1, -1):
            e = self.elements[i]
            if isinstance(e, VLine):
                x1, y1 = self._g2s(e.x1, e.y1)
                x2, y2 = self._g2s(e.x2, e.y2)
                if _near_seg(sx, sy, x1, y1, x2, y2, r):
                    return i
            elif isinstance(e, VEllipse):
                cx, cy = self._g2s(e.cx, e.cy)
                if (sx-cx)**2 + (sy-cy)**2 < r*r:
                    return i
        return None

    def _elements_in_rect(self, sx0, sy0, sx1, sy1) -> set:
        lx, rx = min(sx0, sx1), max(sx0, sx1)
        ty, by = min(sy0, sy1), max(sy0, sy1)
        result = set()
        for i, elem in enumerate(self.elements):
            if isinstance(elem, VLine):
                p1x, p1y = self._g2s(elem.x1, elem.y1)
                p2x, p2y = self._g2s(elem.x2, elem.y2)
                if ((lx <= p1x <= rx and ty <= p1y <= by) or
                        (lx <= p2x <= rx and ty <= p2y <= by)):
                    result.add(i)
            elif isinstance(elem, VEllipse):
                cx, cy = self._g2s(elem.cx, elem.cy)
                if lx <= cx <= rx and ty <= cy <= by:
                    result.add(i)
        return result

    # ── Snap ──────────────────────────────────────────────────────────────────

    def _snap_coord(self, gx, gy):
        if not self._snap.get():
            return gx, gy
        try:
            s = float(self._snap_sz.get())
            if s <= 0: s = 10.0
        except ValueError:
            s = 10.0
        return round(gx/s)*s, round(gy/s)*s

    # ── Pan / Zoom ────────────────────────────────────────────────────────────

    def _pan_start(self, event):
        self._cv.focus_set()
        self._pan_origin = (event.x, event.y, self._vcx, self._vcy)
        self._cv.configure(cursor='fleur')

    def _pan_drag(self, event):
        if not self._pan_origin: return
        ox, oy, ovcx, ovcy = self._pan_origin
        self._vcx = ovcx - (event.x-ox) / self._scale
        self._vcy = ovcy - (event.y-oy) / (2*self._scale)
        self._redraw()

    def _pan_end(self, event):
        self._pan_origin = None
        self._cv.configure(cursor='crosshair')

    def _wheel(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        gx, gy = self._s2g(event.x, event.y)
        self._scale = max(0.08, min(6.0, self._scale * factor))
        self._vcx = gx - (event.x-CW/2) / self._scale
        self._vcy = gy - (event.y-CH/2) / (2*self._scale)
        self._redraw()

    def _reset_view(self):
        self._vcx = 0.0; self._vcy = 0.0; self._scale = 0.5
        self._redraw()

    # ── Element management ────────────────────────────────────────────────────

    def _delete_sel(self):
        targets = self._sel_set if self._sel_set else (
            {self.selected} if self.selected is not None else set())
        if not targets:
            return
        for i in sorted(targets, reverse=True):
            if i < len(self.elements):
                self.elements.pop(i)
        self.selected = None
        self._sel_set = set()
        self._refresh_props()
        self._redraw()

    def _clear_all(self):
        if messagebox.askyesno("Clear All", "Remove all elements?", parent=self.root):
            self.elements.clear()
            self.selected = None
            self._sel_set = set()
            self._refresh_props()
            self._redraw()

    def _reorder(self, delta):
        if self.selected is None: return
        new = self.selected + delta
        if 0 <= new < len(self.elements):
            self.elements[self.selected], self.elements[new] = \
                self.elements[new], self.elements[self.selected]
            self._sel_set = {new}
            self.selected = new
            self._redraw()

    def _lb_select(self, _event):
        sel = self._lb.curselection()
        if sel:
            self._sel_set = set(sel)
            self.selected = sel[-1]
            self._refresh_props()
            self._redraw()

    # ── Profiles ──────────────────────────────────────────────────────────────

    def _refresh_profile_list(self):
        names = profile_list()
        self._prof_cb['values'] = names

    def _prof_save(self):
        name = self._profile_var.get().strip()
        if not name:
            name = simpledialog.askstring("Save Profile", "Profile name:",
                                           parent=self.root)
        if not name:
            return
        # Sanitize filename
        name = re.sub(r'[\\/:*?"<>|]', '_', name)
        profile_save(name, self.elements, self.color)
        self._refresh_profile_list()
        self._profile_var.set(name)
        self._set_status(f"Saved profile: {name}")

    def _prof_load(self):
        name = self._profile_var.get().strip()
        if not name:
            messagebox.showwarning("No Profile", "Select or type a profile name.",
                                    parent=self.root)
            return
        try:
            elems, color = profile_load(name)
        except FileNotFoundError:
            messagebox.showerror("Not found", f"Profile '{name}' not found.",
                                  parent=self.root)
            return
        self.elements = elems
        self.color = color
        self._color_btn.configure(bg='#%02x%02x%02x' % color)
        self.selected = None
        self._sel_set = set()
        self._refresh_props()
        self._redraw()
        self._set_status(f"Loaded profile: {name}")

    def _prof_delete(self):
        name = self._profile_var.get().strip()
        if not name:
            return
        if messagebox.askyesno("Delete Profile", f"Delete profile '{name}'?",
                                parent=self.root):
            profile_delete(name)
            self._refresh_profile_list()
            self._profile_var.set('')
            self._set_status(f"Deleted profile: {name}")

    # ── Color ─────────────────────────────────────────────────────────────────

    def _pick_color(self):
        init = '#%02x%02x%02x' % self.color
        result = colorchooser.askcolor(color=init, title="Reticle Color",
                                        parent=self.root)
        if result[0]:
            self.color = tuple(int(c) for c in result[0])
            self._color_btn.configure(bg='#%02x%02x%02x' % self.color)
            self._redraw()

    # ── WT directory / version ────────────────────────────────────────────────

    def _browse_dir(self):
        d = filedialog.askdirectory(title="Select War Thunder Directory",
                                     parent=self.root)
        if d:
            self.wt_dir.set(d)

    def _read_ver(self):
        p = os.path.join(self.wt_dir.get(), 'content', 'pkg_main.ver')
        try:
            with open(p, errors='replace') as f:
                self._ver_lbl.configure(text=f"Version: {f.read().strip()[:90]}")
        except Exception as e:
            self._ver_lbl.configure(text=f"Version error: {e}")

    # ── Export / Restore ──────────────────────────────────────────────────────

    def _set_status(self, msg):
        self._status.configure(text=msg)
        self.root.update_idletasks()

    def _content_dir(self):
        return os.path.join(self.wt_dir.get().strip(), 'content')

    def _vromfs_path(self):
        return os.path.join(self._content_dir(), 'pkg_user', 'base.vromfs.bin')

    def _vanilla_vromfs(self):
        """Bundled vanilla base.vromfs.bin shipped alongside the editor."""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'base_vanilla.vromfs.bin')

    def _export(self):
        if not self.elements:
            messagebox.showwarning("Empty", "No elements to export.", parent=self.root)
            return

        content_dir  = self._content_dir()
        pkg_user_dir = os.path.join(content_dir, 'pkg_user')
        vp           = os.path.join(pkg_user_dir, 'base.vromfs.bin')
        is_fresh     = not os.path.exists(vp)

        try:
            nut_key = 'reactivegui/airHudElems.nut'

            if is_fresh:
                # pkg_user doesn't exist yet — get the nut from the live game first,
                # then fall back to the bundled vanilla copy
                self._set_status("First-time install — reading nut from game files…")
                live_nut = find_nut_in_game(self.wt_dir.get().strip())
                if live_nut:
                    # Build a minimal files dict: just the nut (SVG slots stay empty,
                    # game falls back to pkg_main for those)
                    files = {nut_key: live_nut,
                             'reactivegui/airhudelems.nut': live_nut}
                    self._set_status("Got fresh nut from game — patching…")
                else:
                    # VRFx decode failed or nut not found — fall back to bundled copy
                    self._set_status("Live read failed — falling back to bundled vanilla…")
                    src = self._vanilla_vromfs()
                    if not os.path.exists(src):
                        messagebox.showerror(
                            "No base file found",
                            "Could not read the nut from the game's VRFx files AND\n"
                            "base_vanilla.vromfs.bin is missing from the editor folder.\n\n"
                            "At least one of these must be present to do a first-time install.",
                            parent=self.root)
                        return
                    files = vromfs_unpack(src)
            else:
                src = vp
                self._set_status("Unpacking vromfs…")
                files = vromfs_unpack(src)

            if nut_key not in files:
                raise ValueError("airHudElems.nut not found — wrong directory?")

            self._set_status("Patching nut script…")
            nut = files[nut_key].decode('utf-8', errors='replace')
            ri, gi, bi = self.color
            nut = patch_nut(nut, self.elements, ri, gi, bi)
            nut_bytes = nut.encode('utf-8')
            files['reactivegui/airHudElems.nut'] = nut_bytes
            files['reactivegui/airhudelems.nut'] = nut_bytes

            self._set_status("Repacking vromfs…")
            packed = vromfs_repack(files)

            if is_fresh:
                # Create the pkg_user overlay directory
                self._set_status("Creating pkg_user directory…")
                os.makedirs(pkg_user_dir, exist_ok=True)
                # Seed .ver and .rq2 from pkg_main equivalents so the launcher
                # treats pkg_user as a valid same-version package
                for ext in ('.ver', '.rq2'):
                    src_meta = os.path.join(content_dir, f'pkg_main{ext}')
                    dst_meta = os.path.join(content_dir, f'pkg_user{ext}')
                    if os.path.exists(src_meta) and not os.path.exists(dst_meta):
                        shutil.copy2(src_meta, dst_meta)
            else:
                bak = vp + '.bak'
                if not os.path.exists(bak):
                    self._set_status("Creating backup…")
                    shutil.copy2(vp, bak)

            self._set_status("Writing…")
            with open(vp, 'wb') as f:
                f.write(packed)

            self._set_status(f"Done!  {len(packed):,} bytes written.")
            action = "installed" if is_fresh else "updated"
            messagebox.showinfo(
                "Exported",
                f"Reticle {action} in War Thunder.\n\n"
                f"Size: {len(packed):,} bytes\n\n"
                f"Launch War Thunder to see your reticle.",
                parent=self.root)

        except Exception as exc:
            self._set_status(f"Error: {exc}")
            messagebox.showerror("Export failed", str(exc), parent=self.root)
            import traceback; traceback.print_exc()

    def _restore(self):
        """Delete the entire pkg_user overlay — vanilla game has no such folder."""
        wt          = self.wt_dir.get().strip()
        content_dir = os.path.join(wt, 'content')
        pkg_dir     = os.path.join(content_dir, 'pkg_user')

        to_delete: list[tuple[str, str]] = []   # (kind, abs_path)

        # Every file inside content/pkg_user/ (our vromfs + any .bak)
        if os.path.isdir(pkg_dir):
            for name in os.listdir(pkg_dir):
                to_delete.append(('file', os.path.join(pkg_dir, name)))
            to_delete.append(('dir', pkg_dir))

        # Version / request files that live alongside the folder
        for fname in ('pkg_user.ver', 'pkg_user.rq2'):
            p = os.path.join(content_dir, fname)
            if os.path.exists(p):
                to_delete.append(('file', p))

        if not to_delete:
            messagebox.showinfo(
                "Nothing to remove",
                "No mod files found in the game directory.\n"
                "The game is already at factory state.",
                parent=self.root)
            return

        file_list = '\n'.join('  ' + os.path.relpath(p, wt)
                              for kind, p in to_delete)
        if not messagebox.askyesno(
                "Remove Mod Completely",
                f"The following mod files will be permanently deleted:\n\n"
                f"{file_list}\n\n"
                f"War Thunder will fall back to its built-in defaults.\n\n"
                f"Continue?", parent=self.root):
            return

        errors = []
        for kind, p in to_delete:
            try:
                if kind == 'dir':
                    if os.path.isdir(p) and not os.listdir(p):
                        os.rmdir(p)
                else:
                    if os.path.exists(p):
                        os.remove(p)
            except Exception as exc:
                errors.append(f"{os.path.relpath(p, wt)}: {exc}")

        if errors:
            self._set_status(f"Partial removal — {len(errors)} error(s).")
            messagebox.showerror(
                "Partial Removal",
                "Some files could not be deleted:\n\n" + '\n'.join(errors),
                parent=self.root)
        else:
            self._set_status("Mod removed. Game directory is at factory state.")
            messagebox.showinfo(
                "Mod Removed",
                "All mod files deleted.\n"
                "Game directory is back to factory state.\n\n"
                "Launch War Thunder normally — it will use its built-in reticle.\n"
                "You can export a new custom reticle at any time.",
                parent=self.root)


# ── Geometry ──────────────────────────────────────────────────────────────────

def _near_seg(px, py, x1, y1, x2, y2, r):
    dx, dy = x2-x1, y2-y1
    L2 = dx*dx + dy*dy
    if L2 < 1: return (px-x1)**2+(py-y1)**2 < r*r
    t = max(0.0, min(1.0, ((px-x1)*dx+(py-y1)*dy)/L2))
    nx, ny = x1+t*dx, y1+t*dy
    return (px-nx)**2+(py-ny)**2 < r*r


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not _ensure_zstandard():
        sys.exit(1)
    root = tk.Tk()
    root.resizable(True, True)
    Editor(root)
    root.mainloop()


if __name__ == '__main__':
    main()
