#!/usr/bin/env python3
"""Minimal Linux GPIO character-device (uAPI v2) wrapper - no external deps.
Needed to hold the Goodix sensor's enable/reset line while we drive SPI."""
import fcntl, struct, os, ctypes

_IOC_READ, _IOC_WRITE = 2, 1
def _IOWR(t, nr, size): return ((_IOC_READ | _IOC_WRITE) << 30) | (size << 16) | (t << 8) | nr

GPIO_V2_LINES_MAX, GPIO_MAX_NAME_SIZE, GPIO_V2_LINE_NUM_ATTRS_MAX = 64, 32, 10
LINE_ATTR_SZ   = 16                       # gpio_v2_line_attribute
CFG_ATTR_SZ    = LINE_ATTR_SZ + 8         # gpio_v2_line_config_attribute (attr + mask)
LINE_CONFIG_SZ = 8 + 4 + 20 + GPIO_V2_LINE_NUM_ATTRS_MAX * CFG_ATTR_SZ      # 272
LINE_REQ_SZ    = GPIO_V2_LINES_MAX*4 + GPIO_MAX_NAME_SIZE + LINE_CONFIG_SZ + 4 + 4 + 20 + 4
assert LINE_CONFIG_SZ == 272 and LINE_REQ_SZ == 592, (LINE_CONFIG_SZ, LINE_REQ_SZ)

GPIO_V2_GET_LINEINFO_IOCTL   = _IOWR(0xB4, 0x05, 4 + 32 + 32 + 8 + 4 + 4 + 4*4)  # unused
GPIO_V2_GET_LINE_IOCTL       = _IOWR(0xB4, 0x07, LINE_REQ_SZ)
GPIO_V2_LINE_GET_VALUES_IOCTL = _IOWR(0xB4, 0x0E, 16)
GPIO_V2_LINE_SET_VALUES_IOCTL = _IOWR(0xB4, 0x0F, 16)

FLAG_ACTIVE_LOW   = 1 << 1
FLAG_INPUT        = 1 << 2
FLAG_OUTPUT       = 1 << 3
FLAG_BIAS_PULL_UP = 1 << 8
FLAG_BIAS_DISABLED = 1 << 10
ATTR_ID_OUTPUT_VALUES = 2

class GpioLine:
    """Request one line and hold it for the lifetime of this object."""
    def __init__(self, chip, offset, output=False, value=0, consumer=b"gxfp-re"):
        self.cfd = os.open(chip, os.O_RDWR)
        flags = FLAG_OUTPUT if output else FLAG_INPUT
        # gpio_v2_line_config: flags(u64), num_attrs(u32), pad[5](u32), attrs[10]
        num_attrs = 1 if output else 0
        attrs = b""
        if output:
            # attr: id(u32) pad(u32) values(u64) ; then mask(u64)
            attrs += struct.pack("<IIQQ", ATTR_ID_OUTPUT_VALUES, 0, value & 1, 1)
        attrs = attrs.ljust(GPIO_V2_LINE_NUM_ATTRS_MAX * CFG_ATTR_SZ, b"\0")
        cfg = struct.pack("<QI", flags, num_attrs) + b"\0"*20 + attrs
        assert len(cfg) == LINE_CONFIG_SZ
        offsets = struct.pack("<I", offset) + b"\0" * ((GPIO_V2_LINES_MAX-1)*4)
        req = offsets + consumer.ljust(GPIO_MAX_NAME_SIZE, b"\0") + cfg \
              + struct.pack("<II", 1, 0) + b"\0"*20 + struct.pack("<i", -1)
        assert len(req) == LINE_REQ_SZ
        buf = ctypes.create_string_buffer(req, LINE_REQ_SZ)
        fcntl.ioctl(self.cfd, GPIO_V2_GET_LINE_IOCTL, buf)
        self.fd = struct.unpack_from("<i", buf.raw, LINE_REQ_SZ - 4)[0]
        if self.fd < 0:
            raise OSError("gpio line request returned fd %d" % self.fd)
        self.offset = offset

    def set(self, v):
        buf = ctypes.create_string_buffer(struct.pack("<QQ", v & 1, 1), 16)
        fcntl.ioctl(self.fd, GPIO_V2_LINE_SET_VALUES_IOCTL, buf)

    def get(self):
        buf = ctypes.create_string_buffer(struct.pack("<QQ", 0, 1), 16)
        fcntl.ioctl(self.fd, GPIO_V2_LINE_GET_VALUES_IOCTL, buf)
        return struct.unpack_from("<Q", buf.raw, 0)[0] & 1

    def close(self):
        try: os.close(self.fd)
        finally: os.close(self.cfd)
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
