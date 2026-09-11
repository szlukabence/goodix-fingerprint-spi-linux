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
| The enable line doing nothing at all | It switches something: the interrupt pad's level follows it. But see the caveat below — this is weaker evidence than it first looks. |
| The sensor reacting to a finger unprompted | 30 s at 1.1 kHz with a finger repeatedly rested on the sensor: **zero** interrupt transitions, zero pad changes, every SPI read idle. Inconclusive though — finger-detect mode is host-configured in this family, so an unconfigured part may simply not look for fingers. |
| Reset polarity / pulse shape / settle time | Ten combinations tested (5 reset shapes × 2 speeds), including the exact sequence from a working sibling implementation. All returned `ff ff ff ff`. |
| The host controller's spidev state being wedged | Sigfrodr documents a wedge needing "a long reset AND the detach/reattach of the spidev kernel driver". Tested 4 recovery shapes × 2 speeds; spidev genuinely detached and reattached each time. All 8 returned `ff ff ff ff`. |
| A reset shape the working drivers use that we don't | Sigfrodr's **working** 5187 driver resets LOW 10 ms → HIGH → 120 ms settle — the same shape we already test. |
| Talking to the wrong device / bus / chip select | Traced end to end: `/dev/spidev1.0` (153:0) resolves through sysfs back to `spi-GXFP51A0:00` under `00:1e.3`. Only one GXFP ACPI device exists; `spi0` is the BIOS-flash controller, unrelated. |
| **Our own receive path being broken** | **Positive control**: spidev `SPI_LOOP` (SSCR1 LBM) loops TX→RX inside the SSP. A 10-byte pattern came back byte-perfect on the same open device with the same ioctls, while a normal transfer returns all-`ff`. The receive engine, RX FIFO, kernel driver read path and our ioctl usage are all proven good — and the clock genuinely runs. |
| Frame structure, command order, preamble, dummy read byte | 48 combinations tested, drawn from **both** working drivers: one chip-select per frame vs two, with/without the NOP (`GX_AMORCE`) preamble, the documented Windows opening order including `0x96` DriverState Install, clocking `0xFF` vs `0x00` during reads, at both speeds. Every one returned `ff ff ff ff`. Note berkekbgz uses two CS cycles and Sigfrodr uses one, and both work — framing is not decisive. |
| The controller being misconfigured | Live registers read from the LPSS BAR: mode 0, 8-bit, correct divider, loopback off, and `CS_CONTROL = 0xe003` — software mode, **CS deasserted**, CS0 selected. |
| Windows applying hidden settings Linux misses | `ialpss2_spi_cnl.inf` — the INF for our exact controller `PCI\VEN_8086&DEV_02AB` — contains no tuning at all: only a power-management flag, an event-log path and a logging GUID. |

## What the enable line actually tells us

Driving the enable line changes what the interrupt pad reads, deterministically,
in both directions:

```
enable driven LOW   ->  interrupt pad reads 0
enable driven HIGH  ->  interrupt pad reads 1
```

**Read this conservatively.** The most likely explanation is that the enable
line switches a power rail and the interrupt line carries a pull-up to that
rail — so the reading tracks the rail, not the silicon. A 30-second recording at
1.1 kHz with a finger repeatedly on the sensor produced **zero** movement on
that line: 100% high, not one transition in 33,110 samples. That is what a
passive pull-up looks like.

So: the enable line switches *something*. **We have not proven the sensor MCU
itself responds to anything.**

**Data-in has never carried a single byte, under any condition.**

## The host side is fully verified

Every layer between a `read()` and the pin has now been checked, and each one
is correct:

| Layer | Status |
|---|---|
| Device selection (right chip, right bus, right CS) | **proven** by sysfs trace |
| Controller configuration | **proven** by live register read |
| Receive machinery (engine, FIFO, driver, ioctls, clock) | **proven** by internal loopback |
| Pad *configuration* (all six pads) | **proven** by pinctrl register decode |
| Pad *electrical function*, pin multiplexer | **NOT verified** — see below |
| The wires between pad and sensor | **NOT verified** |

### Exactly where the verified/unverified boundary sits

`SPI_LOOP` sets `SSCR1` bit 2 (LBM), which ties the transmit shift register to
the receive shift register **inside the SSP block** — before the pin
multiplexer and before the pads. So the loopback proves everything from our
`read()` down to the shifter, and nothing beyond it:

```
code → ioctl → spidev → pxa2xx-spi → FIFO → TX shifter ─┐   PROVEN
  read ← FIFO ← RX shifter ←────────────────────────────┘

TX shifter → pin mux → PCH pad → package ball → PCB trace
   → connector → flex → sensor → and all the way back        NOT PROVEN
```

The pads and multiplexer are **inside** the unverified region. Their
configuration registers read correctly, but configuration is not function, and
their live state cannot be observed — see below.

### Why the pads cannot be used as a software logic probe

The BIOS locks them, and the lock bits explain every behaviour we see:

```
pin 41  IRQ    [LOCKED full]     PADCFGLOCK + PADCFGLOCKTX
pin 44  CS0B   [LOCKED full]
pin 45  CLK    [LOCKED full]
pin 46  MISO   [LOCKED full]
pin 47  MOSI   [LOCKED full]
pin 189 enable [LOCKED]          PADCFGLOCK only — TX state NOT locked
```

`[LOCKED]` locks the pad's configuration; `[LOCKED full]` locks the
configuration *and* the output state. That single difference is why the enable
pin obeys our writes while the four bus pads cannot be turned into probes:
reading their live level needs `GPIORXDIS` cleared, and that write is rejected
by hardware until the next power cycle.

Confirmed empirically: during a 4 KiB transfer, `MOSI` — a pin that is
definitely toggling — never changed its readback bit. Neither did any other.

There is nothing in software left between our `read()` and the pin, and no way
to see past it. That is why the remaining question is physical.

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

## Why this is worth someone's evening

**`GDIX51C0` is the same die as `GXFP51A0`** — chip `0x2504`, sensor type 12,
ChicagoHS, 80×64, 64-byte OTP, identical chip-ID reply bytes — and it is driven
successfully on Linux today. GXFP5187 works too. This exact silicon is not the
obstacle; the board it sits on is. Four machines, four investigators, never one
byte.

And everything downstream of that first byte is already written. The TLS/PSK
layer that was assumed to be the wall is not: Sigfrodr's working 5187 driver
reads the PSK **out of the sensor's RAM** via the `0xF2` memory command, 48
bytes, with no Intel ME, SGX or IAP involved.

So this is not a project needing months of protocol work. It needs **one
measurement**, after which there is a working reference implementation for
every remaining stage.

## If it turns out the sensor does answer

The path from there is mapped:

- The sensor's reply path, ACK format and command table are in [PROTOCOL.md](PROTOCOL.md).
- The TLS/PSK layer, enrolment and image decoding are already solved on the
  sibling part by [lexakimov/goodix51c0_spi-reversing](https://github.com/lexakimov/goodix51c0_spi-reversing).
- The PSK is readable from sensor RAM via `0xF2` on the sibling 5187 (48
  bytes). `ProductionOperateKey` in this driver also exposes **write PMK** and
  **reset PMK** over the wire. Our Windows log says `init to const pmk` — a
  *constant* key, not per-device.

## Related projects

- [Sigfrodr/libfprint-goodixtls](https://github.com/Sigfrodr/libfprint-goodixtls) — **working** GXFP5187 driver. Closest working relative; solved TLS via `0xF2`.
- [berkekbgz/libfprint-goodix-spi](https://github.com/berkekbgz/libfprint-goodix-spi) — **working** GDIX51C0 libfprint driver. Also ships `re/gfspi_trace.js`, a **Frida** script that instruments `gfspi.dll` at runtime — a software route to capturing real SPI buffers.
- [lexakimov/goodix51c0_spi-reversing](https://github.com/lexakimov/goodix51c0_spi-reversing) — GDIX51C0 PoC; independently confirms this repo's framing and checksums.
- [GodsQuantum/huawei-matebook-fingerprint-linux](https://github.com/GodsQuantum/huawei-matebook-fingerprint-linux) — GXFP51A0, same wall, libfprint integration and controller-side analysis.
- [PeshalaDilshan/OpenGoodixSPI](https://github.com/PeshalaDilshan/OpenGoodixSPI) — kernel driver skeleton for the family.
