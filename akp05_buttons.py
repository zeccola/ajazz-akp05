"""
Clean button/encoder listener for the Ajazz AKP05 (VID_0300 & PID_3004).

Prints only recognized events, one line each, no raw hex. For debugging
unmapped input (e.g. the touch strip), use akp05_listener.py instead.

See akp05_device.py for the full protocol write-up. Ctrl+C to stop.
"""

import time

from akp05_device import KEY_IDX, STATE_IDX, connect

KEY_NAMES = {i: f"Key {i}" for i in range(1, 11)}

ENCODER_PRESS = {
    0x37: "Encoder 1",
    0x35: "Encoder 2",
    0x33: "Encoder 3",
    0x36: "Encoder 4",
}

ENCODER_TWIST = {
    0xA0: "Encoder 1 turned CCW",
    0xA1: "Encoder 1 turned CW",
    0x50: "Encoder 2 turned CCW",
    0x51: "Encoder 2 turned CW",
    0x90: "Encoder 3 turned CCW",
    0x91: "Encoder 3 turned CW",
    0x70: "Encoder 4 turned CCW",
    0x71: "Encoder 4 turned CW",
}


def describe(key: int, state: int) -> str | None:
    if key in KEY_NAMES:
        return f"{KEY_NAMES[key]} {'pressed' if state == 1 else 'released'}"
    if key in ENCODER_PRESS:
        return f"{ENCODER_PRESS[key]} button {'pressed' if state == 1 else 'released'}"
    if key in ENCODER_TWIST:
        return ENCODER_TWIST[key]
    return None


def raw_data_handler(data):
    if len(data) <= STATE_IDX:
        return
    key = data[KEY_IDX]
    state = data[STATE_IDX]
    if key == 0:
        return

    message = describe(key, state)
    if message:
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {message}")


def main():
    print("Connecting to AKP05...")
    device = connect(raw_data_handler)
    print("Ready. Press keys / turn encoders / touch the strip. Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
