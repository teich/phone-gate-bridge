import { describe, expect, it } from "vitest";

import { dashboardState } from "../test/fixtures";
import { parseDashboardState } from "./types";

describe("parseDashboardState", () => {
  it("accepts the supported contract", () => {
    expect(parseDashboardState(dashboardState()).door).toBe("Gate");
  });

  it("rejects an incompatible schema", () => {
    expect(() =>
      parseDashboardState({ ...dashboardState(), schema_version: 2 }),
    ).toThrow(/Unsupported dashboard schema/);
  });
});
