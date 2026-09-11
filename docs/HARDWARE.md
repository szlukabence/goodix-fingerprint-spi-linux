# Hardware: what the sensor is and how it is wired

Machine: **Huawei MateBook 13 (2020)**, board WRTB-WXX9-PCB (ODM Huaqin,
subsystem `1e83:3e1a`), BIOS M1260 v1.26, Intel Comet Lake-U i5-10210U.

## The sensor

Read out of a working Windows session (see [WINDOWS-LOGGING.md](WINDOWS-LOGGING.md)),
so these are measured values, not inferences:

| Property | Value |
|---|---|
| ACPI hardware ID | `GXFP51A0` |
| Chip ID | **`0x2504`** |
| Sensor type | 12 = **"ChicagoHS"** |
| Array geometry | **80 columns × 64 rows** |
| Firmware running | `GF_ST411SEC_APP_14115` (STM32-class MCU) |
| FDT parameters | `fdt_delta` 33, `tcode` 224 |
| Driver build | "GF3658 SPI", `gfspi.dll` v1.1.141.40 |

The driver supports sensor types MilanF, MilanFN, MilanEG, MilanL, MilanHU and
ChicagoHS; the chip ID selects among them.

**The sensor is the power button.** It sits on its own small board joined to the
mainboard by a fine-pitch flex cable.

## Bus and wiring

The sensor is an ACPI device on the PCH's on-package SPI #1 — *not* USB, *not*
I²C, and *not* behind the EC (that is the GXFP51B7 / 5130 architecture on other
MateBooks).

```
PCI 00:1e.3  Intel LPSS SPI #1  [8086:02ab]
  -> pxa2xx-spi -> spi_master/spi1 -> spi-GXFP51A0:00
ACPI path: \_SB.PCI0.SPI1.SPBA
```

From `_CRS`:

| Resource | Setting |
|---|---|
| SPI mode | 0 (CPOL=0, CPHA=0) |
| Word size | 8 bits, MSB first |
| Chip select | 0, active low |
| Max clock | 10 MHz (`0x00989680`) |
| Interrupt | GpioInt, **level, active high** |
| Enable | GpioIo, output only, declared `PullUp` |

On Linux both GPIOs land on `/dev/gpiochip0` (`INT34BB:00`):

| Function | gpiochip line | pinctrl pin | Pad name |
|---|---|---|---|
| Interrupt | **48** | 41 | `GSPI0_CLK` (repurposed) |
| Enable / reset | **264** | 189 | `UART0_RXD` (repurposed) |

Note both control pins are repurposed pads whose names have nothing to do with
their function — do not let the pad names mislead you.

## Pad register state, decoded

All six pads, read from `/sys/kernel/debug/pinctrl/INT34BB:00/pins`. Bit
meanings are Intel PCH `PADCFG0`: bit 0 `GPIOTXSTATE`, bit 1 `GPIORXSTATE`,
bit 8 `GPIOTXDIS`, bit 9 `GPIORXDIS`, bits 12:10 `PMODE`.

```
pin 44 GSPI1_CS0B   mode 1  0x44000700   TXDIS=1 RXDIS=1 PMODE=native   OK
pin 45 GSPI1_CLK    mode 1  0x44000700                                  OK
pin 46 GSPI1_MISO   mode 1  0x44000702   bit1 set -> line idles HIGH    OK
pin 47 GSPI1_MOSI   mode 1  0x44000700                                  OK
pin 41 interrupt    GPIO    0x40100102   TXDIS=1 RXDIS=0 RXEVCFG=level  OK
pin 189 enable      GPIO    0x44000201   TXDIS=0 RXDIS=1                OK
```

Everything is configured correctly. The four bus pads are in native SPI mode
with their GPIO buffers disabled, which is exactly right; the interrupt pad is a
level-triggered input; the enable pad is an output with its driver enabled.

### The `[LOCKED]` flag does not block writes

All these pads report `[LOCKED]` (BIOS set `PADCFGLOCK`). That does **not**
prevent driving the enable line — the output value tracks writes exactly:

```
drive HIGH -> pin 189 = 0x44000201
drive LOW  -> pin 189 = 0x44000200
```

### There is no real pull-up on the enable pad

`dw1 = 0x00000050`; the termination field (bits 13:10) is **0**. Whatever `_CRS`
declares, no pull-up is configured. Releasing the GPIO line leaves the pad
latched at its last driven value rather than floating high — so the
"release the line and let the pull-up deassert reset" idiom that works on some
sibling boards has no analogue here. Drive it explicitly instead.

### The enable line switches something — but read it conservatively

The interrupt pad's level follows the enable pad's level, every time, in both
directions:

```
enable LOW   ->  pin 41 = 0x40100100   (interrupt reads 0)
enable HIGH  ->  pin 41 = 0x40100102   (interrupt reads 1)
```

The tempting reading is that the module powers down and drags the interrupt line
with it. The **more likely** reading is duller: the enable line switches a power
rail, and the interrupt line simply has a pull-up to that rail, so what we are
watching is the rail — not the sensor.

A 30-second recording at 1.1 kHz with a finger repeatedly rested on the sensor
produced no movement at all on that line (100% high, zero transitions in 33,110
samples) and no non-idle SPI read. That is consistent with a passive pull-up.
Treat this as evidence that a rail switches, **not** as evidence that the sensor
MCU is alive.

## ACPI

The device node is minimal: `_ADR`, `_HID`, `_CID`, `_UID`,
`OperationRegion(HWFP)`, `_DSM`, `_INI`, `_STA`, `_CRS`. There is **no**
`_PS0`/`_PS3`/`_PR0`/`_PR3`, no power resource, on it or any ancestor.

- `_INI` does exactly two things: `SHPO(0x04010010, 1)` and `SHPO(0x04020008, 1)`
  — set host software ownership on the two pads. Nothing else.
- `_STA` is gated on the NVS byte `FISW`, which is the BIOS setting that
  enables/disables the fingerprint sensor.
- Nothing anywhere else in the ACPI tables touches these two pads. There is no
  hidden platform, EC or power-management code operating this sensor.

### The pre-boot handoff window

`_DSM` (UUID `cc58b68a-4479-4893-a8bb-961209db59e5`) has two functions:

- function 0 → `0x03` (functions 0 and 1 exist)
- function 1 → a **2048-byte buffer** mapped from the physical address in the
  NVS variable `FPAD`

It is tempting to read this as the platform handing the OS the results of
pre-boot fingerprint work — this laptop advertises power-on-and-login, and in
practice you do land logged in without touching the sensor a second time.
**The evidence does not support that reading.**

- The UEFI firmware contains fingerprint modules (`OemFPDxe`, `OemFPSmm`,
  `FingerPrintSwtitch`), but disassembly shows **no MMIO, no port I/O, no SPI or
  GPIO protocol, and none of the Goodix protocol constants**. They consume only
  SMM base / software-SMI dispatch / SMM access — a policy and setup-switch
  layer, not a sensor driver. The firmware never talks to this sensor.
- **Windows never calls `_DSM`** in a full successful session, so nothing
  collects whatever is in the buffer.

So this is more likely factory-provisioned per-device data than a capture
result, and the power-on-login behaviour remains unexplained.

Contents on this machine: 399 non-zero bytes. Roughly `0x000`–`0x04f` of
per-device configuration, then two 16-byte descriptors at `0x200` and `0x210`
each declaring length 325, followed by ~325 bytes of high-entropy data at
`0x224`–`0x368` — almost certainly per-device key material.

**The bytes are not published here** (see the README's redistribution note); if
you dump your own, treat them as secrets.

Worth knowing: **Windows never calls `_DSM`.** Not once in a full successful
session. Whatever this buffer is for, reading it is not a prerequisite for
talking to the sensor.

## The enable line gates a rail with ~89 ms of bulk capacitance (measured)

Sweeping the enable-low hold time and watching the interrupt pad's live level at
7.6 µs resolution gives a constant offset:

| enable held LOW | interrupt line LOW for | difference |
|---|---|---|
| 100 ms | 11.2 ms | 88.8 ms |
| 200 ms | 111.2 ms | 88.8 ms |
| 400 ms | 311.2 ms | 88.8 ms |
| 800 ms | 711.2 ms | 88.8 ms |
| 1600 ms | 1511.2 ms | 88.8 ms |

Constant to one decimal across a 16× range. The line **falls 88.8 ms late and
rises immediately** (rise faster than 7.6 µs). Slow passive fall, fast active
rise is a capacitor discharging through a high impedance and then being driven.

**Practical consequence, and it is easy to get wrong:** a short reset pulse does
not remove power. Anything under ~90 ms leaves the rail above threshold and the
part is never actually power-cycled. Our own tooling used a 10 ms pulse for a
long time, which was a no-op.

(For completeness: correcting this did *not* make the sensor respond. Resets of
300 ms and 1000 ms, and true power cycles with settles of 3 s and 8 s, all still
return `0xff`. But anyone reproducing this should still use a pulse longer than
90 ms so the variable is actually being tested.)

## The interrupt line never moves — measured at 7.6 µs

Sampling the interrupt pad's `PADCFG0` register in a tight kernel loop, 395,053
samples across 3 s, while sending NOP → DriverState Install → GetEvkVersion →
ChipId → ReadOTP: **exactly two transitions, both from a deliberate rail cut
used as a built-in control.** The sensor never touched the line.

On Windows the same line pulses once per reply, roughly 16 ms after each
command (`MilanEvtInterruptIsr`, 333 occurrences). Windows never reads the bus
blindly — every read happens inside that ISR. We read blind, because there is
never an interrupt to wait for.

### If you write a kernel probe for these pads, read this first

`ioremap()` on the GPIO community returns a **cached** alias. A single read after
mapping is correct; reading the same address in a loop returns the first value
forever. This produces perfectly plausible, completely false data — we recorded
"100% HIGH, zero transitions" across a deliberate rail cut that a single-shot
read at the same instant showed as LOW.

Use `ioremap_uc()`. A tell-tale is sample rate: ~130 samples/ms (7.7 µs each) is
far too slow for genuinely uncached MMIO.

The same caution applies to the GPIO **edge detector** on this board. Because the
interrupt line sits in its asserted state, `gpiomon` returns whatever edge type
you ask for at a fixed ~240 µs cadence indefinitely — 37,062 "rising" and zero
"falling" in 9 seconds. A line must fall before it can rise; that output is an
artifact of a stuck level, not a signal.

**Put a control inside every measurement window.** A deliberate rail cut, whose
effect *must* appear, is what caught all three of the artifacts above.
