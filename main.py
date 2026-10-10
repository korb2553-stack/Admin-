import os

import discord
from discord.ext import commands

# โทเคนห้ามใส่ในโค้ด (repo เป็น Public) ให้ตั้งเป็นตัวแปร DISCORD_TOKEN บนเซิร์ฟเวอร์
# หรือสร้างไฟล์ token.txt ไว้บนเซิร์ฟเวอร์โดยตรง (ไม่อัปขึ้น GitHub)
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN and os.path.exists("token.txt"):
    with open("token.txt", encoding="utf-8") as f:
        TOKEN = f.read().strip()


class AdminBot(commands.Bot):
    async def setup_hook(self):
        await self.load_extension("modlog")
        await self.tree.sync()


bot = AdminBot(command_prefix="!", intents=discord.Intents.default())


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


bot.run(TOKEN)
