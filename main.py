import os
import traceback
import discord
import requests
from dotenv import load_dotenv
from datetime import datetime
import sys
# ─────────────────────────────────────────────
#  CONFIG — fill these in or use env vars
# ─────────────────────────────────────────────
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GROQ_API_KEY  = os.getenv("GROQ_API_KEY")
GROQ_MODEL    = os.getenv("GROQ_MODEL")
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

MAX_HISTORY   = 10   # messages kept per channel (pairs: user + assistant)
 
SYSTEM_PROMPT = (
    "You are a witty, sarcastic-but-helpful assistant living inside a Discord server. "
    "Your answers must always be factually correct and complete — accuracy is non-negotiable. "
    "BUT your delivery should be funny: use dry humor, light roasts, or absurd analogies. "
    "Keep replies short (2-5 sentences max). Never sacrifice correctness for a joke. "
    "No emoji spam. No cringe. Think 'brilliant friend who can't resist a quip'. "
    "Format any code in proper markdown code blocks (```language ... ```)."
)
# ─────────────────────────────────────────────
#  PER-CHANNEL CONVERSATION HISTORY
# ─────────────────────────────────────────────
# { channel_id: [ {role, content}, ... ] }
conversation_history: dict[int, list[dict]] = {}
 
 
def get_history(channel_id: int) -> list[dict]:
    return conversation_history.setdefault(channel_id, [])
 
 
def add_to_history(channel_id: int, role: str, content: str):
    history = get_history(channel_id)
    history.append({"role": role, "content": content})
    # Keep last MAX_HISTORY messages (trim from front)
    if len(history) > MAX_HISTORY:
        conversation_history[channel_id] = history[-MAX_HISTORY:]
 
 
def clear_history(channel_id: int):
    conversation_history[channel_id] = []
 
 
# ─────────────────────────────────────────────
#  AI CALL
# ─────────────────────────────────────────────
def ask_ai(channel_id: int, prompt: str) -> str:
    """Send prompt + history to Groq, return reply or fallback."""
    add_to_history(channel_id, "user", prompt)
    now = datetime.now().strftime("%A, %B %d %Y, %I:%M %p")
    system_with_time = SYSTEM_PROMPT + f" The current date and time is: {now}."
    messages = [{"role": "system", "content": system_with_time}] + get_history(channel_id)
 
    print(f"[AI] Calling Groq | channel={channel_id} | prompt={prompt!r}")
    try:
        response = requests.post(
            GROQ_ENDPOINT,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": messages,
                "max_tokens": 512,
                "temperature": 0.5,
            },
            timeout=20,
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"].strip()
        add_to_history(channel_id, "assistant", answer)
        print(f"[AI] Response: {answer[:80]}{'...' if len(answer) > 80 else ''}")
        return answer
 
    except requests.exceptions.Timeout:
        print("[ERROR] Groq request timed out.")
        return "> Request timed out. Try again."
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code
        print(f"[ERROR] HTTP {status} from Groq: {e}")
        if status == 401:
            return "> Invalid API key. Check your GROQ_API_KEY."
        if status == 429:
            return "> Rate limit hit. Wait a moment and retry."
        return f"> AI returned HTTP {status}. Try again later."
    except Exception as e:
        print(f"[ERROR] Unexpected: {e}")
        return "> Unexpected error. Try again."
 
 
# ─────────────────────────────────────────────
#  BOT SETUP
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True   # must also be ON in Discord Developer Portal
 
bot = discord.Client(intents=intents)
 
 
# ─────────────────────────────────────────────
#  SLASH COMMANDS  (discord.py app_commands)
# ─────────────────────────────────────────────
tree = discord.app_commands.CommandTree(bot)
 
 
@tree.command(name="clear", description="Clear this channel's conversation history with the bot.")
async def clear_cmd(interaction: discord.Interaction):
    clear_history(interaction.channel_id)
    await interaction.response.send_message("Done — history cleared (only you see this).", ephemeral=True)
    await interaction.channel.send("Memory wiped. I now know absolutely nothing about you. Fresh start.")
 
 
@tree.command(name="history", description="Show how many messages are in the current context.")
async def history_cmd(interaction: discord.Interaction):
    count = len(get_history(interaction.channel_id))
    await interaction.response.send_message(
        f"{count} message(s) in context (max {MAX_HISTORY}).", ephemeral=True
    )
 
 
@tree.command(name="ping", description="Check bot latency.")
async def ping_cmd(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! `{latency}ms`", ephemeral=True)
 
 
# ─────────────────────────────────────────────
#  EVENTS
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()   # register slash commands globally
    print(f"[BOT] Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"[BOT] Slash commands synced.")
    print(f"[BOT] Ready — mention me or use /ping, /clear, /history")
 
 
@bot.event
async def on_message(message: discord.Message):
    timestamp = datetime.now().strftime("%H:%M:%S")
 
    # 1. Ignore non-standard message types (slash command ephemeral results,
    #    thread starters, pins, etc.) — they have no usable .content
    if message.type not in (discord.MessageType.default, discord.MessageType.reply):
        return
 
    # 2. Ignore bots (prevents infinite loops)
    if message.author.bot:
        return
 
    # 3. Guard: .content can be empty string on some gateway events
    if not message.content:
        return
 
    print(f"[{timestamp}] #{message.channel} | {message.author}: {message.content!r}")
 
    # 4. Passively store every human message so context is preserved
    #    even when the user does not mention the bot (e.g. "my name is sam")
    add_to_history(message.channel.id, "user", f"{message.author.display_name}: {message.content}")
 
    # 5. Only respond when mentioned
    if bot.user not in message.mentions:
        return
 
    print(f"[BOT] Triggered by {message.author} in #{message.channel}")
 
    # 3. Strip mention(s) — handle both <@ID> and <@!ID>
    prompt = message.content
    for mention_fmt in [f"<@{bot.user.id}>", f"<@!{bot.user.id}>"]:
        prompt = prompt.replace(mention_fmt, "")
    prompt = prompt.strip()
 
    # 4. Handle special text commands (mention-based)
    if prompt.lower() in ("!clear", "clear", "reset"):
        clear_history(message.channel.id)
        await message.reply("Memory wiped. I now know absolutely nothing about you. Fresh start.")
        return
 
    if prompt.lower() in ("!help", "help", "?"):
        help_text = (
            "**Commands**\n"
            "`@bot <question>` — ask anything\n"
            "`@bot clear` — reset conversation memory\n"
            "`/ping` — check latency\n"
            "`/clear` — clear history (slash)\n"
            "`/history` — show context size"
        )
        await message.reply(help_text)
        return
 
    # 5. Guard: empty input
    if not prompt:
        await message.reply("Include a message after mentioning me. Try `@bot help`.")
        return
 
    # 6. Call AI with history context
    async with message.channel.typing():
        answer = ask_ai(message.channel.id, prompt)
 
    # 7. Split long replies (Discord 2000-char limit)
    if len(answer) <= 1900:
        await message.reply(answer)
    else:
        chunks = [answer[i:i+1900] for i in range(0, len(answer), 1900)]
        for i, chunk in enumerate(chunks):
            if i == 0:
                await message.reply(chunk)
            else:
                await message.channel.send(chunk)
 
 
@bot.event
async def on_error(event: str, *args, **kwargs):
    # sys.exc_info() captures the live exception; print_exception() shows
    # the full stack trace so you can see exactly what failed and where
    exc_type, exc_value, exc_tb = sys.exc_info()
    print(f"[ERROR] Exception in event '{event}' — full traceback:")
    traceback.print_exception(exc_type, exc_value, exc_tb)
    print(f"[ERROR] Event args: {args}")
 
 
# ─────────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN":
        print("[FATAL] Set DISCORD_TOKEN before running.")
        exit(1)
    if GROQ_API_KEY == "YOUR_GROQ_API_KEY":
        print("[FATAL] Set GROQ_API_KEY before running.")
        exit(1)
    bot.run(DISCORD_TOKEN)