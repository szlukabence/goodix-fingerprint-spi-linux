#!/usr/bin/env python3
"""
Board discovery for GXFP51A0 tools.

Reported by @xamelllion (HONOR BBR-WAX9, repo issue #1): the same sensor sits on
a DIFFERENT spidev node and a DIFFERENT interrupt line on another board, so every
hardcoded /dev/spidev1.0 + line 48/264 tool fails or, worse, talks to the BIOS
flash. Both boards' values are discovered correctly by what is below.

  spidev node : /sys/bus/spi/devices/<sensor>/spidev/  -> the real node name
  GPIO lines  : /sys/kernel/debug/pinctrl/INT34BB:*/pins, which prints
                  pin 41 (GSPI0_CLK) 48:INT34BB:00 GPIO 0x40100100 ...
                       ^pad            ^gpio line          ^PADCFG0
                PADCFG0 bit8  GPIOTXDIS (1 = output off -> input)
                        bit9  GPIORXDIS (1 = input off  -> output)
                        bit20 GPIROUTIOXAPIC (interrupt routed)
                Pads owned by ACPI are printed with "ACPI" and are not ours.

Nothing here drives a pin; it only reads, classifies and warns. Explicit
overrides always win:  --spidev/--reset/--irq  or  GXFP_SPIDEV/GXFP_RESET/GXFP_IRQ
"""
import glob
import os
import re

SENSOR_GLOB = "/sys/bus/spi/devices/*GXFP*"
PINCTRL_GLOB = "/sys/kernel/debug/pinctrl/INT34BB:*/pins"

PAD_RE = re.compile(
    r"^pin\s+(\d+)\s+\(([^)]*)\)\s+(?:(\d+):(\S+))?\s*(.*)$")


def find_sensor_device():
    for p in sorted(glob.glob(SENSOR_GLOB)):
        return p
    return None


def find_spidev():
    """The node the sensor is actually bound to -- never assume spidev1.0."""
    dev = find_sensor_device()
    if dev:
        for name in os.listdir(os.path.join(dev, "spidev")) if os.path.isdir(
                os.path.join(dev, "spidev")) else []:
            return "/dev/" + name
    # fall back: any spidev whose parent is the sensor
    for p in glob.glob("/sys/class/spidev/spidev*"):
        real = os.path.realpath(p)
        if "GXFP" in real:
            return "/dev/" + os.path.basename(p)
    return None


def read_pads():
    """[{pin, name, line, chip, cfg0, acpi, mode}] from pinctrl debugfs (root)."""
    pads = []
    for path in glob.glob(PINCTRL_GLOB):
        try:
            text = open(path).read()
        except OSError:
            continue
        for line in text.splitlines():
            m = PAD_RE.match(line.strip())
            if not m:
                continue
            pin, name, gpio, chip, rest = m.groups()
            cfg = re.search(r"0x([0-9a-f]{8})", rest)
            pads.append(dict(
                pin=int(pin), name=name,
                line=int(gpio) if gpio else None, chip=chip,
                cfg0=int(cfg.group(1), 16) if cfg else None,
                acpi="ACPI" in rest,
                gpio_mode=" GPIO " in (" " + rest + " ") or rest.startswith("GPIO"),
                raw=rest.strip()))
    return pads


def is_output(cfg0):
    return cfg0 is not None and not (cfg0 & 0x100)      # GPIOTXDIS clear


def is_input(cfg0):
    return cfg0 is not None and not (cfg0 & 0x200)      # GPIORXDIS clear


def routed_to_ioxapic(cfg0):
    return cfg0 is not None and bool(cfg0 & 0x00100000)


def classify(pads):
    """Candidate reset lines (host-owned GPIO outputs) and interrupt lines
    (host-owned GPIO inputs routed to the IOxAPIC)."""
    resets, irqs = [], []
    for p in pads:
        if p["acpi"] or p["line"] is None or p["cfg0"] is None or not p["gpio_mode"]:
            continue
        if is_output(p["cfg0"]) and not is_input(p["cfg0"]):
            resets.append(p)
        if is_input(p["cfg0"]) and routed_to_ioxapic(p["cfg0"]):
            irqs.append(p)
    return resets, irqs


def gpiochip_path(chip_label):
    for d in sorted(glob.glob("/sys/bus/gpio/devices/gpiochip*")):
        try:
            if open(os.path.join(d, "label")).read().strip() == chip_label:
                return "/dev/" + os.path.basename(d)
        except OSError:
            pass
    return "/dev/gpiochip0"


def resolve(spidev=None, reset=None, irq=None, quiet=False):
    """Return (spidev_path, chip_path, reset_line, irq_line). Overrides win;
    anything not given is discovered and sanity-checked, with warnings."""
    spidev = spidev or os.environ.get("GXFP_SPIDEV") or find_spidev()
    reset = reset if reset is not None else os.environ.get("GXFP_RESET")
    irq = irq if irq is not None else os.environ.get("GXFP_IRQ")
    reset = int(reset) if reset not in (None, "") else None
    irq = int(irq) if irq not in (None, "") else None

    pads = read_pads()
    cand_rst, cand_irq = classify(pads)
    by_line = {p["line"]: p for p in pads if p["line"] is not None}
    chip = gpiochip_path(pads[0]["chip"]) if pads and pads[0]["chip"] else "/dev/gpiochip0"

    def say(*a):
        if not quiet:
            print(*a)

    if not spidev:
        say("!! no spidev node found for the sensor. Bind it first:")
        say("   echo spidev | sudo tee /sys/bus/spi/devices/spi-GXFP51A0:00/driver_override")
        say("   echo spi-GXFP51A0:00 | sudo tee /sys/bus/spi/drivers/spidev/bind")
    else:
        say("spidev      %s" % spidev)

    # Reset: default to the one both known boards use, but verify its shape.
    if reset is None:
        reset = 264 if 264 in by_line and not by_line[264]["acpi"] else (
            cand_rst[0]["line"] if cand_rst else None)
    if reset is not None and reset in by_line:
        p = by_line[reset]
        ok = is_output(p["cfg0"]) and not p["acpi"]
        say("reset line  %d  (pin %d %s, PADCFG0 0x%08x)%s"
            % (reset, p["pin"], p["name"], p["cfg0"], "" if ok else "   <-- NOT a host-owned output!"))
        if not ok:
            say("            candidates: %s" % ", ".join(
                "%d(pin %d %s)" % (c["line"], c["pin"], c["name"]) for c in cand_rst) or "none")
    else:
        say("reset line  %s  (not found in pinctrl -- pass --reset)" % reset)

    # Interrupt: must be a host-owned input routed to the IOxAPIC on THIS board.
    if irq is None:
        if 48 in by_line and by_line[48] in cand_irq:
            irq = 48
        elif len(cand_irq) == 1:
            irq = cand_irq[0]["line"]
        elif cand_irq:
            irq = cand_irq[0]["line"]
    if irq is not None and irq in by_line:
        p = by_line[irq]
        ok = p in cand_irq
        say("irq line    %d  (pin %d %s, PADCFG0 0x%08x)%s"
            % (irq, p["pin"], p["name"], p["cfg0"], "" if ok else "   <-- not an IOxAPIC input!"))
        if not ok or len(cand_irq) > 1:
            say("            candidates: %s" % (", ".join(
                "%d(pin %d %s)" % (c["line"], c["pin"], c["name"]) for c in cand_irq) or "none"))
    else:
        say("irq line    %s  (not found -- pass --irq)" % irq)

    say("gpiochip    %s" % chip)
    return spidev, chip, reset, irq


def add_args(ap):
    ap.add_argument("--spidev", help="spidev node (default: discovered)")
    ap.add_argument("--reset", type=int, help="reset GPIO line (default: discovered)")
    ap.add_argument("--irq", type=int, help="interrupt GPIO line (default: discovered)")
    return ap


if __name__ == "__main__":
    import sys
    if os.geteuid() != 0:
        print("note: run as root to read pinctrl debugfs\n")
    spidev, chip, rst, irq = resolve()
    print("\nuse:  --spidev %s --reset %s --irq %s" % (spidev, rst, irq))
    sys.exit(0)
