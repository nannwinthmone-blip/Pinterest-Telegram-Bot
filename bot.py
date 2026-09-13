"""Pinterest RSS to Telegram bot.

The bot polls configured Pinterest-compatible RSS feeds, deduplicates items in SQLite,
and publishes images/videos into Telegram chats or forum topics.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from telegram import Bot, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes

LOG = logging.getLogger("pinterest_telegram_bot")


@dataclass(frozen=True)
class FeedConfig:
    url: str
    destination: str
    topic_id: int | None = None
    name: str | None = None


@dataclass(frozen=True)
class MediaItem:
    uid: str
    title: str
    link: str
    media_url: str
    media_type: str
    published_at: str | None


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feeds (
                    url TEXT PRIMARY KEY,
                    destination TEXT NOT NULL,
                    topic_id INTEGER,
                    name TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sent_items (
                    uid TEXT PRIMARY KEY,
                    feed_url TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def add_feed(self, feed: FeedConfig) -> None:
        with self.lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO feeds(url,destination,topic_id,name,created_at) VALUES(?,?,?,?,?)",
                (feed.url, feed.destination, feed.topic_id, feed.name, utc_now()),
            )

    def remove_feed(self, url: str) -> bool:
        with self.lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM feeds WHERE url = ?", (url,))
            return cur.rowcount > 0

    def list_feeds(self) -> list[FeedConfig]:
        with self.lock, self._connect() as conn:
            rows = conn.execute("SELECT url,destination,topic_id,name FROM feeds ORDER BY created_at").fetchall()
        return [FeedConfig(row["url"], row["destination"], row["topic_id"], row["name"]) for row in rows]

    def mark_if_new(self, uid: str, feed_url: str) -> bool:
        with self.lock, self._connect() as conn:
            try:
                conn.execute("INSERT INTO sent_items(uid,feed_url,sent_at) VALUES(?,?,?)", (uid, feed_url, utc_now()))
                return True
            except sqlite3.IntegrityError:
                return False


class FeedReader:
    def __init__(self, timeout: float = 30) -> None:
        self.timeout = timeout

    async def fetch(self, feed: FeedConfig) -> list[MediaItem]:
        headers = {"User-Agent": "PinterestTelegramBot/1.0 (+https://github.com/nannwinthmone-blip/Pinterest-Telegram-Bot)"}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(feed.url)
            response.raise_for_status()
        return parse_feed(response.text, feed.url)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_feed(payload: str, feed_url: str) -> list[MediaItem]:
    root = ElementTree.fromstring(payload)
    items: list[MediaItem] = []
    for node in root.iter():
        if local_name(node.tag) not in {"item", "entry"}:
            continue
        values = {local_name(child.tag): (child.text or "").strip() for child in node.iter() if child is not node and child.text}
        link = values.get("link", "")
        if not link:
            for child in node.iter():
                if local_name(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        media_url, media_type = extract_media(node, values, link)
        if not media_url:
            continue
        title = html.unescape(values.get("title") or "Pinterest item")
        published = values.get("pubDate") or values.get("published") or values.get("updated")
        uid = hashlib.sha256(f"{feed_url}|{link}|{media_url}".encode()).hexdigest()
        items.append(MediaItem(uid, title, link or feed_url, media_url, media_type, published))
    return items


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def extract_media(node: ElementTree.Element, values: dict[str, str], link: str) -> tuple[str, str]:
    for child in node.iter():
        tag = local_name(child.tag)
        url = child.attrib.get("url") or child.attrib.get("href") or child.text
        if not url or not url.startswith(("http://", "https://")):
            continue
        if tag in {"content", "enclosure", "thumbnail", "image", "media:content"} or "image" in tag or "video" in tag:
            mime = child.attrib.get("type", "")
            return url.strip(), media_type_for(url, mime)
    for key in ("media_url", "image", "thumbnail", "content"):
        if values.get(key):
            return values[key], media_type_for(values[key], "")
    return "", "unknown"


def media_type_for(url: str, mime: str) -> str:
    value = f"{mime} {url}".lower()
    return "video" if any(x in value for x in ("video", ".mp4", ".mov", ".webm")) else "photo"


def parse_destination(value: str) -> str:
    if not value.startswith(("@", "-", "-100")):
        raise ValueError("destination must be a Telegram @username or numeric chat id")
    return value


def is_admin(update: Update) -> bool:
    raw = os.getenv("ADMIN_USER_IDS", "").strip()
    if not raw:
        return True
    user = update.effective_user
    return bool(user and str(user.id) in {part.strip() for part in raw.split(",")})


async def send_item(bot: Bot, feed: FeedConfig, item: MediaItem) -> None:
    kwargs = {"chat_id": feed.destination, "caption": f"{item.title}\n{item.link}"[:1024]}
    if feed.topic_id is not None:
        kwargs["message_thread_id"] = feed.topic_id
    if item.media_type == "video":
        await bot.send_chat_action(feed.destination, ChatAction.UPLOAD_VIDEO, message_thread_id=feed.topic_id)
        await bot.send_video(video=item.media_url, **kwargs)
    else:
        await bot.send_chat_action(feed.destination, ChatAction.UPLOAD_PHOTO, message_thread_id=feed.topic_id)
        await bot.send_photo(photo=item.media_url, **kwargs)


async def scan_feeds(application: Application) -> tuple[int, int]:
    store: Store = application.bot_data["store"]
    reader: FeedReader = application.bot_data["reader"]
    sent = failed = 0
    for feed in store.list_feeds():
        try:
            items = await reader.fetch(feed)
            for item in items:
                if not store.mark_if_new(item.uid, feed.url):
                    continue
                try:
                    await send_item(application.bot, feed, item)
                    sent += 1
                except Exception:
                    failed += 1
                    LOG.exception("Unable to send %s from %s", item.uid, feed.url)
        except Exception:
            failed += 1
            LOG.exception("Unable to read feed %s", feed.url)
    return sent, failed


async def command_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Pinterest → Telegram Bot\n\n"
        "/add <feed_url> <chat_id_or_@channel> [topic_id] [name]\n"
        "/remove <feed_url>\n/list\n/scan\n/help"
    )


async def command_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await command_start(update, context)


async def command_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await update.message.reply_text("You are not authorized to configure this bot.")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /add <feed_url> <chat_id_or_@channel> [topic_id] [name]")
    url, destination = context.args[:2]
    topic_id = int(context.args[2]) if len(context.args) >= 3 and context.args[2].isdigit() else None
    name_index = 3 if topic_id is not None else 2
    name = " ".join(context.args[name_index:]) or None
    try:
        parse_destination(destination)
        store: Store = context.application.bot_data["store"]
        store.add_feed(FeedConfig(url, destination, topic_id, name))
        await update.message.reply_text(f"Feed added: {name or url}")
    except Exception as exc:
        await update.message.reply_text(f"Could not add feed: {exc}")


async def command_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await update.message.reply_text("You are not authorized to configure this bot.")
    if not context.args:
        return await update.message.reply_text("Usage: /remove <feed_url>")
    removed = context.application.bot_data["store"].remove_feed(context.args[0])
    await update.message.reply_text("Feed removed." if removed else "Feed not found.")


async def command_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    feeds = context.application.bot_data["store"].list_feeds()
    if not feeds:
        return await update.message.reply_text("No feeds configured.")
    lines = [f"{i}. {feed.name or feed.url} → {feed.destination}" + (f" topic {feed.topic_id}" if feed.topic_id else "") for i, feed in enumerate(feeds, 1)]
    await update.message.reply_text("\n".join(lines))


async def command_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await update.message.reply_text("You are not authorized to scan feeds.")
    sent, failed = await scan_feeds(context.application)
    await update.message.reply_text(f"Scan complete. Sent: {sent}; failed: {failed}.")


async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE) -> None:
    sent, failed = await scan_feeds(context.application)
    LOG.info("Scheduled scan complete: sent=%s failed=%s", sent, failed)


def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    app = Application.builder().token(token).build()
    app.bot_data["store"] = Store(os.getenv("DATABASE_PATH", "bot.sqlite3"))
    app.bot_data["reader"] = FeedReader(float(os.getenv("HTTP_TIMEOUT_SECONDS", "30")))
    for command, handler in {
        "start": command_start,
        "help": command_help,
        "add": command_add,
        "remove": command_remove,
        "list": command_list,
        "scan": command_scan,
    }.items():
        app.add_handler(CommandHandler(command, handler))
    interval = max(60, int(os.getenv("POLL_INTERVAL_SECONDS", "900")))
    app.job_queue.run_repeating(scheduled_scan, interval=interval, first=10)
    return app


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
