"""Gifts: /giftshop (buy), /mygifts (inventory), /sendgift <name> (reply to someone).
GIFs come from the free Giphy API (needs GIPHY_API_KEY in .env). A different GIF each time."""
import logging
import os
import random
import re
import time

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

import config
from ax_common import esc
from services import ensure_user_and_wallet

log = logging.getLogger(__name__)
H = ParseMode.HTML

# ---------------------------------------------------------------------------
# GIFT LIST. To add a gift, copy one line and change it.
# id: (emoji, name, price, [giphy search words], note)
# ---------------------------------------------------------------------------
ITEMS = {
    "rose": ("🌹", "Rose", 1500, ["red rose flower", "rose flower animated"],
             "A little flower to say you matter."),
    "tulip": ("🌷", "Tulip", 2500, ["tulip flowers", "pink tulips bloom"],
              "Fresh tulips for someone who makes the chat brighter."),
    "bouquet": ("💐", "Bouquet", 6000, ["flower bouquet", "bouquet of flowers"],
                "A whole bouquet, because one flower was not enough."),
    "loveletter": ("💌", "Love Letter", 4000, ["love letter", "love letter heart envelope"],
                   "Every word was written straight from the heart."),
    "boyfriend": ("👨", "Boyfriend", 10000, ["couple love cute", "boyfriend girlfriend love"],
                  "Handsome, loyal and now officially yours."),
    "girlfriend": ("👩", "Girlfriend", 15000, ["cute couple love", "couple hug cute"],
                   "Relationship status: officially upgraded."),
    "surprisebox": ("🎁", "Surprise Box", 5000, ["surprise gift box", "opening gift box"],
                    "Nobody knows what is inside, not even the sender."),
    "diamond": ("💎", "Diamond", 40000, ["diamond sparkle", "shiny diamond"],
                "Rare, shiny and absolutely priceless."),
    "crown": ("👑", "Crown", 22000, ["gold crown", "king queen crown"],
              "Long live the royalty of this chat."),
    "lamborghini": ("🏎", "Lamborghini", 60000, ["lamborghini", "lamborghini supercar"],
                    "Engine roaring, heads turning."),
    "ferrari": ("🚗", "Ferrari", 75000, ["ferrari", "ferrari supercar"],
                "Red, fast and unforgettable."),
    "yacht": ("🛥", "Yacht", 150000, ["luxury yacht", "yacht ocean"],
              "Sunset, waves and a whole yacht just for you."),
    "mansion": ("🏰", "Mansion", 300000, ["luxury mansion", "luxury house"],
                "A whole mansion. Please invite everyone to the party."),
    "jet": ("✈️", "Private Jet", 500000, ["private jet", "private jet luxury"],
            "Boarding now. Destination: wherever you like."),
}
BOX = "surprisebox"

# Other ways people may type a gift name.
ALIASES = {
    "gf": "girlfriend", "girl": "girlfriend", "bf": "boyfriend", "boy": "boyfriend",
    "love": "loveletter", "letter": "loveletter", "box": BOX, "surprise": BOX,
    "lambo": "lamborghini", "plane": "jet", "privatejet": "jet", "home": "mansion",
    "house": "mansion", "flowers": "bouquet", "flower": "rose", "gem": "diamond",
}


def resolve(words):
    key = re.sub(r"[^a-z]", "", " ".join(words).lower())
    if key in ITEMS:
        return key
    return ALIASES.get(key)


def money(n):
    return f"{config.CURRENCY_SYMBOL}{n:,}"


def mention(u):
    return '<b><a href="tg://user?id=%d">%s</a></b>' % (u.id, esc(u.first_name or "Someone"))


# ---------------------------------------------------------------------------
# GIPHY
# ---------------------------------------------------------------------------
_CACHE = {}        # search words -> (time, [urls])
_RECENT = {}       # item id -> last few urls used (so GIFs do not repeat)
TTL = 3600


async def _search(q):
    hit = _CACHE.get(q)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    key = os.environ.get("GIPHY_API_KEY")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://api.giphy.com/v1/gifs/search",
                            params={"api_key": key, "q": q, "limit": 50,
                                    "rating": "pg", "lang": "en"})
            data = r.json().get("data", [])
    except Exception as e:
        log.warning("giphy failed: %s", e)
        return []
    urls = []
    for d in data:
        im = d.get("images", {})
        mp4 = im.get("original_mp4", {})
        if mp4.get("mp4") and int(mp4.get("mp4_size") or 0) < 8_000_000:
            urls.append(mp4["mp4"])
        elif im.get("downsized", {}).get("url"):
            urls.append(im["downsized"]["url"])
    if urls:
        _CACHE[q] = (time.time(), urls)
    return urls


async def get_gif(iid):
    words = ITEMS[iid][3]
    urls = await _search(random.choice(words))
    if not urls:
        return None
    recent = _RECENT.setdefault(iid, [])
    fresh = [u for u in urls if u not in recent] or urls
    pick = random.choice(fresh)
    recent.append(pick)
    del recent[:-8]
    return pick


# ---------------------------------------------------------------------------
# MONEY + INVENTORY (all inside one database transaction)
# ---------------------------------------------------------------------------
async def _pay(c, uid, price):
    """Returns ('ok', cash_after) / ('poor', cash) / ('nowallet', 0). Call inside a transaction."""
    w = await c.fetchrow("SELECT cash FROM wallets WHERE user_id=$1 FOR UPDATE", uid)
    if w is None:
        return "nowallet", 0
    before = w["cash"]
    if before < price:
        return "poor", before
    await c.execute("UPDATE wallets SET cash=cash-$1, updated_at=now() WHERE user_id=$2", price, uid)
    await c.execute(
        "INSERT INTO transactions (user_id,type,currency,amount,balance_before,balance_after,source) "
        "VALUES ($1,'debit','cash',$2,$3,$4,'gift')", uid, price, before, before - price)
    return "ok", before - price


async def _inv(pool, uid):
    rows = await pool.fetch("SELECT item_id, qty FROM ax_gift_inv WHERE user_id=$1 AND qty>0", uid)
    return {r["item_id"]: r["qty"] for r in rows}


# ---------------------------------------------------------------------------
# /giftshop
# ---------------------------------------------------------------------------
async def _shop_view(pool, uid, name):
    inv = await _inv(pool, uid)
    w = await pool.fetchrow("SELECT cash FROM wallets WHERE user_id=$1", uid)
    cash = w["cash"] if w else 0
    lines, btns = [], []
    for iid, (e, n, p, _, _) in ITEMS.items():
        have = f"  <i>(you have {inv[iid]})</i>" if iid in inv else ""
        lines.append(f"{e} <b>{n}</b> — {money(p)}{have}")
        btns.append(InlineKeyboardButton(f"{e} {n}", callback_data=f"axgift:buy:{iid}:{uid}"))
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    text = (f"🎁 <b>Rumi Gift Shop</b>\n\n<b>{esc(name)}</b>, tap a gift to buy it 💜\n\n"
            f"<blockquote>{chr(10).join(lines)}</blockquote>\n"
            f"You have <code>{money(cash)}</code>\n"
            f"Send one: reply to someone with <code>/sendgift rose</code>\n"
            f"See what you own: /mygifts")
    return text, InlineKeyboardMarkup(rows)


async def giftshop_cmd(update, context):
    u = update.effective_user
    pool = context.bot_data["pool"]
    await ensure_user_and_wallet(pool, u.id, u.username, u.first_name)
    text, kb = await _shop_view(pool, u.id, u.first_name or "Friend")
    await update.effective_message.reply_text(text, parse_mode=H, reply_markup=kb)


async def gift_cb(update, context):
    q = update.callback_query
    parts = q.data.split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await q.answer()
        return
    _, act, iid, uid = parts
    uid = int(uid)
    if q.from_user.id != uid:
        await q.answer("This shop belongs to someone else 💜", show_alert=True)
        return
    if act != "buy" or iid not in ITEMS:
        await q.answer()
        return
    pool = context.bot_data["pool"]
    e, n, price, _, _ = ITEMS[iid]
    async with pool.acquire() as c:
        async with c.transaction():
            st, cash = await _pay(c, uid, price)
            if st == "ok":
                row = await c.fetchrow(
                    "INSERT INTO ax_gift_inv (user_id,item_id,qty) VALUES ($1,$2,1) "
                    "ON CONFLICT (user_id,item_id) DO UPDATE SET qty=ax_gift_inv.qty+1 RETURNING qty",
                    uid, iid)
    if st == "nowallet":
        await q.answer("Send /start to Rumi first 💜", show_alert=True)
        return
    if st == "poor":
        await q.answer(f"Not enough cash 💔 You have {money(cash)}", show_alert=True)
        return
    await q.answer(f"Bought {e} {n} 💜 You now have {row['qty']}. Send it with /sendgift {iid}",
                   show_alert=True)
    text, kb = await _shop_view(pool, uid, q.from_user.first_name or "Friend")
    try:
        await q.edit_message_text(text, parse_mode=H, reply_markup=kb)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /mygifts
# ---------------------------------------------------------------------------
async def mygifts_cmd(update, context):
    u = update.effective_user
    msg = update.effective_message
    pool = context.bot_data["pool"]
    target = u
    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        target = msg.reply_to_message.from_user
    inv = await _inv(pool, target.id)
    rows = await pool.fetch("SELECT item_id, qty FROM ax_gift_recv WHERE user_id=$1 AND qty>0", target.id)
    got = {r["item_id"]: r["qty"] for r in rows}
    own = "\n".join(f"{ITEMS[i][0]} {ITEMS[i][1]} × <b>{q}</b>" for i, q in inv.items() if i in ITEMS)
    rec = "\n".join(f"{ITEMS[i][0]} {ITEMS[i][1]} × <b>{q}</b>" for i, q in got.items() if i in ITEMS)
    text = f"🎒 <b>{esc(target.first_name or 'Friend')}'s Gifts</b>\n\n"
    text += f"<b>To send</b>\n<blockquote>{own or 'Nothing yet. Visit /giftshop'}</blockquote>\n"
    text += f"<b>Received</b>\n<blockquote>{rec or 'No gifts received yet'}</blockquote>"
    if target.id == u.id:
        text += "\nSend one: reply to someone with <code>/sendgift rose</code>"
    await msg.reply_text(text, parse_mode=H)


# ---------------------------------------------------------------------------
# /sendgift
# ---------------------------------------------------------------------------
async def sendgift_cmd(update, context):
    msg = update.effective_message
    a = update.effective_user
    if not msg or not a:
        return
    r = msg.reply_to_message
    if not context.args or not r or not r.from_user:
        await msg.reply_text(
            "🎁 <b>Reply to someone</b> with <code>/sendgift rose</code>\n"
            "See all gifts: /giftshop   Your gifts: /mygifts", parse_mode=H)
        return
    b = r.from_user
    if b.is_bot or b.id == a.id:
        await msg.reply_text("You can only gift another person 💜")
        return
    iid = resolve(context.args)
    if iid is None:
        await msg.reply_text("I don't know that gift 💔 See /giftshop")
        return
    pool = context.bot_data["pool"]
    await ensure_user_and_wallet(pool, a.id, a.username, a.first_name)
    e, n, price, _, note = ITEMS[iid]

    shown = iid
    if iid == BOX:
        pool_ids = [i for i in ITEMS if i != BOX]
        shown = random.choices(pool_ids, weights=[1 / ITEMS[i][2] ** 0.5 for i in pool_ids])[0]

    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "UPDATE ax_gift_inv SET qty=qty-1 WHERE user_id=$1 AND item_id=$2 AND qty>0 RETURNING qty",
                a.id, iid)
            if row:
                st, cash, left = "inv", 0, row["qty"]
            else:
                st, cash = await _pay(c, a.id, price)
                left = 0
            if st in ("inv", "ok"):
                await c.execute(
                    "INSERT INTO ax_gift_recv (user_id,item_id,qty) VALUES ($1,$2,1) "
                    "ON CONFLICT (user_id,item_id) DO UPDATE SET qty=ax_gift_recv.qty+1", b.id, shown)
    if st == "nowallet":
        await msg.reply_text("Send /start to Rumi first 💜")
        return
    if st == "poor":
        await msg.reply_text(
            f"💔 You don't have a {e} {n} and need {money(price)} to send it "
            f"(you have {money(cash)}). Try /giftshop", parse_mode=H)
        return

    se, sn, _, _, snote = ITEMS[shown]
    if iid == BOX:
        story = (f"{mention(a)} gifted {mention(b)} a 🎁 <b>Surprise Box</b>\n"
                 f"It opened and inside was {se} <b>{sn}</b>!\n<i>{snote}</i>")
        head = "🎁 <b>Surprise Box</b>"
    else:
        story = f"{mention(a)} gifted {mention(b)} {e} <b>{n}</b>\n<i>{note}</i>"
        head = f"{e} <b>{n} Gift</b>"
    if st == "inv":
        foot = f"🎒 Used from your gifts (left: <b>{left}</b>)"
    else:
        foot = f"💸 Deducted: <b>-{money(price)}</b>"
    cap = f"{head}\n<blockquote>{story}</blockquote>\n{foot}"

    url = await get_gif(shown)
    if url:
        try:
            await msg.reply_animation(url, caption=cap, parse_mode=H)
            return
        except Exception as ex:
            log.warning("gift gif send failed: %s", ex)
    await msg.reply_text(cap, parse_mode=H)


def register(app):
    app.add_handler(CommandHandler("giftshop", giftshop_cmd))
    app.add_handler(CommandHandler("mygifts", mygifts_cmd))
    app.add_handler(CommandHandler("sendgift", sendgift_cmd))
    app.add_handler(CallbackQueryHandler(gift_cb, pattern=r"^axgift:"))
