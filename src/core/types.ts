export interface Env {
  DB: D1Database;
  TELEGRAM_BOT_TOKEN: string;
  WEBHOOK_SECRET: string;
}

export type Currency = "cash" | "gems" | "xp";

export interface Wallet {
  user_id: string;
  cash: number;
  gems: number;
  xp: number;
  level: number;
  daily_transferred: number;
  daily_transferred_date: string | null;
  created_at: number;
  updated_at: number;
}

export interface StreakRow {
  user_id: string;
  current_streak: number;
  longest_streak: number;
  last_claim_at: number | null;
  milestones_claimed: string;
}

export type TransactionType =
  | "DAILY_REWARD"
  | "GAME_REWARD"
  | "TRANSFER_SENT"
  | "TRANSFER_RECEIVED"
  | "ACHIEVEMENT_REWARD"
  | "ADMIN_ADJUSTMENT"
  | "SHOP_PURCHASE"
  | "REFUND";
