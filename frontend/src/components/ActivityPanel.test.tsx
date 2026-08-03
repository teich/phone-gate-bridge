import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { event } from "../test/fixtures";
import { ActivityPanel } from "./ActivityPanel";

describe("ActivityPanel", () => {
  it("hides routine dashboard views by default and exposes them in their own filter", () => {
    const opened = event("call:CA1");
    const dashboardView = event("event:2", "dashboard_view");
    const dashboardDenied = event("event:3", "dashboard_denied");
    const view = render(
      <ActivityPanel events={[opened, dashboardView, dashboardDenied]} />,
    );

    expect(view.container.querySelector('[data-event-id="call:CA1"]')).not.toHaveAttribute(
      "hidden",
    );
    expect(view.container.querySelector('[data-event-id="event:2"]')).toHaveAttribute(
      "hidden",
    );
    expect(view.container.querySelector('[data-event-id="event:3"]')).not.toHaveAttribute(
      "hidden",
    );
    expect(screen.getByText("2 events shown")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dashboard" }));

    expect(view.container.querySelector('[data-event-id="call:CA1"]')).toHaveAttribute(
      "hidden",
    );
    expect(view.container.querySelector('[data-event-id="event:2"]')).not.toHaveAttribute(
      "hidden",
    );
    expect(view.container.querySelector('[data-event-id="event:3"]')).toHaveAttribute(
      "hidden",
    );
    expect(screen.getByText("1 events shown")).toBeInTheDocument();
  });

  it("shows only events in the selected category", () => {
    const opened = event("call:CA1");
    const denied = event("call:CA2", "caller_blocked");
    const view = render(<ActivityPanel events={[opened, denied]} />);

    fireEvent.click(screen.getByRole("button", { name: "Denied" }));

    expect(view.container.querySelector('[data-event-id="call:CA1"]')).toHaveAttribute(
      "hidden",
    );
    expect(view.container.querySelector('[data-event-id="call:CA2"]')).not.toHaveAttribute(
      "hidden",
    );
    expect(screen.getByText("1 events shown")).toBeInTheDocument();
  });
});
