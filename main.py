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
@app_commands.command(name="register", description="Register your level and number of factories")
@app_commands.describe(level="Your player level (1-999)", factories="Number of factories you own")
async def register(interaction: discord.Interaction, level: int, factories: int):
    if level < 1:
        await interaction.response.send_message("Invalid level.", ephemeral=True)
        return
    upsert_player(str(interaction.user.id), interaction.user.name, level, factories)
    await interaction.response.send_message(f"Registered {interaction.user.name} — level {level}, factories {factories}", ephemeral=True)

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
bot.tree.add_command(tax)
bot.tree.add_command(pay)
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
