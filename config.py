# Every tunable economy number lives here. Change values, restart the bot —
# nothing else in the codebase should hard-code a reward amount or limit.

CURRENCY_SYMBOL = "$"

# /tribute (daily reward) — resets at UTC midnight, not a rolling timer.
DAILY_BASE_REWARD = 2500
DAILY_WEEKLY_BONUS_CASH = 5000       # replaces the base reward every Nth streak day
DAILY_WEEKLY_BONUS_INTERVAL = 7      # every 7th consecutive day (7, 14, 21, ...)

DAILY_RANDOM_BONUS_CHANCE = 0.15
DAILY_RANDOM_BONUS_MIN = 50
DAILY_RANDOM_BONUS_MAX = 300
DAILY_GEM_CHANCE = 0.1
DAILY_GEM_AMOUNT = 1

STREAK_MILESTONES = [3, 7, 14, 30, 60, 100]
STREAK_MILESTONE_GEM_REWARD = 5

# /pay
MIN_TRANSFER = 10
MAX_TRANSFER = 50_000
TRANSFER_TAX_PERCENT = 0.10   # taken from what the receiver gets
DAILY_TRANSFER_LIMIT = 100_000

# /ward (shield)
SHIELD_COST = 400
SHIELD_DURATION_HOURS = 24

# /rob
ROB_COOLDOWN_MINUTES = 0
ROB_MIN_VICTIM_BALANCE = 200          # victim must have at least this to be robbable
ROB_MIN_AMOUNT = 50              # smallest amount you can try to rob
ROB_SUCCESS_CHANCE = 0.45
ROB_STEAL_PERCENT_MIN = 0.10
ROB_STEAL_PERCENT_MAX = 0.30
ROB_TAX_PERCENT = 0.10        # taken from the robber's loot
ROB_FAIL_PENALTY_PERCENT_MIN = 0.05   # on a failed rob, robber pays this to the victim
ROB_FAIL_PENALTY_PERCENT_MAX = 0.15

# /empire (leaderboard)
LEADERBOARD_PAGE_SIZE = 10

# Shadow Bluff (card game)
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
