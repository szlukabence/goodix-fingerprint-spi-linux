#!/usr/bin/env python3
"""
Reproduce the first non-idle bytes ever seen (AUDIT AI) and dump them in full.

bruteforce.py under lowstate.py (pin 189 LOW) produced ASCII-looking bytes in
every SPI_CS_HIGH row, and only there. This isolates that condition and dumps
every byte, hex + ASCII, with controls:

  A  mode0 | CS_HIGH, pin 189 LOW, NOP + EVK + CHIPID then 256-byte read  (the hit)
  B  same, CS low                                                          (control: ff?)
  C  same as A, but pin 189 HIGH                                           (control: ff?)
  D  mode0 | CS_HIGH, pin 189 LOW, NO commands, just the read              (stale or fresh?)
  E  mode0 | CS_HIGH, pin 189 LOW, EVK only then read                      (which command?)

Each case starts from its own fresh reset (189 HIGH 300 ms -> LOW, 600 ms settle)
except C, which ends HIGH. Run as root.
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
SPI_CS_HIGH = 0x04

NOP = bytes.fromhex("0005000000000088")
EVK = bytes.fromhex("a8030000" "00ff")
CHIPID = bytes.fromhex("82060000000004001e")


def header(n):
    h = bytes([0xA0, n & 0xFF, n >> 8])
    return h + bytes([sum(h) & 0xFF])


def dump(rx):
    for i in range(0, len(rx), 16):
        chunk = rx[i:i + 16]
        print("     %04x  %-48s %s" % (i, " ".join("%02x" % b for b in chunk),
                                     "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)))


def run_case(tag, en, irq, level_after_reset, mode_bits, bodies):
    en.set(1)
    time.sleep(0.3)
    en.set(level_after_reset)
    time.sleep(0.6)
    print("\n== %s   189=%d  irq=%d  mode=0x%02x  cmds=%s" % (
        tag, level_after_reset, irq.get(), mode_bits,
        ",".join(b[:1].hex() for b in bodies) or "none"))
    s = Spi(SPI_DEV, mode=mode_bits, speed=1_000_000)
    try:
        for body in bodies:
            s.duplex(header(len(body)))
            time.sleep(0.002)
            s.duplex(body)
            time.sleep(0.02)
        rx = s.duplex(bytes(256))
    finally:
        s.close()
    odd = sorted(set(rx) - {0x00, 0xFF})
    print("   irq after=%d   non-idle bytes: %d distinct" % (irq.get(), len(odd)))
    if odd:
        dump(rx)
    return rx


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    print("### CS_HIGH reproduction  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    irq = GpioLine(CHIP, LINE_IRQ, output=False)
    en = GpioLine(CHIP, LINE_EN, output=True, value=1)

    run_case("A  CS_HIGH, 189 LOW, NOP+EVK+CHIPID", en, irq, 0, 0 | SPI_CS_HIGH, [NOP, EVK, CHIPID])
    run_case("B  CS low,  189 LOW, NOP+EVK+CHIPID", en, irq, 0, 0, [NOP, EVK, CHIPID])
    run_case("C  CS_HIGH, 189 HIGH, NOP+EVK+CHIPID", en, irq, 1, 0 | SPI_CS_HIGH, [NOP, EVK, CHIPID])
    run_case("D  CS_HIGH, 189 LOW, no commands", en, irq, 0, 0 | SPI_CS_HIGH, [])
    run_case("E  CS_HIGH, 189 LOW, EVK only", en, irq, 0, 0 | SPI_CS_HIGH, [EVK])
    run_case("A' repeat of A", en, irq, 0, 0 | SPI_CS_HIGH, [NOP, EVK, CHIPID])

    en.set(1); time.sleep(0.3); en.set(0); time.sleep(0.3)
    print("\nleft 189 LOW, irq=%d" % irq.get())
    en.close()
    irq.close()


if __name__ == "__main__":
    main()
