#!/usr/bin/env python3
"""
Brute-force sweep of every bus parameter we have always taken on trust.

Everything we have tried so far assumed the ACPI _CRS description is correct:
SPI mode 0, MSB first, 8-bit words, chip select active low, 1-10 MHz. Those
came from the firmware and we never questioned them. But _CRS is written by the
BIOS vendor, and the only thing that actually proves it right is that Windows
uses it successfully -- which tells us about Windows' path, not about whether a
different setting might also, or better, reach the sensor.

This sweeps:
    SPI mode      0, 1, 2, 3          (clock polarity and phase)
    bit order     MSB first, LSB first
    chip select   active low, active high
    speed         10 MHz, 1 MHz, 400 kHz, 100 kHz, 50 kHz
and for each combination sends a NOP then GetEvkVersion, then reads a long
window looking for ANY byte that is not 0xff and not 0x00.

The detector is deliberately crude. We are not looking for a valid packet -- we
are looking for the line to do ANYTHING it has never done. One stray byte would
be the first evidence in this entire project that the sensor exists.

Run as root.
"""
import ctypes
import fcntl
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spidev_raw import Spi
from gpio_raw import GpioLine

SPI_DEV = "/dev/spidev1.0"
CHIP = "/dev/gpiochip0"
LINE_RST = 264

SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_RD_MODE = 0x80016B01
SPI_IOC_WR_LSB_FIRST = 0x40016B02

SPI_CPHA, SPI_CPOL = 0x01, 0x02
SPI_CS_HIGH, SPI_LSB_FIRST = 0x04, 0x08

NOP = bytes([0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x88])
EVK = bytes.fromhex("a8030000 00ff".replace(" ", ""))
CHIPID = bytes.fromhex("82060000000004001e")

READ_WINDOW = 256          # read this many bytes looking for anything at all


def header(body_len, ptype=0xA0):
    h = bytes([ptype, body_len & 0xFF, (body_len >> 8) & 0xFF])
    return h + bytes([sum(h) & 0xFF])


def interesting(buf):
    """Any byte that is neither idle-high nor idle-low is news."""
    return set(buf) - {0x00, 0xFF}


def attempt(mode_bits, speed, label, results):
    try:
        fd = os.open(SPI_DEV, os.O_RDWR)
    except OSError as e:
        print("  !! open: %r" % e)
        return
    try:
        m = ctypes.c_uint8(mode_bits)
        try:
            fcntl.ioctl(fd, SPI_IOC_WR_MODE, m)
        except OSError:
            print("  %-52s mode rejected by driver" % label)
            return
        fcntl.ioctl(fd, SPI_IOC_RD_MODE, m)
        if m.value != mode_bits:
            print("  %-52s mode not honoured (0x%02x)" % (label, m.value))
            return
    finally:
        os.close(fd)

    s = Spi(SPI_DEV, mode=mode_bits, speed=speed)
    try:
        for body in (NOP, EVK, CHIPID):
            s.duplex(header(len(body)))
            time.sleep(0.002)
            s.duplex(body)
            time.sleep(0.02)
        rx = s.duplex(bytes(READ_WINDOW))
    except OSError as e:
        print("  %-52s transfer failed: %r" % (label, e))
        s.close()
        return
    s.close()

    odd = interesting(rx)
    uniq = sorted(set(rx))
    flag = ""
    if odd:
        flag = "   <<<<<<<<<<<<<< NON-IDLE BYTES: %s" % sorted(odd)[:12]
        results.append((label, sorted(odd)[:12]))
    print("  %-52s distinct=%s%s"
          % (label, " ".join("%02x" % b for b in uniq[:6]), flag))


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    if not os.path.exists(SPI_DEV):
        sys.exit("%s missing -- is the sensor enabled in the BIOS and bound to spidev?"
                 % SPI_DEV)

    print("### brute-force bus sweep  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("### looking for ANY byte that is not 0x00 or 0xff\n")

    rst = GpioLine(CHIP, LINE_RST, output=True, value=1)
    rst.set(0); time.sleep(0.01)
    rst.set(1); time.sleep(0.15)

    results = []
    for mode in (0, 1, 2, 3):
        for lsb in (0, SPI_LSB_FIRST):
            for cshigh in (0, SPI_CS_HIGH):
                for speed in (10_000_000, 1_000_000, 400_000, 100_000, 50_000):
                    bits = mode | lsb | cshigh
                    label = ("mode%d %s %s @%7d Hz"
                             % (mode,
                                "LSB" if lsb else "MSB",
                                "CS-high" if cshigh else "CS-low ",
                                speed))
                    attempt(bits, speed, label, results)
    rst.close()

    print("\n" + "=" * 78)
    if results:
        print("*** THE LINE PRODUCED SOMETHING under:")
        for label, odd in results:
            print("      %s   bytes %s" % (label, odd))
    else:
        print("nothing but 0x00/0xff under every combination tested")
    print("=" * 78)


if __name__ == "__main__":
    main()
