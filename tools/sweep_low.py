#!/usr/bin/env python3
"""
Re-run of the big command sweep with pin 189 held LOW (the Windows working
state, AUDIT AG). dialects.py / bruteforce.py always left 189 HIGH.

With 189 LOW and the interrupt settled LOW:
    clock modes 0-3 at 1 MHz
    x all 128 Milan commands (cmd0 0x0-0xF x cmd1 0-7), one transfer per frame
    after each: poll the interrupt 30 ms for a HIGH pulse, read 4 bytes (0xff dummy)
Detector: any non-0xff byte OR any interrupt pulse.

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


def milan(cmd0, cmd1, payload=b"\x00\x00"):
    cmd = ((cmd0 << 4) | (cmd1 << 1)) & 0xFF
    n = len(payload) + 1
    body = bytes([cmd, n & 0xFF, (n >> 8) & 0xFF]) + payload
    body += bytes([(0xAA - sum(body)) & 0xFF])
    hdr = bytes([0xA0, len(body) & 0xFF, len(body) >> 8])
    return hdr + bytes([sum(hdr) & 0xFF]) + body


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
    print("### sweep with pin 189 LOW  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    irq = GpioLine(CHIP, LINE_IRQ, output=False)
    en = GpioLine(CHIP, LINE_EN, output=True, value=1)
    time.sleep(0.3)
    en.set(0)
    time.sleep(0.6)
    print("189 LOW, settled interrupt = %d (expect 0)" % irq.get())

    hits, seen, sent = [], set(), 0
    for mode in (0, 1, 2, 3):
        spi = Spi(SPI_DEV, mode=mode, speed=1_000_000)
        for cmd0 in range(16):
            for cmd1 in range(8):
                spi.duplex(milan(cmd0, cmd1))
                sent += 1
                p = pulse(irq, 30)
                rx = spi.duplex(b"\xff" * 4)
                seen.update(rx)
                if p or set(rx) != {0xFF}:
                    hits.append("mode %d cmd %x-%d irq=%d rx=%s" % (mode, cmd0, cmd1, p, rx.hex(" ")))
            if irq.get():
                print("   note: interrupt high after cmd0 %x in mode %d" % (cmd0, mode))
        spi.close()
        print("mode %d done, interrupt = %d" % (mode, irq.get()))

    en.set(1)
    ctl_hi = pulse(irq, 50)
    en.set(0)
    time.sleep(0.3)
    print("control: 189 HIGH -> interrupt high: %s; back LOW -> %d" % (ctl_hi, irq.get()))
    en.close()
    irq.close()

    print("\n" + "=" * 70)
    print("commands sent %d; distinct bytes seen: %s" % (sent, " ".join("%02x" % b for b in sorted(seen))))
    print("ACTIVITY: %d" % len(hits))
    for h in hits[:40]:
        print("  " + h)
    print("=" * 70)


if __name__ == "__main__":
    main()
