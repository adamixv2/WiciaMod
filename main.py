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
TICKET_ARCHIVE_CHANNEL_ID = int(os.getenv("TICKET_ARCHIVE_CHANNEL_ID", "0"))  # archiwum dla adminów
GIVEAWAY_WIN_ROLE_ID = int(os.getenv("GIVEAWAY_WIN_ROLE_ID", "0"))  # rola dla zwycięzców (np. Klient)

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
        # Usuń duplikaty (zostaw najnowszy wpis na invited_id) zanim założymy unique index
        await db.execute("""
            DELETE FROM invites WHERE rowid NOT IN (
                SELECT MAX(rowid) FROM invites GROUP BY invited_id
            )
        """)
        # Jedna osoba = jedno aktywne zaproszenie (przy ponownym wejściu nadpisujemy)
        try:
            await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_invites_invited ON invites(invited_id)")
        except Exception:
            pass
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
        await db.execute("""CREATE TABLE IF NOT EXISTS tickets (
            channel_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            category TEXT,
            created_at TEXT,
            closed INTEGER DEFAULT 0,
            claimed_by INTEGER DEFAULT 0,
            ticket_number INTEGER DEFAULT 0,
            close_reason TEXT DEFAULT '')""")
        for col, typ in [
            ("claimed_by", "INTEGER DEFAULT 0"),
            ("ticket_number", "INTEGER DEFAULT 0"),
            ("close_reason", "TEXT DEFAULT ''"),
        ]:
            try:
                await db.execute(f"ALTER TABLE tickets ADD COLUMN {col} {typ}")
            except Exception:
                pass
        await db.execute("""CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER,
            message_id INTEGER,
            prize TEXT,
            winners_count INTEGER,
            end_at TEXT,
            host_id INTEGER,
            ended INTEGER DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS giveaway_entries (
            giveaway_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (giveaway_id, user_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS ticket_transcripts (
            ticket_number INTEGER PRIMARY KEY,
            channel_id INTEGER,
            content_txt TEXT,
            created_at TEXT)""")
        await db.commit()


async def get_active_invite_count(inviter_id: int) -> int:
    """Liczba aktywnych zaproszeń (osoby nadal na serwerze są w bazie)."""
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM invites WHERE inviter_id = ?",
            (inviter_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else 0


async def remove_invite_for_member(member_id: int) -> Optional[int]:
    """Usuwa zaproszenie przy wyjściu. Zwraca inviter_id albo None."""
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT inviter_id FROM invites WHERE invited_id = ?",
            (member_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        inviter_id = row[0]
        await db.execute("DELETE FROM invites WHERE invited_id = ?", (member_id,))
        await db.commit()
        return inviter_id


def is_mod():
    async def predicate(interaction: discord.Interaction):
        # Owner serwera zawsze może
        if interaction.guild and interaction.user.id == interaction.guild.owner_id:
            return True
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
        "banned": f"Zostałeś zbanowany na serwerze **{server}** na **{duration or 'zawsze'}**. | {reason}",
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
    embed = discord.Embed(title=titles.get(action, "Powiadomienie"), description=line, color=color, timestamp=datetime.now(timezone.utc))
    if guild and guild.icon:
        embed.set_author(name=server, icon_url=guild.icon.url)
    else:
        embed.set_author(name=server)
    embed.add_field(name="Serwer", value=server, inline=True)
    if action == "banned":
        embed.add_field(name="Czas bana", value=duration or "Na zawsze", inline=True)
    elif duration:
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

    # Kompaktowa karta – tekst zajmuje większość boxa (nie wygląda na mały)
    W, H = 720, 200
    card = Image.new("RGBA", (W, H), (16, 16, 20, 255))
    draw = ImageDraw.Draw(card)

    # Tło jak u ProBota – ciemne + geometryczne kształty
    for y in range(H):
        shade = 16 + int(12 * (y / max(H, 1)))
        draw.line([(0, y), (W, y)], fill=(shade, shade, shade + 5, 255))

    # Trójkąty / kształty w tle
    polys = [
        [(480, -40), (720, 40), (620, 180)],
        [(350, 160), (650, 90), (720, 220)],
        [(-30, 30), (180, -50), (120, 220)],
        [(200, 170), (420, 120), (480, 240)],
        [(500, 50), (700, -20), (750, 150)],
    ]
    for pts in polys:
        draw.polygon(pts, fill=(32, 32, 42, 110))

    # Avatar – zaokrąglony kwadrat
    avatar_size = 130
    avatar = Image.open(io.BytesIO(avatar_data)).convert("RGBA").resize((avatar_size, avatar_size))
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, avatar_size - 1, avatar_size - 1], radius=20, fill=255)

    ax, ay = 28, (H - avatar_size) // 2
    # lekki cień
    shadow = Image.new("RGBA", (avatar_size + 10, avatar_size + 10), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([4, 4, avatar_size + 3, avatar_size + 3], radius=20, fill=(0, 0, 0, 110))
    card.paste(shadow, (ax - 2, ay + 3), shadow)
    card.paste(avatar, (ax, ay), mask)

    # Box tekstowy – ciasny, mało pustej przestrzeni
    bx1, by1 = 185, 30
    bx2, by2 = W - 28, H - 30
    draw.rounded_rectangle([bx1, by1, bx2, by2], radius=16, fill=(26, 26, 34, 240))
    draw.rounded_rectangle([bx1, by1, bx2, by2], radius=16, outline=(55, 55, 70, 220), width=2)

    # Fonty – BARDZO duże względem karty
    try:
        font_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 52)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
    except Exception:
        font_name = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    name = str(member.display_name)[:16]

    # Wyśrodkowanie w pionie w boxie
    name_bbox = draw.textbbox((0, 0), name, font=font_name)
    sub_bbox = draw.textbbox((0, 0), "Witaj na serwerze!", font=font_sub)
    name_h = name_bbox[3] - name_bbox[1]
    sub_h = sub_bbox[3] - sub_bbox[1]
    gap = 12
    total_h = name_h + gap + sub_h
    start_y = by1 + (by2 - by1 - total_h) // 2

    draw.text((bx1 + 28, start_y), name, font=font_name, fill=(255, 255, 255, 255))
    draw.text((bx1 + 28, start_y + name_h + gap), "Witaj na serwerze!", font=font_sub, fill=(145, 160, 255, 255))

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
    # Persistent views (działają po restarcie bota)
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    bot.add_view(GiveawayView())
    bot.loop.create_task(giveaway_watcher())
    try:
        synced = await bot.tree.sync()
        print(f"✅ Zalogowano: {bot.user}")
        print(f"✅ Zsynchronizowano komend: {len(synced)}")
        for cmd in synced:
            print(f"   /{cmd.name}")
    except Exception as e:
        print(f"❌ Błąd sync: {e}")


@bot.listen("on_interaction")
async def on_transcript_button(interaction: discord.Interaction):
    """Persistent przycisk transcript w DM (nie nadpisuje innych interakcji)."""
    if interaction.type != discord.InteractionType.component:
        return
    cid = (interaction.data or {}).get("custom_id", "")
    if not cid.startswith("ticket_transcript:"):
        return
    if interaction.response.is_done():
        return
    try:
        tnum = int(cid.split(":")[1])
    except Exception:
        return
    await _send_transcript_file(interaction, tnum)


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
    inviter = None
    invite_code = None
    try:
        invites = await guild.invites()
        for inv in invites:
            if inv.uses > invite_cache[guild.id].get(inv.code, 0):
                inviter = inv.inviter
                invite_code = inv.code
                inviter_id = inviter.id if inviter else 0
                async with aiosqlite.connect("moderation.db") as db:
                    # Usuń stare wpisy tej osoby (np. po rejoin) i wstaw aktualny
                    await db.execute("DELETE FROM invites WHERE invited_id = ?", (member.id,))
                    await db.execute(
                        "INSERT INTO invites (inviter_id, invited_id, code, timestamp) VALUES (?,?,?,?)",
                        (inviter_id, member.id, invite_code, datetime.now(timezone.utc).isoformat()),
                    )
                    await db.commit()
                break
        await update_invite_cache(guild)
    except Exception as e:
        print(f"Błąd trackingu invite: {e}")

    # Log na kanał zaproszeń (tylko aktywne)
    if INVITE_LOG_CHANNEL_ID and inviter:
        log_ch = bot.get_channel(INVITE_LOG_CHANNEL_ID)
        if log_ch:
            try:
                count = await get_active_invite_count(inviter.id)
                word = "zaproszenie" if count == 1 else ("zaproszenia" if 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14) else "zaproszeń")
                await log_ch.send(
                    f"**{member.mention}** został zaproszony przez **{inviter.mention}** i ma teraz **{count}** {word}."
                )
            except Exception as e:
                print(f"Błąd logu invite: {e}")

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
    # Usuń aktywne zaproszenie — licznik invitera spada
    inviter_id = await remove_invite_for_member(member.id)
    if INVITE_LOG_CHANNEL_ID and inviter_id:
        log_ch = bot.get_channel(INVITE_LOG_CHANNEL_ID)
        if log_ch:
            try:
                count = await get_active_invite_count(inviter_id)
                word = "zaproszenie" if count == 1 else ("zaproszenia" if 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14) else "zaproszeń")
                await log_ch.send(
                    f"**{member}** wyszedł z serwera. **<@{inviter_id}>** ma teraz **{count}** {word}."
                )
            except Exception:
                pass

    if GOODBYE_CHANNEL_ID:
        channel = bot.get_channel(GOODBYE_CHANNEL_ID)
        if channel:
            try:
                await channel.send(f"{member.mention} wyszedł z serwera")
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
    duration_human = format_time_human(delta) if delta else "zawsze"
    await notify_user(uzytkownik, interaction.guild, action="banned", color=0xFF0000, reason=powod, duration=duration_human)
    await interaction.guild.ban(uzytkownik, reason=f"{interaction.user} | {powod}" + (f" | {czas}" if czas else ""), delete_message_days=1)
    embed = discord.Embed(title="🔨 Zbanowany", color=0xFF0000, timestamp=datetime.now(timezone.utc))
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
        embed = discord.Embed(title="✅ Odbanowany", color=0x00FF00, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="💨 Softban", color=0xFF4500, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="👢 Wyrzucony", color=0xFFA500, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🔇 Wyciszony", color=0x808080, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🔊 Mute zdjęty", color=0x00FF00, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🔇 Timeout", color=0x808080, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🔊 Timeout zdjęty", color=0x00FF00, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🔇 Text Mute", color=0x808080, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🔊 Text Unmute", color=0x00FF00, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🔇 Voice Mute", color=0x808080, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🔊 Voice Unmute", color=0x00FF00, timestamp=datetime.now(timezone.utc))
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
            (uzytkownik.id, interaction.user.id, powod, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        cur = await db.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (uzytkownik.id,))
        count = (await cur.fetchone())[0]
    embed = discord.Embed(title="⚠️ Ostrzeżenie", color=0xFFFF00, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title=f"Warny — {uzytkownik}", color=0xFFA500, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🧹 Warny wyczyszczone", color=0x00FF00, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title="🧹 Warny usunięte", color=0x00FF00, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title=f"Informacje — {user}", color=user.color or 0x5865F2, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title=f"Informacje — {user}", color=user.color or 0x5865F2, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title=g.name, color=0x5865F2, timestamp=datetime.now(timezone.utc))
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
    embed = discord.Embed(title=g.name, color=0x5865F2, timestamp=datetime.now(timezone.utc))
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
    embed.add_field(name="Tickety", value="`/ticket_setup` `/ticket_close` `/ticket_sync`", inline=False)
    embed.add_field(name="Konkursy", value="`/giveaway` `/giveaway_end` `/giveaway_reroll`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="invite", description="Link do zaproszenia bota")
async def cmd_invite(interaction: discord.Interaction):
    url = discord.utils.oauth_url(bot.user.id, permissions=discord.Permissions(administrator=True))
    await interaction.response.send_message(f"🔗 Zaproś bota:\n{url}")


@bot.tree.command(name="invites", description="Aktywne zaproszenia (osoby nadal na serwerze)")
@app_commands.describe(uzytkownik="Opcjonalnie")
async def cmd_invites(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    target = uzytkownik or interaction.user
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT invited_id, code, timestamp FROM invites WHERE inviter_id = ? ORDER BY timestamp DESC",
            (target.id,),
        )
        rows = await cur.fetchall()
    # Extra safety: tylko osoby, które faktycznie są na serwerze
    active = []
    for invited_id, code, ts in rows:
        if interaction.guild.get_member(invited_id):
            active.append((invited_id, code, ts))
        else:
            # Sprzątanie śmieci (np. stary wpis sprzed update)
            async with aiosqlite.connect("moderation.db") as db:
                await db.execute("DELETE FROM invites WHERE invited_id = ?", (invited_id,))
                await db.commit()
    embed = discord.Embed(
        title=f"📨 Aktywne zaproszenia — {target}",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
        description="Liczone są tylko osoby, które **nadal są** na serwerze.",
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Łącznie (aktywne)", value=str(len(active)), inline=False)
    if active:
        tekst = "\n".join(
            [f"• <@{i}> (`{i}`) — `{c}` • <t:{int(datetime.fromisoformat(t).timestamp())}:R>" for i, c, t in active[:15]]
        )
        embed.add_field(name="Ostatnie", value=tekst, inline=False)
    else:
        embed.add_field(name="Ostatnie", value="Brak aktywnych zaproszeń", inline=False)
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
    now = datetime.now(timezone.utc)
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
    now = datetime.now(timezone.utc)
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


# ===================== TICKETY =====================
def _ticket_staff_role_id() -> int:
    return TICKET_STAFF_ROLE_ID or MOD_ROLE_ID


def _is_ticket_staff(member: discord.Member) -> bool:
    if member.guild and member.id == member.guild.owner_id:
        return True
    if member.guild_permissions.administrator or member.guild_permissions.manage_channels:
        return True
    if MOD_ROLE_ID and any(r.id == MOD_ROLE_ID for r in member.roles):
        return True
    rid = _ticket_staff_role_id()
    if rid and any(r.id == rid for r in member.roles):
        return True
    return False


async def _get_ticket(channel_id: int):
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT user_id, category, closed, claimed_by, created_at, ticket_number FROM tickets WHERE channel_id = ?",
            (channel_id,),
        )
        return await cur.fetchone()


async def _next_ticket_number() -> int:
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute("SELECT COALESCE(MAX(ticket_number), 0) FROM tickets")
        row = await cur.fetchone()
        return (row[0] or 0) + 1


async def _collect_ticket_messages(channel: discord.TextChannel):
    messages = []
    try:
        async for msg in channel.history(limit=500, oldest_first=True):
            messages.append(msg)
    except Exception:
        pass
    return messages


async def _human_messages_only(messages):
    """Tylko wiadomości od ludzi (bez botów), z treścią."""
    out = []
    for msg in messages:
        if msg.author.bot:
            continue
        content = (msg.content or "").strip()
        if not content and not msg.attachments:
            continue
        line = content if content else ""
        if msg.attachments:
            atts = ", ".join(a.filename for a in msg.attachments)
            line = (line + f" [pliki: {atts}]").strip()
        out.append({"author": str(msg.author.display_name), "content": line})
    return out


async def _build_transcript_txt(
    messages,
    *,
    ticket_number: int,
    category: str,
    opener_id: int,
    closer: discord.Member,
    reason: str,
    claimed_by: int,
    created_at: str,
) -> bytes:
    """Czytelny TXT — tylko ludzie, bez dat i bez wiadomości bota."""
    cat_label = category
    for c in TICKET_CATEGORIES:
        if c["value"] == category:
            cat_label = c["label"]
            break
    humans = await _human_messages_only(messages)
    lines = [
        f"=== Przebieg rozmowy ===",
        f"Serwer: {SERVER_NAME}",
        f"Kategoria: {cat_label}",
        f"Powod zamkniecia: {reason or 'Brak'}",
        f"Zamkniety przez: {closer}",
        "-" * 32,
        "",
    ]
    if not humans:
        lines.append("(brak wiadomosci od uzytkownikow)")
    else:
        for h in humans:
            lines.append(f"{h['author']}:")
            lines.append(f"  {h['content']}")
            lines.append("")
    return "\n".join(lines).encode("utf-8")


async def _build_transcript_html(
    messages,
    *,
    ticket_number: int,
    category: str,
    opener_id: int,
    closer: discord.Member,
    reason: str,
    claimed_by: int,
    created_at: str,
) -> bytes:
    """HTML — tylko ludzie, kolory, bez dat."""
    def esc(s: str) -> str:
        return (
            str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        )

    cat_label = category
    for c in TICKET_CATEGORIES:
        if c["value"] == category:
            cat_label = c["label"]
            break

    humans = await _human_messages_only(messages)
    colors = ["#57F287", "#5865F2", "#FEE75C", "#EB459E", "#ED4245", "#00D4FF"]
    rows = []
    for i, h in enumerate(humans):
        col = colors[i % len(colors)]
        rows.append(
            f'<div class="msg" style="border-left:4px solid {col}">'
            f'<div class="name" style="color:{col}">{esc(h["author"])}</div>'
            f'<div class="body">{esc(h["content"])}</div></div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="pl"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Przebieg rozmowy</title>
<style>
body{{margin:0;font-family:system-ui,sans-serif;background:#1e1f22;color:#dbdee1}}
.wrap{{max-width:720px;margin:0 auto;padding:24px}}
h1{{font-size:1.3rem;color:#fff}}
.info{{background:#2b2d31;border-radius:8px;padding:12px 14px;margin-bottom:16px;color:#b5bac1}}
.msg{{background:#2b2d31;border-radius:8px;padding:12px 14px;margin-bottom:10px}}
.name{{font-weight:700;margin-bottom:4px}}
.body{{white-space:pre-wrap;word-break:break-word;line-height:1.45}}
</style></head><body><div class="wrap">
<h1>Przebieg rozmowy</h1>
<div class="info">
Kategoria: <b>{esc(cat_label)}</b><br>
Powód zamknięcia: <b>{esc(reason or "Brak")}</b><br>
Zamknięty przez: <b>{esc(str(closer))}</b>
</div>
{"".join(rows) if rows else "<p>Brak wiadomości od użytkowników.</p>"}
</div></body></html>"""
    return html.encode("utf-8")


async def _close_ticket(
    channel: discord.TextChannel,
    closer: discord.Member,
    reason: str,
):
    """Zamyka ticket, transcript, DM do gracza, archiwum, usuwa kanał."""
    row = await _get_ticket(channel.id)
    if not row:
        return False
    owner_id, category, closed, claimed_by, created_at, ticket_number = row
    if closed:
        return False

    cat_label = category
    for c in TICKET_CATEGORIES:
        if c["value"] == category:
            cat_label = f'{c["emoji"]} {c["label"]}'
            break

    messages = await _collect_ticket_messages(channel)

    txt_bytes = await _build_transcript_txt(
        messages,
        ticket_number=ticket_number or 0,
        category=category,
        opener_id=owner_id,
        closer=closer,
        reason=reason,
        claimed_by=claimed_by or 0,
        created_at=created_at or "?",
    )
    html_bytes = await _build_transcript_html(
        messages,
        ticket_number=ticket_number or 0,
        category=category,
        opener_id=owner_id,
        closer=closer,
        reason=reason,
        claimed_by=claimed_by or 0,
        created_at=created_at or "?",
    )

    async with aiosqlite.connect("moderation.db") as db:
        await db.execute(
            "UPDATE tickets SET closed = 1, close_reason = ? WHERE channel_id = ?",
            (reason, channel.id),
        )
        await db.commit()

    duration_str = "?"
    try:
        opened = datetime.fromisoformat(created_at)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - opened
        mins = int(delta.total_seconds() // 60)
        if mins < 60:
            duration_str = f"{mins} min"
        elif mins < 1440:
            duration_str = f"{mins // 60}h {mins % 60}min"
        else:
            duration_str = f"{mins // 1440}d {(mins % 1440) // 60}h"
    except Exception:
        pass

    # Zapisz transcript do bazy (przycisk w DM)
    try:
        async with aiosqlite.connect("moderation.db") as db:
            await db.execute(
                "INSERT OR REPLACE INTO ticket_transcripts (ticket_number, channel_id, content_txt, created_at) VALUES (?,?,?,?)",
                (
                    ticket_number or channel.id,
                    channel.id,
                    txt_bytes.decode("utf-8", errors="replace"),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception as e:
        print(f"Błąd zapisu transcript: {e}")

    # DM do właściciela ticketa + przycisk "Zobacz przebieg"
    try:
        owner = channel.guild.get_member(owner_id) or await bot.fetch_user(owner_id)
        dm_embed = discord.Embed(
            title="🔒 Twój ticket został zamknięty",
            description=(
                f"**Serwer:** {SERVER_NAME}\n"
                f"**Ticket:** #{ticket_number or '?'}\n"
                f"**Kategoria:** {cat_label}\n"
                f"**Zamknięty przez:** {closer}\n"
                f"**Powód:** {reason or 'Brak powodu'}\n\n"
                f"Kliknij przycisk poniżej, aby zobaczyć przebieg rozmowy."
            ),
            color=0xE74C3C,
            timestamp=datetime.now(timezone.utc),
        )
        dm_embed.set_footer(text="Jeśli masz pytania — otwórz nowy ticket.")
        view = TicketTranscriptView(ticket_number or channel.id)
        await owner.send(embed=dm_embed, view=view)
    except Exception as e:
        print(f"Nie udało się wysłać DM o zamknięciu ticketa: {e}")

    # Archiwum adminów: embed + czytelny TXT + HTML do pobrania
    if TICKET_ARCHIVE_CHANNEL_ID:
        arch = channel.guild.get_channel(TICKET_ARCHIVE_CHANNEL_ID)
        if arch:
            embed = discord.Embed(
                title="Ticket Closed",
                color=0x2ECC71,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Ticket ID", value=str(channel.id), inline=True)
            embed.add_field(name="Ticket Number", value=f"#{ticket_number or '?'}", inline=True)
            embed.add_field(name="Category", value=cat_label, inline=True)
            embed.add_field(name="Opened By", value=f"<@{owner_id}>", inline=True)
            embed.add_field(name="Closed By", value=closer.mention, inline=True)
            embed.add_field(
                name="Claimed By",
                value=f"<@{claimed_by}>" if claimed_by else "Not claimed",
                inline=True,
            )
            try:
                open_ts = int(datetime.fromisoformat(created_at).timestamp())
                embed.add_field(name="Open Time", value=f"<t:{open_ts}:f>", inline=True)
            except Exception:
                embed.add_field(name="Open Time", value=created_at or "?", inline=True)
            embed.add_field(
                name="Close Time",
                value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:f>",
                inline=True,
            )
            embed.add_field(name="Duration", value=duration_str, inline=True)
            embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
            embed.add_field(
                name="📄 Przebieg",
                value=(
                    "• **`.txt`** — otwórz w Discordzie / Notatniku (czytelny tekst)\n"
                    "• **`.html`** — **pobierz** i otwórz w przeglądarce (ładna strona)"
                ),
                inline=False,
            )
            embed.set_footer(text=SERVER_NAME)
            files = [
                discord.File(io.BytesIO(txt_bytes), filename=f"transcript-ticket-{ticket_number or channel.id}.txt"),
                discord.File(io.BytesIO(html_bytes), filename=f"transcript-ticket-{ticket_number or channel.id}.html"),
            ]
            try:
                await arch.send(embed=embed, files=files)
            except Exception as e:
                print(f"Błąd archiwum ticket: {e}")
                try:
                    await arch.send(embed=embed)
                except Exception:
                    pass

    await asyncio.sleep(3)
    try:
        await channel.delete(reason=f"Ticket closed by {closer}: {reason}")
    except Exception:
        pass
    return True


async def _send_transcript_file(interaction: discord.Interaction, tnum: int):
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT content_txt FROM ticket_transcripts WHERE ticket_number = ?",
            (tnum,),
        )
        row = await cur.fetchone()
    if not row or not row[0]:
        return await interaction.response.send_message(
            "❌ Przebieg niedostępny (wygasł lub usunięty).", ephemeral=True
        )
    raw = row[0]
    embeds = []
    colors = [0x57F287, 0x5865F2, 0xFEE75C, 0xEB459E, 0xED4245, 0x00D4FF]
    blocks = []
    current_author = None
    current_lines = []
    skip_prefixes = ("===", "---", "Serwer:", "Kategoria:", "Powod", "Zamkniety", "Powód")
    for line in raw.splitlines():
        if any(line.startswith(p) for p in skip_prefixes) or not line.strip():
            if line.startswith("  ") and current_author is not None:
                current_lines.append(line.strip())
            continue
        if line.endswith(":") and not line.startswith(" "):
            if current_author and current_lines:
                blocks.append((current_author, "\n".join(current_lines)))
            current_author = line[:-1].strip()
            current_lines = []
        elif line.startswith("  ") and current_author is not None:
            current_lines.append(line.strip())
        elif current_author is not None and line.strip():
            current_lines.append(line.strip())
    if current_author and current_lines:
        blocks.append((current_author, "\n".join(current_lines)))

    if not blocks:
        embed = discord.Embed(
            title="📄 Przebieg rozmowy",
            description=(raw[:4000] if raw else "Brak wiadomości."),
            color=0x5865F2,
        )
        return await interaction.response.send_message(embed=embed, ephemeral=True)

    for i, (author, content) in enumerate(blocks[:20]):
        emb = discord.Embed(
            description=f"**{author}**\n{content[:1000]}",
            color=colors[i % len(colors)],
        )
        embeds.append(emb)

    await interaction.response.send_message(
        content="📄 **Przebieg rozmowy** (tylko wiadomości użytkowników):",
        embeds=embeds[:10],
        ephemeral=True,
    )


class TicketTranscriptView(discord.ui.View):
    """Przycisk w DM — działa od razu; po restarcie obsługuje on_interaction."""

    def __init__(self, ticket_number: int = 0):
        super().__init__(timeout=None)
        self.ticket_number = ticket_number
        btn = discord.ui.Button(
            label="Zobacz przebieg ticketa",
            style=discord.ButtonStyle.primary,
            emoji="📄",
            custom_id=f"ticket_transcript:{ticket_number}",
        )

        async def _cb(interaction: discord.Interaction):
            tnum = ticket_number
            cid = (interaction.data or {}).get("custom_id", "")
            if cid.startswith("ticket_transcript:"):
                try:
                    tnum = int(cid.split(":")[1])
                except Exception:
                    pass
            await _send_transcript_file(interaction, tnum)

        btn.callback = _cb
        self.add_item(btn)


class TicketCloseReasonModal(discord.ui.Modal, title="Zamknij ticket z powodem"):
    reason = discord.ui.TextInput(
        label="Powód zamknięcia",
        placeholder="Np. sprawa rozwiązana / spam / brak odpowiedzi...",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        row = await _get_ticket(self.channel.id)
        if not row or row[2]:
            return await interaction.response.send_message("❌ Ticket nieaktywny.", ephemeral=True)
        owner_id = row[0]
        if interaction.user.id != owner_id and not _is_ticket_staff(interaction.user):
            return await interaction.response.send_message("❌ Brak uprawnień.", ephemeral=True)

        reason = str(self.reason)
        await interaction.response.send_message(
            "🔒 Zamykam ticket, generuję transcript i archiwum..."
        )
        await _close_ticket(self.channel, interaction.user, reason)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close with Reason",
        style=discord.ButtonStyle.primary,
        emoji="📝",
        custom_id="ticket_close_reason",
    )
    async def close_reason_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = await _get_ticket(interaction.channel.id)
        if not row:
            return await interaction.response.send_message("❌ To nie jest kanał ticketa.", ephemeral=True)
        owner_id, _, closed, *_ = row
        if closed:
            return await interaction.response.send_message("❌ Ticket już zamknięty.", ephemeral=True)
        if interaction.user.id != owner_id and not _is_ticket_staff(interaction.user):
            return await interaction.response.send_message(
                "❌ Tylko właściciel ticketa lub administracja może zamknąć.", ephemeral=True
            )
        await interaction.response.send_modal(TicketCloseReasonModal(interaction.channel))

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.success,
        emoji="✋",
        custom_id="ticket_claim",
    )
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_ticket_staff(interaction.user):
            return await interaction.response.send_message("❌ Tylko administracja może claimować.", ephemeral=True)
        row = await _get_ticket(interaction.channel.id)
        if not row:
            return await interaction.response.send_message("❌ To nie jest kanał ticketa.", ephemeral=True)
        _, _, closed, claimed_by, *_ = row
        if closed:
            return await interaction.response.send_message("❌ Ticket zamknięty.", ephemeral=True)
        if claimed_by and claimed_by != interaction.user.id:
            return await interaction.response.send_message(
                f"❌ Ticket już claimnięty przez <@{claimed_by}>. Użyj **Unclaim**.", ephemeral=True
            )
        if claimed_by == interaction.user.id:
            return await interaction.response.send_message("✅ Już claimnąłeś ten ticket.", ephemeral=True)

        async with aiosqlite.connect("moderation.db") as db:
            await db.execute(
                "UPDATE tickets SET claimed_by = ? WHERE channel_id = ?",
                (interaction.user.id, interaction.channel.id),
            )
            await db.commit()

        embed = discord.Embed(
            title="✋ Ticket claimnięty",
            description=f"{interaction.user.mention} przejął ten ticket.",
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(embed=embed)
        try:
            name = interaction.channel.name
            if name.startswith("ticket-") and not name.startswith("claimed-"):
                await interaction.channel.edit(name=f"claimed-{name[7:]}")
        except Exception:
            pass

    @discord.ui.button(
        label="Unclaim",
        style=discord.ButtonStyle.secondary,
        emoji="🔓",
        custom_id="ticket_unclaim",
    )
    async def unclaim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_ticket_staff(interaction.user):
            return await interaction.response.send_message("❌ Tylko administracja może unclaimować.", ephemeral=True)
        row = await _get_ticket(interaction.channel.id)
        if not row:
            return await interaction.response.send_message("❌ To nie jest kanał ticketa.", ephemeral=True)
        _, _, closed, claimed_by, *_ = row
        if closed:
            return await interaction.response.send_message("❌ Ticket zamknięty.", ephemeral=True)
        if not claimed_by:
            return await interaction.response.send_message("❌ Ticket nie jest claimnięty.", ephemeral=True)
        if claimed_by != interaction.user.id and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                f"❌ Ticket claimnięty przez <@{claimed_by}>.", ephemeral=True
            )

        async with aiosqlite.connect("moderation.db") as db:
            await db.execute(
                "UPDATE tickets SET claimed_by = 0 WHERE channel_id = ?",
                (interaction.channel.id,),
            )
            await db.commit()

        embed = discord.Embed(
            title="🔓 Ticket unclaimnięty",
            description=f"{interaction.user.mention} oddał ticket.",
            color=0x95A5A6,
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(embed=embed)
        try:
            name = interaction.channel.name
            if name.startswith("claimed-"):
                await interaction.channel.edit(name=f"ticket-{name[8:]}")
        except Exception:
            pass


class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=c["label"],
                value=c["value"],
                emoji=c["emoji"],
                description=c["desc"],
            )
            for c in TICKET_CATEGORIES
        ]
        super().__init__(
            placeholder="Kliknij aby wybrać kategorię ticketa",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_category_select",
        )

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        cat_info = next((c for c in TICKET_CATEGORIES if c["value"] == category), None)
        cat_label = cat_info["label"] if cat_info else category
        cat_emoji = cat_info["emoji"] if cat_info else "🎫"

        async with aiosqlite.connect("moderation.db") as db:
            cur = await db.execute(
                "SELECT channel_id FROM tickets WHERE user_id = ? AND closed = 0",
                (interaction.user.id,),
            )
            existing = await cur.fetchone()
        if existing:
            ch = interaction.guild.get_channel(existing[0])
            if ch:
                return await interaction.response.send_message(
                    f"❌ Masz już otwarty ticket: {ch.mention}", ephemeral=True
                )
            async with aiosqlite.connect("moderation.db") as db:
                await db.execute("UPDATE tickets SET closed = 1 WHERE channel_id = ?", (existing[0],))
                await db.commit()

        await interaction.response.defer(ephemeral=True)

        staff_role_id = _ticket_staff_role_id()
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=True,
                embed_links=True,
                read_message_history=True,
            ),
            interaction.guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }
        if staff_role_id:
            role = interaction.guild.get_role(staff_role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    attach_files=True,
                    manage_messages=True,
                    read_message_history=True,
                )

        category_obj = None
        if TICKET_CATEGORY_ID:
            category_obj = interaction.guild.get_channel(TICKET_CATEGORY_ID)
            if not isinstance(category_obj, discord.CategoryChannel):
                category_obj = None

        tnum = await _next_ticket_number()
        safe_name = re.sub(r"[^a-zA-Z0-9\-]", "", interaction.user.name.lower())[:16] or "user"
        # Nazwa prosta dla wszystkich: ticket-nick (numer+kategoria tylko w topic — admini widzą)
        channel_name = f"ticket-{safe_name}"

        try:
            ticket_ch = await interaction.guild.create_text_channel(
                name=channel_name[:90],
                overwrites=overwrites,
                category=category_obj,
                topic=f"#{tnum} | {cat_label} | {interaction.user} ({interaction.user.id})",
                reason=f"Ticket #{tnum}: {cat_label} — {interaction.user}",
            )
        except Exception as e:
            return await interaction.followup.send(f"❌ Nie udało się utworzyć ticketa: `{e}`", ephemeral=True)

        async with aiosqlite.connect("moderation.db") as db:
            await db.execute(
                "INSERT INTO tickets (channel_id, user_id, category, created_at, closed, claimed_by, ticket_number) VALUES (?,?,?,?,0,0,?)",
                (ticket_ch.id, interaction.user.id, category, datetime.now(timezone.utc).isoformat(), tnum),
            )
            await db.commit()

        embed = discord.Embed(
            title="🎫 Witaj w swoim tickecie!",
            description=(
                f"Witam {interaction.user.mention}, opisz dokładnie swój problem lub pytanie. "
                f"Administracja odpowie najszybciej jak to możliwe!\n\n"
                f"**Ticket:** #{tnum}\n"
                f"**Kategoria:** {cat_emoji} {cat_label}"
            ),
            color=0xF1C40F,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="📌 Pamiętaj",
            value=(
                "► **Cierpliwość:** maksymalny czas odpowiedzi to **24 godziny**.\n"
                "► **Nie oznaczaj** zarządu (Właścicieli/Developerów).\n"
                "► Zamknij ticket przyciskiem **Close with Reason** gdy sprawa załatwiona."
            ),
            inline=False,
        )
        embed.set_footer(text=f"{SERVER_NAME} • Ticket #{tnum}")

        staff_ping = f"<@&{staff_role_id}>" if staff_role_id else ""
        await ticket_ch.send(
            content=f"{interaction.user.mention} {staff_ping}".strip(),
            embed=embed,
            view=TicketControlView(),
        )

        await interaction.followup.send(
            f"✅ Utworzono ticket: {ticket_ch.mention}", ephemeral=True
        )

        try:
            if interaction.message:
                await interaction.message.edit(view=TicketPanelView())
        except Exception:
            pass


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


@bot.tree.command(name="ticket_setup", description="Wyślij panel ticketów na ten kanał (działa na każdym kanale)")
@is_mod()
async def cmd_ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✉️ Kontakt z administracją",
        description=(
            "**Jeśli chcesz kupić WiciaClient, potrzebujesz pomocy technicznej lub masz ofertę współpracy — otwórz ticket.**\n\n"
            "Wybierz kategorię z menu poniżej, aby otworzyć ticketa.\n\n"
            "**Dostępne kategorie:**\n"
            "🛒 **Zakup** — produkt / usługa\n"
            "🆘 **Pomoc** — wsparcie techniczne\n"
            "🤝 **Współpraca** — oferty partnerskie\n"
            "❓ **Inne** — pozostałe sprawy"
        ),
        color=0x5865F2,
    )
    embed.add_field(
        name="📌 Zasady",
        value=(
            "► **Cierpliwość:** odpowiadamy w ciągu **24 godzin**.\n"
            "► **Nie oznaczaj** Właścicieli/Developerów.\n"
            "► Jeden otwarty ticket na osobę."
        ),
        inline=False,
    )
    embed.set_footer(text="Kliknij aby wybrać kategorię ticketa")
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("✅ Panel ticketów wysłany na ten kanał.", ephemeral=True)


@bot.tree.command(name="ticket_close", description="Zamknij ticket z powodem (w kanale ticketa)")
async def cmd_ticket_close(interaction: discord.Interaction):
    row = await _get_ticket(interaction.channel.id)
    if not row:
        return await interaction.response.send_message("❌ To nie jest kanał ticketa.", ephemeral=True)
    owner_id, _, closed, *_ = row
    if closed:
        return await interaction.response.send_message("❌ Ticket już zamknięty.", ephemeral=True)
    if interaction.user.id != owner_id and not _is_ticket_staff(interaction.user):
        return await interaction.response.send_message("❌ Brak uprawnień.", ephemeral=True)
    await interaction.response.send_modal(TicketCloseReasonModal(interaction.channel))


@bot.tree.command(name="ticket_sync", description="Wymuś synchronizację komend ticketów")
@is_mod()
async def cmd_ticket_sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        synced = await bot.tree.sync()
        names = [c.name for c in synced if "ticket" in c.name]
        await interaction.followup.send(
            f"✅ Zsynchronizowano **{len(synced)}** komend.\nTickety: {', '.join(f'`/{n}`' for n in names) or 'brak'}",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd sync: `{e}`", ephemeral=True)



# ===================== GIVEAWAY / KONKURS =====================
def parse_end_datetime(s: str) -> Optional[datetime]:
    """Parsuje datę zakończenia: '18:00 28.09.2026' lub '28.09.2026 18:00' lub ISO."""
    s = (s or "").strip()
    formats = [
        "%H:%M %d.%m.%Y",
        "%d.%m.%Y %H:%M",
        "%H:%M %d/%m/%Y",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            # zakładamy czas lokalny PL (CEST/CET) → UTC-ish: traktujemy jako Europe/Warsaw ~ UTC+2 latem
            # prosty offset +2h na CEST (wystarczy do konkursów)
            dt = dt.replace(tzinfo=timezone(timedelta(hours=2)))
            return dt
        except ValueError:
            continue
    return None


async def _giveaway_entry_count(gid: int) -> int:
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = ?", (gid,)
        )
        row = await cur.fetchone()
        return row[0] if row else 0


async def _build_giveaway_embed(
    *,
    prize: str,
    end_at: datetime,
    winners_count: int,
    participants: int,
    host: discord.abc.User,
    ended: bool = False,
    winners_mentions: str = None,
) -> discord.Embed:
    end_ts = int(end_at.timestamp())
    if ended:
        embed = discord.Embed(
            title="🎉 KONKURS ZAKOŃCZONY",
            description=f"**Nagroda:** {prize}",
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Zwycięzcy", value=winners_mentions or "Brak uczestników", inline=False)
        embed.add_field(name="Uczestnicy", value=str(participants), inline=True)
        embed.add_field(name="Host", value=host.mention if hasattr(host, "mention") else str(host), inline=True)
    else:
        embed = discord.Embed(
            title="🎉 KONKURS",
            description=f"**Nagroda:** {prize}\n\nKliknij **Dołącz**, aby wziąć udział!",
            color=0x9B59B6,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Ends", value=f"<t:{end_ts}:F> (<t:{end_ts}:R>)", inline=False)
        embed.add_field(name="Winners", value=str(winners_count), inline=True)
        embed.add_field(name="Participants", value=str(participants), inline=True)
        embed.add_field(name="Host", value=host.mention if hasattr(host, "mention") else str(host), inline=True)
        embed.set_footer(text="Kliknij Dołącz • Konkurs")
    return embed


async def _end_giveaway(gid: int):
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT channel_id, message_id, prize, winners_count, end_at, host_id, ended FROM giveaways WHERE id = ?",
            (gid,),
        )
        row = await cur.fetchone()
        if not row or row[6]:
            return
        channel_id, message_id, prize, winners_count, end_at, host_id, _ = row
        await db.execute("UPDATE giveaways SET ended = 1 WHERE id = ?", (gid,))
        cur2 = await db.execute(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (gid,)
        )
        entries = [r[0] for r in await cur2.fetchall()]
        await db.commit()

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    participants = len(entries)
    winners = []
    if entries:
        k = min(winners_count, len(entries))
        winners = random.sample(entries, k)

    # Nadaj rolę Klient
    role = None
    if GIVEAWAY_WIN_ROLE_ID and channel.guild:
        role = channel.guild.get_role(GIVEAWAY_WIN_ROLE_ID)
    for uid in winners:
        member = channel.guild.get_member(uid) if channel.guild else None
        if member and role:
            try:
                await member.add_roles(role, reason=f"Wygrana w konkursie #{gid}")
            except Exception:
                pass

    host = bot.get_user(host_id) or await bot.fetch_user(host_id)
    end_dt = datetime.fromisoformat(end_at)
    winners_mentions = ", ".join(f"<@{u}>" for u in winners) if winners else "Brak uczestników"
    embed = await _build_giveaway_embed(
        prize=prize,
        end_at=end_dt,
        winners_count=winners_count,
        participants=participants,
        host=host,
        ended=True,
        winners_mentions=winners_mentions,
    )

    view = discord.ui.View()  # puste — bez przycisku
    try:
        msg = await channel.fetch_message(message_id)
        await msg.edit(embed=embed, view=view)
    except Exception:
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    if winners:
        await channel.send(
            f"🎉 **Konkurs zakończony!**\nNagroda: **{prize}**\nZwycięzcy: {winners_mentions}"
            + (f"\nRola {role.mention} nadana automatycznie." if role else "")
        )
    else:
        await channel.send("🎉 Konkurs zakończony — brak uczestników.")


async def giveaway_watcher():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = datetime.now(timezone.utc).isoformat()
            async with aiosqlite.connect("moderation.db") as db:
                cur = await db.execute(
                    "SELECT id, end_at FROM giveaways WHERE ended = 0"
                )
                rows = await cur.fetchall()
            for gid, end_at in rows:
                try:
                    end_dt = datetime.fromisoformat(end_at)
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) >= end_dt:
                        await _end_giveaway(gid)
                except Exception as e:
                    print(f"Giveaway end error {gid}: {e}")
        except Exception as e:
            print(f"Giveaway watcher: {e}")
        await asyncio.sleep(20)


class GiveawayJoinButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Dołącz",
            style=discord.ButtonStyle.primary,
            emoji="🎉",
            custom_id="giveaway_join",
        )

    async def callback(self, interaction: discord.Interaction):
        # Znajdź giveaway po message_id
        async with aiosqlite.connect("moderation.db") as db:
            cur = await db.execute(
                "SELECT id, ended, prize, winners_count, end_at, host_id FROM giveaways WHERE message_id = ?",
                (interaction.message.id,),
            )
            row = await cur.fetchone()
        if not row:
            return await interaction.response.send_message("❌ Ten konkurs już nie istnieje.", ephemeral=True)
        gid, ended, prize, winners_count, end_at, host_id = row
        if ended:
            return await interaction.response.send_message("❌ Konkurs już zakończony.", ephemeral=True)

        async with aiosqlite.connect("moderation.db") as db:
            cur = await db.execute(
                "SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
                (gid, interaction.user.id),
            )
            already = await cur.fetchone()
            if already:
                return await interaction.response.send_message("✅ Już dołączyłeś do tego konkursu!", ephemeral=True)
            await db.execute(
                "INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
                (gid, interaction.user.id),
            )
            await db.commit()

        count = await _giveaway_entry_count(gid)
        end_dt = datetime.fromisoformat(end_at)
        host = interaction.guild.get_member(host_id) or interaction.user
        embed = await _build_giveaway_embed(
            prize=prize,
            end_at=end_dt,
            winners_count=winners_count,
            participants=count,
            host=host,
        )
        try:
            await interaction.message.edit(embed=embed, view=GiveawayView())
        except Exception:
            pass
        await interaction.response.send_message(
            f"✅ Dołączyłeś do konkursu! Uczestników: **{count}**", ephemeral=True
        )


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GiveawayJoinButton())


@bot.tree.command(name="giveaway", description="Utwórz konkurs (giveaway)")
@app_commands.describe(
    nagroda="Co można wygrać (np. WiciaClient 30 dni)",
    koniec="Data końca np. 18:00 28.09.2026",
    wygrani="Ilu zwycięzców (domyślnie 1)",
    kanal="Kanał z konkursem (domyślnie ten)",
)
@is_mod()
async def cmd_giveaway(
    interaction: discord.Interaction,
    nagroda: str,
    koniec: str,
    wygrani: app_commands.Range[int, 1, 20] = 1,
    kanal: Optional[discord.TextChannel] = None,
):
    end_dt = parse_end_datetime(koniec)
    if not end_dt:
        return await interaction.response.send_message(
            "❌ Zła data. Przykłady: `18:00 28.09.2026` albo `28.09.2026 18:00`",
            ephemeral=True,
        )
    if end_dt <= datetime.now(timezone(timedelta(hours=2))):
        return await interaction.response.send_message("❌ Data zakończenia musi być w przyszłości.", ephemeral=True)

    channel = kanal or interaction.channel
    await interaction.response.defer(ephemeral=True)

    embed = await _build_giveaway_embed(
        prize=nagroda,
        end_at=end_dt,
        winners_count=wygrani,
        participants=0,
        host=interaction.user,
    )
    msg = await channel.send(embed=embed, view=GiveawayView())

    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "INSERT INTO giveaways (channel_id, message_id, prize, winners_count, end_at, host_id, ended) VALUES (?,?,?,?,?,?,0)",
            (
                channel.id,
                msg.id,
                nagroda,
                wygrani,
                end_dt.astimezone(timezone.utc).isoformat(),
                interaction.user.id,
            ),
        )
        await db.commit()

    await interaction.followup.send(
        f"✅ Konkurs utworzony na {channel.mention}\n"
        f"**Nagroda:** {nagroda}\n"
        f"**Koniec:** <t:{int(end_dt.timestamp())}:F>\n"
        f"**Zwycięzców:** {wygrani}",
        ephemeral=True,
    )


@bot.tree.command(name="giveaway_end", description="Zakończ konkurs ręcznie (podaj ID wiadomości)")
@app_commands.describe(message_id="ID wiadomości konkursu")
@is_mod()
async def cmd_giveaway_end(interaction: discord.Interaction, message_id: str):
    try:
        mid = int(message_id.strip())
    except ValueError:
        return await interaction.response.send_message("❌ Podaj liczbowe ID wiadomości.", ephemeral=True)
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT id, ended FROM giveaways WHERE message_id = ?", (mid,)
        )
        row = await cur.fetchone()
    if not row:
        return await interaction.response.send_message("❌ Nie znaleziono konkursu.", ephemeral=True)
    if row[1]:
        return await interaction.response.send_message("❌ Konkurs już zakończony.", ephemeral=True)
    await interaction.response.send_message("⏳ Kończę konkurs...", ephemeral=True)
    await _end_giveaway(row[0])


@bot.tree.command(name="giveaway_reroll", description="Wylosuj ponownie zwycięzcę konkursu")
@app_commands.describe(message_id="ID wiadomości konkursu", ilosc="Ilu nowych zwycięzców")
@is_mod()
async def cmd_giveaway_reroll(
    interaction: discord.Interaction,
    message_id: str,
    ilosc: app_commands.Range[int, 1, 20] = 1,
):
    try:
        mid = int(message_id.strip())
    except ValueError:
        return await interaction.response.send_message("❌ Podaj liczbowe ID wiadomości.", ephemeral=True)
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT id, prize, ended FROM giveaways WHERE message_id = ?", (mid,)
        )
        row = await cur.fetchone()
        if not row:
            return await interaction.response.send_message("❌ Nie znaleziono konkursu.", ephemeral=True)
        gid, prize, ended = row
        if not ended:
            return await interaction.response.send_message("❌ Najpierw zakończ konkurs.", ephemeral=True)
        cur = await db.execute(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (gid,)
        )
        entries = [r[0] for r in await cur.fetchall()]
    if not entries:
        return await interaction.response.send_message("❌ Brak uczestników.", ephemeral=True)
    winners = random.sample(entries, min(ilosc, len(entries)))
    mentions = ", ".join(f"<@{u}>" for u in winners)

    role = None
    if GIVEAWAY_WIN_ROLE_ID:
        role = interaction.guild.get_role(GIVEAWAY_WIN_ROLE_ID)
    for uid in winners:
        m = interaction.guild.get_member(uid)
        if m and role:
            try:
                await m.add_roles(role, reason="Giveaway reroll")
            except Exception:
                pass

    await interaction.response.send_message(
        f"🎲 **Reroll!** Nagroda: **{prize}**\nNowi zwycięzcy: {mentions}"
    )



# ===================== START =====================
if __name__ == "__main__":
    if not TOKEN:
        print("❌ Brak TOKEN w zmiennych środowiskowych!")
    else:
        bot.run(TOKEN)
