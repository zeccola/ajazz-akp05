"""
Set the AKP05's display brightness (affects buttons and strip together).

Confirmed straight from mirajazz's set_brightness(): a single "LIG"
output command with a 0-100 value at payload offset 2 (after two zero
bytes: [0x00, 0x00, percent]). No CLE/STP needed -- brightness isn't
tied to the image-commit cycle, and every other script in this project
has already been sending "LIG" with value 50 as part of its wake-up
sequence, so this command path is well exercised already.

"LIG" appears to only control backlight/PWM level, not a true power
state -- at 0% the panel content is still technically there, just very
dim, not black. There's no confirmed dedicated "sleep"/power-off command
from any of the sources this project is built on. "off" below is a
software approximation: minimum brightness + clearing every button and
the strip to solid black, using the same confirmed CLE command used
elsewhere. This is DESTRUCTIVE -- it wipes whatever images are currently
shown, and "on" cannot restore them for you; you'd need to re-upload.

Usage:
    python akp05_set_brightness.py <0-100>
    python akp05_set_brightness.py off   (dims to 0 AND clears everything to black)
    python akp05_set_brightness.py on    (restores 100% brightness; doesn't restore images)
"""

import sys

from akp05_device import STRIP_WIRE_KEY, crt_command, open_device, send_commands


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]
    device = open_device()
    out_len = device.hid_caps.output_report_byte_length
    try:
        wake_up = [
            crt_command("DIS", [], out_len),
            crt_command("LIG", [0x00, 0x00], out_len),
        ]

        if arg == "off":
            wake_up.append(crt_command("LIG", [0x00, 0x00, 0], out_len))
            wake_up.append(crt_command("CLE", [0x00, 0x00, 0x00, 0xFF], out_len))  # all buttons
            wake_up.append(crt_command("CLE", [0x00, 0x00, 0x00, STRIP_WIRE_KEY], out_len))  # strip
            wake_up.append(crt_command("STP", [], out_len))
            send_commands(device, wake_up)
            print("Display off: brightness 0% + all buttons/strip cleared to black.")
        elif arg == "on":
            wake_up.append(crt_command("LIG", [0x00, 0x00, 100], out_len))
            send_commands(device, wake_up)
            print("Brightness restored to 100%. Note: images were wiped by 'off' -- re-upload them if needed.")
        else:
            percent = max(0, min(100, int(arg)))
            wake_up.append(crt_command("LIG", [0x00, 0x00, percent], out_len))
            send_commands(device, wake_up)
            print(f"Brightness set to {percent}%.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
