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
from datetime import datetime, timedelta
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

SERVER_NAME = "WiciaClient20PLN"
TEMP_ENABLED = True
TEMP_MAX_CHANNELS = 3
TEMP_DELETE_SECONDS = 30

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
        elif unit == "m":
            total += timedelta(minutes=value)
        elif unit == "h":
            total += timedelta(hours=value)
        elif unit == "d":
            total += timedelta(days=value)
        elif unit == "w":
            total += timedelta(weeks=value)
        elif unit == "mo":
            total += timedelta(days=value * 30)
    return total if total.total_seconds() > 0 else None


def format_time(td: timedelta) -> str:
    seconds = int(td.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    if seconds < 604800:
        return f"{seconds // 86400}d"
    return f"{seconds // 604800}w"


def _plural(n: int, forms: tuple) -> str:
    n = abs(n)
    if n == 1:
        return f"{n} {forms[0]}"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return f"{n} {forms[1]}"
    return f"{n} {forms[2]}"


def format_time_human(td: timedelta) -> str:
    seconds = int(td.total_seconds())
    if seconds < 60:
        return _plural(seconds, ("sekundę", "sekundy", "sekund"))
    if seconds < 3600:
        return _plural(seconds // 60, ("minutę", "minuty", "minut"))
    if seconds < 86400:
        return _plural(seconds // 3600, ("godzinę", "godziny", "godzin"))
    if seconds < 604800:
        return _plural(seconds // 86400, ("dzień", "dni", "dni"))
    return _plural(seconds // 604800, ("tydzień", "tygodnie", "tygodni"))


async def init_db():
    async with aiosqlite.connect("moderation.db") as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, moderator_id INTEGER, reason TEXT, timestamp TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS invites (
            inviter_id INTEGER, invited_id INTEGER, code TEXT, timestamp TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS economy (
            user_id INTEGER PRIMARY KEY,
            credits INTEGER DEFAULT 0,
            last_daily TEXT,
            rep INTEGER DEFAULT 0,
            last_rep TEXT,
            title TEXT DEFAULT '',
            text_xp INTEGER DEFAULT 0,
            voice_xp INTEGER DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS points (
            user_id INTEGER PRIMARY KEY, points INTEGER DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS muted_text (
            user_id INTEGER PRIMARY KEY, until TEXT)""")
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


async def send_log(embed: discord.Embed, *, skip_channel_id: int = None):
    if not LOG_CHANNEL_ID:
        return
    if skip_channel_id and skip_channel_id == LOG_CHANNEL_ID:
        return
    ch = bot.get_channel(LOG_CHANNEL_ID)
    if ch:
        try:
            await ch.send(embed=embed)
        except Exception:
            pass


async def notify_user(user, guild=None, *, action, color, reason="Brak powodu", duration=None, extra=None):
    reason = (reason or "Brak powodu").strip() or "Brak powodu"
    server = SERVER_NAME
    lines = {
        "muted": f"Zostałeś wyciszony na serwerze **{server}**" + (f" na **{duration}**" if duration else "") + f". | {reason}",
        "unmuted": f"Wyciszenie na serwerze **{server}** zostało zdjęte.",
        "banned": f"Zostałeś zbanowany na serwerze **{server}**" + (f" na **{duration}**" if duration else "") + f". | {reason}",
        "unbanned": f"Zostałeś odbanowany na serwerze **{server}**. | {reason}",
        "kicked": f"Zostałeś wyrzucony z serwera **{server}**. | {reason}",
        "softbanned": f"Zostałeś softbanowany na serwerze **{server}**. | {reason}",
        "warned": f"Otrzymałeś ostrzeżenie na serwerze **{server}**. | {reason}",
    }
    titles = {
        "muted": "🔇 Zostałeś wyciszony",
        "unmuted": "🔊 Wyciszenie zdjęte",
        "banned": "🔨 Zostałeś zbanowany",
        "unbanned": "✅ Zostałeś odbanowany",
        "kicked": "👢 Zostałeś wyrzucony",
        "softbanned": "💨 Softban",
        "warned": "⚠️ Otrzymałeś ostrzeżenie",
    }
    line = lines.get(action, f"Powiadomienie z serwera **{server}**. | {reason}")
    plain = line.replace("**", "")
    embed = discord.Embed(title=titles.get(action, "Powiadomienie"), description=line, color=color, timestamp=datetime.utcnow())
    if guild and guild.icon:
        embed.set_author(name=server, icon_url=guild.icon.url)
    else:
        embed.set_author(name=server)
    embed.add_field(name="Serwer", value=server, inline=True)
    if duration:
        embed.add_field(name="Czas", value=duration, inline=True)
    if action != "unmuted":
        embed.add_field(name="Powód", value=reason, inline=False)
    if extra:
        embed.add_field(name="Info", value=extra, inline=False)
    embed.set_footer(text="Jeśli uważasz, że to pomyłka — napisz do administracji serwera.")
    try:
        await user.send(content=plain, embed=embed)
        return True
    except Exception:
        return False


async def update_invite_cache(guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {i.code: i.uses for i in invites}
    except Exception:
        invite_cache[guild.id] = {}


async def create_welcome_card(member: discord.Member) -> discord.File:
    async with aiohttp.ClientSession() as session:
        async with session.get(str(member.display_avatar.replace(size=256))) as resp:
            avatar_data = await resp.read()

    W, H = 720, 200
    card = Image.new("RGBA", (W, H), (16, 16, 20, 255))
    draw = ImageDraw.Draw(card)

    # Tło w stylu ProBot – ciemne + geometryczne kształty
    # bazowy gradient
    for y in range(H):
        c = 16 + int(6 * (y / H))
        draw.line([(0, y), (W, y)], fill=(c, c, c + 3, 255))

    # duże rozmyte trójkąty / bloby (jak tło ProBota)
    shapes = [
        # lewy górny
        [(0, 0), (180, 0), (90, 140)],
        # prawy górny
        [(W - 220, 0), (W, 0), (W, 160), (W - 100, 80)],
        # dolny środek
        [(200, H), (420, H), (350, 80)],
        # prawy dół
        [(W - 180, H), (W, H), (W, 100)],
    ]
    for pts in shapes:
        draw.polygon(pts, fill=(28, 28, 36, 90))

    # dodatkowe elipsy
    draw.ellipse([-40, -60, 160, 140], fill=(35, 35, 48, 70))
    draw.ellipse([W - 200, 40, W + 40, H + 40], fill=(32, 32, 42, 80))
    draw.ellipse([300, -50, 520, 100], fill=(25, 25, 35, 60))

    # Avatar – zaokrąglony kwadrat
    avatar_size = 120
    avatar = Image.open(io.BytesIO(avatar_data)).convert("RGBA").resize((avatar_size, avatar_size))
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, avatar_size - 1, avatar_size - 1], radius=16, fill=255)

    # cień
    shadow = Image.new("RGBA", (avatar_size + 6, avatar_size + 6), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([3, 3, avatar_size + 2, avatar_size + 2], radius=16, fill=(0, 0, 0, 120))
    ax, ay = 28, (H - avatar_size) // 2
    card.paste(shadow, (ax + 2, ay + 3), shadow)
    card.paste(avatar, (ax, ay), mask)

    # Ciemny box tekstowy
    bx1, by1 = 175, 35
    bx2, by2 = W - 30, H - 35
    draw.rounded_rectangle([bx1, by1, bx2, by2], radius=12, fill=(26, 26, 32, 235))
    draw.rounded_rectangle([bx1, by1, bx2, by2], radius=12, outline=(50, 50, 60, 180), width=1)

    # Fonty – DUŻE i pogrubione
    try:
        font_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 30)
    except Exception:
        font_name = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    name = str(member.display_name)[:18]
    draw.text((bx1 + 24, by1 + 22), name, font=font_name, fill=(255, 255, 255, 255))
    draw.text((bx1 + 24, by1 + 80), "Witaj na serwerze!", font=font_sub, fill=(150, 160, 255, 255))

    buffer = io.BytesIO()
    card.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="welcome.png")


async def get_economy(user_id: int):
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT credits, last_daily, rep, last_rep, title, text_xp, voice_xp FROM economy WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            await db.execute("INSERT INTO economy (user_id) VALUES (?)", (user_id,))
            await db.commit()
            return 0, None, 0, None, "", 0, 0
        return row


async def set_economy(user_id: int, **kwargs):
    async with aiosqlite.connect("moderation.db") as db:
        await db.execute("INSERT OR IGNORE INTO economy (user_id) VALUES (?)", (user_id,))
        for k, v in kwargs.items():
            await db.execute(f"UPDATE economy SET {k} = ? WHERE user_id = ?", (v, user_id))
        await db.commit()


async def get_points(user_id: int) -> int:
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute("SELECT points FROM points WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            await db.execute("INSERT INTO points (user_id, points) VALUES (?, 0)", (user_id,))
            await db.commit()
            return 0
        return row[0]


async def set_points(user_id: int, amount: int):
    async with aiosqlite.connect("moderation.db") as db:
        await db.execute(
            "INSERT OR REPLACE INTO points (user_id, points) VALUES (?, ?)",
            (user_id, max(0, amount)),
        )
        await db.commit()


def xp_to_level(xp: int) -> int:
    return int((xp / 100) ** 0.5) + 1


def level_to_xp(level: int) -> int:
    return (level - 1) ** 2 * 100


# ===================== EVENTS =====================
@bot.event
async def on_ready():
    await init_db()
    for g in bot.guilds:
        await update_invite_cache(g)
    try:
        synced = await bot.tree.sync()
        print(f"✅ Zalogowano: {bot.user}")
        print(f"✅ Zsynchronizowano komend: {len(synced)}")
        for cmd in synced:
            print(f"   /{cmd.name}")
    except Exception as e:
        print(f"❌ Błąd sync: {e}")


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
                        (inv.inviter.id if inv.inviter else 0, member.id, inv.code, datetime.utcnow().isoformat()),
                    )
                    await db.commit()
                break
        await update_invite_cache(guild)
    except Exception:
        pass
    if WELCOME_CHANNEL_ID:
        channel = bot.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            try:
                file = await create_welcome_card(member)
                await channel.send(file=file)
                await channel.send(f"Siema {member.mention} na **{SERVER_NAME}**")
            except Exception as e:
                print(f"Błąd powitania: {e}")
    if VERIFIED_ROLE_ID:
        role = member.guild.get_role(VERIFIED_ROLE_ID)
        if role:
            try:
                await member.add_roles(role, reason="Automatyczna weryfikacja")
            except Exception:
                pass


@bot.event
async def on_member_remove(member: discord.Member):
    if GOODBYE_CHANNEL_ID:
        channel = bot.get_channel(GOODBYE_CHANNEL_ID)
        if channel:
            try:
                await channel.send(f"**{member}** wyszedł z serwera")
            except Exception:
                pass


@bot.event
async def on_voice_state_update(member, before, after):
    global TEMP_ENABLED
    if not TEMP_ENABLED or not TEMP_HUB_CHANNEL_ID:
        return
    if after.channel and after.channel.id == TEMP_HUB_CHANNEL_ID:
        owned = sum(1 for cid, oid in temp_channels.items() if oid == member.id)
        if owned >= TEMP_MAX_CHANNELS:
            try:
                await member.move_to(None)
            except Exception:
                pass
            return
        category = bot.get_channel(TEMP_CATEGORY_ID) if TEMP_CATEGORY_ID else after.channel.category
        try:
            overwrites = {
                member.guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
                member: discord.PermissionOverwrite(manage_channels=True, move_members=True, mute_members=True),
            }
            new_ch = await member.guild.create_voice_channel(
                name=f"🔊 {member.display_name}",
                category=category,
                overwrites=overwrites,
                reason="Temp VC",
            )
            temp_channels[new_ch.id] = member.id
            await member.move_to(new_ch)
        except Exception as e:
            print(f"Temp VC error: {e}")
    if before.channel and before.channel.id in temp_channels:
        if len(before.channel.members) == 0:
            ch_id = before.channel.id

            async def delete_later():
                await asyncio.sleep(TEMP_DELETE_SECONDS)
                ch = bot.get_channel(ch_id)
                if ch and len(ch.members) == 0:
                    try:
                        await ch.delete(reason="Temp VC pusty")
                    except Exception:
                        pass
                    temp_channels.pop(ch_id, None)

            asyncio.create_task(delete_later())


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    try:
        _, _, _, _, _, text_xp, _ = await get_economy(message.author.id)
        await set_economy(message.author.id, text_xp=text_xp + random.randint(5, 15))
    except Exception:
        pass
    await bot.process_commands(message)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return
    if interaction.response.is_done():
        return
    if isinstance(error, app_commands.TransformerError):
        await interaction.response.send_message(
            "❌ Nie znaleziono użytkownika. Użyj @wzmianki albo ID.", ephemeral=True
        )
        return
    await interaction.response.send_message(f"❌ Błąd: `{str(error)[:200]}`", ephemeral=True)
    print(f"[ERROR] {error}")


# ===================== MODERACJA =====================
@bot.tree.command(name="ban", description="Zbanuj użytkownika")
@app_commands.describe(uzytkownik="Kogo zbanować", powod="Powód", czas="np. 10m 1h 1d (puste=perm)")
@is_mod()
async def cmd_ban(interaction: discord.Interaction, uzytkownik: discord.User, powod: str = "Brak powodu", czas: str = None):
    member = interaction.guild.get_member(uzytkownik.id)
    if member and member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    delta = parse_time(czas) if czas else None
    duration_human = format_time_human(delta) if delta else None
    await notify_user(uzytkownik, interaction.guild, action="banned", color=0xFF0000, reason=powod, duration=duration_human)
    await interaction.guild.ban(uzytkownik, reason=f"{interaction.user} | {powod}" + (f" | {czas}" if czas else ""), delete_message_days=1)
    embed = discord.Embed(title="🔨 Zbanowany", color=0xFF0000, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    embed.add_field(name="Czas", value=format_time(delta) if delta else "Permanentny", inline=True)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)
    if delta:
        async def unban_later():
            await asyncio.sleep(delta.total_seconds())
            try:
                await interaction.guild.unban(uzytkownik, reason="Koniec tempbana")
                await send_log(discord.Embed(title="✅ Tempban zakończony", description=f"{uzytkownik} odbanowany", color=0x00FF00))
                await notify_user(uzytkownik, interaction.guild, action="unbanned", color=0x00FF00, reason="Koniec bana czasowego")
            except Exception:
                pass
        asyncio.create_task(unban_later())


@bot.tree.command(name="unban", description="Odbanuj użytkownika")
@app_commands.describe(uzytkownik_id="ID użytkownika", powod="Powód")
@is_mod()
async def cmd_unban(interaction: discord.Interaction, uzytkownik_id: str, powod: str = "Brak powodu"):
    try:
        user = await bot.fetch_user(int(uzytkownik_id))
        await interaction.guild.unban(user, reason=f"{interaction.user} | {powod}")
        await notify_user(user, interaction.guild, action="unbanned", color=0x00FF00, reason=powod)
        embed = discord.Embed(title="✅ Odbanowany", color=0x00FF00, timestamp=datetime.utcnow())
        embed.add_field(name="Użytkownik", value=f"{user} (`{user.id}`)", inline=False)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Powód", value=powod, inline=False)
        await interaction.response.send_message(embed=embed)
        await send_log(embed, skip_channel_id=interaction.channel_id)
    except Exception:
        await interaction.response.send_message("❌ Nie znaleziono / nie jest zbanowany. Podaj ID.", ephemeral=True)


@bot.tree.command(name="softban", description="Softban (ban + unban)")
@app_commands.describe(uzytkownik="Kogo", powod="Powód")
@is_mod()
async def cmd_softban(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    await notify_user(uzytkownik, interaction.guild, action="softbanned", color=0xFF4500, reason=powod, extra="Wiadomości z 7 dni usunięte.")
    await uzytkownik.ban(reason=f"Softban | {interaction.user} | {powod}", delete_message_days=7)
    await interaction.guild.unban(uzytkownik, reason="Softban")
    embed = discord.Embed(title="💨 Softban", color=0xFF4500, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="kick", description="Wyrzuć użytkownika")
@app_commands.describe(uzytkownik="Kogo", powod="Powód")
@is_mod()
async def cmd_kick(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    await notify_user(uzytkownik, interaction.guild, action="kicked", color=0xFFA500, reason=powod)
    await uzytkownik.kick(reason=f"{interaction.user} | {powod}")
    embed = discord.Embed(title="👢 Wyrzucony", color=0xFFA500, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="mute", description="Timeout użytkownika")
@app_commands.describe(uzytkownik="Kogo", czas="np. 10m 1h 1d", powod="Powód")
@is_mod()
async def cmd_mute(interaction: discord.Interaction, uzytkownik: discord.Member, czas: str, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    delta = parse_time(czas)
    if not delta:
        return await interaction.response.send_message("❌ Zły czas! Przykłady: `10m` `1h` `1d` `1w`", ephemeral=True)
    if delta.total_seconds() > 28 * 24 * 3600:
        return await interaction.response.send_message("❌ Max 28 dni.", ephemeral=True)
    await notify_user(uzytkownik, interaction.guild, action="muted", color=0x808080, reason=powod, duration=format_time_human(delta))
    await uzytkownik.timeout(delta, reason=f"{interaction.user} | {powod}")
    embed = discord.Embed(title="🔇 Wyciszony", color=0x808080, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Czas", value=format_time(delta), inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="unmute", description="Zdejmij timeout")
@app_commands.describe(uzytkownik="Komu")
@is_mod()
async def cmd_unmute(interaction: discord.Interaction, uzytkownik: discord.Member):
    await uzytkownik.timeout(None)
    await notify_user(uzytkownik, interaction.guild, action="unmuted", color=0x00FF00, reason="Zdjęte przez moda")
    embed = discord.Embed(title="🔊 Mute zdjęty", color=0x00FF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="timeout", description="Timeout (alias mute)")
@app_commands.describe(uzytkownik="Kogo", czas="np. 10m 1h", powod="Powód")
@is_mod()
async def cmd_timeout(interaction: discord.Interaction, uzytkownik: discord.Member, czas: str, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    delta = parse_time(czas)
    if not delta:
        return await interaction.response.send_message("❌ Zły czas! Przykłady: `10m` `1h` `1d`", ephemeral=True)
    if delta.total_seconds() > 28 * 24 * 3600:
        return await interaction.response.send_message("❌ Max 28 dni.", ephemeral=True)
    await notify_user(uzytkownik, interaction.guild, action="muted", color=0x808080, reason=powod, duration=format_time_human(delta))
    await uzytkownik.timeout(delta, reason=f"{interaction.user} | {powod}")
    embed = discord.Embed(title="🔇 Timeout", color=0x808080, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Czas", value=format_time(delta), inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="untimeout", description="Zdejmij timeout (alias unmute)")
@app_commands.describe(uzytkownik="Komu")
@is_mod()
async def cmd_untimeout(interaction: discord.Interaction, uzytkownik: discord.Member):
    await uzytkownik.timeout(None)
    await notify_user(uzytkownik, interaction.guild, action="unmuted", color=0x00FF00, reason="Zdjęte przez moda")
    embed = discord.Embed(title="🔊 Timeout zdjęty", color=0x00FF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="mutetext", description="Wycisz tekstowo")
@app_commands.describe(uzytkownik="Kogo", czas="opcjonalnie", powod="Powód")
@is_mod()
async def cmd_mutetext(interaction: discord.Interaction, uzytkownik: discord.Member, czas: str = None, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    for channel in interaction.guild.text_channels:
        try:
            overwrite = channel.overwrites_for(uzytkownik)
            overwrite.send_messages = False
            await channel.set_permissions(uzytkownik, overwrite=overwrite, reason=powod)
        except Exception:
            pass
    delta = parse_time(czas) if czas else None
    if delta:
        async def unmute_later():
            await asyncio.sleep(delta.total_seconds())
            for channel in interaction.guild.text_channels:
                try:
                    await channel.set_permissions(uzytkownik, overwrite=None)
                except Exception:
                    pass
        asyncio.create_task(unmute_later())
    embed = discord.Embed(title="🔇 Text Mute", color=0x808080, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.add_field(name="Powód", value=powod, inline=False)
    if delta:
        embed.add_field(name="Czas", value=format_time(delta))
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="unmutetext", description="Odcisz tekstowo")
@app_commands.describe(uzytkownik="Komu")
@is_mod()
async def cmd_unmutetext(interaction: discord.Interaction, uzytkownik: discord.Member):
    for channel in interaction.guild.text_channels:
        try:
            await channel.set_permissions(uzytkownik, overwrite=None)
        except Exception:
            pass
    embed = discord.Embed(title="🔊 Text Unmute", color=0x00FF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="mutevoice", description="Wycisz głosowo")
@app_commands.describe(uzytkownik="Kogo", powod="Powód")
@is_mod()
async def cmd_mutevoice(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    try:
        await uzytkownik.edit(mute=True, reason=powod)
    except Exception:
        return await interaction.response.send_message("❌ Nie mogę wyciszyć (brak uprawnień / nie na VC).", ephemeral=True)
    embed = discord.Embed(title="🔇 Voice Mute", color=0x808080, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="unmutevoice", description="Odcisz głosowo")
@app_commands.describe(uzytkownik="Komu")
@is_mod()
async def cmd_unmutevoice(interaction: discord.Interaction, uzytkownik: discord.Member):
    try:
        await uzytkownik.edit(mute=False)
    except Exception:
        return await interaction.response.send_message("❌ Nie mogę odciszyć.", ephemeral=True)
    embed = discord.Embed(title="🔊 Voice Unmute", color=0x00FF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention)
    embed.add_field(name="Moderator", value=interaction.user.mention)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="warn", description="Ostrzeż użytkownika")
@app_commands.describe(uzytkownik="Kogo", powod="Powód")
@is_mod()
async def cmd_warn(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str):
    async with aiosqlite.connect("moderation.db") as db:
        await db.execute(
            "INSERT INTO warnings (user_id, moderator_id, reason, timestamp) VALUES (?,?,?,?)",
            (uzytkownik.id, interaction.user.id, powod, datetime.utcnow().isoformat()),
        )
        await db.commit()
        cur = await db.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (uzytkownik.id,))
        count = (await cur.fetchone())[0]
    embed = discord.Embed(title="⚠️ Ostrzeżenie", color=0xFFFF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Ilość warnów", value=str(count), inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)
    await notify_user(uzytkownik, interaction.guild, action="warned", color=0xFFFF00, reason=powod, extra=f"Łącznie warnów: **{count}**")


@bot.tree.command(name="warnings", description="Sprawdź ostrzeżenia")
@app_commands.describe(uzytkownik="Kogo")
@is_mod()
async def cmd_warnings(interaction: discord.Interaction, uzytkownik: discord.Member):
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT reason, timestamp, moderator_id FROM warnings WHERE user_id = ? ORDER BY id DESC",
            (uzytkownik.id,),
        )
        rows = await cur.fetchall()
    if not rows:
        return await interaction.response.send_message(f"{uzytkownik.mention} nie ma ostrzeżeń.", ephemeral=True)
    embed = discord.Embed(title=f"Warny — {uzytkownik}", color=0xFFA500, timestamp=datetime.utcnow())
    for i, (reason, ts, mod) in enumerate(rows[:12], 1):
        embed.add_field(name=f"#{i} • <t:{int(datetime.fromisoformat(ts).timestamp())}:R>", value=f"{reason}\nMod: <@{mod}>", inline=False)
    embed.set_footer(text=f"Łącznie: {len(rows)}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="clearwarns", description="Wyczyść warny")
@app_commands.describe(uzytkownik="Kogo")
@is_mod()
async def cmd_clearwarns(interaction: discord.Interaction, uzytkownik: discord.Member):
    async with aiosqlite.connect("moderation.db") as db:
        await db.execute("DELETE FROM warnings WHERE user_id = ?", (uzytkownik.id,))
        await db.commit()
    embed = discord.Embed(title="🧹 Warny wyczyszczone", color=0x00FF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="warn_remove", description="Usuń warny")
@app_commands.describe(uzytkownik="Kogo")
@is_mod()
async def cmd_warn_remove(interaction: discord.Interaction, uzytkownik: discord.Member):
    async with aiosqlite.connect("moderation.db") as db:
        await db.execute("DELETE FROM warnings WHERE user_id = ?", (uzytkownik.id,))
        await db.commit()
    embed = discord.Embed(title="🧹 Warny usunięte", color=0x00FF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="clear", description="Usuń wiadomości")
@app_commands.describe(ilosc="1-100")
@is_mod()
async def cmd_clear(interaction: discord.Interaction, ilosc: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=ilosc)
    embed = discord.Embed(title="🧹 Usunięto", description=f"**{len(deleted)}** wiadomości", color=0x3498DB)
    await interaction.followup.send(embed=embed, ephemeral=True)
    await send_log(embed)


@bot.tree.command(name="slowmode", description="Ustaw slowmode")
@app_commands.describe(sekundy="0 = wyłącz")
@is_mod()
async def cmd_slowmode(interaction: discord.Interaction, sekundy: app_commands.Range[int, 0, 21600]):
    await interaction.channel.edit(slowmode_delay=sekundy)
    embed = discord.Embed(title="🐌 Slowmode", description=f"Ustawiono na **{sekundy}s**", color=0x9B59B6)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="lock", description="Zablokuj kanał")
@is_mod()
async def cmd_lock(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    embed = discord.Embed(title="🔒 Kanał zablokowany", color=0xE74C3C)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="unlock", description="Odblokuj kanał")
@is_mod()
async def cmd_unlock(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    embed = discord.Embed(title="🔓 Kanał odblokowany", color=0x2ECC71)
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="nick", description="Zmień nick")
@app_commands.describe(uzytkownik="Kogo", nowy_nick="Nowy nick (puste = reset)")
@is_mod()
async def cmd_nick(interaction: discord.Interaction, uzytkownik: discord.Member, nowy_nick: str = None):
    stary = uzytkownik.display_name
    await uzytkownik.edit(nick=nowy_nick)
    embed = discord.Embed(title="📝 Nick zmieniony", color=0x1ABC9C)
    embed.add_field(name="Użytkownik", value=uzytkownik.mention)
    embed.add_field(name="Stary", value=stary)
    embed.add_field(name="Nowy", value=nowy_nick or "zresetowany")
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


@bot.tree.command(name="setnick", description="Zmień nick")
@app_commands.describe(uzytkownik="Kogo", nowy_nick="Nowy nick")
@is_mod()
async def cmd_setnick(interaction: discord.Interaction, uzytkownik: discord.Member, nowy_nick: str = None):
    stary = uzytkownik.display_name
    await uzytkownik.edit(nick=nowy_nick)
    embed = discord.Embed(title="📝 Nick zmieniony", color=0x1ABC9C)
    embed.add_field(name="Użytkownik", value=uzytkownik.mention)
    embed.add_field(name="Stary", value=stary)
    embed.add_field(name="Nowy", value=nowy_nick or "zresetowany")
    await interaction.response.send_message(embed=embed)
    await send_log(embed, skip_channel_id=interaction.channel_id)


# ===================== INFO =====================
@bot.tree.command(name="userinfo", description="Info o użytkowniku")
@app_commands.describe(uzytkownik="Kogo")
async def cmd_userinfo(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    user = uzytkownik or interaction.user
    embed = discord.Embed(title=f"Informacje — {user}", color=user.color or 0x5865F2, timestamp=datetime.utcnow())
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="ID", value=str(user.id), inline=True)
    embed.add_field(name="Nick", value=user.display_name, inline=True)
    embed.add_field(name="Konto utworzone", value=f"<t:{int(user.created_at.timestamp())}:R>", inline=False)
    embed.add_field(name="Dołączył", value=f"<t:{int(user.joined_at.timestamp())}:R>" if user.joined_at else "?", inline=False)
    roles = [r.mention for r in user.roles if r != interaction.guild.default_role]
    embed.add_field(name=f"Role ({len(roles)})", value=" ".join(roles[:12]) or "Brak", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="user", description="Info o użytkowniku")
@app_commands.describe(uzytkownik="Kogo")
async def cmd_user(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    user = uzytkownik or interaction.user
    embed = discord.Embed(title=f"Informacje — {user}", color=user.color or 0x5865F2, timestamp=datetime.utcnow())
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="ID", value=str(user.id), inline=True)
    embed.add_field(name="Nick", value=user.display_name, inline=True)
    embed.add_field(name="Konto utworzone", value=f"<t:{int(user.created_at.timestamp())}:R>", inline=False)
    embed.add_field(name="Dołączył", value=f"<t:{int(user.joined_at.timestamp())}:R>" if user.joined_at else "?", inline=False)
    roles = [r.mention for r in user.roles if r != interaction.guild.default_role]
    embed.add_field(name=f"Role ({len(roles)})", value=" ".join(roles[:12]) or "Brak", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="serverinfo", description="Info o serwerze")
async def cmd_serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=0x5865F2, timestamp=datetime.utcnow())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Właściciel", value=g.owner.mention if g.owner else "?", inline=True)
    embed.add_field(name="Członkowie", value=str(g.member_count), inline=True)
    embed.add_field(name="Kanały", value=str(len(g.channels)), inline=True)
    embed.add_field(name="Role", value=str(len(g.roles)), inline=True)
    embed.add_field(name="Boosty", value=str(g.premium_subscription_count or 0), inline=True)
    embed.add_field(name="Utworzony", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="server", description="Info o serwerze")
async def cmd_server(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=0x5865F2, timestamp=datetime.utcnow())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Właściciel", value=g.owner.mention if g.owner else "?", inline=True)
    embed.add_field(name="Członkowie", value=str(g.member_count), inline=True)
    embed.add_field(name="Kanały", value=str(len(g.channels)), inline=True)
    embed.add_field(name="Role", value=str(len(g.roles)), inline=True)
    embed.add_field(name="Boosty", value=str(g.premium_subscription_count or 0), inline=True)
    embed.add_field(name="Utworzony", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="avatar", description="Pokaż avatar")
@app_commands.describe(uzytkownik="Kogo")
async def cmd_avatar(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    user = uzytkownik or interaction.user
    embed = discord.Embed(title=f"Avatar — {user}", color=0x5865F2)
    embed.set_image(url=user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="roles", description="Lista ról serwera")
async def cmd_roles(interaction: discord.Interaction):
    roles = sorted([r for r in interaction.guild.roles if r != interaction.guild.default_role], key=lambda r: r.position, reverse=True)
    tekst = "\n".join([f"{r.mention} — {len(r.members)} osób" for r in roles[:25]])
    embed = discord.Embed(title=f"Role serwera ({len(roles)})", description=tekst or "Brak", color=0x5865F2)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ping", description="Sprawdź opóźnienie bota")
async def cmd_ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! `{latency}ms`")


@bot.tree.command(name="help", description="Lista komend")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(title="📚 Pomoc — komendy bota", color=0x5865F2, description="Wszystkie komendy slash `/`")
    embed.add_field(name="Moderacja", value="`/ban` `/kick` `/mute` `/unmute` `/timeout` `/mutetext` `/mutevoice` `/warn` `/clear` `/lock` `/unlock` `/slowmode` `/nick`", inline=False)
    embed.add_field(name="Info", value="`/user` `/server` `/avatar` `/roles` `/ping` `/invites` `/help`", inline=False)
    embed.add_field(name="Kolory", value="`/color` `/colors` `/setcolor`", inline=False)
    embed.add_field(name="Ekonomia & Level", value="`/credits` `/daily` `/rep` `/rank` `/profile` `/title` `/top`", inline=False)
    embed.add_field(name="Punkty", value="`/points_increase` `/points_decrease` `/points_set` `/points_list` `/points_reset`", inline=False)
    embed.add_field(name="Głos", value="`/moveme` `/move` `/moveall` `/vkick`", inline=False)
    embed.add_field(name="Inne", value="`/roll` `/short` `/rolegive` `/roleremove`", inline=False)
    embed.add_field(name="Temp VC", value="`/tempon` `/tempoff` `/tempmax` `/temptime`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="invite", description="Link do zaproszenia bota")
async def cmd_invite(interaction: discord.Interaction):
    url = discord.utils.oauth_url(bot.user.id, permissions=discord.Permissions(administrator=True))
    await interaction.response.send_message(f"🔗 Zaproś bota:\n{url}")


@bot.tree.command(name="invites", description="Kto kogo zaprosił")
@app_commands.describe(uzytkownik="Opcjonalnie")
async def cmd_invites(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    target = uzytkownik or interaction.user
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT invited_id, code, timestamp FROM invites WHERE inviter_id = ? ORDER BY timestamp DESC",
            (target.id,),
        )
        rows = await cur.fetchall()
    embed = discord.Embed(title=f"📨 Zaproszenia — {target}", color=0x5865F2, timestamp=datetime.utcnow())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Łącznie", value=str(len(rows)), inline=False)
    if rows:
        tekst = "\n".join([f"• <@{i}> (`{i}`) — `{c}` • <t:{int(datetime.fromisoformat(t).timestamp())}:R>" for i, c, t in rows[:12]])
        embed.add_field(name="Ostatnie", value=tekst, inline=False)
    else:
        embed.add_field(name="Ostatnie", value="Brak danych", inline=False)
    await interaction.response.send_message(embed=embed)


# ===================== KOLORY =====================
@bot.tree.command(name="colors", description="Lista dostępnych kolorów")
async def cmd_colors(interaction: discord.Interaction):
    color_roles = [r for r in interaction.guild.roles if r.name.isdigit() and 1 <= int(r.name) <= 100]
    color_roles.sort(key=lambda r: int(r.name))
    if not color_roles:
        return await interaction.response.send_message("❌ Brak ról kolorów.\nStwórz role nazwane `1`, `2`, `3`... z kolorami.", ephemeral=True)
    tekst = "\n".join([f"**{r.name}** — {r.mention}" for r in color_roles[:30]])
    embed = discord.Embed(title="🎨 Dostępne kolory", description=tekst, color=0x5865F2)
    embed.set_footer(text="Użyj /color numer")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="color", description="Zmień swój kolor")
@app_commands.describe(numer="Numer koloru (0 = usuń)")
async def cmd_color(interaction: discord.Interaction, numer: int):
    member = interaction.user
    for r in list(member.roles):
        if r.name.isdigit() and 1 <= int(r.name) <= 100:
            try:
                await member.remove_roles(r, reason="Zmiana koloru")
            except Exception:
                pass
    if numer == 0:
        return await interaction.response.send_message("✅ Kolor zresetowany.")
    role = discord.utils.get(interaction.guild.roles, name=str(numer))
    if not role:
        return await interaction.response.send_message(f"❌ Nie ma koloru **{numer}**. Sprawdź `/colors`.", ephemeral=True)
    try:
        await member.add_roles(role, reason="Kolor")
        await interaction.response.send_message(f"✅ Ustawiono kolor **{numer}** {role.mention}")
    except Exception:
        await interaction.response.send_message("❌ Nie mogę dać roli (hierarchia / uprawnienia).", ephemeral=True)


@bot.tree.command(name="setcolor", description="Zmień kolor roli (hex)")
@app_commands.describe(rola="Rola", hex_color="np. #FF0000 lub FF0000")
@is_mod()
async def cmd_setcolor(interaction: discord.Interaction, rola: discord.Role, hex_color: str):
    hex_color = hex_color.lstrip("#")
    try:
        color_int = int(hex_color, 16)
        await rola.edit(color=discord.Color(color_int))
        await interaction.response.send_message(f"✅ Kolor roli {rola.mention} zmieniony na `#{hex_color}`")
    except Exception:
        await interaction.response.send_message("❌ Zły hex lub brak uprawnień.", ephemeral=True)


# ===================== EKONOMIA =====================
@bot.tree.command(name="credits", description="Sprawdź kredyty / przelej")
@app_commands.describe(uzytkownik="Kogo sprawdzić", kwota="Ile przelać (opcjonalnie)")
async def cmd_credits(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None, kwota: Optional[int] = None):
    target = uzytkownik or interaction.user
    if kwota is not None and kwota > 0 and target.id != interaction.user.id:
        my_credits, *_ = await get_economy(interaction.user.id)
        if my_credits < kwota:
            return await interaction.response.send_message("❌ Za mało kredytów.", ephemeral=True)
        await set_economy(interaction.user.id, credits=my_credits - kwota)
        their, *_ = await get_economy(target.id)
        await set_economy(target.id, credits=their + kwota)
        return await interaction.response.send_message(f"✅ Przelano **{kwota}** kredytów do {target.mention}")
    creds, _, rep, _, title, text_xp, _ = await get_economy(target.id)
    embed = discord.Embed(title=f"💰 Kredyty — {target.display_name}", color=0xF1C40F)
    embed.add_field(name="Kredyty", value=str(creds))
    embed.add_field(name="Rep", value=str(rep))
    embed.add_field(name="Tytuł", value=title or "Brak")
    embed.set_thumbnail(url=target.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="daily", description="Odbierz codzienną nagrodę")
async def cmd_daily(interaction: discord.Interaction):
    creds, last_daily, *_ = await get_economy(interaction.user.id)
    now = datetime.utcnow()
    if last_daily:
        last = datetime.fromisoformat(last_daily)
        if (now - last).total_seconds() < 86400:
            left = 86400 - (now - last).total_seconds()
            return await interaction.response.send_message(
                f"⏳ Daily dostępne za **{format_time_human(timedelta(seconds=int(left)))}**", ephemeral=True
            )
    reward = random.randint(50, 150)
    await set_economy(interaction.user.id, credits=creds + reward, last_daily=now.isoformat())
    await interaction.response.send_message(f"🎁 Otrzymałeś **{reward}** kredytów! Masz teraz **{creds + reward}**.")


@bot.tree.command(name="rep", description="Daj komuś reputację (1x/24h)")
@app_commands.describe(uzytkownik="Komu")
async def cmd_rep(interaction: discord.Interaction, uzytkownik: discord.Member):
    if uzytkownik.id == interaction.user.id:
        return await interaction.response.send_message("❌ Nie możesz dać rep sobie.", ephemeral=True)
    _, _, _, last_rep, *_ = await get_economy(interaction.user.id)
    now = datetime.utcnow()
    if last_rep:
        last = datetime.fromisoformat(last_rep)
        if (now - last).total_seconds() < 86400:
            left = 86400 - (now - last).total_seconds()
            return await interaction.response.send_message(
                f"⏳ Możesz dać rep za **{format_time_human(timedelta(seconds=int(left)))}**", ephemeral=True
            )
    _, _, their_rep, *_ = await get_economy(uzytkownik.id)
    await set_economy(uzytkownik.id, rep=their_rep + 1)
    await set_economy(interaction.user.id, last_rep=now.isoformat())
    await interaction.response.send_message(f"⭐ Dałeś **+1 rep** użytkownikowi {uzytkownik.mention}!")


@bot.tree.command(name="title", description="Ustaw tytuł w profilu")
@app_commands.describe(tytul="Nowy tytuł")
async def cmd_title(interaction: discord.Interaction, tytul: str):
    if len(tytul) > 32:
        return await interaction.response.send_message("❌ Max 32 znaki.", ephemeral=True)
    await set_economy(interaction.user.id, title=tytul)
    await interaction.response.send_message(f"✅ Tytuł ustawiony na: **{tytul}**")


@bot.tree.command(name="rank", description="Karta rangi")
@app_commands.describe(uzytkownik="Kogo")
async def cmd_rank(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    user = uzytkownik or interaction.user
    _, _, rep, _, title, text_xp, _ = await get_economy(user.id)
    level = xp_to_level(text_xp)
    next_xp = level_to_xp(level + 1)
    embed = discord.Embed(title=f"📊 Rank — {user.display_name}", color=user.color or 0x5865F2)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="Poziom", value=str(level))
    embed.add_field(name="XP", value=f"{text_xp} / {next_xp}")
    embed.add_field(name="Rep", value=str(rep))
    if title:
        embed.add_field(name="Tytuł", value=title, inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="profile", description="Profil użytkownika")
@app_commands.describe(uzytkownik="Kogo")
async def cmd_profile(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    user = uzytkownik or interaction.user
    creds, _, rep, _, title, text_xp, _ = await get_economy(user.id)
    pts = await get_points(user.id)
    level = xp_to_level(text_xp)
    embed = discord.Embed(title=f"👤 Profil — {user.display_name}", color=user.color or 0x5865F2)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="Kredyty", value=str(creds))
    embed.add_field(name="Punkty", value=str(pts))
    embed.add_field(name="Rep", value=str(rep))
    embed.add_field(name="Poziom", value=str(level))
    embed.add_field(name="XP", value=str(text_xp))
    if title:
        embed.add_field(name="Tytuł", value=title, inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="top", description="Top XP")
async def cmd_top(interaction: discord.Interaction):
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute("SELECT user_id, text_xp FROM economy ORDER BY text_xp DESC LIMIT 10")
        rows = await cur.fetchall()
    if not rows:
        return await interaction.response.send_message("Brak danych.")
    tekst = "\n".join([f"**{i}.** <@{uid}> — {xp} XP (lvl {xp_to_level(xp)})" for i, (uid, xp) in enumerate(rows, 1)])
    embed = discord.Embed(title="🏆 Top XP", description=tekst, color=0xF1C40F)
    await interaction.response.send_message(embed=embed)


# ===================== PUNKTY =====================
@bot.tree.command(name="points_increase", description="Dodaj punkty")
@app_commands.describe(uzytkownik="Komu", ilosc="Ile")
@is_mod()
async def cmd_points_increase(interaction: discord.Interaction, uzytkownik: discord.Member, ilosc: int):
    current = await get_points(uzytkownik.id)
    await set_points(uzytkownik.id, current + ilosc)
    await interaction.response.send_message(f"✅ Dodano **{ilosc}** punktów {uzytkownik.mention}. Ma teraz **{current + ilosc}**.")


@bot.tree.command(name="points_decrease", description="Odejmij punkty")
@app_commands.describe(uzytkownik="Komu", ilosc="Ile")
@is_mod()
async def cmd_points_decrease(interaction: discord.Interaction, uzytkownik: discord.Member, ilosc: int):
    current = await get_points(uzytkownik.id)
    await set_points(uzytkownik.id, current - ilosc)
    await interaction.response.send_message(f"✅ Odjęto **{ilosc}** punktów {uzytkownik.mention}. Ma teraz **{max(0, current - ilosc)}**.")


@bot.tree.command(name="points_set", description="Ustaw punkty")
@app_commands.describe(uzytkownik="Komu", ilosc="Ile")
@is_mod()
async def cmd_points_set(interaction: discord.Interaction, uzytkownik: discord.Member, ilosc: int):
    await set_points(uzytkownik.id, ilosc)
    await interaction.response.send_message(f"✅ Ustawiono **{ilosc}** punktów dla {uzytkownik.mention}.")


@bot.tree.command(name="points_list", description="Lista punktów")
async def cmd_points_list(interaction: discord.Interaction):
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute("SELECT user_id, points FROM points WHERE points > 0 ORDER BY points DESC LIMIT 15")
        rows = await cur.fetchall()
    if not rows:
        return await interaction.response.send_message("Brak punktów.")
    tekst = "\n".join([f"**{i}.** <@{uid}> — **{pts}**" for i, (uid, pts) in enumerate(rows, 1)])
    embed = discord.Embed(title="📊 Punkty", description=tekst, color=0x3498DB)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="points_reset", description="Reset punktów")
@app_commands.describe(uzytkownik="Kogo (puste = wszyscy)")
@is_mod()
async def cmd_points_reset(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    async with aiosqlite.connect("moderation.db") as db:
        if uzytkownik:
            await db.execute("UPDATE points SET points = 0 WHERE user_id = ?", (uzytkownik.id,))
            msg = f"✅ Zresetowano punkty {uzytkownik.mention}."
        else:
            await db.execute("UPDATE points SET points = 0")
            msg = "✅ Zresetowano wszystkie punkty."
        await db.commit()
    await interaction.response.send_message(msg)


# ===================== GŁOS =====================
@bot.tree.command(name="moveme", description="Przenieś się na inny kanał głosowy")
@app_commands.describe(kanal="Kanał głosowy")
async def cmd_moveme(interaction: discord.Interaction, kanal: discord.VoiceChannel):
    if not interaction.user.voice:
        return await interaction.response.send_message("❌ Musisz być na kanale głosowym.", ephemeral=True)
    try:
        await interaction.user.move_to(kanal)
        await interaction.response.send_message(f"✅ Przeniesiono Cię na {kanal.mention}")
    except Exception:
        await interaction.response.send_message("❌ Nie mogę Cię przenieść.", ephemeral=True)


@bot.tree.command(name="move", description="Przenieś użytkownika na VC")
@app_commands.describe(uzytkownik="Kogo", kanal="Dokąd")
@is_mod()
async def cmd_move(interaction: discord.Interaction, uzytkownik: discord.Member, kanal: discord.VoiceChannel):
    if not uzytkownik.voice:
        return await interaction.response.send_message("❌ Użytkownik nie jest na VC.", ephemeral=True)
    try:
        await uzytkownik.move_to(kanal)
        await interaction.response.send_message(f"✅ Przeniesiono {uzytkownik.mention} na {kanal.mention}")
    except Exception:
        await interaction.response.send_message("❌ Nie mogę przenieść.", ephemeral=True)


@bot.tree.command(name="moveall", description="Przenieś wszystkich z Twojego VC")
@app_commands.describe(kanal="Dokąd")
@is_mod()
async def cmd_moveall(interaction: discord.Interaction, kanal: discord.VoiceChannel):
    if not interaction.user.voice:
        return await interaction.response.send_message("❌ Musisz być na kanale głosowym.", ephemeral=True)
    source = interaction.user.voice.channel
    count = 0
    for m in list(source.members):
        try:
            await m.move_to(kanal)
            count += 1
        except Exception:
            pass
    await interaction.response.send_message(f"✅ Przeniesiono **{count}** osób na {kanal.mention}")


@bot.tree.command(name="vkick", description="Wyrzuć z kanału głosowego")
@app_commands.describe(uzytkownik="Kogo")
@is_mod()
async def cmd_vkick(interaction: discord.Interaction, uzytkownik: discord.Member):
    if not uzytkownik.voice:
        return await interaction.response.send_message("❌ Użytkownik nie jest na VC.", ephemeral=True)
    try:
        await uzytkownik.move_to(None)
        await interaction.response.send_message(f"✅ Wyrzucono {uzytkownik.mention} z VC.")
    except Exception:
        await interaction.response.send_message("❌ Nie mogę wyrzucić.", ephemeral=True)


# ===================== ROLE =====================
@bot.tree.command(name="rolegive", description="Daj rolę")
@app_commands.describe(uzytkownik="Komu", rola="Jaka rola")
@is_mod()
async def cmd_rolegive(interaction: discord.Interaction, uzytkownik: discord.Member, rola: discord.Role):
    if rola >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    try:
        await uzytkownik.add_roles(rola, reason=f"Przez {interaction.user}")
        await interaction.response.send_message(f"✅ Nadano {rola.mention} → {uzytkownik.mention}")
    except Exception:
        await interaction.response.send_message("❌ Nie mogę nadać roli.", ephemeral=True)


@bot.tree.command(name="roleremove", description="Zdejmij rolę")
@app_commands.describe(uzytkownik="Komu", rola="Jaka rola")
@is_mod()
async def cmd_roleremove(interaction: discord.Interaction, uzytkownik: discord.Member, rola: discord.Role):
    try:
        await uzytkownik.remove_roles(rola, reason=f"Przez {interaction.user}")
        await interaction.response.send_message(f"✅ Zdjęto {rola.mention} z {uzytkownik.mention}")
    except Exception:
        await interaction.response.send_message("❌ Nie mogę zdjąć roli.", ephemeral=True)


# ===================== INNE =====================
@bot.tree.command(name="roll", description="Rzuć kostką")
@app_commands.describe(max="Maksymalna liczba (domyślnie 6)")
async def cmd_roll(interaction: discord.Interaction, max: app_commands.Range[int, 2, 1000] = 6):
    wynik = random.randint(1, max)
    await interaction.response.send_message(f"🎲 Wyrzucono: **{wynik}** (1-{max})")


@bot.tree.command(name="short", description="Skróć link")
@app_commands.describe(url="Link do skrócenia")
async def cmd_short(interaction: discord.Interaction, url: str):
    if not url.startswith("http"):
        url = "https://" + url
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://is.gd/create.php?format=simple&url={url}") as resp:
                if resp.status == 200:
                    short_url = await resp.text()
                    await interaction.response.send_message(f"🔗 Skrócony link: {short_url}")
                else:
                    await interaction.response.send_message("❌ Nie udało się skrócić.", ephemeral=True)
    except Exception:
        await interaction.response.send_message("❌ Błąd przy skracaniu.", ephemeral=True)


# ===================== TEMP VC =====================
@bot.tree.command(name="tempon", description="Włącz tymczasowe kanały VC")
@is_mod()
async def cmd_tempon(interaction: discord.Interaction):
    global TEMP_ENABLED
    TEMP_ENABLED = True
    await interaction.response.send_message("✅ Temp VC włączone.\nUstaw `TEMP_HUB_CHANNEL_ID` i `TEMP_CATEGORY_ID` w env.")


@bot.tree.command(name="tempoff", description="Wyłącz tymczasowe kanały VC")
@is_mod()
async def cmd_tempoff(interaction: discord.Interaction):
    global TEMP_ENABLED
    TEMP_ENABLED = False
    await interaction.response.send_message("✅ Temp VC wyłączone.")


@bot.tree.command(name="tempmax", description="Max kanałów na osobę")
@app_commands.describe(ilosc="Ile max")
@is_mod()
async def cmd_tempmax(interaction: discord.Interaction, ilosc: app_commands.Range[int, 1, 10]):
    global TEMP_MAX_CHANNELS
    TEMP_MAX_CHANNELS = ilosc
    await interaction.response.send_message(f"✅ Max kanałów na osobę: **{ilosc}**")


@bot.tree.command(name="temptime", description="Czas usuwania pustego temp VC (sekundy)")
@app_commands.describe(sekundy="Po ilu sekundach usunąć")
@is_mod()
async def cmd_temptime(interaction: discord.Interaction, sekundy: app_commands.Range[int, 5, 600]):
    global TEMP_DELETE_SECONDS
    TEMP_DELETE_SECONDS = sekundy
    await interaction.response.send_message(f"✅ Czas usuwania pustego temp: **{sekundy}s**")


# ===================== START =====================
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Brak TOKEN w zmiennych środowiskowych!")
    else:
        bot.run(TOKEN)
