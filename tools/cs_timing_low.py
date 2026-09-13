#!/usr/bin/env python3
"""
CS setup / hold / inactive timing, re-run with pin 189 LOW (AUDIT AH3).

A5 swept these through the kernel module, whose reset left pin 189 HIGH. Here it
is done from spidev, so the pin can be held in the Windows state:

  setup    zero-length first segment with delay_usecs -> CS asserted, wait, then data
  hold     delay_usecs on the data segment -> wait before CS is released
  inactive sleep between messages (CS released)

CONTROL: the ioctl duration is timed at 0 and at 1000 us, so we know the delays
were really applied rather than silently ignored.

Detector: any non-0xff byte, or an interrupt pulse within 30 ms of a command.
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

BODIES = [
    ("NOP",          bytes.fromhex("0005000000000088")),
    ("0x96 Install", bytes.fromhex("960300010010")),
    ("NOP",          bytes.fromhex("0005000000000088")),
    ("0xa8 EVK",     bytes.fromhex("a8030070dcb3")),
    ("0x82 ChipReg", bytes.fromhex("82060000000004001e")),
]


def header(n):
    h = bytes([0xA0, n & 0xFF, n >> 8])
    return h + bytes([sum(h) & 0xFF])


def send(spi, data, setup, hold):
    segs = []
    if setup:
        segs.append((b"", 0, 0, setup))
    segs.append((data, len(data), 0, hold))
    return spi.xfer(segs)[-1]


def pulse(irq, ms):
    t = time.monotonic()
    while (time.monotonic() - t) * 1000 < ms:
        if irq.get():
            return True
        time.sleep(0.0002)
    return False


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    print("### CS timing with pin 189 LOW  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    irq = GpioLine(CHIP, LINE_IRQ, output=False)
    en = GpioLine(CHIP, LINE_EN, output=True, value=1)
    time.sleep(0.3)
    en.set(0)
    time.sleep(0.6)
    print("189 LOW, interrupt = %d (expect 0)" % irq.get())

    spi = Spi(SPI_DEV, mode=0, speed=1_000_000)
    # control: are the delays applied?
    for label, s, h in (("no delay", 0, 0), ("setup 1000 + hold 1000 us", 1000, 1000)):
        t = time.monotonic()
        for _ in range(50):
            send(spi, b"\xff" * 4, s, h)
        print("control %-28s %.2f ms per transfer" % (label, (time.monotonic() - t) * 1000 / 50))
    spi.close()

    hits = []
    for speed in (1_000_000, 10_000_000):
        for setup, hold, inactive in ((0, 0, 0), (10, 10, 10), (50, 50, 50),
                                      (200, 200, 200), (1000, 1000, 1000), (0, 0, 500)):
            for framing in ("two", "one"):
                spi = Spi(SPI_DEV, mode=0, speed=speed)
                seen, pulses = set(), 0
                for name, body in BODIES:
                    frames = [header(len(body)), body] if framing == "two" else [header(len(body)) + body]
                    for f in frames:
                        send(spi, f, setup, hold)
                        time.sleep(inactive / 1e6 if inactive else 0.002)
                    p = pulse(irq, 30)
                    rx = send(spi, b"\xff" * 4, setup, hold)
                    pulses += p
                    seen.update(rx)
                spi.close()
                label = "%5d kHz setup %4d hold %4d inactive %4d %s-transfer" % (
                    speed // 1000, setup, hold, inactive, framing)
                active = pulses or seen != {0xFF}
                print("  %s  bytes %s  irq pulses %d%s" % (
                    label, " ".join("%02x" % b for b in sorted(seen)), pulses,
                    "   <<< ACTIVITY" if active else ""))
                if active:
                    hits.append(label)

    en.set(1)
    ctl = pulse(irq, 50)
    en.set(0)
    time.sleep(0.3)
    print("control: 189 HIGH -> interrupt high: %s; back LOW -> %d" % (ctl, irq.get()))
    en.close()
    irq.close()
    print("ACTIVITY: %s" % (", ".join(hits) if hits else "none"))


if __name__ == "__main__":
    main()
