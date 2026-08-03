import { describe, expect, it } from "vitest";

import { dashboardState, event } from "../test/fixtures";
import { dashboardContentSignature } from "./useDashboardState";

describe("dashboardContentSignature", () => {
  it("ignores poll timestamps but notices real dashboard changes", () => {
    const initial = dashboardState();
    const timestampOnly = {
      ...initial,
      server_time: initial.server_time + 3,
    };
    const withNewEvent = {
      ...timestampOnly,
      events: [event("call:new"), ...timestampOnly.events],
    };

    expect(dashboardContentSignature(timestampOnly)).toBe(
      dashboardContentSignature(initial),
    );
    expect(dashboardContentSignature(withNewEvent)).not.toBe(
      dashboardContentSignature(initial),
    );
  });
});
