#!/usr/bin/env python3
"""
Reference-sequence bring-up for the Goodix GXFP51A0 (Milan-SPI).

Ported from lexakimov/goodix51c0_spi-reversing -- a *working* PoC on a sibling
Goodix Milan-SPI sensor (GDIX51C0) in a Huawei laptop, driven from Linux over
plain spidev.  That project's transport framing and checksum maths are
byte-identical to what we recovered independently from gfspi.dll, which is why
its bring-up sequence is worth taking literally.

Five things here differ from everything we have tried so far:

  1. RESET POLARITY IS INVERTED.  Reference does  HIGH -> 10ms -> LOW -> 10ms
     -> *release the line* -> 150ms settle.  We have always done LOW -> HIGH ->
     and then HELD it high.  The _CRS declares this pad `PullUp`, which is what
     you put on an active-low reset that is meant to REST high.  Our pad is
     BIOS-LOCKED, so it is entirely possible our "drive high" never reaches the
     pin and the part has been sitting in reset the whole time -- whereas
     releasing the line lets the pull-up deassert reset without needing the
     (possibly disabled) output driver.
  2. The line is not held at all during normal operation.
  3. 150 ms settle, not 100 ms.
  4. The first packet after reset is "unlock TLS" (d5 03 00 00 00 d3).  We have
     never sent it.
  5. GetMcuState carries a live millisecond timestamp and uses checksum PLUS
     ONE (verified against the reference's own worked example, af..86).

Run as root.  Reports pad registers at every step so we can see what the
hardware actually did, not what we asked it to do.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spidev_raw import Spi, hexd
from gpio_raw import GpioLine

SPI_DEV   = "/dev/spidev1.0"
CHIP      = "/dev/gpiochip0"
LINE_RST  = 264          # _CRS GpioIo  -> pinctrl pin 189 (UART0_RXD pad)
LINE_IRQ  = 48           # _CRS GpioInt -> pinctrl pin 41  (GSPI0_CLK pad)
PINS      = "/sys/kernel/debug/pinctrl/INT34BB:00/pins"
WATCH     = (41, 189)


# ---------------------------------------------------------------- observation

def pads(tag):
    """Dump the two pads we care about straight from pinctrl debugfs."""
    out = []
    try:
        with open(PINS) as f:
            for ln in f:
                for p in WATCH:
                    if ln.startswith("pin %d " % p):
                        out.append(ln.rstrip())
    except OSError as e:
        out.append("  (pinctrl debugfs unreadable: %s)" % e)
    print("  -- pads %-28s" % tag)
    for ln in out:
        print("       " + ln)


# ------------------------------------------------------------------- protocol

def hx(s):
    return bytes.fromhex(s.replace(" ", ""))


def make_header(ptype, body_len):
    """Transport header: [type][lenLO][lenHI][sum of the three]."""
    h = bytes([ptype, body_len & 0xFF, (body_len >> 8) & 0xFF])
    return h + bytes([sum(h) & 0xFF])


def header_ok(h):
    return len(h) == 4 and h[3] == (sum(h[:3]) & 0xFF)


def cksum(b):
    return (0xAA - sum(b)) & 0xFF


class Ref:
    """The reference's perform_write / perform_read, transaction-for-transaction."""

    def __init__(self, spi, irq):
        self.spi = spi
        self.irq = irq

    def write(self, payload, tag=""):
        payload = hx(payload) if isinstance(payload, str) else payload
        hdr = make_header(0xA0, len(payload))
        # two separate CS transactions, exactly as spidev writebytes() does
        self.spi.duplex(hdr)
        self.spi.duplex(payload)
        print("  TX %-22s hdr=%s body=%s" % (tag, hdr.hex(" "), payload.hex(" ")))

    def wait_irq(self, timeout=0.5):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.irq.get() == 1:
                return True
            time.sleep(0.001)
        return False

    def read(self, tag="", timeout=0.5):
        got_irq = self.wait_irq(timeout)
        hdr = self.spi.duplex(b"\x00" * 4)
        ok = header_ok(hdr)
        n = (hdr[1] | (hdr[2] << 8)) if ok else None
        flag = ""
        if hdr == b"\xff\xff\xff\xff":
            flag = "  (all-FF: the wire is idle)"
        elif hdr == b"\x00\x00\x00\x00":
            flag = "  (all-zero)"
        elif ok and 0 < n <= 4096:
            flag = "  <<<<<<<<<<<<<<<<<< VALID HEADER"
        print("  RX %-22s irq=%d hdr=%s len=%s%s"
              % (tag, got_irq, hdr.hex(" "), n, flag))
        if not ok or not n or n > 4096:
            return None
        body = self.spi.duplex(b"\x00" * n)
        print("     body (%d bytes):" % n)
        print(hexd(body))
        return body


def mcu_state_packet():
    """af 06 00 55 <ms LE16> 00 00 <cksum+1>  -- live timestamp, checksum + 1.

    Verified against the reference's own worked example:
        af 06 00 55 5c bf 00 00 86
    """
    now = time.time()
    ms = (int(now) % 60) * 1000 + int((now % 1) * 1000)
    p = bytes([0xAF, 0x06, 0x00, 0x55, ms & 0xFF, (ms >> 8) & 0xFF, 0x00, 0x00])
    return p + bytes([(cksum(p) + 1) & 0xFF])


# ------------------------------------------------------------------- variants

def reset_reference():
    """HIGH 10ms -> LOW 10ms -> RELEASE -> 150ms.  The one that works elsewhere."""
    g = GpioLine(CHIP, LINE_RST, output=True, value=1)
    pads("after acquire(value=1)")
    time.sleep(0.010)
    g.set(0)
    pads("after drive LOW")
    time.sleep(0.010)
    g.close()                      # <-- release: let the pull-up deassert reset
    pads("after RELEASE")
    time.sleep(0.150)
    pads("after 150ms settle")


def reset_hold_low():
    """Same pulse but keep the line held low, to separate 'low' from 'released'."""
    g = GpioLine(CHIP, LINE_RST, output=True, value=1)
    time.sleep(0.010)
    g.set(0)
    time.sleep(0.150)
    pads("held LOW")
    return g


def reset_release_only():
    """Acquire and immediately release -- no pulse at all."""
    g = GpioLine(CHIP, LINE_RST, output=True, value=1)
    g.close()
    pads("after acquire+release")
    time.sleep(0.150)


def reset_legacy():
    """What we have always done: LOW -> HIGH -> hold high.  Control case."""
    g = GpioLine(CHIP, LINE_RST, output=True, value=1)
    g.set(0)
    time.sleep(0.010)
    g.set(1)
    time.sleep(0.150)
    pads("held HIGH (legacy)")
    return g


def never_touch():
    """Baseline: no GPIO at all."""
    pads("untouched")


VARIANTS = [
    ("reference  (high->low->RELEASE)", reset_reference),
    ("hold-low   (high->low->hold)",    reset_hold_low),
    ("release-only (no pulse)",         reset_release_only),
    ("legacy     (low->high->hold)",    reset_legacy),
    ("baseline   (no GPIO at all)",     never_touch),
]


# ------------------------------------------------------------------- sequence

def probe(speed):
    """The reference's init sequence, in its order, with its exact bytes."""
    spi = Spi(SPI_DEV, mode=0, speed=speed)
    irq = GpioLine(CHIP, LINE_IRQ, output=False)
    r = Ref(spi, irq)
    hit = False
    try:
        # 1. force-unlock TLS -- no ack expected
        r.write("d5 03 00 00 00 d3", "unlockTLS")
        time.sleep(0.05)

        # 2. MCU config upload -- prerequisite for both queries below.
        #    Note the 0x88 checksum: a magic constant, not the formula.
        r.write("01 05 00 00 00 00 00 88", "mcuConfig")
        time.sleep(0.05)

        # 3. GetEvkVersion
        r.write("a8 03 00 00 00 ff", "getEvkVersion")
        if r.read("evkVersion") is not None:
            hit = True
        time.sleep(0.05)

        # 4. GetMcuState (live timestamp, checksum+1)
        r.write(mcu_state_packet(), "getMcuState")
        if r.read("mcuState") is not None:
            hit = True
        time.sleep(0.05)

        # 5. MILAN chip id -- we expect 0x2504 if this lands
        r.write("82 06 00 00 00 00 04 00 1e", "milanChipId")
        if r.read("chipId") is not None:
            hit = True
    finally:
        irq.close()
        spi.close()
    return hit


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    if not os.path.exists(SPI_DEV):
        sys.exit("%s missing -- bind spidev first" % SPI_DEV)

    print("### refseq  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("### uptime %ss" % open("/proc/uptime").read().split(".")[0])
    print()

    wins = []
    for speed in (10_000_000, 1_000_000):
        for name, fn in VARIANTS:
            print("=" * 78)
            print("== %-42s  @ %d kHz" % (name, speed // 1000))
            print("=" * 78)
            held = None
            try:
                held = fn()
                if probe(speed):
                    wins.append("%s @ %dkHz" % (name, speed // 1000))
            except Exception as e:
                print("  !! %s: %r" % (name, e))
            finally:
                if held is not None:
                    try:
                        held.close()
                    except Exception:
                        pass
            pads("end of variant")
            print()
            time.sleep(0.2)

    print("=" * 78)
    if wins:
        print("SENSOR RESPONDED under: %s" % ", ".join(wins))
    else:
        print("no valid header under any variant")
    print("=" * 78)


if __name__ == "__main__":
    main()
