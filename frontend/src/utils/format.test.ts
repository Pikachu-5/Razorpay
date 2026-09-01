import { describe, expect, it } from "vitest";

import { inr } from "./format";

describe("inr", () => {
  it("renders minor units as rupees", () => {
    expect(inr(19359800)).toBe("₹1,93,598");
  });

  it("keeps negative values signed", () => {
    // A negative net figure (refunds and disputes exceeding recoveries, or a
    // negative experiment result) is a real signal and must not be hidden.
    expect(inr(-500000)).toBe("-₹5,000");
  });

  it("distinguishes zero from missing", () => {
    expect(inr(0)).toBe("₹0");
    expect(inr(undefined)).toBe("—");
  });
});
