# Goodix GXFP51A0 (Milan-SPI) wire protocol — recovered from gfspi.dll v1.1.141.40

Source of truth: static RE of `01-windows-driver/gfspi-driver-package/gfspi.dll`
(UMDF user-mode driver, x86-64, PDB path
`D:\Project\Huawei_Watt2\winfpcode\Milan_Watt\MilanSpi\x64\Release_GF3658\gfspi\gfspi.pdb`).
Every claim below cites the function it came from. VAs are with ImageBase 0x180000000.

The sensor speaks the **same "Milan" message protocol as the USB Goodix parts**
(the driver's HAL file is literally `milanspi/milanfusb/usbhal.c` and it logs
`USB_CMD0_*` symbol names) wrapped in an **extra 4-byte SPI transport header**.

--------------------------------------------------------------------------------
## 1. Bus parameters (from ACPI `_CRS` of \_SB.PCI0.SPI1.SPBA)
--------------------------------------------------------------------------------
    SPI mode 0 (CPOL=0 ClockPolarityLow, CPHA=0 ClockPhaseFirst)
    8 bits/word, MSB first, chip-select 0, CS active low
    10 MHz max (0x00989680)
    GpioInt : level, active high, chip-relative gpio 48  on gpiochip0 (INT34BB:00)
    GpioIo  : output,             chip-relative gpio 264 on gpiochip0  = ENABLE/RESET

--------------------------------------------------------------------------------
## 2. Transmit framing  (`PeripheralWriteWrapper` @0x180008130)
--------------------------------------------------------------------------------
```c
int PeripheralWriteWrapper(dev, uint8_t *buf, uint32_t len) {
    if (!buf || len <= 4)  return -1;              // "wrong parameters"
    if (g_hardwareID < 1 || g_hardwareID > 3) ...  // "!!!Unknown HardWareID"
    r = SpbPeripheralWrite(dev, buf,     4,       0);   // (1) 4-byte SPI header
    if (r < 0) return r;                                //     "...header failed"
    Sleep(2);                                           // (2) 2 ms gap  <-- REQUIRED
    r = SpbPeripheralWrite(dev, buf + 4, len - 4, 0);   // (3) rest of buffer
    if (r < 0) return r;                                //     "...payload failed"
    return r;
}
```
**Two separate SPI transactions (CS deasserted between them) with a 2 ms gap.**

--------------------------------------------------------------------------------
## 3. The 4-byte SPI transport header  (`MakeSpiHeader` @0x18000bfcc)
--------------------------------------------------------------------------------
```c
void MakeSpiHeader(uint8_t *h, uint16_t body_len) {
    h[0] = (h[0] & 0x0F) | 0xA0;        // packet type in the HIGH nibble
    h[1] = body_len        & 0xFF;      // LE16 length of what follows
    h[2] = (body_len >> 8) & 0xFF;
    h[3] = (uint8_t)(h[0] + h[2] + h[1]);   // plain 8-bit SUM of h[0..2]
}
```
Layout:  `[ type<<4 | flags ][ len LO ][ len HI ][ (h0+h1+h2) & 0xFF ]`

Packet types accepted in BOTH directions (`CheckPackage` @0x18000be7c):
    high nibble **0xA** = normal message
    high nibble **0xB** = TLS record
    anything else -> "!!!!unknown pack type"
NOTE: the header checksum here is a *plain additive sum*, NOT the 0xAA-complement
used by the inner message layer. Do not confuse the two.

--------------------------------------------------------------------------------
## 4. The inner Milan message  (`SpiSendDataToDevice` @0x180073108)
--------------------------------------------------------------------------------
```c
BOOL SpiSendDataToDevice(dev, uint8_t cmd0, uint8_t cmd1,
                         uint8_t *payload, uint16_t len,
                         uint8_t want_checksum, int ack_timeout_ms)
{
    uint8_t cmd = (cmd0 << 4) | (cmd1 << 1);          // the classic Goodix packing
    if (ack_timeout_ms > 0 && ack_timeout_ms < 1000) ack_timeout_ms = 1000;

    uint8_t seed = cmd + LO(len+1) + HI(len+1);
    uint8_t ck   = want_checksum ? GetCheckSum(seed, payload, len, 0xAA) : 0x88;

    uint32_t total = len + 8;
    uint8_t *buf = calloc(1, total);
    MakeSpiHeader(buf, len + 4);        // body length = len + 4
    uint8_t *b = buf + 4;
    b[0]       = cmd;
    b[1]       = (len + 1) & 0xFF;      // NB: the inner length field is len+1
    b[2]       = (len + 1) >> 8;
    memcpy(b + 3, payload, len);
    b[3 + len] = ck;
    PeripheralWriteWrapper(dev, buf, total);
    /* then wait for the IRQ/ack, retry once: "wait for ack timeout, try again",
       "!!!!ack timeout second time" */
}
```
`GetCheckSum(seed, buf, len, base=0xAA)` @0x180070238:
```c
uint8_t s = seed;  for (i=0;i<len;i++) s += buf[i];  return base - s;
```
so the message checksum is `0xAA - (cmd + LO(len+1) + HI(len+1) + Σpayload)`,
identical to the USB Goodix family. `0x88` is the "checksum omitted" marker.

### Full byte stream for one command
```
 SPI transaction #1 (4 bytes):
     A0  LO(len+4)  HI(len+4)  (0xA0+LO+HI)&0xFF
 ---- 2 ms ----
 SPI transaction #2 (len+4 bytes):
     cmd  LO(len+1)  HI(len+1)  payload[len]  checksum
```

--------------------------------------------------------------------------------
## 5. Receive path  (`MilanEvtInterruptIsr` @0x1800119a0)
--------------------------------------------------------------------------------
On a level-high IRQ on gpio 48:
1. `SpbPeripheralRead(dev, hdr, 4, 0)`   — read exactly 4 bytes.
2. `CheckPackage(hdr)` — validate type nibble (0xA/0xB) and the additive checksum.
   On failure it logs "Incorrect spi header" / "Incorrect spi header, should but
   not %d" and bails; every 0x32 (50) bad headers it re-logs.
3. Body length = `*(uint16_t*)(hdr+1)`; verify ring-buffer space
   ("!!!!ring buffer len left: %d < %d").
4. `SpbPeripheralRead(dev, body, body_len, 0)` into the ring buffer @0x180407c08.
5. `data_from_device` (@0x180074340) then parses the inner message:
   logs `cmd0-cmd1-cmd2-len-state`, checks "check sum error", and dispatches.

--------------------------------------------------------------------------------
## 6. cmd0 dispatch table  (verbatim from the driver's own log format string)
--------------------------------------------------------------------------------
```
cmd0-cmd1-Len-ackt:0x%x-%d-0x%x-%d(0x0NOP,0x2Ima,0x3FDT(dow/up/man),0x4FF,
0x5NAV,0x6Sle,0x7IDL,0x8REG,0x9CHIP,0xAOTHER,0xBMSG,0xCNOTI,0xDTLSCONN,
0xEPROD,0xFUPFW)
```
| cmd0 | meaning                        | cmd byte = cmd0<<4 | cmd1<<1 |
|------|--------------------------------|--------------------------------|
| 0x0  | NOP                            | 0x00                           |
| 0x2  | Image                          | 0x20                           |
| 0x3  | FDT (down / up / manual)       | 0x30 / 0x32 / 0x34 / 0x36      |
| 0x4  | FF  (finger flash)             | 0x40                           |
| 0x5  | NAV                            | 0x50                           |
| 0x6  | Sleep                          | 0x60                           |
| 0x7  | Idle                           | 0x70                           |
| 0x8  | REG (register read/write)      | 0x80 write / 0x82 read         |
| 0x9  | CHIP (chip cfg / chip id)      | 0x90                           |
| 0xA  | OTHER                          | 0xA0                           |
| 0xB  | MSG                            | 0xB0                           |
| 0xC  | NOTI (notice: ESD, wakeup)     | 0xC0                           |
| 0xD  | TLSCONN                        | 0xD0                           |
| 0xE  | PROD (production)              | 0xE0                           |
| 0xF  | **UPFW (firmware upgrade)**    | 0xF0                           |
This matches goodix-fp-dump's USB command table 1:1 (MCU_GET_IMAGE 0x20,
SWITCH_TO_FDT_* 0x32/0x34/0x36, NAV 0x50, SLEEP 0x60, IDLE 0x70,
WRITE/READ_SENSOR_REGISTER 0x80/0x82, UPLOAD_CONFIG_MCU 0x90).

NOTE: OpenGoodixSPI's guess that 0xF0 is "chip id" is **wrong** — 0xF0 is the
firmware-upgrade command. Chip ID lives under cmd0 0x9 (`--- MILAN_CHIPID`,
"Get Chip ID: 0x%x").

--------------------------------------------------------------------------------
## 7. Firmware update path
--------------------------------------------------------------------------------
Functions: `gfUpdatefirmware` @0x18007a7f4, `updatefirmware` @0x180081120,
`IsMCUIAP` @0x1800711b4, `device_action_erase_app`, `ACT_ERASE_FIRMWARE`.
Log lines that describe it:
  "Begin to update firmware ...slice length %d, %d framaes, cmd type used %d"
  "Unknow cmd for Update Firmwarwe, g_WhichIapUpFwCmd:%d"
  "Update Firmware Failed, frame number: %d"
  "Check firmware success and Reset MCU to run to APP..."
Two MCU vendors are supported and need *different* IAP command variants:
  * **HDSC** (Huada Semiconductor, HC32-class) — "!!!HDSC firmware, judge cmd to
    used", "!!!HDSC Old IAP" / "New IAP" / "Old APP" / "New APP"
  * **ST** (STM32) — "!!!ST firmware, used old cmd"
selected into `g_WhichIapUpFwCmd`.

### Firmware images embedded in gfspi.dll (see docs/FIRMWARE.md; extract with tools/extract_firmware.py)
Record format inside `.rdata`: `[u8 name_len][name ASCII][raw image]`
| name | size | copies | initial SP | reset vector | MCU |
|---|---|---|---|---|---|
| `GF_ST411SEC_APP_14115` | 85994 (0x14fea) | 17 identical | 0x20020000 | 0x08033199 | STM32 (flash @0x08000000, image base 0x08020000) |
| `GF_HC460SEC_APP_14104` | 84150 (0x148b6) | 1 | 0x20015540 | 0x00033001 | HDSC HC32 (flash @0x00000000) |
Also referenced as plain strings (not embedded images):
`MILAN_HC460SEC_IAP_14102`, `GF_HC460SEC_APP_14102`.

--------------------------------------------------------------------------------
## 8. Security layer — the open feasibility question
--------------------------------------------------------------------------------
* mbedTLS is statically linked into gfspi.dll; `mbedtls_ssl_conf_psk` is used.
  Packet type 0xB in the SPI header carries the TLS records
  (`SendTLSPackage` @0x1803721c8-ish, `RecvTLSPackage`).
* The TLS **server runs inside an SGX enclave** (`TLS_Server_Exit_ecall`,
  `Engine_Enclave_*.signed.dll`, `WBDI_Enclave.signed.dll`).
* `ProductionOperateKey` exposes operateType values:
      0 = Write PMK      1 = Reset PMK
      2 = Read MCU PMK HASH
      4 = Write PublicKey 5 = Reset PublicKey
      6 = Read MCU Pkey  7 = Read MCU state
  i.e. **the PMK (pairing master key) can be written/reset over the wire** — the
  same trust-on-first-use hook that goodix-fp-dump exploits on the USB parts.
  Whether this MCU still accepts it unauthenticated is UNVERIFIED.
* MCU state is reported as `version, isImageValid, isTlsConnected, isLocked`.
  "isLocked" is the thing to watch.

--------------------------------------------------------------------------------
## 9. Sensor identity
--------------------------------------------------------------------------------
Chip ID read at init ("Get Chip ID: 0x%x") selects a sensor type; the driver
supports MilanF, MilanFN, MilanEG, MilanL, MilanHU, ChicagoHS and logs
`sensor info ready, chipid:0x%x, sensorType:%d, col:%d, row:%d`.
Our part is the "GF3658 SPI" build ("!!!GF3658 SPI:Update firmware").

================================================================================
## 10. GROUND TRUTH FROM A WORKING WINDOWS SESSION (2026-09-10)
================================================================================
Captured by enabling the driver's own logger (see PROGRESS.md 12d) and reading
`C:\ProgramData\Goodix\WBDI.log`. Clean transcript: data/WBDI-transcript.txt

### 10.1 THE SENSOR, identified exactly
    evk/firmware version : GF_ST411SEC_APP_14115   (the ST411 image, already in flash)
    Chip ID              : **0x2504**
    sensorType           : 12  = "ChicagoHS"
    array geometry       : **col 80 x row 64**
    FDT                  : fdt_delta 33, tcode 224
    OTP (64 bytes)       : not published (per-device, see above)
      <not published - it is per-device and begins with the sensor's serial
      number in ASCII. Dump your own if you need it; treat it as a secret.>
      Structure: the driver CRC-checks three sub-blocks (cp / mt / ft), each
      with its own CRC8, and patches DAC registers from the values it finds.
    Sensor config (256 B): data/sensor-config.bin  (sent as cmd0=9,cmd1=0)
    Firmware update      : "the same version, no need to update firmware" -> the
                           MCU already runs the right APP; NO upload is required.

### 10.2 EVERY COMMAND CONFIRMED ON THE WIRE
The log prints `cmd0-cmd1-Len-ackt`. Len = PAYLOAD length. Observed opening set:
    0x0-0-0x4-0     NOP,   4-byte payload, **ack timeout 0 = "not to wait for ack"**
    0x9-3-0x2-1000  0x96   send_driver_install_to_MCU  ("DriverState:Install")
    0x0-0-0x4-0     NOP    (again, before the next group)
    0xa-4-0x2-1000  0xa8   GetEvkVersion
    0xa-1-0x2-1000  0xa2   gfresetMCUAndfingerprint (SOFT reset) -> CHIP_RESET::0x010008
    0x8-1-0x5-1000  0x82   ChipRegRead -> Chip ID 0x2504
    0xa-3-0x2-1000  0xa6   read OTP (68-byte reply)
    0x7-0-0x2-1000  0x70   IDLE
    0x8-0-0x5-1000  0x80   WriteSensorRegister (x4)
    0x9-0-0x100-1000 0x90  download_general_config (256-byte payload)
    0xd-0 / 0xd-2   0xd0/0xd4  TLS connect
    0x3-1/2/3-0xe   0x32/0x34/0x36  FDT down/up/manual (14-byte payload)
    0x2-0-0x2       0x20   image
    0x5-0-0x2       0x50   NAV
    0x6-0-0x2       0x60   sleep
**KEY: a NOP (cmd0=0,cmd1=0, 4-byte payload, no ack wait) is sent immediately
before each command group.**

### 10.3 THE REPLY FORMAT - OUR CHECKSUM FORMULA IS CONFIRMED EXACT
The ISR logs `spi pack :type: T, len: L, checksum: C and first 5 bytes: ...`
    type 10 (0xA) = message, type 11 (0xB) = TLS record   <- matches CheckPackage
    C is the 4-byte transport header's checksum byte, and in EVERY observed case
    C == (h0 + lo(L) + hi(L)) & 0xFF   with h0 = type<<4:
        type 10, len 6      -> 0xA0+0x06+0x00 = 0xA6 = 166  OK
        type 10, len 20     -> 0xA0+0x14+0x00 = 0xB4 = 180  OK
        type 10, len 68     -> 0xA0+0x44+0x00 = 0xE4 = 228  OK
        type 11, len 26     -> 0xB0+0x1A+0x00 = 0xCA = 202  OK
        type 11, len 10602  -> 0xB0+0x6A+0x29 = 0x143&0xFF = 0x43 = 67  OK
        type 10, len 3313   -> 0xA0+0xF1+0x0C = 0x19D&0xFF = 0x9D = 157 OK
Generic ACK body: `b0 03 00 <cmd_being_acked> 01`
    i.e. cmd 0xB0 (cmd0=0xB MSG), inner length 3, payload = [acked_cmd, 0x01].
Version reply body begins: `a8 17 00 47 46 ...`  = cmd 0xa8, inner len 0x17,
    payload "GF..." -> "GF_ST411SEC_APP_14115".
Chip-ID reply body begins: `82 05 00 a2 04 ...` = cmd 0x82, inner len 5.

### 10.4 WHAT WINDOWS DOES **NOT** DO
* It never toggles the enable GPIO during init. `MilanEvtDevicePrepareHardware`
  opens the GPIO target (resource-hub LowPart:5) and the SPI target (LowPart:3),
  and the resource list is exactly `gpio-io count: 1, spi count: 1, interrupt
  count: 1` - i.e. identical to our ACPI _CRS, no hidden resource.
* `HardResetMcu` is NOT called in a normal successful init. The reset that IS
  performed is a SOFT reset: command 0xa2, answered with CHIP_RESET::0x010008.
* No SPI-speed change (`ChangeSpiSpeedSwitch` stays 0).
=> There is no extra power or reset step for us to copy. The sensor simply
   answers immediately under Windows.

================================================================================
## 11. INDEPENDENT CONFIRMATION, AND A CONCRETE BRING-UP SEQUENCE
================================================================================
Everything above was derived from `gfspi.dll` plus a captured Windows session on
a MateBook 13.  It is independently corroborated by
**github.com/lexakimov/goodix51c0_spi-reversing**, an unrelated project that
reverse-engineered a *sibling* part (Goodix GDIX51C0) in a different Huawei
laptop and got it **working** from Linux over plain spidev.

That project arrived at byte-identical framing and checksum rules:

    transport header : [type][len LO][len HI][(sum of the three) & 0xFF]
    inner message    : [cmd][len+1 LO][len+1 HI][payload][0xAA - sum]

which is section 3 and section 4 of this document, reached by a different route
on different silicon.  Treat the framing as settled.

### 11.1 Three details that only the working implementation revealed
Static RE of the Windows driver does not give you these.

1. **"Unlock TLS" opener** — `d5 03 00 00 00 d3`, sent first, no ACK expected.
   (`0xD5` = cmd0 0xD TLSCONN, cmd1 0x2.)
2. **The MCU-config packet uses the "no checksum" marker**, not the formula:
       `01 05 00 00 00 00 00 88`
   The trailing `0x88` is the omitted-checksum sentinel from section 4, not an
   arithmetic result.  It is a prerequisite for both queries below.
3. **GetMcuState carries a live millisecond timestamp, and its checksum is the
   normal formula PLUS ONE**:
       `af 06 00 55 <ms LE16> 00 00 <(0xAA - sum) + 1>`
   Verified against their worked example `af 06 00 55 5c bf 00 00 86`:
   sum = 0x25, 0xAA - 0x25 = 0x85, +1 = **0x86**.  Reproduced exactly by
   `tools/refseq.py`.

### 11.2 The bring-up sequence, in order
As implemented in `tools/refseq.py`.  Each line is one `perform_write`: the
4-byte transport header in its own CS transaction, then the body in a second.

    d5 03 00 00 00 d3              unlock TLS          (no ack)
    01 05 00 00 00 00 00 88        upload MCU config   (no ack, magic checksum)
    a8 03 00 00 00 ff              GetEvkVersion       -> version string
    af 06 00 55 <ms> 00 00 <ck+1>  GetMcuState         -> MCU state block
    82 06 00 00 00 00 04 00 1e     MILAN_CHIPID        -> 0x2504 on this part

Windows additionally sends a bare **NOP** (`cmd0=0, cmd1=0`, 4-byte payload,
ack timeout 0) immediately before each command group — see section 10.2.

### 11.3 Reading a reply
1. Wait for the interrupt line to go high (level, active high).
2. Read exactly 4 bytes.  Validate: high nibble of byte 0 must be 0xA or 0xB,
   and byte 3 must equal `(b0 + b1 + b2) & 0xFF`.
3. Length is `b1 | (b2 << 8)`.  Read that many more bytes.
4. `ff ff ff ff` fails the checksum test (0xFD != 0xFF) and means an idle wire,
   not a reply.  `00 00 00 00` likewise.

### 11.4 Correction to section 7
Both firmware images are embedded **17 times** each, not 17 and 1, and the
copies are not contiguous — they sit inside a larger repeating structure, so the
record spacing is not the image length.  `tools/extract_firmware.py` uses the
documented lengths and proves them with SHA-256.
