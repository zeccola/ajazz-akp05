"""
Local web UI for editing ha_config.json's bindings -- a form instead of
hand-editing JSON -- and for starting/stopping akp05_homeassistant.py as
a subprocess, so you only need to run this one script.

Binds to 127.0.0.1 only, not exposed to your network. No login -- same
trust boundary as editing the file directly: anyone with access to this
machine can open it.

The access token field is always blank when the page loads (never echoes
the saved secret back into the page); leave it blank on save to keep the
existing token, or paste a new one to replace it.

Bindings only need an entity ID -- domain is read off it automatically,
and the service defaults to "toggle" unless you type an override (e.g.
"turn_on" for a scene). Icon is a free-text Material Design Icons name
(e.g. "floor-lamp-outline") -- see https://pictogrammers.com/library/mdi/
for the full set; any name HA itself recognizes works here too.

The bridge control section runs `python akp05_homeassistant.py` as a
child process and shows its recent output (a snapshot on each page load,
not a live stream -- this is a config editor + supervisor, not a
monitoring dashboard). Saving bindings does NOT auto-restart the bridge
if it's already running -- hit Restart yourself so it's an explicit,
visible action rather than something happening behind your back.

Usage:
    python akp05_web.py [path/to/ha_config.json]   (default: ha_config.json)
Then open http://127.0.0.1:5757
"""

import collections
import json
import os
import signal
import subprocess
import sys
import threading

from flask import Flask, redirect, render_template_string, request, url_for

CONFIG_PATH = sys.argv[1] if len(sys.argv) > 1 else "ha_config.json"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BRIDGE_SCRIPT = os.path.join(SCRIPT_DIR, "akp05_homeassistant.py")

EVENT_IDS = (
    [f"button_{i}" for i in range(1, 11)]
    + [f"encoder_{i}_button" for i in range(1, 5)]
    + [f"encoder_{i}_cw" for i in range(1, 5)]
    + [f"encoder_{i}_ccw" for i in range(1, 5)]
)

# First event_id of each visual section, for a header row in the table.
SECTION_STARTS = {
    "button_1": "Buttons",
    "encoder_1_button": "Encoders",
}


def display_label(event_id: str) -> str:
    if event_id.startswith("button_"):
        return f"Button {event_id.split('_')[1]}"
    if event_id.endswith("_button"):
        return f"Encoder {event_id.split('_')[1]} — push"
    if event_id.endswith("_cw"):
        return f"Encoder {event_id.split('_')[1]} — clockwise"
    if event_id.endswith("_ccw"):
        return f"Encoder {event_id.split('_')[1]} — counter-clockwise"
    return event_id

app = Flask(__name__)

bridge = {"process": None, "log": collections.deque(maxlen=200)}
bridge_lock = threading.Lock()


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {"base_url": "http://homeassistant.local:8123", "token": "", "bindings": {}}


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def _pump_output(proc):
    for line in proc.stdout:
        bridge["log"].append(line.rstrip("\n"))
    bridge["log"].append(f"[bridge exited, code {proc.poll()}]")


def bridge_is_running() -> bool:
    proc = bridge["process"]
    return proc is not None and proc.poll() is None


def start_bridge():
    with bridge_lock:
        if bridge_is_running():
            return
        bridge["log"].clear()
        proc = subprocess.Popen(
            [sys.executable, "-u", BRIDGE_SCRIPT, CONFIG_PATH],
            cwd=SCRIPT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        bridge["process"] = proc
        threading.Thread(target=_pump_output, args=(proc,), daemon=True).start()


def stop_bridge():
    with bridge_lock:
        proc = bridge["process"]
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # lets it hit its own KeyboardInterrupt/finally
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>AKP05 -> Home Assistant bindings</title>
<style>
  body { font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8; margin: 2rem; }
  h1 { font-size: 1.3rem; }
  fieldset { border: 1px solid #333; border-radius: 8px; margin-bottom: 1.5rem; padding: 1rem; }
  legend { padding: 0 0.5rem; color: #9ab; }
  label { display: block; margin: 0.4rem 0; }
  input[type=text], input[type=password] { background: #23262c; color: #e8e8e8; border: 1px solid #3a3f47; border-radius: 4px; padding: 0.3rem 0.5rem; width: 260px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: 0.35rem 0.5rem; border-bottom: 1px solid #2a2d33; text-align: left; }
  th { color: #9ab; font-weight: 600; }
  td input[type=text] { width: 170px; }
  td input.narrow { width: 110px; }
  tr.disabled td { opacity: 0.45; }
  tr.section th { background: #1b1e24; color: #9ab; padding-top: 0.6rem; padding-bottom: 0.3rem; border-bottom: 1px solid #2a2d33; }
  .eventname { display: block; }
  .eventname code { display: block; color: #666; font-size: 0.75rem; }
  button { background: #2e7d32; color: white; border: none; border-radius: 6px; padding: 0.5rem 1.2rem; font-size: 0.95rem; cursor: pointer; margin-top: 0.5rem; margin-right: 0.5rem; }
  button:hover { background: #388e3c; }
  button.stop { background: #a33; }
  button.stop:hover { background: #c33; }
  button:disabled { background: #333; color: #777; cursor: not-allowed; }
  .saved { color: #4caf50; margin-left: 1rem; }
  .hint { color: #888; font-size: 0.85rem; }
  code { color: #9ab; }
  .status { font-weight: 600; }
  .status.running { color: #4caf50; }
  .status.stopped { color: #888; }
  pre.log { background: #0c0d10; border: 1px solid #2a2d33; border-radius: 6px; padding: 0.75rem; max-height: 260px; overflow-y: auto; font-size: 0.8rem; color: #ccc; }
</style>
</head>
<body>
<h1>AKP05 &rarr; Home Assistant bindings</h1>

<fieldset>
  <legend>Bridge (akp05_homeassistant.py)</legend>
  <p>Status: <span class="status {{ 'running' if running else 'stopped' }}">{{ '● running' if running else '○ stopped' }}</span></p>
  <form method="post" action="/bridge/start" style="display:inline">
    <button type="submit" {% if running %}disabled{% endif %}>Start</button>
  </form>
  <form method="post" action="/bridge/stop" style="display:inline">
    <button type="submit" class="stop" {% if not running %}disabled{% endif %}>Stop</button>
  </form>
  <form method="post" action="/bridge/restart" style="display:inline">
    <button type="submit">Restart</button>
  </form>
  {% if log_lines %}
  <p class="hint" style="margin-top:0.75rem">Recent output (snapshot, not live -- reload / use the buttons above to refresh):</p>
  <pre class="log">{{ log_lines }}</pre>
  {% endif %}
</fieldset>

<form method="post" action="/save">
  <fieldset>
    <legend>Connection</legend>
    <label>Base URL <input type="text" name="base_url" value="{{ base_url }}"></label>
    <label>Access token <input type="password" name="token" placeholder="{{ 'a token is currently saved -- leave blank to keep it' if has_token else 'paste your long-lived access token' }}"></label>
  </fieldset>

  <table>
    <tr><th>Event</th><th>On</th><th>Entity ID</th><th>Service <span class="hint">(optional, default: toggle)</span></th><th>Data <span class="hint">(optional, JSON object)</span></th><th>Icon <span class="hint">(optional, MDI name)</span></th></tr>
    {% for event_id in event_ids %}
    {% if event_id in section_starts %}
    <tr class="section"><th colspan="6">{{ section_starts[event_id] }}</th></tr>
    {% endif %}
    {% set b = bindings.get(event_id, {}) %}
    <tr class="{{ '' if event_id in bindings else 'disabled' }}">
      <td class="eventname">{{ display_label(event_id) }}<code>{{ event_id }}</code></td>
      <td><input type="checkbox" name="enabled_{{ event_id }}" {% if event_id in bindings %}checked{% endif %}></td>
      <td><input type="text" name="entity_{{ event_id }}" value="{{ b.get('entity_id', '') }}" placeholder="e.g. light.bedroom_lights"></td>
      <td><input type="text" class="narrow" name="service_{{ event_id }}" value="{{ b.get('service', '') }}" placeholder="toggle"></td>
      <td><input type="text" name="data_{{ event_id }}" value='{{ (b["data"] | tojson) if b.get("data") else "" }}' placeholder='e.g. {"brightness_step_pct": 10}'></td>
      <td>
        {% if event_id.startswith('button_') %}
        <input type="text" class="narrow" name="icon_{{ event_id }}" value="{{ b.get('icon', '') }}" placeholder="e.g. floor-lamp-outline">
        {% else %}
        <span class="hint">n/a</span>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>

  <button type="submit">Save</button>
  {% if saved %}<span class="saved">Saved.{% if running %} Bridge is still running with the old config -- hit Restart above to apply.{% endif %}</span>{% endif %}
  {% if data_errors %}<p style="color:#e55">Invalid "Data" JSON, not saved for: {{ data_errors }}</p>{% endif %}
</form>
<p class="hint">Editing {{ config_path }}.</p>
</body>
</html>
"""


def render(saved=False, data_errors=""):
    config = load_config()
    return render_template_string(
        PAGE,
        base_url=config.get("base_url", ""),
        has_token=bool(config.get("token")),
        bindings=config.get("bindings", {}),
        event_ids=EVENT_IDS,
        section_starts=SECTION_STARTS,
        display_label=display_label,
        saved=saved,
        data_errors=data_errors,
        config_path=CONFIG_PATH,
        running=bridge_is_running(),
        log_lines="\n".join(bridge["log"]),
    )


@app.route("/")
def index():
    return render(saved=bool(request.args.get("saved")), data_errors=request.args.get("data_errors", ""))


@app.route("/save", methods=["POST"])
def save():
    config = load_config()

    base_url = request.form.get("base_url", "").strip()
    if base_url:
        config["base_url"] = base_url

    token = request.form.get("token", "").strip()
    if token:
        config["token"] = token

    new_bindings = {}
    data_errors = []
    for event_id in EVENT_IDS:
        if not request.form.get(f"enabled_{event_id}"):
            continue
        entity_id = request.form.get(f"entity_{event_id}", "").strip()
        if not entity_id:
            continue
        binding = {"entity_id": entity_id}
        service = request.form.get(f"service_{event_id}", "").strip()
        if service:
            binding["service"] = service
        data_raw = request.form.get(f"data_{event_id}", "").strip()
        if data_raw:
            try:
                parsed = json.loads(data_raw)
                if not isinstance(parsed, dict):
                    raise ValueError("must be a JSON object, e.g. {...}")
                binding["data"] = parsed
            except (json.JSONDecodeError, ValueError) as exc:
                data_errors.append(f"{event_id} ({exc})")
        icon = request.form.get(f"icon_{event_id}", "").strip()
        if icon:
            binding["icon"] = icon
        new_bindings[event_id] = binding

    config["bindings"] = new_bindings
    save_config(config)
    return redirect(url_for("index", saved=1, data_errors="; ".join(data_errors)))


@app.route("/bridge/start", methods=["POST"])
def route_bridge_start():
    start_bridge()
    return redirect(url_for("index"))


@app.route("/bridge/stop", methods=["POST"])
def route_bridge_stop():
    stop_bridge()
    return redirect(url_for("index"))


@app.route("/bridge/restart", methods=["POST"])
def route_bridge_restart():
    stop_bridge()
    start_bridge()
    return redirect(url_for("index"))


def main():
    print(f"Editing {CONFIG_PATH}")
    print("Open http://127.0.0.1:5757 -- Ctrl+C to stop.")
    print("(Ctrl+C here also stops the bridge subprocess if it's running.)")
    try:
        app.run(host="127.0.0.1", port=5757, debug=False, threaded=True)
    finally:
        stop_bridge()


if __name__ == "__main__":
    main()
