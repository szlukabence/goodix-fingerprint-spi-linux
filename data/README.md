# Data

## `WBDI-transcript.txt`

A cleaned transcript of a **working** Windows session — the Goodix driver
initialising the sensor successfully, from `DriverEntry` through to sleep. Taken
with the logging recipe in [../docs/WINDOWS-LOGGING.md](../docs/WINDOWS-LOGGING.md).

This is the ground truth the protocol was verified against. It contains the
firmware version string, the chip ID, the sensor type and geometry, every
command with its `cmd0-cmd1-Len-ackt` line, and the interrupt handler's decode
of each reply header including its checksum.

**Redactions.** Five occurrences of the sensor's factory OTP were removed — the
full 64-byte block twice, and its three CRC-checked sub-blocks — because it is
per-device and begins with the sensor's serial number in ASCII. They are marked
`<REDACTED: ...>` in place, so the surrounding log structure is intact. Nothing
else was altered.

What was deliberately **kept**, and why it is safe:

- The TLS handshake records. These are ephemeral session data from a session
  long ended, and they are useful evidence: they show the TLS version in use and
  that the record layer really is wrapped in the `0xB` transport type. Note the
  driver logs `init to const pmk` — the pre-shared key is a constant held inside
  the SGX enclave, not derived from this session, so the handshake reveals
  nothing about it.
- The FDT baseline readings. These are capacitance baselines for finger
  detection, not image data. There is no biometric content anywhere in this file
  — the session captured initialisation, not a fingerprint scan.

## `sensor-config.bin`

The 256-byte generic sensor tuning block the driver downloads at init
(`cmd0=9, cmd1=0`). Device-independent, and included because interoperability is
impossible without it.

One caveat: the driver patches a handful of DAC register values into the config
from the per-device OTP before sending it
(`from otp dac 0xb98, dac1 0xbc, dac2 0xb9, dac3 0xb9`). It is not established
whether this capture is the pre- or post-patch version. Four calibration
integers at most — worth knowing if your sensor images come out oddly biased.

## `refseq-run.txt`

Output of `tools/refseq.py` on the author's machine: the bring-up sequence under
five reset shapes × two bus speeds, with pad registers dumped at every step.

All ten attempts returned `ff ff ff ff`. It is included as the record of what a
**negative** result looks like, and because the pad register dumps in it are the
evidence behind [../docs/HARDWARE.md](../docs/HARDWARE.md) — including the
enable/interrupt correlation that proves the enable line physically reaches the
sensor.
