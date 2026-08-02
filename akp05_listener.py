"""
Raw/debug listener for the Ajazz AKP05 (VID_0300 & PID_3004).

Prints every input report as trimmed hex (padding stripped) plus a
best-effort decode. Useful for digging into anything not yet mapped (e.g.
the touch strip). For clean button/encoder events with no raw bytes, use
akp05_buttons.py instead.

See akp05_device.py for the full protocol write-up. Ctrl+C to stop.
"""

import time

from akp05_device import KEY_IDX, STATE_IDX, connect

KNOWN_NAMES = {
    **{i: f"Key {i}" for i in range(1, 11)},
    0x37: "Encoder 1 btn",
    0x35: "Encoder 2 btn",
    0x33: "Encoder 3 btn",
    0x36: "Encoder 4 btn",
    0xA0: "Encoder 1 CCW",
    0xA1: "Encoder 1 CW",
    0x50: "Encoder 2 CCW",
    0x51: "Encoder 2 CW",
    0x90: "Encoder 3 CCW",
    0x91: "Encoder 3 CW",
    0x70: "Encoder 4 CCW",
    0x71: "Encoder 4 CW",
}


def trim_hex(data: list[int]) -> str:
    last_nonzero = max((i for i, b in enumerate(data) if b != 0), default=0)
    end = max(last_nonzero, STATE_IDX)
    return " ".join(f"{b:02x}" for b in data[: end + 1])


def decode(data: list[int]) -> str | None:
    if len(data) <= STATE_IDX:
        return None
    key = data[KEY_IDX]
    state = data[STATE_IDX]
    if key == 0:
        return None  # idle/no-op report
    name = KNOWN_NAMES.get(key, f"key 0x{key:02x}")
    state_word = "DOWN" if state == 1 else ("UP  " if state == 0 else f"state=0x{state:02x}")
    return f"{name:<14} {state_word}"


def raw_data_handler(data):
    ts = time.strftime("%H:%M:%S")
    decoded = decode(data)
    raw = trim_hex(data)
    label = decoded if decoded else "(unrecognized)"
    print(f"[{ts}] {label:<20} raw: {raw}")


def main():
    print("Connecting and sending init sequence...")
    device = connect(raw_data_handler)
    print("Listening for raw input reports. Press keys / turn encoders / touch the strip. Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        device.close()


if __name__ == "__main__":
    main()
