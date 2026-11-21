# Every tunable economy number lives here. Change values, restart the bot —
# nothing else in the codebase should hard-code a reward amount or limit.

CURRENCY_SYMBOL = "$"

# Premium & Normal UI / Emoji configuration (safe fallback when custom emoji is unavailable)
NORMAL_DEFAULT_EMOJI = "👤"
PREMIUM_DEFAULT_EMOJI = "💓"
PREMIUM_CUSTOM_EMOJI_ENABLED = True

# /tribute & /claim daily reward rules
DAILY_BASE_REWARD = 2000
PREMIUM_DAILY_REWARD = 5000
DAILY_WEEKLY_BONUS_CASH = 5000       # replaces the base reward every Nth streak day
DAILY_WEEKLY_BONUS_INTERVAL = 7      # every 7th consecutive day (7, 14, 21, ...)

DAILY_RANDOM_BONUS_CHANCE = 0.15
DAILY_RANDOM_BONUS_MIN = 50
DAILY_RANDOM_BONUS_MAX = 300
DAILY_GEM_CHANCE = 0.1
DAILY_GEM_AMOUNT = 1

STREAK_MILESTONES = [3, 7, 14, 30, 60, 100]
STREAK_MILESTONE_GEM_REWARD = 5

# /pay & /give transfer rules
MIN_TRANSFER = 10
MAX_TRANSFER = 50_000
NORMAL_TRANSFER_TAX_PERCENT = 0.10
PREMIUM_TRANSFER_TAX_PERCENT = 0.05
TRANSFER_TAX_PERCENT = 0.10
DAILY_TRANSFER_LIMIT = 100_000

# /protect & /guard (shield)
SHIELD_COST = 400
SHIELD_DURATION_HOURS = 24
PREMIUM_SHIELD_MAX_DAYS = 2

# /kill & /revive rules
NORMAL_KILL_MIN = 100
NORMAL_KILL_MAX = 200
NORMAL_KILL_XP_MIN = 0
NORMAL_KILL_XP_MAX = 10
NORMAL_DAILY_KILL_LIMIT = 200

PREMIUM_KILL_MIN = 200
PREMIUM_KILL_MAX = 400
PREMIUM_KILL_XP_MIN = 10
PREMIUM_KILL_XP_MAX = 20
PREMIUM_DAILY_KILL_LIMIT = 400

# /rob & 30% wallet save rules
ROB_COOLDOWN_MINUTES = 0
ROB_MIN_VICTIM_BALANCE = 2          # victim must have at least this to be robbable
ROB_MIN_AMOUNT = 1                  # smallest amount you can try to rob
ROB_SUCCESS_CHANCE = 6 / 7
ROB_STEAL_PERCENT_MIN = 0.10
ROB_STEAL_PERCENT_MAX = 0.30
NORMAL_ROB_TAX_PERCENT = 0.10
PREMIUM_ROB_TAX_PERCENT = 0.05
ROB_TAX_PERCENT = 0.10
PREMIUM_WALLET_SAVE_PERCENT = 0.30  # 30% of wallet balance protected from robbery for Premium
ROB_FAIL_PENALTY_PERCENT_MIN = 0.05
ROB_FAIL_PENALTY_PERCENT_MAX = 0.15

ROB_MAX_AMOUNT = 10_000             # normal single-rob cap
PREMIUM_ROB_BASE_MAX_AMOUNT = 30_000
POWER_ROB_MAX_AMOUNT = 100_000      # up to 1 lakh with Premium + active power
NORMAL_DAILY_ROB_LIMIT = 200
PREMIUM_DAILY_ROB_LIMIT = 400
ROB_XP_PER_HOUR = 3
ROB_ONCE_PER_MSG_BELOW = 3000

# Gems, /convert & Powers
GEM_COIN_RATE = 10_000              # 10,000 Coins = 1 Gem
GEM_CONVERT_MAX_GEMS = 20           # up to 200,000 Coins -> 20 Gems per season
DAILY_GEM_USE_LIMIT = 50            # max Gems a user can spend/use per day
GAME_WIN_TAX_NORMAL = 0.10
GAME_WIN_TAX_PREMIUM = 0.05

# Leaderboards & Bluff
LEADERBOARD_PAGE_SIZE = 10

BLUFF_MIN_WAGER = 100
BLUFF_MAX_WAGER = 50_000
BLUFF_TAX_PERCENT = 0.10
BLUFF_MIN_PLAYERS = 2
BLUFF_MAX_PLAYERS = 6
BLUFF_LOBBY_SECONDS = 120
BLUFF_ROUND_SECONDS = 60
BLUFF_RESULT_SECONDS = 5
BLUFF_HAND_SUM_MIN = 16
BLUFF_HAND_SUM_MAX = 28

# ---- XP and levels ----
XP_CLAIM = 20
XP_ROB_SUCCESS = 25
XP_ROB_FAIL = 5
XP_BLUFF_PLAY = 15
XP_BLUFF_WIN = 40

# level = 1 + floor(sqrt(xp / XP_LEVEL_BASE)); level 2 needs XP_LEVEL_BASE xp
XP_LEVEL_BASE = 100
LEVEL_UP_CASH_PER_LEVEL = 200     # reward = new_level * this
LEVEL_UP_GEM_EVERY = 5            # +1 gem on every 5th level

# ---- AI chat ----
AI_MODEL = "gemini-3.8-flash"
AI_DAILY_LIMIT = 300              # messages per user per day (IST midnight reset)
AI_MAX_TOKENS = 300
AI_HISTORY_MESSAGES = 12          # remembered per chat
AI_FLOOD_SECONDS = 2              # min gap between replies in one chat
AI_THINKING_BUDGET = 0
AI_TRIGGER_WORDS = ["rumi"]

# Groq fallback, used only when every Gemini model fails
GROQ_MODELS = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]

AI_MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash", "gemini-flash-latest"]   # tried in order

AI_GREETINGS = ["hi+", "hey+", "hello+", "hlo+", "hola", "yo+", "sup", "namaste"]

# Vault & Tasks (/task replaces /work; /spin removed)
VAULT_MAX_CAPACITY = 20_000
VAULT_BASE_CAPACITY = 20_000
VAULT_CAPACITY_PER_LEVEL = 0
TASK_BOTH_COMPLETE_REWARD = 9_000
TASK_ACTION_TARGET_MIN = 20
TASK_ACTION_TARGET_MAX = 25

# Random Events & Anime Catch
EVENT_MIN_GROUP_MEMBERS = 25
EVENT_MAX_PER_GROUP_PER_DAY = 1
CATCH_REWARD_COINS = 2_000
EVENT_REWARD_MIN = 500
EVENT_REWARD_MAX = 1_000
GUESS_MIN_NUMBER = 100
GUESS_MAX_NUMBER = 500
GUESS_MAX_TRIES = 12

# Clans
CLAN_CREATE_FEE = 10_000
CLAN_MAX_MEMBERS = 5
CLAN_NAME_MIN = 3
CLAN_NAME_MAX = 16
CLAN_MAX_LEVEL = 5
CLAN_LEVEL_REQUIREMENTS = {2: 25_000, 3: 100_000, 4: 250_000, 5: 500_000}
CLAN_INVITE_EXPIRE_MINUTES = 10
