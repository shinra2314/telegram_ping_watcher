# Unified Dashboard / Launcher

Pulse Desk is the **host shell** for your local stack. Instead of starting the
Telegram side and the Discord side in separate windows, one auto-started Pulse
Desk process brings everything up and gives a single control plane at
`http://127.0.0.1:8000` → tab **Сервисы**.

```
Boot/logon
  └─ Task Scheduler "PulseDashboard"
       └─ scripts/start_dashboard.ps1   (network wait → hidden uvicorn → health gate)
            └─ Pulse Desk host (FastAPI @8000)
                 ├─ watcher + TG bot + crypto + analytics + digest   (in-process jobs)
                 └─ ProcessSupervisor → spawns Discord bot (node) as a child
```

What lives where:

| Piece | File |
|---|---|
| Process supervisor (spawn / restart / logs / health) | `src/pulse_desk/process_supervisor.py` |
| Service manifest loader | `src/pulse_desk/service_registry.py` |
| HTTP control endpoints (`/api/services/*`, admin-only) | `routers/launcher.py` |
| Frontend tab | `static/js/app-services.js` + section in `static/index.html` |
| Boot script | `scripts/start_dashboard.ps1` |
| Auto-start install/remove | `scripts/install_autostart.ps1` / `uninstall_autostart.ps1` |

## Configure managed services

Copy the template and edit paths (the real file is git-ignored, like `.env`):

```powershell
Copy-Item config\services.example.json config\services.json
```

```jsonc
{
  "services": [{
    "name": "discord-bot",
    "label": "Discord Bot",
    "cmd": ["node", "src/index.js"],     // cmd[0] resolved on PATH; args support ${VAR}, ~
    "cwd": "D:/.../onix/discord-js-components-bot",
    "health": { "host": "127.0.0.1", "port": 8080 },  // TCP probe shown in UI
    "panel_url": "http://127.0.0.1:8080",             // iframed into the Сервисы tab
    "autostart": false,    // true = spawn automatically when Pulse Desk starts
    "autorestart": true    // restart on crash with capped exponential backoff
  }]
}
```

- `autostart: false` is the safe default — start it once from the UI, confirm it
  works, then flip to `true` so reboots bring the whole stack up.
- The embedded Discord panel + the `:8080` health probe need the bot's own
  `WEB_ENABLED=true` (its dashboard is localhost-only, no auth — keep it on `127.0.0.1`).
- Override the manifest location with `PULSE_SERVICES_FILE=/path/to.json`.

## Auto-start on logon (Windows)

```powershell
.\scripts\install_autostart.ps1          # register "PulseDashboard" logon task
Start-ScheduledTask -TaskName PulseDashboard   # test it now
.\scripts\uninstall_autostart.ps1        # remove
```

Task Scheduler (not the Startup folder) is used so the launch is hidden, gated on
the network, and restarts on failure. `start_dashboard.ps1` is idempotent — if a
host is already answering `/api/health` it just opens the dashboard window.

## API (all admin-only)

| Method | Path | Purpose |
|---|---|---|
| GET  | `/api/services` | list + status + health |
| POST | `/api/services/{name}/start\|stop\|restart` | control one service |
| POST | `/api/services/start-all\|stop-all` | control all |
| GET  | `/api/services/{name}/logs?limit=200` | rolling stdout tail |
| GET  | `/api/services/{name}/health` | one-shot TCP probe |

Crypto and analytics are **not** separate services — they are jobs inside Pulse
Desk (`market-monitor`, `analytics.py`, `daily-digest`). The launcher only
supervises genuinely separate runtimes.
