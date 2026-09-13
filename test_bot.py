import tempfile
import unittest
from pathlib import Path

from bot import FeedConfig, Store, parse_feed


RSS = '''<?xml version="1.0"?><rss><channel><item><title>Sunset</title><link>https://pin.example/1</link><enclosure url="https://cdn.example/sunset.jpg" type="image/jpeg"/><pubDate>Sat, 13 Sep 2026 12:00:00 GMT</pubDate></item><item><title>Clip</title><link>https://pin.example/2</link><media:content xmlns:media="http://search.yahoo.com/mrss/" url="https://cdn.example/clip.mp4" type="video/mp4"/></item></channel></rss>'''


class BotTests(unittest.TestCase):
    def test_parse_rss_photo_and_video(self):
        items = parse_feed(RSS, "https://feed.example/rss")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].media_type, "photo")
        self.assertEqual(items[1].media_type, "video")
        self.assertEqual(items[0].title, "Sunset")

    def test_store_deduplicates_items(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "bot.sqlite3"))
            store.add_feed(FeedConfig("https://feed.example/rss", "@channel"))
            self.assertTrue(store.mark_if_new("abc", "https://feed.example/rss"))
            self.assertFalse(store.mark_if_new("abc", "https://feed.example/rss"))
            self.assertEqual(len(store.list_feeds()), 1)


if __name__ == "__main__":
    unittest.main()
