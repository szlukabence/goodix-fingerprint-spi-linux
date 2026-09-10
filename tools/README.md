# Tools

Everything here is **dependency-free** — plain Python 3, raw `ioctl`s, no
`spidev`/`periphery`/`pyusb` packages to install. Copy the directory onto the
target and run it.

## Talking to the sensor

| Tool | What it does |
|---|---|
| `bind-spidev.sh` | Binds the ACPI sensor to `spidev` and keeps the controller awake. Run first. |
| `refseq.py` | The bring-up sequence, tried under 5 reset shapes × 2 bus speeds, with pad registers dumped at every step. **Start here.** |
| `gxfp.py` | The protocol as a library: framing, checksums, a `Sensor` class. Import this to write your own experiments. |
| `spidev_raw.py` | Minimal `SPI_IOC_MESSAGE` wrapper. |
| `gpio_raw.py` | Minimal GPIO character-device (uAPI v2) wrapper. |

```sh
sudo ./bind-spidev.sh
sudo python3 refseq.py
```

`refseq.py` prints, for every attempt, the exact bytes sent, the bytes read
back, the interrupt level, and the pad registers before and after. A reply is
flagged `<<< VALID HEADER`. `ff ff ff ff` means an idle wire, not a reply.

## Reverse-engineering the Windows driver

| Tool | What it does |
|---|---|
| `extract_firmware.py` | Pulls both MCU firmware images out of `gfspi.dll` and verifies them by hash. See [../docs/FIRMWARE.md](../docs/FIRMWARE.md). |
| `pdata.py` | Parses a PE `.pdata` exception directory into a function map — gives you every function boundary in a stripped binary. |
| `pexref.py` | Scans for RIP-relative `LEA` cross-references, so you can find which function uses a given string or table. |
| `thumbcalls.py` | Decodes Thumb-2 `BL` instructions into a call graph — for the sensor's own ARM firmware, not the Windows driver. |

The workflow that recovered the protocol: `pdata.py` for function boundaries,
`pexref.py` to find which function references a given log string, then read that
function. The driver's log format strings are unusually descriptive and name the
fields of the packets they describe.

## Note on the interrupt line

The interrupt is **level**-triggered, active high, so polling its level works
fine and `refseq.py` does exactly that. If you switch to edge events, remember
the level may already be high when you start waiting.
