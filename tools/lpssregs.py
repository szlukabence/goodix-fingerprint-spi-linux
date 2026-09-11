#!/usr/bin/env python3
"""
Dump the live Intel LPSS SPI#1 controller registers.

Reads PCI BAR0 of 00:1e.3 (8086:02ab) through sysfs `resource0`, which is
mmap-able as root even when /dev/mem is locked down.

Why: we have verified the *pads* are configured correctly, but never the
*controller*. On Comet Lake the LPSS SSP has a private register block at
BAR+0x200 that Linux's pxa2xx-spi programs:

    reg_ssp      = 0x220
    reg_cs_ctrl  = 0x224      <- chip-select control
    capabilities = 0x2fc

`reg_cs_ctrl` decides whether chip select is driven by the SSP hardware or by
software, and which CS line is selected:

    bit 0   SW_MODE   1 = software drives CS, 0 = hardware drives it
    bit 1   CS_HIGH   the level software is asserting
    bits 9:8 CS_SEL   which chip select

If this is wrong, the sensor never sees a framed transaction no matter how
perfect our bytes are -- and it would look exactly like our symptom.
"""
import mmap
import os
import struct
import sys

BAR = "/sys/bus/pci/devices/0000:00:1e.3/resource0"

SSP = [
    (0x00, "SSCR0", "control 0: enable, data size, clock rate"),
    (0x04, "SSCR1", "control 1: interrupts, FIFO thresholds, loopback"),
    (0x08, "SSSR",  "status: TNF/RNE/BSY/TFL/RFL"),
    (0x0C, "SSITR", "interrupt test"),
    (0x28, "SSTO",  "timeout"),
    (0x30, "SSPSP", "programmable serial protocol"),
]
PRIV = [
    (0x220, "SSP_REG",   "LPSS private SSP control"),
    (0x224, "CS_CTRL",   "chip-select control  <-- the interesting one"),
    (0x2FC, "CAPS",      "capabilities"),
]


def bits(v, pairs):
    on = [n for b, n in pairs if v & (1 << b)]
    return ", ".join(on) if on else "-"


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    if not os.path.exists(BAR):
        sys.exit("%s missing" % BAR)

    size = os.path.getsize(BAR)
    m = fd = None
    last = None
    for flags, prot, note in (
            (os.O_RDWR, mmap.PROT_READ | mmap.PROT_WRITE, "rw/full"),
            (os.O_RDWR, mmap.PROT_READ, "ro-prot/full"),
            (os.O_RDONLY, mmap.PROT_READ, "ro/full")):
        try:
            fd = os.open(BAR, flags)
            m = mmap.mmap(fd, size, mmap.MAP_SHARED, prot)
            print("  (mapped %d bytes via %s)" % (size, note))
            break
        except OSError as e:
            last = e
            if fd is not None:
                os.close(fd); fd = None
    if m is None:
        sys.exit("could not mmap %s: %r" % (BAR, last))

    def rd(off):
        return struct.unpack_from("<I", m, off)[0]

    print("### LPSS SPI#1 (00:1e.3) live registers   BAR size 0x%x" % size)
    print()
    print("  -- SSP core")
    for off, name, desc in SSP:
        print("     +0x%03x %-6s = 0x%08x   %s" % (off, name, rd(off), desc))

    sscr0, sscr1, sssr = rd(0x00), rd(0x04), rd(0x08)
    print()
    print("     SSCR0 decode: SSE(enable)=%d  DSS(data size)=%d bits  SCR(clk div)=%d"
          % (sscr0 & 1, ((sscr0 & 0xF) + 1 + (((sscr0 >> 20) & 1) * 16)),
             (sscr0 >> 8) & 0xFFF))
    print("     SSCR1 decode: %s" % bits(sscr1, [
        (0, "RIE"), (1, "TIE"), (2, "LBM(LOOPBACK!)"), (3, "SPO(CPOL)"),
        (4, "SPH(CPHA)"), (23, "RWOT"), (24, "TRAIL"), (22, "TTE")]))
    print("     SSSR  decode: %s   TxFIFO level=%d  RxFIFO level=%d" % (
        bits(sssr, [(2, "TNF"), (3, "RNE"), (4, "BSY"), (5, "TFS"),
                    (6, "RFS"), (7, "ROR")]),
        (sssr >> 8) & 0xF, (sssr >> 12) & 0xF))

    print()
    print("  -- LPSS private block (BAR+0x200)")
    for off, name, desc in PRIV:
        v = rd(off)
        print("     +0x%03x %-8s = 0x%08x   %s" % (off, name, v, desc))
        if name == "CS_CTRL":
            print("            SW_MODE=%d  (1 = software drives CS, 0 = hardware)"
                  % (v & 1))
            print("            CS_STATE=%d (level software is asserting)"
                  % ((v >> 1) & 1))
            print("            CS_SEL=%d" % ((v >> 8) & 3))

    m.close()
    os.close(fd)


if __name__ == "__main__":
    main()
