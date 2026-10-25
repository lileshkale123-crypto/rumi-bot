"""Social action commands: hug, kiss, pat, slap ... with nekos.best GIFs."""
import random
import httpx
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from ax_common import esc

H = ParseMode.HTML
GIF = {"hug": "hug", "kiss": "kiss", "pat": "pat", "slap": "slap",
       "punch": "punch", "bite": "bite", "poke": "poke", "wink": "wink",
       "cuddle": "cuddle", "bonk": "bonk", "murder": "shoot"}
ICON = {"hug": "🤗", "kiss": "💋", "pat": "🫶", "slap": "👋", "punch": "👊",
        "bite": "🦷", "poke": "👉", "wink": "😉", "cuddle": "💞",
        "bonk": "🔨", "murder": "🔪"}
TITLE = {"hug": "Warm Hug", "kiss": "Sweet Kiss", "pat": "Gentle Pat",
         "slap": "Surprise Slap", "punch": "Mighty Punch",
         "bite": "Playful Bite", "poke": "Little Poke",
         "wink": "Charming Wink", "cuddle": "Cozy Cuddle",
         "bonk": "Bonk Alert", "murder": "Dramatic Murder"}
LINES = {
    "hug": [
        "{a} opened both arms wide and pulled {b} into the warmest, softest hug of the day, the kind that melts every worry away.",
        "{a} wrapped {b} in a long, gentle hug and whispered that everything will be okay, because it always is with a friend like this.",
        "{a} ran over and gave {b} a hug so cozy that the whole chat felt a little brighter.",
        "{a} held {b} close for a quiet moment, a hug that said more than any words could.",
        "{a} stepped in and hugged {b} tight, sharing a little sunshine on a not so sunny day.",
    ],
    "kiss": [
        "{a} leaned in and left a soft, sweet kiss on the cheek of {b}, and the whole room went rosy.",
        "{a} gently kissed {b} on the forehead, a tiny gesture full of care and a lot of heart.",
        "{a} blew a dreamy kiss across the chat and it landed right on {b}, who is now blushing.",
        "{a} shyly kissed the hand of {b} like a true gentleperson, and the moment felt like a movie scene.",
        "{a} stole a sweet little kiss from {b}, and the butterflies have not stopped since.",
    ],
    "pat": [
        "{a} softly patted the head of {b} and said you did so well today, and {b} glowed with pride.",
        "{a} gave {b} the gentlest pat, the kind that says I am proud of you without a single word.",
        "{a} patted {b} with the calmest, kindest hand, and for a second the world felt peaceful.",
        "{a} reached out and gave {b} a warm, comforting pat, because every hero deserves some praise.",
        "{a} patted {b} gently and smiled, a small moment that made the whole day better.",
    ],
    "slap": [
        "{a} delivered a dramatic, perfectly timed slap to {b}, and the chat gasped in elegant unison.",
        "{a} swung with great style and slapped {b}, who is now questioning every choice that led here.",
        "{a} gave {b} a slap so theatrical that it deserves its own round of applause.",
        "{a} slapped {b} with total confidence, then calmly fixed their hair like nothing happened.",
        "{a} walked up with a royal expression and slapped {b}, and the silence afterwards was priceless.",
    ],
    "punch": [
        "{a} threw a mighty punch at {b}, and the sparkles of justice flew everywhere.",
        "{a} wound up like a champion and punched {b}, who is now seeing stars and tiny hearts.",
        "{a} landed a powerful punch on {b} with the grace of a true fighter.",
        "{a} punched {b} so heroically that the whole group wants a replay in slow motion.",
        "{a} unleashed an epic punch on {b}, and somewhere a dramatic soundtrack began to play.",
    ],
    "bite": [
        "{a} playfully nibbled on {b}, and now {b} is both shocked and a little bit flattered.",
        "{a} took a tiny, cute bite out of {b}, leaving behind a very surprised smile.",
        "{a} could not resist and gently bit {b}, who squeaked in the most adorable way.",
        "{a} gave {b} a little chomp, the soft kind that only close friends are allowed to give.",
        "{a} sneaked up and bit {b}, then ran away giggling like a mischievous kitten.",
    ],
    "poke": [
        "{a} gently poked {b} again and again until {b} finally gave in and smiled.",
        "{a} tiptoed over and poked {b} just to say hello, with a very innocent look.",
        "{a} poked {b} on the shoulder, a tiny nudge that said please notice me.",
        "{a} could not help it and poked {b}, who turned around with the cutest confused face.",
        "{a} gave {b} a playful little poke, because sometimes friends just need attention.",
    ],
    "wink": [
        "{a} gave {b} a charming wink across the chat, and {b} is still trying to act cool about it.",
        "{a} winked at {b} with perfect confidence, leaving a trail of sparkles behind.",
        "{a} flashed a smooth, secret wink at {b}, and now everyone is wondering what it meant.",
        "{a} winked at {b} like it was the most natural thing in the world, and it worked.",
        "{a} sent a playful wink to {b}, a tiny moment that made the whole day sweeter.",
    ],
    "cuddle": [
        "{a} curled up next to {b} under the softest blanket, and the world finally felt calm.",
        "{a} cuddled {b} close and rested a head on their shoulder, a perfect cozy moment.",
        "{a} pulled {b} into a long, warm cuddle, the kind you never want to end.",
        "{a} snuggled up to {b} like a sleepy kitten looking for the warmest spot.",
        "{a} and {b} are wrapped up in a cozy cuddle, and nothing else matters right now.",
    ],
    "bonk": [
        "{a} bonked {b} on the head with a soft golden hammer and declared: go sit in the quiet corner and think about it.",
        "{a} delivered a swift and very official bonk to {b}, and the case is now closed.",
        "{a} raised a tiny golden hammer and bonked {b} right on the head, with perfect aim.",
        "{a} bonked {b} so lightly and so stylishly that even {b} had to laugh.",
        "{a} gave {b} a firm, friendly bonk, a gentle reminder to behave in the group.",
    ],
    "murder": [
        "{a} dramatically eliminated {b} in a stylish, totally fictional showdown that nobody will ever forget.",
        "{a} took aim with deadly elegance and sent {b} out of the story, all in good fun of course.",
        "{a} pulled off a perfectly planned and completely pretend finale for {b}, with great flair.",
        "{a} ended {b} in the most cinematic way possible, and the credits started rolling right away.",
        "{a} and a very dramatic plot twist took down {b}, and the chat is still clapping for the show.",
    ],
}

FOOT = ["Spread the love in Rumi.", "Made with care by Rumi.",
        "Rumi approves this moment.", "Another cute moment in the chat."]


def mention(u):
    return '<b><a href="tg://user?id=%d">%s</a></b>' % (
        u.id, esc(u.first_name or "Someone"))


async def fetch_gif(action):
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://nekos.best/api/v2/" + GIF[action])
            return r.json()["results"][0]["url"]
    except Exception:
        return None


def make(cmd):
    async def handler(update, context):
        msg = update.effective_message
        a = update.effective_user
        if not msg or not a:
            return
        r = msg.reply_to_message
        if not r or not r.from_user:
            await msg.reply_text(
                "%s <b>Reply to someone</b> with /%s"
                % (ICON[cmd], cmd), parse_mode=H)
            return
        b = r.from_user
        line = random.choice(LINES[cmd]).format(a=mention(a), b=mention(b))
        cap = ("%s <b>%s</b>\n<blockquote>%s</blockquote>\n<i>%s</i>"
               % (ICON[cmd], TITLE[cmd], line, random.choice(FOOT)))
        url = await fetch_gif(cmd)
        if url:
            try:
                await msg.reply_animation(url, caption=cap, parse_mode=H)
                return
            except Exception:
                pass
        await msg.reply_text(cap, parse_mode=H)
    return handler


def register(app):
    for c in GIF:
        app.add_handler(CommandHandler(c, make(c)))
