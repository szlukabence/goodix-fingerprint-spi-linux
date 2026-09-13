#!/usr/bin/env python3
"""
Run an old sweep script unchanged, but with pin 189 INVERTED (AUDIT AG/AH3).

Every earlier script treats line 264 (pin 189) as "enable, HIGH = on" and ends
each reset pulse HIGH. Windows keeps that pin LOW while the sensor works. This
wrapper swaps gpio_raw.GpioLine for a subclass that inverts line 264 only, so a
script's "LOW 10 ms -> HIGH" becomes "HIGH 10 ms -> LOW": a short reset that
leaves the pin in the Windows state. Nothing else in the script changes.

usage (root):  python3 lowstate.py bruteforce.py
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gpio_raw

LINE_EN = 264
_Base = gpio_raw.GpioLine


class InvertedEnable(_Base):
    def __init__(self, chip, offset, output=False, value=0, consumer=b"gxfp-re"):
        self._inv = (offset == LINE_EN)
        if self._inv and output:
            value = 0 if value else 1
        super().__init__(chip, offset, output=output, value=value, consumer=consumer)

    def set(self, v):
        return super().set((0 if v else 1) if self._inv else v)

    def get(self):
        v = super().get()
        return (0 if v else 1) if self._inv else v


gpio_raw.GpioLine = InvertedEnable

script = os.path.join(HERE, sys.argv[1])
print("### lowstate wrapper: %s with pin 189 INVERTED (reset pulses end LOW)" % sys.argv[1])
sys.argv = [script] + sys.argv[2:]
runpy.run_path(script, run_name="__main__")

irq = _Base("/dev/gpiochip0", 48, output=False)
print("### lowstate wrapper: finished, interrupt = %d (0 expected in the Windows state)" % irq.get())
irq.close()
