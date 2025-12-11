# (تكملة الملف بعد /pay ...)

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
    await interaction.response.send_message(f"✅ {target.display_name} level: {level} → {new_level}", ephemeral=(member is None))

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
    await interaction.response.send_message(f"✅ {target.display_name} factories: {factories} → {new_factories}", ephemeral=(member is None))

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
        if last_paid_date != tod
::contentReference[oaicite:0]{index=0}
ay:
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
bot.tree.add_command(level_up)
bot.tree.add_command(add_factories)
bot.tree.add_command(set_level)
bot.tree.add_command(set_factories)
bot.tree.add_command(grant)
bot.tree.add_command(revoke)
bot.tree.add_command(unpaid)
bot.tree.add_command(dashboard)

# --------------- Run ---------------
if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN environment variable not set. Add your bot token and retry.")
else:
    bot.run(BOT_TOKEN)

