# AKP05 Bridge (Home Assistant add-on)

Owns the USB connection to the Ajazz AKP05 on your Home Assistant OS /
Supervised host and exposes it over a local HTTP+WebSocket API. Pair it
with the `custom_components/akp05` integration (see the repo root
[README](../../README.md#running-natively-on-home-assistant-os)) to get
the device as a real HA device with automation triggers and a brightness
entity — this add-on by itself doesn't do anything visible in HA.

## Prerequisites

- Home Assistant OS or Supervised (this needs Supervisor — it won't work
  on Home Assistant Container/Core-only installs).
- The AKP05 physically reachable by that host. If HA OS is itself a VM
  (Proxmox, ESXi, etc.), pass the USB device through to the VM first —
  the add-on's USB access starts from what the host OS can already see.

## Installing it

Pick whichever of these two you find easier — both end up in the same
place.

### Option A — add this repo as an add-on repository (recommended)

This is the least fiddly if the repo's already on GitHub, and gives you
"Check for updates" going forward instead of re-copying files.

1. Push your changes (this `addon/` folder needs to be on the remote,
   not just local) — `git push origin homeassistant` or merge to
   `main` first, whichever you're using.
2. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ (top
   right) → Repositories**, and add:
   ```
   https://github.com/zeccola/ajazz-akp05
   ```
3. Close and reopen the Add-on Store. An "AKP05 Bridge" card should
   appear (Supervisor scans the whole repo for folders containing a
   `config.yaml`, so it finds `addon/akp05_bridge/` automatically —
   no `repository.yaml` needed for a single add-on like this).
4. Click it, then **Install**.

### Option B — copy the folder directly as a local add-on

No GitHub push needed, good for testing changes before committing them.

1. Get file access to the HA host: install the **Samba share** add-on
   (easiest from Windows — mounts `\\<host>\addons`) or the **SSH &
   Terminal**/**Terminal & SSH** add-on if you'd rather `scp`/`rsync`.
2. Copy this whole `addon/akp05_bridge/` folder to the host at
   `/addons/local/akp05_bridge/` (the folder itself must contain
   `config.yaml` directly, not a nested subfolder).
3. **Settings → Add-ons → Add-on Store → ⋮ → Check for updates**. An
   "AKP05 Bridge" card should appear under **Local add-ons**.
4. Click it, then **Install**.

Either way, the first install builds a Docker image on the host, which
takes a few minutes — the progress log is visible while it builds.

## Configure and start

1. Open the add-on's **Configuration** tab and set `api_token` to any
   random string you make up (e.g. generate one with
   `python -c "import secrets; print(secrets.token_hex(16))"`). This is
   the only thing authenticating requests to its API, since it has to be
   reachable from Home Assistant Core's container, not just localhost —
   don't leave it blank. Save.
2. Go to the **Info** tab and toggle **Start**. Optionally enable **Start
   on boot** and **Watchdog** too, so it comes back automatically after
   an HA restart or crash.
3. Open the **Log** tab and confirm you see the device get found (no HID
   errors). If it doesn't:
   - Nothing shows up at all / immediate crash → check the log for a
     Python traceback first, that'll usually name the problem directly.
   - "No hidraw device found" → the container can't see the USB device.
     Confirm the AKP05 shows up on the *host* itself first (this is the
     VM-passthrough case mentioned above), then double check the add-on
     still has `usb: true`/`udev: true` (Option A/B above should have
     brought `config.yaml` over correctly — don't hand-edit these unless
     you know you need to).
   - Device found but a permission error opening `/dev/hidrawN` → a
     udev-rule/permission mismatch inside the container. This project
     hasn't had a chance to confirm the exact permissions Supervisor's
     `udev: true` grants against real AKP05 hardware yet — if you hit
     this, it needs a udev rule added to the add-on to fix, not a config
     change on your end.

## Verify the API before touching the integration

From any machine on your LAN (replace host/token):

```
curl http://homeassistant.local:8000/status \
  -H "Authorization: Bearer <your api_token>"
```

should return something like `{"connected": true, "brightness": 50}`.
Confirming this works first makes it much easier to tell, if the
integration setup step later fails, whether the problem is the add-on or
the integration side.

Once that works, install `custom_components/akp05/` and add the
integration — see the root README's
[Home Assistant OS section](../../README.md#running-natively-on-home-assistant-os)
for that part.

## Updating

If you change `akp05_device.py` or `akp05_icons.py` at the repo root,
run `python addon/akp05_bridge/sync_vendor.py` to copy the changes into
this folder before rebuilding — Docker's build context is this folder
only, so it can't reach the repo root copies directly (see
`sync_vendor.py`'s docstring). Then reinstall/rebuild the add-on (Option
A: push + "Check for updates" + Update; Option B: re-copy the folder +
rebuild from the add-on's page).
