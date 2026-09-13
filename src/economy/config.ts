// Every tunable economy number lives here. Change values, redeploy — nothing
// else in the codebase should hard-code a reward amount or limit.
export const ECONOMY_CONFIG = {
  CURRENCY_SYMBOL: "$",

  // /daily
  DAILY_BASE_REWARD: 500,
  DAILY_STREAK_BONUS_PER_DAY: 50,
  DAILY_STREAK_BONUS_CAP: 1000,
  DAILY_RANDOM_BONUS_CHANCE: 0.15,
  DAILY_RANDOM_BONUS_MIN: 50,
  DAILY_RANDOM_BONUS_MAX: 300,
  DAILY_GEM_CHANCE: 0.1,
  DAILY_GEM_AMOUNT: 1,
  DAILY_COOLDOWN_HOURS: 20, // can claim again after this many hours
  DAILY_STREAK_RESET_HOURS: 48, // miss more than this and the streak resets

  STREAK_MILESTONES: [3, 7, 14, 30, 60, 100],
  STREAK_MILESTONE_GEM_REWARD: 5,

  // /pay
  MIN_TRANSFER: 10,
  MAX_TRANSFER: 50_000,
  DAILY_TRANSFER_LIMIT: 100_000,

  // /leaderboard
  LEADERBOARD_PAGE_SIZE: 10,
} as const;
