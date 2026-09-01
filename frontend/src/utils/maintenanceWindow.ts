// Postgres runs on a cost-saving nightly stop/start schedule outside the
// buildathon judging window, and stays fully stopped once that window ends.
// This mirrors the schedule the GitHub Actions workflow
// (.github/workflows/postgres-schedule.yml) actually enforces server-side --
// keeping the two in sync is a deliberate manual step, not derived from a
// shared source, so update both together if the schedule ever changes.
//
// Nightly window: 22:00-07:00 IST, every day.
// Critical window: 2026-09-04 through 2026-09-25 IST -- always up, no stops.
// After 2026-09-26 IST: stopped permanently until manually restarted.
const IST_OFFSET_MINUTES = 5 * 60 + 30;
const CRITICAL_WINDOW_START = "2026-09-04";
const CRITICAL_WINDOW_END = "2026-09-25";
const PERMANENT_STOP_FROM = "2026-09-26";

export interface MaintenanceStatus {
  isDown: boolean;
  message: string;
}

function toIst(date: Date): Date {
  return new Date(date.getTime() + IST_OFFSET_MINUTES * 60_000);
}

export function getMaintenanceStatus(now: Date = new Date()): MaintenanceStatus {
  const ist = toIst(now);
  const dateStr = ist.toISOString().slice(0, 10);
  const hour = ist.getUTCHours();

  if (dateStr >= PERMANENT_STOP_FROM) {
    return {
      isDown: true,
      message: "The database is paused for now. Reach out to the site owner to bring it back online.",
    };
  }

  if (dateStr >= CRITICAL_WINDOW_START && dateStr <= CRITICAL_WINDOW_END) {
    return { isDown: false, message: "" };
  }

  const isNight = hour >= 22 || hour < 7;
  if (isNight) {
    return {
      isDown: true,
      message: "The database rests nightly (10 PM-7 AM IST) to save cost. Back online at 7:00 AM IST.",
    };
  }

  return { isDown: false, message: "" };
}
