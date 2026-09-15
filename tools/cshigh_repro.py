#!/usr/bin/env python3
"""
First contact on a GXFP51A0: prove the sensor answers, with controls.

  A  CS_HIGH, reset released, NOP+EVK+CHIPID  -> the firmware-version reply
  B  CS low,  reset released                  control: silent
  C  CS_HIGH, reset ASSERTED                  control: silent
  D  CS_HIGH, reset released, no commands     control: nothing framed
  E  CS_HIGH, reset released, EVK only        -> the ACK frame
  A' repeat of A                              reproducibility

The two conditions that matter (AUDIT AJ): the "enable" GPIO is an active-HIGH
RESET and must be held LOW, and spidev needs SPI_CS_HIGH.

Board values are DISCOVERED, not hardcoded: @xamelllion's HONOR BBR-WAX9 (repo
issue #1) has the sensor on spidev0.0 with the interrupt on line 279, where this
laptop has spidev1.0 and line 48 -- and on that board spidev1.0 is the BIOS
flash, so a hardcoded node would have talked to the wrong chip entirely.
Override anything with --spidev/--reset/--irq. Run as root.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board
from spidev_raw import Spi
from gpio_raw import GpioLine

SPI_CS_HIGH = 0x04

NOP = bytes.fromhex("0005000000000088")
EVK = bytes.fromhex("a8030000" "00ff")
CHIPID = bytes.fromhex("82060000000004001e")


def header(n):
    h = bytes([0xA0, n & 0xFF, n >> 8])
    return h + bytes([sum(h) & 0xFF])


def dump(rx):
    """Hex+ASCII, collapsing the long tail of one repeated filler byte."""
    i = 0
    while i < len(rx):
        chunk = rx[i:i + 16]
        if i and len(set(rx[i:])) == 1:
            print("     ...   (%02x to 0x%04x)" % (rx[i], len(rx) - 1))
            return
        print("     %04x  %-48s %s" % (i, " ".join("%02x" % b for b in chunk),
                                       "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)))
        i += 16


def show_pads(tag, lines):
    pads = {p["line"]: p for p in board.read_pads() if p["line"] is not None}
    print("%s:" % tag)
    for ln in lines:
        p = pads.get(ln)
        if p:
            print("  line %-4d pin %-4d %-14s PADCFG0 0x%08x" % (ln, p["pin"], p["name"], p["cfg0"]))


def run_case(tag, spidev, en, irq, reset_asserted, mode_bits, bodies):
    en.set(1)                      # assert reset
    time.sleep(0.3)
    irq_in_reset = irq.get()
    en.set(1 if reset_asserted else 0)
    time.sleep(0.6)                # MCU boot is ~90 ms after release
    print("\n== %s   reset=%d  irq(in reset)=%d  irq(before cmds)=%d  mode=0x%02x  cmds=%s" % (
        tag, reset_asserted, irq_in_reset, irq.get(), mode_bits,
        ",".join(b[:1].hex() for b in bodies) or "none"))
    s = Spi(spidev, mode=mode_bits, speed=1_000_000)
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
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    board.add_args(ap)
    args = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("run as root")

    print("### GXFP51A0 CS_HIGH first-contact test  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    spidev, chip, rst_line, irq_line = board.resolve(args.spidev, args.reset, args.irq)
    if not spidev or rst_line is None or irq_line is None:
        sys.exit("could not resolve the board; pass --spidev/--reset/--irq")
    if not os.path.exists(spidev):
        sys.exit("%s does not exist -- bind spidev first" % spidev)
    print()
    show_pads("pads before", [rst_line, irq_line])

    irq = GpioLine(chip, irq_line, output=False)
    en = GpioLine(chip, rst_line, output=True, value=1)
    try:
        run_case("A  CS_HIGH, reset LOW, NOP+EVK+CHIPID", spidev, en, irq, 0, SPI_CS_HIGH, [NOP, EVK, CHIPID])
        run_case("B  CS low,  reset LOW, NOP+EVK+CHIPID", spidev, en, irq, 0, 0, [NOP, EVK, CHIPID])
        run_case("C  CS_HIGH, reset HIGH, NOP+EVK+CHIPID", spidev, en, irq, 1, SPI_CS_HIGH, [NOP, EVK, CHIPID])
        run_case("D  CS_HIGH, reset LOW, no commands", spidev, en, irq, 0, SPI_CS_HIGH, [])
        run_case("E  CS_HIGH, reset LOW, EVK only", spidev, en, irq, 0, SPI_CS_HIGH, [EVK])
        run_case("A' repeat of A", spidev, en, irq, 0, SPI_CS_HIGH, [NOP, EVK, CHIPID])
        en.set(1); time.sleep(0.3); en.set(0); time.sleep(0.3)
        print("\nleft reset LOW (MCU running), irq=%d" % irq.get())
    finally:
        en.close()
        irq.close()
    print()
    show_pads("pads after", [rst_line, irq_line])


if __name__ == "__main__":
    main()
