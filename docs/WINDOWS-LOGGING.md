# Making Goodix's own Windows driver log its SPI traffic

This is the most reusable thing in this project. It turns a closed, signed,
SGX-backed driver into a labelled transcript of everything it puts on the wire —
no logic analyser, no kernel debugger, no patched binaries.

It works for the whole Goodix fingerprint driver family, not just this part.

## The recipe

Create the key `HKLM\Software\Goodix\FP\LogOutput` and set:

| Value | Type | Set to | Meaning |
|---|---|---|---|
| `LogLevel` | DWORD | `9` | verbosity gate |
| `LogTarget` | DWORD | `31` | sink bitmask — all sinks on |
| `LogPath` | String | `C:\goodixlog` | optional; defaults to `C:\ProgramData\Goodix` |
| `LogFileLimit` | DWORD | `50` | max log size |

Then reboot, or disable and re-enable the fingerprint device in Device Manager.
The transcript appears at `<LogPath>\WBDI.log`.

## Both values are required

This is the part that costs people a day. Setting `LogTarget` alone produces
**absolutely nothing**, which reads exactly like the registry route not working
at all.

From the driver's own `Log()` function:

```c
if (ctx->[0x30c] == 0)          return;   // sink mask empty  -> LogTarget
if (param_level == 0)           return;
if (param_level > ctx->[0x100]) return;   // level gate       -> LogLevel
```

`LogLevel` **defaults to 0**, so every message is filtered out no matter what
the sinks are set to. Call sites pass levels 3 through 8, so it must be ≥ 8.

Sink bits are 1, 2 (file writer), 4 (`OutputDebugStringW`), 8 and 0x10; `31`
turns all of them on. Bit 4 means **DebugView also works** once `LogLevel` is
set, which is handy for watching live.

## Gotchas

- **`WBDI.log` appends across sessions.** It is cumulative. When analysing,
  find the *last* `DriverEntry` and read only from there, or you will happily
  draw conclusions from a boot three days ago. This nearly derailed the analysis
  here.
- `C:\fingerprintlog\WbioUXLog.log` is a **different** logger belonging to the
  Windows Biometric UX component. An empty folder there tells you nothing about
  whether this recipe worked.
- The driver also registers an ETW provider for the Windows event-log channel.
  That is a separate mechanism and not the one you want; the internal logger is
  purely registry-configured.

## What you get

Per-command lines in the form:

```
cmd0-cmd1-Len-ackt: 0x9-0-0x100-1000
```

`cmd0`/`cmd1` are the command nibbles, `Len` is the **payload** length, `ackt`
is the ACK timeout in ms (`0` = fire and forget). Plus, from the interrupt
handler:

```
spi pack :type: 10, len: 68, checksum: 228 and first 5 bytes: ...
```

which is the reply's 4-byte transport header decoded, including the checksum —
enough to verify a framing hypothesis without ever touching the hardware. That
is how the checksum rules in [PROTOCOL.md](PROTOCOL.md) were confirmed.

You also get, in plain text: the firmware version string, the chip ID, the
sensor type and array geometry, the factory OTP, the 256-byte sensor config, and
every state transition.

A full annotated example is in [../data/WBDI-transcript.txt](../data/WBDI-transcript.txt).

## An unexplored lead

The same region of the driver reads a second key:

```
HKLM\Software\Goodix\FP\DataDump\
    DumpSwitch  DWORD
    DumpPath    String
```

This is a raw-data dump feature. It was never exercised here. If it dumps sensor
frames or raw SPI buffers, it would be more valuable than the text log — worth
trying `DumpSwitch = 1` and seeing what lands.
