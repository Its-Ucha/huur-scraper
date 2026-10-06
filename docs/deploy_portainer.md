# Deploy on the homelab with Portainer

The scraper runs as one long-lived container: a Discord bot that scrapes every
`SCRAPE_INTERVAL_MINUTES` and answers slash commands. Portainer builds the image
straight from this GitHub repo.

## 1) Create the Discord bot

1. Go to https://discord.com/developers/applications → **New Application**.
2. **Bot** tab → **Reset Token** → copy it. This is `DISCORD_BOT_TOKEN`.
   No privileged intents are needed.
3. **OAuth2** tab → copy the **Client ID**, then open (replace `CLIENT_ID`):
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=93184`
   (93184 = View Channels + Send Messages + Embed Links + Read Message History + Manage Messages)
   and add the bot to your server. Read Message History is needed for the weekly cleanup
   of the alert channel; Manage Messages makes that cleanup a fast bulk delete (without it
   the bot deletes its own messages one at a time).

## 2) Collect the IDs

In Discord: **User Settings → Advanced → Developer Mode** on. Then right-click → **Copy ID**:

| Variable | Right-click on |
|---|---|
| `DISCORD_GUILD_ID` | the server icon |
| `DISCORD_ALERT_CHANNEL_ID` (optional) | only used on first start to seed your profile; also the ops fallback |
| `DISCORD_OPS_CHANNEL_ID` (recommended) | a channel for source errors/blocks and profile events; without it, ops messages go to `DISCORD_ALERT_CHANNEL_ID`, which becomes your profile's channel |
| `DISCORD_MENTION_USER_ID` (optional) | owner of the seeded profile (first ID) |
| `DISCORD_CONTROL_USER_IDS` | users allowed to run `/scrape`, `/pause`, `/resume` (comma-separated) |
| `DISCORD_CONTROL_ROLE_ID` (alternative) | a role allowed to run them |
| `HUUR_CATEGORY_NAME` (optional) | category for the per-user alert channels (default `Huur`) |

Make sure the bot's role can see and post in the ops channel and has **Manage Channels**
(it creates a private channel per profile).
If you invited the bot earlier with `permissions=19456`, also give its role **Read Message
History** (and optionally **Manage Messages**) in the alert channel, or the weekly cleanup fails
with a `[CLEAR_ERROR]` ops message.

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
   The compose file sets `pull_policy: build`, so each redeploy rebuilds the image.
6. **Environment variables**: on your own machine, copy `.env.example` to `.env` (it is
   gitignored) and fill in at least `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`,
   and `DISCORD_CONTROL_USER_IDS` (or `DISCORD_CONTROL_ROLE_ID`).
   In the stack editor, click **Load variables from .env file** and select that file.
   Portainer imports every line as a stack variable.
   - Write values without quotes. Portainer would keep the quotes as part of the value.
   - Delete or comment out optional lines you leave empty. Unset values use the defaults
     in `docker-compose.yml`.
   - Path and logging settings (`DATABASE_PATH`, `LOG_FILE_PATH`, …) are ignored here.
     The image sets them.
   - Your secrets stay out of GitHub and are stored only in Portainer. To change a value
     later, edit it under the stack's Environment variables and click **Update the stack**.
7. **Deploy the stack**.

## 5) Check it works

- Container logs (Portainer → Containers → huur-scraper → Logs) should show
  `Synced 7 slash commands`, `Logged in as …`, `Scheduler started`, then a cycle about 30 s later.
- In Discord: `/status`, `/profile`, `/listings`.
- If the container keeps restarting, the logs start with `Config error: …` naming the
  variable to fix.
- Upgrading from the single-profile version: the first start creates your profile from
  `.env` (`DISCORD_ALERT_CHANNEL_ID`, first `DISCORD_MENTION_USER_ID`, matching values) and
  marks every stored listing as sent, so nothing is reposted. The log shows `Seeded profile=…`.

## Commands

| Command | Who | What |
|---|---|---|
| `/profile` | everyone | create or edit your own search profile (panel with buttons) |
| `/listings [limit]` | everyone | current listings matching your profile |
| `/status` | everyone | paused/running, next run, last result per source |
| `/profiles` | control | everyone's profiles |
| `/scrape [sources]` | control | run a cycle now (also while paused) |
| `/pause`, `/resume` | control | stop/start scheduled scraping (survives redeploys) |

Every week (`LISTINGS_CLEAR_DAY`, default `monday`, at `LISTINGS_CLEAR_HOUR_UTC`, default
`4`) the bot deletes its own messages from every profile channel. Pinned messages and messages
from people are kept. If the bot was offline at that time it catches up after it starts.
Set `LISTINGS_CLEAR_DAY=off` to disable. On the first start after this feature is deployed
nothing is deleted; the first cleanup happens at the next scheduled slot.

Source error messages (`[SOURCE_ERROR]`, `[SOURCE_BLOCKED]`) are posted once when a
source starts failing, and `[SOURCE_RECOVERED]` once when it works again.

## CLI inside the container

Portainer → Containers → huur-scraper → **Console** (`/bin/sh`), or on the host:

```sh
docker exec huur-scraper python -m src.main --listings --limit 50
```

`python -m src.main --once` in the container only logs the matches per profile (a dry run);
it doesn't mark them as sent, so the bot still posts them. Prefer `/scrape` in Discord.

When someone deletes their profile, the bot renames the channel to `archived-…`, makes it
read-only and posts `[PROFILE_DELETED]` in the ops channel. Delete the channel by hand.

## Data

The SQLite DB lives in the named volume `huur-data` (`/app/data/huur_scraper.db`).
It survives redeploys. Removing the stack **with** volumes deletes it.
