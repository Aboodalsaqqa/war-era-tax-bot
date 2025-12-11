# main.py — War Era Tax Bot (old full code) with updated dashboard
import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from datetime import date, datetime

# ================== CONFIG ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
# If you want commands to register instantly for testing, set your guild id(s) here:
GUILD_IDS = None  # e.g. [123456789012345678]
# Optional: restrict admin checks to a specific role (put role ID) or leave None
ADMIN_ROLE_ID = None  # e.g. 987654321012345678
# Optional: channel ID where logs/dashboards are posted (or None)
LOG_CHANNEL_ID = None  # e.g. 234567890123456789
# Database file
DB_FILE = "tax_bot.db"
# ============================================

# Intents: do NOT request message_content or privileged intents
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ----------------- Database helpers -----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # players table
    c.execute('''
    CREATE TABLE IF NOT EXISTS players (
        discord_id TEXT PRIMARY KEY,
        name TEXT,
        level INTEGER,
        factories INTEGER,
        last_paid_date TEXT,
        last_paid_amount REAL
    )''')
    # payments table (history)
    c.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discord_id TEXT,
        payer_name TEXT,
        amount REAL,
        proof TEXT,
        admin_name TEXT,
        timestamp TEXT
    )''')
    # bot admins table (users allowed to use admin commands via bot)
    c.execute('''
    CREATE TABLE IF NOT EXISTS bot_admins (
        discord_id TEXT PRIMARY KEY
    )''')
    conn.commit()
    conn.close()

def upsert_player(discord_id, name, level, factories):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
    INSERT INTO players(discord_id,name,level,factories,last_paid_date,last_paid_amount)
    VALUES(?,?,?,?,?,?)
    ON CONFLICT(discord_id) DO UPDATE SET
      name=excluded.name,
      level=excluded.level,
      factories=excluded.factories
    ''', (discord_id, name, level, factories, None, 0.0))
    conn.commit()
    conn.close()

def mark_paid(discord_id, amount):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = date.today().isoformat()
    c.execute('UPDATE players SET last_paid_date=?, last_paid_amount=? WHERE discord_id=?',
              (today, amount, discord_id))
    conn.commit()
    conn.close()

def add_payment_record(discord_id, payer_name, amount, proof, admin_name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    ts = datetime.utcnow().isoformat()
    c.execute('''
        INSERT INTO payments(discord_id,payer_name,amount,proof,admin_name,timestamp)
        VALUES(?,?,?,?,?,?)
    ''', (discord_id, payer_name, amount, proof or "", admin_name, ts))
    conn.commit()
    conn.close()

def get_all_players():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT discord_id,name,level,factories,last_paid_date,last_paid_amount FROM players')
    rows = c.fetchall()
    conn.close()
    return rows

def get_player(discord_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT discord_id,name,level,factories,last_paid_date,last_paid_amount FROM players WHERE discord_id=?', (discord_id,))
    row = c.fetchone()
    conn.close()
    return row

def add_bot_admin(discord_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO bot_admins(discord_id) VALUES(?)', (discord_id,))
    conn.commit()
    conn.close()

def remove_bot_admin(discord_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM bot_admins WHERE discord_id=?', (discord_id,))
    conn.commit()
    conn.close()

def is_bot_admin_db(discord_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT 1 FROM bot_admins WHERE discord_id=?', (discord_id,))
    r = c.fetchone()
    conn.close()
    return bool(r)

def get_payment_history(discord_id, limit=20):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT payer_name,amount,proof,admin_name,timestamp FROM payments WHERE discord_id=? ORDER BY id DESC LIMIT ?', (discord_id, limit))
    rows = c.fetchall()
    conn.close()
    return rows

# ----------------- Tax calculation -----------------
def tax_by_level(level):
    if 1 <= level <= 4:
        return 0.0
    if 5 <= level <= 9:
        return 1.0
    if 10 <= level <= 15:
        return 3.0
    if 16 <= level <= 20:
        return 5.5
    if 21 <= level <= 25:
        return 8.0
    if 26 <= level <= 30:
        return 12.0
    return 12.0

def total_tax(level, factories):
    base = tax_by_level(level)
    factory_tax = 0.0
    if factories >= 3:
        factory_tax = 0.5 * factories
    return round(base + factory_tax, 2)

# ----------------- Admin check helper -----------------
async def is_user_tax_admin(interaction: discord.Interaction) -> bool:
    # 1) Guild owner
    try:
        if interaction.guild is not None and interaction.guild.owner_id == interaction.user.id:
            return True
    except Exception:
        pass

    # 2) DB bot_admins
    if is_bot_admin_db(str(interaction.user.id)):
        return True

    # 3) Guild permissions / role
    if interaction.guild is None:
        return False
    try:
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            member = await interaction.guild.fetch_member(interaction.user.id)
    except Exception:
        member = None

    if member:
        if getattr(member.guild_permissions, "administrator", False):
            return True
        if ADMIN_ROLE_ID:
            try:
                if any(r.id == ADMIN_ROLE_ID for r in member.roles):
                    return True
            except Exception:
                pass

    return False

# ----------------- Bot events & sync -----------------
@bot.event
async def on_ready():
    init_db()
    # sync commands
    if GUILD_IDS:
        for gid in GUILD_IDS:
            guild = discord.Object(id=gid)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()
    print(f"Bot ready as {bot.user} (id: {bot.user.id})")

# ----------------- Slash commands -----------------
import asyncio
import os

# Optional env vars (add these in Railway Variables)
# LOG_CHANNEL_ID -- channel id where admins get summary (optional)
# REMINDER_ENABLED -- "true" to enable daily loop (optional)
# REMINDER_HOUR -- hour in 24h (0-23) when daily reminders run (optional)
REMINDER_ENABLED = os.environ.get("REMINDER_ENABLED", "false").lower() == "true"
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "12"))  # default noon
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID")) if os.environ.get("LOG_CHANNEL_ID") else LOG_CHANNEL_ID

# ---------------- reminder helpers ----------------
@app_commands.command(name="remind", description="(Admin) Send reminders to unpaid players now")
@app_commands.describe(mode="dm / admin / dm_and_admin (default: dm_and_admin)")
async def remind(interaction: discord.Interaction, mode: str = "dm_and_admin"):
    if not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return

    mode = (mode or "dm_and_admin").lower()
    if mode not in ("dm", "admin", "dm_and_admin"):
        await interaction.response.send_message("Invalid mode. Use dm, admin, or dm_and_admin.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    res = await send_reminders_to_unpaid(mode=mode, interaction=interaction)
    sent = res.get("sent", 0)
    failed = res.get("failed", [])

    await interaction.followup.send(
        f"تمت العملية بنجاح.\n"
        f"💌 رسائل تم إرسالها: {sent}\n"
        f"❌ فشل: {len(failed)}",
        ephemeral=True
    )

async def send_dm_safe(user: discord.User, content: str):
    try:
        await user.send(content)
        return True, None
    except Exception as e:
        return False, str(e)

async def gather_unpaid_players():
    rows = get_all_players()  # (discord_id,name,level,factories,last_paid_date,last_paid_amount)
    today = date.today().isoformat()
    unpaid = []
    for discord_id, name, level, factories, last_paid_date, last_paid_amount in rows:
        if last_paid_date != today:
            amt = total_tax(level, factories)
            unpaid.append({
                "discord_id": discord_id,
                "name": name,
                "level": level,
                "factories": factories,
                "due": amt
            })
    return unpaid

async def send_reminders_to_unpaid(mode="dm_and_admin", interaction: discord.Interaction = None):
    """
    mode: "dm" -> only DM players
          "admin" -> only send admin summary to LOG_CHANNEL_ID or ephemeral reply
          "dm_and_admin" -> do both
    interaction: if provided (command call), used to reply to invoker (ephemeral)
    """
    unpaid = await gather_unpaid_players()
    if not unpaid:
        if interaction:
            await interaction.response.send_message("كل اللاعبين دفعوا اليوم ✅", ephemeral=True)
        return {"sent": 0, "failed": []}

    sent = 0
    failed = []

    # 1) DM each unpaid player (private)
    if mode in ("dm", "dm_and_admin"):
        for p in unpaid:
            try:
                # try to get User object (cached) then fetch if necessary
                user_obj = bot.get_user(int(p["discord_id"]))
                if user_obj is None:
                    user_obj = await bot.fetch_user(int(p["discord_id"]))
                dm_text = (f"تذكير من بوت الضرائب:\n"
                           f"يا {p['name']}, لم يتم تسجيل دفع الضريبة اليوم.\n"
                           f"المبلغ المطلوب اليوم: ${p['due']}\n"
                           f"استخدم /pay <amount> أو اطلب من الأدمن يسجّلك.\n"
                           f"— War Era Tax Bot")
                ok, err = await send_dm_safe(user_obj, dm_text)
                if ok:
                    sent += 1
                else:
                    failed.append({"id": p["discord_id"], "name": p["name"], "error": err})
                # small delay to avoid hitting rate limits; adjust as needed
                await asyncio.sleep(0.8)
            except Exception as e:
                failed.append({"id": p["discord_id"], "name": p["name"], "error": str(e)})

    # 2) Build admin summary
    admin_text = f"تذكير: قائمة اللاعبين الذين لم يدفعوا اليوم ({len(unpaid)}):\n"
    for p in unpaid:
        admin_text += f"- {p['name']} (Lvl {p['level']}, Factories {p['factories']}) — Due ${p['due']}\n"

    # include failures
    if failed:
        admin_text += "\nفشل في إرسال DM لـ:\n"
        for f in failed:
            admin_text += f"- {f['name']} (id: {f['id']}) — {f.get('error')}\n"

    # 3) Send admin summary to LOG_CHANNEL_ID if configured, otherwise reply to interaction (ephemeral)
    if mode in ("admin", "dm_and_admin"):
        sent_to_log = False
        if LOG_CHANNEL_ID:
            # try to find the guild context: if interaction exists -> use its guild, else try all guilds
            try:
                ch = None
                if interaction and interaction.guild:
                    ch = interaction.guild.get_channel(LOG_CHANNEL_ID) or await interaction.guild.fetch_channel(LOG_CHANNEL_ID)
                else:
                    # try find any guild that has that channel id
                    for g in bot.guilds:
                        if g is None:
                            continue
                        try:
                            c = g.get_channel(LOG_CHANNEL_ID)
                            if c:
                                ch = c
                                break
                        except Exception:
                            continue
                if ch:
                    # respect length limits by chunking
                    chunk_size = 1900
                    for i in range(0, len(admin_text), chunk_size):
                        await ch.send(admin_text[i:i+chunk_size])
                    sent_to_log = True
            except Exception as e:
                # can't send to log channel
                if interaction:
                    await interaction.followup.send(f"لم أستطع إرسال ملخص الأدمن للقناة المحددة: {e}", ephemeral=True)
        if not sent_to_log and interaction:
            # reply ephemerally to the admin invoker
            # chunk if long
            if len(admin_text) <= 1900:
                await interaction.response.send_message(admin_text, ephemeral=True)
            else:
                # send the first chunk as response, followups for the rest
                await interaction.response.send_message(admin_text[:1900], ephemeral=True)
                for i in range(1900, len(admin_text), 1900):
                    await interaction.followup.send(admin_text[i:i+1900], ephemeral=True)

    # 4) If only DM mode and interaction provided, confirm how many sent
    if mode == "dm" and interaction:
        await interaction.response.send_message(f"تم إرسال {sent} رسائل خاصة. (فشل: {len(failed)})", ephemeral=True)

    return {"sent": sent, "failed": failed}

@app_commands.command(name="admin_register", description="(Admin) Register a player manually")
@app_commands.describe(
    member="The player you want to register",
    level="Player level (1-999)",
    factories="Number of factories"
)
async def admin_register(interaction: discord.Interaction, member: discord.Member, level: int, factories: int):
    # admin check
    if not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return

    if level < 1:
        await interaction.response.send_message("Invalid level.", ephemeral=True)
        return

    # save to DB
    upsert_player(str(member.id), member.name, level, factories)

    await interaction.response.send_message(
        f"✅ Registered **{member.name}** — level **{level}**, factories **{factories}**",
        ephemeral=True
    )

@app_commands.command(name="register", description="Register your level and number of factories (admin can register others)")
@app_commands.describe(
    level="Player level (1-999)",
    factories="Number of factories",
    member="Register this member instead of yourself (admin only)"
)
async def register(interaction: discord.Interaction, level: int, factories: int, member: discord.Member = None):

    # Check level validity
    if level < 1:
        await interaction.response.send_message("Invalid level.", ephemeral=True)
        return

    # If member provided → only admins can use it
    if member is not None:
        if not await is_user_tax_admin(interaction):
            await interaction.response.send_message("Admin only — you can't register other players.", ephemeral=True)
            return
        target = member
    else:
        target = interaction.user

    # Save player
    upsert_player(str(target.id), target.name, level, factories)

    # Response
    if member:
        await interaction.response.send_message(
            f"Registered {target.mention} — level {level}, factories {factories}", 
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"Registered {interaction.user.name} — level {level}, factories {factories}", 
            ephemeral=True
        )


@app_commands.command(name="tax", description="Show today's tax for you or another player")
@app_commands.describe(member="Member to check (optional)")
async def tax(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    row = get_player(str(target.id))
    if not row:
        await interaction.response.send_message("Player not registered.", ephemeral=True)
        return
    _, name, level, factories, last_paid_date, last_paid_amount = row
    t = total_tax(level, factories)
    paid_today = (last_paid_date == date.today().isoformat())
    await interaction.response.send_message(f"{name} — Level {level}, Factories {factories} → Daily tax: ${t} — Paid today: {paid_today}", ephemeral=True)

@app_commands.command(name="pay", description="Mark your payment (you) or for another player (admin)")
@app_commands.describe(member="Member who paid (optional)", amount="Amount paid, e.g. 5.5")
async def pay(interaction: discord.Interaction, amount: float, member: discord.Member = None):
    target = member or interaction.user
    row = get_player(str(target.id))
    if not row:
        await interaction.response.send_message("Player not registered. Use /register first.", ephemeral=True)
        return
    # mark paid and add payment record (payer_name = target.name, admin_name = interaction.user.name)
    mark_paid(str(target.id), amount)
    add_payment_record(str(target.id), target.name, amount, None, interaction.user.name)
    await interaction.response.send_message(f"Marked payment: {target.name} paid ${amount} today ✅", ephemeral=True)

@app_commands.command(name="markpaid", description="(Admin) Mark a player as paid with optional proof URL")
@app_commands.describe(member="Member who paid", amount="Amount paid", proof="Proof image URL (optional)")
async def markpaid(interaction: discord.Interaction, member: discord.Member, amount: float, proof: str = None):
    if not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return
    row = get_player(str(member.id))
    if not row:
        await interaction.response.send_message("Player not registered. Ask them to /register first.", ephemeral=True)
        return
    mark_paid(str(member.id), amount)
    add_payment_record(str(member.id), member.name, amount, proof, interaction.user.name)
    text = f"✅ Marked {member.name} as paid ${amount} by {interaction.user.name}."
    # send to log channel or reply
    if LOG_CHANNEL_ID and interaction.guild:
        ch = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="Payment recorded", color=0x2ecc71, timestamp=datetime.utcnow())
            embed.add_field(name="Player", value=f"{member.mention} ({member.name})", inline=True)
            embed.add_field(name="Amount", value=f"${amount}", inline=True)
            embed.add_field(name="By", value=interaction.user.mention, inline=True)
            if proof:
                embed.add_field(name="Proof", value=proof, inline=False)
                try:
                    embed.set_image(url=proof)
                except Exception:
                    pass
            await ch.send(embed=embed)
            await interaction.response.send_message("Payment recorded and sent to log channel.", ephemeral=True)
            return
    # fallback
    if proof:
        await interaction.response.send_message(text + f"\nProof: {proof}", ephemeral=False)
    else:
        await interaction.response.send_message(text, ephemeral=True)

@app_commands.command(name="history", description="Show payment history for a user (recent entries)")
@app_commands.describe(member="Member to check (optional)", limit="Number of entries (max 50)")
async def history(interaction: discord.Interaction, member: discord.Member = None, limit: int = 10):
    target = member or interaction.user
    limit = max(1, min(50, limit))
    rows = get_payment_history(str(target.id), limit)
    if not rows:
        await interaction.response.send_message("No payment history found.", ephemeral=True)
        return
    text = f"Payment history for {target.display_name} (last {len(rows)}):\n"
    for payer_name, amount, proof, admin_name, timestamp in rows:
        ts = timestamp.split("T")[0] if timestamp else timestamp
        text += f"- {ts} — ${amount} — recorded by {admin_name}\n"
    await interaction.response.send_message(text, ephemeral=True)

# Admin-only / powerful commands
# ---------- Utility: update player fields ----------
def update_player_field(discord_id: str, field: str, value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if field not in ("level", "factories", "last_paid_date", "last_paid_amount", "name"):
        conn.close()
        raise ValueError("Invalid field")
    c.execute(f'UPDATE players SET {field} = ? WHERE discord_id = ?', (value, discord_id))
    conn.commit()
    conn.close()

# ---------- Commands to change level / factories ----------
@app_commands.command(name="level_up", description="Increase level for you or a member (admin can change others)")
@app_commands.describe(member="Member to level up (admin only)", amount="How many levels to add (default 1)")
async def level_up(interaction: discord.Interaction, amount: int = 1, member: discord.Member = None):
    if amount < 1:
        await interaction.response.send_message("Amount must be >= 1.", ephemeral=True)
        return

    target = member or interaction.user
    # if targeting other member -> must be admin
    if member and not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only to modify other players.", ephemeral=True)
        return

    row = get_player(str(target.id))
    if not row:
        await interaction.response.send_message("Player not registered. Use /register first.", ephemeral=True)
        return

    _, name, level, factories, last_paid_date, last_paid_amount = row
    new_level = max(1, level + amount)
    update_player_field(str(target.id), "level", new_level)
    await interaction.response.send_message(f"✅ {target.display_name} level: {level} → {new_level}", ephemeral=(not member))

@app_commands.command(name="add_factories", description="Add factories for you or a member (admin can change others)")
@app_commands.describe(member="Member to add factories for (admin only)", amount="How many factories to add (default 1)")
async def add_factories(interaction: discord.Interaction, amount: int = 1, member: discord.Member = None):
    if amount < 1:
        await interaction.response.send_message("Amount must be >= 1.", ephemeral=True)
        return

    target = member or interaction.user
    if member and not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only to modify other players.", ephemeral=True)
        return

    row = get_player(str(target.id))
    if not row:
        await interaction.response.send_message("Player not registered. Use /register first.", ephemeral=True)
        return

    _, name, level, factories, last_paid_date, last_paid_amount = row
    new_factories = max(0, factories + amount)
    update_player_field(str(target.id), "factories", new_factories)
    await interaction.response.send_message(f"✅ {target.display_name} factories: {factories} → {new_factories}", ephemeral=(not member))

@app_commands.command(name="set_level", description="(Admin) Set exact level for a player")
@app_commands.describe(member="Member to set level for", level="Level to set (>=1)")
async def set_level(interaction: discord.Interaction, member: discord.Member, level: int):
    if not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return
    if level < 1:
        await interaction.response.send_message("Level must be >= 1.", ephemeral=True)
        return
    row = get_player(str(member.id))
    if not row:
        await interaction.response.send_message("Player not registered.", ephemeral=True)
        return
    update_player_field(str(member.id), "level", level)
    await interaction.response.send_message(f"✅ Set {member.display_name} level to {level}.", ephemeral=True)

@app_commands.command(name="set_factories", description="(Admin) Set exact number of factories for a player")
@app_commands.describe(member="Member to set factories for", factories="Number of factories (>=0)")
async def set_factories(interaction: discord.Interaction, member: discord.Member, factories: int):
    if not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return
    if factories < 0:
        await interaction.response.send_message("Factories must be >= 0.", ephemeral=True)
        return
    row = get_player(str(member.id))
    if not row:
        await interaction.response.send_message("Player not registered.", ephemeral=True)
        return
    update_player_field(str(member.id), "factories", factories)
    await interaction.response.send_message(f"✅ Set {member.display_name} factories to {factories}.", ephemeral=True)

@app_commands.command(name="grant", description="Grant tax-admin to a user (bot-admin table)")
@app_commands.describe(member="Member to grant")
async def grant(interaction: discord.Interaction, member: discord.Member):
    if not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return
    add_bot_admin(str(member.id))
    await interaction.response.send_message(f"{member.mention} is now a tax-admin (bot).", ephemeral=True)

@app_commands.command(name="revoke", description="Revoke tax-admin from a user")
@app_commands.describe(member="Member to revoke")
async def revoke(interaction: discord.Interaction, member: discord.Member):
    if not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return
    remove_bot_admin(str(member.id))
    await interaction.response.send_message(f"{member.mention} removed from tax-admins.", ephemeral=True)

@app_commands.command(name="unpaid", description="List players who didn't pay today (admin only)")
async def unpaid(interaction: discord.Interaction):
    if not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return
    rows = get_all_players()
    not_paid = []
    today = date.today().isoformat()
    total = 0.0
    for r in rows:
        discord_id, name, level, factories, last_paid_date, last_paid_amount = r
        t = total_tax(level, factories)
        if last_paid_date != today:
            not_paid.append((name, t))
        else:
            total += last_paid_amount
    if not_paid:
        text = f"Total collected today: ${round(total,2)}\nNot paid ({len(not_paid)}):\n"
        for n, amt in not_paid:
            text += f"- {n}: ${amt}\n"
    else:
        text = f"Total collected today: ${round(total,2)}\nAll paid ✅"
    # post to log channel if configured
    if LOG_CHANNEL_ID and interaction.guild:
        ch = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if ch:
            await ch.send(text)
            await interaction.response.send_message("Sent unpaid list to log channel.", ephemeral=True)
            return
    await interaction.response.send_message(text, ephemeral=False)

@app_commands.command(name="dashboard", description="Show tax dashboard (admin only)")
async def dashboard(interaction: discord.Interaction):
    if not await is_user_tax_admin(interaction):
        await interaction.response.send_message("Admin only.", ephemeral=True)
        return

    rows = get_all_players()
    if not rows:
        await interaction.response.send_message("No players registered.", ephemeral=True)
        return

    today = date.today().isoformat()
    lines = []
    for discord_id, name, level, factories, last_paid_date, last_paid_amount in rows:
        due = total_tax(level, factories)
        paid = (last_paid_date == today)
        paid_text = f"✅ Paid (${last_paid_amount})" if paid else "❌ Not paid"
        lines.append(f"- {name} | Lvl {level} | Due ${due} | {paid_text}")

    header = f"**Dashboard — Players: {len(rows)}**\n"
    full_text = header + "\n".join(lines)

    # split into chunks to avoid message length limits
    chunk_size = 1800
    chunks = [full_text[i:i+chunk_size] for i in range(0, len(full_text), chunk_size)]

    if LOG_CHANNEL_ID and interaction.guild:
        ch = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if ch:
            for c in chunks:
                await ch.send(c)
            await interaction.response.send_message("Dashboard sent to log channel.", ephemeral=True)
            return

    await interaction.response.send_message(chunks[0], ephemeral=False)
    for c in chunks[1:]:
        await interaction.followup.send(c, ephemeral=False)

# register commands to tree
bot.tree.add_command(register)
bot.tree.add_command(admin_register)
bot.tree.add_command(tax)
bot.tree.add_command(remind)
bot.tree.add_command(pay)
bot.tree.add_command(level_up)
bot.tree.add_command(add_factories)
bot.tree.add_command(set_level)
bot.tree.add_command(set_factories)
bot.tree.add_command(markpaid)
bot.tree.add_command(history)
bot.tree.add_command(grant)
bot.tree.add_command(revoke)
bot.tree.add_command(unpaid)
bot.tree.add_command(dashboard)

# --------------- Run ---------------
if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN environment variable not set. Add your bot token and retry.")
else:
    bot.run(BOT_TOKEN)
