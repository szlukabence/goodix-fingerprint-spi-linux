#!/usr/bin/env python3
"""
The axes the earlier brute force never touched.

bruteforce.py swept clock mode, chip-select polarity and speed. This sweeps what
was left, all of it untried:

  * SETTLE TIME after reset. We have always used 150 ms (Sigfrodr uses 120,
    berkekbgz 200). If this part simply boots slowly, every probe we have ever
    run happened before it was ready. Tries up to 5 s.
  * WORD DELAY -- a gap between every byte. Some slow slaves need one.
  * BITS PER WORD -- always 8 until now. 16 and 32 are untried.
  * INTER-TRANSFER GAP between header and body: 2 ms until now; also 0 and 20.
  * HUGE READ WINDOW -- 8 KiB instead of 256 B, in case a reply arrives very
    late or very far into the stream.
  * HAMMERING -- the same command 200 times, then a long read, in case the part
    needs repeated poking.

Detector is the same deliberately crude one: ANY byte that is not 0x00 or 0xff.

Run as root.
"""
import ctypes
import fcntl
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spidev_raw import Spi
from gpio_raw import GpioLine

SPI_DEV = "/dev/spidev1.0"
CHIP = "/dev/gpiochip0"
LINE_RST = 264

SPI_IOC_WR_BITS_PER_WORD = 0x40016B03

NOP = bytes([0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x88])
EVK = bytes.fromhex("a8030000 00ff".replace(" ", ""))
INSTALL = bytes.fromhex("960300010010")


def header(n, ptype=0xA0):
    h = bytes([ptype, n & 0xFF, (n >> 8) & 0xFF])
    return h + bytes([sum(h) & 0xFF])


def odd(buf):
    return sorted(set(buf) - {0x00, 0xFF})


def reset(settle_s):
    g = GpioLine(CHIP, LINE_RST, output=True, value=1)
    g.set(0)
    time.sleep(0.010)
    g.set(1)
    time.sleep(settle_s)
    return g


def probe(spi, gap_s, read_len, word_delay_us=0):
    for body in (NOP, INSTALL, EVK):
        spi.duplex(header(len(body)), word_delay_us=word_delay_us)
        time.sleep(gap_s)
        spi.duplex(body, word_delay_us=word_delay_us)
        time.sleep(0.02)
    return spi.duplex(bytes(read_len), word_delay_us=word_delay_us)


def report(label, rx, hits):
    o = odd(rx)
    if o:
        hits.append((label, o[:12]))
        print("  %-46s  *** NON-IDLE: %s ***" % (label, o[:12]))
    else:
        print("  %-46s  %s only" % (label, " ".join("%02x" % b for b in sorted(set(rx)))))


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    if not os.path.exists(SPI_DEV):
        sys.exit("%s missing" % SPI_DEV)

    print("### wider sweep  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("### detector: any byte that is not 0x00 or 0xff\n")
    hits = []

    print("--- 1. SETTLE TIME after reset (never varied; always 150 ms)")
    for settle in (0.15, 0.5, 1.0, 3.0, 5.0):
        g = reset(settle)
        s = Spi(SPI_DEV, mode=0, speed=1_000_000)
        report("settle %.2f s" % settle, probe(s, 0.002, 512), hits)
        s.close(); g.close()

    print("\n--- 2. WORD DELAY between bytes (never varied; always 0)")
    for wd in (0, 5, 20, 100):
        g = reset(0.2)
        s = Spi(SPI_DEV, mode=0, speed=1_000_000)
        try:
            report("word delay %3d us" % wd, probe(s, 0.002, 512, wd), hits)
        except OSError as e:
            print("  word delay %3d us -> rejected (%s)" % (wd, e))
        s.close(); g.close()

    print("\n--- 3. BITS PER WORD (never varied; always 8)")
    for bpw in (8, 16, 32):
        fd = os.open(SPI_DEV, os.O_RDWR)
        ok = True
        try:
            fcntl.ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, ctypes.c_uint8(bpw))
        except OSError:
            ok = False
        os.close(fd)
        if not ok:
            print("  bits/word %2d -> rejected by driver" % bpw)
            continue
        g = reset(0.2)
        s = Spi(SPI_DEV, mode=0, speed=1_000_000, bits=bpw)
        try:
            report("bits/word %2d" % bpw, probe(s, 0.002, 512), hits)
        except OSError as e:
            print("  bits/word %2d -> transfer failed (%s)" % (bpw, e))
        s.close(); g.close()
    # restore
    fd = os.open(SPI_DEV, os.O_RDWR)
    try:
        fcntl.ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, ctypes.c_uint8(8))
    except OSError:
        pass
    os.close(fd)

    print("\n--- 4. HEADER->BODY GAP (always 2 ms until now)")
    for gap in (0.0, 0.002, 0.020, 0.100):
        g = reset(0.2)
        s = Spi(SPI_DEV, mode=0, speed=1_000_000)
        report("gap %5.0f ms" % (gap * 1000), probe(s, gap, 512), hits)
        s.close(); g.close()

    print("\n--- 5. HUGE READ WINDOW (8 KiB, in case a reply is very late)")
    g = reset(0.2)
    s = Spi(SPI_DEV, mode=0, speed=1_000_000)
    rx = probe(s, 0.002, 8192)
    report("8 KiB read", rx, hits)
    s.close(); g.close()

    print("\n--- 6. HAMMERING: 200 repeats then a long read")
    g = reset(0.2)
    s = Spi(SPI_DEV, mode=0, speed=1_000_000)
    for _ in range(200):
        s.duplex(header(len(EVK)))
        s.duplex(EVK)
    report("after 200 repeats", s.duplex(bytes(4096)), hits)
    s.close(); g.close()

    print("\n" + "=" * 70)
    if hits:
        print("*** SOMETHING APPEARED:")
        for label, o in hits:
            print("      %-40s bytes %s" % (label, o))
    else:
        print("nothing but 0x00/0xff on every axis tested")
    print("=" * 70)


if __name__ == "__main__":
    main()
