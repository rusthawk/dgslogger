import os
import sys
import argparse
import time
import yaml
import logging
import logging.config
import threading

import serial
try:
    import RPi.GPIO as gpio
except ImportError:
    print("Raspberry PI GPIO Module is not available, LED signaling disabled.")
    gpio = False


class SerialLogger:
    def __init__(self, argv):
        parser = argparse.ArgumentParser(prog=argv[0],
                                         description="Serial Data Logger")
        parser.add_argument('-V', '--version', action='version',
                            version='0.1')
        parser.add_argument('-v', '--verbose', action='count')
        parser.add_argument('-l', '--logdir', action='store', default='/var/log/dgs')
        parser.add_argument('-d', '--device', action='store', default='/dev/ttyS0')
        opts = parser.parse_args(argv[1:])

        self.thread_poll_interval = 1   # Seconds to sleep between run loops

        # Logging definitions
        self.logdir = os.path.abspath(opts.logdir)
        self.logname = __name__
        self.log = None
        self.data_level = 60
        self.verbosity = opts.verbose
        self.init_logging()

        # Thread signal definitions
        self.exit_signal = threading.Event()
        self.data_signal = threading.Event()
        self.usb_signal = threading.Event()

        # Serial Port Settings (TODO: Read these from configuration file)
        self.device = opts.device
        self.baudrate = 57600
        self.parity = serial.PARITY_NONE
        self.stopbits = serial.STOPBITS_ONE

        # LED Signal Settings
        self.data_led = 11
        self.usb_led = 16
        self.aux_led = 18

        self.log.info("SerialLogger initialized.")

    def init_logging(self):
        """
        Initialize logging facilities, defined in logging.yaml file.
        :return:
        """
        config_f = 'logging.yaml'
        log_yaml = open(config_f, 'r')
        log_dict = yaml.load(log_yaml)

        # Apply base logdir to any filepaths in log_dict
        for hdlr, properties in log_dict.get('handlers').items():
            path = properties.get('filename', False)
            if path:
                # Rewrite log config path with self.logdir as the base
                _, fname = os.path.split(path)
                abs_path = os.path.join(self.logdir, fname)
                log_dict['handlers'][hdlr]['filename'] = abs_path

        # Check/create logging directory
        if not os.path.exists(self.logdir):
            os.makedirs(self.logdir, mode=0o755, exist_ok=False)

        logging.config.dictConfig(log_dict)
        # Select only the first logger defined in the log yaml
        self.logname = list(log_dict.get('loggers').keys())[0]
        self.log = logging.getLogger(self.logname)

    def clean_exit(self, threads):
        self.exit_signal.set()
        self.log.info("Application exiting, joining threads.")
        for thread in threads:
            if thread.is_alive():
                self.log.debug("Thread {} is still alive, joining.".format(thread.name))
                thread.join()
        return 0

    @staticmethod
    def decode(bytearr, encoding='utf-8'):
        if isinstance(bytearr, str):
            return bytearr
        try:
            decoded = bytearr.decode(encoding).strip('\r\n')
        except UnicodeDecodeError:
            illegal = [0, 255]
            decoded = bytes([x for x in bytearr if x not in illegal]).decode(encoding)
        except AttributeError:
            decoded = None
        return decoded

    def device_listener(self, device=None):
        try:
            handle = serial.Serial(device, baudrate=self.baudrate, parity=self.parity,
                                   stopbits=self.stopbits, timeout=1)
        except serial.SerialException:
            self.log.exception('Exception encountered attempting to open serial comm port %s', device)
            return 1
        while not self.exit_signal.is_set():
            try:
                data = self.decode(handle.readline())
                self.log.log(self.data_level, data)
                self.data_signal.set()
            except serial.SerialException:
                self.log.exception('Exception encountered attempting to read from device %s', device)
                handle.close()
                return 1
        if self.exit_signal.is_set():
            self.log.info('Exit signal received, exiting thread %s', device)
        handle.close()
        return 0

    def led_signaler(self):
        if not gpio:
            self.log.warning("GPIO Module is not available, LED signaling will not function.")
            return 1
        # Initialize Raspberry Pi GPIO pins
        gpio.setwarnings(False)
        gpio.setmode(gpio.BOARD)

        gpio.setup(self.data_led, gpio.OUT)
        gpio.setup(self.usb_led, gpio.OUT)
        gpio.setup(self.aux_led, gpio.OUT)

        def blink_led(pin, duration=.1):
            """Turn an output at pin on for duration, then off"""
            # Gets the current state of an output (not necessary currently)
            # state = gpio.input(pin)
            gpio.output(pin, True)
            time.sleep(duration)
            gpio.output(pin, False)

        while not self.exit_signal.is_set():
            # USB signal takes precedence over data recording
            if self.usb_signal.is_set():
                blink_led(self.usb_led)
                # Don't clear the signal, the transfer logic will clear when complete
            elif self.data_signal.is_set():
                blink_led(self.data_led)
                self.data_signal.clear()

    def usb_utility(self):
        pass

    def run(self):
        threads = []

        # Initialize utility threads
        led_thread = threading.Thread(target=self.led_signaler, name='ledsignal')
        led_thread.start()
        threads.append(led_thread)

        usb_thread = threading.Thread(target=self.usb_utility, name='usbutility')
        usb_thread.start()
        threads.append(usb_thread)

        while not self.exit_signal.is_set():
            try:
                # Filter out dead threads
                threads = list(filter(lambda x: x.is_alive(), threads[:]))
                if self.device not in [t.name for t in threads]:
                    self.log.debug("Spawning new thread for device {}".format(self.device))
                    dev_thread = threading.Thread(target=self.device_listener,
                                                  name=self.device, kwargs={'device': self.device})
                    dev_thread.start()
                    threads.append(dev_thread)
            except KeyboardInterrupt:
                self.clean_exit(threads)

            time.sleep(self.thread_poll_interval)
        # Run loop exited - cleanup and return
        self.clean_exit(threads)
        return 0


if __name__ == "__main__":
    main = SerialLogger(sys.argv)
    try:
        exit_code = main.run()
    except KeyboardInterrupt:
        print("KeyboardInterrupt intercepted. Exiting Program.")
    finally:
        exit(exit_code)