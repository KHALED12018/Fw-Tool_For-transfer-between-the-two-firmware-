from __future__ import annotations
import os
import sys
import struct
import zlib
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

class CramFSNode:
    def __init__(self):
        self.mode = 0
        self.uid = 0
        self.size = 0
        self.gid = 0
        self.namelen = 0
        self.offset = 0
        self.name = ""
        self.children = []
        self.data_offset = 0

class DragonNoirFwTool:
    def __init__(self, master_window: tk.Tk):
        self.root = master_window
        self.root.title("DRAGON_NOIR GX6605S FW TOOL")
        self.root.geometry("850x700")
        self.root.configure(bg="#11161B")
        self.root.resizable(False, False)
        
        self.fw_path_1 = tk.StringVar()
        self.rcu_val_1 = tk.StringVar()
        self.mode_val_1 = tk.StringVar()
        
        self.fw_path_2 = tk.StringVar()
        self.rcu_val_2 = tk.StringVar()
        self.mode_val_2 = tk.StringVar()
        
        self.file_data_1 = b""
        self.file_data_2 = b""
        self.cramfs_offset_1 = -1
        self.cramfs_offset_2 = -1
        self.cramfs_size_1 = 0
        self.cramfs_size_2 = 0
        self.table_data_1 = b""
        self.table_data_2 = b""
        self.table_offset_1 = -1
        self.table_offset_2 = -1

        self._build_retro_ui()
        self._log_msg("SYSTEM: Engine online and ready.")

    def _build_retro_ui(self):
        main_container = tk.Frame(self.root, bg="#11161B", bd=3, relief="groove")
        main_container.place(relx=0.01, rely=0.01, relwidth=0.98, relheight=0.98)
        
        title_lbl = tk.Label(
            main_container, 
            text="::: DRAGON NOIR GX6605S CONVERTER & REPACKER :::", 
            bg="#11161B", 
            fg="#00FF66", 
            font=("Courier New", 12, "bold")
        )
        title_lbl.pack(pady=8)

        f1_frame = tk.LabelFrame(
            main_container, 
            text=" BASE FIRMWARE (FILE 1) ", 
            bg="#182026", 
            fg="#FFFFFF", 
            font=("Courier New", 9, "bold"), 
            bd=2, 
            relief="groove"
        )
        f1_frame.pack(padx=10, pady=5, fill="x")

        f1_row1 = tk.Frame(f1_frame, bg="#182026")
        f1_row1.pack(fill="x", padx=6, pady=4)
        
        tk.Label(f1_row1, text="Path:", bg="#182026", fg="#A0B0C0", font=("Courier New", 9)).pack(side="left")
        ent_f1 = tk.Entry(f1_row1, textvariable=self.fw_path_1, bg="#0C1013", fg="#FFFFFF", bd=2, relief="sunken", font=("Courier New", 9))
        ent_f1.pack(side="left", fill="x", expand=True, padx=6)
        btn_f1 = tk.Button(f1_row1, text=" BROWSE ", command=self._load_file_1, bg="#2C3A47", fg="#00FF66", bd=2, relief="raised", font=("Courier New", 8, "bold"))
        btn_f1.pack(side="right")

        f1_row2 = tk.Frame(f1_frame, bg="#182026")
        f1_row2.pack(fill="x", padx=6, pady=4)
        
        tk.Label(f1_row2, text="RCUTYPE (Remote):", bg="#182026", fg="#00FF66", font=("Courier New", 9)).pack(side="left")
        ent_rcu1 = tk.Entry(f1_row2, textvariable=self.rcu_val_1, bg="#0C1013", fg="#FFFFFF", bd=2, relief="sunken", font=("Courier New", 9, "bold"), width=15)
        ent_rcu1.pack(side="left", padx=6)

        tk.Label(f1_row2, text="MODEID (Device ID):", bg="#182026", fg="#00FF66", font=("Courier New", 9)).pack(side="left", padx=(15, 0))
        ent_mode1 = tk.Entry(f1_row2, textvariable=self.mode_val_1, bg="#0C1013", fg="#FFFFFF", bd=2, relief="sunken", font=("Courier New", 9, "bold"), width=15)
        ent_mode1.pack(side="left", padx=6)

        f2_frame = tk.LabelFrame(
            main_container, 
            text=" DONOR FIRMWARE (FILE 2) ", 
            bg="#182026", 
            fg="#FFFFFF", 
            font=("Courier New", 9, "bold"), 
            bd=2, 
            relief="groove"
        )
        f2_frame.pack(padx=10, pady=5, fill="x")

        f2_row1 = tk.Frame(f2_frame, bg="#182026")
        f2_row1.pack(fill="x", padx=6, pady=4)
        
        tk.Label(f2_row1, text="Path:", bg="#182026", fg="#A0B0C0", font=("Courier New", 9)).pack(side="left")
        ent_f2 = tk.Entry(f2_row1, textvariable=self.fw_path_2, bg="#0C1013", fg="#FFFFFF", bd=2, relief="sunken", font=("Courier New", 9))
        ent_f2.pack(side="left", fill="x", expand=True, padx=6)
        btn_f2 = tk.Button(f2_row1, text=" BROWSE ", command=self._load_file_2, bg="#2C3A47", fg="#00FF66", bd=2, relief="raised", font=("Courier New", 8, "bold"))
        btn_f2.pack(side="right")

        f2_row2 = tk.Frame(f2_frame, bg="#182026")
        f2_row2.pack(fill="x", padx=6, pady=4)
        
        tk.Label(f2_row2, text="RCUTYPE (Remote):", bg="#182026", fg="#00FF66", font=("Courier New", 9)).pack(side="left")
        ent_rcu2 = tk.Entry(f2_row2, textvariable=self.rcu_val_2, bg="#0C1013", fg="#FFFFFF", bd=2, relief="sunken", font=("Courier New", 9, "bold"), width=15)
        ent_rcu2.pack(side="left", padx=6)

        tk.Label(f2_row2, text="MODEID (Device ID):", bg="#182026", fg="#00FF66", font=("Courier New", 9)).pack(side="left", padx=(15, 0))
        ent_mode2 = tk.Entry(f2_row2, textvariable=self.mode_val_2, bg="#0C1013", fg="#FFFFFF", bd=2, relief="sunken", font=("Courier New", 9, "bold"), width=15)
        ent_mode2.pack(side="left", padx=6)

        log_frame = tk.LabelFrame(
            main_container, 
            text=" PROCESS CONSOLE LOG ", 
            bg="#182026", 
            fg="#FFFFFF", 
            font=("Courier New", 9, "bold"), 
            bd=2, 
            relief="groove"
        )
        log_frame.pack(padx=10, pady=5, fill="both", expand=True)

        self.console = tk.Text(log_frame, bg="#080C0E", fg="#00FF66", insertbackground="#00FF66", bd=2, relief="sunken", font=("Courier New", 8))
        self.console.pack(padx=5, pady=5, fill="both", expand=True)

        ctrl_frame = tk.Frame(main_container, bg="#11161B")
        ctrl_frame.pack(padx=10, pady=10, fill="x")

        self.btn_run = tk.Button(
            ctrl_frame, 
            text="[ EXECUTE TRANSFORMATION ]", 
            command=self._execute_conversion, 
            bg="#194D33", 
            fg="#FFFFFF", 
            font=("Courier New", 10, "bold"),
            bd=3, 
            relief="raised",
            activebackground="#2ECC71"
        )
        self.btn_run.pack(side="left", fill="x", expand=True, padx=4)

        self.btn_save = tk.Button(
            ctrl_frame, 
            text="[ SAVE COMPILED BINARY ]", 
            command=self._compile_and_save, 
            bg="#2E4A62", 
            fg="#FFFFFF", 
            font=("Courier New", 10, "bold"),
            bd=3, 
            relief="raised",
            activebackground="#3498DB"
        )
        self.btn_save.pack(side="left", fill="x", expand=True, padx=4)

        self.btn_reset = tk.Button(
            ctrl_frame, 
            text="[ RESET ]", 
            command=self._reset_system, 
            bg="#5C2D2D", 
            fg="#FFFFFF", 
            font=("Courier New", 10, "bold"),
            bd=3, 
            relief="raised",
            activebackground="#E74C3C"
        )
        self.btn_reset.pack(side="right", fill="x", expand=True, padx=4)

    def _log_msg(self, msg: str):
        self.console.insert(tk.END, f">> {msg}\n")
        self.console.see(tk.END)

    def _parse_cramfs_directory(self, raw_data: bytes, offset: int, count: int = 100) -> list[CramFSNode]:
        nodes = []
        curr = offset
        for _ in range(count):
            if curr + 12 > len(raw_data):
                break
            temp = raw_data[curr:curr+12]
            if temp == b"\x00" * 12:
                break
            
            mode, uid, size_low, size_high, gid, info = struct.unpack_from("<HHHBBB", raw_data, curr)
            size = size_low | (size_high << 16)
            namelen = info & 0x3F
            offset_val = (struct.unpack_from("<I", raw_data, curr + 8)[0] >> 6) & 0x3FFFFFF
            
            curr += 12
            if curr + (namelen * 4) > len(raw_data):
                break
                
            name_bytes = raw_data[curr : curr + (namelen * 4)]
            curr += namelen * 4
            
            name = name_bytes.split(b"\x00")[0].decode("utf-8", errors="ignore")
            if not name:
                continue
                
            node = CramFSNode()
            node.mode = mode
            node.uid = uid
            node.size = size
            node.gid = gid
            node.namelen = namelen
            node.offset = offset_val * 4
            node.name = name
            node.data_offset = node.offset
            nodes.append(node)
        return nodes

    def _decompress_file_data(self, raw_data: bytes, file_offset: int, file_size: int) -> bytes:
        try:
            num_blocks = (file_size + 4095) // 4096
            header_offset = file_offset
            block_pointers = []
            for i in range(num_blocks):
                ptr = struct.unpack_from("<I", raw_data, header_offset + (i * 4))[0]
                block_pointers.append(ptr)
            
            out_data = bytearray()
            curr_src = header_offset + (num_blocks * 4)
            for i in range(num_blocks):
                end_src = block_pointers[i]
                chunk = raw_data[curr_src:end_src]
                if chunk:
                    try:
                        decompressed = zlib.decompress(chunk)
                        out_data.extend(decompressed)
                    except:
                        out_data.extend(chunk)
                curr_src = end_src
            return bytes(out_data[:file_size])
        except:
            return b""

    def _extract_parameters(self, data: bytes, num: int) -> bool:
        cram_sig = b"\x45\x3d\xcd\x28"
        offset = data.find(cram_sig)
        if offset == -1:
            cram_sig = b"\x28\xcd\x3d\x45"
            offset = data.find(cram_sig)
            
        if offset == -1:
            self._log_msg(f"ERROR: No valid CramFS found inside File {num}!")
            return False
            
        size = struct.unpack_from("<I", data, offset + 4)[0]
        if size > len(data) - offset:
            size = len(data) - offset
            
        cram_payload = data[offset : offset+size]
        rcu_val, mode_val = "0", "0"
        table_data = b""
        table_offset = -1
        
        try:
            root_nodes = self._parse_cramfs_directory(cram_payload, 64, 50)
            etc_node = None
            for n in root_nodes:
                if n.name == "etc":
                    etc_node = n
                    break
                    
            if etc_node:
                etc_nodes = self._parse_cramfs_directory(cram_payload, etc_node.data_offset, 50)
                for n in etc_nodes:
                    if n.name == "gx.cfg":
                        cfg_bytes = self._decompress_file_data(cram_payload, n.data_offset, n.size)
                        for line in cfg_bytes.split(b"\n"):
                            line_str = line.decode("utf-8", errors="ignore").strip()
                            if "=" in line_str:
                                left, right = line_str.split("=", 1)
                                left = left.strip()
                                right = right.strip().strip('"').strip("'")
                                if left == "RCUTYPE":
                                    rcu_val = right
                                elif left == "MODEID":
                                    mode_val = right
                                    
            for n in root_nodes:
                if n.name == "TABLE.bin":
                    table_data = self._decompress_file_data(cram_payload, n.data_offset, n.size)
                    table_offset = n.data_offset
                    break
        except:
            pass

        if rcu_val == "0" or mode_val == "0":
            cfg_offset = cram_payload.find(b"RCUTYPE")
            if cfg_offset != -1:
                start = cfg_offset
                while start > 0 and cram_payload[start - 1] != 10 and cram_payload[start - 1] != 0:
                    start -= 1
                end = cfg_offset
                while end < len(cram_payload) and cram_payload[end] != 10 and cram_payload[end] != 0:
                    end += 1
                chunk = cram_payload[max(0, start - 150): min(len(cram_payload), end + 450)]
                for line in chunk.split(b"\n"):
                    line_str = line.decode("utf-8", errors="ignore").strip()
                    if "=" in line_str:
                        left, right = line_str.split("=", 1)
                        left = left.strip()
                        right = right.strip().strip('"').strip("'")
                        if left == "RCUTYPE":
                            rcu_val = right
                        elif left == "MODEID":
                            mode_val = right

        if not table_data:
            table_sig = b"\xaa\xbc\xde\xfa"
            t_idx = cram_payload.find(table_sig)
            if t_idx != -1:
                table_data = cram_payload[t_idx : t_idx+512]
                table_offset = t_idx

        if num == 1:
            self.cramfs_offset_1 = offset
            self.cramfs_size_1 = size
            self.rcu_val_1.set(rcu_val)
            self.mode_val_1.set(mode_val)
            self.table_data_1 = table_data
            self.table_offset_1 = table_offset
            self._log_msg(f"FILE 1 LOADED: Size={size} Bytes. RCUTYPE={rcu_val}, MODEID={mode_val}")
        else:
            self.cramfs_offset_2 = offset
            self.cramfs_size_2 = size
            self.rcu_val_2.set(rcu_val)
            self.mode_val_2.set(mode_val)
            self.table_data_2 = table_data
            self.table_offset_2 = table_offset
            self._log_msg(f"FILE 2 LOADED: Size={size} Bytes. RCUTYPE={rcu_val}, MODEID={mode_val}")
            
        return True

    def _load_file_1(self):
        p = filedialog.askopenfilename(title="Select Base Firmware", filetypes=[("Binary Files (*.bin)", "*.bin"), ("All Files (*.*)", "*.*")])
        if p:
            self.fw_path_1.set(p)
            self.file_data_1 = Path(p).read_bytes()
            self._extract_parameters(self.file_data_1, 1)

    def _load_file_2(self):
        p = filedialog.askopenfilename(title="Select Donor Firmware", filetypes=[("Binary Files (*.bin)", "*.bin"), ("All Files (*.*)", "*.*")])
        if p:
            self.fw_path_2.set(p)
            self.file_data_2 = Path(p).read_bytes()
            self._extract_parameters(self.file_data_2, 2)

    def _execute_conversion(self):
        if not self.file_data_1 or not self.file_data_2:
            messagebox.showerror("Error", "Please load both binary files first.")
            return
            
        self._log_msg("TRANSFORMATION: Merging parameter data from File 2 into File 1 structure...")
        self.rcu_val_1.set(self.rcu_val_2.get())
        self.mode_val_1.set(self.mode_val_2.get())
        
        if self.table_offset_1 != -1 and self.table_offset_2 != -1:
            self.table_data_1 = self.table_data_2
            self._log_msg("TRANSFORMATION: Remote key Table from File 2 injected into File 1.")
        else:
            self._log_msg("TRANSFORMATION: No hardware Table detected to replace.")

        self._log_msg("TRANSFORMATION: Injection ready. Proceed with compiling and saving.")
        messagebox.showinfo("Success", "Transformation parameters loaded!\nYou can now compile the output binary.")

    def _compile_and_save(self):
        if not self.file_data_1:
            messagebox.showerror("Error", "Base firmware is empty.")
            return
            
        save_path = filedialog.asksaveasfilename(
            title="Save Output Firmware",
            defaultextension=".bin",
            filetypes=[("Binary Files (*.bin)", "*.bin")]
        )
        if not save_path:
            return
            
        self._log_msg("COMPILER: Reassembling internal CramFS payload...")
        try:
            raw_cram = bytearray(self.file_data_1[self.cramfs_offset_1 : self.cramfs_offset_1 + self.cramfs_size_1])
            
            cfg_index = raw_cram.find(b"RCUTYPE")
            if cfg_index != -1:
                start = cfg_index
                while start > 0 and raw_cram[start - 1] != 10 and raw_cram[start - 1] != 0:
                    start -= 1
                end = cfg_index
                while end < len(raw_cram) and raw_cram[end] != 10 and raw_cram[end] != 0:
                    end += 1
                
                new_block = f"RCUTYPE = \"{self.rcu_val_1.get()}\"\nMODEID = \"{self.mode_val_1.get()}\"\n".encode("utf-8")
                space = end - start
                if len(new_block) <= space:
                    raw_cram[start:start+len(new_block)] = new_block
                    raw_cram[start+len(new_block):end] = b"\x00" * (space - len(new_block))
                else:
                    raw_cram[start:end] = new_block[:space]

            if self.table_offset_1 != -1 and len(self.table_data_1) == 512:
                raw_cram[self.table_offset_1 : self.table_offset_1 + 512] = self.table_data_1
                
            if len(raw_cram) >= 64:
                struct.pack_into("<I", raw_cram, 32, 0)
                recomputed_crc = zlib.crc32(bytes(raw_cram)) & 0xFFFFFFFF
                struct.pack_into("<I", raw_cram, 32, recomputed_crc)
                
            final_bin = bytearray(self.file_data_1)
            final_bin[self.cramfs_offset_1 : self.cramfs_offset_1 + self.cramfs_size_1] = raw_cram
            
            self._log_msg("SIGNATURE: Writing developer signature right before the protection bytes...")
            sig_text = "THIS FIRMWARE TOOL  BY  DRAGON_NOIR-DZ GUI \nKHALED_BRAHMIA"
            sig_bytes = sig_text.encode("utf-8")
            sig_len = len(sig_bytes)
            
            start_sig_idx = len(final_bin) - 4 - sig_len
            final_bin[start_sig_idx : len(final_bin) - 4] = sig_bytes
            
            self._log_msg("SECURITY: Recomputing security checksum with the signature included...")
            struct.pack_into("<I", final_bin, len(final_bin) - 4, 0)
            final_checksum = zlib.crc32(bytes(final_bin[:-4])) & 0xFFFFFFFF
            struct.pack_into("<I", final_bin, len(final_bin) - 4, final_checksum)
            
            Path(save_path).write_bytes(final_bin)
            
            self._log_msg(f"COMPILER SUCCESS: Binary saved to {save_path}")
            self._log_msg(f"STATISTICS: Total size remains identical ({len(final_bin)} bytes). Checksum applied to last 4 bytes.")
            messagebox.showinfo("Success", "Process Completed successfully!\nSecurity protection generated on the last 4 bytes with your custom signature injected.")
            
        except Exception as ex:
            self._log_msg(f"FATAL ERROR during compile: {str(ex)}")
            messagebox.showerror("Error", f"Failed to compile: {str(ex)}")

    def _reset_system(self):
        self.fw_path_1.set("")
        self.fw_path_2.set("")
        self.rcu_val_1.set("")
        self.mode_val_1.set("")
        self.rcu_val_2.set("")
        self.mode_val_2.set("")
        self.file_data_1 = b""
        self.file_data_2 = b""
        self.console.delete("1.0", tk.END)
        self._log_msg("SYSTEM: All channels reset.")

if __name__ == "__main__":
    app_root = tk.Tk()
    app = DragonNoirFwTool(app_root)
    app_root.mainloop()