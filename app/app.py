# app.py
from dotenv import load_dotenv
import discord
import os
import sqlite3
from openskill.models import PlackettLuce
import datetime
import re
import certifi

# Ensure SSL certificate verification works properly
os.environ["SSL_CERT_FILE"] = certifi.where()

load_dotenv()
model = PlackettLuce()
intents = discord.Intents.default()
intents.message_content = True

bot = discord.Bot(intents=intents)  # Slash command enabled :contentReference[oaicite:1]{index=1}

# Database initialization
conn = sqlite3.connect('users.db')
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  rating_mu REAL NOT NULL DEFAULT 25.0,
  rating_sigma REAL NOT NULL DEFAULT 8.333,
  last_played DATE NOT NULL
)
''')
conn.commit()

VALID_MENTION_PATTERN = re.compile(r"<@!?\d+>")

async def update_leaderboard(lines):
    cursor.execute('SELECT id, rating_mu, rating_sigma FROM users')
    players = {row[0]: model.create_rating([row[1], row[2]], name=str(row[0])) for row in cursor.fetchall()}
    today = datetime.date.today().isoformat()
    seen = set()
    performance = []

    for line in lines:
        parts = [p for p in line.split() if VALID_MENTION_PATTERN.fullmatch(p)]
        if not parts: continue
        tier = []
        for mention in parts:
            uid = int(re.search(r"\d+", mention).group())
            seen.add(uid)
            if uid not in players:
                players[uid] = model.create_rating([25.0, 8.333], name=str(uid))
                cursor.execute(
                    'INSERT OR IGNORE INTO users (id,rating_mu,rating_sigma,last_played) VALUES (?, ?, ?, ?)',
                    (uid, players[uid].mu, players[uid].sigma, today)
                )
            tier.append(players[uid])
        performance.append(tier)

    if not performance:
        return

    flat, ranks = [], []
    for rank, tier in enumerate(performance):
        for player in tier:
            flat.append([player])
            ranks.append(rank)

    new_ratings = model.rate(flat, ranks=ranks)
    for idx, tier in enumerate(performance):
        for player in tier:
            uid = int(player.name)
            nr = new_ratings.pop(0)[0]
            cursor.execute(
                'UPDATE users SET rating_mu=?, rating_sigma=?, last_played=? WHERE id=?',
                (nr.mu, nr.sigma, today, uid)
            )

    for uid in set(players) - seen:
        old = players[uid]
        new_mu = max(old.mu - 1.0, 1)
        cursor.execute('UPDATE users SET rating_mu=?, last_played=? WHERE id=?', (new_mu, today, uid))

    conn.commit()

@bot.slash_command(name="show_leaderboard", description="Display the current leaderboard")
async def show_leaderboard(ctx: discord.ApplicationContext):
    cursor.execute('SELECT id, rating_mu, rating_sigma FROM users')
    rows = cursor.fetchall()
    if not rows:
        await ctx.respond("Leaderboard is empty.")
        return
    rows.sort(key=lambda r: r[1] - 3*r[2], reverse=True)
    text = "**Leaderboard**\n" + "\n".join(
        f"{i+1}. <@{uid}> — score: {mu - 3*sigma:.1f} | μ: {mu:.1f}, σ: {sigma:.2f}"
        for i, (uid, mu, sigma) in enumerate(rows)
    )
    await ctx.respond(text)

@bot.slash_command(name="reset_leaderboard", description="Reset and rebuild leaderboard from history")
async def reset_leaderboard(ctx: discord.ApplicationContext):
    await ctx.defer()
    cursor.execute('DROP TABLE IF EXISTS users')
    cursor.execute('''
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            rating_mu REAL NOT NULL DEFAULT 25.0,
            rating_sigma REAL NOT NULL DEFAULT 8.333,
            last_played DATE NOT NULL
        )
    ''')
    conn.commit()
    await ctx.followup.send("Recalculating leaderboard from history...")
    async for m in ctx.channel.history(limit=None, oldest_first=True):
        if (m.author.global_name == "Wordle" or m.author.name == "Wordle") and "results:" in m.content:
            if re.search(r"(?<!<)@", m.content): continue
            await update_leaderboard(m.content.split("\n")[1:])
    await ctx.followup.send("Leaderboard rebuilt.")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.author.id == 269715475410190346 and "results:" in message.content:
        await update_leaderboard(message.content.split("\n")[1:])
        cursor.execute('SELECT id, rating_mu, rating_sigma FROM users')
        rows = cursor.fetchall()
        rows.sort(key=lambda r: r[1] - 3*r[2], reverse=True)
        reply = "**Updated Leaderboard**\n" + "\n".join(
            f"{i+1}. <@{uid}> — score: {mu - 3*sigma:.1f} | μ: {mu:.1f}, σ: {sigma:.2f}"
            for i, (uid, mu, sigma) in enumerate(rows)
        )
        await message.reply(reply)
    await bot.process_commands(message)

@bot.event
async def on_ready():
    await bot.sync_commands()  # ensure slash commands are registered :contentReference[oaicite:2]{index=2}
    print(f"Logged in as {bot.user}")

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ DISCORD_TOKEN not set.")
        exit(1)
    bot.run(token)
