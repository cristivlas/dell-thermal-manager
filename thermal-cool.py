#! /usr/bin/env python3
'''
Keep DELL server cool and quiet (or at least try to find a good balance).

REQUIREMENTS:
The following commands must be available:
    ipmitool
    nvidia-smi
    sensors
'''

import logging
import os
import subprocess
import sys

from time import sleep

'''
Degrees Celsius to percentages of fan power.
'''
CPU_TEMP_FAN_POWER = {
   105:100,
   100: 80,
    95: 70,
    90: 60,
    88: 55,
    85: 50,
    83: 45,
    82: 40,
    81: 35,
    80: 32,
    79: 30,
    78: 28,
    77: 26,
    75: 25,
    72: 23,
    70: 21,
    65: 16,
    60: 12,
    55: 10,
    45:  5,
}

GPU_TEMP_FAN_POWER = {
   100: 80,
    97: 60,
    95: 50,
    92: 47,
    90: 45,
    87: 43,
    85: 40,
    80: 35,
    77: 30,
    75: 27,
    73: 25,
    72: 20,
    70: 15,
    67: 10,
    65:  7,
    60:  5,
    55:  5,
    45:  3,
}

PCI_TEMP_FAN_POWER = {
   100:100,
    70: 90,
    65: 80,
    60: 60,
    55: 45,
    50: 35,
    45: 25,
    42: 20,
    40: 15,
    35: 12,
    30: 10,
}

DRIVE_TEMP_FAN_POWER = {
    50: 80,   # Aggressive above 50°C
    45: 60,   # Ramp up at 45°C  
    40: 40,   # Start responding at 40°C
    35: 25,
    30: 15,
    25: 10,
}


TARGET_TEMP = 70


def run_cmd(args, input=None):
    return subprocess.run(args, check=True, input=input, stdout=subprocess.PIPE).stdout


def fan_set_manual_control(on):
    cmd = [ "ipmitool", "raw", "0x30", "0x30", "0x01",  "0x00" if on else "0x01" ]
    logging.debug(" ".join(cmd))
    run_cmd(cmd)


def fan_set_level(level):
    level = max(min(100, level), 10) # clamp [10, 100]
    cmd = [ "ipmitool", "raw", "0x30", "0x30", "0x02", "0xff", f"0x{level:02x}" ]
    logging.debug(" ".join(cmd))
    run_cmd(cmd)


def get_min_fan_power(temp_to_fan_power, target_temp):
    sorted_temp_to_fan_power = sorted(temp_to_fan_power.items())

    for temp, fan_power in sorted_temp_to_fan_power:
        if temp >= target_temp:
            return fan_power

    return 100 # percent


def get_gpu_temperatures():
    output = run_cmd(['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader'])
    return list(map(float, output.decode().split()))


def get_core_temperatures(report):
    r"""
    Use `sensors` command to read CPU core temperatures
    sensors | grep Core | sed -e 's/ \+/ /g;s/[^[:alnum:].-| ]//g' | cut -f3 -d' ' | sed -e 's/[C|F]//g'

    Return max temperature.
    """
    output = run_cmd(['sensors'])
    if report:
        for line in output.decode().split('\n'):
            if line:
                print(line)

    grep = run_cmd(['grep', 'Core'], input=output)
    sed1 = run_cmd(['sed', '-e', r's/ \+/ /g;s/[^[:alnum:].-| ]//g'], input=grep)
    cut = run_cmd(['cut', '-f3', '-d', ' '], input=sed1)
    sed2 = run_cmd(['sed', '-e', 's/[C|F]//g'], input=cut)

    return list(map(float, sed2.split()))


def get_pci_temperatures(report):
    r"""
    Use `sensors` command to read CPU core temperatures
    sensors | grep loc | sed -e 's/ \+/ /g;s/[^[:alnum:].-| ]//g' | cut -f3 -d' ' | sed -e 's/[C|F]//g'

    Return max temperature.
    """
    output = run_cmd(['sensors'])
    if report:
        for line in output.decode().split('\n'):
            if line:
                print(line)

    grep = run_cmd(['grep', 'loc'], input=output)
    sed1 = run_cmd(['sed', '-e', r's/ \+/ /g;s/[^[:alnum:].-| ]//g'], input=grep)
    cut = run_cmd(['cut', '-f2', '-d', ' '], input=sed1)
    sed2 = run_cmd(['sed', '-e', 's/[C|F]//g'], input=cut)

    return list(map(float, sed2.split()))


def get_drive_list():
    """Get list of physical drives from lsblk"""
    # Get block devices, no headers, only names of disks (not partitions)
    output = run_cmd(['lsblk', '-d', '-n', '-o', 'NAME,TYPE'])
    drives = []
    
    for line in output.decode().split('\n'):
        if line.strip():
            name, dtype = line.split()
            if dtype == 'disk':  # Only physical disks, not partitions
                drives.append(name)
    
    logging.debug(f"Found drives: {drives}")
    return drives


def get_drive_temp(drive):
    """Get temperature for a single drive using smartctl"""
    try:
        output = run_cmd(['smartctl', '-A', f'/dev/{drive}'])
        # More specific grep to avoid matching warning/critical lines
        grep = run_cmd(['grep', '-E', '(Temperature_Celsius|^Temperature:)'], input=output)
        
        # Extract the temperature number
        # For SATA: "194 Temperature_Celsius ... - 23 (Min/Max...)"  
        # For NVMe: "Temperature:                        26 Celsius"
        sed = run_cmd(['sed', '-e', 's/.*[[:space:]-]\\([0-9]\\+\\)[[:space:]]*\\(Celsius\\|(.*)\\).*/\\1/'], input=grep)
        temp_str = sed.decode().strip()
        
        # Handle case where sed might not match properly
        if not temp_str.isdigit():
            # Fallback: extract any number followed by "Celsius"
            import re
            match = re.search(r'(\d+)\s*Celsius', grep.decode())
            if match:
                temp_str = match.group(1)
            else:
                raise ValueError(f"Could not parse temperature from: {grep.decode().strip()}")
        
        temp = float(temp_str)
        logging.debug(f"Drive {drive}: {temp}°C")
        return temp
    except Exception as e:
        logging.warning(f"Failed to get temp for {drive}: {e}")
        return 0


def get_max_drive_temperature():
    """Get max temperature from all drives"""
    drives = get_drive_list()
    temps = {}
    
    for drive in drives:
        temp = get_drive_temp(drive)
        if temp > 0:  # Only include valid temperatures
            temps[drive] = temp
    
    max_temp = max(temps.values()) if temps else 0
    logging.debug(f"Drive temperatures: {temps}, max: {max_temp}°C")
    return max_temp


def get_fan_power(report, target_temp=None):
    if target_temp is None:
        # get hotest CPU, and print out a report
        max_cpu_temp = max(get_core_temperatures(report))

        gpu_temperatures = get_gpu_temperatures()
        # print(f'GPU(s): {gpu_temperatures}')
        # get hotest GPU
        max_gpu_temp = max(gpu_temperatures)

        max_pci_temp = max(get_pci_temperatures(report))
        # print(max_pci_temp)

        max_drive_temp = get_max_drive_temperature()

        return max(
            get_min_fan_power(CPU_TEMP_FAN_POWER, max_cpu_temp),
            get_min_fan_power(GPU_TEMP_FAN_POWER, max_gpu_temp),
            get_min_fan_power(PCI_TEMP_FAN_POWER, max_pci_temp),
            get_min_fan_power(DRIVE_TEMP_FAN_POWER, max_drive_temp),
        ), max_cpu_temp, max_gpu_temp, max_pci_temp, max_drive_temp
    else:
        return max(
            get_min_fan_power(CPU_TEMP_FAN_POWER, target_temp),
            get_min_fan_power(GPU_TEMP_FAN_POWER, target_temp),
            get_min_fan_power(PCI_TEMP_FAN_POWER, target_temp),
            get_min_fan_power(DRIVE_TEMP_FAN_POWER, target_temp),
        ), None, None, None, None


def main():
    get_core_temperatures(True)
    current = fan_power = get_fan_power(True, TARGET_TEMP)[0]

    # bypass automatic fan control
    fan_set_manual_control(True)

    # set initial power for TARGET_TEMP
    fan_set_level(fan_power)

    while True:
        fan_power, max_cpu_temp, max_gpu_temp, max_pci_temp, max_drive_temp = get_fan_power(report=False)
        log = f'Max temp CPU: {max_cpu_temp}°C, GPU: {max_gpu_temp}°C, PCI: {max_pci_temp}°C, DISK: {max_drive_temp}°C, fan: {current}%'
        delay = 0.5

        if current != fan_power:
            log += f' -> {fan_power}%'
            delay = 3
            fan_set_level(fan_power)
            current = fan_power
            logging.info(log)

        sleep(delay)


if __name__ == '__main__':
    if os.geteuid() != 0:
        print("This script must be run with sudo privileges.", file=sys.stderr)
        exit(-1)

    logging.basicConfig(
        #level=logging.DEBUG,
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    main()

