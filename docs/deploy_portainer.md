# Deploy on the homelab with Portainer

The scraper runs as one long-lived container: a Discord bot that scrapes every
`SCRAPE_INTERVAL_MINUTES` and answers slash commands. Portainer builds the image
straight from this GitHub repo.

## 1) Create the Discord bot

1. Go to https://discord.com/developers/applications → **New Application**.
2. **Bot** tab → **Reset Token** → copy it. This is `DISCORD_BOT_TOKEN`.
   No privileged intents are needed.
3. **OAuth2** tab → copy the **Client ID**, then open (replace `CLIENT_ID`):
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=19456`
   (19456 = View Channels + Send Messages + Embed Links) and add the bot to your server.

## 2) Collect the IDs

In Discord: **User Settings → Advanced → Developer Mode** on. Then right-click → **Copy ID**:

| Variable | Right-click on |
|---|---|
| `DISCORD_GUILD_ID` | the server icon |
| `DISCORD_ALERT_CHANNEL_ID` | the channel for listing alerts |
| `DISCORD_OPS_CHANNEL_ID` (optional) | a channel for source errors/blocks |
| `DISCORD_MENTION_USER_ID` (optional) | your own name, so hard matches ping you |
| `DISCORD_CONTROL_USER_IDS` | users allowed to run `/scrape`, `/pause`, `/resume` (comma-separated) |
| `DISCORD_CONTROL_ROLE_ID` (alternative) | a role allowed to run them |

Make sure the bot's role can see and post in the alert and ops channels.

## 3) GitHub access token (private repo)

GitHub → Settings → Developer settings → **Fine-grained personal access tokens** →
Generate. Repository access: **only this repo**. Permissions: **Contents: Read-only**.
Copy the token.

## 4) Create the Portainer stack

1. Portainer → your environment → **Stacks → Add stack**.
2. Name: `huur-scraper`. Build method: **Repository**.
3. Repository URL: `https://github.com/<you>/huur-scraper`, reference `refs/heads/main`,
   compose path `docker-compose.yml`.
4. **Authentication** on: username = your GitHub username, password = the token from step 3.
5. **GitOps updates** on (polling, for example every 5 minutes) so pushes to `main` redeploy.
   Enable **Re-pull image and redeploy** / force rebuild if your Portainer version offers it,
   so code changes are rebuilt.
6. **Environment variables**: add at least `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`,
   `DISCORD_ALERT_CHANNEL_ID`, and `DISCORD_CONTROL_USER_IDS` (or `DISCORD_CONTROL_ROLE_ID`).
   Optional: everything else in `.env.example` (`MAX_RENT_EUR`, `MIN_SIZE_M2`, `ALLOWED_CITIES`, …).
   Unset values use the defaults in `docker-compose.yml`.
7. **Deploy the stack**.

## 5) Check it works

- Container logs (Portainer → Containers → huur-scraper → Logs) should show
  `Synced 6 slash commands`, `Logged in as …`, `Scheduler started`, then a cycle about 30 s later.
- In Discord: `/status`, `/profile`, `/listings`.
- If the container keeps restarting, the logs start with `Config error: …` naming the
  variable to fix, or `Alert channel … not found`, meaning a wrong ID or the bot can't see the channel.

## Commands

| Command | Who | What |
|---|---|---|
| `/listings [limit]` | everyone | recent stored matches |
| `/status` | everyone | paused/running, next run, last result per source |
| `/profile` | everyone | current filters |
| `/scrape [sources]` | control | run a cycle now (also while paused) |
| `/pause`, `/resume` | control | stop/start scheduled scraping (survives redeploys) |

Source error messages (`[SOURCE_ERROR]`, `[SOURCE_BLOCKED]`) are posted once when a
source starts failing, and `[SOURCE_RECOVERED]` once when it works again.

## CLI inside the container

Portainer → Containers → huur-scraper → **Console** (`/bin/sh`), or on the host:

```sh
docker exec huur-scraper python -m src.main --listings --limit 50
docker exec huur-scraper python -m src.main --prune-non-matches
```

`python -m src.main --once` also works there. It logs alerts instead of posting them to Discord.

## Data

The SQLite DB lives in the named volume `huur-data` (`/app/data/huur_scraper.db`).
It survives redeploys. Removing the stack **with** volumes deletes it.
