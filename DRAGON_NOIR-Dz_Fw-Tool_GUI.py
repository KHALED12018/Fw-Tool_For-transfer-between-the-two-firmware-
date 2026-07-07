#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, struct, re, threading, time, zlib, tempfile, shutil, math, collections
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

CRAMFS_MAGIC = 0x28CD3D45
CRAMFS_BLOCK_SIZE = 4096
TABLE_MAGIC = b'\xaa\xbc\xde\xfa'
TBL_CRAMFS_MAGIC = b'\x45\x3d\xcd\x28'
TABLE_SIZE = 0x200
ENTRY_SIZE = 24
T_COUNT_OFF = 4
T_ENTRIES_OFF = 5
T_CRC32_BASE = 0x19D
T_WP_OFF = 0x1F9
T_CRCEN_OFF = 0x1FA
T_VERSION_OFF = 0x1FB
T_TABLECRC_OFF = 0x1FC
HYBRID_KERN_OFF = 0x40100
METADATA_SIZE = 0x100
METADATA_VERSION = 1
METADATA_F0 = 0x00020E00
METADATA_F2 = 0x00020000
MULTISW_MAGIC = b'mult_sw\x00'

def crc32_mpeg2(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= (byte << 24)
        for _ in range(8):
            if crc & 0x80000000: crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else: crc = (crc << 1) & 0xFFFFFFFF
    return crc

def patch_crc32(data: bytes, target_crc: int) -> bytes:
    poly = 0x04C11DB7
    def crc_step(crc, byte):
        crc ^= (byte << 24)
        for _ in range(8):
            if crc & 0x80000000: crc = ((crc << 1) ^ poly) & 0xFFFFFFFF
            else: crc = (crc << 1) & 0xFFFFFFFF
        return crc
    crc_base = 0xFFFFFFFF
    for byte in data[:-4]: crc_base = crc_step(crc_base, byte)
    crc_zero = crc_base
    for _ in range(4): crc_zero = crc_step(crc_zero, 0)
    y = target_crc ^ crc_zero
    matrix = []
    for pos in range(4):
        for bit in range(8):
            test_bytes = [0, 0, 0, 0]
            test_bytes[pos] = 1 << (7 - bit)
            c = 0
            for b in test_bytes: c = crc_step(c, b)
            matrix.append(c)
    system = []
    for r in range(32):
        row_val = 0
        for c in range(32):
            bit = (matrix[c] >> (31 - r)) & 1
            row_val |= (bit << (31 - c))
        bit_y = (y >> (31 - r)) & 1
        system.append((row_val, bit_y))
    augmented = list(system)
    for i in range(32):
        pivot_row = -1
        for r in range(i, 32):
            if (augmented[r][0] >> (31 - i)) & 1: pivot_row = r; break
        if pivot_row == -1: continue
        augmented[i], augmented[pivot_row] = augmented[pivot_row], augmented[i]
        for r in range(32):
            if r != i and ((augmented[r][0] >> (31 - i)) & 1):
                augmented[r] = (augmented[r][0] ^ augmented[i][0], augmented[r][1] ^ augmented[i][1])
    patch_val = 0
    for i in range(32): patch_val |= (augmented[i][1] << (31 - i))
    return data[:-4] + bytes([(patch_val >> 24) & 0xFF, (patch_val >> 16) & 0xFF, (patch_val >> 8) & 0xFF, patch_val & 0xFF])

class _Node:
    __slots__ = ('name', 'full_path', 'rel', 'is_dir', 'mode', 'uid', 'gid',
                 'children', 'inode_offset', 'entries_offset', 'entries_size',
                 'name_bytes', 'data_off_field', 'file_size')

class CramFSEngine:
    @staticmethod
    def find_cramfs(data: bytes) -> tuple:
        for i in range(0, len(data) - 4, 4):
            if struct.unpack_from('<I', data, i)[0] == CRAMFS_MAGIC: return i, data[i:]
        return -1, None

    @staticmethod
    def parse_inode(data: bytes, offset: int) -> tuple:
        w0, w1, w2 = struct.unpack_from('<III', data, offset)
        return w0 & 0xFFFF, (w0 >> 16) & 0xFFFF, (w1 >> 24) & 0xFF, w1 & 0xFFFFFF, (w2 & 0x3F) * 4, (w2 >> 6) * 4

    @staticmethod
    def decompress_file(data: bytes, data_off: int, file_size: int) -> bytes:
        if file_size == 0: return b''
        n = (file_size + CRAMFS_BLOCK_SIZE - 1) // CRAMFS_BLOCK_SIZE
        out, prev = b'', data_off + n * 4
        for i in range(n):
            if data_off + i * 4 + 4 > len(data): break
            ptr = struct.unpack_from('<I', data, data_off + i * 4)[0]
            if ptr > len(data): break
            chunk = data[prev:ptr]
            try: out += zlib.decompress(chunk)
            except: out += chunk
            prev = ptr
        return out[:file_size]

    @staticmethod
    def walk_cramfs(data: bytes, dir_off: int, dir_size: int, path: str, entries: list, depth: int = 0):
        if depth > 32: return
        pos, end = dir_off, min(dir_off + dir_size, len(data))
        while pos < end:
            if pos + 12 > len(data): break
            try: mode, uid, gid, size, name_len, data_off = CramFSEngine.parse_inode(data, pos)
            except: break
            inode_pos = pos; pos += 12
            if name_len == 0 or name_len > 256 or pos + name_len > len(data): break
            name_bytes = data[pos:pos + name_len]; pos += name_len
            name = name_bytes.decode('latin1', 'replace').rstrip('\x00')
            if not name or name in ('.', '..'): continue
            fpath = (path + '/' + name).lstrip('/')
            if ((mode >> 12) & 0xF) == 4:
                entries.append(('dir', fpath, mode, uid, gid, 0, inode_pos, data_off, size))
                if data_off > 0 and size > 0: CramFSEngine.walk_cramfs(data, data_off, size, fpath, entries, depth + 1)
            else:
                entries.append(('file', fpath, mode, uid, gid, size, inode_pos, data_off, 0))

    @staticmethod
    def extract(cramfs_data: bytes, output_dir: str) -> list:
        if len(cramfs_data) < 76: raise ValueError("CRAMFS data too small")
        if struct.unpack_from('<I', cramfs_data, 0)[0] != CRAMFS_MAGIC: raise ValueError("Invalid CRAMFS magic")
        _, _, _, root_size, _, root_off = CramFSEngine.parse_inode(cramfs_data, 64)
        entries = []
        CramFSEngine.walk_cramfs(cramfs_data, root_off, root_size, '', entries)
        os.makedirs(output_dir, exist_ok=True)
        for typ, path, mode, uid, gid, size, inode_pos, data_off, dir_size in entries:
            full_path = os.path.join(output_dir, path)
            if typ == 'dir': os.makedirs(full_path, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'wb') as f: f.write(CramFSEngine.decompress_file(cramfs_data, data_off, size))
        return entries

    @staticmethod
    def _pack_inode(mode: int, uid: int, gid: int, size: int, namelen_padded: int, data_off: int) -> bytes:
        return struct.pack('<III', ((uid & 0xFFFF) << 16) | (mode & 0xFFFF), ((gid & 0xFF) << 24) | (size & 0xFFFFFF), ((data_off // 4) << 6) | ((namelen_padded // 4) & 0x3F))

    @staticmethod
    def build_cramfs(root_dir: str, original_header: bytes = None, perm_map: dict = None, orig_size: int = 0) -> bytes:
        perm_map = perm_map or {}
        def scan(path, name, rel):
            node = _Node()
            node.name, node.full_path, node.rel, node.is_dir = name, path, rel, os.path.isdir(path)
            meta = perm_map.get(rel, {})
            node.mode = meta.get('mode', 0o40755 if node.is_dir else 0o100755)
            node.uid, node.gid, node.children = meta.get('uid', 0), meta.get('gid', 0), []
            if node.is_dir:
                for cn in sorted(os.listdir(path)):
                    node.children.append(scan(os.path.join(path, cn), cn, (rel + '/' + cn).lstrip('/')))
            return node
        root = scan(root_dir, '', '')
        offset_cursor, queue, inode_positions = 76, [root], []
        while queue:
            dnode = queue.pop(0)
            dnode.entries_offset, entries_size = offset_cursor, 0
            for child in dnode.children:
                child.inode_offset = offset_cursor
                raw_name = child.name.encode('latin1', 'replace')[:252]
                namelen_padded = max(4, ((len(raw_name) + 3) // 4) * 4)
                child.name_bytes = raw_name.ljust(namelen_padded, b'\x00')
                entry_size = 12 + namelen_padded
                offset_cursor += entry_size; entries_size += entry_size
                inode_positions.append(child)
                if child.is_dir: queue.append(child)
            dnode.entries_size = entries_size
            
        inode_table = bytearray(offset_cursor - 76)
        data_cursor, data_blob, total_blocks, total_files = offset_cursor, bytearray(), 0, 0
        
        for node in inode_positions:
            if node.is_dir: continue
            total_files += 1
            with open(node.full_path, 'rb') as f: filedata = f.read()
            size = len(filedata); node.file_size = size
            if size == 0: node.data_off_field = 0; continue
            n_blocks = (size + CRAMFS_BLOCK_SIZE - 1) // CRAMFS_BLOCK_SIZE
            total_blocks += n_blocks
            pad = (-data_cursor) % 4
            if pad: data_blob += b'\x00' * pad; data_cursor += pad
            ptr_array_offset = data_cursor
            compressed_blocks = [zlib.compress(filedata[i * CRAMFS_BLOCK_SIZE:(i + 1) * CRAMFS_BLOCK_SIZE], 9) for i in range(n_blocks)]
            running = ptr_array_offset + n_blocks * 4
            ptrs = [running + sum(len(compressed_blocks[j]) for j in range(i + 1)) for i in range(n_blocks)]
            for p in ptrs: data_blob += struct.pack('<I', p)
            for comp in compressed_blocks: data_blob += comp
            data_cursor += n_blocks * 4 + sum(len(c) for c in compressed_blocks)
            node.data_off_field = ptr_array_offset
            
        for node in inode_positions:
            pos = node.inode_offset - 76
            if node.is_dir: ib = CramFSEngine._pack_inode(node.mode, node.uid, node.gid, node.entries_size, len(node.name_bytes), node.entries_offset)
            else: ib = CramFSEngine._pack_inode(node.mode, node.uid, node.gid, node.file_size, len(node.name_bytes), node.data_off_field)
            inode_table[pos:pos + 12] = ib
            inode_table[pos + 12:pos + 12 + len(node.name_bytes)] = node.name_bytes
            
        root_ib = CramFSEngine._pack_inode((root.mode | 0o40000), root.uid, root.gid, root.entries_size, 0, root.entries_offset)
        total_size = 76 + len(root_ib) + len(inode_table) + len(data_blob)
        
        header = bytearray(original_header[:64]) if original_header and len(original_header) >= 64 else bytearray(64)
        if not original_header: header[16:32] = b'Compressed ROMFS'.ljust(16, b'\x00')
        struct.pack_into('<II', header, 0, CRAMFS_MAGIC, total_size)
        struct.pack_into('<II', header, 40, total_blocks, total_files + 1)
        
        image = bytearray(total_size)
        image[0:64] = header
        image[64:64 + len(root_ib)] = root_ib
        image[76:76 + len(inode_table)] = inode_table
        image[data_cursor : data_cursor + len(data_blob)] = data_blob
        
        if orig_size > 0 and orig_size > total_size:
            image += bytearray(b'\xFF' * (orig_size - total_size))
        
        struct.pack_into('<I', image, 32, 0)
        struct.pack_into('<I', image, 32, zlib.crc32(bytes(image)) & 0xFFFFFFFF)
        return bytes(image)

def build_metadata(folder_name, base_offset, table_flash_bytes):
    name = folder_name.encode('utf-8') if isinstance(folder_name, str) else folder_name
    meta = bytearray(b'\xff' * METADATA_SIZE)
    struct.pack_into('<IIII', meta, 0, 0, 0x10 + len(name) - 1, METADATA_VERSION, len(name))
    meta[16:16 + len(name)] = name
    a = 16 + len(name)
    struct.pack_into('<III', meta, a, METADATA_F0, base_offset, METADATA_F2)
    meta[a + 12] = 0x00
    tbl_off = a + 13; avail = METADATA_SIZE - tbl_off
    meta[tbl_off:tbl_off + avail] = table_flash_bytes[:avail]
    return bytes(meta)

def parse_metadata(data_bin):
    if len(data_bin) < METADATA_SIZE: return 0, data_bin
    meta = data_bin[-METADATA_SIZE:]
    if struct.unpack('<I', meta[0:4])[0] != 0: return 0, data_bin
    if struct.unpack('<I', meta[8:12])[0] not in (1, 2): return 0, data_bin
    nlen = struct.unpack('<I', meta[12:16])[0]
    if not (0 < nlen < 0xC0): return 0, data_bin
    tbl_off = 16 + nlen + 13
    if tbl_off + 4 > METADATA_SIZE or meta[tbl_off:tbl_off + 4] != TABLE_MAGIC: return 0, data_bin
    return struct.unpack('<I', meta[16 + nlen + 4:16 + nlen + 8])[0], data_bin[:-METADATA_SIZE]

def _fs_of(data): return 'CRAMFS' if data[:4] == TBL_CRAMFS_MAGIC else 'RAW'
_FS_FLAGS = {0x02: 'CRAMFS', 0x7F: 'MINIFS'}
def _fs_from_flags(flags, data=b''):
    fb = (flags >> 24) & 0xFF
    return _FS_FLAGS[fb] if fb in _FS_FLAGS else _fs_of(data)

def fmt_size(n):
    if n == 0: return '0 B'
    if n < 1024: return f'{n} B'
    if n < 1048576: return f'{n/1024:.1f} KB'
    return f'{n/1048576:.2f} MB'

def crc32b(data): return zlib.crc32(data) & 0xFFFFFFFF
def is_ff(data): return all(b == 0xFF for b in data)

def parse_table(tb):
    if len(tb) < TABLE_SIZE: raise ValueError('TABLE block too small')
    if tb[:4] != TABLE_MAGIC: raise ValueError('Invalid TABLE magic')
    count = tb[T_COUNT_OFF]
    if count == 0 or count > 16: raise ValueError(f'Invalid partition count: {count}')
    tcrc_s = struct.unpack('>I', tb[T_TABLECRC_OFF:T_TABLECRC_OFF + 4])[0]
    if tcrc_s != crc32b(tb[:T_TABLECRC_OFF]): raise ValueError('TABLE CRC Mismatch')
    entries = []
    for i in range(count):
        off = T_ENTRIES_OFF + i * ENTRY_SIZE; raw = tb[off:off + ENTRY_SIZE]
        name = raw[0:8].rstrip(b'\x00').decode('ascii', 'replace')
        if not name: break
        total, main, start, flags = struct.unpack('>IIII', raw[8:24])
        entries.append({'idx': i, 'name': name, 'total': total, 'main': main, 'start': start, 'flags': flags, 'fs': '', 'stored_crc': struct.unpack('>I', tb[T_CRC32_BASE + i*4:T_CRC32_BASE + i*4 + 4])[0], 'crc_en': bool((flags >> 16) & 0x80), 'crc_flag': (flags >> 16) & 0xFF, 'data': b''})
    return count, entries, bool(tb[T_WP_OFF]), bool(tb[T_CRCEN_OFF]), tb[T_VERSION_OFF], tcrc_s

def build_table(orig_tb, entries, new_mains, parts_data, new_totals=None, new_starts=None, platini_mode=False):
    new_totals, new_starts = new_totals or {}, new_starts or {}
    tb = bytearray(orig_tb[:TABLE_SIZE].ljust(TABLE_SIZE, b'\xff'))
    for i, e in enumerate(entries):
        nm, bo = e['name'], T_ENTRIES_OFF + i * ENTRY_SIZE
        if nm in new_totals: struct.pack_into('>I', tb, bo + 8, new_totals[nm])
        if nm in new_starts: struct.pack_into('>I', tb, bo + 16, new_starts[nm])
        if nm in new_mains: struct.pack_into('>I', tb, bo + 12, new_mains[nm])
    for i, e in enumerate(entries):
        nm, so, d = e['name'], T_CRC32_BASE + i * 4, parts_data.get(e['name'], b'')
        if e['stored_crc'] == 0: struct.pack_into('>I', tb, so, 0)
        elif nm == 'BOOT':
            crc_len = e['main'] if len(d) == e['total'] else len(d)
            struct.pack_into('>I', tb, so, crc32b(d[:crc_len]) if d else e['stored_crc'])
        else:
            calc_len = new_mains.get(nm, e['main'])
            if calc_len == 0 or calc_len > len(d): calc_len = len(d)
            struct.pack_into('>I', tb, so, crc32b(d[:calc_len]) if d else e['stored_crc'])
    struct.pack_into('>I', tb, T_TABLECRC_OFF, crc32b(bytes(tb[:T_TABLECRC_OFF])))
    return bytes(tb)

def find_table(fw):
    pos = 0
    while True:
        p = fw.find(TABLE_MAGIC, pos)
        if p == -1: raise ValueError('No TABLE magic found')
        if p + TABLE_SIZE <= len(fw) and 1 <= fw[p + T_COUNT_OFF] <= 16:
            try:
                count, entries, wp, crcen, ver, tcrc = parse_table(fw[p:p + TABLE_SIZE])
                t_start = next((e['start'] for e in entries if e['name'] == 'TABLE'), 0)
                base_offset = p - t_start
                for e in entries:
                    fo = e['start'] + base_offset
                    if e['total'] > 0 and fo < len(fw): e['data'] = fw[fo:fo + min(e['total'], len(fw) - fo)]
                    e['fs'] = _fs_from_flags(e.get('flags', 0), e['data'])
                return p, count, entries, wp, crcen, ver, tcrc, base_offset
            except ValueError: pass
        pos = p + 1

def _all_cramfs(fw):
    r, pos = [], 0
    while True:
        p = fw.find(TBL_CRAMFS_MAGIC, pos)
        if p == -1: break
        if p + 8 <= len(fw):
            sz = struct.unpack('<I', fw[p + 4:p + 8])[0]
            if 0x1000 < sz < len(fw) - p: r.append((p, sz))
        pos = p + 1
    return r

def detect_cramfs(fw):
    def _e(idx, name, start, total):
        d = fw[start:start + total]
        return {'idx': idx, 'name': name, 'total': total, 'main': total, 'start': start, 'flags': 0, 'fs': _fs_of(d), 'stored_crc': 0, 'data': d}
    if fw[0x100:0x104] == TBL_CRAMFS_MAGIC:
        lo = 0x100; ls = struct.unpack('<I', fw[lo + 4:lo + 8])[0]; le = lo + ls
        off = (le + 0xFF) & ~0xFF; ko = None
        while off < len(fw):
            if not is_ff(fw[off:off + 0x100]): ko = off; break
            off += 0x100
        if ko is None: raise ValueError('Could not locate KERNEL')
        rl = [(p, s) for p, s in _all_cramfs(fw) if p > le]
        if not rl: raise ValueError('Could not locate ROOT')
        ro, rs = rl[0]
        return [_e(0, 'HEADER', 0, lo), _e(1, 'LOGO', lo, ls), _e(2, 'KERNEL', ko, ro - ko), _e(3, 'ROOT', ro, rs), _e(4, 'DATA', ro + rs, len(fw) - (ro + rs))]
    if len(fw) >= 256:
        freq = collections.Counter(fw[:256])
        if (-sum(c / 256 * math.log2(c / 256) for c in freq.values())) > 7.0:
            cramfs_all = _all_cramfs(fw)
            if cramfs_all:
                ro, rs = cramfs_all[0]
                return [_e(0, 'KERNEL', 0, ro), _e(1, 'ROOT', ro, rs), _e(2, 'DATA', ro + rs, len(fw) - (ro + rs))]
    raise ValueError('No valid firmware structure identified')

def _parse_multisw(fw):
    slot_count = struct.unpack_from('<H', fw, 0x0C)[0]
    if slot_count == 0 or slot_count > 16: raise ValueError('Invalid MultSW slot count')
    slots = []
    for i in range(slot_count):
        base = struct.unpack_from('<I', fw, 0x110 + i * 4)[0] + 0x100
        tf = base + 0x17E00
        if tf + TABLE_SIZE > len(fw): continue
        try:
            count, entries, wp, crcen, ver, tcrc = parse_table(fw[tf:tf + TABLE_SIZE])
            for e in entries:
                file_off = base + e['start']
                if e['total'] > 0 and file_off < len(fw): e['data'] = fw[file_off:file_off + min(e['total'], len(fw) - file_off)]
                e['fs'] = _fs_from_flags(e.get('flags', 0), e['data'])
            slots.append({'idx': i, 'base': base, 'flash_size': 0x320000, 'table_off': tf, 'entries': entries, 'wp': wp, 'crcen': crcen, 'ver': ver, 'tcrc': tcrc})
        except ValueError: continue
    if not slots: raise ValueError('No valid MultSW slots found')
    return slots

def detect_firmware(fw):
    if fw[4:12] == MULTISW_MAGIC:
        try:
            s0 = _parse_multisw(fw)[0]
            return ('MULTISW', s0['table_off'], len(s0['entries']), s0['entries'], s0['wp'], s0['crcen'], s0['ver'], s0['tcrc'], len(_parse_multisw(fw)))
        except ValueError: pass
    try: return ('TABLE',) + find_table(fw)
    except ValueError: pass
    return ('CRAMFS', None, len(detect_cramfs(fw)), detect_cramfs(fw), False, False, 0, None, 0)

def unpack_firmware(fw_path, out_dir):
    with open(fw_path, 'rb') as f: fw = f.read()
    mode, tbl_off, count, entries, wp, crcen, ver, tcrc, base_offset = detect_firmware(fw)
    os.makedirs(out_dir, exist_ok=True)
    if mode == 'MULTISW':
        slots = _parse_multisw(fw)
        with open(os.path.join(out_dir, '_multisw_header.bin'), 'wb') as f: f.write(fw[:0x300])
        for i in range(len(slots) - 1):
            with open(os.path.join(out_dir, f'_multisw_gap_{i + 1}.bin'), 'wb') as f: f.write(fw[slots[i]['base'] + 0x320000:slots[i + 1]['base']])
        meta = struct.pack('<I', len(slots))
        for s in slots: meta += struct.pack('<II', s['base'], 0x320000)
        with open(os.path.join(out_dir, '_multisw_meta.bin'), 'wb') as f: f.write(meta)
        for s in slots:
            sw_dir = os.path.join(out_dir, f"SW{s['idx'] + 1}"); os.makedirs(sw_dir, exist_ok=True)
            for e in s['entries']:
                with open(os.path.join(sw_dir, f"{e['name']}.bin"), 'wb') as f: f.write(e['data'])
        return mode, slots[0]['table_off'], len(slots[0]['entries']), slots[0]['entries'], slots[0]['wp'], slots[0]['crcen'], slots[0]['ver'], slots[0]['tcrc'], len(slots)
    if mode == 'TABLE':
        orig_tb = fw[tbl_off:tbl_off + TABLE_SIZE]
        folder_name = os.path.splitext(os.path.basename(fw_path))[0] + '_extracted'
        for e in entries:
            data = orig_tb if e['name'] == 'TABLE' else (e['data'] + build_metadata(folder_name, base_offset, orig_tb) if (e['name'] == 'DATA' and base_offset > 0) else e['data'])
            with open(os.path.join(out_dir, f"{e['name']}.bin"), 'wb') as f: f.write(data)
    else:
        for e in entries:
            with open(os.path.join(out_dir, f"{e['name']}.bin"), 'wb') as f: f.write(e['data'])
    return mode, tbl_off, count, entries, wp, crcen, ver, tcrc, len(fw)

def _layout_from_folder(folder):
    tbl_path = os.path.join(folder, 'TABLE.bin')
    if os.path.exists(tbl_path):
        with open(tbl_path, 'rb') as f: tbl = f.read()
        count, entries, wp, crcen, ver, tcrc = parse_table(tbl)
        layout = {e['name']: {'offset': e['start'], 'total': e['total'], 'main': e['main'], 'flags': e['flags']} for e in entries}
        return 'TABLE', layout, entries, layout.get('TABLE', {}).get('offset'), max(0x800000, max(v['offset'] + v['total'] for v in layout.values())), wp, crcen, ver, tcrc
    pp = [nm for nm in ['HEADER', 'LOGO', 'KERNEL', 'ROOT', 'DATA'] if os.path.exists(os.path.join(folder, f'{nm}.bin'))]
    if not pp: raise FileNotFoundError('No valid .bin partition files found in folder.')
    sizes = {nm: os.path.getsize(os.path.join(folder, f'{nm}.bin')) for nm in pp}
    layout = {}
    if 'HEADER' not in pp and 'LOGO' not in pp:
        layout['KERNEL'] = {'offset': 0, 'total': sizes.get('KERNEL', 0), 'main': sizes.get('KERNEL', 0), 'flags': 0}
        layout['ROOT'] = {'offset': layout['KERNEL']['total'], 'total': sizes.get('ROOT', 0), 'main': sizes.get('ROOT', 0), 'flags': 0}
        layout['DATA'] = {'offset': layout['ROOT']['offset'] + layout['ROOT']['total'], 'total': sizes.get('DATA', 0), 'main': sizes.get('DATA', 0), 'flags': 0}
    else:
        layout['HEADER'] = {'offset': 0x000, 'total': sizes.get('HEADER', 0x100), 'main': sizes.get('HEADER', 0x100), 'flags': 0}
        layout['LOGO'] = {'offset': 0x100, 'total': sizes.get('LOGO', 0), 'main': sizes.get('LOGO', 0), 'flags': 0}
        layout['KERNEL'] = {'offset': HYBRID_KERN_OFF, 'total': sizes.get('KERNEL', 0), 'main': sizes.get('KERNEL', 0), 'flags': 0}
        layout['ROOT'] = {'offset': HYBRID_KERN_OFF + sizes.get('KERNEL', 0), 'total': sizes.get('ROOT', 0), 'main': sizes.get('ROOT', 0), 'flags': 0}
        layout['DATA'] = {'offset': layout['ROOT']['offset'] + layout['ROOT']['total'], 'total': sizes.get('DATA', 0), 'main': sizes.get('DATA', 0), 'flags': 0}
    return 'CRAMFS', layout, [], None, 0, False, False, 0, None

def repack_firmware(folder, out_fw_path):
    meta_path, header_path = os.path.join(folder, '_multisw_meta.bin'), os.path.join(folder, '_multisw_header.bin')
    if os.path.exists(meta_path) and os.path.exists(header_path):
        meta_raw = open(meta_path, 'rb').read()
        slot_count = struct.unpack_from('<I', meta_raw, 0)[0]
        si = [{'base': struct.unpack_from('<II', meta_raw, 4 + i * 8)[0], 'flash_size': struct.unpack_from('<II', meta_raw, 4 + i * 8)[1]} for i in range(slot_count)]
        fw_buf = bytearray(b'\xFF' * (si[-1]['base'] + si[-1]['flash_size']))
        hb = open(header_path, 'rb').read(); fw_buf[:len(hb)] = hb
        for i in range(slot_count - 1):
            gp = os.path.join(folder, f'_multisw_gap_{i + 1}.bin')
            if os.path.exists(gp):
                gd = open(gp, 'rb').read()
                fw_buf[si[i]['base'] + si[i]['flash_size']:si[i]['base'] + si[i]['flash_size'] + len(gd)] = gd
        le, lw, lc, lv, lt = [], False, False, 0, None
        for i, s_info in enumerate(si):
            sw_dir = os.path.join(folder, f"SW{i + 1}")
            with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tf: tf_path = tf.name
            try:
                r = repack_firmware(sw_dir, tf_path)
                sd = open(tf_path, 'rb').read()
                fw_buf[s_info['base']:s_info['base'] + min(s_info['flash_size'], len(sd))] = sd[:min(s_info['flash_size'], len(sd))]
                if i == 0 and r[0] in ('TABLE', 'MULTISW'): _, _, le, lw, lc, lv, lt = r
            finally:
                try: os.unlink(tf_path)
                except: pass
        with open(out_fw_path, 'wb') as f: f.write(fw_buf)
        return 'MULTISW', len(le), le, lw, lc, lv, lt
    orig_path = os.path.join(folder, '_multisw_original.bin')
    if os.path.exists(orig_path):
        orig = open(orig_path, 'rb').read()
        open(out_fw_path, 'wb').write(orig)
        s0 = _parse_multisw(orig)[0]
        for e in s0['entries']: e['fs'] = _fs_from_flags(e.get('flags', 0), e['data'])
        return 'MULTISW', len(s0['entries']), s0['entries'], s0['wp'], s0['crcen'], s0['ver'], s0['tcrc']
    mode, layout, tbl_entries, tbl_off, fw_size, wp, crcen, ver, tcrc = _layout_from_folder(folder)
    parts_data = {nm: open(os.path.join(folder, f'{nm}.bin'), 'rb').read() for nm in layout}
    if mode == 'TABLE':
        bo, parts_data['DATA'] = parse_metadata(parts_data.get('DATA', b''))
        sm = parts_data.get('DATA', b'')[-METADATA_SIZE:] if bo > 0 else b''
        ordered = sorted(layout.keys(), key=lambda x: layout[x]['offset'])
        req = {nm: layout[nm]['total'] for nm in ordered}
        for nm in ordered:
            if nm == 'DATA' and len(parts_data.get(nm, b'')) > layout[nm]['total']: raise ValueError("DATA exceeds space allocate.")
            if nm != 'DATA' and parts_data.get(nm, b'') and len(parts_data[nm]) > layout[nm]['total']:
                req[nm] = (len(parts_data[nm]) + 0x10000 - 1) & ~(0x10000 - 1)
        if sum(req.values()) > fw_size:
            d_nm = ordered[-1]
            lnf = next((i + 1 for i in range(len(parts_data[d_nm]) - 1, -1, -1) if parts_data[d_nm][i] != 0xFF), 0)
            md = max(0x10000, (lnf + 0x10000 - 1) & ~(0x10000 - 1))
            if fw_size - sum(req[n] for n in ordered if n != d_nm) < md: raise ValueError("Flash structure limit overflowed.")
            req[d_nm] = fw_size - sum(req[n] for n in ordered if n != d_nm)
        cursor = layout[ordered[0]]['offset']
        for nm in ordered:
            layout[nm]['offset'], layout[nm]['total'] = cursor, req[nm]
            if req[nm] > 0: cursor += req[nm]
        nm_mains = {}
        for nm, d in parts_data.items():
            if layout[nm]['main'] == 0: nm_mains[nm] = 0
            elif len(d) >= len(open(os.path.join(folder, f'{nm}.bin'), 'rb').read()):
                nm_mains[nm] = len(open(os.path.join(folder, f'{nm}.bin'), 'rb').read())
            else: nm_mains[nm] = layout[nm]['main']
        f_size = max(layout[nm]['offset'] + min(len(parts_data[nm]), layout[nm]['total']) for nm in layout if layout[nm]['total'] > 0)
        fw_buf = bytearray(b'\xFF' * f_size)
        for nm, d in parts_data.items(): fw_buf[layout[nm]['offset']:layout[nm]['offset'] + min(len(d), f_size)] = d[:min(len(d), f_size)]
        orig_tb = open(os.path.join(folder, 'TABLE.bin'), 'rb').read()
        _, o_ent, _, _, _, _ = parse_table(orig_tb)
        new_tb = build_table(orig_tb, o_ent, nm_mains, parts_data, {n: layout[n]['total'] for n in layout}, {n: layout[n]['offset'] for n in layout}, platini_mode=(bo > 0))
        fw_buf[layout.get('TABLE', {}).get('offset', 0):layout.get('TABLE', {}).get('offset', 0) + TABLE_SIZE] = new_tb
        with open(out_fw_path, 'wb') as f:
            f.write(fw_buf)
            if sm: f.write(sm)
        _, e_out, w_out, c_out, v_out, t_out = parse_table(new_tb)
        for e in e_out: e['fs'], e['data'] = _fs_from_flags(e.get('flags', 0), parts_data.get(e['name'], b'')), parts_data.get(e['name'], b'')
        return 'TABLE', len(e_out), e_out, w_out, c_out, v_out, t_out
    overflow = any(len(parts_data.get(n, b'')) > layout[n]['total'] for n in layout if layout[n]['total'] > 0)
    if overflow:
        bs, lgs, ks, rs, ds = len(parts_data.get('HEADER', b'')), len(parts_data.get('LOGO', b'')), len(parts_data.get('KERNEL', b'')), len(parts_data.get('ROOT', b'')), len(parts_data.get('DATA', b''))
        layout = {'HEADER': {'offset': 0, 'total': bs}, 'LOGO': {'offset': 0x100, 'total': lgs}, 'KERNEL': {'offset': HYBRID_KERN_OFF, 'total': ks}, 'ROOT': {'offset': HYBRID_KERN_OFF + ks, 'total': rs}, 'DATA': {'offset': HYBRID_KERN_OFF + ks + rs, 'total': ds}}
        rm = 'HYBRID'
    else: rm = 'CRAMFS'
    ts = max(v['offset'] + v['total'] for v in layout.values() if v['total'] > 0)
    fb = bytearray(b'\xFF' * ts)
    for nm, inf in layout.items():
        if parts_data.get(nm): fb[inf['offset']:inf['offset'] + min(len(parts_data[nm]), ts)] = parts_data[nm][:min(len(parts_data[nm]), ts)]
    if 'LOGO' in parts_data: fb[0x100 + len(parts_data['LOGO']):HYBRID_KERN_OFF] = b'\xFF' * (HYBRID_KERN_OFF - (0x100 + len(parts_data['LOGO'])))
    with open(out_fw_path, 'wb') as f: f.write(fb)
    eo = [{'idx': i, 'name': n, 'total': layout[n]['total'], 'main': layout[n]['total'], 'start': layout[n]['offset'], 'flags': 0, 'fs': _fs_of(parts_data.get(n, b'')), 'stored_crc': 0, 'data': parts_data.get(n, b'')} for i, n in enumerate(layout)]
    return rm, len(eo), eo, False, False, 0, None

def safe_replace_root(original_root_bytes, new_root_bytes):
    orig_size = len(original_root_bytes)
    new_size = len(new_root_bytes)
    if new_size == orig_size: return new_root_bytes
    elif new_size < orig_size: return new_root_bytes + b'\xff' * (orig_size - new_size)
    else: return new_root_bytes[:orig_size]

def safe_replace_partition(original_part_bytes, new_part_bytes):
    orig_size = len(original_part_bytes)
    new_size = len(new_part_bytes)
    if new_size >= orig_size: return new_part_bytes[:orig_size]
    return new_part_bytes + b'\xff' * (orig_size - new_size)

class FirmwareToolGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Dragon_Noir Suite v9.9")
        self.geometry("850x650")
        self.configure(bg="#0d0d13")
        
        self.temp_dir = tempfile.mkdtemp(prefix="dn_")
        self.extracted_root = None
        self.perm_map = {}
        self.src_path = tk.StringVar()
        self.tgt_path = tk.StringVar()
        self.out_path = tk.StringVar()
        self.logo_path = tk.StringVar()
        self.rcu = tk.StringVar(value="53")
        self.model = tk.StringVar(value="DRAGON_NOIR-DZ")
        self.pt_fw_path = tk.StringVar()
        self.pt_dir_path = tk.StringVar()
        self.pt_fw_data = b''
        self.pt_entries = []
        self.pt_mode = None
        self.pt_tbl_off = 0
        self.pt_base_offset = 0
        
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook', background='#0d0d13', borderwidth=0)
        style.configure('TNotebook.Tab', background='#1a1a24', foreground='#ffffff', padding=[10, 4])
        style.map('TNotebook.Tab', background=[('selected', '#ff4400')], foreground=[('selected', '#ffffff')])
        
        header = tk.Frame(self, bg="#14141f", height=50)
        header.pack(fill="x", padx=10, pady=5)
        tk.Label(header, text="DRAGON_NOIR-Dz_FW-TOOL", font=("Arial", 14, "bold"), fg="#ff4400", bg="#14141f").pack(pady=2)
        
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.tab1 = tk.Frame(self.nb, bg="#0d0d13")
        self.tab2 = tk.Frame(self.nb, bg="#0d0d13")
        self.nb.add(self.tab1, text="  CFG / Logo Platform  ")
        self.nb.add(self.tab2, text="  Partition Advanced Tool  ")
        
        self._init_tab1()
        self._init_tab2()
        
        console_frame = tk.Frame(self, bg="#0d0d13")
        console_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_status = tk.Label(console_frame, text="Status: Ready", font=("Arial", 10, "bold"), fg="#00ff88", bg="#0d0d13")
        self.lbl_status.pack(anchor="w", padx=5)
        
        self.pbar = ttk.Progressbar(console_frame, orient="horizontal", mode="determinate")
        self.pbar.pack(fill="x", padx=5, pady=2)
        
        self.log_box = tk.Text(console_frame, height=8, font=("Courier", 9), bg="#000000", fg="#ffffff", state="disabled")
        self.log_box.pack(fill="both", padx=5, pady=2)
        
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log("Engine Initialized successfully. Ready for Pydroid3.")

    def _init_tab1(self):
        f = tk.Frame(self.tab1, bg="#14141f", bd=1, relief="solid")
        f.pack(fill="x", padx=10, pady=10)
        
        self._row(f, "Source Firmware:", self.src_path, self.b_src, 0)
        self._row(f, "Target Firmware:", self.tgt_path, self.b_tgt, 1)
        self._row(f, "Output Binary:", self.out_path, self.b_out, 2)
        self._row(f, "Optional Logo:", self.logo_path, self.b_logo, 3)
        
        f2 = tk.Frame(self.tab1, bg="#14141f", bd=1, relief="solid")
        f2.pack(fill="x", padx=10, pady=5)
        tk.Label(f2, text="RCUTYPE:", fg="#ffffff", bg="#14141f").grid(row=0, column=0, padx=5, pady=5)
        tk.Entry(f2, textvariable=self.rcu, bg="#2a2a3a", fg="#ffffff", insertbackground="white", width=12).grid(row=0, column=1, padx=5, pady=5)
        tk.Label(f2, text="MODEID:", fg="#ffffff", bg="#14141f").grid(row=0, column=2, padx=5, pady=5)
        tk.Entry(f2, textvariable=self.model, bg="#2a2a3a", fg="#ffffff", insertbackground="white", width=25).grid(row=0, column=3, padx=5, pady=5)
        
        f3 = tk.Frame(self.tab1, bg="#0d0d13")
        f3.pack(fill="x", padx=10, pady=10)
        tk.Button(f3, text="Parse Target CFG", bg="#2a2a3a", fg="#ffffff", command=self.parse_target_cfg).pack(side="left", padx=2)
        tk.Button(f3, text="Extract ROOT", bg="#2a2a3a", fg="#ffffff", command=self.extract_root).pack(side="left", padx=2)
        tk.Button(f3, text="Clear Cache", bg="#4a1a24", fg="#ffffff", command=self.cleanup).pack(side="left", padx=2)
        tk.Button(f3, text="EXECUTE CONVERSION", bg="#00aa00", fg="#000000", font=("Arial", 10, "bold"), command=self.process).pack(side="right", fill="x", expand=True, padx=5)

    def _init_tab2(self):
        f = tk.Frame(self.tab2, bg="#14141f")
        f.pack(fill="x", padx=10, pady=5)
        tk.Button(f, text="Load Firmware", bg="#1f3d52", fg="#ffffff", command=self.pt_open_firm).pack(side="left", padx=5, pady=5)
        self.pt_lbl = tk.Label(f, text="No File Loaded", fg="#888888", bg="#14141f", anchor="w")
        self.pt_lbl.pack(side="left", fill="x", expand=True, padx=5)
        tk.Button(f, text="Map Folder", bg="#1f3d52", fg="#ffffff", command=self.pt_open_dir).pack(side="right", padx=5, pady=5)
        
        f2 = tk.Frame(self.tab2, bg="#0d0d13")
        f2.pack(fill="x", padx=10, pady=2)
        self.btn_pt_un = tk.Button(f2, text="Unpack Image", bg="#2a2a3a", fg="#ffffff", state="disabled", command=self.pt_do_unpack)
        self.btn_pt_un.pack(side="left", padx=5)
        self.btn_pt_re = tk.Button(f2, text="Repack from Folder", bg="#125237", fg="#ffffff", state="disabled", command=self.pt_do_repack)
        self.btn_pt_re.pack(side="right", padx=5)
        
        cols = ('ID', 'NAME', 'FS', 'CRC32', 'START', 'SIZE', 'USED', 'Use%', 'CRC_EN')
        style = ttk.Style()
        style.configure('Treeview', background='#16161a', foreground='#ffffff', fieldbackground='#16161a', rowheight=20)
        style.configure('Treeview.Heading', background='#25252b', foreground='#ff4400')
        
        self.tree = ttk.Treeview(self.tab2, columns=cols, show='headings', height=8)
        cw = {'ID': 30, 'NAME': 80, 'FS': 70, 'CRC32': 80, 'START': 80, 'SIZE': 70, 'USED': 70, 'Use%': 50, 'CRC_EN': 50}
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=cw.get(c, 70), anchor='center')
        self.tree.pack(fill="both", expand=True, padx=10, pady=5)

    def _row(self, master, label, var, cmd, r):
        tk.Label(master, text=label, fg="#ffffff", bg="#14141f", anchor="w", width=15).grid(row=r, column=0, padx=5, pady=4, sticky="w")
        tk.Entry(master, textvariable=var, bg="#2a2a3a", fg="#ffffff", insertbackground="white").grid(row=r, column=1, padx=5, pady=4, sticky="ew")
        tk.Button(master, text="...", bg="#3a3a4a", fg="#ffffff", width=3, command=cmd).grid(row=r, column=2, padx=5, pady=4)
        master.grid_columnconfigure(1, weight=1)

    def log(self, text, color="#ffffff"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}] {text}\n")
        self.log_box.configure(state="disabled")
        self.log_box.see("end")

    def b_src(self):
        p = filedialog.askopenfilename()
        if p: self.src_path.set(p)

    def b_tgt(self):
        p = filedialog.askopenfilename()
        if p: self.tgt_path.set(p)

    def b_out(self):
        p = filedialog.asksaveasfilename(defaultextension=".bin")
        if p: self.out_path.set(p)

    def b_logo(self):
        p = filedialog.askopenfilename()
        if p: self.logo_path.set(p)

    def cleanup(self):
        if self.extracted_root and os.path.exists(self.extracted_root):
            shutil.rmtree(self.extracted_root, ignore_errors=True)
        self.extracted_root = None
        self.perm_map = {}
        self.log("Cache storage cleared.")

    def on_close(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.destroy()

    def parse_target_cfg(self):
        t = self.tgt_path.get()
        if not t or not os.path.exists(t): return
        def run():
            try:
                self.log("Parsing metadata config...")
                with open(t, "rb") as f: data = f.read()
                off, cramfs = CramFSEngine.find_cramfs(data)
                if off == -1: return
                ex = os.path.join(self.temp_dir, 'cfg_p')
                CramFSEngine.extract(cramfs, ex)
                cfg = next((p for p in [os.path.join(ex, 'etc', 'gx.cfg'), os.path.join(ex, 'etc', 'gui.cfg')] if os.path.exists(p)), None)
                if cfg:
                    with open(cfg, 'r', encoding='utf-8', errors='ignore') as f: content = f.read()
                    r = re.search(rf'^RCUTYPE\s*=\s*([^;\n]+);', content, re.M | re.I)
                    m = re.search(rf'^MODEID\s*=\s*([^;\n]+);', content, re.M | re.I)
                    if r: self.rcu.set(r.group(1).strip())
                    if m: self.model.set(m.group(1).strip())
                    self.log(f"Parsed Configs: RCUTYPE={self.rcu.get()} | MODEID={self.model.get()}")
                shutil.rmtree(ex, ignore_errors=True)
            except Exception as e: self.log(f"Error: {e}")
        threading.Thread(target=run, daemon=True).start()

    def extract_root(self):
        t = self.tgt_path.get()
        if not t or not os.path.exists(t): return
        def run():
            try:
                self.log("Extracting root system files...")
                with open(t, "rb") as f: data = f.read()
                off, cramfs = CramFSEngine.find_cramfs(data)
                if off == -1: return
                self.extracted_root = os.path.join(self.temp_dir, 'ex_root')
                shutil.rmtree(self.extracted_root, ignore_errors=True)
                entries = CramFSEngine.extract(cramfs, self.extracted_root)
                self.perm_map = {p: {'mode': m, 'uid': u, 'gid': g} for t, p, m, u, g, s, ip, do, ds in entries}
                self.log(f"Extracted nodes successfully: {len(entries)}")
            except Exception as e: self.log(f"Extraction failed: {e}")
        threading.Thread(target=run, daemon=True).start()

    def modify_cfg_file(self, path, mods):
        try:
            if not os.path.exists(path):
                with open(path, 'w', newline='\n') as f:
                    for k, v in mods.items(): f.write(f"{k}={v};\n")
                return True
            sz = os.path.getsize(path)
            with open(path, 'r', errors='ignore') as f: lines = f.readlines()
            nl = []; app = set()
            for l in lines:
                st = l.strip(); rep = False
                for k, v in mods.items():
                    if re.match(rf'^{re.escape(k)}\s*=', st, re.I):
                        nl.append(f"{k}={v};\n"); app.add(k); rep = True; break
                if not rep: nl.append(l)
            for k, v in mods.items():
                if k not in app: nl.append(f"{k}={v};\n")
            nb = ''.join(nl).encode('utf-8')
            if len(nb) > sz: nb = nb[:sz]
            else: nb += b'\n' * (sz - len(nb))
            with open(path, 'wb') as f: f.write(nb)
            return True
        except: return False

    def process(self):
        src, tgt, out, logo = self.src_path.get(), self.tgt_path.get(), self.out_path.get(), self.logo_path.get()
        if not src or not tgt or not out: return
        def run():
            try:
                self.lbl_status.config(text="Processing...", fg="#ffaa00")
                self.pbar.config(value=20)
                with open(src, 'rb') as f: src_fw = bytearray(f.read())
                
                mode, tbl_off, count, entries, wp, crcen, ver, tcrc, base_off = detect_firmware(bytes(src_fw))
                r_entry = next((e for e in entries if e['name'] == 'ROOT'), None)
                l_entry = next((e for e in entries if e['name'] == 'LOGO'), None)
                if not r_entry: raise Exception("No ROOT partition detected.")
                
                r_off = r_entry['start'] + (base_off if mode == 'TABLE' else 0)
                r_size = r_entry['total']
                orig_root = bytes(src_fw[r_off:r_off + r_size])
                
                with open(tgt, 'rb') as f: tgt_fw = f.read()
                t_off, t_cramfs = CramFSEngine.find_cramfs(tgt_fw)
                ex_dir = os.path.join(self.temp_dir, 'proc_root')
                shutil.rmtree(ex_dir, ignore_errors=True)
                t_entries = CramFSEngine.extract(t_cramfs, ex_dir)
                t_perm = {p: {'mode': m, 'uid': u, 'gid': g} for t, p, m, u, g, s, ip, do, ds in t_entries}
                
                mods = {'RCUTYPE': self.rcu.get(), 'MODEID': self.model.get()}
                for cf in [os.path.join(ex_dir, 'etc', 'gx.cfg'), os.path.join(ex_dir, 'etc', 'gui.cfg')]:
                    if os.path.exists(cf): self.modify_cfg_file(cf, mods)
                
                new_root = CramFSEngine.build_cramfs(ex_dir, orig_root[:64] if len(orig_root)>=64 else None, t_perm, orig_size=r_size)
                new_root = safe_replace_root(orig_root, new_root)
                src_fw[r_off:r_off + r_size] = new_root
                
                if mode == 'TABLE' and tbl_off > 0:
                    r_idx = r_entry['idx']
                    real_cramfs_len = struct.unpack('<I', new_root[4:8])[0]
                    if real_cramfs_len == 0 or real_cramfs_len > r_size: real_cramfs_len = r_size
                    
                    struct.pack_into('>I', src_fw, tbl_off + T_ENTRIES_OFF + r_idx * ENTRY_SIZE + 12, real_cramfs_len)
                    new_r_crc = crc32b(bytes(new_root[:real_cramfs_len]))
                    struct.pack_into('>I', src_fw, tbl_off + T_CRC32_BASE + r_idx * 4, new_r_crc)
                    
                    new_t_crc = crc32b(bytes(src_fw[tbl_off : tbl_off + T_TABLECRC_OFF]))
                    struct.pack_into('>I', src_fw, tbl_off + T_TABLECRC_OFF, new_t_crc)
                    self.log("Synchronized real CramFS size and patched protections in TABLE block.")
                
                if logo and os.path.exists(logo) and l_entry:
                    with open(logo, 'rb') as f: l_data = f.read()
                    l_off = l_entry['start'] + (base_off if mode == 'TABLE' else 0)
                    src_fw[l_off:l_off + l_entry['total']] = safe_replace_partition(bytes(src_fw[l_off:l_off + l_entry['total']]), l_data)
                
                with open(out, 'wb') as f: f.write(src_fw)
                self.pbar.config(value=100)
                self.lbl_status.config(text="Finished Successful", fg="#00ff88")
                self.log(f"Compiled: {fmt_size(len(src_fw))} | Protections Fixed Safely.")
                messagebox.showinfo("Success", "Process Completed successfully.")
            except Exception as e:
                self.log(f"Error executing logic: {e}")
                self.lbl_status.config(text="Failed", fg="#ff0044")
        threading.Thread(target=run, daemon=True).start()

    def pt_open_firm(self):
        p = filedialog.askopenfilename()
        if not p: return
        self.pt_fw_path.set(p)
        self.pt_lbl.config(text=os.path.basename(p), fg="#00ff88")
        def run():
            try:
                with open(p, 'rb') as f: self.pt_fw_data = f.read()
                self.pt_mode, self.pt_tbl_off, count, self.pt_entries, wp, crcen, ver, tcrc, self.pt_base_offset = detect_firmware(self.pt_fw_data)
                for item in self.tree.get_children(): self.tree.delete(item)
                for e in self.pt_entries:
                    d = e['data']; u = len(d.rstrip(b'\xff')) if d else 0
                    pct = (u / e['total'] * 100) if e['total'] > 0 else 0
                    self.tree.insert('', 'end', values=(e['idx'], e['name'], e.get('fs', ''), f"{e['stored_crc']:08X}" if e['stored_crc'] else "—", f"0x{e['start']:X}", fmt_size(e['total']), fmt_size(u), f"{pct:.0f}%", "Yes" if e.get('crc_en') else "No"))
                self.btn_pt_un.config(state="normal")
                self.btn_pt_re.config(state="disabled")
            except Exception as e: self.log(f"Layout Error: {e}")
        threading.Thread(target=run, daemon=True).start()

    def pt_open_dir(self):
        p = filedialog.askdirectory()
        if not p: return
        self.pt_dir_path.set(p)
        self.pt_lbl.config(text=f"Folder: {os.path.basename(p)}", fg="#00ffff")
        self.btn_pt_re.config(state="normal")
        self.btn_pt_un.config(state="disabled")

    def pt_do_unpack(self):
        p = self.pt_fw_path.get()
        out = filedialog.askdirectory()
        if not p or not out: return
        def run():
            try:
                unpack_firmware(p, out)
                self.log(f"Unpacked partitions into: {out}")
                messagebox.showinfo("Done", "Unpacked successfully.")
            except Exception as e: self.log(f"Error unpacking: {e}")
        threading.Thread(target=run, daemon=True).start()

    def pt_do_repack(self):
        d = self.pt_dir_path.get()
        out = filedialog.asksaveasfilename(defaultextension=".bin")
        if not d or not out: return
        def run():
            try:
                repack_firmware(d, out)
                self.log(f"Repacked cleanly to: {out}")
                messagebox.showinfo("Done", "Repacked successfully.")
            except Exception as e: self.log(f"Error repacking: {e}")
        threading.Thread(target=run, daemon=True).start()

if __name__ == "__main__":
    app = FirmwareToolGUI()
    app.mainloop()