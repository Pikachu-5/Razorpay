import { describe, expect, it } from "vitest";

import { buildLanes } from "./recoveryQueueLanes";
import type { Opportunity } from "../api/types";

const NOW = new Date("2026-08-31T12:00:00Z").getTime();

function opp(over: Partial<Opportunity> & { id: string }): Opportunity {
  return {
    status: "open",
    category: "recoverable",
    amount_minor: 100000,
    experiment_group: "treatment",
    contact_attempts: 0,
    best_action: null,
    expected_recovery_minor: 0,
    // Far outside the six-hour urgency window unless a test says otherwise.
    window_ends_at: new Date(NOW + 48 * 3600_000).toISOString(),
    created_at: new Date(NOW).toISOString(),
    ...over,
  };
}

function lane(lanes: ReturnType<typeof buildLanes>, key: string) {
  return lanes.find((l) => l.key === key)!;
}

describe("recovery queue triage", () => {
  it("puts policy escalations in their own lane, largest first", () => {
    const lanes = buildLanes(
      [
        opp({ id: "small", status: "escalated", amount_minor: 50000 }),
        opp({ id: "large", status: "escalated", amount_minor: 9000000 }),
      ],
      NOW,
    );
    expect(lane(lanes, "escalated").items.map((o) => o.id)).toEqual(["large", "small"]);
    expect(lane(lanes, "escalated").urgent).toBe(true);
  });

  it("flags opportunities whose recovery window closes within six hours", () => {
    const lanes = buildLanes(
      [
        opp({ id: "soon", window_ends_at: new Date(NOW + 2 * 3600_000).toISOString() }),
        opp({ id: "later", window_ends_at: new Date(NOW + 20 * 3600_000).toISOString() }),
      ],
      NOW,
    );
    expect(lane(lanes, "closing").items.map((o) => o.id)).toEqual(["soon"]);
    expect(lane(lanes, "undecided").items.map((o) => o.id)).toEqual(["later"]);
  });

  it("ranks non-escalated work by expected recovery, not by amount", () => {
    // A large opportunity the model rates poorly must not outrank a smaller one
    // that is actually worth acting on — that is the whole point of the ranking.
    const lanes = buildLanes(
      [
        opp({ id: "big-but-hopeless", amount_minor: 9000000, expected_recovery_minor: 1000 }),
        opp({ id: "smaller-but-winnable", amount_minor: 200000, expected_recovery_minor: 80000 }),
      ],
      NOW,
    );
    expect(lane(lanes, "undecided").items.map((o) => o.id)).toEqual([
      "smaller-but-winnable",
      "big-but-hopeless",
    ]);
  });

  it("leaves resolved opportunities out of the queue entirely", () => {
    const lanes = buildLanes(
      [
        opp({ id: "done", status: "recovered_intervention" }),
        opp({ id: "closed", status: "closed_not_viable" }),
        opp({ id: "natural", status: "recovered_natural" }),
      ],
      NOW,
    );
    expect(lanes.every((l) => l.items.length === 0)).toBe(true);
  });

  it("keeps shadow and holdout work visible — the money is still out", () => {
    const lanes = buildLanes(
      [opp({ id: "shadow", status: "shadow_observation", window_ends_at: new Date(NOW + 3600_000).toISOString() })],
      NOW,
    );
    expect(lane(lanes, "closing").items.map((o) => o.id)).toEqual(["shadow"]);
  });
});

describe("recovery queue edge cases", () => {
  it("does not present an already-expired window as urgent work", () => {
    // Acting on a closed window recovers nothing. Ranking it as urgent would
    // send an operator to spend attention on money that is already gone.
    const lanes = buildLanes(
      [opp({ id: "expired", window_ends_at: new Date(NOW - 3600_000).toISOString() })],
      NOW,
    );
    expect(lane(lanes, "closing").items).toHaveLength(0);
    expect(lane(lanes, "stale").items.map((o) => o.id)).toEqual(["expired"]);
    expect(lane(lanes, "stale").urgent).toBe(false);
  });

  it("survives an unparseable window timestamp instead of dropping the row", () => {
    const lanes = buildLanes([opp({ id: "bad", window_ends_at: "not-a-date" })], NOW);
    expect(lane(lanes, "undecided").items.map((o) => o.id)).toEqual(["bad"]);
  });
});

describe("recovery queue completeness", () => {
  it("accounts for every unresolved opportunity in some lane", () => {
    // A row that matches no lane vanishes from the operator's view while the
    // money is still out. Every unresolved status must land somewhere.
    const unresolved = [
      "open",
      "decision_in_progress",
      "intervention_pending",
      "native_retry_pending",
      "shadow_observation",
      "escalated",
    ];
    const lanes = buildLanes(
      unresolved.map((status) => opp({ id: status, status })),
      NOW,
    );
    const placed = lanes.flatMap((l) => l.items.map((o) => o.id));
    expect(placed.sort()).toEqual([...unresolved].sort());
  });

  it("separates work in flight from work that needs a decision", () => {
    const lanes = buildLanes(
      [
        opp({ id: "acted", status: "intervention_pending" }),
        opp({ id: "undecided", status: "open" }),
      ],
      NOW,
    );
    expect(lane(lanes, "awaiting").items.map((o) => o.id)).toEqual(["acted"]);
    expect(lane(lanes, "undecided").items.map((o) => o.id)).toEqual(["undecided"]);
  });
});
