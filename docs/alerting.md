# Alerting — Routing Alerts to Notification Channels

This document describes how Argus alerting works out of the box and how to
configure real notification receivers (Slack, email, PagerDuty) in
Alertmanager.

## How the alert pipeline works

```
rules/*.yml  →  Prometheus (evaluates rules)  →  Alertmanager (:9093)  →  receiver
                                                     ↑
Grafana-managed alerts ──────────────────────────────┘
(via provisioned contact point in configs/grafana/alerting.yml)
```

- Prometheus loads the rule files in `rules/agent-alerts.yml`,
  `rules/atlas-alerts.yml`, and `rules/recording-rules.yml`, evaluates them
  every 15s, and forwards firing alerts to Alertmanager at
  `alertmanager:9093` (wired via the `alerting:` block in
  `configs/prometheus.yml`).
- Alertmanager deduplicates, groups, and routes alerts to a receiver defined
  in `configs/alertmanager.yml`.
- Grafana-managed alerts also land in the same Alertmanager through the
  provisioned contact point (`configs/grafana/alerting.yml`).

## The default: a null receiver

The shipped `configs/alertmanager.yml` routes every alert to a receiver
named `null`, which has no notification integrations — alerts are silently
dropped. This is intentional as a safe default so a fresh stack never spams
external services, but it also means **you will not be notified of anything**
until you configure a real receiver.

## Configuring a real receiver

Edit `configs/alertmanager.yml`. Below are working examples for the three
most common channel types.

### Slack (fully worked example)

```yaml
route:
  receiver: 'slack-ops'
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: 'null'
  - name: 'slack-ops'
    slack_configs:
      # Read the webhook URL from a file instead of committing it.
      # Mount the file into the container (see "Secrets" below).
      - api_url_file: '/etc/alertmanager/secrets/slack_webhook'
        channel: '#argus-alerts'
        username: 'argus-alertmanager'
        send_resolved: true
```

Create the webhook URL file on the host (untracked — never commit it):

```bash
mkdir -p secrets
printf 'https://hooks.slack.com/services/YOUR/WEBHOOK/URL' > secrets/slack_webhook
chmod 600 secrets/slack_webhook
```

Then mount it into the container by adding to the `alertmanager` service's
`volumes:` list in `docker-compose.yml`:

```yaml
      - ./secrets/slack_webhook:/etc/alertmanager/secrets/slack_webhook:ro
```

### Email

Add an `email_configs` block to a receiver (SMTP credentials can also come
from files via `auth_password_file`):

```yaml
  - name: 'email-ops'
    email_configs:
      - to: 'oncall@example.com'
        from: 'alertmanager@example.com'
        smarthost: 'smtp.example.com:587'
        auth_username: 'alertmanager@example.com'
        auth_password_file: '/etc/alertmanager/secrets/smtp_password'
        send_resolved: true
```

### PagerDuty

```yaml
  - name: 'pagerduty-ops'
    pagerduty_configs:
      - routing_key_file: '/etc/alertmanager/secrets/pagerduty_routing_key'
        severity: 'critical'
        send_resolved: true
```

### Routing multiple receivers

To send different severities to different channels, add sub-routes:

```yaml
route:
  receiver: 'email-ops'          # default for everything else
  routes:
    - matchers: [severity="critical"]
      receiver: 'pagerduty-ops'
    - matchers: [severity="warning"]
      receiver: 'slack-ops'
```

## Applying changes

After editing `configs/alertmanager.yml`, validate it and hot-reload — no
container restart needed:

```bash
# Optional: validate syntax first (uses the pinned image)
docker run --rm -v ./configs/alertmanager.yml:/am.yml:ro \
  --entrypoint amtool prom/alertmanager:v0.32.1 check-config /am.yml

just reload-alertmanager
```

## Verifying delivery

Check that Alertmanager is healthy and your config loaded:

```bash
just test-alertmanager                       # /-/healthy + cluster status
curl -sf http://localhost:9093/api/v2/status | jq '.config.original'   # active config
```

Fire a synthetic alert end-to-end:

```bash
curl -XPOST http://localhost:9093/api/v2/alerts \
  -H 'Content-Type: application/json' \
  -d '[{"labels":{"alertname":"DocsSmokeTest","severity":"info"}}]'
```

You should see the test notification arrive on your configured channel within
`group_wait` (30s by default).

## Secrets

Webhook URLs, SMTP passwords, and PagerDuty routing keys are credentials.
Treat them like the rest of the stack's secrets:

- Keep them in untracked files (the repo already gitignores `.env`; put
  secret files under `secrets/` or similar and verify they are ignored).
- Use the `*_file` config variants shown above rather than inline values.
- Bind-mount them read-only into the container.
