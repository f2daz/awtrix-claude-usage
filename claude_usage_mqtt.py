#!/usr/bin/env python3
"""Claude Code status line that publishes your plan limits to MQTT.

Claude Code pipes a JSON document into the status line command. For claude.ai
subscribers it contains rate_limits.five_hour / rate_limits.seven_day with
used_percentage and resets_at. This script prints a one-line status line and
publishes the values as a retained JSON message, for the AWTRIX NG script
"Claude Usage" (or Home Assistant, Node-RED, ...).

Publishes at most once a minute, at once on a change, otherwise every 5 minutes.
The MQTT send runs in the background so the status line never waits for it.

Setup:
  1. pip/brew: needs `mosquitto_pub` on the PATH (brew install mosquitto,
     apt install mosquitto-clients).
  2. Copy this file to ~/.claude/claude_usage_mqtt.py and make it executable.
  3. Create ~/.config/claude-usage-mqtt.env (chmod 600), see README.md.
  4. In ~/.claude/settings.json:
       "statusLine": {"type": "command", "command": "~/.claude/claude_usage_mqtt.py"}

Payload on STATE_TOPIC (default claude-code/usage):
  {"five_hour_pct": 11.0, "five_hour_resets_at": 1791453000,
   "seven_day_pct": 41.0, "seven_day_resets_at": 1791756000, "updated_at": 1791441318}
"""
import json
import os
import shutil
import subprocess
import sys
import time

CONF = os.path.expanduser(os.environ.get("CLAUDE_USAGE_MQTT_CONF", "~/.config/claude-usage-mqtt.env"))
STATE = os.path.expanduser("~/.cache/claude-usage-mqtt/last.json")
MIN_GAP = 60
KEEPALIVE = 300


def load_conf():
    conf = {}
    try:
        with open(CONF) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip()
    except OSError:
        pass
    return conf


def window(rl, key):
    w = rl.get(key) or {}
    pct = w.get("used_percentage")
    return (None, None) if pct is None else (float(pct), w.get("resets_at"))


def publish(conf, payload):
    host = conf.get("MQTT_HOST")
    pub = shutil.which("mosquitto_pub")
    if not host or not pub:
        return
    base = [pub, "-h", host, "-p", conf.get("MQTT_PORT", "1883"), "-q", "1"]
    if conf.get("MQTT_USER"):
        base += ["-u", conf["MQTT_USER"], "-P", conf.get("MQTT_PASS", "")]
    topic = conf.get("STATE_TOPIC", "claude-code/usage")
    msgs = [(topic, payload, True)]
    if conf.get("HA_DISCOVERY", "0") == "1":
        ha = conf.get("HA_PREFIX", "homeassistant")
        device = {"identifiers": ["claude_code_usage"], "name": "Claude Code usage"}
        for key, name in (("five_hour", "5-hour limit"), ("seven_day", "Weekly limit")):
            msgs.append((f"{ha}/sensor/claude_code_{key}/config", {
                "name": name, "unique_id": f"claude_code_{key}", "state_topic": topic,
                "value_template": "{{ value_json.%s_pct }}" % key, "unit_of_measurement": "%",
                "state_class": "measurement", "icon": "mdi:gauge", "device": device}, True))
            msgs.append((f"{ha}/sensor/claude_code_{key}_reset/config", {
                "name": f"{name} reset", "unique_id": f"claude_code_{key}_reset", "state_topic": topic,
                "value_template": "{{ (value_json.%s_resets_at | int) | timestamp_custom('%%Y-%%m-%%dT%%H:%%M:%%S+00:00', false) }}" % key,
                "device_class": "timestamp", "device": device}, True))
    for t, body, retain in msgs:
        cmd = base + ["-t", t, "-m", json.dumps(body, separators=(",", ":"))] + (["-r"] if retain else [])
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        print("Claude")
        return
    model = (data.get("model") or {}).get("display_name", "Claude")
    rl = data.get("rate_limits") or {}
    five, five_reset = window(rl, "five_hour")
    seven, seven_reset = window(rl, "seven_day")
    if five is None and seven is None:
        print(f"[{model}]")
        return
    parts = [f"{k}: {int(round(v))}%" for k, v in (("5h", five), ("7d", seven)) if v is not None]
    print(f"[{model}] " + " | ".join(parts))

    now = int(time.time())
    payload = {"five_hour_pct": five, "five_hour_resets_at": five_reset,
               "seven_day_pct": seven, "seven_day_resets_at": seven_reset, "updated_at": now}
    try:
        with open(STATE) as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = {}
    changed = (last.get("five_hour_pct"), last.get("seven_day_pct")) != (five, seven)
    age = now - last.get("updated_at", 0)
    if age < MIN_GAP or (not changed and age < KEEPALIVE):
        return
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(payload, f)
    if os.fork() == 0:              # send in the background; Claude Code cancels slow status lines
        os.setsid()
        try:
            publish(load_conf(), payload)
        finally:
            os._exit(0)


if __name__ == "__main__":
    main()
