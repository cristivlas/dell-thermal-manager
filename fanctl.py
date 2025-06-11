#! /usr/bin/env python3
import argparse
import logging
import os
import subprocess
import sys

'''
https://github.com/ipmitool/ipmitool/issues/30
https://back2basics.io/1010/05/reduce-the-fan-noise-of-the-dell-r710xd-plus-other-12th-gen-servers-with-ipmi/
'''

def set_manual_control(on: bool) -> None:
    # Use the subprocess module to call the ipmi tool and set manual control
    cmd = [ "ipmitool", "raw", "0x30", "0x30", "0x01",  "0x00" if on else "0x01" ]
    logging.debug(" ".join(cmd))
    subprocess.run(cmd)

def set_fan_level(level: int) -> None:
    # Use the subprocess module to call the ipmi tool and set the fan level
    cmd = [ "ipmitool", "raw", "0x30", "0x30", "0x02", "0xff", f"0x{level:02x}" ]
    logging.debug(" ".join(cmd))
    subprocess.run(cmd)

if __name__ == "__main__":
    # Use argparse to define the command-line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--manual", choices=["on", "off"], help="Turn manual control on or off")
    parser.add_argument("--level", type=int, choices=range(10, 101), metavar="LEVEL", help="Set the fan speed level (10-100)")
    args, _ = parser.parse_known_args()

    if os.geteuid() != 0:
        print("This script must be run with sudo privileges.", file=sys.stderr)
        exit(-1)

    # If the --debug flag is set, enable debugging output
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    # Based on the command-line arguments, call the appropriate function
    if args.manual == "on":
        set_manual_control(True)
    elif args.manual == "off":
        set_manual_control(False)
    if args.level is not None:
        set_fan_level(args.level)

