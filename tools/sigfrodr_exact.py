#!/usr/bin/env python3
"""
Sigfrodr's first-contact exchange, byte for byte and delay for delay.

He sent it on 2026-09-13 in reply to our question "what is the first exchange
that answers from a cold boot?". It is what first made his GXFP5187 speak after
three days of total silence, and what his driver still uses as a liveness test.

framing.py (section M1) already tested "one frame = one transfer" and found no
difference -- but with OUR bodies, OUR delays and OUR read order. This runs HIS:

    1. preamble, ONE 12-byte transfer:  A0 08 00 A8  00 05 00 00 00 00 00 88
       sleep 8 ms
    2. FIRMWARE_VERSION, ONE 10-byte transfer:  A0 06 00 A6  A8 03 00 70 DC B3
       sleep 10 ms
    3. read 4   -> expect B0 03 00 A8 (ACK)
       sleep 5 ms
    4. read 4   -> expect A0 1B 00 .. (header), then read the body

Writes are one transfer; reads are two (header, 200 us, body) -- his asymmetry.

Conditions, each with a built-in CONTROL: the IRQ pad follows the enable rail
(section E / AB2), so reading it high proves the rail is actually up for the
attempt. A row whose control fails is reported as INVALID, not as a negative.

Run as root.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spidev_raw import Spi
from gpio_raw import GpioLine

SPI_DEV = "/dev/spidev1.0"
CHIP = "/dev/gpiochip0"
LINE_EN = 264
LINE_IRQ = 48

PREAMBLE_00 = bytes.fromhex("a00800a8" "0005000000000088")
PREAMBLE_01 = bytes.fromhex("a00800a8" "0105000000000088")   # the 51C0-log variant
FW_VERSION = bytes.fromhex("a00600a6" "a8030070dcb3")


def check_constants():
    # outer header checksum = plain sum of first three bytes
    for f in (PREAMBLE_00, FW_VERSION):
        assert f[3] == sum(f[:3]) & 0xFF, f.hex()
        assert f[1] | (f[2] << 8) == len(f) - 4, f.hex()
    # FIRMWARE_VERSION body follows the 0xAA-sum rule; the preamble does not
    body = FW_VERSION[4:]
    assert body[-1] == (0xAA - sum(body[:-1])) & 0xFF


def exchange(spi, irq, scale, preamble):
    """Returns (log lines, responded?)."""
    out, hit = [], False
    d = lambda ms: time.sleep(ms * scale / 1000.0)

    spi.duplex(preamble)
    d(8)
    spi.duplex(FW_VERSION)
    d(10)
    ack = spi.duplex(b"\xff" * 4)
    out.append("ack  %s  irq=%d" % (ack.hex(" "), irq.get()))
    d(5)
    hdr = spi.duplex(b"\xff" * 4)
    out.append("hdr  %s  irq=%d" % (hdr.hex(" "), irq.get()))
    if set(ack + hdr) != {0xFF}:
        hit = True
    ok = hdr[3] == sum(hdr[:3]) & 0xFF and (hdr[0] >> 4) in (0xA, 0xB)
    n = hdr[1] | (hdr[2] << 8)
    if ok and 0 < n <= 2048:
        time.sleep(0.0002)
        body = spi.duplex(b"\xff" * n)
        out.append("body %s  %r" % (body.hex(" "), body[3:]))
        hit = True
    return out, hit


def run_row(label, en, irq, speed, scale, preamble, prep):
    prep(en)
    control = irq.get()             # rail-up proof: IRQ pad follows the rail
    spi = Spi(SPI_DEV, mode=0, speed=speed)
    try:
        lines, hit = exchange(spi, irq, scale, preamble)
    finally:
        spi.close()
    if control != 1:
        verdict = "INVALID (control: irq=%d, rail not up)" % control
    elif hit:
        verdict = "*** RESPONDED ***"
    else:
        verdict = "silent (control OK)"
    print("== %-52s %s" % (label, verdict))
    for l in lines:
        print("     " + l)
    return hit, control == 1


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    if not os.path.exists(SPI_DEV):
        sys.exit("%s missing" % SPI_DEV)
    check_constants()
    print("### Sigfrodr exact first-contact  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    irq = GpioLine(CHIP, LINE_IRQ, output=False)
    print("### as found: irq pad = %d (1 means the rail is already up)" % irq.get())
    irq.close()

    # Take the enable line WITHOUT changing it if the rail is already up.
    irq = GpioLine(CHIP, LINE_IRQ, output=False)
    found = irq.get()
    en = GpioLine(CHIP, LINE_EN, output=True, value=found)

    def no_reset(e):
        e.set(1); time.sleep(1.0)            # just ensure power; no pulse

    def true_cycle(e):
        e.set(0); time.sleep(0.5)            # 5.6x the 88.8 ms rail decay
        e.set(1); time.sleep(0.6)

    def long_settle(e):
        e.set(0); time.sleep(0.5)
        e.set(1); time.sleep(3.0)

    rows = []
    for prep_name, prep in (("no reset", no_reset),
                            ("true power cycle, 600 ms", true_cycle),
                            ("true power cycle, 3 s", long_settle)):
        for speed in (10_000_000, 1_000_000):
            for scale in (1.0, 1.5, 3.0):
                for pname, pre in (("pre 00", PREAMBLE_00), ("pre 01", PREAMBLE_01)):
                    label = "%s | %d kHz | delay x%.1f | %s" % (
                        prep_name, speed // 1000, scale, pname)
                    rows.append((label,) + run_row(label, en, irq, speed, scale, pre, prep))

    # negative control for the control: rail OFF must read irq=0
    en.set(0); time.sleep(0.5)
    off = irq.get()
    en.set(1); time.sleep(0.3)
    on = irq.get()
    print("\n### control check: rail off -> irq=%d (expect 0), rail on -> irq=%d (expect 1)"
          % (off, on))
    en.close(); irq.close()

    valid = [r for r in rows if r[2]]
    hits = [r for r in rows if r[1]]
    print("\n" + "=" * 74)
    print("rows %d, valid (rail proven up) %d, responded %d" % (len(rows), len(valid), len(hits)))
    for r in hits:
        print("  RESPONDED: " + r[0])
    print("=" * 74)


if __name__ == "__main__":
    main()
