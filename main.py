import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import os
import re
import aiohttp
import io
from datetime import datetime, timedelta
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

# ================== KONFIGURACJA ==================
TOKEN = os.getenv("TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID", "0"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0"))
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0"))
# ==================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.invites = True

bot = commands.Bot(command_prefix="!", intents=intents)
invite_cache = {}

def parse_time(time_str: str) -> Optional[timedelta]:
    time_str = time_str.lower().strip()
    match = re.match(r"^(\d+)(s|m|h|d|w|mo)$", time_str)
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    return {
        "s": timedelta(seconds=value),
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
        "w": timedelta(weeks=value),
        "mo": timedelta(days=value * 30)
    }.get(unit)

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
        async with session.get(str(member.display_avatar.replace(size=256))) as resp:
            avatar_data = await resp.read()

    avatar = Image.open(io.BytesIO(avatar_data)).convert("RGBA").resize((180, 180))
    card = Image.new("RGBA", (800, 300), (20, 20, 25, 255))
    draw = ImageDraw.Draw(card)

    mask = Image.new("L", (180, 180), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 180, 180), fill=255)
    card.paste(avatar, (50, 60), mask)

    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((260, 80), "Siema", font=font_big, fill=(255, 255, 255))
    draw.text((260, 150), str(member.display_name), font=font_small, fill=(180, 180, 255))

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
    # Zaproszenia
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

    # Powitanie
    if WELCOME_CHANNEL_ID:
        channel = bot.get_channel(WELCOME_CHANNEL_ID)
        if channel:
            try:
                file = await create_welcome_card(member)
                await channel.send(file=file)
                await channel.send(f"Witamy {member.mention} na **WiciaClient 20PLN**")
            except Exception as e:
                print(f"Błąd powitania: {e}")

    # Rola Zweryfikowany
    if VERIFIED_ROLE_ID:
        role = member.guild.get_role(VERIFIED_ROLE_ID)
        if role:
            try:
                await member.add_roles(role, reason="Automatyczna weryfikacja")
            except Exception as e:
                print(f"Nie udało się dać roli: {e}")

@bot.event
async def on_member_remove(member: discord.Member):
    if GOODBYE_CHANNEL_ID:
        channel = bot.get_channel(GOODBYE_CHANNEL_ID)
        if channel:
            await channel.send(f"**{member}** wyszedł z serwera")

# ====================== BANY ======================

@bot.tree.command(name="ban", description="Zbanuj na stałe")
@app_commands.describe(uzytkownik="Kogo", powod="Powód", usun_wiadomosci="0-7 dni")
@is_mod()
async def ban(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu", usun_wiadomosci: app_commands.Range[int, 0, 7] = 0):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    await uzytkownik.ban(reason=f"{interaction.user} | {powod}", delete_message_days=usun_wiadomosci)
    embed = discord.Embed(title="🔨 Zbanowany", color=0xFF0000, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

@bot.tree.command(name="tempban", description="Tymczasowy ban")
@app_commands.describe(uzytkownik="Kogo", czas="10m / 2h / 1d / 1w / 1mo", powod="Powód")
@is_mod()
async def tempban(interaction: discord.Interaction, uzytkownik: discord.Member, czas: str, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    delta = parse_time(czas)
    if not delta:
        return await interaction.response.send_message("❌ Zły format czasu.", ephemeral=True)
    await uzytkownik.ban(reason=f"{interaction.user} | {powod} | {czas}")
    embed = discord.Embed(title="⏰ Tempban", color=0x8B0000, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Czas", value=czas, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)
    await discord.utils.sleep_until(datetime.utcnow() + delta)
    try:
        await interaction.guild.unban(uzytkownik, reason="Koniec tempbana")
        await send_log(discord.Embed(title="✅ Tempban zakończony", description=f"{uzytkownik} odbanowany", color=0x00FF00))
    except:
        pass

@bot.tree.command(name="softban", description="Softban (czyści wiadomości)")
@app_commands.describe(uzytkownik="Kogo", powod="Powód")
@is_mod()
async def softban(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    await uzytkownik.ban(reason=f"Softban | {interaction.user} | {powod}", delete_message_days=7)
    await interaction.guild.unban(uzytkownik, reason="Softban")
    embed = discord.Embed(title="💨 Softban", color=0xFF4500, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

@bot.tree.command(name="unban", description="Odbanuj")
@app_commands.describe(uzytkownik_id="ID użytkownika", powod="Powód")
@is_mod()
async def unban(interaction: discord.Interaction, uzytkownik_id: str, powod: str = "Brak powodu"):
    try:
        user = await bot.fetch_user(int(uzytkownik_id))
        await interaction.guild.unban(user, reason=f"{interaction.user} | {powod}")
        embed = discord.Embed(title="✅ Odbanowany", color=0x00FF00, timestamp=datetime.utcnow())
        embed.add_field(name="Użytkownik", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Powód", value=powod, inline=False)
        await interaction.response.send_message(embed=embed)
        await send_log(embed)
    except:
        await interaction.response.send_message("❌ Nie znaleziono lub nie jest zbanowany.", ephemeral=True)

# ====================== KICK / MUTE ======================

@bot.tree.command(name="kick", description="Wyrzuć")
@app_commands.describe(uzytkownik="Kogo", powod="Powód")
@is_mod()
async def kick(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    await uzytkownik.kick(reason=f"{interaction.user} | {powod}")
    embed = discord.Embed(title="👢 Wyrzucony", color=0xFFA500, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

@bot.tree.command(name="mute", description="Timeout")
@app_commands.describe(uzytkownik="Kogo", czas="10m / 2h / 1d / 1w", powod="Powód")
@is_mod()
async def mute(interaction: discord.Interaction, uzytkownik: discord.Member, czas: str, powod: str = "Brak powodu"):
    if uzytkownik.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message("❌ Za niska rola.", ephemeral=True)
    delta = parse_time(czas)
    if not delta or delta.total_seconds() > 2419200:
        return await interaction.response.send_message("❌ Zły czas (max 28 dni).", ephemeral=True)
    await uzytkownik.timeout(delta, reason=f"{interaction.user} | {powod}")
    embed = discord.Embed(title="🔇 Wyciszony", color=0x808080, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Czas", value=czas, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

@bot.tree.command(name="unmute", description="Zdejmij timeout")
@app_commands.describe(uzytkownik="Komu")
@is_mod()
async def unmute(interaction: discord.Interaction, uzytkownik: discord.Member):
    await uzytkownik.timeout(None)
    embed = discord.Embed(title="🔊 Mute zdjęty", color=0x00FF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

# ====================== WARNY ======================

@bot.tree.command(name="warn", description="Ostrzeżenie")
@app_commands.describe(uzytkownik="Kogo", powod="Powód")
@is_mod()
async def warn(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str):
    async with aiosqlite.connect("moderation.db") as db:
        await db.execute("INSERT INTO warnings (user_id, moderator_id, reason, timestamp) VALUES (?,?,?,?)",
                         (uzytkownik.id, interaction.user.id, powod, datetime.utcnow().isoformat()))
        await db.commit()
        cur = await db.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (uzytkownik.id,))
        count = (await cur.fetchone())[0]
    embed = discord.Embed(title="⚠️ Ostrzeżenie", color=0xFFFF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    embed.add_field(name="Ilość", value=str(count), inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)
    try:
        await uzytkownik.send(f"**Warn** na **{interaction.guild.name}**\nPowód: {powod}\nŁącznie: **{count}**")
    except:
        pass

@bot.tree.command(name="warnings", description="Lista warnów")
@app_commands.describe(uzytkownik="Kogo")
@is_mod()
async def warnings(interaction: discord.Interaction, uzytkownik: discord.Member):
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute("SELECT reason, timestamp, moderator_id FROM warnings WHERE user_id = ? ORDER BY id DESC", (uzytkownik.id,))
        rows = await cur.fetchall()
    if not rows:
        return await interaction.response.send_message(f"{uzytkownik.mention} nie ma warnów.", ephemeral=True)
    embed = discord.Embed(title=f"Warny — {uzytkownik}", color=0xFFA500, timestamp=datetime.utcnow())
    for i, (reason, ts, mod) in enumerate(rows[:12], 1):
        embed.add_field(name=f"#{i} • <t:{int(datetime.fromisoformat(ts).timestamp())}:R>",
                        value=f"{reason}\nMod: <@{mod}>", inline=False)
    embed.set_footer(text=f"Łącznie: {len(rows)}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clearwarns", description="Wyczyść warny")
@app_commands.describe(uzytkownik="Kogo")
@is_mod()
async def clearwarns(interaction: discord.Interaction, uzytkownik: discord.Member):
    async with aiosqlite.connect("moderation.db") as db:
        await db.execute("DELETE FROM warnings WHERE user_id = ?", (uzytkownik.id,))
        await db.commit()
    embed = discord.Embed(title="🧹 Warny wyczyszczone", color=0x00FF00, timestamp=datetime.utcnow())
    embed.add_field(name="Użytkownik", value=uzytkownik.mention, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

# ====================== WIADOMOŚCI / KANAŁ ======================

@bot.tree.command(name="clear", description="Usuń wiadomości")
@app_commands.describe(ilosc="1-100")
@is_mod()
async def clear(interaction: discord.Interaction, ilosc: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=ilosc)
    embed = discord.Embed(title="🧹 Usunięto", description=f"**{len(deleted)}** wiadomości", color=0x3498DB)
    await interaction.followup.send(embed=embed, ephemeral=True)
    await send_log(embed)

@bot.tree.command(name="slowmode", description="Ustaw slowmode")
@app_commands.describe(sekundy="0 = wyłącz, max 21600")
@is_mod()
async def slowmode(interaction: discord.Interaction, sekundy: app_commands.Range[int, 0, 21600]):
    await interaction.channel.edit(slowmode_delay=sekundy)
    embed = discord.Embed(title="🐌 Slowmode", description=f"Ustawiono na **{sekundy}s**", color=0x9B59B6)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

@bot.tree.command(name="lock", description="Zablokuj kanał")
@is_mod()
async def lock(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    embed = discord.Embed(title="🔒 Kanał zablokowany", color=0xE74C3C)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

@bot.tree.command(name="unlock", description="Odblokuj kanał")
@is_mod()
async def unlock(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    embed = discord.Embed(title="🔓 Kanał odblokowany", color=0x2ECC71)
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

# ====================== USER / SERVER ======================

@bot.tree.command(name="nick", description="Zmień nick")
@app_commands.describe(uzytkownik="Kogo", nowy_nick="Nowy nick (pusty = reset)")
@is_mod()
async def nick(interaction: discord.Interaction, uzytkownik: discord.Member, nowy_nick: str = None):
    stary = uzytkownik.display_name
    await uzytkownik.edit(nick=nowy_nick)
    embed = discord.Embed(title="📝 Nick zmieniony", color=0x1ABC9C)
    embed.add_field(name="Użytkownik", value=uzytkownik.mention)
    embed.add_field(name="Stary", value=stary)
    embed.add_field(name="Nowy", value=nowy_nick or "zresetowany")
    await interaction.response.send_message(embed=embed)
    await send_log(embed)

@bot.tree.command(name="userinfo", description="Informacje o użytkowniku")
@app_commands.describe(uzytkownik="Kogo (domyślnie Ty)")
async def userinfo(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    user = uzytkownik or interaction.user
    embed = discord.Embed(title=f"Informacje — {user}", color=user.color or 0x5865F2, timestamp=datetime.utcnow())
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="ID", value=user.id, inline=True)
    embed.add_field(name="Nick", value=user.display_name, inline=True)
    embed.add_field(name="Konto utworzone", value=f"<t:{int(user.created_at.timestamp())}:R>", inline=False)
    embed.add_field(name="Dołączył", value=f"<t:{int(user.joined_at.timestamp())}:R>" if user.joined_at else "?", inline=False)
    roles = [r.mention for r in user.roles if r != interaction.guild.default_role]
    embed.add_field(name=f"Role ({len(roles)})", value=" ".join(roles[:15]) or "Brak", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="serverinfo", description="Informacje o serwerze")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=0x5865F2, timestamp=datetime.utcnow())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Właściciel", value=g.owner.mention if g.owner else "?", inline=True)
    embed.add_field(name="ID", value=g.id, inline=True)
    embed.add_field(name="Utworzono", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=False)
    embed.add_field(name="Członkowie", value=g.member_count, inline=True)
    embed.add_field(name="Kanały", value=len(g.channels), inline=True)
    embed.add_field(name="Role", value=len(g.roles), inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="invites", description="Kto kogo zaprosił")
@app_commands.describe(uzytkownik="Opcjonalnie – czyje zaproszenia sprawdzić")
async def invites(interaction: discord.Interaction, uzytkownik: Optional[discord.Member] = None):
    target = uzytkownik or interaction.user
    async with aiosqlite.connect("moderation.db") as db:
        cur = await db.execute(
            "SELECT invited_id, code, timestamp FROM invites WHERE inviter_id = ? ORDER BY timestamp DESC",
            (target.id,)
        )
        rows = await cur.fetchall()

    embed = discord.Embed(title=f"📨 Zaproszenia — {target}", color=0x5865F2, timestamp=datetime.utcnow())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Łącznie zaproszonych", value=str(len(rows)), inline=False)

    if rows:
        tekst = ""
        for invited_id, code, ts in rows[:15]:
            tekst += f"• <@{invited_id}> (`{invited_id}`) — `{code}` • <t:{int(datetime.fromisoformat(ts).timestamp())}:R>\n"
        embed.add_field(name="Ostatnie zaproszenia", value=tekst, inline=False)
    else:
        embed.add_field(name="Ostatnie zaproszenia", value="Brak danych", inline=False)

    embed.set_footer(text="Kliknij @wzmiankę żeby wejść na profil")
    await interaction.response.send_message(embed=embed)

# ====================== START ======================
bot.run(TOKEN)