import os
import time
import traceback

import discord
from discord.ext import commands

# โทเคนห้ามใส่ในโค้ด (repo เป็น Public) ให้ตั้งเป็นตัวแปร DISCORD_TOKEN บนเซิร์ฟเวอร์
# หรือสร้างไฟล์ token.txt ไว้บนเซิร์ฟเวอร์โดยตรง (ไม่อัปขึ้น GitHub)
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN and os.path.exists("token.txt"):
    with open("token.txt", encoding="utf-8") as f:
        TOKEN = f.read().strip()

intents = discord.Intents.default()
intents.members = True          # ต้องเปิด Server Members Intent ใน Developer Portal
intents.message_content = True  # ต้องเปิด Message Content Intent ใน Developer Portal


async def _tell_error(interaction: discord.Interaction, error: Exception):
    """แสดงข้อผิดพลาดให้คนกดเห็นในดิสคอร์ด (และพิมพ์ลง Console) แทนที่จะขึ้นแค่ 'ไม่ตอบสนอง'"""
    traceback.print_exception(type(error), error, error.__traceback__)
    msg = f"⚠️ เกิดข้อผิดพลาด: `{type(error).__name__}: {error}`"[:1900]
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


async def _view_error(self, interaction, error, item):
    await _tell_error(interaction, error)


async def _modal_error(self, interaction, error):
    await _tell_error(interaction, error)


discord.ui.View.on_error = _view_error
discord.ui.Modal.on_error = _modal_error


class AdminBot(commands.Bot):
    async def on_tree_error(self, interaction, error):
        await _tell_error(interaction, error)

    async def setup_hook(self):
        self.tree.on_error = self.on_tree_error
        await self.load_extension("modlog")
        await self.load_extension("protect")
        await self.tree.sync()

    async def on_ready(self):
        print(f"Logged in as {self.user}")


def make_bot():
    return AdminBot(command_prefix="!", intents=intents)


# ถ้า Discord/Cloudflare บล็อก IP ของโฮสต์ (429) บอทจะรอแล้วลองใหม่เอง
# ไม่ล่ม และไม่ยิงล็อกอินรัว ๆ (การยิงถี่ ๆ ทำให้โดนบล็อกนานขึ้น)
delay = 900  # 15 นาที
while True:
    try:
        make_bot().run(TOKEN)
        break
    except discord.HTTPException as e:
        if e.status != 429:
            raise
        print(f"⚠️ ล็อกอินไม่ได้ (429 โดนบล็อกชั่วคราว) รอ {delay // 60} นาทีแล้วลองใหม่ ไม่ต้องกด Start ซ้ำ")
        time.sleep(delay)
        delay = min(delay * 2, 3600)
