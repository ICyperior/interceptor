# Webhooks & Alert Notifications

INTERCEPT has a built-in alert engine that fires webhooks when decoded events match configurable rules. Forward pager messages (or events from any other mode) to Discord, Slack, n8n, Home Assistant, or any HTTP endpoint.

## How it works

1. Configure **alert rules** in the Alerts UI — each rule defines which mode and event type to watch, optional match criteria, and a severity level.
2. When an incoming event matches a rule, INTERCEPT stores it in the alert log and POSTs a JSON payload to your configured webhook URL.
3. All modes are supported: pager, sensor, ADS-B, AIS, ACARS, WiFi, Bluetooth, and more.

## Configuration

Set these environment variables in your `.env` file or `docker-compose.yml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ALERT_WEBHOOK_URL` | _(empty)_ | URL to POST alert payloads to |
| `ALERT_WEBHOOK_SECRET` | _(empty)_ | Optional token sent as `X-Alert-Token` header |
| `ALERT_WEBHOOK_TIMEOUT` | `5` | HTTP timeout in seconds |

```env
ALERT_WEBHOOK_URL=https://your-endpoint.example.com/intercept-alerts
ALERT_WEBHOOK_SECRET=mysecrettoken
```

## Creating alert rules

1. Open the **Alerts** panel in INTERCEPT
2. Click **New Rule**
3. Configure:
   - **Mode**: `pager` (or any other mode, or leave blank to match all)
   - **Event type**: `message` for pager decodes (or blank to match all event types)
   - **Match criteria**: leave empty to forward everything, or add filters (e.g. capcode equals `1234567`, or message contains `FIRE`)
   - **Severity**: `low`, `medium`, or `high`
4. Save and enable the rule

## Webhook payload format

```json
{
  "id": 42,
  "rule_id": 1,
  "mode": "pager",
  "event_type": "message",
  "severity": "medium",
  "title": "My Pager Rule",
  "message": "message | 1234567",
  "created_at": "2026-04-13T10:00:00+00:00",
  "payload": {
    "mode": "pager",
    "event_type": "message",
    "event": {
      "capcode": "1234567",
      "message": "UNIT 4 RESPOND TO 123 MAIN ST",
      "type": "POCSAG1200"
    },
    "rule": { "id": 1, "name": "My Pager Rule" }
  }
}
```

## Sending to Discord

Discord webhooks expect a different JSON format, so you need a relay between INTERCEPT and Discord.

**Option A — No-code relay (recommended)**

Use [n8n](https://n8n.io), [Make](https://make.com), or [Pipedream](https://pipedream.com) to receive INTERCEPT's webhook and forward it to Discord with a custom message template. Point `ALERT_WEBHOOK_URL` at your workflow's ingest URL.

**Option B — Self-hosted Python relay**

Save as `discord_relay.py` and run it alongside INTERCEPT:

```python
from flask import Flask, request
import urllib.request, json

app = Flask(__name__)

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"

@app.post("/relay")
def relay():
    data = request.get_json(force=True)
    mode = data.get("mode", "unknown").upper()
    title = data.get("title", "Alert")
    message = data.get("message", "")
    event = data.get("payload", {}).get("event", {})

    lines = [f"**[{mode}]** {title}", message]
    if event.get("capcode"):
        lines.append(f"Capcode: `{event['capcode']}`")
    if event.get("type"):
        lines.append(f"Protocol: {event['type']}")

    payload = json.dumps({"content": "\n".join(lines)}).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)
    return "", 204

app.run(host="0.0.0.0", port=5051)
```

Set `ALERT_WEBHOOK_URL=http://localhost:5051/relay` and run: `python3 discord_relay.py`

## Filtering specific capcodes

To only forward decodes from a specific capcode, set the rule's **Match criteria**:

| Field | Operator | Value |
|-------|----------|-------|
| `capcode` | equals | `1234567` |

Multiple rules can coexist — e.g. one rule for all pager traffic and a second rule for emergency capcodes with `high` severity routed to a separate channel.
