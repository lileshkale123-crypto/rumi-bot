"""Random group events: a Catch drop (/catch name) and a Number Guess drop (/g 250).

Rules: a group needs EVENT_MIN_GROUP_MEMBERS members, gets at most EVENT_MAX_PER_GROUP_PER_DAY events a day
(IST), and an event only drops while people are chatting, at a random moment.
Guess image: put it at assets/guess_event.jpg (or .png), or set GUESS_EVENT_IMAGE to a Telegram file_id / URL.
Owners can test with /spawnevent catch or /spawnevent guess in a group.
"""
import asyncio
import logging
import os
import random
import re
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone

import aiohttp
import asyncpg
from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import bot_context
import config
import services
from ax_common import cmd_args, esc
from bot_context import BotContext

log = logging.getLogger(__name__)
router = Router(name="ax_eventspawn")
H = ParseMode.HTML
IST = timezone(timedelta(hours=5, minutes=30))
HERE = os.path.dirname(os.path.abspath(__file__))

MIN_MEMBERS = int(getattr(config, "EVENT_MIN_GROUP_MEMBERS", 25))
MAX_PER_DAY = int(getattr(config, "EVENT_MAX_PER_GROUP_PER_DAY", 1))
CATCH_REWARD = int(getattr(config, "CATCH_REWARD_COINS", 2000))
GUESS_REWARD = (int(getattr(config, "EVENT_REWARD_MIN", 500)), int(getattr(config, "EVENT_REWARD_MAX", 1000)))
GUESS_RANGE = (int(getattr(config, "GUESS_MIN_NUMBER", 100)), int(getattr(config, "GUESS_MAX_NUMBER", 500)))
GUESS_TRIES = int(getattr(config, "GUESS_MAX_TRIES", 12))
EVENT_LIFETIME = 24 * 60 * 60     # an event stays until someone wins it (or 24 hours pass)
_CTX: dict[int, dict] = {}        # bot_id -> {'bot':..., 'pool':...}
_LAST: dict[tuple[int, int], float] = {}   # (bot, chat) -> when the last event dropped
_SKIP: set = set()
_MSGS_READY = False

FALLBACK_CHARACTERS = [
    "Naruto Uzumaki", "Sasuke Uchiha", "Monkey D. Luffy", "Roronoa Zoro", "Son Goku", "Vegeta",
    "Light Yagami", "Levi Ackerman", "Eren Yeager", "Mikasa Ackerman", "Tanjiro Kamado", "Nezuko Kamado",
    "Zenitsu Agatsuma", "Gojo Satoru", "Yuji Itadori", "Megumi Fushiguro", "Izuku Midoriya", "Katsuki Bakugo",
    "Edward Elric", "Alphonse Elric", "Lelouch Lamperouge", "Spike Spiegel", "Killua Zoldyck", "Gon Freecss",
    "Hinata Hyuga", "Itachi Uchiha", "Kakashi Hatake", "Rem", "Asuna Yuuki", "Kirito", "Saitama", "Genos",
    "Anya Forger", "Loid Forger", "Yor Forger", "Makima", "Denji", "Power", "Zero Two", "Misa Amane",
]

# LUNASPAWN_V1  Luna-style catch drop: same pool, same texts, nekos.best wink GIF
CHARACTER_POOL = [
    {"name": "Raiden", "title": "Raiden Shogun", "anime": "Genshin Impact", "rarity": "🌟 Mythic", "reward": 2500},
    {"name": "Gojo", "title": "Satoru Gojo", "anime": "Jujutsu Kaisen", "rarity": "🔮 Celestial", "reward": 3000},
    {"name": "Makima", "title": "Makima", "anime": "Chainsaw Man", "rarity": "🔥 Legendary", "reward": 2200},
    {"name": "Mikasa", "title": "Mikasa Ackerman", "anime": "Attack on Titan", "rarity": "💎 Epic", "reward": 1800},
    {"name": "ZeroTwo", "title": "Zero Two", "anime": "Darling in the Franxx", "rarity": "🌸 Mythic", "reward": 2600},
    {"name": "Levi", "title": "Levi Ackerman", "anime": "Attack on Titan", "rarity": "⚔️ Legendary", "reward": 2400},
    {"name": "Yor", "title": "Yor Forger", "anime": "Spy x Family", "rarity": "🌹 Epic", "reward": 1900},
    {"name": "Sukuna", "title": "Ryomen Sukuna", "anime": "Jujutsu Kaisen", "rarity": "🩸 Celestial", "reward": 3200},
]

# SPAWNPOOL_V1  ~100 characters. Rarer tiers spawn less often (weights are per tier, not per character).
_TIER = {
    "C": ("🔮 Celestial", 3000),
    "M": ("🌟 Mythic", 2500),
    "L": ("🔥 Legendary", 2200),
    "E": ("💎 Epic", 1800),
}
TIER_CHANCE = {"Celestial": 5, "Mythic": 15, "Legendary": 30, "Epic": 50}   # percent per spawn
_MORE = [
    # Celestial
    ("Goku", "Son Goku", "Dragon Ball", "C"), ("Saitama", "Saitama", "One Punch Man", "C"),
    ("Madara", "Madara Uchiha", "Naruto", "C"), ("Aizen", "Sosuke Aizen", "Bleach", "C"),
    ("Meruem", "Meruem", "Hunter x Hunter", "C"), ("Newgate", "Edward Newgate", "One Piece", "C"),
    ("Anos", "Anos Voldigoad", "Misfit of Demon King Academy", "C"), ("Rimuru", "Rimuru Tempest", "Reincarnated as a Slime", "C"),
    ("Yhwach", "Yhwach", "Bleach", "C"), ("Escanor", "Escanor", "Seven Deadly Sins", "C"),
    # Mythic
    ("Naruto", "Naruto Uzumaki", "Naruto", "M"), ("Sasuke", "Sasuke Uchiha", "Naruto", "M"),
    ("Itachi", "Itachi Uchiha", "Naruto", "M"), ("Luffy", "Monkey D. Luffy", "One Piece", "M"),
    ("Ichigo", "Ichigo Kurosaki", "Bleach", "M"), ("Lelouch", "Lelouch Lamperouge", "Code Geass", "M"),
    ("Light", "Light Yagami", "Death Note", "M"), ("Eren", "Eren Yeager", "Attack on Titan", "M"),
    ("Rem", "Rem", "Re:Zero", "M"), ("Asta", "Asta", "Black Clover", "M"),
    ("Vegeta", "Vegeta", "Dragon Ball", "M"), ("Tanjiro", "Tanjiro Kamado", "Demon Slayer", "M"),
    ("Shanks", "Shanks", "One Piece", "M"), ("Kaguya", "Kaguya Shinomiya", "Kaguya-sama: Love is War", "M"),
    ("Albedo", "Albedo", "Overlord", "M"), ("Ainz", "Ainz Ooal Gown", "Overlord", "M"),
    ("Toji", "Toji Fushiguro", "Jujutsu Kaisen", "M"), ("Muzan", "Muzan Kibutsuji", "Demon Slayer", "M"),
    # Legendary
    ("Zoro", "Roronoa Zoro", "One Piece", "L"), ("Sanji", "Vinsmoke Sanji", "One Piece", "L"),
    ("Nami", "Nami", "One Piece", "L"), ("Robin", "Nico Robin", "One Piece", "L"),
    ("Ace", "Portgas D. Ace", "One Piece", "L"), ("Kakashi", "Kakashi Hatake", "Naruto", "L"),
    ("Hinata", "Hinata Hyuga", "Naruto", "L"), ("Gaara", "Gaara", "Naruto", "L"),
    ("Killua", "Killua Zoldyck", "Hunter x Hunter", "L"), ("Gon", "Gon Freecss", "Hunter x Hunter", "L"),
    ("Hisoka", "Hisoka Morow", "Hunter x Hunter", "L"), ("Kurapika", "Kurapika", "Hunter x Hunter", "L"),
    ("Edward", "Edward Elric", "Fullmetal Alchemist", "L"), ("Spike", "Spike Spiegel", "Cowboy Bebop", "L"),
    ("Kirito", "Kirito", "Sword Art Online", "L"), ("Asuna", "Asuna Yuuki", "Sword Art Online", "L"),
    ("Deku", "Izuku Midoriya", "My Hero Academia", "L"), ("Bakugo", "Katsuki Bakugo", "My Hero Academia", "L"),
    ("Todoroki", "Shoto Todoroki", "My Hero Academia", "L"), ("Nezuko", "Nezuko Kamado", "Demon Slayer", "L"),
    ("Rengoku", "Kyojuro Rengoku", "Demon Slayer", "L"), ("Megumin", "Megumin", "KonoSuba", "L"),
    ("Mai", "Mai Sakurajima", "Bunny Girl Senpai", "L"), ("Power", "Power", "Chainsaw Man", "L"),
    ("Denji", "Denji", "Chainsaw Man", "L"), ("Yuji", "Yuji Itadori", "Jujutsu Kaisen", "L"),
    ("Megumi", "Megumi Fushiguro", "Jujutsu Kaisen", "L"), ("Nobara", "Nobara Kugisaki", "Jujutsu Kaisen", "L"),
    ("Erza", "Erza Scarlet", "Fairy Tail", "L"),
    # Epic
    ("Natsu", "Natsu Dragneel", "Fairy Tail", "E"), ("Lucy", "Lucy Heartfilia", "Fairy Tail", "E"),
    ("Anya", "Anya Forger", "Spy x Family", "E"), ("Loid", "Loid Forger", "Spy x Family", "E"),
    ("Usopp", "Usopp", "One Piece", "E"), ("Chopper", "Tony Tony Chopper", "One Piece", "E"),
    ("Shinobu", "Shinobu Kocho", "Demon Slayer", "E"), ("Zenitsu", "Zenitsu Agatsuma", "Demon Slayer", "E"),
    ("Inosuke", "Inosuke Hashibira", "Demon Slayer", "E"), ("Sakura", "Sakura Haruno", "Naruto", "E"),
    ("Jiraiya", "Jiraiya", "Naruto", "E"), ("Shikamaru", "Shikamaru Nara", "Naruto", "E"),
    ("Armin", "Armin Arlert", "Attack on Titan", "E"), ("Hange", "Hange Zoe", "Attack on Titan", "E"),
    ("Erwin", "Erwin Smith", "Attack on Titan", "E"), ("Misa", "Misa Amane", "Death Note", "E"),
    ("L", "L Lawliet", "Death Note", "E"), ("Ryuk", "Ryuk", "Death Note", "E"),
    ("Emilia", "Emilia", "Re:Zero", "E"), ("Subaru", "Subaru Natsuki", "Re:Zero", "E"),
    ("Aqua", "Aqua", "KonoSuba", "E"), ("Kazuma", "Kazuma Satou", "KonoSuba", "E"),
    ("Holo", "Holo", "Spice and Wolf", "E"), ("Violet", "Violet Evergarden", "Violet Evergarden", "E"),
    ("Frieren", "Frieren", "Frieren", "E"), ("Fern", "Fern", "Frieren", "E"),
    ("Himmel", "Himmel", "Frieren", "E"), ("Marin", "Marin Kitagawa", "My Dress-Up Darling", "E"),
    ("Chika", "Chika Fujiwara", "Kaguya-sama: Love is War", "E"), ("Bocchi", "Hitori Gotoh", "Bocchi the Rock!", "E"),
    ("Shoyo", "Shoyo Hinata", "Haikyuu!!", "E"), ("Kageyama", "Tobio Kageyama", "Haikyuu!!", "E"),
    ("Mob", "Shigeo Kageyama", "Mob Psycho 100", "E"), ("Reigen", "Arataka Reigen", "Mob Psycho 100", "E"),
    ("Senku", "Senku Ishigami", "Dr. Stone", "E"), ("Kaneki", "Ken Kaneki", "Tokyo Ghoul", "E"),
    ("Alucard", "Alucard", "Hellsing", "E"),
]
CHARACTER_POOL += [
    {"name": n, "title": t, "anime": a, "rarity": _TIER[k][0], "reward": _TIER[k][1]} for n, t, a, k in _MORE
]

# SPAWNPOOL2_V1  extra characters, one per line: short|full name|anime|tier (C/M/L/E)
_MORE2 = """
Law|Trafalgar Law|One Piece|L
Kaido|Kaido|One Piece|M
BigMom|Charlotte Linlin|One Piece|M
Hancock|Boa Hancock|One Piece|L
Mihawk|Dracule Mihawk|One Piece|M
Brook|Brook|One Piece|E
Franky|Franky|One Piece|E
Jinbe|Jinbe|One Piece|E
Yamato|Yamato|One Piece|L
Blackbeard|Marshall D. Teach|One Piece|M
Garp|Monkey D. Garp|One Piece|M
Roger|Gol D. Roger|One Piece|C
Sabo|Sabo|One Piece|L
Doflamingo|Donquixote Doflamingo|One Piece|L
Crocodile|Crocodile|One Piece|E
Kizaru|Borsalino|One Piece|L
Akainu|Sakazuki|One Piece|L
Aokiji|Kuzan|One Piece|L
Katakuri|Charlotte Katakuri|One Piece|L
Vivi|Nefertari Vivi|One Piece|E
Buggy|Buggy|One Piece|E
Smoker|Smoker|One Piece|E
Rayleigh|Silvers Rayleigh|One Piece|M
Kid|Eustass Kid|One Piece|L
Enel|Enel|One Piece|E
Lucci|Rob Lucci|One Piece|E
Imu|Imu|One Piece|C
Minato|Minato Namikaze|Naruto|M
Obito|Obito Uchiha|Naruto|M
Hashirama|Hashirama Senju|Naruto|C
Tobirama|Tobirama Senju|Naruto|L
Pain|Nagato|Naruto|M
Konan|Konan|Naruto|E
Tsunade|Tsunade|Naruto|L
Orochimaru|Orochimaru|Naruto|L
Kisame|Kisame Hoshigaki|Naruto|E
Deidara|Deidara|Naruto|E
Sasori|Sasori|Naruto|E
Hidan|Hidan|Naruto|E
Kakuzu|Kakuzu|Naruto|E
RockLee|Rock Lee|Naruto|E
Neji|Neji Hyuga|Naruto|E
MightGuy|Might Guy|Naruto|L
Ino|Ino Yamanaka|Naruto|E
Tenten|Tenten|Naruto|E
Kiba|Kiba Inuzuka|Naruto|E
Shino|Shino Aburame|Naruto|E
Choji|Choji Akimichi|Naruto|E
Kurama|Kurama|Naruto|M
Boruto|Boruto Uzumaki|Boruto|E
Kawaki|Kawaki|Boruto|E
Shisui|Shisui Uchiha|Naruto|L
Otsutsuki|Kaguya Otsutsuki|Naruto|C
Mei|Mei Terumi|Naruto|E
KillerBee|Killer Bee|Naruto|E
Asuma|Asuma Sarutobi|Naruto|E
Hiruzen|Hiruzen Sarutobi|Naruto|L
Zabuza|Zabuza Momochi|Naruto|E
Haku|Haku|Naruto|E
Byakuya|Byakuya Kuchiki|Bleach|M
Rukia|Rukia Kuchiki|Bleach|L
Renji|Renji Abarai|Bleach|E
Kenpachi|Kenpachi Zaraki|Bleach|M
Yoruichi|Yoruichi Shihoin|Bleach|L
Urahara|Kisuke Urahara|Bleach|M
Toshiro|Toshiro Hitsugaya|Bleach|L
Ulquiorra|Ulquiorra Cifer|Bleach|L
Grimmjow|Grimmjow Jaegerjaquez|Bleach|L
Orihime|Orihime Inoue|Bleach|E
Uryu|Uryu Ishida|Bleach|E
Chad|Yasutora Sado|Bleach|E
Yamamoto|Genryusai Yamamoto|Bleach|C
Gin|Gin Ichimaru|Bleach|L
Starrk|Coyote Starrk|Bleach|L
Unohana|Retsu Unohana|Bleach|L
Shunsui|Shunsui Kyoraku|Bleach|M
Mayuri|Mayuri Kurotsuchi|Bleach|E
SoiFon|Soi Fon|Bleach|E
Rangiku|Rangiku Matsumoto|Bleach|E
Nelliel|Nelliel Tu Odelschwanck|Bleach|E
Gohan|Son Gohan|Dragon Ball|M
Piccolo|Piccolo|Dragon Ball|L
Frieza|Frieza|Dragon Ball|M
Cell|Cell|Dragon Ball|L
Buu|Majin Buu|Dragon Ball|L
Trunks|Trunks|Dragon Ball|L
Goten|Son Goten|Dragon Ball|E
Krillin|Krillin|Dragon Ball|E
Bulma|Bulma|Dragon Ball|E
Broly|Broly|Dragon Ball|C
Beerus|Beerus|Dragon Ball|C
Whis|Whis|Dragon Ball|C
Jiren|Jiren|Dragon Ball|C
Android18|Android 18|Dragon Ball|E
Android17|Android 17|Dragon Ball|E
Zeno|Zeno|Dragon Ball|C
Hit|Hit|Dragon Ball|E
Gogeta|Gogeta|Dragon Ball|C
Vegito|Vegito|Dragon Ball|C
Yamcha|Yamcha|Dragon Ball|E
Tien|Tien Shinhan|Dragon Ball|E
Roshi|Master Roshi|Dragon Ball|E
Videl|Videl|Dragon Ball|E
Pan|Pan|Dragon Ball|E
Yuta|Yuta Okkotsu|Jujutsu Kaisen|M
Maki|Maki Zenin|Jujutsu Kaisen|L
Panda|Panda|Jujutsu Kaisen|E
Inumaki|Toge Inumaki|Jujutsu Kaisen|E
Nanami|Kento Nanami|Jujutsu Kaisen|L
Todo|Aoi Todo|Jujutsu Kaisen|L
Mahito|Mahito|Jujutsu Kaisen|L
Geto|Suguru Geto|Jujutsu Kaisen|M
Jogo|Jogo|Jujutsu Kaisen|E
Choso|Choso|Jujutsu Kaisen|E
Hakari|Kinji Hakari|Jujutsu Kaisen|L
Yuki|Yuki Tsukumo|Jujutsu Kaisen|M
Kenjaku|Kenjaku|Jujutsu Kaisen|M
MeiMei|Mei Mei|Jujutsu Kaisen|E
Utahime|Utahime Iori|Jujutsu Kaisen|E
Higuruma|Hiromi Higuruma|Jujutsu Kaisen|L
Ishigori|Ryu Ishigori|Jujutsu Kaisen|E
Kashimo|Hajime Kashimo|Jujutsu Kaisen|L
Uraume|Uraume|Jujutsu Kaisen|E
Giyu|Giyu Tomioka|Demon Slayer|L
Mitsuri|Mitsuri Kanroji|Demon Slayer|L
Tengen|Tengen Uzui|Demon Slayer|L
Muichiro|Muichiro Tokito|Demon Slayer|L
Gyomei|Gyomei Himejima|Demon Slayer|M
Sanemi|Sanemi Shinazugawa|Demon Slayer|L
Obanai|Obanai Iguro|Demon Slayer|L
Kanao|Kanao Tsuyuri|Demon Slayer|E
Genya|Genya Shinazugawa|Demon Slayer|E
Akaza|Akaza|Demon Slayer|L
Doma|Doma|Demon Slayer|L
Kokushibo|Kokushibo|Demon Slayer|M
Yoriichi|Yoriichi Tsugikuni|Demon Slayer|C
Daki|Daki|Demon Slayer|E
Gyutaro|Gyutaro|Demon Slayer|E
Kanae|Kanae Kocho|Demon Slayer|E
Urokodaki|Sakonji Urokodaki|Demon Slayer|E
Tamayo|Tamayo|Demon Slayer|E
Annie|Annie Leonhart|Attack on Titan|E
Reiner|Reiner Braun|Attack on Titan|L
Bertholdt|Bertholdt Hoover|Attack on Titan|E
Historia|Historia Reiss|Attack on Titan|E
Jean|Jean Kirstein|Attack on Titan|E
Sasha|Sasha Blouse|Attack on Titan|E
Connie|Connie Springer|Attack on Titan|E
Zeke|Zeke Yeager|Attack on Titan|M
Ymir|Ymir Fritz|Attack on Titan|M
Pieck|Pieck Finger|Attack on Titan|E
Porco|Porco Galliard|Attack on Titan|E
Kenny|Kenny Ackerman|Attack on Titan|E
Gabi|Gabi Braun|Attack on Titan|E
Falco|Falco Grice|Attack on Titan|E
Netero|Isaac Netero|Hunter x Hunter|C
Chrollo|Chrollo Lucilfer|Hunter x Hunter|M
Illumi|Illumi Zoldyck|Hunter x Hunter|L
Feitan|Feitan|Hunter x Hunter|E
Ging|Ging Freecss|Hunter x Hunter|M
Alluka|Alluka Zoldyck|Hunter x Hunter|E
Biscuit|Biscuit Krueger|Hunter x Hunter|E
Leorio|Leorio Paradinight|Hunter x Hunter|E
Pitou|Neferpitou|Hunter x Hunter|L
Komugi|Komugi|Hunter x Hunter|E
Machi|Machi Komacine|Hunter x Hunter|E
Silva|Silva Zoldyck|Hunter x Hunter|L
Kite|Kite|Hunter x Hunter|E
Near|Near|Death Note|E
Mello|Mello|Death Note|E
Mikami|Teru Mikami|Death Note|E
AllMight|All Might|My Hero Academia|C
Endeavor|Endeavor|My Hero Academia|L
Hawks|Hawks|My Hero Academia|L
Shigaraki|Tomura Shigaraki|My Hero Academia|M
Dabi|Dabi|My Hero Academia|L
Toga|Himiko Toga|My Hero Academia|E
Uraraka|Ochaco Uraraka|My Hero Academia|E
Iida|Tenya Iida|My Hero Academia|E
Kirishima|Eijiro Kirishima|My Hero Academia|E
Tsuyu|Tsuyu Asui|My Hero Academia|E
Momo|Momo Yaoyorozu|My Hero Academia|E
Aizawa|Shota Aizawa|My Hero Academia|E
AllForOne|All For One|My Hero Academia|C
Mirio|Mirio Togata|My Hero Academia|L
Stain|Stain|My Hero Academia|E
Denki|Denki Kaminari|My Hero Academia|E
Overhaul|Kai Chisaki|My Hero Academia|L
Tokoyami|Fumikage Tokoyami|My Hero Academia|E
Gray|Gray Fullbuster|Fairy Tail|L
Wendy|Wendy Marvell|Fairy Tail|E
Gajeel|Gajeel Redfox|Fairy Tail|E
Juvia|Juvia Lockser|Fairy Tail|E
Laxus|Laxus Dreyar|Fairy Tail|L
Mirajane|Mirajane Strauss|Fairy Tail|L
Happy|Happy|Fairy Tail|E
Zeref|Zeref Dragneel|Fairy Tail|C
Makarov|Makarov Dreyar|Fairy Tail|L
Gildarts|Gildarts Clive|Fairy Tail|M
Jellal|Jellal Fernandes|Fairy Tail|L
Mavis|Mavis Vermillion|Fairy Tail|M
Yuno|Yuno|Black Clover|L
Noelle|Noelle Silva|Black Clover|E
Yami|Yami Sukehiro|Black Clover|L
Julius|Julius Novachrono|Black Clover|M
Mereoleona|Mereoleona Vermillion|Black Clover|L
Luck|Luck Voltia|Black Clover|E
Liebe|Liebe|Black Clover|E
Sinon|Sinon|Sword Art Online|E
Alice|Alice Zuberg|Sword Art Online|L
Leafa|Leafa|Sword Art Online|E
Eugeo|Eugeo|Sword Art Online|E
Yui|Yui|Sword Art Online|E
Ram|Ram|Re:Zero|E
Beatrice|Beatrice|Re:Zero|E
Echidna|Echidna|Re:Zero|L
Reinhard|Reinhard van Astrea|Re:Zero|M
Roswaal|Roswaal L. Mathers|Re:Zero|L
Darkness|Darkness|KonoSuba|E
Wiz|Wiz|KonoSuba|E
Vanir|Vanir|KonoSuba|E
Shalltear|Shalltear Bloodfallen|Overlord|L
Demiurge|Demiurge|Overlord|L
Cocytus|Cocytus|Overlord|E
AuraBella|Aura Bella Fiora|Overlord|E
Narberal|Narberal Gamma|Overlord|E
Sebas|Sebas Tian|Overlord|E
Milim|Milim Nava|Reincarnated as a Slime|C
Shion|Shion|Reincarnated as a Slime|E
Benimaru|Benimaru|Reincarnated as a Slime|L
Shuna|Shuna|Reincarnated as a Slime|E
Diablo|Diablo|Reincarnated as a Slime|M
Veldora|Veldora Tempest|Reincarnated as a Slime|C
Souei|Souei|Reincarnated as a Slime|E
Meliodas|Meliodas|Seven Deadly Sins|M
Elizabeth|Elizabeth Liones|Seven Deadly Sins|E
Ban|Ban|Seven Deadly Sins|L
King|King|Seven Deadly Sins|L
Diane|Diane|Seven Deadly Sins|E
Gowther|Gowther|Seven Deadly Sins|E
Merlin|Merlin|Seven Deadly Sins|M
Zeldris|Zeldris|Seven Deadly Sins|L
Aki|Aki Hayakawa|Chainsaw Man|L
Kobeni|Kobeni Higashiyama|Chainsaw Man|E
Reze|Reze|Chainsaw Man|L
Himeno|Himeno|Chainsaw Man|E
Kishibe|Kishibe|Chainsaw Man|E
Pochita|Pochita|Chainsaw Man|M
Asa|Asa Mitaka|Chainsaw Man|E
Yoru|Yoru|Chainsaw Man|L
Quanxi|Quanxi|Chainsaw Man|E
Bond|Bond Forger|Spy x Family|E
Yuri|Yuri Briar|Spy x Family|E
Becky|Becky Blackbell|Spy x Family|E
Damian|Damian Desmond|Spy x Family|E
Fiona|Fiona Frost|Spy x Family|E
CC|C.C.|Code Geass|L
Suzaku|Suzaku Kururugi|Code Geass|L
Kallen|Kallen Kozuki|Code Geass|E
Nunnally|Nunnally Lamperouge|Code Geass|E
Schneizel|Schneizel el Britannia|Code Geass|E
Alphonse|Alphonse Elric|Fullmetal Alchemist|L
Mustang|Roy Mustang|Fullmetal Alchemist|L
Riza|Riza Hawkeye|Fullmetal Alchemist|E
Winry|Winry Rockbell|Fullmetal Alchemist|E
Scar|Scar|Fullmetal Alchemist|E
Greed|Greed|Fullmetal Alchemist|E
Envy|Envy|Fullmetal Alchemist|E
Lust|Lust|Fullmetal Alchemist|E
Father|Father|Fullmetal Alchemist|M
Hohenheim|Van Hohenheim|Fullmetal Alchemist|M
Armstrong|Olivier Mira Armstrong|Fullmetal Alchemist|L
Bradley|King Bradley|Fullmetal Alchemist|L
Hughes|Maes Hughes|Fullmetal Alchemist|E
Faye|Faye Valentine|Cowboy Bebop|E
Jet|Jet Black|Cowboy Bebop|E
Ed|Radical Edward|Cowboy Bebop|E
Vicious|Vicious|Cowboy Bebop|L
Touka|Touka Kirishima|Tokyo Ghoul|E
Rize|Rize Kamishiro|Tokyo Ghoul|E
Juuzou|Juuzou Suzuya|Tokyo Ghoul|E
Arima|Kishou Arima|Tokyo Ghoul|M
Tsukiyama|Shuu Tsukiyama|Tokyo Ghoul|E
Hide|Hideyoshi Nagachika|Tokyo Ghoul|E
Genos|Genos|One Punch Man|L
Garou|Garou|One Punch Man|M
Tatsumaki|Tatsumaki|One Punch Man|M
Fubuki|Fubuki|One Punch Man|E
Bang|Silver Fang|One Punch Man|L
Boros|Boros|One Punch Man|M
Mumen|Mumen Rider|One Punch Man|E
Dimple|Dimple|Mob Psycho 100|E
Ritsu|Ritsu Kageyama|Mob Psycho 100|E
Teru|Teruki Hanazawa|Mob Psycho 100|E
Oikawa|Toru Oikawa|Haikyuu!!|E
Kenma|Kenma Kozume|Haikyuu!!|E
Kuroo|Tetsuro Kuroo|Haikyuu!!|E
Bokuto|Kotaro Bokuto|Haikyuu!!|E
Nishinoya|Yu Nishinoya|Haikyuu!!|E
Tsukishima|Kei Tsukishima|Haikyuu!!|E
Ushijima|Wakatoshi Ushijima|Haikyuu!!|E
Stark|Stark|Frieren|E
Heiter|Heiter|Frieren|E
Eisen|Eisen|Frieren|E
Ubel|Ubel|Frieren|E
Serie|Serie|Frieren|M
Flamme|Flamme|Frieren|L
Denken|Denken|Frieren|E
Macht|Macht|Frieren|L
Thorfinn|Thorfinn|Vinland Saga|L
Askeladd|Askeladd|Vinland Saga|L
Thorkell|Thorkell|Vinland Saga|L
Canute|Canute|Vinland Saga|E
Guts|Guts|Berserk|M
Griffith|Griffith|Berserk|M
Casca|Casca|Berserk|E
Jotaro|Jotaro Kujo|JoJo's Bizarre Adventure|M
Dio|Dio Brando|JoJo's Bizarre Adventure|C
Joseph|Joseph Joestar|JoJo's Bizarre Adventure|L
Jonathan|Jonathan Joestar|JoJo's Bizarre Adventure|L
Josuke|Josuke Higashikata|JoJo's Bizarre Adventure|L
Giorno|Giorno Giovanna|JoJo's Bizarre Adventure|M
Jolyne|Jolyne Cujoh|JoJo's Bizarre Adventure|L
Kira|Yoshikage Kira|JoJo's Bizarre Adventure|L
Polnareff|Jean Pierre Polnareff|JoJo's Bizarre Adventure|E
Kakyoin|Noriaki Kakyoin|JoJo's Bizarre Adventure|E
Bucciarati|Bruno Bucciarati|JoJo's Bizarre Adventure|E
Rohan|Rohan Kishibe|JoJo's Bizarre Adventure|E
Johnny|Johnny Joestar|JoJo's Bizarre Adventure|L
Gyro|Gyro Zeppeli|JoJo's Bizarre Adventure|L
Shinji|Shinji Ikari|Evangelion|L
Rei|Rei Ayanami|Evangelion|L
Asuka|Asuka Langley Soryu|Evangelion|L
Misato|Misato Katsuragi|Evangelion|E
Kaworu|Kaworu Nagisa|Evangelion|L
Gendo|Gendo Ikari|Evangelion|E
Okabe|Rintaro Okabe|Steins;Gate|L
Kurisu|Kurisu Makise|Steins;Gate|L
Mayushii|Mayuri Shiina|Steins;Gate|E
Taiga|Taiga Aisaka|Toradora!|E
Ryuji|Ryuji Takasu|Toradora!|E
Chizuru|Chizuru Mizuhara|Rent-a-Girlfriend|E
Miku|Miku Nakano|The Quintessential Quintuplets|E
Nino|Nino Nakano|The Quintessential Quintuplets|E
Ichika|Ichika Nakano|The Quintessential Quintuplets|E
Yotsuba|Yotsuba Nakano|The Quintessential Quintuplets|E
Itsuki|Itsuki Nakano|The Quintessential Quintuplets|E
Komi|Shoko Komi|Komi Can't Communicate|E
Tohru|Tohru Honda|Fruits Basket|E
Kyo|Kyo Sohma|Fruits Basket|E
Hachiman|Hachiman Hikigaya|Oregairu|E
Yukino|Yukino Yukinoshita|Oregairu|E
Mitsuha|Mitsuha Miyamizu|Your Name|E
Taki|Taki Tachibana|Your Name|E
Kaori|Kaori Miyazono|Your Lie in April|E
Kousei|Kousei Arima|Your Lie in April|E
Shouko|Shouko Nishimiya|A Silent Voice|E
Hori|Kyoko Hori|Horimiya|E
Nagatoro|Hayase Nagatoro|Don't Toy with Me, Miss Nagatoro|E
Takagi|Takagi|Teasing Master Takagi-san|E
Shikimori|Shikimori|Shikimori's Not Just a Cutie|E
Rikka|Rikka Takanashi|Love, Chunibyo & Other Delusions|E
Kurumi|Kurumi Tokisaki|Date A Live|L
Tohka|Tohka Yatogami|Date A Live|E
Shiro|Shiro|No Game No Life|L
Sora|Sora|No Game No Life|L
Jibril|Jibril|No Game No Life|L
Raphtalia|Raphtalia|The Rising of the Shield Hero|E
Naofumi|Naofumi Iwatani|The Rising of the Shield Hero|L
Filo|Filo|The Rising of the Shield Hero|E
Esdeath|Esdeath|Akame ga Kill!|M
Akame|Akame|Akame ga Kill!|L
Tatsumi|Tatsumi|Akame ga Kill!|E
Mine|Mine|Akame ga Kill!|E
Saber|Artoria Pendragon|Fate|M
Gilgamesh|Gilgamesh|Fate|C
Rin|Rin Tohsaka|Fate|L
Shirou|Shirou Emiya|Fate|L
Archer|Archer|Fate|L
Kuroko|Tetsuya Kuroko|Kuroko's Basketball|E
Kagami|Taiga Kagami|Kuroko's Basketball|E
Akashi|Seijuro Akashi|Kuroko's Basketball|L
Aomine|Daiki Aomine|Kuroko's Basketball|E
Isagi|Yoichi Isagi|Blue Lock|L
Bachira|Meguru Bachira|Blue Lock|E
Itoshi|Rin Itoshi|Blue Lock|L
Nagi|Seishiro Nagi|Blue Lock|L
Barou|Shoei Barou|Blue Lock|E
Kaiser|Michael Kaiser|Blue Lock|L
Sakuragi|Hanamichi Sakuragi|Slam Dunk|E
Rukawa|Kaede Rukawa|Slam Dunk|E
Ippo|Ippo Makunouchi|Hajime no Ippo|E
Yusuke|Yusuke Urameshi|Yu Yu Hakusho|L
Hiei|Hiei|Yu Yu Hakusho|L
Kuwabara|Kazuma Kuwabara|Yu Yu Hakusho|E
Toguro|Toguro|Yu Yu Hakusho|L
Inuyasha|Inuyasha|Inuyasha|L
Kagome|Kagome Higurashi|Inuyasha|E
Sesshomaru|Sesshomaru|Inuyasha|M
Kenshin|Kenshin Himura|Rurouni Kenshin|L
Usagi|Usagi Tsukino|Sailor Moon|L
Yugi|Yugi Muto|Yu-Gi-Oh!|L
Kaiba|Seto Kaiba|Yu-Gi-Oh!|L
Gintoki|Gintoki Sakata|Gintama|M
Kagura|Kagura|Gintama|E
Shinpachi|Shinpachi Shimura|Gintama|E
Hijikata|Toshiro Hijikata|Gintama|E
Okita|Sougo Okita|Gintama|E
Takasugi|Shinsuke Takasugi|Gintama|L
Seras|Seras Victoria|Hellsing|E
Integra|Integra Hellsing|Hellsing|E
Jinwoo|Sung Jinwoo|Solo Leveling|C
Igris|Igris|Solo Leveling|L
Beru|Beru|Solo Leveling|L
ChaHaeIn|Cha Hae-In|Solo Leveling|E
Okarun|Ken Takakura|Dandadan|E
Ayase|Momo Ayase|Dandadan|E
Kafka|Kafka Hibino|Kaiju No. 8|L
Mina|Mina Ashiro|Kaiju No. 8|L
Kikoru|Kikoru Shinomiya|Kaiju No. 8|E
Narumi|Gen Narumi|Kaiju No. 8|L
Sakamoto|Taro Sakamoto|Sakamoto Days|L
Shin|Shin Asakura|Sakamoto Days|E
Andy|Andy|Undead Unluck|L
Fuuko|Fuuko Izumo|Undead Unluck|E
Gabimaru|Gabimaru|Hell's Paradise|L
Sagiri|Yamada Asaemon Sagiri|Hell's Paradise|E
Shinra|Shinra Kusakabe|Fire Force|L
Arthur|Arthur Boyle|Fire Force|E
Emma|Emma|The Promised Neverland|E
Norman|Norman|The Promised Neverland|L
Ray|Ray|The Promised Neverland|E
Tsukasa|Tsukasa Shishio|Dr. Stone|L
Chrome|Chrome|Dr. Stone|E
Gen|Gen Asagiri|Dr. Stone|E
Korosensei|Koro-sensei|Assassination Classroom|M
Nagisa|Nagisa Shiota|Assassination Classroom|E
Karma|Karma Akabane|Assassination Classroom|E
Yato|Yato|Noragami|L
Hiyori|Hiyori Iki|Noragami|E
Yukine|Yukine|Noragami|E
Dazai|Osamu Dazai|Bungo Stray Dogs|L
Atsushi|Atsushi Nakajima|Bungo Stray Dogs|E
Chuuya|Chuuya Nakahara|Bungo Stray Dogs|L
Akutagawa|Ryunosuke Akutagawa|Bungo Stray Dogs|E
Akane|Akane Tsunemori|Psycho-Pass|E
Kogami|Shinya Kogami|Psycho-Pass|E
Makishima|Shogo Makishima|Psycho-Pass|L
Shinichi|Shinichi Izumi|Parasyte|E
Migi|Migi|Parasyte|E
Riko|Riko|Made in Abyss|E
Reg|Reg|Made in Abyss|E
Nanachi|Nanachi|Made in Abyss|E
Bondrewd|Bondrewd|Made in Abyss|L
Okumura|Rin Okumura|Blue Exorcist|E
Mash|Mash Burnedead|Mashle|L
Caiman|Caiman|Dorohedoro|E
Nikaido|Nikaido|Dorohedoro|E
Rudeus|Rudeus Greyrat|Mushoku Tensei|L
Roxy|Roxy Migurdia|Mushoku Tensei|E
Eris|Eris Boreas Greyrat|Mushoku Tensei|E
Sylphiette|Sylphiette|Mushoku Tensei|E
Ai|Ai Hoshino|Oshi no Ko|L
Aquamarine|Aqua Hoshino|Oshi no Ko|E
Ruby|Ruby Hoshino|Oshi no Ko|E
Kana|Kana Arima|Oshi no Ko|E
Maomao|Maomao|The Apothecary Diaries|L
Jinshi|Jinshi|The Apothecary Diaries|E
David|David Martinez|Cyberpunk: Edgerunners|E
Miyuki|Miyuki Shirogane|Kaguya-sama: Love is War|E
Ishigami|Yu Ishigami|Kaguya-sama: Love is War|E
Hanako|Hanako-kun|Toilet-bound Hanako-kun|E
Yumeko|Yumeko Jabami|Kakegurui|E
Hiro|Hiro|Darling in the Franxx|E
Ayanokoji|Kiyotaka Ayanokoji|Classroom of the Elite|L
Horikita|Suzune Horikita|Classroom of the Elite|E
Cid|Cid Kagenou|The Eminence in Shadow|M
Chisato|Chisato Nishikigi|Lycoris Recoil|E
Takina|Takina Inoue|Lycoris Recoil|E
Vash|Vash the Stampede|Trigun|L
Mugen|Mugen|Samurai Champloo|E
Jin|Jin|Samurai Champloo|E
Kamina|Kamina|Gurren Lagann|L
Simon|Simon|Gurren Lagann|L
Yoko|Yoko Littner|Gurren Lagann|E
Ryuko|Ryuko Matoi|Kill la Kill|L
Satsuki|Satsuki Kiryuin|Kill la Kill|L
Oreki|Houtarou Oreki|Hyouka|E
Chitanda|Eru Chitanda|Hyouka|E
Johan|Johan Liebert|Monster|M
Tenma|Kenzo Tenma|Monster|E
Motoko|Motoko Kusanagi|Ghost in the Shell|L
Kaneda|Shotaro Kaneda|Akira|E
Tetsuo|Tetsuo Shima|Akira|L
"""
CHARACTER_POOL += [
    {"name": n, "title": t, "anime": a, "rarity": _TIER[k][0], "reward": _TIER[k][1]}
    for n, t, a, k in (ln.split("|") for ln in _MORE2.strip().splitlines() if ln.strip())
]


def _tier_word(r: str) -> str:
    return (r or "").split(" ", 1)[-1]


_TIER_COUNT: dict = {}
for _c in CHARACTER_POOL:
    _TIER_COUNT[_tier_word(_c["rarity"])] = _TIER_COUNT.get(_tier_word(_c["rarity"]), 0) + 1
_WEIGHTS = [TIER_CHANCE.get(_tier_word(c["rarity"]), 10) / _TIER_COUNT[_tier_word(c["rarity"])] for c in CHARACTER_POOL]


def _pick_char() -> dict:
    return random.choices(CHARACTER_POOL, weights=_WEIGHTS, k=1)[0]


SYM = getattr(config, "CURRENCY_SYMBOL", "$")
WINK_FALLBACK = "https://media.giphy.com/media/l4pTdcifPZLpDjL1e/giphy.gif"
_HAREM_READY = False


async def _wink_gif() -> str:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get("https://nekos.best/api/v2/wink") as r:
                if r.status == 200:
                    return (await r.json())["results"][0]["url"]
    except Exception:
        pass
    return WINK_FALLBACK


def _char_matches(guess: str, char: dict) -> bool:
    g = _norm(guess)
    if not g:
        return False
    if g.replace(" ", "") == _norm(char["name"]).replace(" ", ""):
        return True
    return _name_matches(guess, char["title"])


# (bot_id, chat_id) -> live event
ACTIVE: dict[tuple[int, int], dict] = {}
# (bot_id, chat_id) -> activity info
SEEN: dict[tuple[int, int], dict] = {}
_TODAY: dict[tuple[int, int, str], int] = {}
_MEMBERS: dict[tuple[int, int], tuple[float, int]] = {}
_LOOP: asyncio.Task | None = None
_IMG: dict[int, str] = {}      # bot_id -> guess image (file_id or URL)
_POOL: list = []
IMG_ADMINS = {8920362130}     # may use /setguessimg besides the bot owners
_TABLE_READY = False
_MW = False


def _day() -> str:
    return datetime.now(IST).date().isoformat()


async def _ensure(pool: asyncpg.Pool) -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    async with pool.acquire() as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS ax_event_log ("
            "id BIGSERIAL PRIMARY KEY, bot_id BIGINT NOT NULL DEFAULT 0, chat_id BIGINT NOT NULL, "
            "day_key DATE NOT NULL, kind TEXT NOT NULL, winner_id BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    _TABLE_READY = True


# ---------------------------------------------------------------- payouts
async def _pay(pool: asyncpg.Pool, bid: int, user, amount: int, kind: str, tag: str) -> bool:
    try:
        await services.ensure_user_and_wallet(pool, user.id, user.username, user.first_name, bot_id=bid)
        async with pool.acquire() as c:
            async with c.transaction():
                w = await c.fetchrow("SELECT cash FROM wallets WHERE bot_id=$1 AND user_id=$2 FOR UPDATE", bid, user.id)
                if w is None:
                    return False
                await c.execute(
                    "UPDATE wallets SET cash = cash + $1, updated_at = now() WHERE bot_id=$2 AND user_id=$3",
                    amount, bid, user.id,
                )
                await services.insert_transaction(
                    c, user_id=user.id, type_="EVENT_REWARD", currency="cash", amount=amount,
                    balance_before=int(w["cash"]), balance_after=int(w["cash"]) + amount, source=kind,
                    metadata={"event": tag}, idempotency_key=f"event:{bid}:{tag}", bot_id=bid,
                )
        return True
    except Exception:
        log.exception("event payout failed")
        return False


# ---------------------------------------------------------------- catch characters
def _clean_name(raw: str) -> str:
    raw = re.sub(r"\s+", " ", (raw or "").strip())
    if "," in raw:  # Jikan gives "Uzumaki, Naruto"
        last, first = [p.strip() for p in raw.split(",", 1)]
        raw = f"{first} {last}".strip()
    return raw


async def _pick_character() -> tuple[str, str | None]:
    try:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            for _ in range(3):
                async with s.get("https://api.jikan.moe/v4/random/characters") as r:
                    if r.status != 200:
                        continue
                    d = (await r.json()).get("data") or {}
                name = _clean_name(d.get("name") or "")
                img = ((d.get("images") or {}).get("jpg") or {}).get("image_url")
                if name and img and "questionmark" not in img and len(name) <= 40 and name.isascii():
                    return name, img
    except Exception:
        pass
    return random.choice(FALLBACK_CHARACTERS), None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower().replace("-", " ")).strip()


def _name_matches(guess: str, name: str) -> bool:
    g, n = _norm(guess), _norm(name)
    if not g:
        return False
    if g == n:
        return True
    return len(g) >= 3 and g in n.split()


def _mask(name: str) -> str:
    return "  ".join(w[0] + " " + " ".join("_" * (len(w) - 1)) if len(w) > 1 else w for w in name.split())


# ---------------------------------------------------------------- spawning
def _guess_image(bid: int):
    if bid in _IMG:
        return _IMG[bid]
    env = os.getenv("GUESS_EVENT_IMAGE") or getattr(config, "GUESS_EVENT_IMAGE", "")
    if env:
        return env
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = os.path.join(HERE, "assets", f"guess_event.{ext}")
        if os.path.exists(p):
            from aiogram.types import FSInputFile
            return FSInputFile(p)
    return None


async def _ensure_settings(pool) -> None:
    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS ax_event_settings (bot_id BIGINT NOT NULL, key TEXT NOT NULL, "
                        "value TEXT NOT NULL, PRIMARY KEY (bot_id, key))")


async def _load_img(bid: int) -> None:
    if bid in _IMG or not _POOL:
        return
    try:
        await _ensure_settings(_POOL[0])
        async with _POOL[0].acquire() as c:
            v = await c.fetchval("SELECT value FROM ax_event_settings WHERE bot_id=$1 AND key='guess_image'", bid)
        if v:
            _IMG[bid] = v
    except Exception:
        log.exception("could not load the guess image")


async def _send(bot: Bot, chat_id: int, text: str, photo=None) -> Message | None:
    if photo is not None:
        try:
            return await bot.send_photo(chat_id, photo, caption=text, parse_mode=H)
        except Exception:
            log.warning("event photo failed, sending text instead")
    try:
        return await bot.send_message(chat_id, text, parse_mode=H)
    except Exception:
        log.exception("event message failed")
        return None


async def spawn(bot: Bot, bid: int, chat_id: int, kind: str) -> bool:
    key = (bid, chat_id)
    if key in ACTIVE:
        return False
    now = time.time()
    if kind == "catch":
        char = _pick_char()
        gif = await _wink_gif()
        if key in ACTIVE:
            return False
        ev = {"kind": "catch", "name": char["title"], "char": char, "ends": now + EVENT_LIFETIME,
              "tag": f"catch:{chat_id}:{int(now)}"}
        text = (
            f"✨ <b>A WILD CHARACTER APPEARED!</b>\n\n"
            f"<blockquote>🎭 <b>Series:</b> {char['anime']}\n"
            f"🌟 <b>Rarity:</b> <code>{char['rarity']}</code>\n"
            f"💰 <b>Bounty:</b> <code>+{SYM}{char['reward']:,}</code></blockquote>\n"
            f"⚡ Type <code>/catch &lt;name&gt;</code> first to claim them! <i>(Hint: starts with <b>{char['name'][0]}</b>)</i>"
        )
        ACTIVE[key] = ev
        sent = None
        try:
            sent = await bot.send_animation(chat_id, animation=gif, caption=text, parse_mode=H)
        except Exception:
            log.warning("catch gif failed, sending text instead")
            sent = await _send(bot, chat_id, text)
    else:
        secret = random.randint(*GUESS_RANGE)
        reward = random.randint(*GUESS_REWARD)
        ev = {"kind": "guess", "secret": secret, "reward": reward, "lo": GUESS_RANGE[0], "hi": GUESS_RANGE[1],
              "tries": 0, "ends": now + EVENT_LIFETIME, "tag": f"guess:{chat_id}:{int(now)}"}
        text = (
            "🎯 <b>NUMBER GUESS EVENT!</b>\n"
            f"<blockquote>I'm thinking of a number from <b>{GUESS_RANGE[0]}</b> to <b>{GUESS_RANGE[1]}</b>.\n"
            f"Type <code>/g 250</code> to guess. First to get it wins <b>${reward:,}</b>!\n"
            f"The whole group shares <b>{GUESS_TRIES}</b> tries.</blockquote>"
        )
        ACTIVE[key] = ev
        await _load_img(bid)
        sent = await _send(bot, chat_id, text, _guess_image(bid))
        if sent is not None and sent.photo and bid not in _IMG:
            _IMG[bid] = sent.photo[-1].file_id
    if sent is None:
        ACTIVE.pop(key, None)
        return False
    ev["bot"] = bot
    _LAST[key] = now
    await _track(bid, chat_id, [sent.message_id], now)
    return True


async def _expire(bid: int, chat_id: int, ev: dict) -> None:
    if ACTIVE.get((bid, chat_id)) is ev:
        ACTIVE.pop((bid, chat_id), None)   # silent: the messages are removed by the cleaner


async def _ensure_msgs(pool) -> None:
    global _MSGS_READY
    if _MSGS_READY:
        return
    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS ax_event_msgs (bot_id BIGINT NOT NULL, chat_id BIGINT NOT NULL, "
                        "message_id BIGINT NOT NULL, delete_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (bot_id, chat_id, message_id))")
    _MSGS_READY = True


async def _track(bid: int, chat_id: int, ids: list, born: float) -> None:
    ctx = _CTX.get(bid)
    if not ctx:
        return
    try:
        await _ensure_msgs(ctx["pool"])
        async with ctx["pool"].acquire() as c:
            for i in ids:
                await c.execute("INSERT INTO ax_event_msgs (bot_id, chat_id, message_id, delete_at) VALUES ($1,$2,$3,$4) "
                                "ON CONFLICT DO NOTHING", bid, chat_id, int(i), born + EVENT_LIFETIME)
    except Exception:
        log.exception("event message tracking failed")


async def _r(message: Message, text: str, **kw):
    """reply, and remember both the user's message and our reply for the 1-day cleanup"""
    bid = bot_context.resolve_bot_id(None)
    born = _LAST.get((bid, message.chat.id))
    sent = await message.reply(text, **kw)
    if born and time.time() < born + EVENT_LIFETIME:
        await _track(bid, message.chat.id, [message.message_id, sent.message_id], born)
    return sent


async def _clean(bid: int, ctx: dict) -> None:
    try:
        await _ensure_msgs(ctx["pool"])
        async with ctx["pool"].acquire() as c:
            rows = await c.fetch("SELECT chat_id, message_id FROM ax_event_msgs WHERE bot_id=$1 AND delete_at<=$2 LIMIT 80",
                                 bid, time.time())
            for r in rows:
                try:
                    await ctx["bot"].delete_message(r["chat_id"], r["message_id"])
                except Exception as exc:
                    if "retry" in type(exc).__name__.lower():
                        return
                await c.execute("DELETE FROM ax_event_msgs WHERE bot_id=$1 AND chat_id=$2 AND message_id=$3",
                                bid, r["chat_id"], r["message_id"])
                await asyncio.sleep(0.05)
    except Exception:
        log.exception("event cleanup failed")


def _drop_at(bid: int, chat_id: int, day: str) -> float:
    """a random but fixed moment of the (IST) day for this group"""
    sec = random.Random(f"{bid}:{chat_id}:{day}").randint(0, 86399)
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, tzinfo=IST).timestamp() + sec


async def _members(bot: Bot, bid: int, chat_id: int) -> int:
    hit = _MEMBERS.get((bid, chat_id))
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    try:
        n = int(await bot.get_chat_member_count(chat_id))
    except Exception:
        return 0
    _MEMBERS[(bid, chat_id)] = (time.time(), n)
    return n


async def _tick() -> None:
    now = time.time()
    for key, ev in list(ACTIVE.items()):
        if now > ev["ends"]:
            await _expire(key[0], key[1], ev)
    day = _day()
    for bid, ctx in list(_CTX.items()):
        await _clean(bid, ctx)
        pool, bot = ctx["pool"], ctx["bot"]
        try:
            await _ensure(pool)
            async with pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT g.chat_id FROM ax_groups g WHERE g.bot_id=$1 AND g.chat_id < 0 "
                    "AND NOT EXISTS (SELECT 1 FROM ax_channels ch WHERE ch.chat_id = g.chat_id)", bid)
        except Exception:
            log.exception("event group list failed")
            continue
        started = 0
        for row in rows:
            chat_id = row["chat_id"]
            key = (bid, chat_id)
            if key in ACTIVE or (bid, chat_id, day) in _SKIP or now < _drop_at(bid, chat_id, day) or started >= 3:
                continue
            try:
                done = _TODAY.get((bid, chat_id, day))
                if done is None:
                    async with pool.acquire() as c:
                        done = int(await c.fetchval(
                            "SELECT COUNT(*) FROM ax_event_log WHERE bot_id=$1 AND chat_id=$2 AND day_key=$3::date",
                            bid, chat_id, date.fromisoformat(day)))
                    _TODAY[(bid, chat_id, day)] = done
                if done >= MAX_PER_DAY:
                    _SKIP.add((bid, chat_id, day))
                    continue
                if await _members(bot, bid, chat_id) < MIN_MEMBERS:
                    _SKIP.add((bid, chat_id, day))
                    continue
                kind = random.choice(("catch", "guess"))
                _TODAY[(bid, chat_id, day)] = done + 1
                _SKIP.add((bid, chat_id, day))      # one try per day, never spam a group that fails
                if await spawn(bot, bid, chat_id, kind):
                    started += 1
                    async with pool.acquire() as c:
                        await c.execute(
                            "INSERT INTO ax_event_log (bot_id, chat_id, day_key, kind) VALUES ($1,$2,$3::date,$4)",
                            bid, chat_id, date.fromisoformat(day), kind)
                else:
                    _TODAY[(bid, chat_id, day)] = done
            except Exception:
                log.exception("event tick failed for chat %s", chat_id)
    if len(_SKIP) > 5000:
        _SKIP.clear()


async def _loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await _tick()
        except Exception:
            log.exception("event loop error")


class ActivityMiddleware(BaseMiddleware):
    """Remembers which groups are chatting right now (nothing is stored in the database)."""

    async def __call__(self, handler, event, data):
        global _LOOP
        try:
            if isinstance(event, Message) and event.chat.type in ("group", "supergroup") and event.from_user \
                    and not event.from_user.is_bot:
                pool = data.get("pool")
                bid = data.get("bot_id") or (data.get("bot_ctx").bot_id if data.get("bot_ctx") else event.bot.id)
                if pool is not None:
                    _POOL[:] = [pool]
                    _CTX[bid] = {"bot": event.bot, "pool": pool}
                    if _LOOP is None or _LOOP.done():
                        _LOOP = asyncio.get_running_loop().create_task(_loop())
        except Exception:
            log.exception("event activity watcher failed")
        return await handler(event, data)


# ---------------------------------------------------------------- commands
def _in_group(m: Message) -> bool:
    return m.chat.type in ("group", "supergroup")


@router.message(Command("catch", "grab", "claimchar"))
async def catch_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user or not _in_group(message):
        return
    key = (bot_ctx.bot_id, message.chat.id)
    ev = ACTIVE.get(key)
    guess = " ".join(cmd_args(message)).strip()
    if not ev or ev["kind"] != "catch":
        await _r(message, "💨 No character is currently waiting to be caught!")
        return
    if not guess:
        await _r(message, "📌 Guess their name! Usage: <code>/catch &lt;name&gt;</code>", parse_mode=H)
        return
    char = ev.get("char") or {"name": ev["name"], "title": ev["name"], "rarity": "", "reward": CATCH_REWARD}
    if not _char_matches(guess, char):
        await _r(message, "❌ Wrong character name! Keep trying!")
        return
    if ACTIVE.pop(key, None) is None:
        return
    user = message.from_user
    bid = bot_ctx.bot_id
    text = (
        f"🎉 <b>CHARACTER CAUGHT!</b>\n\n"
        f"<blockquote>👤 {user.mention_html()} captured <b>{char['title']}</b>!\n"
        f"🌟 <b>Rarity:</b> <code>{char['rarity']}</code>\n"
        f"💰 <b>Reward:</b> <code>+{SYM}{char['reward']:,}</code></blockquote>"
    )
    ok, _ = await asyncio.gather(
        _pay(pool, bid, user, int(char["reward"]), "catch", ev["tag"]),
        _r(message, text, parse_mode=H),
        return_exceptions=True,
    )
    if ok is not True:
        try:
            await message.reply("⚠️ The prize could not be paid. Please tell an admin.")
        except Exception:
            pass
    asyncio.get_running_loop().create_task(_after_catch(pool, bid, message.chat.id, user.id, char))


async def _ensure_harem(pool) -> None:
    global _HAREM_READY
    if _HAREM_READY:
        return
    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS ax_harem (id BIGSERIAL PRIMARY KEY, bot_id BIGINT NOT NULL, "
                        "user_id BIGINT NOT NULL, title TEXT NOT NULL, rarity TEXT NOT NULL DEFAULT '', "
                        "caught_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        await c.execute("CREATE INDEX IF NOT EXISTS ax_harem_user ON ax_harem (bot_id, user_id, id)")
    _HAREM_READY = True


async def _after_catch(pool, bid, chat_id, uid, char) -> None:
    try:
        await _ensure_harem(pool)
        async with pool.acquire() as c:
            await c.execute("INSERT INTO ax_harem (bot_id, user_id, title, rarity) VALUES ($1,$2,$3,$4)",
                            bid, uid, char["title"], char.get("rarity") or "")
    except Exception:
        log.exception("harem save failed")
    await _log_winner(pool, bid, chat_id, "catch", uid)


# COLLECTION_V1  /collection hub with category buttons + pages.
# To add a new collection type later: write count_fn + items_fn and add one entry to COLLECTIONS.
COL_PAGE = 8
_RARITY_ORDER = ["celestial", "mythic", "legendary", "epic", "rare", "common"]
_POOL_SIZE = len(CHARACTER_POOL)


def _rarity_rank(r: str) -> int:
    low = (r or "").lower()
    for i, w in enumerate(_RARITY_ORDER):
        if w in low:
            return i
    return len(_RARITY_ORDER)


def _bar(have: int, total: int, size: int = 10) -> str:
    if total <= 0:
        return ""
    fill = min(size, round(size * have / total))
    return "▰" * fill + "▱" * (size - fill)


async def _chars_count(pool, bid, uid) -> tuple[int, int]:
    await _ensure_harem(pool)
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT COUNT(*) AS n, COUNT(DISTINCT title) AS u FROM ax_harem "
                             "WHERE bot_id=$1 AND user_id=$2", bid, uid)
    return int(r["n"]), int(r["u"])


async def _chars_items(pool, bid, uid) -> list[str]:
    await _ensure_harem(pool)
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT title, rarity, COUNT(*) AS n FROM ax_harem WHERE bot_id=$1 AND user_id=$2 "
                             "GROUP BY title, rarity", bid, uid)
    anime = {ch["title"]: ch["anime"] for ch in CHARACTER_POOL}
    rows = sorted(rows, key=lambda r: (_rarity_rank(r["rarity"]), r["title"]))
    out = []
    for r in rows:
        rar = r["rarity"] or ""
        icon, _, word = rar.partition(" ")
        dup = f" <b>×{r['n']}</b>" if r["n"] > 1 else ""
        series = anime.get(r["title"])
        sub = f"\n     <i>{esc(word or rar)}{' · ' + esc(series) if series else ''}</i>"
        out.append(f"{esc(icon) if word else '🎴'} <b>{esc(r['title'])}</b>{dup}{sub}")
    return out


COLLECTIONS = {
    "ch": {"icon": "🎴", "label": "Characters", "total": _POOL_SIZE,
           "count": _chars_count, "items": _chars_items, "empty": "Catch one with <code>/catch</code> when a character appears!"},
}


def _col_home_kb(oid, tid, counts) -> InlineKeyboardMarkup:
    rows, line = [], []
    for key, col in COLLECTIONS.items():
        n = counts.get(key, (0, 0))[0]
        line.append(InlineKeyboardButton(text=f"{col['icon']} {col['label']} ({n})", callback_data=f"cl:{oid}:{tid}:{key}:0"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([InlineKeyboardButton(text="✖️ Close", callback_data=f"cl:{oid}:{tid}:x:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _col_home(pool, bid, oid, target) -> tuple[str, InlineKeyboardMarkup]:
    counts = {}
    res = await asyncio.gather(*(c["count"](pool, bid, target.id) for c in COLLECTIONS.values()), return_exceptions=True)
    for key, r in zip(COLLECTIONS, res):
        counts[key] = r if isinstance(r, tuple) else (0, 0)
    total = sum(v[0] for v in counts.values())
    lines = []
    for key, col in COLLECTIONS.items():
        n, u = counts[key]
        t = col.get("total") or 0
        prog = f"  {_bar(u, t, 8)} <code>{u}/{t}</code>" if t else ""
        lines.append(f"{col['icon']} <b>{col['label']}:</b> <code>{n}</code>{prog}")
    name = esc(target.first_name or "Player")
    text = (
        f"🗂 <b>{name.upper()}'S COLLECTION</b>\n\n"
        f"<blockquote>" + "\n".join(lines) + "</blockquote>\n"
        f"📦 <b>Total items:</b> <code>{total}</code>\n"
        f"👇 <i>Pick a category to open it</i>"
    )
    return text, _col_home_kb(oid, target.id, counts)


async def _col_page(pool, bid, oid, tid, tname, key, page) -> tuple[str, InlineKeyboardMarkup]:
    col = COLLECTIONS[key]
    items, (n, u) = await asyncio.gather(col["items"](pool, bid, tid), col["count"](pool, bid, tid))
    pages = max(1, (len(items) + COL_PAGE - 1) // COL_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * COL_PAGE:(page + 1) * COL_PAGE]
    t = col.get("total") or 0
    head = f"{col['icon']} <b>{esc(tname).upper()} · {col['label'].upper()}</b>\n"
    if t:
        head += f"<code>{_bar(u, t)}</code> <b>{u}/{t}</b> unlocked · <code>{n}</code> caught\n"
    if chunk:
        body = "<blockquote>" + "\n".join(chunk) + "</blockquote>"
    else:
        body = f"<blockquote>Nothing here yet. {col['empty']}</blockquote>"
    nav = []
    if pages > 1:
        nav = [
            InlineKeyboardButton(text="◀️", callback_data=f"cl:{oid}:{tid}:{key}:{(page - 1) % pages}"),
            InlineKeyboardButton(text=f"📄 {page + 1}/{pages}", callback_data=f"cl:{oid}:{tid}:n:0"),
            InlineKeyboardButton(text="▶️", callback_data=f"cl:{oid}:{tid}:{key}:{(page + 1) % pages}"),
        ]
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data=f"cl:{oid}:{tid}:h:0"),
                 InlineKeyboardButton(text="✖️ Close", callback_data=f"cl:{oid}:{tid}:x:0")])
    return head + "\n" + body, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("collection", "col", "mycollection"))
async def collection_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    u = message.from_user
    if not u:
        return
    rep = message.reply_to_message
    target = rep.from_user if rep and rep.from_user and not rep.from_user.is_bot else u
    try:
        text, kb = await _col_home(pool, bot_ctx.bot_id, u.id, target)
    except Exception:
        log.exception("collection failed")
        await message.reply("⚠️ Could not open the collection right now. Try again.")
        return
    await message.reply(text, parse_mode=H, reply_markup=kb)


@router.callback_query(F.data.startswith("cl:"))
async def collection_cb(call: CallbackQuery, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    try:
        _, oid, tid, key, page = call.data.split(":")
        oid, tid, page = int(oid), int(tid), int(page)
    except Exception:
        await call.answer()
        return
    if call.from_user.id != oid:
        await call.answer("🔒 This isn't your menu. Use /collection to open yours!", show_alert=True)
        return
    if key == "n":
        await call.answer()
        return
    if key == "x":
        await call.answer()
        try:
            await call.message.delete()
        except Exception:
            pass
        return
    bid = bot_ctx.bot_id
    try:
        if key == "h" or key not in COLLECTIONS:
            target = call.from_user
            if tid != oid:
                from types import SimpleNamespace
                target = SimpleNamespace(id=tid, first_name=_col_name(call.message, tid))
            text, kb = await _col_home(pool, bid, oid, target)
        else:
            tname = call.from_user.first_name if tid == oid else _col_name(call.message, tid)
            text, kb = await _col_page(pool, bid, oid, tid, tname or "Player", key, page)
    except Exception:
        log.exception("collection page failed")
        await call.answer("⚠️ Try again.", show_alert=True)
        return
    await asyncio.gather(
        call.answer(),
        call.message.edit_text(text, parse_mode=H, reply_markup=kb),
        return_exceptions=True,
    )


def _col_name(msg, tid) -> str:
    # target name from the header of the current card ("NAME'S COLLECTION" or "NAME · ...")
    t = (msg.text or "") if msg else ""
    first = t.split("\n", 1)[0]
    for sep in ("'S COLLECTION", " · "):
        if sep in first:
            return first.split(sep, 1)[0].lstrip("🗂🎴 ").strip().title()
    return "Player"


@router.message(Command("harem", "chars"))
async def harem_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    rep = message.reply_to_message
    target = rep.from_user if rep and rep.from_user and not rep.from_user.is_bot else message.from_user
    if not target:
        return
    name = esc(target.first_name or "Player")
    try:
        await _ensure_harem(pool)
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT title, rarity, COUNT(*) OVER () AS n FROM ax_harem WHERE bot_id=$1 AND user_id=$2 "
                "ORDER BY id LIMIT 15", bot_ctx.bot_id, target.id)
    except Exception:
        log.exception("harem read failed")
        rows = []
    if not rows:
        await message.reply(f"🃏 <b>{name}</b> hasn't caught any characters yet! Use <code>/catch</code>.", parse_mode=H)
        return
    total = int(rows[0]["n"])
    lines = [f"{i}. {esc(r['title'])} ({esc(r['rarity'])})" for i, r in enumerate(rows, 1)]
    await message.reply(
        f"🃏 <b>{name.upper()}'S COLLECTION ({total})</b>\n\n<blockquote>" + "\n".join(lines) + "</blockquote>",
        parse_mode=H,
    )


async def _log_winner(pool, bid, chat_id, kind, uid) -> None:
    try:
        async with pool.acquire() as c:
            await c.execute(
                "UPDATE ax_event_log SET winner_id=$4 WHERE id=(SELECT id FROM ax_event_log "
                "WHERE bot_id=$1 AND chat_id=$2 AND kind=$3 AND winner_id IS NULL ORDER BY id DESC LIMIT 1)",
                bid, chat_id, kind, uid)
    except Exception:
        pass


@router.message(Command("g"))
async def guess_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    if not message.from_user or not _in_group(message):
        return
    key = (bot_ctx.bot_id, message.chat.id)
    ev = ACTIVE.get(key)
    if not ev or ev["kind"] != "guess":
        await _r(message, "No number guess is running right now.")
        return
    args = cmd_args(message)
    lo_all, hi_all = GUESS_RANGE
    if not args or not args[0].isdigit() or not (lo_all <= int(args[0]) <= hi_all):
        await _r(message, f"Use: <code>/g 250</code> (a number from {lo_all} to {hi_all})", parse_mode=H)
        return
    val = int(args[0])
    if val == ev["secret"]:
        if ACTIVE.pop(key, None) is None:
            return
        ok = await _pay(pool, bot_ctx.bot_id, message.from_user, ev["reward"], "guess", ev["tag"])
        who = esc(message.from_user.first_name or "Player")
        tail = f"won <code>+${ev['reward']:,}</code>!" if ok else "won, but the prize could not be paid. Please tell an admin."
        await _r(message, f"🎯 Correct! <b>{who}</b> guessed <code>{val}</code> and {tail}", parse_mode=H)
        await _log_winner(pool, bot_ctx.bot_id, message.chat.id, "guess", message.from_user.id)
        return
    ev["tries"] += 1
    if val < ev["secret"]:
        ev["lo"] = max(ev["lo"], val + 1)
        hint = "⬆️ Higher!"
    else:
        ev["hi"] = min(ev["hi"], val - 1)
        hint = "⬇️ Lower!"
    left = GUESS_TRIES - ev["tries"]
    if left <= 0:
        ACTIVE.pop(key, None)
        await _r(message, f"{hint}\n💥 No tries left! The number was <b>{ev['secret']}</b>.", parse_mode=H)
        return
    await _r(message, f"{hint} It is between <code>{ev['lo']}</code> and <code>{ev['hi']}</code>. Tries left: <b>{left}</b>", parse_mode=H)


@router.message(Command("spawnevent"))
async def spawn_cmd(message: Message, bot: Bot, bot_ctx: BotContext) -> None:
    if not message.from_user or not _can_manage(message.from_user.id) or not _in_group(message):
        return
    args = cmd_args(message)
    kind = args[0].lower() if args and args[0].lower() in ("catch", "guess") else random.choice(("catch", "guess"))
    ok = await spawn(bot, bot_ctx.bot_id, message.chat.id, kind)
    if not ok:
        await message.reply("An event is already running here (or it could not be sent).")


def _can_manage(uid: int) -> bool:
    if uid in IMG_ADMINS:
        return True
    try:
        from ax_adminpanel import OWNER_IDS
        return uid in OWNER_IDS
    except Exception:
        return False


@router.message(Command("setguessimg"))
async def setimg_cmd(message: Message, pool: asyncpg.Pool, bot_ctx: BotContext) -> None:
    """Reply to a photo with /setguessimg to use it for the guess event. /setguessimg clear removes it."""
    u = message.from_user
    if not u or not _can_manage(u.id):
        return
    _POOL[:] = [pool]
    bid = bot_ctx.bot_id
    await _ensure_settings(pool)
    args = cmd_args(message)
    if args and args[0].lower() == "clear":
        async with pool.acquire() as c:
            await c.execute("DELETE FROM ax_event_settings WHERE bot_id=$1 AND key='guess_image'", bid)
        _IMG.pop(bid, None)
        await message.reply("🗑 Guess event image removed.")
        return
    rep = message.reply_to_message
    if not rep or not rep.photo:
        await message.reply("Send the image here, then reply to it with <code>/setguessimg</code>.", parse_mode=H)
        return
    fid = rep.photo[-1].file_id
    async with pool.acquire() as c:
        await c.execute("INSERT INTO ax_event_settings (bot_id, key, value) VALUES ($1,'guess_image',$2) "
                        "ON CONFLICT (bot_id, key) DO UPDATE SET value=EXCLUDED.value", bid, fid)
    _IMG[bid] = fid
    await message.reply("✅ Saved! The guess event will use this image.\nCode: <code>" + esc(fid) + "</code>", parse_mode=H)


async def on_startup(bot: Bot, dispatcher) -> None:
    global _LOOP
    pool = dispatcher["pool"]
    _POOL[:] = [pool]
    _CTX[bot_context.resolve_bot_id(bot.id)] = {"bot": bot, "pool": pool}
    if _LOOP is None or _LOOP.done():
        _LOOP = asyncio.get_running_loop().create_task(_loop())


def register(dp) -> None:
    global _MW
    if not _MW:
        _MW = True
        dp.message.outer_middleware(ActivityMiddleware())
    if router.parent_router is None:
        dp.include_router(router)
