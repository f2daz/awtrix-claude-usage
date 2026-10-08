#!/usr/bin/env python3
"""Claude Code hook: tells the clock when Claude is done or waits for you.

Register it for the hook events UserPromptSubmit, Stop and Notification. It publishes a small,
non-retained JSON message to EVENT_TOPIC (default claude-code/event); the AWTRIX script
"Claude Usage" shows it as a notification.

  UserPromptSubmit  remembers when the turn started (nothing is sent)
  Stop              {"event": "done"}  only if the turn took at least MIN_SECONDS
  Notification      {"event": "input"} when Claude asks for a permission or an answer

Settings come from the same file as the status line sender (~/.config/claude-usage-mqtt.env,
or CLAUDE_USAGE_ENV): MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS, EVENT_TOPIC, MIN_SECONDS.
The hook never blocks Claude Code: it returns at once and sends from a background process.
"""
import json
import os
import shutil
import subprocess
import sys
import time

CONF = os.path.expanduser(os.environ.get("CLAUDE_USAGE_ENV", "~/.config/claude-usage-mqtt.env"))
STATE = os.path.expanduser("~/.cache/claude-usage-mqtt/turns")
# Notification types that mean "Claude waits for you"; idle_prompt is left out (the "done"
# message already covered it), as are auth and quota messages.
ASKING = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}


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


def started_file(session):
    safe = "".join(c for c in session if c.isalnum() or c in "-_") or "unknown"
    return os.path.join(STATE, safe)


def publish(conf, body):
    host = conf.get("MQTT_HOST")
    if not host:
        return
    pub = shutil.which("mosquitto_pub") or "/opt/homebrew/bin/mosquitto_pub"
    cmd = [pub, "-h", host, "-p", conf.get("MQTT_PORT", "1883"), "-q", "1",
           "-t", conf.get("EVENT_TOPIC", "claude-code/event"),
           "-m", json.dumps(body, separators=(",", ":"))]
    if conf.get("MQTT_USER"):
        cmd += ["-u", conf["MQTT_USER"], "-P", conf.get("MQTT_PASS", "")]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    event = data.get("hook_event_name", "")
    if data.get("agent_id"):            # fired inside a subagent
        return
    session = str(data.get("session_id", ""))
    now = time.time()

    if event == "UserPromptSubmit":
        os.makedirs(STATE, exist_ok=True)
        with open(started_file(session), "w") as f:
            f.write(str(now))
        return

    conf = load_conf()
    project = os.path.basename(os.path.normpath(data.get("cwd") or os.getcwd()))
    body = {"project": project, "at": int(now)}

    if event == "Stop":
        if data.get("stop_hook_active"):
            return
        try:
            with open(started_file(session)) as f:
                secs = now - float(f.read().strip())
        except (OSError, ValueError):
            return
        if secs < float(conf.get("MIN_SECONDS", "60")):
            return
        body.update(event="done", secs=int(secs))
    elif event == "Notification":
        kind = data.get("notification_type", "")
        if kind not in ASKING:
            return
        body.update(event="input", kind=kind)
    else:
        return

    # Send in the background so Claude Code never waits for the broker
    if os.fork() == 0:
        os.setsid()
        try:
            publish(conf, body)
        finally:
            os._exit(0)


if __name__ == "__main__":
    main()
