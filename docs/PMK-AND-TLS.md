# The PSK, the encrypted channel, and where imaging stands

**2026-09-16 — the crypto wall is down.** The sensor's TLS-PSK key was extracted
from its own flash, validated offline against a captured handshake, and then used
to complete a **live TLS 1.2 handshake on the hardware**. Everything the sensor
gates behind the encrypted channel is now reachable.

This documents the *method*. No key material or per-device secret is published
here; each owner extracts their own key from their own sensor.

## 1. Two `0xF2` memory-read artifacts

The arbitrary-memory-read command (`0xF2`, format per Sigfrodr) has **two** quirks
that corrupt naïve dumps — miss either and a correct key still "decrypts" to
garbage:

1. **An 8-byte request echo.** The reply body is `F2 <len16> <addr32><len32>
   <memory…> <ck>` — it prepends the requested `addr||len` before the data. A
   reader that treats those 8 bytes as data is shifted and drops the last 8 real
   bytes of every chunk.
2. **A corrupted first data byte.** The first memory byte of each read comes back
   with an address-dependent value. It's harmless for most dumps but fatal for the
   key blob, where byte 0 feeds both the cipher key and IV.

## 2. The flash-blob PSK decrypt

The 48-byte PMK is decrypted at boot from a flash blob (three redundant copies).
Reverse-engineered by instrumenting the firmware's own decrypt in emulation
(Unicorn), the scheme is:

```
salt = blob_body[0:16]           # byte 0 is the read-corrupted byte above
key  = SHA256( salt || 48 zero bytes || goodix_fallback_seed )[:16]   # AES-128
iv   = salt
plaintext = AES-128-CBC(key, iv, blob_body[0x10:])
          = [u16 marker 0x000d][u32 length BE = 0x30][48-byte PMK]
```

`goodix_fallback_seed` is the family constant from @lexakimov's white-box work.
Recover the real byte 0 by brute-forcing 0..255 against the `0x000d` marker (one
value hits, identical across all three copies). **The full 48-byte PMK is the
PSK** — not the 32-byte head — with classic (non-EMS) key derivation.

## 3. The live channel

The sensor is the TLS **client**; the host is the **server**; suite
`PSK-AES128-GCM-SHA256`, extended-master-secret **off**. Feeding the extracted
48-byte PSK, the handshake completes on hardware:

```
*** LIVE TLS HANDSHAKE COMPLETE ***
    cipher: ('PSK-AES128-GCM-SHA256', 'TLSv1.2', 128)
```

With the gate open, plaintext finger-detection (FDT, `0x36`) commands return data,
and finger presses are detected by the drop in the FDT readings.

## 4. Provisioning is not available on this build

Writing a host-chosen PSK (`preset_psk_write`, `0xe0`, the `0xbb010003` path used
on the sibling GDIX51C0) is a **no-op** on `GF_ST411SEC_APP_14115`: the command
dispatcher's `0xe` handler implements only the read sub-ops; the write slot falls
through. Confirmed statically on two machines. So extraction (above) is the path,
not provisioning — and, reassuringly, no normal command can write the sensor's
flash (only the `0xF0`/UPFW firmware-update path can, which is never used).

## 5. Where imaging stands (open)

Getting the fingerprint **raster** is the remaining step. The reference driver for
the sibling GXFP5187 receives the image as one large TLS record (~22 KB, 132×112,
12-bit packed) after the capture sequence. On this build that large record does
not yet arrive: the `0x20` get-image command only ACKs, and the large frame we do
get (from `0x50`) is **navigation** data, not a spatial image (verified by
rendering — no ridges). Open leads: arming FDT-down (`0x32`) before capture,
waiting for a late/large record, and matching the reference's exact image read.

## Credit

Sigfrodr (protocol, marker/staging map, the oversized-record decrypt recipe),
lexakimov (the sibling white-box KDF), GodsQuantum (independent confirmations and
the ChicagoHS capture backend), and xamelllion (second-machine first contact).
