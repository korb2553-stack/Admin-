import json
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

DATA_FILE = "protect_data.json"
DEFAULT = {
    "log_channel": None,
    "whitelist_users": [],
    "whitelist_roles": [],
    "lockdown": False,
    "nuke": {"enabled": True, "limit": 4, "seconds": 10},
    "raid": {"enabled": True, "joins": 8, "seconds": 10, "min_age_days": 0},
    "spam": {"enabled": True, "msgs": 6, "seconds": 5, "dupes": 4, "mentions": 5, "timeout_min": 10},
    "links": {"enabled": True},
    "bots": {"enabled": True},
}

A = discord.AuditLogAction
NUKE_ACTIONS = {
    A.channel_create, A.channel_delete, A.role_create, A.role_delete,
    A.ban, A.kick, A.webhook_create, A.webhook_delete, A.emoji_delete,
}
INVITE_RE = re.compile(r"(discord\.gg|discord(?:app)?\.com/invite|dsc\.gg)/[\w-]+", re.I)
RAID_MODE_SECONDS = 300


def fill(base, d):
    for k, v in base.items():
        if isinstance(v, dict):
            d[k] = fill(v, d.get(k) if isinstance(d.get(k), dict) else {})
        else:
            d.setdefault(k, json.loads(json.dumps(v)))
    return d


def load():
    d = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            d = json.load(f)
    return fill(DEFAULT, d)


def save(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def onoff(b):
    return "✅ เปิด" if b else "❌ ปิด"


# ───────────────────────── แผงตั้งค่า (เจ้าของเท่านั้น) ─────────────────────────
class NumModal(discord.ui.Modal, title="ตั้งค่าตัวเลข"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
        c = cog.cfg
        self.nuke = discord.ui.TextInput(label="Anti-nuke: ทำได้สูงสุดกี่ครั้งใน 10 วิ", default=str(c["nuke"]["limit"]), max_length=3)
        self.raid = discord.ui.TextInput(label="Anti-raid: คนเข้าใน 10 วิ ถึงกี่คนถือว่าบุก", default=str(c["raid"]["joins"]), max_length=3)
        self.age = discord.ui.TextInput(label="อายุบัญชีขั้นต่ำ (วัน, 0 = ปิด)", default=str(c["raid"]["min_age_days"]), max_length=3)
        self.spam = discord.ui.TextInput(label="สแปม: กี่ข้อความใน 5 วิ", default=str(c["spam"]["msgs"]), max_length=3)
        self.tmo = discord.ui.TextInput(label="Timeout ตอนสแปม (นาที)", default=str(c["spam"]["timeout_min"]), max_length=4)
        for x in (self.nuke, self.raid, self.age, self.spam, self.tmo):
            self.add_item(x)

    async def on_submit(self, i: discord.Interaction):
        try:
            nuke, raid, age = int(self.nuke.value), int(self.raid.value), int(self.age.value)
            spam, tmo = int(self.spam.value), int(self.tmo.value)
            if min(nuke, raid, spam, tmo) < 1 or age < 0:
                raise ValueError
        except ValueError:
            return await i.response.send_message("กรอกเป็นตัวเลขจำนวนเต็มให้ถูกต้อง", ephemeral=True)
        c = self.cog.cfg
        c["nuke"]["limit"], c["raid"]["joins"], c["raid"]["min_age_days"] = nuke, raid, age
        c["spam"]["msgs"], c["spam"]["timeout_min"] = spam, min(tmo, 40320)
        save(c)
        await i.response.send_message("บันทึกแล้ว กด รีเฟรช เพื่อดูค่าใหม่", ephemeral=True)


class ProtectView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=600)
        self.cog = cog

    async def interaction_check(self, i: discord.Interaction):
        ok = await self.cog.bot.is_owner(i.user) or i.user.id == i.guild.owner_id
        if not ok:
            await i.response.send_message("เฉพาะเจ้าของเท่านั้น", ephemeral=True)
        return ok

    async def _flip(self, i, key):
        c = self.cog.cfg
        c[key]["enabled"] = not c[key]["enabled"]
        save(c)
        await i.response.edit_message(embed=self.cog.embed(i.guild), view=self)

    @discord.ui.button(label="Anti-Nuke", emoji="💣", style=discord.ButtonStyle.secondary)
    async def b_nuke(self, i, b):
        await self._flip(i, "nuke")

    @discord.ui.button(label="Anti-Raid", emoji="🚧", style=discord.ButtonStyle.secondary)
    async def b_raid(self, i, b):
        await self._flip(i, "raid")

    @discord.ui.button(label="Anti-Spam", emoji="🔇", style=discord.ButtonStyle.secondary)
    async def b_spam(self, i, b):
        await self._flip(i, "spam")

    @discord.ui.button(label="กันลิงก์เชิญ", emoji="🔗", style=discord.ButtonStyle.secondary)
    async def b_links(self, i, b):
        await self._flip(i, "links")

    @discord.ui.button(label="กันบอทแปลกปลอม", emoji="🤖", style=discord.ButtonStyle.secondary)
    async def b_bots(self, i, b):
        await self._flip(i, "bots")

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text],
                       placeholder="📋 ห้อง log ของระบบป้องกัน")
    async def log_ch(self, i: discord.Interaction, s: discord.ui.ChannelSelect):
        self.cog.cfg["log_channel"] = s.values[0].id
        save(self.cog.cfg)
        await i.response.edit_message(embed=self.cog.embed(i.guild), view=self)

    @discord.ui.select(cls=discord.ui.RoleSelect, min_values=1, max_values=25,
                       placeholder="✅ ไวท์ลิสต์ยศ (เลือกซ้ำ = ลบออก)")
    async def wl_roles(self, i: discord.Interaction, s: discord.ui.RoleSelect):
        cur = set(self.cog.cfg["whitelist_roles"])
        for r in s.values:
            cur ^= {r.id}
        self.cog.cfg["whitelist_roles"] = list(cur)
        save(self.cog.cfg)
        await i.response.edit_message(embed=self.cog.embed(i.guild), view=self)

    @discord.ui.select(cls=discord.ui.UserSelect, min_values=1, max_values=25,
                       placeholder="✅ ไวท์ลิสต์คน (เลือกซ้ำ = ลบออก)")
    async def wl_users(self, i: discord.Interaction, s: discord.ui.UserSelect):
        cur = set(self.cog.cfg["whitelist_users"])
        for u in s.values:
            cur ^= {u.id}
        self.cog.cfg["whitelist_users"] = list(cur)
        save(self.cog.cfg)
        await i.response.edit_message(embed=self.cog.embed(i.guild), view=self)

    @discord.ui.button(label="ตั้งค่าตัวเลข", emoji="⚙️", style=discord.ButtonStyle.primary)
    async def b_num(self, i, b):
        await i.response.send_modal(NumModal(self.cog))

    @discord.ui.button(label="Lockdown (ปิดประตูเซิร์ฟ)", emoji="🔒", style=discord.ButtonStyle.danger)
    async def b_lock(self, i, b):
        c = self.cog.cfg
        c["lockdown"] = not c["lockdown"]
        save(c)
        await self.cog.log(i.guild, "🔒 Lockdown", f"{'เปิด' if c['lockdown'] else 'ปิด'}โดย {i.user.mention}",
                           0xE74C3C if c["lockdown"] else 0x2ECC71)
        await i.response.edit_message(embed=self.cog.embed(i.guild), view=self)

    @discord.ui.button(label="รีเฟรช", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def b_ref(self, i, b):
        await i.response.edit_message(embed=self.cog.embed(i.guild), view=self)


# ───────────────────────── Cog หลัก ─────────────────────────
class Protect(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg = load()
        self.hist = defaultdict(deque)
        self.punished = {}
        self.joins = defaultdict(deque)
        self.raid_until = {}
        self.msgs = defaultdict(deque)
        self.dupes = defaultdict(deque)
        self.cool = {}
        self.app_owner = None

    async def cog_load(self):
        try:
            info = await self.bot.application_info()
            self.app_owner = info.owner.id
        except discord.HTTPException:
            pass

    # ── ตัวช่วย ──
    def trusted(self, guild, uid):
        c = self.cfg
        if uid in (getattr(self.bot.user, "id", None), guild.owner_id, self.app_owner) or uid in c["whitelist_users"]:
            return True
        m = guild.get_member(uid)
        return bool(m and any(r.id in c["whitelist_roles"] for r in m.roles))

    def hit(self, key, window, store=None):
        dq = (store if store is not None else self.hist)[key]
        t = time.monotonic()
        dq.append(t)
        while dq and t - dq[0] > window:
            dq.popleft()
        return len(dq)

    async def log(self, guild, title, desc, color=0xE74C3C):
        cid = self.cfg["log_channel"]
        ch = guild.get_channel(cid) if cid else None
        if ch:
            e = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.now(timezone.utc))
            try:
                await ch.send(embed=e)
            except discord.HTTPException:
                pass

    def embed(self, guild):
        c = self.cfg
        e = discord.Embed(title="🛡️ ระบบป้องกันเซิร์ฟเวอร์", color=0x5865F2)
        e.add_field(name="💣 Anti-Nuke", value=f"{onoff(c['nuke']['enabled'])}\nทำเกิน {c['nuke']['limit']} ครั้งใน {c['nuke']['seconds']} วิ → ถอดยศ + แบน", inline=False)
        e.add_field(name="🚧 Anti-Raid", value=f"{onoff(c['raid']['enabled'])}\nเข้า {c['raid']['joins']} คนใน {c['raid']['seconds']} วิ → เตะ + โหมดบุก {RAID_MODE_SECONDS // 60} นาที\nอายุบัญชีขั้นต่ำ: {c['raid']['min_age_days'] or 'ปิด'} วัน", inline=False)
        e.add_field(name="🔇 Anti-Spam", value=f"{onoff(c['spam']['enabled'])}\n{c['spam']['msgs']} ข้อความใน {c['spam']['seconds']} วิ / ซ้ำ {c['spam']['dupes']} ครั้ง / แท็ก {c['spam']['mentions']}+ คน → ลบ + Timeout {c['spam']['timeout_min']} นาที", inline=False)
        e.add_field(name="🔗 กันลิงก์เชิญ", value=onoff(c["links"]["enabled"]))
        e.add_field(name="🤖 กันบอทแปลกปลอม", value=onoff(c["bots"]["enabled"]))
        e.add_field(name="🔒 Lockdown", value=onoff(c["lockdown"]))
        lc = c["log_channel"]
        e.add_field(name="📋 ห้อง log", value=f"<#{lc}>" if lc else "ยังไม่ตั้ง", inline=False)
        wr = "\n".join(f"<@&{r}>" for r in c["whitelist_roles"]) or "-"
        wu = "\n".join(f"<@{u}>" for u in c["whitelist_users"]) or "-"
        e.add_field(name="✅ ไวท์ลิสต์ยศ", value=wr[:900])
        e.add_field(name="✅ ไวท์ลิสต์คน", value=wu[:900])
        e.set_footer(text="เจ้าเซิร์ฟ / เจ้าของบอท / บอททุกตัว ถูกยกเว้น Anti-Nuke อัตโนมัติ")
        return e

    @app_commands.command(name="protect", description="ตั้งค่าระบบป้องกันเซิร์ฟเวอร์ (เจ้าของเท่านั้น)")
    @app_commands.guild_only()
    async def protect(self, i: discord.Interaction):
        if not (await self.bot.is_owner(i.user) or i.user.id == i.guild.owner_id):
            return await i.response.send_message("เฉพาะเจ้าของเท่านั้น", ephemeral=True)
        await i.response.send_message(embed=self.embed(i.guild), view=ProtectView(self), ephemeral=True)

    # ── Anti-Nuke / กันบอทแปลกปลอม (อ่านจาก Audit Log แบบเรียลไทม์) ──
    async def punish(self, guild, uid, why):
        t = time.monotonic()
        if t - self.punished.get(uid, -999) < 60:
            return
        self.punished[uid] = t
        res = []
        member = guild.get_member(uid)
        if member:
            keep = [r for r in member.roles if not r.is_default() and (r.managed or r >= guild.me.top_role)]
            try:
                await member.edit(roles=keep, reason="Anti-Nuke")
                res.append("ถอดยศทั้งหมดแล้ว")
            except discord.HTTPException:
                res.append("ถอดยศไม่สำเร็จ")
        try:
            await guild.ban(discord.Object(uid), reason=f"Anti-Nuke: {why}", delete_message_seconds=0)
            res.append("แบนแล้ว")
        except discord.HTTPException:
            res.append("แบนไม่สำเร็จ (ยศบอทต้องสูงกว่าเป้าหมาย)")
        await self.log(guild, "🛡️ Anti-Nuke", f"<@{uid}> (`{uid}`)\nสาเหตุ: {why}\nผล: {', '.join(res)}")

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        guild, u, a = entry.guild, entry.user, entry.action
        if u is None or self.trusted(guild, u.id):
            return
        if getattr(u, "bot", False):   # Anti-Nuke ยกเว้นบอททุกตัว
            return
        c = self.cfg
        if a == A.bot_add and c["bots"]["enabled"] and entry.target is not None:
            try:
                await guild.kick(discord.Object(entry.target.id), reason="กันบอทแปลกปลอม")
                res = "เตะบอทออกแล้ว"
            except discord.HTTPException:
                res = "เตะบอทไม่สำเร็จ (ตรวจสิทธิ์/ยศบอท)"
            await self.log(guild, "🤖 บอทแปลกปลอม", f"ผู้เชิญ: <@{u.id}>\nบอท: <@{entry.target.id}> (`{entry.target.id}`)\nผล: {res}")
            return
        if not c["nuke"]["enabled"]:
            return
        if a == A.member_prune:
            return await self.punish(guild, u.id, "สั่ง Prune สมาชิก")
        if a in NUKE_ACTIONS:
            n = self.hit(("nuke", guild.id, u.id), c["nuke"]["seconds"])
            if n >= c["nuke"]["limit"]:
                await self.punish(guild, u.id, f"ทำรายการผิดปกติ {n} ครั้งใน {c['nuke']['seconds']} วิ (ล่าสุด: {a.name})")

    # ── Anti-Raid / Lockdown ──
    async def _kick(self, member, why):
        try:
            await member.guild.kick(member, reason=why)
            return True
        except discord.HTTPException:
            return False

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or self.trusted(member.guild, member.id):
            return
        g, c = member.guild, self.cfg
        if c["lockdown"]:
            if await self._kick(member, "Lockdown"):
                await self.log(g, "🔒 Lockdown", f"เตะ {member.mention} (`{member.id}`) ที่พยายามเข้ามา", 0xE67E22)
            return
        r = c["raid"]
        if not r["enabled"]:
            return
        age = (datetime.now(timezone.utc) - member.created_at).days
        if r["min_age_days"] and age < r["min_age_days"]:
            if await self._kick(member, "บัญชีใหม่เกินไป"):
                await self.log(g, "🚧 บัญชีใหม่", f"เตะ {member.mention} อายุบัญชี {age} วัน (ขั้นต่ำ {r['min_age_days']})", 0xE67E22)
            return
        t = time.monotonic()
        if t < self.raid_until.get(g.id, 0):
            await self._kick(member, "Raid mode")
            return
        dq = self.joins[g.id]
        dq.append((t, member.id))
        while dq and t - dq[0][0] > r["seconds"]:
            dq.popleft()
        if len(dq) >= r["joins"]:
            self.raid_until[g.id] = t + RAID_MODE_SECONDS
            ids = [uid for _, uid in dq]
            dq.clear()
            kicked = 0
            for uid in ids:
                m = g.get_member(uid)
                if m and not self.trusted(g, uid) and await self._kick(m, "Anti-Raid"):
                    kicked += 1
            await self.log(g, "🚧 ตรวจพบการบุก (Raid)",
                           f"มีคนเข้า {len(ids)} คนใน {r['seconds']} วิ\nเตะแล้ว {kicked} คน\n"
                           f"โหมดบุกทำงาน {RAID_MODE_SECONDS // 60} นาที (คนใหม่ที่เข้ามาจะถูกเตะ)")

    # ── Anti-Spam / ลิงก์เชิญ / แท็กรัว ──
    async def _spam_hit(self, m: discord.Message, why):
        key = (m.guild.id, m.author.id)
        t = time.monotonic()
        if t - self.cool.get(key, -999) < 10:
            return
        self.cool[key] = t
        self.msgs.pop(key, None)
        self.dupes.pop(key, None)
        res = []
        try:
            await m.channel.purge(limit=30, check=lambda x: x.author.id == m.author.id
                                  and (datetime.now(timezone.utc) - x.created_at).total_seconds() < 90)
            res.append("ลบข้อความแล้ว")
        except discord.HTTPException:
            res.append("ลบข้อความไม่สำเร็จ")
        mins = self.cfg["spam"]["timeout_min"]
        try:
            await m.author.timeout(timedelta(minutes=mins), reason=f"Anti-Spam: {why}")
            res.append(f"Timeout {mins} นาที")
        except discord.HTTPException:
            res.append("Timeout ไม่สำเร็จ")
        await self.log(m.guild, "🔇 Anti-Spam", f"{m.author.mention} ใน {m.channel.mention}\nสาเหตุ: {why}\nผล: {', '.join(res)}", 0xE67E22)

    @commands.Cog.listener()
    async def on_message(self, m: discord.Message):
        if m.author.bot or m.guild is None or not isinstance(m.author, discord.Member):
            return
        c = self.cfg
        p = m.author.guild_permissions
        if p.administrator or p.manage_messages or self.trusted(m.guild, m.author.id):
            return

        if c["links"]["enabled"] and INVITE_RE.search(m.content or ""):
            try:
                await m.delete()
            except discord.HTTPException:
                pass
            await self.log(m.guild, "🔗 ลิงก์เชิญ", f"ลบข้อความของ {m.author.mention} ใน {m.channel.mention}", 0xE67E22)
            try:
                await m.channel.send(f"{m.author.mention} ห้ามส่งลิงก์เชิญเซิร์ฟเวอร์อื่น", delete_after=6)
            except discord.HTTPException:
                pass
            return

        s = c["spam"]
        if not s["enabled"]:
            return
        key = (m.guild.id, m.author.id)

        mentions = len({u.id for u in m.mentions}) + len({r.id for r in m.role_mentions})
        if mentions >= s["mentions"]:
            return await self._spam_hit(m, f"แท็กรัว ({mentions} รายการ)")

        if self.hit(key, s["seconds"], self.msgs) >= s["msgs"]:
            return await self._spam_hit(m, f"ส่งข้อความรัว ({s['msgs']}+ ข้อความใน {s['seconds']} วิ)")

        if m.content:
            dq = self.dupes[key]
            t = time.monotonic()
            dq.append((t, m.content))
            while dq and t - dq[0][0] > 20:
                dq.popleft()
            if sum(1 for _, x in dq if x == m.content) >= s["dupes"]:
                await self._spam_hit(m, "ส่งข้อความซ้ำ ๆ")


async def setup(bot: commands.Bot):
    await bot.add_cog(Protect(bot))
