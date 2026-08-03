import { describe, expect, it } from "vitest";

import { dashboardState } from "../test/fixtures";
import { parseDashboardState } from "./types";

describe("parseDashboardState", () => {
  it("accepts the supported contract", () => {
    const parsed = parseDashboardState(dashboardState());
    expect(parsed.door).toBe("Gate");
    expect(parsed.phone_number).toBe("+17075551234");
  });

  it("rejects an incompatible schema", () => {
    expect(() =>
      parseDashboardState({ ...dashboardState(), schema_version: 2 }),
    ).toThrow(/Unsupported dashboard schema/);
  });

  it("normalizes a missing gate phone number for schema v1 compatibility", () => {
    const state = dashboardState();
    expect(
      parseDashboardState({ ...state, phone_number: undefined }).phone_number,
    ).toBe("");
  });
});
