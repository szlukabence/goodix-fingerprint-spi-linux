#!/usr/bin/env python3
"""Minimal spidev ioctl wrapper - no external deps.
Used to talk to the Goodix GXFP51A0 over /dev/spidevX.Y"""
import fcntl, struct, ctypes, os

SPI_IOC_MAGIC = ord('k')
_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2
def _IOC(d, t, nr, size): return (d << 30) | (size << 16) | (t << 8) | nr
def _IOR(t, nr, size): return _IOC(_IOC_READ, t, nr, size)
def _IOW(t, nr, size): return _IOC(_IOC_WRITE, t, nr, size)

SPI_IOC_RD_MODE          = _IOR(SPI_IOC_MAGIC, 1, 1)
SPI_IOC_WR_MODE          = _IOW(SPI_IOC_MAGIC, 1, 1)
SPI_IOC_RD_LSB_FIRST     = _IOR(SPI_IOC_MAGIC, 2, 1)
SPI_IOC_WR_LSB_FIRST     = _IOW(SPI_IOC_MAGIC, 2, 1)
SPI_IOC_RD_BITS_PER_WORD = _IOR(SPI_IOC_MAGIC, 3, 1)
SPI_IOC_WR_BITS_PER_WORD = _IOW(SPI_IOC_MAGIC, 3, 1)
SPI_IOC_RD_MAX_SPEED_HZ  = _IOR(SPI_IOC_MAGIC, 4, 4)
SPI_IOC_WR_MAX_SPEED_HZ  = _IOW(SPI_IOC_MAGIC, 4, 4)
SPI_IOC_RD_MODE32        = _IOR(SPI_IOC_MAGIC, 5, 4)
SPI_IOC_WR_MODE32        = _IOW(SPI_IOC_MAGIC, 5, 4)

XFER_FMT = "<QQIIHBBBBBB"      # 32 bytes, struct spi_ioc_transfer
assert struct.calcsize(XFER_FMT) == 32
def SPI_IOC_MESSAGE(n): return _IOW(SPI_IOC_MAGIC, 0, 32 * n)

class Spi:
    def __init__(self, path, mode=0, speed=10_000_000, bits=8):
        self.fd = os.open(path, os.O_RDWR)
        fcntl.ioctl(self.fd, SPI_IOC_WR_MODE, struct.pack("B", mode))
        fcntl.ioctl(self.fd, SPI_IOC_WR_BITS_PER_WORD, struct.pack("B", bits))
        fcntl.ioctl(self.fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("<I", speed))
        self.speed, self.bits = speed, bits

    def info(self):
        g = lambda req, f: struct.unpack(f, fcntl.ioctl(self.fd, req, b"\0"*struct.calcsize(f)))[0]
        return dict(mode=g(SPI_IOC_RD_MODE, "B"), bits=g(SPI_IOC_RD_BITS_PER_WORD, "B"),
                    speed=g(SPI_IOC_RD_MAX_SPEED_HZ, "<I"), lsb=g(SPI_IOC_RD_LSB_FIRST, "B"),
                    mode32=g(SPI_IOC_RD_MODE32, "<I"))

    def xfer(self, segments, speed=None, cs_change_last=0, word_delay_us=0):
        """segments: list of (tx_bytes_or_None, rxlen, cs_change, delay_us).
        Returns list of rx bytes per segment."""
        speed = speed or self.speed
        bufs, packed = [], b""
        for tx, rxlen, cs_change, delay in segments:
            n = max(len(tx) if tx else 0, rxlen)
            txb = ctypes.create_string_buffer((tx or b"") + b"\x00"*(n-len(tx or b"")), n)
            rxb = ctypes.create_string_buffer(n)
            bufs.append((rxb, rxlen))
            packed += struct.pack(XFER_FMT, ctypes.addressof(txb), ctypes.addressof(rxb),
                                  n, speed, delay, self.bits, cs_change, 0, 0,
                                  word_delay_us & 0xFF, 0)
            bufs[-1] = (txb, rxb, rxlen)          # keep txb alive too
        fcntl.ioctl(self.fd, SPI_IOC_MESSAGE(len(segments)), packed)
        return [bytes(rxb.raw[:rxlen]) for (_, rxb, rxlen) in bufs]

    def duplex(self, tx, speed=None, word_delay_us=0):
        return self.xfer([(tx, len(tx), 0, 0)], speed, word_delay_us=word_delay_us)[0]

    def close(self): os.close(self.fd)

def hexd(b, pfx="  "):
    out = []
    for i in range(0, len(b), 16):
        c = b[i:i+16]
        out.append(f"{pfx}{i:04x}  " + " ".join(f"{x:02x}" for x in c).ljust(48)
                   + " |" + "".join(chr(x) if 32 <= x < 127 else "." for x in c) + "|")
    return "\n".join(out)
