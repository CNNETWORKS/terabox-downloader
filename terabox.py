#!/usr/bin/env python3
"""
Compatibility entrypoint named like some forks expect (terabox.py).

This simply imports the bot module (bot.py) and starts the Pyrogram client.
If you prefer to run `python bot.py` directly, you can continue to do so.
"""
import logging

# Import the bot app object from bot.py and run it.
# bot.py defines `app` (Pyrogram Client) at module level.
from bot import app

if __name__ == "__main__":
    logging.info("Starting terabox entrypoint...")
    app.run()
