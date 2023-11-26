#!/usr/bin/env python3
# ===================================================================================
# Project:   stc8isp - Programming Tool for STC8G/8H Microcontrollers
# Version:   v0.1
# Year:      2023
# Author:    Stefan Wagner
# Github:    https://github.com/wagiminator
# License:   MIT License
# ===================================================================================
#
# Description:
# ------------
# Simple Python tool for flashing STC8G/8H microcontrollers via USB-to-serial 
# converter utilizing the factory built-in embedded boot loader.
#
# Dependencies:
# -------------
# - pyserial
#
# Operating Instructions:
# -----------------------
# You need to install PySerial to use stc8isp.
# Install it via "python3 -m pip install pyserial".
# You may need to install a driver for your USB-to-serial converter.
#
# Connect your USB-to-serial converter to your MCU and to a USB port of your PC.
# Run "python3 stc8isp.py -p /dev/ttyUSB0 -f firmware.bin".
# Perform a power cycle of your MCU (reconnect to power) when prompted.

# If the PID/VID of the USB-to-Serial converter is known, it can be defined here.
# The specified COM port is then ignored, and all ports are automatically searched 
# for the device. Comment the lines to ignore PID/VID.
#STC_VID  = '1A86'
#STC_PID  = '7523'

# Define the default COM port here. This will be used if no VID/PID is defined and
# no COM port is specified within the arguments.
STC_PORT = '/dev/ttyUSB0'

# Define BAUD rate here, range: 2400 - 115200, default: 115200
STC_BAUD = 115200

# Define time to wait for power cycle in seconds, default: 10
STC_WAIT = 10

# Libraries
import sys
import time
import argparse
import serial
from serial import Serial
from serial.tools.list_ports import comports

# ===================================================================================
# Main Function
# ===================================================================================

def _main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Minimal command line interface for stc8isp')
    parser.add_argument('-p', '--port',  default=STC_PORT, help='set COM port')
    parser.add_argument('-e', '--erase', action='store_true', help='perform chip erase (implied with -f)')
    parser.add_argument('-f', '--flash', help='write BIN file to flash')
    args = parser.parse_args(sys.argv[1:])

    # Check arguments
    if not any( (args.erase, args.flash) ):
        print('No arguments - no action!')
        sys.exit(0)

    # Establish connection to USB-to-serial converter
    try:
        print('Connecting to USB-to-serial converter ...')
        isp = Programmer(args.port)
        print('SUCCESS: Connected via', isp.port + '.')
    except Exception as ex:
        sys.stderr.write('ERROR: ' + str(ex) + '!\n')
        sys.exit(1)

    # Performing actions
    try:
        # Connect to and identify MCU
        print('Waiting for MCU power cycle ...')
        isp.identify()
        print('SUCCESS: Found', isp.chipname, 'version', isp.chipverstr + '.')

        # Set BAUD rate
        print('Setting BAUD rate ...')
        isp.setbaud()
        time.sleep(0.01)
        isp.checkbaud()
        print('SUCCESS: BAUD rate set to', str(STC_BAUD) + '.')

        # Perform chip erase
        if (args.erase) or (args.flash is not None):
            print('Performing chip erase ...')
            isp.erase()
            print('SUCCESS: Chip is erased.')

        # Flash binary file
        if args.flash is not None:
            print('Flashing', args.flash, 'to MCU ...')
            with open(args.flash, 'rb') as f: data = f.read()
            isp.writeflash(0, data)
            print('SUCCESS:', len(data), 'bytes written.')

        # Close connection
        isp.close()

    except Exception as ex:
        sys.stderr.write('ERROR: ' + str(ex) + '!\n')
        isp.close()
        sys.exit(1)

    print('DONE.')
    sys.exit(0)

# ===================================================================================
# Programmer Class
# ===================================================================================

class Programmer(Serial):
    def __init__(self, port):
        # BAUD rate:  2400 - 115200bps (default: 115200), will be auto-detected
        # Data frame: 1 start bit, 8 data bit, 1 parity bit set to even, 1 stop bit
        super().__init__(baudrate = STC_BAUD, parity = serial.PARITY_EVEN, timeout = 0.01)

        # Use COM port to define device
        if 'STC_VID' not in globals() or 'STC_PID' not in globals():
            self.port = port

        # Use VID/PID to find device
        else:
            for p in comports():
                if (STC_VID in p.hwid) and (STC_PID in p.hwid):
                    self.port = p.device
                    break

        # Open connection
        try:
            self.open()
        except:
            raise Exception('Failed to connect to USB-to-serial converter')

    # Connect to and identify MCU
    def identify(self):
        # Ping MCU and wait for power cycle
        self.reset_input_buffer()
        waitcounter = STC_WAIT * 100
        reply = None
        while (waitcounter > 0) and (reply is None):
            self.write([STC_SYNCH])
            reply = self.receive()
            waitcounter -= 1
        if reply is None or len(reply) < 23:
            self.close()
            raise Exception('Timeout, failed to connect to MCU')

        # Read chip ID
        self.chipid = int.from_bytes(reply[20:22], byteorder='big')
        
        # Find chip in dictionary
        self.device = None
        for d in DEVICES:
            if d['id'] == self.chipid:
                self.device = d
        if self.device is None:
            raise Exception('Unsupported chip (ID: 0x%04x)' % self.chipid)
        self.chipname   = self.device['name']
        self.flash_size = self.device['flash_size']

        # Read chip version
        self.chipversion  = reply[17]
        self.chipstepping = reply[18]
        self.chipminor    = reply[22]
        self.chipverstr   = '%d.%d.%d%c' % (self.chipversion >> 4, self.chipversion & 0x0f, \
                                            self.chipminor & 0x0f, self.chipstepping)

        # Read oscillator frequency
        self.fosc = int.from_bytes(reply[1:5], byteorder='big')

    #--------------------------------------------------------------------------------

    # Transmit data block
    def transmit(self, data):
        size   = len(data) + 6
        parity = STC_TX_CODE + (size >> 8) + (size & 0xff)
        for x in range(len(data)):
            parity += data[x]
        block  = [STC_PREFIX, STC_PREFIX ^ 0xff, STC_TX_CODE]
        block += size.to_bytes(2, byteorder='big')
        block += data
        block += parity.to_bytes(2, byteorder='big')
        block += [STC_SUFFIX]
        self.write(block)
        reply = self.receive()
        if reply is None or len(reply) == 0:
            raise Exception('Invalid response from MCU')
        return reply

    # Receive data block
    def receive(self):
        reply = self.read(1)
        if len(reply) == 0 or reply[0] != STC_PREFIX:
            return None
        self.timeout = 1
        reply = self.read(2)
        if len(reply) != 2 or reply[0] != (STC_PREFIX ^ 0xff) or reply[1] != STC_RX_CODE:
            raise Exception('Invalid data prefix from MCU')
        size   = int.from_bytes(self.read(2), byteorder='big')
        data   = self.read(size - 6)
        check  = int.from_bytes(self.read(2), byteorder='big')
        suffix = self.read(1)[0]
        parity = STC_RX_CODE + (size >> 8) + (size & 0xff)
        for x in range(len(data)):
            parity += data[x]
        if (parity & 0xffff) != check:
            raise Exception('Invalid data checksum from MCU')
        if suffix != STC_SUFFIX:
            raise Exception('Invalid data suffix from MCU')
        return data

    #--------------------------------------------------------------------------------

    # Set BAUD rate
    def setbaud(self):
        count  = 65536 - (STC_FUSER // (4 * STC_BAUD))
        block  = [STC_CMD_BAUD_SET]
        block += [self.fosc & 0xff]
        block += [0x40]
        block += count.to_bytes(2, byteorder='big')
        block += [0x00, 0x00, 0x97]
        reply  = self.transmit(block)
        if reply[0] != STC_CMD_BAUD_SET:
            raise Exception('Failed to set BAUD rate')

    # Check BAUD rate setting
    def checkbaud(self):
        if self.chipversion < 0x72:
            reply = self.transmit([STC_CMD_BAUD_CHECK])
        else:
            reply = self.transmit([STC_CMD_BAUD_CHECK, 0, 0, STC_BREAK, STC_BREAK ^ 0xff])
        if reply[0] != STC_CMD_BAUD_CHECK:
            raise Exception('BAUD rate check failed')

    #--------------------------------------------------------------------------------

    # Erase flash
    def erase(self):
        reply = self.transmit([STC_CMD_ERASE, 0, 0, STC_BREAK, STC_BREAK ^ 0xff])
        if reply[0] != STC_CMD_ERASE:
            raise Exception('Failed to erase flash')

    # Write data to flash
    def writeflash(self, addr, data):
        if len(data) > (self.flash_size - addr):
            raise Exception('Not enough memory')
        block  = [STC_CMD_WRITE]
        block += addr.to_bytes(2, byteorder='big')
        block += [STC_BREAK, STC_BREAK ^ 0xff]
        block += data
        reply  = self.transmit(block)
        if reply[0] != 0x02:
            raise Exception('Failed to write to flash')

# ===================================================================================
# Device Constants
# ===================================================================================

STC_FUSER          = 24000000

STC_SYNCH          = 0x7f
STC_PREFIX         = 0x46
STC_BREAK          = 0x5a
STC_SUFFIX         = 0x16
STC_TX_CODE        = 0x6a
STC_RX_CODE        = 0x68

STC_CMD_BAUD_SET   = 0x01
STC_CMD_BAUD_CHECK = 0x05
STC_CMD_ERASE      = 0x03
STC_CMD_WRITE      = 0x22

# ===================================================================================
# Device Definitions
# ===================================================================================

DEVICES = [
    {'name': 'STC8H1K16',        'id': 0xF721, 'flash_size':  16384},
    {'name': 'STC8H1K20',        'id': 0xF722, 'flash_size':  20480},
    {'name': 'STC8H1K24',        'id': 0xF723, 'flash_size':  24576},
    {'name': 'STC8H1K28',        'id': 0xF724, 'flash_size':  28672},
    {'name': 'STC8H1K33',        'id': 0xF725, 'flash_size':  33792},
    {'name': 'STC8H1K02',        'id': 0xF731, 'flash_size':   2048},
    {'name': 'STC8H1K04',        'id': 0xF732, 'flash_size':   4096},
    {'name': 'STC8H1K06',        'id': 0xF733, 'flash_size':   6144},
    {'name': 'STC8H1K08',        'id': 0xF734, 'flash_size':   8192},
    {'name': 'STC8H1K10',        'id': 0xF735, 'flash_size':  10240},
    {'name': 'STC8H1K12',        'id': 0xF736, 'flash_size':  12288},
    {'name': 'STC8H1K17',        'id': 0xF737, 'flash_size':  17408},
    {'name': 'STC8H3K16S4',      'id': 0xF741, 'flash_size':  16384},
    {'name': 'STC8H3K32S4',      'id': 0xF742, 'flash_size':  32768},
    {'name': 'STC8H3K60S4',      'id': 0xF743, 'flash_size':  61440},
    {'name': 'STC8H3K64S4',      'id': 0xF744, 'flash_size':  65024},
    {'name': 'STC8H3K48S4',      'id': 0xF745, 'flash_size':  49152},
    {'name': 'STC8H3K16S2',      'id': 0xF749, 'flash_size':  16384},
    {'name': 'STC8H3K32S2',      'id': 0xF74A, 'flash_size':  32768},
    {'name': 'STC8H3K60S2',      'id': 0xF74B, 'flash_size':  61440},
    {'name': 'STC8H3K64S2',      'id': 0xF74C, 'flash_size':  65024},
    {'name': 'STC8H3K48S2',      'id': 0xF74D, 'flash_size':  49152},
    {'name': 'STC8G1K02-20/16P', 'id': 0xF751, 'flash_size':   2048},
    {'name': 'STC8G1K04-20/16P', 'id': 0xF752, 'flash_size':   4096},
    {'name': 'STC8G1K06-20/16P', 'id': 0xF753, 'flash_size':   6144},
    {'name': 'STC8G1K08-20/16P', 'id': 0xF754, 'flash_size':   8192},
    {'name': 'STC8G1K10-20/16P', 'id': 0xF755, 'flash_size':  10240},
    {'name': 'STC8G1K12-20/16P', 'id': 0xF756, 'flash_size':  12288},
    {'name': 'STC8G1K17-20/16P', 'id': 0xF757, 'flash_size':  17408},
    {'name': 'STC8G2K16S4',      'id': 0xF761, 'flash_size':  16384},
    {'name': 'STC8G2K32S4',      'id': 0xF762, 'flash_size':  32768},
    {'name': 'STC8G2K60S4',      'id': 0xF763, 'flash_size':  61440},
    {'name': 'STC8G2K64S4',      'id': 0xF764, 'flash_size':  65024},
    {'name': 'STC8G2K48S4',      'id': 0xF765, 'flash_size':  49152},
    {'name': 'STC8G2K16S2',      'id': 0xF769, 'flash_size':  16384},
    {'name': 'STC8G2K32S2',      'id': 0xF76A, 'flash_size':  32768},
    {'name': 'STC8G2K60S2',      'id': 0xF76B, 'flash_size':  61440},
    {'name': 'STC8G2K64S2',      'id': 0xF76C, 'flash_size':  65024},
    {'name': 'STC8G2K48S2',      'id': 0xF76D, 'flash_size':  49152},
    {'name': 'STC8G1K02T',       'id': 0xF771, 'flash_size':   2048},
    {'name': 'STC8G1K04T',       'id': 0xF772, 'flash_size':   4096},
    {'name': 'STC8G1K06T',       'id': 0xF773, 'flash_size':   6144},
    {'name': 'STC8G1K08T',       'id': 0xF774, 'flash_size':   8192},
    {'name': 'STC8G1K10T',       'id': 0xF775, 'flash_size':  10240},
    {'name': 'STC8G1K12T',       'id': 0xF776, 'flash_size':  12288},
    {'name': 'STC8G1K17T',       'id': 0xF777, 'flash_size':  17408},
    {'name': 'STC8H8K16U',       'id': 0xF781, 'flash_size':  16384},
    {'name': 'STC8H8K32U',       'id': 0xF782, 'flash_size':  32768},
    {'name': 'STC8H8K60U',       'id': 0xF783, 'flash_size':  61440},
    {'name': 'STC8H8K64U',       'id': 0xF784, 'flash_size':  65024},
    {'name': 'STC8H8K48U',       'id': 0xF785, 'flash_size':  49152},
    {'name': 'STC8G1K02A-8P',    'id': 0xF791, 'flash_size':   2048},
    {'name': 'STC8G1K04A-8P',    'id': 0xF792, 'flash_size':   4096},
    {'name': 'STC8G1K06A-8P',    'id': 0xF793, 'flash_size':   6144},
    {'name': 'STC8G1K08A-8P',    'id': 0xF794, 'flash_size':   8192},
    {'name': 'STC8G1K10A-8P',    'id': 0xF795, 'flash_size':  10240},
    {'name': 'STC8G1K12A-8P',    'id': 0xF796, 'flash_size':  12288},
    {'name': 'STC8G1K17A-8P',    'id': 0xF797, 'flash_size':  17408},
    {'name': 'STC8G1K02-8P',     'id': 0xF7A1, 'flash_size':   2048},
    {'name': 'STC8G1K04-8P',     'id': 0xF7A2, 'flash_size':   4096},
    {'name': 'STC8G1K06-8P',     'id': 0xF7A3, 'flash_size':   6144},
    {'name': 'STC8G1K08-8P',     'id': 0xF7A4, 'flash_size':   8192},
    {'name': 'STC8G1K10-8P',     'id': 0xF7A5, 'flash_size':  10240},
    {'name': 'STC8G1K12-8P',     'id': 0xF7A6, 'flash_size':  12288},
    {'name': 'STC8G1K17-8P',     'id': 0xF7A7, 'flash_size':  17408}
]

# ===================================================================================

if __name__ == "__main__":
    _main()
