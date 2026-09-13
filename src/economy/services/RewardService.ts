import { WalletService } from "./WalletService";
import { TransactionService } from "./TransactionService";
import { ECONOMY_CONFIG } from "../config";
import type { StreakRow } from "../../core/types";

export interface DailyClaimResult {
  status: "claimed" | "cooldown" | "already_claimed_race";
  cashReward?: number;
  gemReward?: number;
  luckyBonus?: number;
  newStreak?: number;
  milestoneHit?: number | null;
  nextClaimAt?: number;
}

export class RewardService {
  private wallets: WalletService;
  private tx: TransactionService;

  constructor(private db: D1Database) {
    this.wallets = new WalletService(db);
    this.tx = new TransactionService(db);
  }

  async claimDaily(userId: string): Promise<DailyClaimResult> {
    const now = Date.now();

    const streak = await this.db
      .prepare(`SELECT * FROM streaks WHERE user_id = ?`)
      .bind(userId)
      .first<StreakRow>();

    const cooldownMs = ECONOMY_CONFIG.DAILY_COOLDOWN_HOURS * 3600 * 1000;
    if (streak?.last_claim_at && now - streak.last_claim_at < cooldownMs) {
      return { status: "cooldown", nextClaimAt: streak.last_claim_at + cooldownMs };
    }

    const resetMs = ECONOMY_CONFIG.DAILY_STREAK_RESET_HOURS * 3600 * 1000;
    const streakBroken = !streak?.last_claim_at || now - streak.last_claim_at > resetMs;
    const newStreak = streakBroken ? 1 : streak!.current_streak + 1;
    const longestStreak = Math.max(newStreak, streak?.longest_streak ?? 0);

    const streakBonus = Math.min(
      newStreak * ECONOMY_CONFIG.DAILY_STREAK_BONUS_PER_DAY,
      ECONOMY_CONFIG.DAILY_STREAK_BONUS_CAP
    );

    let luckyBonus = 0;
    if (Math.random() < ECONOMY_CONFIG.DAILY_RANDOM_BONUS_CHANCE) {
      const range = ECONOMY_CONFIG.DAILY_RANDOM_BONUS_MAX - ECONOMY_CONFIG.DAILY_RANDOM_BONUS_MIN;
      luckyBonus = ECONOMY_CONFIG.DAILY_RANDOM_BONUS_MIN + Math.floor(Math.random() * range);
    }

    let gemReward = 0;
    if (Math.random() < ECONOMY_CONFIG.DAILY_GEM_CHANCE) {
      gemReward = ECONOMY_CONFIG.DAILY_GEM_AMOUNT;
    }

    const milestoneHit = (ECONOMY_CONFIG.STREAK_MILESTONES as readonly number[]).includes(newStreak)
      ? newStreak
      : null;
    if (milestoneHit) {
      gemReward += ECONOMY_CONFIG.STREAK_MILESTONE_GEM_REWARD;
    }

    const cashReward = ECONOMY_CONFIG.DAILY_BASE_REWARD + streakBonus + luckyBonus;

    const wallet = await this.wallets.getWallet(userId);
    if (!wallet) throw new Error("Wallet not found — call ensureUserAndWallet first");

    // The idempotency key is scoped to (user, UTC day). If two requests race
    // for the same day, the UNIQUE constraint on transactions.idempotency_key
    // lets only one of them succeed — and since it's all one db.batch(),
    // the wallet credit and the streak update roll back together with it.
    const idempotencyKey = `daily:${userId}:${dateKey(now)}`;

    const milestonesClaimed: number[] = streak ? JSON.parse(streak.milestones_claimed || "[]") : [];
    if (milestoneHit) milestonesClaimed.push(milestoneHit);

    try {
      await this.db.batch([
        this.db
          .prepare(`UPDATE wallets SET cash = cash + ?, gems = gems + ?, updated_at = ? WHERE user_id = ?`)
          .bind(cashReward, gemReward, now, userId),
        this.db
          .prepare(
            `INSERT INTO streaks (user_id, current_streak, longest_streak, last_claim_at, milestones_claimed)
             VALUES (?, ?, ?, ?, ?)
             ON CONFLICT(user_id) DO UPDATE SET
               current_streak = excluded.current_streak,
               longest_streak = excluded.longest_streak,
               last_claim_at = excluded.last_claim_at,
               milestones_claimed = excluded.milestones_claimed`
          )
          .bind(userId, newStreak, longestStreak, now, JSON.stringify(milestonesClaimed)),
        this.tx.buildInsertStatement({
          userId,
          type: "DAILY_REWARD",
          currency: "cash",
          amount: cashReward,
          balanceBefore: wallet.cash,
          balanceAfter: wallet.cash + cashReward,
          source: "daily_command",
          idempotencyKey,
          metadata: { gemReward, luckyBonus, newStreak, milestoneHit },
        }),
      ]);
    } catch (err: any) {
      if (String(err?.message ?? err).toUpperCase().includes("UNIQUE")) {
        return { status: "already_claimed_race" };
      }
      throw err;
    }

    return { status: "claimed", cashReward, gemReward, luckyBonus, newStreak, milestoneHit };
  }
}

function dateKey(ts: number): string {
  return new Date(ts).toISOString().slice(0, 10); // UTC YYYY-MM-DD
}
