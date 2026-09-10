#!/usr/bin/env python3
"""Goodix GXFP51A0 "Milan-SPI" protocol, as recovered from gfspi.dll v1.1.141.40.
See ../docs/PROTOCOL.md for the derivation of every constant here."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spidev_raw import Spi, hexd
from gpio_raw import GpioLine

SPI_DEV     = "/dev/spidev1.0"
GPIO_CHIP   = "/dev/gpiochip0"
GPIO_ENABLE = 264          # _CRS GpioIo  -> pinctrl pin 189 (UART0_RXD pad)
GPIO_IRQ    = 48           # _CRS GpioInt -> pinctrl pin 41  (GSPI0_CLK pad)

PKT_MSG, PKT_TLS = 0xA, 0xB
NO_CHECKSUM = 0x88

CMD0 = {0x0:"NOP", 0x2:"IMAGE", 0x3:"FDT", 0x4:"FF", 0x5:"NAV", 0x6:"SLEEP",
        0x7:"IDLE", 0x8:"REG", 0x9:"CHIP", 0xA:"OTHER", 0xB:"MSG", 0xC:"NOTI",
        0xD:"TLSCONN", 0xE:"PROD", 0xF:"UPFW"}

def spi_header(body_len, ptype=PKT_MSG, flags=0):
    """4-byte transport header: [type<<4|flags][lenLO][lenHI][sum of the 3]"""
    h0 = ((ptype << 4) & 0xF0) | (flags & 0x0F)
    lo, hi = body_len & 0xFF, (body_len >> 8) & 0xFF
    return bytes([h0, lo, hi, (h0 + hi + lo) & 0xFF])

def check_header(h):
    """Mirror of CheckPackage() @0x18000be7c. Returns body length or None."""
    if len(h) < 4: return None
    if (h[0] >> 4) not in (PKT_MSG, PKT_TLS): return None
    if h[3] != ((h[0] + h[2] + h[1]) & 0xFF): return None
    return h[1] | (h[2] << 8)

def message(cmd0, cmd1, payload=b"", checksum=True):
    """Inner Milan message: [cmd][lo(len+1)][hi(len+1)][payload][cksum]"""
    cmd = ((cmd0 << 4) | (cmd1 << 1)) & 0xFF
    n = len(payload)
    lo, hi = (n + 1) & 0xFF, ((n + 1) >> 8) & 0xFF
    if checksum:
        s = (cmd + lo + hi + sum(payload)) & 0xFF
        ck = (0xAA - s) & 0xFF
    else:
        ck = NO_CHECKSUM
    return bytes([cmd, lo, hi]) + payload + bytes([ck])

def parse_message(b):
    if len(b) < 4: return None
    cmd = b[0]
    n = (b[1] | (b[2] << 8)) - 1
    payload = b[3:3+n]
    ck = b[3+n] if len(b) > 3+n else None
    ok = ck == NO_CHECKSUM or ck == ((0xAA - ((cmd + b[1] + b[2] + sum(payload)) & 0xFF)) & 0xFF)
    return dict(cmd=cmd, cmd0=cmd >> 4, cmd1=(cmd >> 1) & 7, name=CMD0.get(cmd >> 4, "?"),
                len=n, payload=bytes(payload), cksum=ck, cksum_ok=ok)


class Sensor:
    def __init__(self, speed=1_000_000, verbose=True):
        self.v = verbose
        self.spi = Spi(SPI_DEV, mode=0, speed=speed)
        self.irq = GpioLine(GPIO_CHIP, GPIO_IRQ, output=False)
        self.en  = GpioLine(GPIO_CHIP, GPIO_ENABLE, output=True, value=1)

    def log(self, *a):
        if self.v: print(*a, flush=True)

    def reset(self, keep_low=0.010, settle=0.100):
        self.log(f"[reset] enable low {keep_low*1000:.0f}ms, then high, settle {settle*1000:.0f}ms")
        self.en.set(0); time.sleep(keep_low)
        self.en.set(1); time.sleep(settle)

    def wait_irq(self, timeout=1.0, level=1):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.irq.get() == level: return True
            time.sleep(0.001)
        return False

    def send(self, cmd0, cmd1=0, payload=b"", checksum=True, ptype=PKT_MSG):
        body = message(cmd0, cmd1, payload, checksum)
        hdr = spi_header(len(body), ptype)
        self.log(f"[tx] {CMD0.get(cmd0,'?')} cmd=0x{((cmd0<<4)|(cmd1<<1)):02x} "
                 f"hdr={hdr.hex(' ')} body={body.hex(' ')}")
        self.spi.duplex(hdr)          # transaction 1: 4-byte header, own CS
        time.sleep(0.002)             # the driver's Sleep(2)
        self.spi.duplex(body)         # transaction 2: body, own CS

    def read_packet(self, timeout=1.0):
        """Wait for IRQ, read 4-byte header, then the body."""
        if not self.wait_irq(timeout):
            self.log("[rx] IRQ never asserted")
            return None
        hdr = self.spi.duplex(b"\x00" * 4)
        n = check_header(hdr)
        self.log(f"[rx] hdr={hdr.hex(' ')} -> {'body len %d' % n if n is not None else 'INVALID'}")
        if n is None or n == 0 or n > 4096:
            return None
        body = self.spi.duplex(b"\x00" * n)
        self.log("[rx] body:\n" + hexd(body))
        return hdr, body

    def close(self):
        self.spi.close(); self.irq.close(); self.en.close()
