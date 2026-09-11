#!/usr/bin/env python3
"""
Does a suspend/resume cycle change anything?

Under Windows the sensor works after a resume.  We have never tested Linux
across a sleep cycle, and suspend is not just a small reboot: rails drop and
come back, the PCH re-runs parts of its power sequencing, the LPSS controller
is re-initialised by the kernel, and pinctrl restores pad state from its own
saved copy rather than from BIOS.  Any of those could differ from the
cold/warm boot paths we have already exhausted.

Uses rtcwake so the machine wakes itself on a timer -- pressing the power
button would touch the sensor and contaminate the test.

Run as root.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gpio_raw import GpioLine
import refseq

CHIP = "/dev/gpiochip0"
LINE_RST = 264
SLEEP_SECONDS = 20


def flush():
    sys.stdout.flush()
    os.fsync(sys.stdout.fileno()) if sys.stdout.seekable() else None


def stage(label):
    print()
    print("=" * 74)
    print("== %s" % label)
    print("=" * 74)
    sys.stdout.flush()


def probe_all(tag):
    """Pads + the reference bring-up at both speeds, without touching GPIO."""
    refseq.pads(tag)
    hit = False
    for sp in (10_000_000, 1_000_000):
        print("  -- probe @ %d kHz" % (sp // 1000))
        try:
            if refseq.probe(sp):
                hit = True
        except Exception as e:
            print("     !! %r" % e)
    sys.stdout.flush()
    return hit


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")
    if not os.path.exists("/dev/spidev1.0"):
        sys.exit("/dev/spidev1.0 missing -- bind spidev first")

    print("### suspendtest  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("### uptime %ss" % open("/proc/uptime").read().split(".")[0])
    sys.stdout.flush()

    results = {}

    stage("A. BEFORE SUSPEND - untouched")
    results["before-untouched"] = probe_all("before, no GPIO touched")

    stage("B. BEFORE SUSPEND - sensor powered")
    g = GpioLine(CHIP, LINE_RST, output=True, value=1)
    time.sleep(0.2)
    results["before-powered"] = probe_all("before, enable HIGH")
    g.close()

    stage("C. SUSPENDING for %d s (rtcwake -m mem)" % SLEEP_SECONDS)
    print("  the machine will sleep and wake itself; do not touch anything")
    sys.stdout.flush()
    t0 = time.time()
    try:
        r = subprocess.run(["rtcwake", "-m", "mem", "-s", str(SLEEP_SECONDS)],
                           capture_output=True, text=True, timeout=300)
        print("  rtcwake rc=%d %s %s" % (r.returncode, r.stdout.strip(), r.stderr.strip()))
    except Exception as e:
        print("  !! rtcwake failed: %r" % e)
        print("  (continuing anyway - the test is meaningless without a real sleep)")
    slept = time.time() - t0
    print("  wall time across the call: %.1f s  (expect >= %d if it really slept)"
          % (slept, SLEEP_SECONDS))
    sys.stdout.flush()

    stage("D. AFTER RESUME - immediately, untouched")
    results["after-untouched"] = probe_all("after resume, no GPIO touched")

    stage("E. AFTER RESUME - sensor powered")
    g = GpioLine(CHIP, LINE_RST, output=True, value=1)
    time.sleep(0.2)
    results["after-powered"] = probe_all("after resume, enable HIGH")

    stage("F. AFTER RESUME - reset pulse then probe")
    g.set(0)
    time.sleep(0.010)
    g.set(1)
    time.sleep(0.150)
    results["after-reset"] = probe_all("after resume + reset pulse")
    g.close()

    print()
    print("=" * 74)
    for k, v in results.items():
        print("  %-22s %s" % (k, "*** RESPONDED ***" if v else "no reply"))
    if any(results.values()):
        print()
        print("  *** SOMETHING CHANGED ACROSS SUSPEND/RESUME ***")
    else:
        print()
        print("  suspend/resume changes nothing: still ff ff ff ff throughout")
    print("=" * 74)


if __name__ == "__main__":
    main()
