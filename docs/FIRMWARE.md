# The MCU firmware — where it lives, and why you don't need to upload it

Two beliefs circulate about this sensor family. Both are wrong.

> "The sensor sits in a bootloader waiting for a firmware blob, and the blob
> isn't publicly available."

**The sensor already runs its firmware.** Windows probes the version, compares,
and logs verbatim:

```
the same version, no need to update firmware
```

No upload happens during a normal successful initialisation. If your sensor is
not answering, a missing firmware upload is not the reason.

> "Nobody has the images."

**Both images ship inside `gfspi.dll`**, in the `.rdata` section, as records of
the form `[u8 name_len][name ASCII][raw image]`. They have been sitting in the
driver package all along.

## Extracting them

```sh
python3 tools/extract_firmware.py /path/to/gfspi.dll -o firmware/
```

`gfspi.dll` is in the Goodix driver package, or on a Windows install under
`C:\Windows\System32\DriverStore\FileRepository\gfspi.inf_amd64_*\`.

## What you get

From `gfspi.dll` v1.1.141.40
(`sha256 36033fbf507620776d9fb686ecfe7847ff41fcbdee6e2afad119e28c6f81ca04`):

| Name | Size | Copies | Initial SP | Reset vector | MCU |
|---|---|---|---|---|---|
| `GF_ST411SEC_APP_14115` | 85994 | 17 | `0x20020000` | `0x08033199` | STM32 (flash `0x08000000`, image base `0x08020000`) |
| `GF_HC460SEC_APP_14104` | 84150 | 17 | `0x20015540` | `0x00033001` | HDSC HC32 (flash `0x00000000`) |

```
6e6cc79bedfbd51cf02f92a00cf3d42a0f1083c134e919b60b265f93f8f37aa4  GF_ST411SEC_APP_14115.bin
7c752777ea13741d8e7c8ceb45f814f485bbe8de5fc7134685ad2d9b2a650699  GF_HC460SEC_APP_14104.bin
```

The MateBook 13 (2020) runs the **ST411** image. The extractor verifies both
against these hashes.

The records carry no length field and the copies are not contiguous — they sit
inside a larger repeating structure — so the lengths above come from analysis
and are proven by the hashes, not derived from record spacing.

Two further names appear as plain strings with no embedded image:
`MILAN_HC460SEC_IAP_14102` and `GF_HC460SEC_APP_14102`.

## Why the images are not redistributed here

They are Goodix's copyrighted code. This project ships an extractor and the
hashes so you can obtain and verify them from a driver you already have a
licence to, on hardware you own.

## The update path, if you ever do need it

Relevant functions: `gfUpdatefirmware` @`0x18007a7f4`, `updatefirmware`
@`0x180081120`, `IsMCUIAP` @`0x1800711b4`, `device_action_erase_app`,
`ACT_ERASE_FIRMWARE`. The command is **cmd0 `0xF`** (`0xF0`, "UPFW").

> Note for anyone using OpenGoodixSPI's tables: it lists `0xF0` as "chip ID".
> It is not — it is firmware upgrade. Probing an unknown sensor with `0xF0` asks
> it to begin a flash operation. Chip ID is `cmd0=0x8` / `0x82`.

Two MCU vendors need different IAP command variants, selected into
`g_WhichIapUpFwCmd`:

- **HDSC** (HC32-class) — the driver distinguishes "Old IAP" / "New IAP" /
  "Old APP" / "New APP"
- **ST** (STM32) — "ST firmware, used old cmd"

Progress is reported as
`"Begin to update firmware ...slice length %d, %d framaes, cmd type used %d"`
(sic), and completion as
`"Check firmware success and Reset MCU to run to APP..."`.
