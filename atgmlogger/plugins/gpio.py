# -*- coding: utf-8 -*-

import logging
import time
import queue

from ..common import Blink
from . import PluginInterface

_APPLOG = logging.getLogger(__name__)

try:
    import RPi.GPIO as gpio
    HAVE_GPIO = True
except (ImportError, RuntimeError):
    HAVE_GPIO = False


class GPIOListener(PluginInterface):
    consumerType = Blink
    options = ['mode', 'data_pin', 'usb_pin']

    def __init__(self):
        super().__init__()
        if not HAVE_GPIO:
            raise RuntimeError("GPIO Module is unavailable. GPIO plugin "
                               "cannot run.")
        self.outputs = []
        self.modes = {'board': gpio.BOARD, 'bcm': gpio.BCM}

    def configure(self, **options):
        super().configure(**options)
        _mode = self.modes[getattr(self, 'mode', 'board')]
        gpio.setmode(_mode)

        self.outputs = [getattr(self, pin) for pin in ['data_pin', 'usb_pin']
                        if hasattr(self, pin)]
        for pin in self.outputs:
            gpio.setup(pin, gpio.OUT)

    def _blink(self, blink):
        if blink.led not in self.outputs:
            return
        if HAVE_GPIO:
            gpio.output(blink.led, True)
            time.sleep(blink.frequency)
            gpio.output(blink.led, False)
            time.sleep(blink.frequency)

    def run(self):
        if not HAVE_GPIO:
            _APPLOG.warning("GPIO Module is unavailable. Exiting %s thread.",
                            self.__class__.__name__)
            return

        while not self.exiting:
            try:
                blink = self.get()
            except queue.Empty:
                continue
            else:
                self._blink(blink)
                self.queue.task_done()

        for pin in self.outputs:
            gpio.output(pin, False)
        gpio.cleanup()


if HAVE_GPIO:
    __plugin__ = GPIOListener
else:
    __plugin__ = None