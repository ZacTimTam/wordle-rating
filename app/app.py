from dotenv import load_dotenv
import discord
from discord.ext import commands
import os
import sqlite3
import trueskill
import datetime
import re

VALID_MENTION_PATTERN = re.compile(r"<@!?\d+>")

load_dotenv()

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

ts = trueskill.TrueSkill(draw_probability=0.1)
async def update_leaderboard(leaderboard_lines):
    cursor.execute('SELECT id, rating_mu, rating_sigma FROM users')
    players = {row[0]: trueskill.Rating(mu=row[1], sigma=row[2]) for row in cursor.fetchall()}

    today = datetime.date.today().isoformat()
    seen_ids = set()
    
    # First parse all players with their guess counts
    player_data = []
    for line in leaderboard_lines:
        parts = line.strip().split()
        if not parts:
            continue
            
        # Extract user mentions and guess count
        mentions = [p for p in parts if VALID_MENTION_PATTERN.fullmatch(p)]
        guess_count = next((int(p) for p in parts if p.isdigit()), None)
        
        if not mentions or guess_count is None:
            continue
            
        for mention in mentions:
            uid = int(re.search(r"\d+", mention).group())
            seen_ids.add(uid)
            if uid not in players:
                players[uid] = ts.create_rating()
                cursor.execute('''
                    INSERT OR IGNORE INTO users (id, rating_mu, rating_sigma, last_played)
                    VALUES (?, ?, ?, ?)
                ''', (uid, players[uid].mu, players[uid].sigma, today))
            player_data.append((guess_count, uid, players[uid]))
    print(player_data)
    if not player_data:
        return

    # Sort by performance (ascending guess count)
    player_data.sort(key=lambda x: x[0])
    
    # Create FFA ranking with proper tie handling
    ranking = []
    current_guess = None
    current_tier = []
    
    for guess, uid, rating in player_data:
        if guess != current_guess:
            if current_tier:
                ranking.append(current_tier)
            current_tier = []
            current_guess = guess
        current_tier.append([rating])  # Each player in their own sub-list
    
    if current_tier:
        ranking.append(current_tier)

    # Calculate new ratings
    new_ratings = ts.rate(ranking)
    print(new_ratings)
    # Update database
    rating_index = 0
    for tier in new_ratings:
        for player_ratings in tier:
            uid = player_data[rating_index][1]
            new_rating = player_ratings[0]
            cursor.execute('''
                UPDATE users SET
                    rating_mu = ?, rating_sigma = ?, last_played = ?
                WHERE id = ?
            ''', (new_rating.mu, new_rating.sigma, today, uid))
            rating_index += 1

    # Handle inactive players
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
  rows.sort(key=lambda r: trueskill.Rating(mu=r[1], sigma=r[2]).exposure, reverse=True)
  lines = []
  for i, (uid, mu, sigma) in enumerate(rows, 1):
    exposed = trueskill.Rating(mu, sigma).exposure
    lines.append(f"{i}. <@{uid}> — {exposed:.1f} (μ={mu:.1f}, σ={sigma:.2f})")
  await ctx.send("Current leaderboard:\n" + "\n".join(lines))

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
      # skip messages with invalid mentions (@ not preceded by <)
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
    await message.reply("Updated ratings.")

  await bot.process_commands(message)

bot.run(os.getenv('DISCORD_TOKEN'))