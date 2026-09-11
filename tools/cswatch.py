#!/usr/bin/env python3
"""
Is chip-select actually being deasserted?

Looking at the four GSPI1 pads again, the RXSTATE bit (PADCFG0 bit 1) is not
uniform across them:

    pin 44 GSPI1_CS0B   0x44000700   bit1 = 0  -> reads LOW
    pin 45 GSPI1_CLK    0x44000700   bit1 = 0  -> reads LOW   (mode 0 idle, correct)
    pin 46 GSPI1_MISO   0x44000702   bit1 = 1  -> reads HIGH
    pin 47 GSPI1_MOSI   0x44000700   bit1 = 0  -> reads LOW

These pads have GPIORXDIS=1 because they are in native SPI mode, so the safe
assumption has always been that RXSTATE is meaningless there.  But it is NOT
uniform -- MISO reads high while the other three read low -- which suggests the
bit does track the pin.

If it does, then **CS is sitting LOW while idle**.  CS0B is active low
(`PolarityLow` in _CRS), so idle should be HIGH.  A chip select stuck asserted
would look exactly like our symptom: the sensor sees one endless framing window,
never sees a transaction boundary, and never replies.

This samples all four pads while idle and while transfers are running.  What we
want to know:
  * does pin 44 ever read HIGH?           (i.e. does CS ever deassert)
  * does it change between idle and busy?  (i.e. is the bit live at all)
  * does MOSI's bit move during a transfer? (proves the bit tracks the pin)

If MOSI/CLK move during transfers but CS never goes high, that is a real finding.
If nothing moves at all, RXSTATE is simply dead on native pads and this tells us
nothing -- which is also worth knowing.

Run as root.
"""
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spidev_raw import Spi

PINS = "/sys/kernel/debug/pinctrl/INT34BB:00/pins"
WATCH = {44: "CS0B", 45: "CLK", 46: "MISO", 47: "MOSI"}
SPI_DEV = "/dev/spidev1.0"


def read_pads():
    out = {}
    try:
        with open(PINS) as f:
            for ln in f:
                for p in WATCH:
                    if ln.startswith("pin %d " % p):
                        for tok in ln.split():
                            if tok.startswith("0x") and len(tok) == 10:
                                out[p] = int(tok, 16)
                                break
    except OSError:
        pass
    return out


def bit1(v):
    return (v >> 1) & 1 if v is not None else None


def sample(label, n, spi=None, payload=None):
    """Take n pad samples; if spi given, keep a transfer running underneath."""
    seen = {p: Counter() for p in WATCH}
    for _ in range(n):
        if spi is not None:
            try:
                spi.duplex(payload)
            except OSError:
                pass
        pads = read_pads()
        for p in WATCH:
            seen[p][bit1(pads.get(p))] += 1
    print("  %-22s" % label, end="")
    for p in sorted(WATCH):
        c = seen[p]
        lv = "/".join("%s:%d" % ("HIGH" if k else "LOW" if k == 0 else "?", v)
                      for k, v in sorted(c.items(), key=lambda x: -x[1]))
        print("  %s=%s" % (WATCH[p], lv), end="")
    print()
    return seen


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")

    print("### cswatch  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print()
    pads = read_pads()
    for p in sorted(WATCH):
        v = pads.get(p)
        print("  pin %d %-5s = 0x%08x   RXSTATE(bit1) = %s"
              % (p, WATCH[p], v or 0, bit1(v)))
    print()
    print("  Sampling. 'HIGH'/'LOW' is PADCFG0 bit 1 for each pad.")
    print()

    a = sample("idle (no traffic)", 200)

    spi = None
    try:
        spi = Spi(SPI_DEV, mode=0, speed=1_000_000)
    except OSError as e:
        print("  (no spidev: %s)" % e)

    if spi:
        # long transfers so the bus is busy as much as possible
        big = bytes(4096)
        b = sample("during 4 KiB xfers", 200, spi, big)
        c = sample("during 64 B xfers", 200, spi, bytes(64))
        spi.close()
    else:
        b = c = None

    print()
    print("=" * 74)
    moved = []
    for p in sorted(WATCH):
        vals = set(a[p].keys())
        if b:
            vals |= set(b[p].keys())
        if c:
            vals |= set(c[p].keys())
        vals.discard(None)
        if len(vals) > 1:
            moved.append(WATCH[p])
    if moved:
        print("  pads whose RXSTATE moved: %s" % ", ".join(moved))
        print("  -> the bit IS live on native pads.")
        cs_high = any(k == 1 for k in a[44].keys()) or (b and any(k == 1 for k in b[44].keys()))
        if not cs_high:
            print()
            print("  *** CS NEVER READ HIGH. Chip select may never deassert. ***")
    else:
        print("  no pad's RXSTATE ever moved.")
        print("  -> RXSTATE is dead on native-mode pads here; this test is")
        print("     inconclusive and the CS-stuck-low reading is unsupported.")
    print("=" * 74)


if __name__ == "__main__":
    main()
