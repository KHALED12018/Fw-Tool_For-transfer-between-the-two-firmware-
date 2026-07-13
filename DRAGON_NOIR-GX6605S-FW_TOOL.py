import os
import sys
import ctypes
import shutil
import atexit
import hashlib
import json
import math
import struct
import zlib
import subprocess
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
import collections
import tempfile

from tkinter import (
    END, WORD, W, StringVar, TclError, Button, Entry, Frame, Label, 
    Scrollbar, Text, Tk, Toplevel, filedialog, messagebox
)

DN_MAGIC = b'\xaa\xbc\xde\xfa'
CRAMFS_MAGIC = b'E=\xcd('
TABLE_SIZE = 512
ENTRY_SIZE = 24
T_COUNT_OFF = 4
T_ENTRIES_OFF = 5
T_CRC32_BASE = 413
T_WP_OFF = 505
T_CRCEN_OFF = 506
T_VERSION_OFF = 507
T_TABLECRC_OFF = 508
HYBRID_KERN_OFF = 262400
METADATA_SIZE = 256
METADATA_VERSION = 1
METADATA_F0 = 134656
METADATA_F2 = 131072
MULTISW_MAGIC = b'mult_sw\x00'

CLR_MAIN_BG = "#080b10"
CLR_PANEL_BG = "#101622"
CLR_ACCENT_BORDER = "#1f293d"
CLR_ENGRAVED_BG = "#05070a"
CLR_TEXT_PRIMARY = "#00ffcc"
CLR_TEXT_MUTED = "#557090"
CLR_BTN_NORMAL = "#121a2e"
CLR_BTN_ACTIVE = "#00e5b8"
CLR_BORDER_LIGHT = "#3b82f6"

FONT_MAIN = ("Consolas", 9, "bold")
FONT_HDR = ("Consolas", 14, "bold")

def get_res_path(rel_path: str) -> str:
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        cand = os.path.join(meipass, rel_path)
        if os.path.exists(cand):
            return cand
    try:
        exe_dir = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
        cand = os.path.join(exe_dir, rel_path)
        if os.path.exists(cand):
            return cand
    except Exception:
        pass
    cand = os.path.join(os.getcwd(), rel_path)
    if os.path.exists(cand):
        return cand
    return os.path.join(meipass or os.getcwd(), rel_path)

def cleanup_tmp():
    try:
        tmp_dir = getattr(sys, '_MEIPASS', None)
        if tmp_dir and os.path.isdir(tmp_dir):
            p_str = str(os.getpid())
            if p_str in tmp_dir or 'onefile' in tmp_dir.lower() or 'nuitka' in tmp_dir.lower():
                shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

atexit.register(cleanup_tmp)

def apply_win_ico(win: Any, ico_name: str = "fan.ico") -> bool:
    try:
        ico_path = get_res_path(ico_name)
        if not os.path.exists(ico_path):
            return False
        if sys.platform == "win32":
            try:
                app_id = "dragon.noir.gx6605s." + os.path.splitext(os.path.basename(ico_name))[0]
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
            except Exception:
                pass
            try:
                LR_LOADFROMFILE = 0x00000010
                IMAGE_ICON = 1
                h_big = ctypes.windll.user32.LoadImageW(0, ico_path, IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
                h_sm = ctypes.windll.user32.LoadImageW(0, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                hwnd = None
                if hasattr(win, "winfo_id"):
                    try:
                        hwnd = win.winfo_id()
                    except Exception:
                        pass
                if hwnd and (h_big or h_sm):
                    WM_SETICON = 0x0080
                    ICON_SMALL = 0
                    ICON_BIG = 1
                    if h_big:
                        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
                    if h_sm:
                        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_sm)
            except Exception:
                pass
        try:
            abs_ico = os.path.abspath(ico_path)
            win.after(10, lambda p=abs_ico: win.iconbitmap(p))
            return True
        except Exception:
            pass
        return False
    except Exception:
        return False

def make_meta(folder_name: str, base_offset: int, table_flash_bytes: bytes) -> bytes:
    name = folder_name.encode('utf-8') if isinstance(folder_name, str) else folder_name
    nlen = len(name)
    meta = bytearray(b'\xff' * METADATA_SIZE)
    struct.pack_into('<I', meta, 0, 0)
    struct.pack_into('<I', meta, 4, 16 + nlen - 1)
    struct.pack_into('<I', meta, 8, METADATA_VERSION)
    struct.pack_into('<I', meta, 12, nlen)
    meta[16:16 + nlen] = name
    a = 16 + nlen
    struct.pack_into('<I', meta, a, METADATA_F0)
    struct.pack_into('<I', meta, a + 4, base_offset)
    struct.pack_into('<I', meta, a + 8, METADATA_F2)
    meta[a + 12] = 0
    tbl_off = a + 13
    avail = METADATA_SIZE - tbl_off
    meta[tbl_off:tbl_off + avail] = table_flash_bytes[:avail]
    return bytes(meta)

def write_idx_file(out_dir: str, fw_path: str, fw: bytes, entries: list, base_offset: int) -> None:
    parts = []
    if base_offset > 0:
        parts.append({'name': 'HEADER', 'file_off': 0, 'total': base_offset})
    for e in entries:
        parts.append({
            'name': e['name'],
            'file_off': int(e['start'] + base_offset),
            'total': int(e['total']),
        })
    crc_pol: dict = {}
    for e in entries:
        if e.get('name') == 'TABLE':
            continue
        fo = int(e['start']) + base_offset
        tot = int(e['total'])
        if tot > 0 and fo >= 0 and fo + tot <= len(fw):
            full_data = fw[fo : fo + tot]
        else:
            full_data = e.get('data', b'')
        p = inf_crc_pol(e, full_data)
        if p:
            crc_pol[e['name']] = p
    idx = {
        'source_path': str(Path(fw_path).resolve()),
        'fw_size': len(fw),
        'base_offset': base_offset,
        'partitions': parts,
        'table_crc_policy': crc_pol,
    }
    try:
        with open(os.path.join(out_dir, '_repack_index.json'), 'w', encoding='utf-8') as f:
            json.dump(idx, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

def read_idx_file(folder: str) -> Optional[dict]:
    p = os.path.join(folder, '_repack_index.json')
    if not os.path.isfile(p):
        return None
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None

def apply_idx_src(folder: str, layout: dict, parts_data: dict) -> None:
    idx = read_idx_file(folder)
    if not idx:
        return
    src = idx.get('source_path')
    if not src or not os.path.isfile(src):
        return
    try:
        fw = Path(src).read_bytes()
    except OSError:
        return
    for part in idx.get('partitions', []):
        nm = part.get('name')
        if nm not in layout:
            continue
        fo = int(part.get('file_off', 0))
        tot = int(part.get('total', 0))
        if tot <= 0 or tot != layout[nm]['total']:
            continue
        cur = parts_data.get(nm, b'')
        if len(cur) == tot:
            continue
        if fo < 0:
            continue
        chunk = fw[fo:fo + tot]
        if len(chunk) < tot:
            chunk = chunk + b'\xff' * (tot - len(chunk))
        parts_data[nm] = chunk

def parse_meta_block(data_bin: bytes) -> tuple[int, bytes]:
    if len(data_bin) < METADATA_SIZE:
        return (0, data_bin)
    meta = data_bin[-METADATA_SIZE:]
    if struct.unpack('<I', meta[0:4])[0] != 0:
        return (0, data_bin)
    ver = struct.unpack('<I', meta[8:12])[0]
    if ver not in (1, 2):
        return (0, data_bin)
    nlen = struct.unpack('<I', meta[12:16])[0]
    if 0 < nlen < 192:
        tbl_off = 16 + nlen + 13
        if tbl_off + 4 > METADATA_SIZE:
            return (0, data_bin)
        if meta[tbl_off:tbl_off + 4] != DN_MAGIC:
            return (0, data_bin)
        base_offset = struct.unpack('<I', meta[16 + nlen + 4:16 + nlen + 8])[0]
        return (base_offset, data_bin[:-METADATA_SIZE])
    return (0, data_bin)

def get_fs_type(data: bytes) -> str:
    return 'CRAMFS' if data[:4] == CRAMFS_MAGIC else 'RAW'

def get_fs_from_flags(flags: int, data: bytes = b'') -> str:
    fs_byte = flags >> 24 & 255
    if fs_byte in {2: 'CRAMFS', 127: 'MINIFS'}:
        return {2: 'CRAMFS', 127: 'MINIFS'}[fs_byte]
    return get_fs_type(data)

def calc_crc32(data: bytes) -> int:
    return zlib.crc32(data) & 4294967295

def inf_crc_pol(e: dict, data: bytes) -> Optional[dict]:
    st = int(e.get('stored_crc', 0) or 0)
    if st == 0:
        return None
    nm = e['name']
    if nm == 'TABLE':
        return None
    if nm == 'BOOT':
        return {'mode': 'boot'}
    tot = int(e['total'])
    if tot <= 0:
        return None
    body = data if len(data) >= tot else (data + b'\xff' * (tot - len(data)))
    body = body[:tot]
    main = int(e.get('main', 0) or 0)
    if calc_crc32(body) == st:
        return {'mode': 'total'}
    if main > 0 and calc_crc32(body[:main]) == st:
        return {'mode': 'main'}
    for n in (main, tot - 4, tot - 256, tot - 512):
        if 0 < n <= len(body) and calc_crc32(body[:n]) == st:
            return {'mode': 'length', 'length': n}
    c = 0
    for n in range(len(body)):
        c = zlib.crc32(body[n : n + 1], c) & 0xFFFFFFFF
        if c == st:
            return {'mode': 'length', 'length': n + 1}
    return {'mode': 'total'}

def get_crc_range_by_pol(e: dict, part_data: bytes, pol: dict) -> tuple[int, int]:
    mode = pol.get('mode')
    if mode == 'boot':
        if len(part_data) >= e['total']:
            return (0, min(int(e['main']), len(part_data)))
        return (0, len(part_data))
    if mode == 'total':
        return (0, min(int(e['total']), len(part_data)))
    if mode == 'main':
        m = int(e.get('main', 0) or 0)
        if m <= 0:
            return (0, min(int(e['total']), len(part_data)))
        return (0, min(m, len(part_data)))
    if mode == 'length':
        ln = int(pol.get('length', 0))
        return (0, min(ln, len(part_data)))
    return (0, min(int(e['total']), len(part_data)))

def get_crc_range_by_entry(e: dict, part_data: bytes, new_main: int) -> tuple[int, int]:
    if e.get('stored_crc', 0) == 0:
        return (0, 0)
    nm = e['name']
    if nm == 'BOOT':
        m = e.get('main', 0)
        return (0, min(m, len(part_data))) if m > 0 else (0, len(part_data))
    if new_main > 0:
        return (0, min(new_main, len(part_data)))
    return (0, min(int(e['total']), len(part_data)))

def get_tbl_slot_crc(e: dict, part_data: bytes, new_main: int, crc_policy: Optional[dict] = None) -> int:
    if e.get('stored_crc', 0) == 0:
        return 0
    nm = e['name']
    if crc_policy and nm in crc_policy and crc_policy[nm]:
        start, ln = get_crc_range_by_pol(e, part_data, crc_policy[nm])
        if ln > 0:
            return calc_crc32(part_data[start : start + ln])
    start, ln = get_crc_range_by_entry(e, part_data, new_main)
    if ln <= 0:
        return e['stored_crc']
    return calc_crc32(part_data[start : start + ln])

def parse_tbl_data(tb: bytes) -> tuple[int, list, bool, bool, int, int]:
    if len(tb) < TABLE_SIZE:
        raise ValueError(f'TABLE block too small ({len(tb)} B)')
    if tb[:4] != DN_MAGIC:
        raise ValueError('Invalid TABLE magic')
    count = tb[T_COUNT_OFF]
    if count == 0 or count > 16:
        raise ValueError(f'Invalid partition count: {count}')
    tcrc_stored = struct.unpack('>I', tb[T_TABLECRC_OFF:T_TABLECRC_OFF + 4])[0]
    tcrc_calc = calc_crc32(tb[:T_TABLECRC_OFF])
    if tcrc_stored != tcrc_calc:
        raise ValueError('TABLE CRC mismatch')
    wp = bool(tb[T_WP_OFF])
    crcen = bool(tb[T_CRCEN_OFF])
    version = tb[T_VERSION_OFF]
    entries = []
    for i in range(count):
        off = T_ENTRIES_OFF + i * ENTRY_SIZE
        raw = tb[off:off + ENTRY_SIZE]
        name = raw[0:8].rstrip(b'\x00').decode('ascii', 'replace')
        if not name:
            break
        total = struct.unpack('>I', raw[8:12])[0]
        main = struct.unpack('>I', raw[12:16])[0]
        start = struct.unpack('>I', raw[16:20])[0]
        flags = struct.unpack('>I', raw[20:24])[0]
        crf = flags >> 16 & 255
        scrc = struct.unpack('>I', tb[T_CRC32_BASE + i * 4:T_CRC32_BASE + i * 4 + 4])[0]
        entries.append({'idx': i, 'name': name, 'total': total, 'main': main, 'start': start, 'flags': flags, 'fs': '', 'stored_crc': scrc, 'crc_en': bool(crf & 128), 'crc_flag': crf, 'data': b''})
    return (count, entries, wp, crcen, version, tcrc_stored)

def patch_tbl_root_crc(orig_tb: bytes, entries: list, new_root_data: bytes, new_mains: dict, crc_policy: Optional[dict]) -> bytes:
    tb = bytearray(orig_tb[:TABLE_SIZE])
    if len(tb) < TABLE_SIZE:
        tb += b'\xff' * (TABLE_SIZE - len(tb))
    root_idx = None
    root_e = None
    for i, e in enumerate(entries):
        if e['name'] == 'ROOT':
            root_idx = i
            root_e = e
            break
    if root_idx is None or root_e is None:
        return bytes(tb)
    if int(root_e.get('stored_crc', 0) or 0) == 0:
        return bytes(tb)
    new_main = new_mains.get('ROOT', root_e.get('main', 0))
    new_crc = get_tbl_slot_crc(root_e, new_root_data, new_main, crc_policy)
    struct.pack_into('>I', tb, T_CRC32_BASE + root_idx * 4, new_crc)
    struct.pack_into('>I', tb, T_TABLECRC_OFF, calc_crc32(bytes(tb[:T_TABLECRC_OFF])))
    return bytes(tb)

def patch_tbl_full(orig_tb: bytes, entries: list, new_mains: dict, parts_data: dict, new_totals: dict = None, new_starts: dict = None, crc_policy: Optional[dict] = None) -> bytes:
    if new_totals is None: new_totals = {}
    if new_starts is None: new_starts = {}
    tb = bytearray(orig_tb[:TABLE_SIZE])
    if len(tb) < TABLE_SIZE:
        tb += b'\xff' * (TABLE_SIZE - len(tb))
    for i, e in enumerate(entries):
        nm = e['name']
        base_off = T_ENTRIES_OFF + i * ENTRY_SIZE
        if nm in new_totals:
            struct.pack_into('>I', tb, base_off + 8, new_totals[nm])
        if nm in new_starts:
            struct.pack_into('>I', tb, base_off + 16, new_starts[nm])
        if nm in new_mains:
            struct.pack_into('>I', tb, base_off + 12, new_mains[nm])
    for i, e in enumerate(entries):
        nm = e['name']
        slot_off = T_CRC32_BASE + i * 4
        d = parts_data.get(nm, b'')
        orig_main = e['main']
        new_main = new_mains.get(nm, orig_main)
        new_crc = get_tbl_slot_crc(e, d, new_main, crc_policy)
        struct.pack_into('>I', tb, slot_off, new_crc)
    struct.pack_into('>I', tb, T_TABLECRC_OFF, calc_crc32(bytes(tb[:T_TABLECRC_OFF])))
    return bytes(tb)

def locate_tbl_in_fw(fw: bytes) -> tuple[int, int, list, bool, bool, int, int, int]:
    pos = 0
    while True:
        p = fw.find(DN_MAGIC, pos)
        if p == -1:
            raise ValueError('No TABLE magic found')
        if p + TABLE_SIZE <= len(fw) and 1 <= fw[p + T_COUNT_OFF] <= 16:
            try:
                count, entries, wp, crcen, ver, tcrc = parse_tbl_data(fw[p:p + TABLE_SIZE])
                tbl_flash_start = 0
                for e in entries:
                    if e['name'] == 'TABLE':
                        tbl_flash_start = e['start']
                        break
                base_offset = p - tbl_flash_start
                for e in entries:
                    file_off = e['start'] + base_offset
                    if e['total'] > 0 and file_off < len(fw):
                        available = min(e['total'], len(fw) - file_off)
                        e['data'] = fw[file_off:file_off + available]
                    e['fs'] = get_fs_from_flags(e.get('flags', 0), e['data'])
                return (p, count, entries, wp, crcen, ver, tcrc, base_offset)
            except ValueError:
                pass
        pos = p + 1

def collect_cramfs_blocks(fw: bytes) -> list:
    res, pos = [], 0
    while True:
        p = fw.find(b'E=\xcd(', pos)
        if p == -1:
            break
        if p + 8 <= len(fw):
            sz = struct.unpack('<I', fw[p + 4:p + 8])[0]
            if 4096 < sz < len(fw) - p:
                res.append((p, sz))
        pos = p + 1
    return res

def scan_nonff_start(fw: bytes, after: int) -> Optional[int]:
    off = after + 255 & -256
    while off < len(fw):
        if not all(b == 255 for b in fw[off:off + 256]):
            return off
        off += 256
    return None

def parse_fallback_cramfs(fw: bytes) -> list:
    def make_entry(idx, name, start, total):
        d = fw[start:start + total]
        return {'idx': idx, 'name': name, 'total': total, 'main': total, 'start': start, 'flags': 0, 'fs': get_fs_type(d), 'stored_crc': 0, 'data': d}
    if fw[256:260] == b'E=\xcd(':
        lo = 256
        ls = struct.unpack('<I', fw[lo + 4:lo + 8])[0]
        le = lo + ls
        ko = scan_nonff_start(fw, le)
        if ko is None:
            raise ValueError('Kernel not found')
        rl = [(p, s) for p, s in collect_cramfs_blocks(fw) if p > le]
        if not rl:
            raise ValueError('Root not found')
        ro, rs = rl[0]
        do = ro + rs
        de = len(fw)
        return [make_entry(0, 'HEADER', 0, lo), make_entry(1, 'LOGO', lo, ls), make_entry(2, 'KERNEL', ko, ro - ko), make_entry(3, 'ROOT', ro, rs), make_entry(4, 'DATA', do, de - do)]
    if len(fw) >= 256:
        freq = collections.Counter(fw[:256])
        entropy = -sum((c / 256 * math.log2(c / 256) for c in freq.values()))
        if entropy > 7.0:
            c_all = collect_cramfs_blocks(fw)
            if c_all:
                ro, rs = c_all[0]
                do = ro + rs
                return [make_entry(0, 'KERNEL', 0, ro), make_entry(1, 'ROOT', ro, rs), make_entry(2, 'DATA', do, len(fw) - do)]
    raise ValueError('No valid firmware structure identified')

def parse_multisw_slots(fw: bytes) -> list:
    slot_count = struct.unpack_from('<H', fw, 12)[0]
    if slot_count == 0 or slot_count > 16:
        raise ValueError('Invalid multisw layout')
    F_SIZE = 3276800
    slots = []
    for i in range(slot_count):
        stored = struct.unpack_from('<I', fw, 272 + i * 4)[0]
        base = stored + 256
        table_file = base + 97792
        if table_file + TABLE_SIZE > len(fw):
            continue
        try:
            count, entries, wp, crcen, ver, tcrc = parse_tbl_data(fw[table_file:table_file + TABLE_SIZE])
            for e in entries:
                file_off = base + e['start']
                if e['total'] > 0 and file_off < len(fw):
                    available = min(e['total'], len(fw) - file_off)
                    e['data'] = fw[file_off:file_off + available]
                e['fs'] = get_fs_from_flags(e.get('flags', 0), e['data'])
            slots.append({'idx': i, 'base': base, 'flash_size': F_SIZE, 'table_off': table_file, 'entries': entries, 'wp': wp, 'crcen': crcen, 'ver': ver, 'tcrc': tcrc})
        except ValueError:
            continue
    if not slots:
        raise ValueError('No slots found')
    return slots

def detect_fw_mode(fw: bytes) -> tuple:
    if fw[4:12] == MULTISW_MAGIC:
        try:
            slots = parse_multisw_slots(fw)
            s0 = slots[0]
            return ('MULTISW', s0['table_off'], len(s0['entries']), s0['entries'], s0['wp'], s0['crcen'], s0['ver'], s0['tcrc'], len(slots))
        except ValueError:
            pass
    try:
        tbl_off, count, entries, wp, crcen, ver, tcrc, base_offset = locate_tbl_in_fw(fw)
        return ('TABLE', tbl_off, count, entries, wp, crcen, ver, tcrc, base_offset)
    except ValueError:
        pass
    entries = parse_fallback_cramfs(fw)
    return ('CRAMFS', None, len(entries), entries, False, False, 0, None, 0)

def extract_fw_partitions(fw_path: str, out_dir: str) -> tuple:
    with open(fw_path, 'rb') as f:
        fw = f.read()
    mode, tbl_off, count, entries, wp, crcen, ver, tcrc, base_offset = detect_fw_mode(fw)
    os.makedirs(out_dir, exist_ok=True)
    if mode == 'MULTISW':
        slots = parse_multisw_slots(fw)
        F_SIZE = 3276800
        with open(os.path.join(out_dir, '_multisw_header.bin'), 'wb') as f:
            f.write(fw[:768])
        for i in range(len(slots) - 1):
            gap_start = slots[i]['base'] + F_SIZE
            gap_end = slots[i + 1]['base']
            gap_data = fw[gap_start:gap_end]
            with open(os.path.join(out_dir, f'_multisw_gap_{i + 1}.bin'), 'wb') as f:
                f.write(gap_data)
        meta = struct.pack('<I', len(slots))
        for s in slots:
            meta += struct.pack('<II', s['base'], F_SIZE)
        with open(os.path.join(out_dir, '_multisw_meta.bin'), 'wb') as f:
            f.write(meta)
        for s in slots:
            sw_dir = os.path.join(out_dir, f"SW{s['idx'] + 1}")
            os.makedirs(sw_dir, exist_ok=True)
            for e in s['entries']:
                with open(os.path.join(sw_dir, f"{e['name']}.bin"), 'wb') as f:
                    f.write(e['data'])
        s0 = slots[0]
        return (mode, s0['table_off'], len(s0['entries']), s0['entries'], s0['wp'], s0['crcen'], s0['ver'], s0['tcrc'], len(slots))
    if mode == 'TABLE':
        table_flash_bytes = fw[tbl_off:tbl_off + TABLE_SIZE]
        folder_name = os.path.splitext(os.path.basename(fw_path))[0] + '_extracted'
        if base_offset > 0:
            with open(os.path.join(out_dir, "HEADER.bin"), 'wb') as f:
                f.write(fw[:base_offset])
        for e in entries:
            if e['name'] == 'TABLE':
                data = table_flash_bytes
            elif e['name'] == 'DATA' and base_offset > 0:
                data = e['data'] + make_meta(folder_name, base_offset, table_flash_bytes)
            else:
                data = e['data']
            with open(os.path.join(out_dir, f"{e['name']}.bin"), 'wb') as f:
                f.write(data)
        write_idx_file(out_dir, fw_path, fw, entries, base_offset)
        return (mode, tbl_off, count, entries, wp, crcen, ver, tcrc, len(fw))
    for e in entries:
        with open(os.path.join(out_dir, f"{e['name']}.bin"), 'wb') as f:
            f.write(e['data'])
    return (mode, tbl_off, count, entries, wp, crcen, ver, tcrc, len(fw))

def parse_dir_layout(folder: str) -> tuple:
    idx = read_idx_file(folder)
    if idx and 'partitions' in idx:
        layout = {}
        for p in idx['partitions']:
            nm = p['name']
            layout[nm] = {'offset': p['file_off'], 'total': p['total'], 'main': p['total'], 'flags': 0}
        tbl_off = layout['TABLE']['offset'] if 'TABLE' in layout else None
        fw_size = idx.get('fw_size', max((v['offset'] + v['total'] for v in layout.values())))
        tbl_path = os.path.join(folder, 'TABLE.bin')
        wp, crcen, ver, tcrc = False, False, 0, None
        tbl_entries = []
        if os.path.exists(tbl_path):
            try:
                with open(tbl_path, 'rb') as f:
                    tbl_data = f.read()
                _, tbl_entries, wp, crcen, ver, tcrc = parse_tbl_data(tbl_data)
            except Exception:
                pass
        return ('TABLE' if tbl_entries else 'CRAMFS', layout, tbl_entries, tbl_off, fw_size, wp, crcen, ver, tcrc)
    tbl_path = os.path.join(folder, 'TABLE.bin')
    if os.path.exists(tbl_path):
        with open(tbl_path, 'rb') as f:
            tbl = f.read()
        count, entries, wp, crcen, ver, tcrc = parse_tbl_data(tbl)
        layout = {}
        for e in entries:
            layout[e['name']] = {'offset': e['start'], 'total': e['total'], 'main': e['main'], 'flags': e['flags']}
        h_path = os.path.join(folder, 'HEADER.bin')
        if os.path.exists(h_path):
            h_size = os.path.getsize(h_path)
            layout['HEADER'] = {'offset': 0, 'total': h_size, 'main': h_size, 'flags': 0}
        tbl_off = layout['TABLE']['offset'] if 'TABLE' in layout else None
        fw_size = max((v['offset'] + v['total'] for v in layout.values()))
        return ('TABLE', layout, entries, tbl_off, fw_size, wp, crcen, ver, tcrc)
    parts_order = ['HEADER', 'LOGO', 'KERNEL', 'ROOT', 'DATA']
    parts_present = [nm for nm in parts_order if os.path.exists(os.path.join(folder, f'{nm}.bin'))]
    if not parts_present:
        raise FileNotFoundError('No files found')
    sizes = {nm: os.path.getsize(os.path.join(folder, f'{nm}.bin')) for nm in parts_present}
    layout = {}
    kern_sz = sizes.get('KERNEL', 0)
    root_sz = sizes.get('ROOT', 0)
    data_sz = sizes.get('DATA', 0)
    if 'HEADER' not in parts_present and 'LOGO' not in parts_present:
        layout['KERNEL'] = {'offset': 0, 'total': kern_sz, 'main': kern_sz, 'flags': 0}
        layout['ROOT'] = {'offset': kern_sz, 'total': root_sz, 'main': root_sz, 'flags': 0}
        layout['DATA'] = {'offset': kern_sz + root_sz, 'total': data_sz, 'main': data_sz, 'flags': 0}
    else:
        logo_sz = sizes.get('LOGO', 0)
        boot_sz = sizes.get('HEADER', 256)
        layout['HEADER'] = {'offset': 0, 'total': boot_sz, 'main': boot_sz, 'flags': 0}
        layout['LOGO'] = {'offset': 256, 'total': logo_sz, 'main': logo_sz, 'flags': 0}
        layout['KERNEL'] = {'offset': HYBRID_KERN_OFF, 'total': kern_sz, 'main': kern_sz, 'flags': 0}
        layout['ROOT'] = {'offset': HYBRID_KERN_OFF + kern_sz, 'total': root_sz, 'main': root_sz, 'flags': 0}
        layout['DATA'] = {'offset': HYBRID_KERN_OFF + kern_sz + root_sz, 'total': data_sz, 'main': data_sz, 'flags': 0}
    return ('CRAMFS', layout, [], None, 0, False, False, 0, None)

def repack_fw_dir(folder: str, out_fw_path: str, original_total_sum: int = None) -> tuple:
    meta_path = os.path.join(folder, '_multisw_meta.bin')
    header_path = os.path.join(folder, '_multisw_header.bin')
    if os.path.exists(meta_path) and os.path.exists(header_path):
        meta_raw = open(meta_path, 'rb').read()
        slot_count = struct.unpack_from('<I', meta_raw, 0)[0]
        slots_info = []
        for i in range(slot_count):
            base, flash_size = struct.unpack_from('<II', meta_raw, 4 + i * 8)
            slots_info.append({'base': base, 'flash_size': flash_size})
        total_size = slots_info[-1]['base'] + slots_info[-1]['flash_size']
        fw_buf = bytearray(b'\xff' * total_size)
        fw_buf[:len(open(header_path, 'rb').read())] = open(header_path, 'rb').read()
        for i in range(slot_count - 1):
            gap_path = os.path.join(folder, f'_multisw_gap_{i + 1}.bin')
            if os.path.exists(gap_path):
                gap_start = slots_info[i]['base'] + slots_info[i]['flash_size']
                fw_buf[gap_start:gap_start + len(open(gap_path, 'rb').read())] = open(gap_path, 'rb').read()
        last_entries = []
        last_wp = last_crcen = False
        last_ver = 0
        last_tcrc = None
        for i, si in enumerate(slots_info):
            sw_dir = os.path.join(folder, f'SW{i + 1}')
            with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tf:
                tf_path = tf.name
            try:
                r = repack_fw_dir(sw_dir, tf_path)
                slot_data = open(tf_path, 'rb').read()
                os.unlink(tf_path)
                fw_buf[si['base']:si['base'] + min(si['flash_size'], len(slot_data))] = slot_data[:si['flash_size']]
                if i == 0 and r[0] in ['TABLE', 'MULTISW']:
                    _, last_cnt, last_entries, last_wp, last_crcen, last_ver, last_tcrc = r
            except Exception as e:
                if os.path.exists(tf_path):
                    os.unlink(tf_path)
                raise e
        with open(out_fw_path, 'wb') as f:
            f.write(fw_buf)
        return ('MULTISW', len(last_entries), last_entries, last_wp, last_crcen, last_ver, last_tcrc)
    
    orig_path = os.path.join(folder, '_multisw_original.bin')
    if os.path.exists(orig_path):
        orig = open(orig_path, 'rb').read()
        with open(out_fw_path, 'wb') as f:
            f.write(orig)
        s0 = parse_multisw_slots(orig)[0]
        for e in s0['entries']:
            e['fs'] = get_fs_from_flags(e.get('flags', 0), e['data'])
        return ('MULTISW', len(s0['entries']), s0['entries'], s0['wp'], s0['crcen'], s0['ver'], s0['tcrc'])
    
    mode, layout, tbl_entries, tbl_off, fw_size, wp, crcen, ver, tcrc = parse_dir_layout(folder)
    parts_data = {nm: open(os.path.join(folder, f'{nm}.bin'), 'rb').read() for nm in layout}
    if 'DATA' in parts_data:
        _, parts_data['DATA'] = parse_meta_block(parts_data['DATA'])
    
    if mode == 'TABLE':
        idx = read_idx_file(folder)
        src = None
        if idx:
            sp = idx.get('source_path')
            if sp and os.path.isfile(sp):
                try:
                    src = Path(sp).read_bytes()
                except OSError:
                    pass
        if src is None:
            apply_idx_src(folder, layout, parts_data)
        
        rpath = os.path.join(folder, 'ROOT.bin')
        if os.path.isfile(rpath):
            parts_data['ROOT'] = open(rpath, 'rb').read()
        
        if src is not None and idx:
            for part in idx.get('partitions', []):
                nm = part.get('name')
                if nm not in layout or nm == 'HEADER' or nm == 'ROOT':
                    continue
                fo = int(part.get('file_off', 0))
                tot = int(part.get('total', 0))
                if tot != layout[nm]['total']:
                    continue
                disk_data = parts_data.get(nm, b'')
                if len(disk_data) < tot:
                    disk_data = disk_data + b'\xff' * (tot - len(disk_data))
                disk_data = disk_data[:tot]
                orig_chunk = src[fo:fo + tot]
                if len(orig_chunk) < tot:
                    orig_chunk = orig_chunk + b'\xff' * (tot - len(orig_chunk))
                if (zlib.crc32(disk_data) & 0xFFFFFFFF) == (zlib.crc32(orig_chunk) & 0xFFFFFFFF):
                    parts_data[nm] = orig_chunk
                else:
                    parts_data[nm] = disk_data

        crc_pol = idx.get('table_crc_policy') if idx else None
        toff = layout['TABLE']['offset']
        
        if src is not None:
            fw_buf = bytearray(src)
            if 'HEADER' in parts_data and 'HEADER' in layout:
                hdat = parts_data['HEADER']
                htot = layout['HEADER']['total']
                if len(hdat) != htot:
                    raise ValueError("HEADER.bin mismatch")
                fw_buf[0 : htot] = hdat
            
            ro = layout['ROOT']['offset']
            orig_tb_bytes = bytes(src[toff : toff + TABLE_SIZE])
            _, real_entries, _, _, _, _ = parse_tbl_data(orig_tb_bytes)
            
            disk_tb_path = os.path.join(folder, 'TABLE.bin')
            disk_tb_bytes = None
            table_was_modified = False
            if os.path.isfile(disk_tb_path):
                with open(disk_tb_path, 'rb') as _f:
                    disk_tb_bytes = _f.read()[:TABLE_SIZE]
                if disk_tb_bytes != orig_tb_bytes:
                    tb_auto = bytearray(disk_tb_bytes)
                    if len(tb_auto) < TABLE_SIZE:
                        tb_auto += b'\xff' * (TABLE_SIZE - len(tb_auto))
                    struct.pack_into('>I', tb_auto, T_TABLECRC_OFF, calc_crc32(bytes(tb_auto[:T_TABLECRC_OFF])))
                    disk_tb_bytes = bytes(tb_auto)
                    try:
                        _, disk_tb_entries, _, _, _, _ = parse_tbl_data(disk_tb_bytes)
                        table_was_modified = True
                    except ValueError:
                        disk_tb_bytes = orig_tb_bytes
            
            active_entries = disk_tb_entries if table_was_modified else real_entries
            root_e_real = next((e for e in active_entries if e['name'] == 'ROOT'), None)
            rtot = root_e_real['total'] if root_e_real else layout['ROOT']['total']
            rdat = parts_data['ROOT']
            if len(rdat) > rtot:
                raise ValueError("ROOT.bin size error")
            
            if idx:
                for part in idx.get('partitions', []):
                    nm = part.get('name')
                    if nm not in layout or nm in ('HEADER', 'ROOT', 'TABLE'):
                        continue
                    fo = int(part.get('file_off', 0))
                    tot = int(part.get('total', 0))
                    if tot <= 0:
                        continue
                    orig_chunk = src[fo:fo + tot]
                    if len(orig_chunk) < tot:
                        orig_chunk = orig_chunk + b'\xff' * (tot - len(orig_chunk))
                    disk_data = parts_data.get(nm, orig_chunk)
                    if zlib.crc32(disk_data) != zlib.crc32(orig_chunk):
                        off = layout[nm]['offset']
                        fw_buf[off : off + tot] = disk_data[:tot]
            
            if table_was_modified and disk_tb_bytes is not None:
                fw_buf[toff : toff + TABLE_SIZE] = disk_tb_bytes
            
            original_root = bytes(src[ro : ro + rtot])
            final_root = rdat + b'\xff' * (rtot - len(rdat))
            if final_root == original_root and not table_was_modified:
                with open(out_fw_path, 'wb') as f:
                    f.write(src if bytes(fw_buf) == src else fw_buf)
                return ('TABLE', toff, len(real_entries), real_entries, wp, crcen, ver, tcrc, len(src))
            
            fw_buf[ro : ro + rtot] = final_root
            if original_total_sum is not None:
                target_fw_sum = original_total_sum & 0xFFFFFFFF
                current_fw_sum = sum(fw_buf) & 0xFFFFFFFF
                if current_fw_sum != target_fw_sum:
                    diff = (target_fw_sum - current_fw_sum)
                    if diff < -0x80000000: diff += 0x100000000
                    if diff > 0x7FFFFFFF: diff -= 0x100000000
                    pad_end = ro + rtot
                    pad_start = pad_end
                    for idx_f in range(pad_end - 1, ro - 1, -1):
                        if fw_buf[idx_f] != 0xFF:
                            pad_start = idx_f + 1
                            break
                    if pad_start == pad_end:
                        pad_start = ro + int(rtot * 0.9)
                    rem = diff
                    for i in range(pad_end - 1, pad_start - 1, -1):
                        if rem == 0:
                            break
                        ov = fw_buf[i]
                        if rem > 0 and ov < 255:
                            change = min(rem, 255 - ov)
                            fw_buf[i] += change
                            rem -= change
                        elif rem < 0 and ov > 0:
                            change = min(abs(rem), ov)
                            fw_buf[i] -= change
                            rem += change
            with open(out_fw_path, 'wb') as f:
                f.write(fw_buf)
            _, entries_out, wp_out, crcen_out, ver_out, tcrc_out = parse_tbl_data(orig_tb_bytes)
            return ('TABLE', len(entries_out), entries_out, wp_out, crcen_out, ver_out, tcrc_out)
        
        fw_buf = bytearray(b'\xff' * fw_size)
        for nm, info in layout.items():
            d = parts_data.get(nm, b'')
            off, tot = info['offset'], info['total']
            if len(d) > tot:
                raise ValueError("Partition overflow")
            fw_buf[off:off + tot] = d + b'\xff' * (tot - len(d))
        with open(os.path.join(folder, 'TABLE.bin'), 'rb') as f:
            orig_tb_work = f.read()
        new_tb = patch_tbl_root_crc(
            orig_tb_work, tbl_entries, parts_data.get('ROOT', b''),
            {e['name']: e['main'] for e in tbl_entries}, crc_pol
        )
        fw_buf[layout['TABLE']['offset']:layout['TABLE']['offset'] + TABLE_SIZE] = new_tb
        with open(out_fw_path, 'wb') as f:
            f.write(fw_buf)
        _, entries_out, wp_out, crcen_out, ver_out, tcrc_out = parse_tbl_data(new_tb)
        return ('TABLE', len(entries_out), entries_out, wp_out, crcen_out, ver_out, tcrc_out)
    
    total_size = max(v['offset'] + v['total'] for v in layout.values() if v['total'] > 0)
    fw_buf = bytearray(b'\xff' * total_size)
    for nm, info in layout.items():
        d = parts_data.get(nm, b'')
        off, tot = info['offset'], info['total']
        if len(d) > tot:
            raise ValueError("Partition overflow")
        fw_buf[off:off + len(d)] = d
        if len(d) < tot:
            fw_buf[off + len(d) : off + tot] = b'\xff' * (tot - len(d))
    if original_total_sum is not None:
        target_fw_sum = original_total_sum & 0xFFFFFFFF
        current_fw_sum = sum(fw_buf) & 0xFFFFFFFF
        if current_fw_sum != target_fw_sum:
            diff = (target_fw_sum - current_fw_sum)
            if diff < -0x80000000: diff += 0x100000000
            if diff > 0x7FFFFFFF: diff -= 0x100000000
            root_info = layout.get('ROOT')
            if root_info:
                ro = root_info['offset']
                tot = root_info['total']
                pad_end = ro + tot
                pad_start = pad_end
                for idx_f in range(pad_end - 1, ro - 1, -1):
                    if fw_buf[idx_f] != 0xFF:
                        pad_start = idx_f + 1
                        break
                if pad_start == pad_end:
                    pad_start = ro + int(tot * 0.9)
            else:
                pad_start = max(0, len(fw_buf) - 65536)
                pad_end = len(fw_buf)
            rem = diff
            for i in range(pad_end - 1, pad_start - 1, -1):
                if rem == 0:
                    break
                ov = fw_buf[i]
                if rem > 0 and ov < 255:
                    change = min(rem, 255 - ov)
                    fw_buf[i] += change
                    rem -= change
                elif rem < 0 and ov > 0:
                    change = min(abs(rem), ov)
                    fw_buf[i] -= change
                    rem += change
    with open(out_fw_path, 'wb') as f:
        f.write(fw_buf)
    entries_out = [{'idx': i, 'name': n, 'total': layout[n]['total'], 'main': layout[n]['total'], 'start': layout[n]['offset'], 'flags': 0, 'fs': get_fs_type(parts_data.get(n, b'')), 'stored_crc': 0, 'data': parts_data.get(n, b'')} for i, n in enumerate(layout)]
    return ('CRAMFS', len(entries_out), entries_out, False, False, 0, None)

CRAMFS_MAGIC_LE = 0x28CD3D45
CRAMFS_MAGIC_BE = 0x453DCD28
BLOCK_SIZE = 4096
ORIG_SIZE_FILE = ".orig_size"
KNOWN_VENDOR_MAGIC = {0x68CD3D45, 0x28CD3D44, 0x28CD3D46, 0x453DCD28, 0x2BE0A245}

def evaluate_cramfs_struct(data: bytes, offset: int = 0) -> int:
    d = data[offset:]
    if len(d) < 76: return 0
    score = 0
    try:
        sz_le = struct.unpack_from('<I', d, 4)[0]
        sz_be = struct.unpack_from('>I', d, 4)[0]
        for sz in [sz_le, sz_be]:
            if 4096 <= sz <= len(d) + 65536:
                score += 15
                break
    except Exception:
        pass
    try:
        w0 = struct.unpack_from('<I', d, 64)[0]
        w2 = struct.unpack_from('<I', d, 72)[0]
        mode = w0 & 0xFFFF
        ftype = mode >> 12 & 15
        if ftype == 4: score += 25
        nl = (w2 & 63) * 4
        off_val = (w2 >> 6) * 4
        if nl == 0: score += 15
        if off_val == 76: score += 25
    except Exception:
        pass
    return min(score, 100)

def fix_cramfs_magic_crc(data: bytes) -> bytes:
    buf = bytearray(data)
    struct.pack_into('<I', buf, 0, CRAMFS_MAGIC_LE)
    struct.pack_into('<I', buf, 32, 0)
    struct.pack_into('<I', buf, 32, zlib.crc32(bytes(buf)) & 0xFFFFFFFF)
    return bytes(buf)

def analyze_dir_size(data: bytes, start: int, max_scan: int = 8192) -> int:
    pos = start
    end = min(start + max_scan, len(data))
    size = 0
    while pos + 12 <= end:
        w0 = struct.unpack_from('<I', data, pos)[0]
        w2 = struct.unpack_from('<I', data, pos + 8)[0]
        mode = w0 & 0xFFFF
        ftype = mode >> 12 & 15
        nl = (w2 & 63) * 4
        if ftype not in (1, 2, 4, 6, 8, 10, 12): break
        if nl < 4 or nl > 256: break
        if pos + 12 + nl > len(data): break
        name_b = data[pos + 12:pos + 12 + nl]
        if not all(32 <= b <= 126 or b == 0 for b in name_b): break
        size += 12 + nl
        pos += 12 + nl
    return size

def rebuild_root_inode(data: bytes) -> Optional[bytes]:
    if len(data) < 100: return None
    CHILDREN_OFF = 76
    actual_size = None
    pos = CHILDREN_OFF
    for _ in range(64):
        if pos + 12 > len(data): break
        w0 = struct.unpack_from('<I', data, pos)[0]
        w2 = struct.unpack_from('<I', data, pos + 8)[0]
        mode = w0 & 0xFFFF
        ftype = mode >> 12 & 15
        nl = (w2 & 63) * 4
        if nl < 4 or nl > 256 or pos + 12 + nl > len(data): break
        if ftype == 4:
            dir_data_off = (w2 >> 6) * 4
            candidate = dir_data_off - CHILDREN_OFF
            if 12 <= candidate <= 8192 and candidate % 4 == 0:
                actual_size = candidate
                break
        pos += 12 + nl
    if actual_size is None:
        actual_size = analyze_dir_size(data, CHILDREN_OFF, max_scan=4096)
    if actual_size is None or actual_size < 12: return None
    buf = bytearray(data)
    gid = buf[71]
    new_w1 = actual_size & 0xFFFFFF | gid << 24
    struct.pack_into('<I', buf, 68, new_w1)
    struct.pack_into('<I', buf, 72, 1216)
    struct.pack_into('<I', buf, 0, CRAMFS_MAGIC_LE)
    struct.pack_into('<I', buf, 32, 0)
    struct.pack_into('<I', buf, 32, zlib.crc32(bytes(buf)) & 0xFFFFFFFF)
    return bytes(buf)

def slice_cramfs(data: bytes, offset: int) -> bytes:
    d = data[offset:]
    if len(d) < 8: return d
    sz = struct.unpack_from('<I', d, 4)[0]
    if 4096 <= sz <= len(d):
        return d[:sz]
    return d

def perform_deep_detect(data: bytes) -> dict:
    SCORE_THRESHOLD = 55
    best = {'found': False, 'payload': b'', 'method': 'none', 'offset': 0, 'score': 0, 'patched': False}

    def evaluate_payload(payload, method, offset, patched, raw_score=None):
        score = raw_score if raw_score is not None else evaluate_cramfs_struct(payload)
        magic = struct.unpack_from('<I', payload, 0)[0] if len(payload) >= 4 else 0
        if magic == CRAMFS_MAGIC_LE:
            score = min(score + 20, 100)
        if score >= SCORE_THRESHOLD and score > best['score']:
            best.update(found=True, payload=payload, method=method, offset=offset, score=score, patched=patched)
            return True
        return False

    if len(data) >= 4:
        if struct.unpack_from('<I', data, 0)[0] == CRAMFS_MAGIC_LE:
            evaluate_payload(slice_cramfs(data, 0), 'standard_LE', 0, False, 99)
            if best['score'] >= 95: return best
        if struct.unpack_from('>I', data, 0)[0] == CRAMFS_MAGIC_BE:
            evaluate_payload(data, 'standard_BE', 0, False, 90)
    
    sc = evaluate_cramfs_struct(data, 0)
    if sc >= SCORE_THRESHOLD:
        patched = fix_cramfs_magic_crc(slice_cramfs(data, 0))
        evaluate_payload(patched, 'tampered_magic@0', 0, True, sc)
    
    if best['score'] < 90 and len(data) >= 100:
        repaired = rebuild_root_inode(slice_cramfs(data, 0))
        if repaired is not None:
            evaluate_payload(repaired, 'repaired_root_inode', 0, True, evaluate_cramfs_struct(repaired, 0))
    
    scan_end = min(len(data) - 76, 524288)
    for off in range(4, scan_end, 4):
        magic = struct.unpack_from('<I', data, off)[0]
        if magic in (CRAMFS_MAGIC_LE, CRAMFS_MAGIC_BE):
            payload = slice_cramfs(data, off)
            evaluate_payload(payload, f'embedded@0x{off:X}', off, False, 99 if magic == CRAMFS_MAGIC_LE else 90)
            if best['score'] >= 95: return best
    return best

def read_cramfs_inode(data: bytes, off: int) -> tuple:
    w0, w1, w2 = struct.unpack_from('<III', data, off)
    return (w0 & 0xFFFF, (w0 >> 16) & 0xFFFF, (w1 >> 24) & 0xFF, w1 & 0xFFFFFF, (w2 & 63) * 4, ((w2 >> 6) & 0x3FFFFFF) * 4)

def unpack_cramfs_file(data: bytes, file_off: int, file_size: int) -> bytes:
    if file_size == 0: return b''
    n = math.ceil(file_size / BLOCK_SIZE)
    out = b''
    prev = file_off + n * 4
    for i in range(n):
        ptr = struct.unpack_from('<I', data, file_off + i * 4)[0]
        chunk = bytes(data[prev:ptr])
        try:
            out += zlib.decompress(chunk)
        except Exception:
            out += chunk
        prev = ptr
    return out[:file_size]

def recursive_cramfs_walk(data: bytes, dir_off: int, dir_size: int, path: str, result: list):
    pos = dir_off
    end = dir_off + dir_size
    while pos < end:
        if pos + 12 > len(data): break
        mode, uid, gid, size, nl, of = read_cramfs_inode(data, pos)
        inode_off = pos
        pos += 12
        if nl == 0 or pos + nl > len(data): break
        name = bytes(data[pos:pos + nl]).rstrip(b'\x00').decode('latin1', 'replace')
        pos += nl
        if not name or name in ('.', '..'): continue
        fpath = (path + '/' + name).lstrip('/')
        ftype = mode >> 12 & 15
        if ftype == 4:
            result.append(('dir', fpath, mode, uid, gid, 0, inode_off, of, size))
            if of > 0 and size > 0:
                recursive_cramfs_walk(data, of, size, fpath, result)
        elif ftype == 8:
            result.append(('file', fpath, mode, uid, gid, size, inode_off, of, 0))

def list_cramfs_dir(data: bytes) -> list:
    magic = struct.unpack_from('<I', data, 0)[0]
    if magic != CRAMFS_MAGIC_LE:
        raise ValueError('Not a CRAMFS image')
    _, _, _, root_size, _, root_off = read_cramfs_inode(data, 64)
    entries = []
    recursive_cramfs_walk(data, root_off, root_size, '', entries)
    return entries

def query_7z_binary() -> Optional[str]:
    try:
        arch = platform.machine().lower()
        is_64 = "64" in arch or "amd64" in arch
        paths = []
        if getattr(sys, 'frozen', False):
            base = Path(sys.executable).parent
            paths.append(base / "7-Zip" / "7z.exe")
            paths.append(base / "7-Zip" / "7-Zip64" / "7z.exe")
        paths.append(r"C:\Program Files\7-Zip\7z.exe")
        for p in paths:
            if os.path.exists(p): return str(p)
        sh_path = shutil.which("7z")
        if sh_path: return sh_path
    except Exception:
        pass
    return None

def calc_dir_md5(path: Path) -> str:
    state = []
    if not path.exists(): return ""
    for root, _, files in os.walk(path):
        for f in sorted(files):
            if f in (ORIG_SIZE_FILE, ".unpacked_state"): continue
            p = os.path.join(root, f)
            try:
                content = open(p, 'rb').read()
                file_hash = hashlib.md5(content).hexdigest()
                rel = os.path.relpath(p, path)
                state.append(f"{rel}|{len(content)}|{file_hash}")
            except Exception:
                pass
    return hashlib.md5("\n".join(sorted(state)).encode()).hexdigest()

def extract_cramfs_root(cramfs_data: bytes, out_dir: Path, log_func=None) -> list:
    def log(msg):
        if log_func: log_func(msg)
    out_dir.mkdir(parents=True, exist_ok=True)
    is_sq = cramfs_data[:4] in (b'hsqs', b'sqsh', b'shsq')
    entries = []
    perms = {}
    success = False
    if not is_sq:
        try:
            entries = list_cramfs_dir(cramfs_data)
            for typ, path, mode, uid, gid, size, _, data_off, _ in entries:
                full = out_dir / path
                perms[path] = {'mode': mode, 'uid': uid, 'gid': gid}
                if typ == 'dir':
                    full.mkdir(parents=True, exist_ok=True)
                else:
                    full.parent.mkdir(parents=True, exist_ok=True)
                    full.write_bytes(unpack_cramfs_file(cramfs_data, data_off, size))
            success = True
        except Exception:
            success = False
    if not success:
        bin_7z = query_7z_binary()
        if bin_7z:
            with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tmp:
                tmp.write(cramfs_data)
                tmp_path = tmp.name
            try:
                cmd = [bin_7z, "x", tmp_path, f"-o{str(out_dir)}", "-y"]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    for root, dirs, files in os.walk(str(out_dir)):
                        for d in dirs:
                            rel = os.path.relpath(os.path.join(root, d), str(out_dir)).replace('\\', '/')
                            entries.append(('dir', rel, 0o40755, 0, 0, 0, 0, 0, 0))
                        for f in files:
                            if f == ORIG_SIZE_FILE: continue
                            rel = os.path.relpath(os.path.join(root, f), str(out_dir)).replace('\\', '/')
                            f_size = os.path.getsize(os.path.join(root, f))
                            entries.append(('file', rel, 0o100644, 0, 0, f_size, 0, 0, 0))
            except Exception:
                pass
            finally:
                if os.path.exists(tmp_path):
                    try: os.unlink(tmp_path)
                    except Exception: pass
    
    sig = cramfs_data[16:32].hex() if len(cramfs_data) >= 32 else ""
    info = {'signature': sig, 'perms': perms, 'is_squashfs': is_sq}
    (out_dir / ORIG_SIZE_FILE).write_text(json.dumps(info), encoding='utf-8')
    try:
        state = calc_dir_md5(out_dir)
        (out_dir.parent / ".unpacked_state").write_text(state, encoding='utf-8')
    except Exception:
        pass
    return entries

def map_arabic_to_latin(name: str) -> str:
    m = {
        'ا': 'a', 'أ': 'a', 'إ': 'i', 'آ': 'aa', 'ب': 'b', 'ت': 't', 'ث': 'th',
        'ج': 'j', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ذ': 'dh', 'ر': 'r', 'ز': 'z',
        'س': 's', 'ش': 'sh', 'ص': 's', 'ض': 'd', 'ط': 't', 'ظ': 'z', 'ع': 'a',
        'غ': 'gh', 'ف': 'f', 'ق': 'q', 'ك': 'k', 'ل': 'l', 'م': 'm', 'ن': 'n',
        'ه': 'h', 'و': 'w', 'ي': 'y', 'ى': 'a', 'ة': 'h'
    }
    res = ''
    for c in name:
        if c in m: res += m[c]
        elif c.isascii() and (c.isalnum() or c in '._-'): res += c
        elif c in ' \t': res += '_'
        else: res += '_'
    while '__' in res: res = res.replace('__', '_')
    return res.strip('_') or 'file'

def build_cramfs_image(src_dir: Path, orig_file_size: Optional[int] = None, orig_crc32: Optional[int] = None, orig_sum: Optional[int] = None) -> bytes:
    sig = b'Compressed ROMFS'
    perms = {}
    sz_file = src_dir / ORIG_SIZE_FILE
    if sz_file.is_file():
        try:
            info = json.loads(sz_file.read_text(encoding='utf-8'))
            if orig_file_size is None: orig_file_size = info.get('orig_file_size')
            sig_hex = info.get('signature', '')
            if sig_hex: sig = bytes.fromhex(sig_hex)
            perms = info.get('perms', {})
        except Exception:
            pass

    def scan_tree(path: Path, rel_prefix: str = '') -> list:
        res = []
        try:
            names = sorted(os.listdir(path))
        except OSError:
            return res
        for orig_name in names:
            if orig_name == ORIG_SIZE_FILE: continue
            orig_full = path / orig_name
            try:
                orig_name.encode('latin1')
                safe_name = orig_name
            except UnicodeEncodeError:
                safe_name = map_arabic_to_latin(orig_name)
            rel = (rel_prefix + '/' + safe_name).lstrip('/')
            p = perms.get(rel, {})
            if orig_full.is_dir():
                res.append({'type': 'dir', 'name': safe_name, 'path': orig_full, 'mode': p.get('mode', 0o40755), 'uid': p.get('uid', 0), 'gid': p.get('gid', 0), 'children': scan_tree(orig_full, rel)})
            elif orig_full.is_file():
                res.append({'type': 'file', 'name': safe_name, 'path': orig_full, 'mode': p.get('mode', 0o100644), 'uid': p.get('uid', 0), 'gid': p.get('gid', 0), 'data': orig_full.read_bytes()})
        return res

    tree = scan_tree(src_dir)
    ROOT_OFF = 64
    cursor = ROOT_OFF + 12
    queue = [tree]
    while queue:
        level = queue.pop(0)
        nxt = []
        for e in level:
            e['inode_off'] = cursor
            cursor += 12 + (len(e['name'].encode('latin1')) + 3 & -4)
        child_ptr = cursor
        for e in level:
            if e['type'] == 'dir' and e['children']:
                e['children_off'] = child_ptr
                child_ptr += sum(12 + (len(c['name'].encode('latin1')) + 3 & -4) for c in e['children'])
                nxt.extend(e['children'])
            elif e['type'] == 'dir':
                e['children_off'] = 0
        if nxt: queue.append(nxt)
    data_start = cursor

    data_section = bytearray()
    content_cache = {}
    dedup_blocks = {}

    def compress_files(entries):
        for e in entries:
            if e['type'] == 'file':
                raw = e['data']
                k = hashlib.sha256(raw).hexdigest()
                if k in content_cache:
                    e['inode_off_resolved'] = content_cache[k]
                else:
                    pad = -len(data_section) % 4
                    data_section.extend(b'\x00' * pad)
                    abs_off = data_start + len(data_section)
                    content_cache[k] = abs_off
                    e['inode_off_resolved'] = abs_off
                    n = math.ceil(len(raw) / BLOCK_SIZE) if raw else 0
                    ptrs = bytearray(n * 4)
                    blks = bytearray()
                    for i in range(n):
                        chunk = raw[i * BLOCK_SIZE:(i + 1) * BLOCK_SIZE]
                        comp = zlib.compress(chunk, 9)
                        blks.extend(comp)
                        struct.pack_into('<I', ptrs, i * 4, abs_off + n * 4 + len(blks))
                    data_section.extend(ptrs + blks)
                    dedup_blocks[k] = n
            elif e['type'] == 'dir':
                compress_files(e['children'])

    compress_files(tree)

    inode_buf = bytearray()
    r_uid = tree[0]['uid'] if tree else 0
    r_gid = tree[0]['gid'] if tree else 0
    r_size = sum(12 + (len(c['name'].encode('latin1')) + 3 & -4) for c in tree)
    inode_buf.extend(struct.pack('<HHII', 0o40755, r_uid, r_size & 0xFFFFFF | r_gid << 24, (ROOT_OFF + 12) // 4 << 6))
    
    queue = [tree]
    while queue:
        level = queue.pop(0)
        nxt = []
        for e in level:
            nb = e['name'].encode('latin1')
            nl = len(nb) + 3 & -4
            padded = nb + b'\x00' * (nl - len(nb))
            if e['type'] == 'dir':
                sz_val = sum(12 + (len(c['name'].encode('latin1')) + 3 & -4) for c in e['children']) if e['children'] else 0
                off_val = e['children_off']
                if e['children']: nxt.append(e['children'])
            else:
                sz_val = len(e['data'])
                off_val = e['inode_off_resolved']
            w1 = sz_val & 0xFFFFFF | e['gid'] << 24
            w2 = nl // 4 | off_val // 4 << 6
            inode_buf.extend(struct.pack('<HHII', e['mode'], e['uid'], w1, w2) + padded)
        if nxt: queue.append(nxt)

    def count_nodes(entries):
        return sum(1 + (count_nodes(e['children']) if e['type'] == 'dir' else 0) for e in entries)

    total_files = count_nodes(tree) + 1
    raw_size = ROOT_OFF + len(inode_buf) + len(data_section)
    total_size = (raw_size + 4095) // 4096 * 4096
    
    sb = bytearray(64)
    struct.pack_into('<I', sb, 0, CRAMFS_MAGIC_LE)
    struct.pack_into('<I', sb, 4, total_size)
    struct.pack_into('<I', sb, 8, 3)
    sb[16:32] = (sig + b'\x00' * 16)[:16]
    struct.pack_into('<I', sb, 40, sum(dedup_blocks.values()))
    struct.pack_into('<I', sb, 44, total_files)
    
    image = bytearray(total_size)
    image[0:64] = sb
    image[64:64 + len(inode_buf)] = inode_buf
    image[data_start:data_start + len(data_section)] = data_section
    struct.pack_into('<I', image, 32, 0)
    struct.pack_into('<I', image, 32, zlib.crc32(bytes(image)) & 0xFFFFFFFF)

    if orig_file_size is not None:
        if len(image) > orig_file_size:
            raise ValueError("Size exceeded original allocation")
        if orig_file_size > len(image):
            image += bytearray(b'\xff' * (orig_file_size - len(image)))
        struct.pack_into('<I', image, 4, orig_file_size)
        struct.pack_into('<I', image, 32, 0)
        struct.pack_into('<I', image, 32, zlib.crc32(bytes(image)) & 0xFFFFFFFF)

    if orig_crc32 is not None and orig_sum is not None and len(image) >= 40:
        for _ in range(500):
            curr = zlib.crc32(image) & 0xFFFFFFFF
            diff = curr ^ orig_crc32
            for _ in range(32):
                if diff & 1: diff = (diff >> 1) ^ 0xEDB88320
                else: diff >>= 1
            l4 = struct.unpack("<I", image[-4:])[0]
            image[-4:] = struct.pack("<I", l4 ^ diff)
            curr_sum = sum(image) & 0xFFFFFFFF
            if curr_sum == orig_sum and (zlib.crc32(image) & 0xFFFFFFFF) == orig_crc32:
                break
            diff_sum = (orig_sum - curr_sum) & 0xFFFFFFFF
            step = 1 if diff_sum <= 0x80000000 else -1
            rem = diff_sum if step == 1 else 0x100000000 - diff_sum
            for i in range(10, 1024):
                idx = len(image) - i
                if idx < 64: break
                old = image[idx]
                if step == 1:
                    change = min(rem, 255 - old)
                    image[idx] += change
                    rem -= change
                else:
                    change = min(rem, old)
                    image[idx] -= change
                    rem -= change
                if rem == 0: break
    return bytes(image)

def query_root_file(sel: Path) -> Optional[Path]:
    if not sel.exists(): return None
    if sel.is_file():
        if "ROOT" in sel.name.upper() and sel.suffix.upper() == ".BIN": return sel
        for n in ("ROOT.bin", "root.bin"):
            c = sel.parent / n
            if c.is_file(): return c
        return sel
    for n in ("ROOT.bin", "root.bin"):
        c = sel / n
        if c.is_file(): return c
    return None

def query_cfg_file(root_dir: Path) -> Optional[Path]:
    if not root_dir.is_dir(): return None
    direct = root_dir / "etc" / "gx.cfg"
    if direct.is_file(): return direct
    for p in root_dir.rglob("gx.cfg"):
        if p.parent.name.lower() == "etc": return p
    return None

def extract_modeid_rcutype(txt: str) -> tuple[Optional[str], Optional[str]]:
    mid = rcu = None
    for raw in txt.splitlines():
        line = raw.strip()
        if not line: continue
        k, _, rest = line.partition("=")
        key = k.strip().upper()
        val = rest.rstrip(";").strip() if rest else ""
        if key == "MODEID": mid = val
        elif key == "RCUTYPE": rcu = val
    return mid, rcu

def inject_modeid_rcutype_strict(raw: bytes, mid: str, rcu: str) -> tuple[bytes, Optional[str]]:
    try:
        s = raw.decode("latin-1")
        mid.encode("latin-1")
        rcu.encode("latin-1")
    except UnicodeEncodeError:
        return raw, "Invalid ASCII input"

    def split_line(line):
        if line.endswith("\r\n"): return line[:-2], "\r\n"
        if line.endswith("\n"): return line[:-1], "\n"
        if line.endswith("\r"): return line[:-1], "\r"
        return line, ""

    lines = s.splitlines(keepends=True)
    old_sz = len(raw)
    m_info = r_info = None
    for i, l in enumerate(lines):
        body, nl = split_line(l)
        k = body.strip().partition("=")[0].strip().upper()
        ind = body[:len(body) - len(body.lstrip())]
        if k == "MODEID": m_info = {"idx": i, "body": body, "nl": nl, "indent": ind, "old_len": len(body)}
        elif k == "RCUTYPE": r_info = {"idx": i, "body": body, "nl": nl, "indent": ind, "old_len": len(body)}

    if not m_info and not r_info: return raw, "Keys not found"
    new_m = m_info["indent"] + f"MODEID={mid};" if m_info else ""
    new_r = r_info["indent"] + f"RCUTYPE={rcu};" if r_info else ""
    m_diff = len(new_m) - (m_info["old_len"] if m_info else 0)
    r_diff = len(new_r) - (r_info["old_len"] if r_info else 0)
    diff = m_diff + r_diff

    if diff <= 0:
        surplus = -diff
        if m_info and r_info:
            half = surplus // 2
            new_m += " " * half
            new_r += " " * (surplus - half)
        elif m_info: new_m += " " * surplus
        elif r_info: new_r += " " * surplus
    else:
        avail = sum(len(split_line(l)[0]) - len(split_line(l)[0].rstrip()) for l in lines)
        if avail < diff:
            return raw, f"Length exceeded by {diff - avail} bytes"

    res_lines = []
    for i, l in enumerate(lines):
        body, nl = split_line(l)
        k = body.strip().partition("=")[0].strip().upper()
        if k == "MODEID" and m_info: res_lines.append(new_m + nl)
        elif k == "RCUTYPE" and r_info: res_lines.append(new_r + nl)
        else: res_lines.append(body + nl)
    res = "".join(res_lines).encode("latin-1")
    return (res, None) if len(res) == old_sz else (raw, "Size mismatch error")

def inject_modeid_rcutype_free(raw: bytes, mid: str, rcu: str, extra: Optional[dict] = None) -> tuple[bytes, Optional[str]]:
    upd = extra or {}
    if mid: upd["MODEID"] = mid
    if rcu: upd["RCUTYPE"] = rcu
    try:
        s = raw.decode("latin-1")
        for k, v in upd.items(): v.encode("latin-1")
    except UnicodeEncodeError:
        return raw, "Invalid ASCII input"

    def split_line(line):
        if line.endswith("\r\n"): return line[:-2], "\r\n"
        if line.endswith("\n"): return line[:-1], "\n"
        if line.endswith("\r"): return line[:-1], "\r"
        return line, ""

    lines = s.splitlines(keepends=True)
    res_lines = []
    applied = set()
    for l in lines:
        body, nl = split_line(l)
        k = body.strip().partition("=")[0].strip().upper()
        ind = body[:len(body) - len(body.lstrip())]
        if k in upd:
            res_lines.append(ind + f"{k}={upd[k]};" + nl)
            applied.add(k)
        else:
            res_lines.append(body + nl)
    for k, v in upd.items():
        if k not in applied and v: res_lines.append(f"{k}={v};\n")
    return "".join(res_lines).encode("latin-1"), None

@dataclass
class FwSlot:
    name: str
    idx: int
    path: Optional[Path] = None
    root_file: Optional[Path] = None
    cfg_file: Optional[Path] = None
    cfg_raw: bytes = b""
    ext_dir: Optional[Path] = None
    root_sz: int = 0
    root_crc: Optional[int] = None
    root_sum: int = 0
    total_sum: int = 0

class DragonNoirTool:
    def __init__(self) -> None:
        self.win = Tk()
        apply_win_ico(self.win)
        self.win.title("DRAGON_NOIR_GX6605S-FW-TOOL")
        self.win.geometry("760x660")
        self.win.configure(bg=CLR_MAIN_BG)
        self.win.resizable(False, False)

        self.s1 = FwSlot("slot1", 1)
        self.s2 = FwSlot("slot2", 2)

        self.v_p1 = StringVar(value="")
        self.v_p2 = StringVar(value="")

        self.m1_orig = StringVar()
        self.r1_orig = StringVar()
        self.m1_new = StringVar()
        self.r1_new = StringVar()

        self.m2_orig = StringVar()
        self.r2_orig = StringVar()
        self.m2_new = StringVar()
        self.r2_new = StringVar()

        self._draw_gui()

    def _make_3d_btn(self, parent: Frame, text: str, command: Any) -> Button:
        btn = Button(
            parent, text=text, command=command,
            bg=CLR_BTN_NORMAL, fg=CLR_TEXT_PRIMARY,
            activebackground=CLR_BTN_ACTIVE, activeforeground=CLR_MAIN_BG,
            font=FONT_MAIN, relief="raised", bd=3,
            highlightbackground=CLR_BORDER_LIGHT, highlightcolor=CLR_TEXT_PRIMARY,
            highlightthickness=1
        )
        return btn

    def _make_engraved_entry(self, parent: Frame, textvar: StringVar, state: str = "normal", width: int = 20) -> Entry:
        ent = Entry(
            parent, textvariable=textvar, state=state,
            bg=CLR_ENGRAVED_BG, fg=CLR_TEXT_PRIMARY,
            insertbackground=CLR_TEXT_PRIMARY,
            font=FONT_MAIN, relief="sunken", bd=3,
            width=width
        )
        return ent

    def _draw_gui(self) -> None:
        hdr = Frame(self.win, bg=CLR_MAIN_BG, pady=10)
        hdr.pack(fill="x")
        Label(hdr, text="DRAGON_NOIR_GX6605S-FW-TOOL", font=FONT_HDR, fg=CLR_TEXT_PRIMARY, bg=CLR_MAIN_BG).pack()

        body = Frame(self.win, bg=CLR_MAIN_BG, padx=15, pady=5)
        body.pack(fill="both", expand=True)

        p1 = Frame(body, bg=CLR_PANEL_BG, bd=2, relief="groove", highlightbackground=CLR_BORDER_LIGHT, highlightthickness=1)
        p1.pack(fill="x", pady=5, padx=5)
        
        row1 = Frame(p1, bg=CLR_PANEL_BG, pady=5)
        row1.pack(fill="x", px=10)
        Label(row1, text="FIRMWARE 1 PATH:", font=FONT_MAIN, fg=CLR_TEXT_MUTED, bg=CLR_PANEL_BG).pack(side="left")
        e_p1 = Entry(row1, textvariable=self.v_p1, font=FONT_MAIN, bg=CLR_ENGRAVED_BG, fg=CLR_TEXT_PRIMARY, relief="sunken", bd=3, width=50)
        e_p1.pack(side="left", padx=10)
        self._make_3d_btn(row1, "BROWSE", lambda: self._browse(1)).pack(side="left")

        fields1 = Frame(p1, bg=CLR_PANEL_BG, pady=5)
        fields1.pack(fill="x", padx=10)
        
        Label(fields1, text="MODEID:", font=FONT_MAIN, fg=CLR_TEXT_MUTED, bg=CLR_PANEL_BG).grid(row=0, column=0, sticky="w", pady=2)
        self._make_engraved_entry(fields1, self.m1_orig, "readonly", 18).grid(row=0, column=1, padx=5)
        Label(fields1, text="RCUTYPE:", font=FONT_MAIN, fg=CLR_TEXT_MUTED, bg=CLR_PANEL_BG).grid(row=0, column=2, sticky="w", pady=2, padx=10)
        self._make_engraved_entry(fields1, self.r1_orig, "readonly", 18).grid(row=0, column=3, padx=5)

        Label(fields1, text="NEW MID:", font=FONT_MAIN, fg=CLR_TEXT_PRIMARY, bg=CLR_PANEL_BG).grid(row=1, column=0, sticky="w", pady=2)
        self._make_engraved_entry(fields1, self.m1_new, "normal", 18).grid(row=1, column=1, padx=5)
        Label(fields1, text="NEW RCU:", font=FONT_MAIN, fg=CLR_TEXT_PRIMARY, bg=CLR_PANEL_BG).grid(row=1, column=2, sticky="w", pady=2, padx=10)
        self._make_engraved_entry(fields1, self.r1_new, "normal", 18).grid(row=1, column=3, padx=5)

        p2 = Frame(body, bg=CLR_PANEL_BG, bd=2, relief="groove", highlightbackground=CLR_BORDER_LIGHT, highlightthickness=1)
        p2.pack(fill="x", pady=5, padx=5)

        row2 = Frame(p2, bg=CLR_PANEL_BG, pady=5)
        row2.pack(fill="x", px=10)
        Label(row2, text="FIRMWARE 2 PATH:", font=FONT_MAIN, fg=CLR_TEXT_MUTED, bg=CLR_PANEL_BG).pack(side="left")
        e_p2 = Entry(row2, textvariable=self.v_p2, font=FONT_MAIN, bg=CLR_ENGRAVED_BG, fg=CLR_TEXT_PRIMARY, relief="sunken", bd=3, width=50)
        e_p2.pack(side="left", padx=10)
        self._make_3d_btn(row2, "BROWSE", lambda: self._browse(2)).pack(side="left")

        fields2 = Frame(p2, bg=CLR_PANEL_BG, pady=5)
        fields2.pack(fill="x", padx=10)

        Label(fields2, text="MODEID:", font=FONT_MAIN, fg=CLR_TEXT_MUTED, bg=CLR_PANEL_BG).grid(row=0, column=0, sticky="w", pady=2)
        self._make_engraved_entry(fields2, self.m2_orig, "readonly", 18).grid(row=0, column=1, padx=5)
        Label(fields2, text="RCUTYPE:", font=FONT_MAIN, fg=CLR_TEXT_MUTED, bg=CLR_PANEL_BG).grid(row=0, column=2, sticky="w", pady=2, padx=10)
        self._make_engraved_entry(fields2, self.r2_orig, "readonly", 18).grid(row=0, column=3, padx=5)

        Label(fields2, text="NEW MID:", font=FONT_MAIN, fg=CLR_TEXT_PRIMARY, bg=CLR_PANEL_BG).grid(row=1, column=0, sticky="w", pady=2)
        self._make_engraved_entry(fields2, self.m2_new, "normal", 18).grid(row=1, column=1, padx=5)
        Label(fields2, text="NEW RCU:", font=FONT_MAIN, fg=CLR_TEXT_PRIMARY, bg=CLR_PANEL_BG).grid(row=1, column=2, sticky="w", pady=2, padx=10)
        self._make_engraved_entry(fields2, self.r2_new, "normal", 18).grid(row=1, column=3, padx=5)

        act_pan = Frame(body, bg=CLR_MAIN_BG, pady=10)
        act_pan.pack(fill="x")
        self._make_3d_btn(act_pan, "CONVERT FIRMWARE", self._convert_sw).pack(side="left", padx=10)

        self.log_widget = Text(body, bg=CLR_ENGRAVED_BG, fg=CLR_TEXT_PRIMARY, font=FONT_MAIN, relief="sunken", bd=3, height=12)
        self.log_widget.pack(fill="both", expand=True, pady=10)

    def _log(self, text: str) -> None:
        self.log_widget.insert(END, text + "\n")
        self.log_widget.see(END)

    def _browse(self, slot_idx: int) -> None:
        target_var = self.v_p1 if slot_idx == 1 else self.v_p2
        p = filedialog.askopenfilename(filetypes=[("Firmware Images", "*.bin"), ("All Files", "*.*")])
        if p:
            target_var.set(p)
            self._load_fw(Path(p), slot_idx)

    def _load_fw(self, path: Path, idx: int) -> None:
        slot = self.s1 if idx == 1 else self.s2
        slot.path = path
        try:
            raw = path.read_bytes()
            slot.total_sum = sum(raw) & 0xFFFFFFFF
            self._log(f"-> LOADED SL {idx}: {path.name} | SUM32: 0x{slot.total_sum:08X}")
            
            tmp_root = path.parent / f"_dn_tmp_{idx}"
            if tmp_root.exists():
                shutil.rmtree(tmp_root)
            
            mode, tbl_off, count, entries, wp, crcen, ver, tcrc, extra = extract_fw_partitions(str(path), str(tmp_root))
            slot.ext_dir = tmp_root
            
            root_bin = tmp_root / "ROOT.bin"
            if root_bin.is_file():
                slot.root_file = root_bin
                root_raw = root_bin.read_bytes()
                slot.root_sz = len(root_raw)
                slot.root_sum = sum(root_raw) & 0xFFFFFFFF
                
                det = perform_deep_detect(root_raw)
                if det['found']:
                    slot.root_crc = zlib.crc32(det['payload']) & 0xFFFFFFFF
                    ext_root_dir = tmp_root / "root_fs"
                    extract_cramfs_root(det['payload'], ext_root_dir)
                    cfg = query_cfg_file(ext_root_dir)
                    if cfg and cfg.is_file():
                        slot.cfg_file = cfg
                        cfg_bytes = cfg.read_bytes()
                        slot.cfg_raw = cfg_bytes
                        mid, rcu = extract_modeid_rcutype(cfg_bytes.decode('latin-1'))
                        if idx == 1:
                            self.m1_orig.set(mid or "")
                            self.r1_orig.set(rcu or "")
                            self.m1_new.set(mid or "")
                            self.r1_new.set(rcu or "")
                        else:
                            self.m2_orig.set(mid or "")
                            self.r2_orig.set(rcu or "")
                            self.m2_new.set(mid or "")
                            self.r2_new.set(rcu or "")
                        self._log(f"   FS DETECTED! MODEID={mid}, RCUTYPE={rcu}")
        except Exception as e:
            self._log(f"[!] LOADING FAIL ON SLOT {idx}: {e}")

    def _convert_sw(self) -> None:
        self._log("================= START CONVERSION =================")
        if not self.s1.path or not self.s2.path:
            self._log("[!] Both Firmware files must be loaded.")
            return

        try:
            m1 = self.m1_new.get()
            r1 = self.r1_new.get()
            m2 = self.m2_new.get()
            r2 = self.r2_new.get()

            
            self._process_firmware(self.s1, m2, r2, 1)
          
            self._process_firmware(self.s2, m1, r1, 2)

            self._log("[*] PROCESS COMPLETE SUCCESSFULLY")
        except Exception as e:
            self._log(f"[!] PROCESS FAIL: {e}")

    def _process_firmware(self, slot: FwSlot, target_mid: str, target_rcu: str, index: int) -> None:
        if not slot.ext_dir or not slot.cfg_file:
            raise ValueError(f"Slot {index} structure incomplete")

        self._log(f"   Repacking ROOT fs for Slot {index} -> MODEID={target_mid}, RCUTYPE={target_rcu}")
        new_cfg, err = inject_modeid_rcutype_free(slot.cfg_raw, target_mid, target_rcu)
        if err:
            raise ValueError(err)
        
        slot.cfg_file.write_bytes(new_cfg)
        fs_dir = slot.ext_dir / "root_fs"
        
        new_cramfs = build_cramfs_image(fs_dir, slot.root_sz, slot.root_crc, slot.root_sum)
        slot.root_file.write_bytes(new_cramfs)
        
        out_name = slot.path.parent / f"CONVERTED_DRAGON_{index}.bin"
        repack_fw_dir(str(slot.ext_dir), str(out_name), slot.total_sum)
  
        reloaded = out_name.read_bytes()
        final_sum = sum(reloaded) & 0xFFFFFFFF
        self._log(f"   Slot {index} Compiled. Output sum32 match: {final_sum == slot.total_sum}")
        self._log(f"   SAVED TO: {out_name.name}")

if __name__ == "__main__":
    app = DragonNoirTool()
    app.win.mainloop()