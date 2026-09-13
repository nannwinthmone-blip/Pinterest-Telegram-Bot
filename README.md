# Pinterest → Telegram Bot

A production-ready Python bot that polls Pinterest-compatible RSS feeds, deduplicates media in SQLite, and publishes photos/videos to Telegram channels, groups, or forum topics.

> **Important:** This implementation uses RSS feeds by default. Pinterest API access depends on your Pinterest developer application and permissions. Do not scrape Pinterest pages or bypass access controls. Replace the feed reader only with an approved Pinterest API integration if you have one.

## Features

- Photo and video delivery to Telegram.
- SQLite-based duplicate prevention.
- Multiple feed-to-destination mappings.
- Telegram forum topic support through `message_thread_id`.
- Scheduled polling plus manual `/scan`.
- Admin allowlist for configuration commands.
- Docker and Docker Compose deployment.
- Secrets loaded from `.env`, never committed to Git.

## Quick start

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Add the bot as an administrator of the destination channel, or as a member with permission to post in the target group/topic.
3. Install Python 3.11+ and dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. Create `.env` from the template and fill in the token:

   ```bash
   cp .env.example .env
   # edit .env and set TELEGRAM_BOT_TOKEN
   ```

5. Start the bot:

   ```bash
   set -a && source .env && set +a
   python bot.py
   ```

## Configure a feed

Send these commands to the bot from an allowed Telegram user:

```text
/add <feed_url> <chat_id_or_@channel> [topic_id] [name]
/list
/scan
/remove <feed_url>
```

Example:

```text
/add https://example.com/pinterest-feed.xml @my_channel Art
/add https://example.com/another-feed.xml -1001234567890 42 Fashion topic
```

For forum topics, pass the numeric topic/thread ID as the third argument. The bot stores configuration in SQLite and scans feeds automatically according to `POLL_INTERVAL_SECONDS`.

## Docker deployment

```bash
cp .env.example .env
# edit .env
docker compose up -d --build
docker compose logs -f
```

For a persistent host, use a managed always-on service or a Linux VM with Docker and restart policy enabled. Keep `.env` private and back up the SQLite database.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | BotFather token |
| `ADMIN_USER_IDS` | No | empty | Comma-separated Telegram user IDs allowed to configure/scan |
| `DATABASE_PATH` | No | `bot.sqlite3` | SQLite database path |
| `POLL_INTERVAL_SECONDS` | No | `900` | Feed scan interval; minimum 60 seconds |
| `HTTP_TIMEOUT_SECONDS` | No | `30` | Feed request timeout |
| `LOG_LEVEL` | No | `INFO` | Python logging level |

If `ADMIN_USER_IDS` is empty, configuration commands are available to any user who can message the bot. Set it in production.

## မြန်မာလို အမြန်စတင်နည်း

1. Telegram မှာ `@BotFather` ကိုဖွင့်ပြီး bot အသစ်လုပ်ကာ token ကိုယူပါ။
2. Bot ကို target channel/group ထဲထည့်ပြီး post လုပ်ခွင့်ပေးပါ။
3. `.env.example` ကို `.env` အဖြစ်ကူးပြီး `TELEGRAM_BOT_TOKEN` ထည့်ပါ။
4. `pip install -r requirements.txt` ပြီး `python bot.py` ဖြင့် run ပါ။
5. Bot ထဲမှာ `/add feed_url chat_id` ဖြင့် feed ထည့်ပါ။ `/list`၊ `/scan`၊ `/remove feed_url` ကို အသုံးပြုနိုင်ပါတယ်။

## Safety and operations

- Never commit `.env`, bot tokens, or exported credentials.
- If a token is exposed, revoke it immediately with BotFather and issue a new one.
- The bot publishes external media URLs. Validate feed sources and use only content you are authorized to redistribute.
- Telegram may reject oversized or unsupported media. Failed items are logged; inspect logs and retry after fixing the source.
- RSS feed formats vary. The parser supports common RSS/Atom fields and media enclosures; an official API adapter can be added later.
