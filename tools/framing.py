#!/usr/bin/env python3
"""
Two framing differences taken from a WORKING driver.

Sigfrodr/libfprint-goodixtls drives the sibling GXFP5187 successfully (enrol +
verify through fprintd). Reading its transport layer turns up two things we have
never done:

1. ONE TRANSFER PER FRAME.  Its comment is literally
   "SPI transport: one frame is exactly one transfer" -- gx_write_frame() builds
   the 4-byte header AND the body into a single buffer and issues ONE
   SPI_IOC_MESSAGE, i.e. ONE chip-select assertion.
   We have always sent them as two separate transactions with a ~2 ms gap,
   copying the Windows driver's two SpbPeripheralWrite calls.  Windows has an
   SPB stack underneath that may merge or frame them differently than spidev
   does; on Linux the two-call form means CS drops in the middle of a frame.

2. A NOP PREAMBLE BEFORE EVERY COMMAND.
       GX_AMORCE = { 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x88 }
   which is cmd 0x00 = NOP, inner length 5, four zero payload bytes, and 0x88 =
   the "checksum omitted" sentinel.  It is sent as its OWN complete A0-framed
   packet, then an 8 ms gap, then the real command.
   Our own Windows transcript says exactly this -- "a NOP is sent immediately
   before each command group" -- and refseq.py never implemented it.

Sweeps both, at both speeds. Run as root.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spidev_raw import Spi, hexd
from gpio_raw import GpioLine
import refseq

CHIP = "/dev/gpiochip0"
LINE_RST = 264
LINE_IRQ = 48
SPI_DEV = "/dev/spidev1.0"

NOP = bytes([0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x88])   # GX_AMORCE

# the reference bring-up bodies, unchanged
# The order our OWN Windows transcript records for THIS part:
#   NOP -> 0x96 DriverState Install -> NOP -> 0xa8 GetEvkVersion -> ...
# (the NOPs are emitted by the preamble option, matching GX_AMORCE)
# 0x96 03 00 01 00 10: cmd 0x96 (cmd0=9,cmd1=3), len 3, payload 01 00,
# checksum 0xAA-(0x96+3+0+1+0) = 0x10 -- verified, and identical to the
# GX_ENABLE constant in Sigfrodr's working driver.
CMDS = [
    ("driverInstall", "96 03 00 01 00 10",           True),
    ("getEvkVersion", "a8 03 00 00 00 ff",           True),
    ("chipRegRead",   "82 06 00 00 00 00 04 00 1e",  True),
    ("readOtp",       "a6 03 00 00 00 01",           True),
    ("getMcuState",   None,                          True),
]


def hx(s):
    return bytes.fromhex(s.replace(" ", ""))


def header(body_len, ptype=0xA0):
    h = bytes([ptype, body_len & 0xFF, (body_len >> 8) & 0xFF])
    return h + bytes([sum(h) & 0xFF])


class Link:
    def __init__(self, spi, irq, one_transfer, preamble, gap_ms=8):
        self.spi, self.irq = spi, irq
        self.one = one_transfer
        self.pre = preamble
        self.gap = gap_ms / 1000.0

    def frame(self, body):
        """Send one complete frame, in whichever style is under test."""
        h = header(len(body))
        if self.one:
            self.spi.duplex(h + body)          # ONE CS assertion
        else:
            self.spi.duplex(h)                 # two CS assertions, 2 ms apart
            time.sleep(0.002)
            self.spi.duplex(body)

    def command(self, body):
        if self.pre:
            self.frame(NOP)
            time.sleep(self.gap)
        self.frame(body)

    def read(self, tag):
        hdr = self.spi.duplex(b"\x00" * 4)
        ok = len(hdr) == 4 and hdr[3] == (sum(hdr[:3]) & 0xFF) and (hdr[0] >> 4) in (0xA, 0xB)
        n = (hdr[1] | (hdr[2] << 8)) if ok else None
        note = ""
        if hdr == b"\xff\xff\xff\xff":
            note = "  (idle)"
        elif ok and 0 < n <= 4096:
            note = "  <<<<<<<<<<<<<<<< VALID HEADER"
        print("     RX %-15s irq=%d hdr=%s len=%s%s"
              % (tag, self.irq.get(), hdr.hex(" "), n, note))
        if not ok or not n or n > 4096:
            return None
        time.sleep(0.0002)                      # their 200 us stage delay
        body = self.spi.duplex(b"\x00" * n)
        print("     body: %s" % body.hex(" "))
        return body


def run(one_transfer, preamble, speed):
    spi = Spi(SPI_DEV, mode=0, speed=speed)
    irq = GpioLine(CHIP, LINE_IRQ, output=False)
    L = Link(spi, irq, one_transfer, preamble)
    hit = False
    try:
        for name, body, expect_reply in CMDS:
            b = refseq.mcu_state_packet() if body is None else hx(body)
            L.command(b)
            time.sleep(0.05)
            if expect_reply and L.read(name) is not None:
                hit = True
    finally:
        irq.close()
        spi.close()
    return hit


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    if not os.path.exists(SPI_DEV):
        sys.exit("%s missing" % SPI_DEV)

    print("### framing sweep  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("### NOP preamble = %s" % NOP.hex(" "))
    print()

    # power the sensor for the whole sweep
    rst = GpioLine(CHIP, LINE_RST, output=True, value=1)
    rst.set(0); time.sleep(0.010)
    rst.set(1); time.sleep(0.150)

    wins = []
    for one in (True, False):
        for pre in (True, False):
            for speed in (10_000_000, 1_000_000):
                label = ("%s CS, %s preamble @ %d kHz"
                         % ("ONE" if one else "two",
                            "WITH" if pre else "no", speed // 1000))
                print("=" * 74)
                print("== %s" % label)
                print("=" * 74)
                try:
                    if run(one, pre, speed):
                        wins.append(label)
                except Exception as e:
                    print("   !! %r" % e)
                print()
                time.sleep(0.2)
    rst.close()

    print("=" * 74)
    print("RESPONDED: %s" % ", ".join(wins) if wins
          else "no valid header under any framing combination")
    print("=" * 74)


if __name__ == "__main__":
    main()
