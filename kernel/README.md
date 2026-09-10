# `gxfp` — out-of-tree Linux driver

A bring-up and diagnostic driver for the Goodix GXFP51A0. It claims the ACPI
resources properly and exposes debugfs knobs so the protocol can be exercised
without rebuilding the module every time.

This is **not** a finished input driver — it deliberately stops short of
libfprint integration, because the sensor does not yet reply on this machine
(see [../docs/OPEN-QUESTION.md](../docs/OPEN-QUESTION.md)). It is the right
starting point for someone who gets past that.

## Build and load

```sh
make
sudo insmod gxfp.ko
```

Requires kernel headers for the running kernel. Matches ACPI HID `GXFP51A0`.

## debugfs

Under `/sys/kernel/debug/gxfp/`:

| Knob | Purpose |
|---|---|
| `enable` | read/write the enable GPIO level |
| `reset` | pulse reset; write the low-time in ms |
| `irq_count`, `irq_level` | interrupt statistics and current level |
| `cmd` | send a protocol command |
| `raw` | raw SPI transfer |
| `dsm` | evaluate ACPI `_DSM` and dump the 2 KB handoff window |
| `ctlregs` | dump the LPSS SPI controller registers |
| `cstest`, `replay`, `padprobe` | chip-select timing, transcript replay, pad probing |

## Module parameters

| Parameter | Default | Meaning |
|---|---|---|
| `touch_enable` | 1 | drive the enable GPIO high (1) / low (0) / leave alone (-1) at probe |
| `cs_setup_us`, `cs_hold_us`, `cs_inactive_us` | | chip-select timing overrides |
| `lpss_phys`, `gpio_com0` | | physical addresses for register probing |

`touch_enable=-1` matters more than it looks: **acquiring a gpiod descriptor
drives the pad as a side effect**, so any measurement of the pin's boot state
must be taken before the descriptor is claimed, or with this set to -1.

## If you are picking this up

The userspace tools in [../tools/](../tools/) are faster to iterate with — they
speak the same protocol over `spidev` with no rebuild cycle. Use them to find
what works, then move it in here. `tools/bind-spidev.sh` unloads this module
first, because it holds the GPIO descriptors.
