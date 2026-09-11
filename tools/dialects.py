#!/usr/bin/env python3
"""
Speak every Goodix dialect anyone has documented, plus the whole command space.

Everything this project has sent used ONE protocol: the Milan A0-framed message
layer. If the part is in some other state -- a bootloader, a different firmware
mode -- those commands are gibberish to it and silence is the expected result.

So this tries, in order:

  1. EVERY COMMAND in the Milan space: cmd0 0x0-0xF x cmd1 0-7, correctly
     A0-framed with correct checksums. 128 commands. Never done exhaustively.
  2. OpenGoodixSPI's raw patterns -- 0xB0/0xF0/0x0C repeated, NO transport
     header. Their driver memsets a 4-byte buffer and sends it bare.
  3. GR5515 DFU framing, magic 'GD' (0x4744). PopulusYang believes the MCU is a
     GR5515 whose ROM bootloader speaks this. (Our evidence says ST411, but the
     cost of asking is nothing.)
  4. The "IHA" framing, magic 0x00010000 big-endian, commands 0x19/0x07/0x09.
  5. The 0xccf2 "community" magic.
  6. Bare patterns: 00, ff, aa, 55, and a 0x7F-byte ramp.

Formats 3-5 are reconstructions from third-party notes and may be wrong in
detail. That is acceptable: we are not trying to have a conversation, we are
trying to provoke ANY response at all. Detector is the usual crude one --
anything that is not 0x00/0xff.

Run as root.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spidev_raw import Spi
from gpio_raw import GpioLine

SPI_DEV="/dev/spidev1.0"; CHIP="/dev/gpiochip0"; RST=264

def hdr(n, t=0xA0):
    h=bytes([t, n & 0xFF, (n >> 8) & 0xFF]); return h + bytes([sum(h) & 0xFF])

def milan(cmd0, cmd1, payload=b"\x00\x00"):
    cmd=((cmd0 << 4) | (cmd1 << 1)) & 0xFF
    n=len(payload) + 1
    body=bytes([cmd, n & 0xFF, (n >> 8) & 0xFF]) + payload
    return body + bytes([(0xAA - sum(body)) & 0xFF])

def odd(b): return sorted(set(b) - {0x00, 0xFF})

def reset(t=0.2):
    g=GpioLine(CHIP, RST, output=True, value=1)
    g.set(0); time.sleep(0.01); g.set(1); time.sleep(t); return g

def look(s, n=64):
    return s.duplex(bytes(n))

hits=[]
def check(label, rx):
    o=odd(rx)
    if o:
        hits.append((label, o[:12]))
        print("   %-40s *** NON-IDLE %s ***" % (label, o[:12]))
        return True
    return False

def main():
    if os.geteuid() != 0: sys.exit("run as root")
    if not os.path.exists(SPI_DEV): sys.exit("%s missing" % SPI_DEV)
    print("### dialect sweep  %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))

    g=reset(); s=Spi(SPI_DEV, mode=0, speed=1_000_000)

    print("--- 1. the ENTIRE Milan command space (cmd0 0-F x cmd1 0-7 = 128)")
    found=0
    for c0 in range(16):
        for c1 in range(8):
            b=milan(c0, c1)
            s.duplex(hdr(len(b))); time.sleep(0.001); s.duplex(b); time.sleep(0.01)
            if check("cmd0=0x%X cmd1=%d (0x%02x)" % (c0, c1, ((c0<<4)|(c1<<1))&0xFF), look(s)):
                found += 1
    print("   128 commands sent, %d produced anything" % found)

    print("\n--- 2. OpenGoodixSPI raw patterns (no transport header)")
    for name, byte in (("WAKE 0xB0", 0xB0), ("CHIPID 0xF0", 0xF0), ("RESET 0x0C", 0x0C)):
        s.duplex(bytes([byte]) * 4); time.sleep(0.02)
        check("raw %s x4" % name, look(s))
        s.duplex(bytes([byte]) * 8); time.sleep(0.02)
        check("raw %s x8" % name, look(s))

    print("\n--- 3. GR5515 DFU framing, magic 'GD' (0x4744)")
    for cmd in (0x01, 0x02, 0x05, 0x10):
        f=bytes([0x44, 0x47, cmd, 0x00, 0x00, 0x00])
        f += bytes([sum(f) & 0xFF, (sum(f) >> 8) & 0xFF])
        s.duplex(f); time.sleep(0.03)
        check("GR5515 DFU cmd 0x%02x" % cmd, look(s))

    print("\n--- 4. IHA framing, magic 0x00010000 big-endian")
    for cmd in (0x19, 0x07, 0x09):
        f=bytes([0x00, 0x01, 0x00, 0x00, cmd, 0x00, 0x00, 0x00])
        s.duplex(f); time.sleep(0.03)
        check("IHA cmd 0x%02x" % cmd, look(s))

    print("\n--- 5. 0xccf2 community magic")
    for cmd in (0xa0, 0xae, 0x00):
        f=bytes([0xcc, 0xf2, cmd, 0x00, 0x00, 0x00, 0x00, 0x00])
        s.duplex(f); time.sleep(0.03)
        check("ccf2 cmd 0x%02x" % cmd, look(s))

    print("\n--- 6. bare patterns")
    for name, buf in (("all 0x00", bytes(32)), ("all 0xff", b"\xff"*32),
                      ("all 0xaa", b"\xaa"*32), ("all 0x55", b"\x55"*32),
                      ("0..0x7f ramp", bytes(range(128)))):
        s.duplex(buf); time.sleep(0.03)
        check(name, look(s))

    s.close(); g.close()
    print("\n" + "="*68)
    if hits:
        print("*** THE SENSOR SAID SOMETHING:")
        for l, o in hits: print("     %-42s %s" % (l, o))
    else:
        print("every dialect, every command, every pattern: ff and nothing else")
    print("="*68)

main()
