#!/usr/bin/env python3
"""Parse a PE64 .pdata (exception directory) into an exact function map.
Far more reliable than scanning for int3 padding.
Usage: pdata.py <dll> [addr...]   addr = VA (0x1800....) or file offset."""
import struct, sys, bisect
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from pexref import PE

def funcs(pe):
    sec = pe.sec(".pdata")
    if not sec: return []
    va, vs, ra, rs = sec
    out = []
    for i in range(ra, ra + min(vs, rs), 12):
        start, end, unwind = struct.unpack_from("<III", pe.d, i)
        if start == 0 and end == 0: continue
        out.append((start, end))
    out.sort()
    return out

if __name__ == "__main__":
    pe = PE(sys.argv[1])
    fs = funcs(pe)
    starts = [f[0] for f in fs]
    print(f"# {len(fs)} functions in .pdata", file=sys.stderr)
    for a in sys.argv[2:]:
        v = int(a, 16)
        rva = v - pe.base if v >= pe.base else (pe.f2rva(v) or v)
        i = bisect.bisect_right(starts, rva) - 1
        if i < 0:
            print(f"{a}: not found"); continue
        s, e = fs[i]
        inside = "INSIDE" if rva < e else "past-end(gap)"
        print(f"{a}: rva 0x{rva:06x} -> func rva 0x{s:06x}-0x{e:06x} "
              f"va 0x{pe.base+s:x} size {e-s} ({inside})")
