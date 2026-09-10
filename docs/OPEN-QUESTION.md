# The one open question

**Does the sensor ever drive the data-in (MISO) line, and is its output actually
connected to the pin the host reads?**

That is the whole of what is left. Everything else in this project is settled.
This document exists so that whoever picks it up does not spend a month
re-deriving what is already ruled out.

---

## The situation, stated plainly

Same silicon, same firmware, same boot path.

- **Under Windows**, the sensor answers its very first command, in every
  condition that can be created: cold boot, warm reboot, and even a boot in
  which Linux had previously switched the sensor off and left it off.
- **Under Linux**, on the same machine, it has never answered anything. The
  data-in line sits high. Reads return `ff ff ff ff` — an idle wire.

## What has been ruled out, with evidence

Do not re-test these.

| Ruled out | How |
|---|---|
| Wrong protocol / framing / checksums | Confirmed three ways, including against a working Windows transcript (6 cases, incl. a wrapping length) and an unrelated project on a sibling part. |
| Missing firmware upload | Windows logs *"the same version, no need to update firmware"*. The MCU already runs the right image. |
| A hidden fourth wire or extra resource | Windows' own resource list is `gpio-io 1, spi 1, interrupt 1` — identical to ACPI `_CRS`. |
| A hidden platform/ACPI/EC init step | Nothing anywhere in ~52,000 lines of ACPI touches these two pads except the sensor's own `_INI`/`_CRS`. No `_PS0`/`_PS3`/`_PR0`, no power resources. |
| The BIOS pre-boot handoff buffer being required | Windows **never reads it** — not once in a full successful session. |
| Huawei PC Manager / a userspace service | The sensor answers during driver start, long before any user-space process exists. |
| A power-button press being required | Linux switched the sensor off; a *restart* with no button press still gave Windows a live sensor on its first command. |
| Wrong SPI mode / speed / bit order / CS timing | All verified against `_CRS` and swept. |
| Host pads misconfigured | All six pads decoded from register state and confirmed correct — see [HARDWARE.md](HARDWARE.md). |
| The BIOS pad lock blocking our GPIO writes | The pad obeys writes exactly (`0x44000201` ↔ `0x44000200`) despite the `[LOCKED]` flag. The output buffer is enabled. |
| The enable line not reaching the sensor | **Disproven — it does.** See below. |
| Reset polarity / pulse shape / settle time | Ten combinations tested (5 reset shapes × 2 speeds), including the exact sequence from a working sibling implementation. All returned `ff ff ff ff`. |

## What is proven to work

Two of the three wires are known good:

- **Enable**: driving it changes the sensor module's power state. The interrupt
  line's level *follows* the enable line's level, every time, in both
  directions:

  ```
  enable driven LOW   ->  interrupt pad reads 0
  enable driven HIGH  ->  interrupt pad reads 1
  ```

  Something on the far end is really there and really responds. This is the only
  direct physical evidence in the whole project.

- **Interrupt**: responds as above, and is correctly configured as a
  level-triggered input.

**Data-in has never carried a single byte, under any condition.**

## The measurement

Five signals, ten seconds, one boot.

### What to probe

The fingerprint sensor **is the power button**. It sits on its own small board
connected to the mainboard by a fine-pitch flex cable. Probe at that connector,
or at test points near it.

| Signal | Why |
|---|---|
| Chip select | frames each transaction |
| Clock | lets the analyser decode |
| Data out (host → sensor) | confirms our bytes physically arrive |
| **Data in (sensor → host)** | **the actual question** |
| Enable / reset | shows what the platform does at power-on |

### Procedure

1. **Boot Windows and capture.** This is the important capture — it is the known-good case.
2. **Boot Linux, run `tools/refseq.py`, and capture the same pins.**
3. Compare.

### What each outcome means

| Observation | Conclusion |
|---|---|
| Data-in carries a reply under Windows but the host reads `0xff` | The line is fine; the host's receive path is broken. A pinmux/routing problem — solvable in software from there. |
| Data-in carries a reply under Windows and **nothing** under Linux, while data-out is identical in both | The sensor is deliberately refusing Linux. Look at what differs before the OS: the enable line's behaviour at power-on is then the prime suspect. |
| Data-in is silent under Windows too | The reply reaches the host some other way, and the entire model of this sensor is wrong. Unlikely, but it would explain everything. |
| Data-out shows nothing under Linux | The host is not actually transmitting despite the registers saying it is. |

### Cautions

- A €10 analyser samples too slowly to trust at 10 MHz. Get one that does
  **≥50 MS/s**; ~€30 buys that. Alternatively drop the bus to 1 MHz first —
  `refseq.py` already sweeps both speeds.
- That flex connector is fine-pitch and fragile. At least one person in the
  repair forums shorted theirs and killed the board. If this is your daily
  machine, weigh that.
- Probe with the machine's own ground reference, and do not back-power the
  sensor board from the analyser.

## If it turns out the sensor does answer

Then the remaining work is not protocol work — it is the security layer, and
there is a map for it:

- The sensor's reply path, ACK format and command table are in [PROTOCOL.md](PROTOCOL.md).
- The TLS/PSK layer, enrolment and image decoding are already solved on the
  sibling part by [lexakimov/goodix51c0_spi-reversing](https://github.com/lexakimov/goodix51c0_spi-reversing).
- `ProductionOperateKey` exposes **write PMK** and **reset PMK** over the wire —
  the same trust-on-first-use hook the USB Goodix drivers exploit. Whether this
  MCU still accepts it unauthenticated is untested. That is the next real
  question after the sensor talks.
