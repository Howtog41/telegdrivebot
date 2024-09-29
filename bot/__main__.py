import os
import logging
from pyrogram import Client
from pyrogram import enums
from config import config
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
LOGGER = logging.getLogger(__name__)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

DOWNLOAD_DIRECTORY = "/path/to/download"  # Replace with your directory path

if __name__ == "__main__":
    if not os.path.isdir(DOWNLOAD_DIRECTORY):
        os.makedirs(DOWNLOAD_DIRECTORY)
    plugins = dict(root="bot/plugins")
    app = Client(
        "G-DriveBot",
        bot_token=config.BOT_TOKEN,
        api_id=config.APP_ID,
        api_hash=config.API_HASH,
        plugins=plugins,
        parse_mode=enums.ParseMode.MARKDOWN,
        workdir=DOWNLOAD_DIRECTORY,
    )
    LOGGER.info("Starting Bot !")
    app.run()
    LOGGER.info("Bot Stopped !")
