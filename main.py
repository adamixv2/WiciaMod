import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import os
import re
import aiohttp
import io
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

TOKEN = os.getenv("TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID", "0"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0"))
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.invites = True

bot = commands.Bot(command_prefix="!", intents=intents)
invite_cache = {}

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
        if unit == "s": total += timedelta(seconds=value)
        elif unit == "m": total += timedelta(minutes=value)
        elif unit == "h": total += timedelta(hours=value)
        elif unit == "d": total += timedelta(days=value)
        elif unit == "w": total += timedelta(weeks=value)
        elif unit == "mo": total += timedelta(days=value * 30)
    return total if total.total_seconds() > 0 else None

def format_time(td: timedelta) -> str:
    seconds = int(td.total_seconds())
    if seconds < 60: return f"{seconds}s"
    if seconds < 3600: return f"{seconds // 60}m"
    if seconds < 86400: return f"{seconds // 3600}h"
    if seconds < 604800: return f"{seconds // 86400}d"
    return f"{seconds // 604800}w"

async def init_db():
    async with aiosqlite.connect("moderation.db") as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, moderator_id INTEGER, reason TEXT, timestamp TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS invites (
            inviter_id INTEGER, invited_id INTEGER, code TEXT, timestamp TEXT)""")
        await db.commit()

def is_mod():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.guild_permissions.moderate_members or interaction.user.guild_permissions.administrator:
            return True
        if MOD_ROLE_ID and any(r.id == MOD_ROLE_ID for r in interaction.user.roles):
            return True
        await interaction.response.send_message("❌ Brak uprawnień.", ephemeral=True)
        return False
    return app_commands.check(predicate)

async def send_log(embed: discord.Embed):
    if LOG_CHANNEL_ID:
        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            await ch.send(embed=embed)

async def update_invite_cache(guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {i.code: i.uses for i in invites}
    except:
        invite_cache[guild.id] = {}

async def create_welcome_card(member: discord.Member) -> discord.File:
    async with aiohttp.ClientSession() as session:
        async with session.get(str(member.display_avatar.replace(size=128))) as resp:
            avatar_data = await resp.read()

    avatar = Image.open(io.BytesIO(avatar_data)).convert("RGBA").resize((120, 120))
    
    # Mniejsza kartka
    card = Image.new("RGBA", (500, 180), (25, 25, 30, 255))
    draw = ImageDraw.Draw(card)

    mask = Image.new("L", (120, 120), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 120, 120), fill=255)
    card.paste(avatar, (30, 30), mask)

    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    except:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((170, 40), "Siema", font=font_big, fill=(255, 255, 255))
    draw.text((170, 100), str(member.display_name), font=font_small, fill=(160, 160, 255))

    buffer = io.BytesIO()
    card.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="welcome.png")

@bot.event
async def on_ready():
    await init_db()
    for g in bot.guilds:
        await update_invite_cache(g)
    synced = await bot.tree.sync()
    print(f"Zalogowano: {bot.user} | Komend: {len(synced)}")

@bot.event
async def on_invite_create(invite):
    await update_invite_cache(invite.guild)

@bot.event
async def on_invite_delete(invite):
    await update_invite_cache(invite.guild)

@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    if guild.id not in invite_cache:
        await update_invite_cache(guild)
    try:
        invites = await guild.invites()
        for inv in invites:
            if inv.uses > invite_cache[guild.id].get(inv.code, 0):
                async with aiosqlite.connect("moderation.db") as db:
                    await db.execute(
                        "INSERT INTO invites (inviter_id, invited_id, code, timestamp) VALUES (?,?,?,?)",
                        (inv.inviter.id if inv.inviter else 0, member.id, inv.code, datetime.utcnow().isoformat())
                    )
                    await db.commit()
                break
        await update_invite_cache(guild)
    except:
        pass

    if WELCOME_CHANNEL_ID:
        channel = bot.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            try:
                file = await create_welcome_card(member)
                await channel.send(file=file)
                await channel.send(f"Witamy {member.mention} na **WiciaClient 20PLN**")
            except Exception as e:
                print(f"Błąd powitania: {e}")

    if VERIFIED_ROLE_ID:
        role = member.guild.get_role(VERIFIED_ROLE_ID)
        if role:
            try:
                await member.add_roles(role, reason="Automatyczna weryfikacja")
            except:
                pass

@bot.event
async def on_member_remove(member: discord.Member):
    if GOODBYE_CHANNEL_ID:
        channel = bot.get_channel(GOODBYE_CHANNEL_ID)
        if channel:
            await channel.send(f"**{member}** wyszedł z serwera")

# ====================== ERROR HANDLER (naprawia "Aplikacja nie reaguje") ======================

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        # Już obsłużone w is_mod()
        return
    if interaction.response.is_done():
        return

    if isinstance(error, app_commands.TransformerError):
        await interaction.response.send_message(
            "❌ **Nie znaleziono użytkownika.**\n"
            "Użyj **@wzmianki** (mention) albo podaj **ID** użytkownika.\n"
            "Przykład: `@kacper` lub `123456789012345678`",
            ephemeral=True
        )
        return

    # Inne błędy
    await interaction.response.send_message(
        f"❌ Wystąpił błąd: `{str(error)[:200]}`",
        ephemeral=True
    )
    print(f"[ERROR] {error}")

# ====================== BANY ======================

@bot.tree.command(name="ban", description="Zbanuj użytkownika (można z czasem)")
@app_commands.describe(
    uzytkownik="Kogo zbanować (@mention lub ID)",
    powod="Powód",
    czas="Opcjonalnie: 10m, 1h, 2h 30m, 1d, 1w (puste = permanentny)"
)
@is_mod()
async def ban(interaction: discord.Interaction, uzytkownik: discord.User, powod: str = "Brak powodu", czas: str = None):
    # Hierarchia ról (tylko jeśli jest na serwerze)
    member = interaction.guild.get_member(uzytkownik.id)
    if member is not None:
        if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)

    delta = parse_time(czas) if czas else None

    await interaction.guild.ban(uzytkownik, reason=f"{interaction.user} | {powod}" + (f" | {czas}" if czas else ""), delete_message_days=1)

    # DM do zbanowanego
    try:
        if delta:
            await uzytkownik.send(f"**Zostałeś zbanowany** na serwerze **{interaction.guild.name}**\nPowód: {powod}\nCzas: **{format_time(delta)}**")
        else:
            await uzytkownik.send(f"**Zostałeś zbanowany** na serwerze **{interaction.guild.name}**\nPowód: {powod}\nCzas: **permanentny**")
    except:
        pass

    embed = discord.Embed(title="🔨 Zbanowany", color=0xFF0000, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    if delta:
        embed.add_field(name="Czas", value=format_time(delta), inline=True)
    else:
        embed.add_field(name="Czas", value="Permanentny", inline=True)

    await interaction.response.send_message(embed=embed)
    await send_log(embed)

    # Automatyczne odbanowanie w tle
    if delta:
        async def unban_later():
            await asyncio.sleep(delta.total_seconds())
            try:
                await interaction.guild.unban(uzytkownik, reason="Koniec tempbana")
                await send_log(discord.Embed(title="✅ Tempban zakończony", description=f"{uzytkownik} został automatycznie odbanowany", color=0x00FF00))
            except:
                pass
        asyncio.create_task(unban_later())

@bot.tree.command(name="unban", description="Odbanuj użytkownika (podaj ID)")
@app_commands.describe(uzytkownik_id="ID użytkownika (prawy klik → Kopiuj ID)", powod="Powód")
@is_mod()
async def unban(interaction: discord.Interaction, uzytkownik_id: str, powod: str = "Brak powodu"):
    try:
        user = await bot.fetch_user(int(uzytkownik_id))
        await interaction.guild.unban(user, reason=f"{interaction.user} | {powod}")
        embed = discord.Embed(title="✅ Odbanowany", color=0x00FF00, timestamp=datetime.utcnow())
        embed.add_field(name="Użytkownik", value=f"{user} (`
