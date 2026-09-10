#!/usr/bin/env python3
"""Decode Thumb-2 BL/BLX targets in a raw Cortex-M image.
Usage: thumbcalls.py <bin> <loadbase-hex> [target-hex ...]
With no targets, prints a histogram of the most-called addresses."""
import struct, sys, collections

def bl_targets(d, base):
    out = []
    for i in range(0, len(d) - 3, 2):
        hw1 = struct.unpack_from("<H", d, i)[0]
        hw2 = struct.unpack_from("<H", d, i + 2)[0]
        if (hw1 & 0xF800) != 0xF000:            # not a 32-bit branch prefix
            continue
        if (hw2 & 0xD000) not in (0xD000, 0xC000):   # BL (11x1) or BLX (11x0)
            continue
        S     = (hw1 >> 10) & 1
        imm10 = hw1 & 0x3FF
        J1    = (hw2 >> 13) & 1
        J2    = (hw2 >> 11) & 1
        imm11 = hw2 & 0x7FF
        I1 = (~(J1 ^ S)) & 1
        I2 = (~(J2 ^ S)) & 1
        off = (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1)
        if S:
            off -= (1 << 25)
        tgt = base + i + 4 + off
        out.append((base + i, tgt))
    return out

if __name__ == "__main__":
    d = open(sys.argv[1], "rb").read()
    base = int(sys.argv[2], 16)
    calls = bl_targets(d, base)
    if len(sys.argv) > 3:
        want = {int(x, 16) for x in sys.argv[3:]}
        for src, tgt in calls:
            if tgt in want or (tgt | 1) in want or (tgt & ~1) in want:
                print(f"  call from 0x{src:08x} -> 0x{tgt:08x}")
    else:
        c = collections.Counter(t for _, t in calls)
        print(f"# {len(calls)} BL/BLX sites, {len(c)} distinct targets")
        for t, n in c.most_common(30):
            print(f"  0x{t:08x}  called {n:3d}x")
