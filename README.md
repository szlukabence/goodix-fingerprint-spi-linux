# Goodix GXFP51A0 "Milan-SPI" — protocol specification, tooling and driver

Reverse engineering of the fingerprint sensor in the **Huawei MateBook 13 (2020)**
(WRTB-WXX9 / M1260), and of the Goodix Milan-SPI family generally.

**Status: the protocol is solved and independently confirmed. The driver is
written. One hardware question remains open, and it needs a logic analyser — see
[docs/OPEN-QUESTION.md](docs/OPEN-QUESTION.md).** That document is a complete,
specific work order; someone with this laptop and a €30 logic clip should be
able to close it in an evening.

Everything here was derived from a machine the author owns, from a driver the
author is licensed to run, for the purpose of interoperability.

---

## Please tell me what happens

I can only test one laptop. If you have one of these sensors, **thirty seconds
of your time is worth more to this project than anything else you could
contribute** — including a negative result.

| What happened | Report it |
|---|---|
| 🎉 Your sensor **replied** on Linux | [open an issue](../../issues/new?template=01-sensor-replied.yml) — this is the one I most want to see |
| 🔬 You **captured the bus** with a logic analyser | [open an issue](../../issues/new?template=02-logic-capture.yml) — this closes the open question |
| 🔇 Your sensor is **silent too** | [open an issue](../../issues/new?template=03-my-sensor-is-silent.yml) — tells us if it's one machine or the whole family |
| 💬 Anything else | [Discussions](../../discussions) |

A single "mine replied on a MateBook X Pro" would tell us more than another
month of work on this end.

---

## Where GXFP51A0 sits in its family

This matters more than anything else here.

| Sensor | Linux status | Project |
|---|---|---|
| **GXFP5187** (MateBook X Pro) | ✅ **fully working** — enrol + verify via fprintd, own matcher | [Sigfrodr/libfprint-goodixtls](https://github.com/Sigfrodr/libfprint-goodixtls) |
| **GDIX51C0** (MateBook 16s) | ✅ **working** libfprint driver — images, enrol | [berkekbgz/libfprint-goodix-spi](https://github.com/berkekbgz/libfprint-goodix-spi) |
| **GDIX51C0** | ✅ PoC — scan, enrol, delete | [lexakimov/goodix51c0_spi-reversing](https://github.com/lexakimov/goodix51c0_spi-reversing) |
| **GXFP51A0** | ❌ **no one, on any machine** | this repo, and the three below |

Four independent GXFP51A0 machines, four independent investigators, all reading
`0xff`:

- this MateBook 13 2020
- [GodsQuantum](https://github.com/GodsQuantum/huawei-matebook-fingerprint-linux), MateBook 13 2021 — libfprint integration, deep controller-side analysis
- [PopulusYang](https://github.com/PopulusYang/GXFP51A0-driver-failed), MateBook 13 WRTB — repo named "driver-failed"
- a further report on [goodix-fp-dump](https://github.com/goodix-fp-linux-dev/goodix-fp-dump/issues)

Different boards, different methods, identical dead end — while two other parts
in the same family are finished and in daily use.

### The sensor is not the problem — it is the same chip that already works

`GDIX51C0` is **the same Goodix die as `GXFP51A0`**. berkekbgz's parity record
names its reference as the Windows stack *"selected by chip `0x2504`, sensor
type 12 (`ChicagoHS`)"* with *"80x64 geometry, 64-byte OTP"* — identifier for
identifier, that is this sensor. Their wire test even carries the chip-ID reply
`82 05 00 a2 04 25 00 58`; our Windows transcript's reply begins
`82 05 00 a2 04`. Byte identical.

The ACPI ID differs because it is assigned per board integration, not per
silicon.

So the accurate statement is **not** "GXFP51A0 is a difficult chip". It is:

> The same silicon is driven successfully on Linux today under the `GDIX51C0`
> integration, and fails under the `GXFP51A0` integration on four machines out
> of four.

That points at the **board** — wiring, power, or a component in the signal path
— and away from the chip, the protocol and the software, all three of which are
demonstrably fine because someone else's code drives this exact die with the
exact bytes in `docs/PROTOCOL.md`.

### Everything after the first reply is already solved

The TLS/PSK layer was long assumed to be the wall for this family, gated behind
SGX. It isn't. Sigfrodr's working 5187 driver reads the PSK **out of the
sensor's own RAM** with the `0xF2` memory command — 48 bytes, no Intel ME, no
SGX, no IAP.

So config upload, TLS, image capture, decoding, enrolment, matching and fprintd
integration all exist in working code on sibling silicon. **The entire remaining
problem for GXFP51A0 is getting one byte back.**

---

## What was previously believed, and what is actually true

The public state of the art for this sensor family got two important things
wrong. Both are corrected here, with evidence.

| Claim in the wild | Reality |
|---|---|
| "`0xF0` is the chip-ID command" (OpenGoodixSPI) | `0xF0` is **firmware upgrade**. Chip ID is `cmd0=0x8`, `0x82`. Sending `0xF0` to probe a sensor is asking it to start a flash operation. |
| "The MCU firmware is missing / must be sourced" | Both MCU images ship **inside `gfspi.dll`**, and on this machine the sensor already runs the right one — Windows logs *"the same version, no need to update firmware"*. **No upload is needed at all.** [tools/extract_firmware.py](tools/extract_firmware.py) pulls them out. |

## What is established here

- **The complete wire protocol** — framing, both checksum rules, the command
  table, the reply format, the ACK format. See [docs/PROTOCOL.md](docs/PROTOCOL.md).
- **Confirmed three independent ways**: static RE of the Windows driver; the
  sensor's own firmware; and a captured transcript of a *working* Windows
  session whose checksums match the derived formula in six distinct cases,
  including one where the length field wraps.
- **Independently corroborated** by [lexakimov/goodix51c0_spi-reversing](https://github.com/lexakimov/goodix51c0_spi-reversing),
  an unrelated project on a sibling part (GDIX51C0) that arrived at
  byte-identical framing and checksum maths.
- **The sensor identified exactly**: chip ID `0x2504`, sensor type 12
  ("ChicagoHS"), **80 × 64** pixel array, running `GF_ST411SEC_APP_14115` on an
  STM32-class MCU. None of this was publicly known.
- **How to make the Windows driver log its own traffic** — the single most
  useful reproducibility tool here, because it turns a closed driver into a
  labelled transcript of the wire. See [docs/WINDOWS-LOGGING.md](docs/WINDOWS-LOGGING.md).
- **A Linux kernel driver** with a debugfs harness ([kernel/](kernel/)) and a
  dependency-free userspace toolkit ([tools/](tools/)).

## What is NOT established

The sensor has never replied to Linux on this machine. Under Windows it answers
its first command in every condition we can create — cold boot, warm reboot,
even after Linux switched it off. Under Linux the data-in line sits high and has
never carried a single byte.

Everything on the host side has been verified correct at register level, and the
enable line has been proven to physically reach and power the sensor module. The
residual unknown is confined to one question that software cannot answer.
[docs/OPEN-QUESTION.md](docs/OPEN-QUESTION.md) states it precisely, lists what
has already been ruled out (so nobody repeats it), and gives the procedure.

## Layout

```
docs/PROTOCOL.md         the wire protocol, with a source citation per claim
docs/HARDWARE.md         sensor identity, ACPI, pin map, pad register decode
docs/WINDOWS-LOGGING.md  how to make Goodix's driver log its own SPI traffic
docs/OPEN-QUESTION.md    the one unsolved thing, and how to measure it
docs/FIRMWARE.md         where the MCU images live and how to get them
tools/                   userspace toolkit + RE tooling (no dependencies)
kernel/                  out-of-tree Linux driver, GPL-2.0
data/                    a full annotated transcript of a working Windows session
```

## Quick start

```sh
# 1. bind the sensor to spidev
sudo modprobe spidev
echo spidev | sudo tee /sys/bus/spi/devices/spi-GXFP51A0:00/driver_override
echo spi-GXFP51A0:00 | sudo tee /sys/bus/spi/drivers/spidev/bind

# 2. try the bring-up sequence (5 reset variants x 2 speeds)
sudo python3 tools/refseq.py

# 3. pull the MCU firmware out of the Windows driver
python3 tools/extract_firmware.py /path/to/gfspi.dll -o firmware/
```

## On redistribution

Two things are deliberately **not** included:

- **The MCU firmware images.** They are Goodix's copyrighted code. This project
  ships an extractor and the SHA-256 of each image so you can verify what you
  got, but you extract them yourself from a driver you already have.
- **Per-device secrets.** The sensor's factory OTP begins with the device serial,
  and the BIOS hands the OS a 2 KB window containing what appears to be
  per-device key material. Their **formats** are documented; the **bytes** from
  the author's machine are not published, and the OTP is redacted in place
  wherever it appeared in the captured transcript. If you dump your own, treat
  them as secrets.

The captured Windows session contains no biometric data — it recorded
initialisation, not a scan. See [data/README.md](data/README.md) for exactly
what was redacted and what was deliberately kept.

The included `data/sensor-config.bin` is the 256-byte generic sensor tuning
block the driver sends at init. It is device-independent and is included because
interoperability is impossible without it.

## Licence

Kernel driver: GPL-2.0. Tools and documentation: same, so the whole thing can be
upstreamed as one piece if it ever gets that far.

## Related work

- [lexakimov/goodix51c0_spi-reversing](https://github.com/lexakimov/goodix51c0_spi-reversing) — a **working** spidev PoC on the sibling GDIX51C0. Best reference for what comes after the sensor answers: TLS/PSK, enrolment, image decode.
- [PeshalaDilshan/OpenGoodixSPI](https://github.com/PeshalaDilshan/OpenGoodixSPI) — kernel driver skeleton for this family (command table predates this work; see the correction above).
- [goodix-fp-linux-dev](https://github.com/goodix-fp-linux-dev) — the USB Goodix parts. The inner message layer is the same protocol; only the transport differs.
- [GXFP5130 kernel series](https://lwn.net/Articles/1083519/) — a Goodix laptop fingerprint driver submitted upstream (different transport: EC mailbox, not SPI). Useful as the shape of something mergeable.
