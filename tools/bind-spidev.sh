#!/bin/bash
# Bind the Goodix GXFP51A0 to spidev and keep the SPI controller awake.
# Run as root, before any of the Python tools.
set -e

DEV=spi-GXFP51A0:00
SYS=/sys/bus/spi/devices/$DEV

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

if [ ! -e "$SYS" ]; then
    echo "$DEV not present under /sys/bus/spi/devices/" >&2
    echo "check that the ACPI device exists:" >&2
    echo "    ls /sys/bus/acpi/devices/ | grep GXFP" >&2
    echo "and that it is enabled in the BIOS." >&2
    exit 1
fi

# our own out-of-tree module would hold the GPIO descriptors
if lsmod | grep -q '^gxfp '; then
    echo "unloading gxfp module (it holds the GPIO descriptors)"
    rmmod gxfp
fi

modprobe spidev

if [ ! -e /dev/spidev1.0 ]; then
    echo spidev > "$SYS/driver_override"
    echo "$DEV"  > /sys/bus/spi/drivers/spidev/bind
    sleep 0.3
fi

# keep the controller out of runtime suspend for the whole session
echo on > /sys/bus/pci/devices/0000:00:1e.3/power/control
echo on > /sys/bus/platform/devices/pxa2xx-spi.4/power/control 2>/dev/null || true

ls -l /dev/spidev1.0
echo "ready -- now run: sudo python3 refseq.py"
