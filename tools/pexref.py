#!/usr/bin/env python3
"""Find RIP-relative LEA references in a PE .text to a given RVA (or file offset).
Usage: pexref.py <dll> <target-hex...>   (targets are FILE offsets)"""
import struct, sys, bisect

class PE:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        d = self.d
        pe = struct.unpack_from("<I", d, 0x3c)[0]
        nsec = struct.unpack_from("<H", d, pe+6)[0]
        optsz = struct.unpack_from("<H", d, pe+20)[0]
        self.base = struct.unpack_from("<Q", d, pe+24+24)[0]
        self.secs = []
        for i in range(nsec):
            o = pe+24+optsz+i*40
            name = d[o:o+8].rstrip(b"\0").decode()
            vs, va, rs, ra = struct.unpack_from("<IIII", d, o+8)
            self.secs.append((name, va, vs, ra, rs))
    def f2rva(self, f):
        for n, va, vs, ra, rs in self.secs:
            if ra <= f < ra+rs: return va + (f-ra)
        return None
    def rva2f(self, r):
        for n, va, vs, ra, rs in self.secs:
            if va <= r < va+max(vs, rs): 
                off = r-va
                return ra+off if off < rs else None
        return None
    def sec(self, name):
        for n, va, vs, ra, rs in self.secs:
            if n == name: return va, vs, ra, rs
        return None

LEA_REGS = {0x05:"rax",0x0d:"rcx",0x15:"rdx",0x1d:"rbx",0x2d:"rbp",0x35:"rsi",0x3d:"rdi"}

def scan_lea(pe):
    """yield (file_off_of_insn, target_rva, reg)"""
    va, vs, ra, rs = pe.sec(".text")
    d = pe.d
    for i in range(ra, ra+rs-7):
        b0, b1, b2 = d[i], d[i+1], d[i+2]
        rexr = 0
        j = i
        if 0x48 <= b0 <= 0x4f and b1 == 0x8d:
            rexr = 8 if (b0 & 4) else 0
            modrm = b2; disp_at = i+3; ln = 7
        else:
            continue
        if (modrm & 0xc7) not in LEA_REGS and (modrm & 0x07) != 0x05:
            continue
        if (modrm & 0xc0) != 0x00 or (modrm & 0x07) != 0x05:
            continue
        disp = struct.unpack_from("<i", d, disp_at)[0]
        nxt_rva = va + (i - ra) + ln
        yield i, nxt_rva + disp

if __name__ == "__main__":
    pe = PE(sys.argv[1])
    targets = {}
    for t in sys.argv[2:]:
        f = int(t, 16)
        targets.setdefault(pe.f2rva(f), []).append(f)
    hits = {}
    for off, tr in scan_lea(pe):
        if tr in targets:
            hits.setdefault(tr, []).append(off)
    for tr, lst in hits.items():
        print(f"target rva 0x{tr:x} (file 0x{pe.rva2f(tr):x}) referenced from:")
        for o in lst:
            print(f"   insn file 0x{o:08x}  rva 0x{pe.f2rva(o):06x}  va 0x{pe.base+pe.f2rva(o):x}")
    for tr in targets:
        if tr not in hits: print(f"target rva 0x{tr:x}: NO LEA XREF")
