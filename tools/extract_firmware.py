#!/usr/bin/env python3
"""
Extract the sensor MCU firmware images that ship inside Goodix's Windows driver.

The community has long believed this sensor family's firmware was undiscovered
and that a blob had to be sourced from somewhere.  It does not: both MCU images
are embedded verbatim in `gfspi.dll`, in the `.rdata` section, as records of the
form

    [u8 name_len][name ASCII][raw image]

This script finds them and writes them out.  The images are Goodix's
copyrighted code, so they are deliberately NOT redistributed with this project
-- you extract them from the driver you already have a licence to, on the
machine you own.

Usage:
    python3 extract_firmware.py /path/to/gfspi.dll [-o outdir]

`gfspi.dll` lives in the driver package, or on a Windows install under
    C:\\Windows\\System32\\DriverStore\\FileRepository\\gfspi.inf_amd64_*\\
"""
import argparse
import hashlib
import os
import sys

# Known-good results, from gfspi.dll v1.1.141.40
#   sha256(gfspi.dll) = 36033fbf507620776d9fb686ecfe7847ff41fcbdee6e2afad119e28c6f81ca04
KNOWN = {
    "GF_ST411SEC_APP_14115": dict(
        size=85994,
        sha256="6e6cc79bedfbd51cf02f92a00cf3d42a0f1083c134e919b60b265f93f8f37aa4",
        mcu="STM32 (ST411) - flash @0x08000000, image base 0x08020000",
        note="this is the image our MateBook 13's sensor actually runs",
    ),
    "GF_HC460SEC_APP_14104": dict(
        size=84150,
        sha256="7c752777ea13741d8e7c8ceb45f814f485bbe8de5fc7134685ad2d9b2a650699",
        mcu="HDSC HC32 (HC460) - flash @0x00000000",
        note="the alternate MCU vendor; needs a different IAP command set",
    ),
}


def find_records(blob, name):
    """Locate every [len][name][image] record for `name`."""
    marker = bytes([len(name)]) + name.encode()
    hits, at = [], 0
    while True:
        at = blob.find(marker, at)
        if at < 0:
            return hits
        hits.append(at + len(marker))   # offset of the image itself
        at += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dll", help="path to gfspi.dll")
    ap.add_argument("-o", "--outdir", default=".", help="where to write the images")
    args = ap.parse_args()

    blob = open(args.dll, "rb").read()
    print("%s  (%d bytes)" % (args.dll, len(blob)))
    print("sha256 %s" % hashlib.sha256(blob).hexdigest())
    print()

    os.makedirs(args.outdir, exist_ok=True)
    found = 0
    for name, meta in KNOWN.items():
        starts = find_records(blob, name)
        if not starts:
            print("  %-24s NOT FOUND" % name)
            continue

        # The record carries no length field and the copies are not contiguous
        # (they sit inside a larger repeating structure), so the image length
        # comes from the table above and is proven by the hash check below.
        size = meta["size"]
        img = blob[starts[0]:starts[0] + size]
        digest = hashlib.sha256(img).hexdigest()
        ok = digest == meta["sha256"]

        out = os.path.join(args.outdir, name + ".bin")
        with open(out, "wb") as f:
            f.write(img)
        found += 1

        sp = int.from_bytes(img[0:4], "little")
        rv = int.from_bytes(img[4:8], "little")
        print("  %s" % name)
        print("     copies embedded : %d" % len(starts))
        print("     offset in file  : 0x%x  (first copy)" % starts[0])
        print("     size            : %d bytes" % size)
        print("     sha256          : %s  %s" % (digest, "OK" if ok else "MISMATCH"))
        print("     Cortex-M vectors: initial SP 0x%08x, reset 0x%08x" % (sp, rv))
        print("     MCU             : %s" % meta["mcu"])
        print("     note            : %s" % meta["note"])
        print("     written to      : %s" % out)
        print()

    if not found:
        sys.exit("no firmware records found -- is this really gfspi.dll?")
    print("extracted %d image(s)" % found)


if __name__ == "__main__":
    main()
