"""Quick enumeration helper: show every HID top-level collection exposed by
the Ajazz device (VID_0300 & PID_3004), so we know which interface(s) to
listen on. Composite HID devices often expose more than one collection
(e.g. one for keys, one vendor-defined control channel)."""

import pywinusb.hid as hid

VENDOR_ID = 0x0300
PRODUCT_ID = 0x3004

devices = hid.HidDeviceFilter(vendor_id=VENDOR_ID, product_id=PRODUCT_ID).get_devices()

if not devices:
    print(f"No HID collections found for VID_{VENDOR_ID:04X} & PID_{PRODUCT_ID:04X}.")
    print("Is the device plugged in? Try Device Manager to confirm the IDs.")
else:
    print(f"Found {len(devices)} HID collection(s):\n")
    for d in devices:
        d.open()
        try:
            print(f"- path            : {d.device_path}")
            print(f"  vendor_name     : {d.vendor_name!r}")
            print(f"  product_name    : {d.product_name!r}")
            print(f"  serial_number   : {d.serial_number!r}")
            print(f"  usage_page      : {hex(d.hid_caps.usage_page)}")
            print(f"  usage           : {hex(d.hid_caps.usage)}")
            print(f"  input_report_len: {d.hid_caps.input_report_byte_length}")
            print(f"  output_report_len: {d.hid_caps.output_report_byte_length}")
            print(f"  feature_report_len: {d.hid_caps.feature_report_byte_length}")
            print()
        finally:
            d.close()
