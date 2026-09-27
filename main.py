Skip to content
adamixv2
WiciaMod
Repository navigation
Code
Issues
Pull requests
Actions
Projects
Security and quality
Insights
Settings
Files
Go to file
t
T
main.py
requirements.txt
WiciaMod
/
main.py
in
main

Edit

Preview
Indent mode

Spaces
Indent size

4
Line wrap mode

No wrap
Editing main.py file contents
  1
  2
  3
  4
  5
  6
  7
  8
  9
 10
 11
 12
 13
 14
 15
 16
 17
 18
 19
 20
 21
 22
 23
 24
 25
 26
 27
 28
 29
 30
 31
 32
 33
 34
 35
 36
 37
 38
 39
 40
 41
 42
 43
 44
 45
 46
 47
 48
 49
 50
 51
 52
 53
 54
 55
 56
 57
 58
 59
 60
 61
 62
 63
 64
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import os
import re
import aiohttp
import io
import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

# ===================== KONFIG =====================
TOKEN = os.getenv("TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID", "0"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0"))
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0"))
TEMP_HUB_CHANNEL_ID = int(os.getenv("TEMP_HUB_CHANNEL_ID", "0"))
TEMP_CATEGORY_ID = int(os.getenv("TEMP_CATEGORY_ID", "0"))
INVITE_LOG_CHANNEL_ID = int(os.getenv("INVITE_LOG_CHANNEL_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
TICKET_STAFF_ROLE_ID = int(os.getenv("TICKET_STAFF_ROLE_ID", "0"))  # jeśli 0 → używa MOD_ROLE_ID

SERVER_NAME = "WiciaClient20PLN"
TEMP_ENABLED = True
TEMP_MAX_CHANNELS = 3
TEMP_DELETE_SECONDS = 30

# Kategorie ticketów (label, value, emoji, opis w select)
TICKET_CATEGORIES = [
    {"label": "Zakup", "value": "zakup", "emoji": "🛒", "desc": "Chcę kupić produkt/usługę"},
    {"label": "Pomoc", "value": "pomoc", "emoji": "🆘", "desc": "Potrzebuję pomocy technicznej"},
    {"label": "Współpraca", "value": "wspolpraca", "emoji": "🤝", "desc": "Mam ofertę współpracy"},
    {"label": "Inne", "value": "inne", "emoji": "❓", "desc": "Inny powód kontaktu"},
]

# ===================== BOT =====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.invites = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
invite_cache = {}
temp_channels = {}

# ===================== HELPERY =====================
def parse_time(time_str: str) -> Optional[timedelta]:
    if not time_str:
        return None
    time_str = time_str.lower().replace(" ", "")
    total = timedelta()
    matches = re.findall(r"(\d+)(s|m|h|d|w|mo)", time_str)
    if not matches:
        return None
    for value, unit in matches:
        value = int(value)
        if unit == "s":
            total += timedelta(seconds=value)
Use Control + Shift + m to toggle the tab key moving focus. Alternatively, use esc then tab to move to the next interactive element on the page.
 
