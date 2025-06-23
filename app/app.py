from dotenv import load_dotenv
import discord
from discord.ext import commands
import os
import sqlite3
from openskill.models import PlackettLuce
import datetime
import re

VALID_MENTION_PATTERN = re.compile(r"<@!?\d+>")

load_dotenv()
model = PlackettLuce()
intents = discord.Intents.default()
intents.message_content = True

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

bot = commands.Bot(command_prefix='!', intents=intents)

async def update_leaderboard(leaderboard_lines):
    cursor.execute('SELECT id, rating_mu, rating_sigma FROM users')
    players = {row[0]: model.create_rating(rating=[row[1], row[2]], name=str(row[0])) for row in cursor.fetchall()}

    today = datetime.date.today().isoformat()
    seen_ids = set()
    
    performance_tiers = []
    for line in leaderboard_lines:
        if not line.strip():
            continue
            
        mentions = [part for part in line.split() if VALID_MENTION_PATTERN.fullmatch(part)]
        if not mentions:
            continue
            
        tier = []
        for mention in mentions:
            uid = int(re.search(r"\d+", mention).group())
            seen_ids.add(uid)
            if uid not in players:
                players[uid] = model.create_rating(rating=[25.0, 8.333], name=str(uid))

                cursor.execute('''
                    INSERT OR IGNORE INTO users (id, rating_mu, rating_sigma, last_played)
                    VALUES (?, ?, ?, ?)
                ''', (uid, players[uid].mu, players[uid].sigma, today))
            tier.append(players[uid])
        
        if tier:
            performance_tiers.append(tier)

    if not performance_tiers:
        return

    ranks = []
    flat_players = []
    current_rank = 0
    for tier in performance_tiers:
        for player in tier:
            flat_players.append([player])
            ranks.append(current_rank)
        current_rank += 1

    new_ratings = model.rate(flat_players, ranks=ranks)

    player_index = 0
    for tier in performance_tiers:
        for player in tier:
            uid = int(player.name)
            new_rating = new_ratings[player_index][0]
            cursor.execute('''
                UPDATE users SET
                    rating_mu = ?, rating_sigma = ?, last_played = ?
                WHERE id = ?
            ''', (new_rating.mu, new_rating.sigma, today, uid))
            player_index += 1

    all_known_ids = set(players.keys())
    missing_ids = all_known_ids - seen_ids
    decay_amount = 1.0

    for uid in missing_ids:
        old_rating = players[uid]
        new_mu = max(old_rating.mu - decay_amount, 1)
        cursor.execute('''
            UPDATE users SET
                rating_mu = ?, last_played = ?
            WHERE id = ?
        ''', (new_mu, today, uid))

    conn.commit()

@bot.command(name='show-leaderboard')
async def show_leaderboard(ctx):
    cursor.execute('SELECT id, rating_mu, rating_sigma FROM users')
    rows = cursor.fetchall()
    if not rows:
        await ctx.send("Leaderboard is empty.")
        return

    rows.sort(key=lambda r: r[1] - 3*r[2], reverse=True)

    lines = []
    for i, (uid, mu, sigma) in enumerate(rows, 1):
        score = mu - 3 * sigma
        lines.append(f"{i}. <@{uid}> — score: {score:.1f} | μ: {mu:.1f}, σ: {sigma:.2f}")
    await ctx.send("**Current Leaderboard**\n" + "\n".join(lines))

@bot.command(name='reset-leaderboard')
async def reset_leaderboard(ctx):
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

  await ctx.send("Resetting leaderboard and recalculating from history...")

  history = []
  async for msg in ctx.channel.history(limit=None, oldest_first=True):
    if (msg.author.global_name == "Wordle" or msg.author.name == "Wordle") and "results:" in msg.content:
      if re.search(r"(?<!<)@", msg.content):
        continue
      history.append(msg)

  for msg in history:
    leaderboard_lines = msg.content.split("\n")[1:]
    await update_leaderboard(leaderboard_lines)
  await ctx.send("Leaderboard recalculated from history.")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    elif message.author.id == 269715475410190346 and "results:" in message.content:
        leaderboard_lines = message.content.split("\n")[1:]
        await update_leaderboard(leaderboard_lines)

        cursor.execute('SELECT id, rating_mu, rating_sigma FROM users')
        rows = cursor.fetchall()
        rows.sort(key=lambda r: r[1] - 3*r[2], reverse=True)

        if not rows:
            await message.reply("Updated ratings.\nLeaderboard is empty.")
            return

        lines = []
        for i, (uid, mu, sigma) in enumerate(rows, 1):
            score = mu - 3 * sigma
            lines.append(f"{i}. <@{uid}> — score: {score:.1f} | μ: {mu:.1f}, σ: {sigma:.2f}")
        
        leaderboard_text = "**Updated Leaderboard**\n" + "\n".join(lines)
        await message.reply(leaderboard_text)

    await bot.process_commands(message)