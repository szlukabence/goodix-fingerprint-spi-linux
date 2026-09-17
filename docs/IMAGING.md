# Getting the fingerprint image off a GXFP51A0

**2026-09-17 — the sensor produces real fingerprint images on Linux.** Capture,
transport, decrypt, decode and enhancement all work end to end, with no vendor
software. This is the method; no key material or biometric data is published.

Prerequisite: the encrypted channel, see [PMK-AND-TLS.md](PMK-AND-TLS.md).

## 1. The capture sequence

Replay the sibling reference (`goodix-fp-dump`, `driver_51x7.run_driver()`) faithfully.
Post-TLS, in order:

```
nop ; query_mcu_state(0x55)
fdt_mode(0d01 b2b2 c2c2 a7a7 b6b6 a6a6 b6b6)
nav
fdt_mode(0d01 80b0 80c0 80a4 80b4 80a3 80b4)
read_reg(0x0082) ; write_reg 0x0220/0x0236/0x0238/0x023a
get_image                               <- background frame (no finger)
fdt_mode(0d01 80b1 ...) ; fdt_down(0c01 80b1 ...)
nop ; query_mcu_state(0x55)
fdt_down(0c01 80b2 80c2 80a7 80b6 80a6 80b6)
get_image                               <- the real frame, finger down
```

Two things cost us a lot of time:

* **FDT payloads are 14 bytes**: a header (`0d 01` mode / `0c 01` down / `0e 01` up)
  followed by **six 16-bit values**. We had been sending a 34-byte `0d01 a7a7…` blob,
  which the sensor accepted (ACK) but which never armed a real scan.
* **`get_image` is `0x20` with payload `01 00`.** Sending it is not enough on its own —
  `fdt_down` must be (re)armed **before every frame**. Arming once per burst yields one
  usable frame and then stale/empty ones.

## 2. The image arrives as one oversized TLS record

The frame comes back as a **single ~10602-byte type-`0xB` record** — a TLS 1.2
application-data record (`17 03 03 29 65 …`). A reader that caps transport reads at
4096 bytes silently drops it, which looks exactly like "the sensor sent nothing."
Read the 4-byte transport header, then the body in ≤4096-byte chunks.

Decrypt with the **client_write** key (the sensor is the TLS client). The record
sequence number climbs for the whole session — a long multi-capture session reaches the
hundreds, so don't assume a small search window.

Plaintext is 10573 bytes: a goodix header (`20 4a 29 00 …`) then the pixels.

## 3. Decode

Per `goodix-fp-dump`'s `tool.decode_image`, strip `plaintext[8:-5]` (8-byte header +
5-byte trailer) → 10560 bytes → **6 bytes = 4 pixels**, 12-bit, de-interleaved:

```
px0 = ((b0 & 0x0f) << 8) | b1
px1 = (b3 << 4) | (b0 >> 4)
px2 = ((b5 & 0x0f) << 8) | b2
px3 = (b4 << 4) | (b5 >> 4)
```

→ 7040 px. **Geometry: the frame is 88 wide × 80 tall, but only columns 0–63 are image**
— columns 64–87 are zero padding. The active image is **64 × 80 = 5120 px**, which
matches the Windows engine log (`col 80, row 64`). Crop to the non-zero columns before
processing; the raw pixel stream autocorrelates at period 88, confirming the stride.

(The firmware's 0x572c = 22316-byte frame slot is sized for the *larger* sibling
132×112 part, not for this one. It is not spare resolution.)

## 4. Turning the frame into a usable print

Capture a **background frame with no finger**, then subtract. Two corrections mattered
more than anything else, and both were found by measuring how many minutiae `mindtct`
actually returns:

* **The DC level drifts between captures.** The reference's `clip(1000 + img - bg)`
  assumes the finger signal sits near zero; here `img - bg` carries a large *drifting*
  negative DC (≈ −1000…−1220), so the constant offset lands below zero and 54–88 % of
  the image clips to 0. Measured: 54 % clipped → 66 minutiae, 88 % clipped → **0**.
  The clipping itself is *needed* (it binarises the ridges — smooth grayscale variants
  scored zero), so set the clip level **adaptively** from the data:
  `sub = clip(d - percentile(d, ~54), 0, None)`, then scale.
* **Don't average frames.** Averaging smears ridges whenever the finger drifts during
  the burst. Measured on one placement: 6-frame average → **0** minutiae, single frame
  → **36**. Build from the best few frames *individually* and keep whichever scores best.

Then the reference preprocessing (histogram equalisation, 3×3 mean filter, unsharp)
gives clean ridges.

## 5. Sensor scale, measured

From the ridge frequency of real prints (~11 px per ridge, human ridge pitch
0.40–0.50 mm):

> **≈ 560–700 DPI over a sensing area of roughly 2.5 × 3.2 mm**

The resolution is good; the **area is tiny** — only ~9 ridges are visible at once. The
power-button housing is fingertip-sized, but the active die is a few millimetres.

## 6. Status: imaging solved, matching is not

Imaging, decoding and enrollment are solid and reproducible. **Verification is not.**
Feeding these images to NBIS (`mindtct` + `bozorth3`) gives a working demonstrator —
genuine prints score above impostors — but the margin is small and a different finger
still false-accepts on live captures. That is unsurprising: NBIS is built for full
500-DPI prints with dozens of minutiae, not ~3 mm partial patches.

**Do not use this as authentication.** Small-area sensors need partial-print matching
(ridge flow / texture / learned features) and template mosaicing across many
enrollments — which is what the vendor stack does, inside an SGX enclave we cannot read.

## Credit

Sigfrodr (protocol and the oversized-record decrypt recipe), the
[goodix-fp-dump](https://github.com/goodix-fp-linux-dev/goodix-fp-dump) project (the
`driver_51x7` capture sequence and `tool.decode_image`), lexakimov (sibling white-box
KDF), GodsQuantum (independent confirmations, ChicagoHS backend), xamelllion
(second-machine first contact).
