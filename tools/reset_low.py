#!/usr/bin/env python3
"""
Pin 189 held LOW -- the state Windows keeps while the sensor WORKS.

2026-09-13 Windows memory read (RWEverything, sensor just used to unlock):
    pin 189 DW0 = 0x44000200   output LOW
    pin  41 DW0 = 0x40100100   interrupt reads LOW
Almost every Linux test drove pin 189 HIGH ("power on") and took interrupt HIGH as
proof of power. Hypothesis: pin 189 is an active-HIGH RESET (the board inverts,
as it does the interrupt: ActiveHigh here, ActiveLow on the 5187). Then HIGH held
the MCU in reset, and the 88.8 ms "rail decay" is the MCU boot time before its
firmware drives the interrupt line low.

This test:
  1. 189 HIGH 300 ms, then LOW; time how long until interrupt goes LOW.
  2. With 189 LOW, run Sigfrodr's exact exchange and the Windows two-transfer
     framing, polling the interrupt for a HIGH pulse after each command (Windows
     sees one ~16 ms after each command).
  3. Control at the end: 189 HIGH -> interrupt should go HIGH; LOW -> LOW again.
Pin 189 is left LOW (the Windows state).

Run as root.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spidev_raw import Spi
from gpio_raw import GpioLine
from sigfrodr_exact import PREAMBLE_00, FW_VERSION

SPI_DEV = "/dev/spidev1.0"
CHIP = "/dev/gpiochip0"
LINE_EN = 264
LINE_IRQ = 48


def wait_level(irq, level, ms):
    t = time.monotonic()
    while (time.monotonic() - t) * 1000 < ms:
        if irq.get() == level:
            return (time.monotonic() - t) * 1000
        time.sleep(0.0002)
    return None


def fmt(ms):
    return "never (%s)" % "timeout" if ms is None else "%.1f ms" % ms


def read_reply(spi, irq, tag):
    lat = wait_level(irq, 1, 300)
    hdr = spi.duplex(b"\xff" * 4)
    line = "   %-18s irq-high after %-16s hdr %s" % (tag, fmt(lat), hdr.hex(" "))
    hit = set(hdr) != {0xFF}
    ok = hdr[3] == sum(hdr[:3]) & 0xFF and (hdr[0] >> 4) in (0xA, 0xB)
    n = hdr[1] | (hdr[2] << 8)
    if ok and 0 < n <= 2048:
        time.sleep(0.0002)
        body = spi.duplex(b"\xff" * n)
        line += "\n      body %s  %r" % (body.hex(" "), body)
    print(line + ("   <<< NON-IDLE" if hit else ""))
    return hit or lat is not None


def exchange_one_transfer(spi, irq):
    spi.duplex(PREAMBLE_00)
    time.sleep(0.008)
    spi.duplex(FW_VERSION)
    a = read_reply(spi, irq, "ack")
    time.sleep(0.005)
    b = read_reply(spi, irq, "reply")
    return a or b


def exchange_two_transfer(spi, irq):
    for frame in (PREAMBLE_00, FW_VERSION):
        spi.duplex(frame[:4])
        time.sleep(0.002)
        spi.duplex(frame[4:])
        time.sleep(0.008)
    a = read_reply(spi, irq, "ack")
    time.sleep(0.005)
    b = read_reply(spi, irq, "reply")
    return a or b


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    if not os.path.exists(SPI_DEV):
        sys.exit("%s missing" % SPI_DEV)
    print("### pin 189 LOW test  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    irq = GpioLine(CHIP, LINE_IRQ, output=False)
    print("as found: interrupt = %d" % irq.get())
    en = GpioLine(CHIP, LINE_EN, output=True, value=1)
    time.sleep(0.3)
    print("189 HIGH 300 ms: interrupt = %d" % irq.get())
    en.set(0)
    print("189 -> LOW: interrupt went LOW after %s" % fmt(wait_level(irq, 0, 2000)))
    time.sleep(0.5)
    print("settled: interrupt = %d\n" % irq.get())

    hits = []
    for speed in (1_000_000, 10_000_000):
        for name, fn in (("one-transfer (Sigfrodr)", exchange_one_transfer),
                         ("two-transfer (Windows)", exchange_two_transfer)):
            print("== %s @ %d kHz   irq before = %d" % (name, speed // 1000, irq.get()))
            spi = Spi(SPI_DEV, mode=0, speed=speed)
            try:
                if fn(spi, irq):
                    hits.append("%s @ %d kHz" % (name, speed // 1000))
            finally:
                spi.close()
            time.sleep(0.2)

    print("\n### control")
    en.set(1)
    print("189 -> HIGH: interrupt went HIGH after %s" % fmt(wait_level(irq, 1, 500)))
    time.sleep(0.2)
    en.set(0)
    print("189 -> LOW:  interrupt went LOW after %s" % fmt(wait_level(irq, 0, 2000)))
    time.sleep(0.3)
    print("left with 189 LOW, interrupt = %d" % irq.get())
    en.close()
    irq.close()

    print("\n" + "=" * 70)
    print("ACTIVITY (non-ff byte or interrupt pulse): " + (", ".join(hits) if hits else "none"))
    print("=" * 70)


if __name__ == "__main__":
    main()
