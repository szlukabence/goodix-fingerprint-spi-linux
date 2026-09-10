#!/usr/bin/env python3
"""
Sigfrodr-style recovery, then probe.

Sigfrodr/libfprint-goodixtls (a WORKING driver for the sibling GXFP5187)
documents a wedged state where short commands still work but long transfers
fail, and notes that neither a reset pulse, nor a one-second hold, nor
reopening the spidev node clears it *on its own*:

    "A long reset AND the detach/reattach of the spidev kernel driver must be
     combined, as its state too must be reset."

We have never unbound spidev between attempts -- every experiment so far has
reused the same binding for the life of the boot.  If the *host controller's*
driver state is what is wedged rather than the sensor's, that would look
exactly like what we see: a perfectly configured bus that never receives.

Run as root.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gpio_raw import GpioLine
import refseq

DEV_NAME = "spi-GXFP51A0:00"
SYS_DEV = "/sys/bus/spi/devices/" + DEV_NAME
DRV = "/sys/bus/spi/drivers/spidev"
CHIP = "/dev/gpiochip0"
LINE_RST = 264


def w(path, val):
    try:
        with open(path, "w") as f:
            f.write(val)
        return True
    except OSError as e:
        print("     (write %s <- %s failed: %s)" % (path, val, e))
        return False


def unbind():
    ok = w(DRV + "/unbind", DEV_NAME)
    time.sleep(0.2)
    print("  spidev unbind: %s   /dev/spidev1.0 exists=%s"
          % ("ok" if ok else "FAILED", os.path.exists("/dev/spidev1.0")))


def bind():
    if not os.path.exists(SYS_DEV + "/driver"):
        w(SYS_DEV + "/driver_override", "spidev")
        w(DRV + "/bind", DEV_NAME)
    time.sleep(0.3)
    print("  spidev bind:   /dev/spidev1.0 exists=%s" % os.path.exists("/dev/spidev1.0"))


def hold_reset(low_ms, then_high=True, settle_ms=150):
    g = GpioLine(CHIP, LINE_RST, output=True, value=1)
    g.set(0)
    time.sleep(low_ms / 1000.0)
    if then_high:
        g.set(1)
        time.sleep(settle_ms / 1000.0)
    return g


def variant_long_reset_with_rebind():
    """The full Sigfrodr recipe: long reset AND spidev detach/reattach."""
    unbind()
    g = hold_reset(1000)          # a full second, held low while spidev is gone
    bind()
    time.sleep(0.2)
    return g


def variant_rebind_only():
    """Isolate the rebind: no long reset."""
    unbind()
    bind()
    time.sleep(0.2)
    return None


def variant_long_reset_only():
    """Isolate the long reset: no rebind."""
    return hold_reset(1000)


def variant_reset_while_unbound():
    """Reset asserted with spidev detached, released only after reattach."""
    unbind()
    g = GpioLine(CHIP, LINE_RST, output=True, value=1)
    g.set(0)
    time.sleep(0.5)
    bind()
    g.set(1)
    time.sleep(0.15)
    return g


VARIANTS = [
    ("full recipe: long reset + rebind", variant_long_reset_with_rebind),
    ("rebind only",                      variant_rebind_only),
    ("long reset only (1 s low)",        variant_long_reset_only),
    ("reset held across the rebind",     variant_reset_while_unbound),
]


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")

    print("### recover_probe  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("### uptime %ss" % open("/proc/uptime").read().split(".")[0])
    print()

    wins = []
    for name, fn in VARIANTS:
        for speed in (10_000_000, 1_000_000):
            print("=" * 78)
            print("== %-40s @ %d kHz" % (name, speed // 1000))
            print("=" * 78)
            held = None
            try:
                held = fn()
                refseq.pads("after recovery")
                if not os.path.exists("/dev/spidev1.0"):
                    print("  !! no spidev node -- skipping probe")
                    continue
                if refseq.probe(speed):
                    wins.append("%s @ %dkHz" % (name, speed // 1000))
            except Exception as e:
                print("  !! %s: %r" % (name, e))
            finally:
                if held is not None:
                    try:
                        held.close()
                    except Exception:
                        pass
            print()
            time.sleep(0.3)

    print("=" * 78)
    print("SENSOR RESPONDED under: %s" % ", ".join(wins) if wins
          else "no valid header under any recovery variant")
    print("=" * 78)


if __name__ == "__main__":
    main()
