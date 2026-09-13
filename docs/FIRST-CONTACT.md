# First contact — how GXFP51A0 was made to answer on Linux

**2026-09-13, MateBook 13 2020 (WRTB-WXX9).** First non-idle bytes ever received
from this part on Linux, after roughly 300 attempts that all read `0xff`.

## The two conditions

Both are required. Either one alone gives silence.

| | Setting | Why it was missed |
|---|---|---|
| 1 | **Pin 189 (`gpiochip0` line 264) held LOW** | We believed it was a power enable (HIGH = on). It is an **active-HIGH reset**: HIGH holds the MCU in reset. Almost every earlier test drove it HIGH. |
| 2 | **spidev mode 0 with `SPI_CS_HIGH` (`0x04`)** | `_CRS` says `PolarityLow` and Intel's Windows driver logs `CsPolarity:Low`. The `SPI_CS_HIGH` rows of the old brute-force sweep only ever ran with pin 189 HIGH. |

## How pin 189 was found

The pad registers were read **while Windows was running and the fingerprint had
just unlocked the machine** (RWEverything, physical addresses below). The SPI
pads matched Linux on every bit. Two live levels did not:

| Pad | Address | Windows, sensor working | Linux, during old tests |
|---|---|---|---|
| pin 189 (reset) | `0xFD6A0680` | `0x44000200` — driven **LOW** | `0x44000201` — HIGH |
| pin 41 (interrupt) | `0xFD6E0890` | `0x40100100` — reads **LOW** | `0x40100102` — HIGH |

The `SPI_CS_HIGH` condition then turned up when every older sweep was re-run with
pin 189 LOW (`tools/lowstate.py` inverts the pin for any old script).

## What came back

Case A — 1 MHz, mode 0 | `SPI_CS_HIGH`, pin 189 LOW, commands NOP → `0xa8`
(firmware version) → `0x82` (chip ID), each as header + body, then one 256-byte
read:

```
0000  a0 1a 00 ba a8 17 00 47 46 5f 53 54 34 31 31 53  .......GF_ST411S
0010  45 43 5f 41 50 50 5f 31 34 31 31 35 00 4c 4c 4c  EC_APP_14115.LLL
0020  4c 4c 4c 4c ...
```

- outer header `a0 1a 00 ba`: checksum `a0+1a+00 = ba` ✔, length 26 ✔
- body `a8 17 00 "GF_ST411SEC_APP_14115\0" 4c`: inner length 23 ✔,
  checksum `(0xAA − sum) & 0xFF = 0x4c` ✔
- the trailing `4c 4c …` is the STM32 slave repeating the last byte it shifted
- the string is exactly what the Windows driver logs for this part

Case E — `0xa8` alone:

```
a0 06 00 a6  b0 03 00 a8 03 4c
```

An ACK for `0xa8` with status `0x03`, checksum ✔. Status bit `0x02` means
"commands 0–5 are gated until TLS is up", as documented by Sigfrodr for GXFP5187.

## Controls

| Case | Chip select | Pin 189 | Commands | Result |
|---|---|---|---|---|
| A | `SPI_CS_HIGH` | LOW | NOP, 0xa8, 0x82 | **firmware-version reply** |
| B | normal | LOW | NOP, 0xa8, 0x82 | `00`/`ff` only |
| C | `SPI_CS_HIGH` | HIGH | NOP, 0xa8, 0x82 | `00`/`ff` only |
| D | `SPI_CS_HIGH` | LOW | none | shifting `00`/`ff` pattern, no frame |
| E | `SPI_CS_HIGH` | LOW | 0xa8 | **ACK frame** |
| A′ | repeat of A | | | byte-identical |

Each case started from its own reset (pin 189 HIGH 300 ms, then LOW or HIGH,
600 ms settle). Full output: [data/cshigh-repro-run.txt](../data/cshigh-repro-run.txt).
Script: [tools/cshigh_repro.py](../tools/cshigh_repro.py).

## What this corrects

- **The "88.8 ms rail decay"** ([HARDWARE.md](HARDWARE.md)) is the MCU's boot
  time after reset is released: its firmware drives the interrupt line LOW
  about 89 ms after pin 189 goes LOW.
- **"`SPI_CS_HIGH` never reaches the hardware"**, published and retracted on
  2026-09-11 in [OPEN-QUESTION.md](OPEN-QUESTION.md), was itself wrong. It
  clearly changes what the sensor sees; sampling only the two states of the
  chip-select register missed which one is held during a transfer.
- **"Interrupt HIGH proves the sensor is powered"**, used as a control in several
  tests, was inverted. HIGH meant held in reset — or, with the MCU running, a
  reply waiting to be read.

## Still open

- Why Linux needs `SPI_CS_HIGH` when ACPI and Windows both say active-low.
- Config upload, TLS, image capture and matching on this part.

## If you have a GXFP51A0 that reads `0xff`

```sh
sudo modprobe spidev
echo spidev | sudo tee /sys/bus/spi/devices/spi-GXFP51A0:00/driver_override
echo spi-GXFP51A0:00 | sudo tee /sys/bus/spi/drivers/spidev/bind
sudo python3 tools/cshigh_repro.py
```

Your reset line number may differ from 264 — check `_CRS` or
`/sys/kernel/debug/pinctrl/*/pins`. Please report the result either way.
