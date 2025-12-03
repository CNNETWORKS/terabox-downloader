```markdown
# Terabox Downloader Bot (Pyrogram) — Full feature set

This bot is a Terabox downloader that includes the features from
bibekkumar723129/Terabox-Downloader-Bot1 plus:

- Use a custom resolver API:
  https://teradl.tiiny.io/?key=RushVx&link={link} (default, can be changed)
- Force-subscribe (force users to join a channel before using the bot).
- Dumb channel: resolved files/videos are uploaded to your dumb channel and then copied to the user (so the file is stored in the channel and the user receives it).
- Telegram API ID & API HASH support (Pyrogram client initialization).
- Admin commands for configuring force-sub and dumb channel.
- Automatic streaming download from resolved link to disk (to handle large files safely).
- Upload progress updates and safe cleanup after upload.
- SQLite persistence for settings and basic stats.

Important notes
- The bot needs to be admin in the dumb channel (to post) and in the force-sub channel (to check membership).
- Uploading large files will use disk space temporarily. Make sure the host has enough free disk space.
- Telegram Bot API supports files up to 2 GB (depending on Telegram limits at runtime). Use caution with very large files.

Quick start
1. Copy `.env.example` to `.env` and fill the variables.
2. Install dependencies:
   pip install -r requirements.txt
3. Run:
   python bot.py

Admin commands (only allowed for ADMIN_ID):
- /set_force_sub <channel_username_or_id> — set the force-sub channel
- /remove_force_sub — remove the force-sub requirement
- /set_dumb_channel <channel_username_or_id> — set the dumb channel
- /remove_dumb_channel — remove the dumb channel
- /stats — show simple usage stats
- /help — usage help

How it works (high level)
1. User sends a terabox share link to the bot.
2. Bot calls the resolver API (configurable) to get a direct download link.
3. Bot streams the file to a temporary file on disk, showing download progress.
4. Bot uploads the file to the dumb channel (if configured) and then copies it to the user.
   - If no dumb channel configured, the bot uploads directly to the user.
5. The temporary file is removed after upload.

Files in this repo
- bot.py — main bot implementation
- requirements.txt — Python dependencies
- .env.example — example environment variables
- README.md — this file
- Dockerfile, docker-compose.yml, Procfile, systemd.service, .github/workflows/docker-build.yml

If you want:
- parallel processing for multiple links in a single message,
- further rate-limiting or per-user queueing,
- automatic cleanup policy for dumb channel (e.g., delete after X days),
I can add those next.
```