#!/usr/bin/env python3
"""
Does the sensor react to a finger?

The Milan command set has a whole finger-detect group (cmd0=0x3 FDT down/up/
manual, plus NAV and a sleep mode).  That is the command set of a sensor built
to watch for a finger on its own and wake itself up -- which is exactly what the
"press the power button and you are logged in" feature would require, because at
the moment the finger lands the host does not exist yet.

If that is how this part works, it is not asleep waiting to be woken.  It is
awake, watching, and simply not answering us.

The interrupt line is level-triggered ACTIVE HIGH, i.e. "I have something for
you".  We have measured it sitting high the whole time the module is powered.
If that line moves when a finger lands, the sensor is alive and sensing, and the
fault is purely in our receive path -- which is the same discrimination a logic
analyser would give us, for free.

Records the interrupt line at ~2 kHz plus the raw pinctrl pad registers, and
fires periodic SPI reads, for 30 s.  Run as root.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gpio_raw import GpioLine
from spidev_raw import Spi

CHIP = "/dev/gpiochip0"
LINE_IRQ = 48
LINE_RST = 264
PINS = "/sys/kernel/debug/pinctrl/INT34BB:00/pins"
SPI_DEV = "/dev/spidev1.0"
DURATION = 30.0
SPI_EVERY = 0.5


def pad(pin):
    """Raw pad register, read straight from pinctrl - unaffected by gpiod."""
    try:
        with open(PINS) as f:
            for ln in f:
                if ln.startswith("pin %d " % pin):
                    for tok in ln.split():
                        if tok.startswith("0x") and len(tok) == 10:
                            return int(tok, 16)
    except OSError:
        pass
    return None


def main():
    if os.geteuid() != 0:
        sys.exit("run as root")

    print("### touchwatch  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print()

    # power the module and hold it powered for the whole run
    rst = GpioLine(CHIP, LINE_RST, output=True, value=1)
    time.sleep(0.2)
    irq = GpioLine(CHIP, LINE_IRQ, output=False)

    p41, p189 = pad(41), pad(189)
    print("  enable pad 189 = 0x%08x   irq pad 41 = 0x%08x" % (p189 or 0, p41 or 0))
    print("  irq level at start = %d" % irq.get())
    print()
    print("  Rest a finger on the power button ~3 s, lift ~3 s, repeat.")
    print("  REST it - do NOT press it in.")
    print("  Starting in 12 s, then recording for %d s." % DURATION)
    for i in range(12, 0, -1):
        print("    %d..." % i, flush=True)
        time.sleep(1)
    print("  GO - touch and lift, repeatedly, until it says done.", flush=True)
    print()

    spi = None
    try:
        spi = Spi(SPI_DEV, mode=0, speed=1_000_000)
    except OSError as e:
        print("  (no spidev: %s -- GPIO-only run)" % e)

    t0 = time.time()
    last = irq.get()
    transitions = []
    samples = 0
    highs = 0
    next_spi = t0 + SPI_EVERY
    spi_reads = []
    pad_changes = []
    last_pad41 = p41
    next_pad = t0 + 0.25

    while True:
        now = time.time()
        if now - t0 >= DURATION:
            break
        v = irq.get()
        samples += 1
        highs += v
        if v != last:
            transitions.append((now - t0, last, v))
            print("  t=%6.3f s   IRQ %d -> %d   <<<<<< CHANGE" % (now - t0, last, v))
            last = v

        if now >= next_pad:
            next_pad = now + 0.25
            cur = pad(41)
            if cur != last_pad41:
                pad_changes.append((now - t0, last_pad41, cur))
                print("  t=%6.3f s   pad41 0x%08x -> 0x%08x   <<<<<< PAD CHANGE"
                      % (now - t0, last_pad41 or 0, cur or 0))
                last_pad41 = cur

        if spi and now >= next_spi:
            next_spi = now + SPI_EVERY
            try:
                rx = spi.duplex(b"\x00" * 4)
                spi_reads.append((now - t0, rx))
                if rx not in (b"\xff\xff\xff\xff", b"\x00\x00\x00\x00"):
                    print("  t=%6.3f s   SPI rx=%s   <<<<<< NON-IDLE"
                          % (now - t0, rx.hex(" ")))
            except OSError:
                pass

        time.sleep(0.0005)

    print()
    print("=" * 70)
    print("  samples          : %d  (%.0f Hz)" % (samples, samples / DURATION))
    print("  IRQ transitions  : %d" % len(transitions))
    print("  IRQ high         : %.1f%% of samples" % (100.0 * highs / max(1, samples)))
    print("  pad-41 changes   : %d" % len(pad_changes))
    uniq = set(bytes(r) for _, r in spi_reads)
    print("  SPI reads        : %d, distinct values: %s"
          % (len(spi_reads), ", ".join(sorted(u.hex(" ") for u in uniq)) or "none"))
    print()
    if transitions or pad_changes or (uniq - {b"\xff\xff\xff\xff"}):
        print("  *** THE SENSOR REACTED TO SOMETHING ***")
    else:
        print("  no reaction of any kind: interrupt line never moved, pad never")
        print("  changed, SPI never returned anything but idle.")
    print("=" * 70)

    if spi:
        spi.close()
    irq.close()
    rst.close()


if __name__ == "__main__":
    main()
