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

### The enable line is physically real

The single piece of direct physical evidence in this project. The interrupt
pad's level follows the enable pad's level, every time, in both directions:

```
enable LOW   ->  pin 41 = 0x40100100   (interrupt reads 0)
enable HIGH  ->  pin 41 = 0x40100102   (interrupt reads 1)
```

Powering the module down drags the interrupt line down with it. Something on
the far end of that wire exists and responds.

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

This is how the platform hands the OS the results of its pre-boot fingerprint
work — on this laptop, pressing the power button both powers on and reads your
finger, and you land logged in without touching the sensor again.

Contents on this machine: 399 non-zero bytes. Roughly `0x000`–`0x04f` of
per-device configuration, then two 16-byte descriptors at `0x200` and `0x210`
each declaring length 325, followed by ~325 bytes of high-entropy data at
`0x224`–`0x368` — almost certainly per-device key material.

**The bytes are not published here** (see the README's redistribution note); if
you dump your own, treat them as secrets.

Worth knowing: **Windows never calls `_DSM`.** Not once in a full successful
session. Whatever this buffer is for, reading it is not a prerequisite for
talking to the sensor.
