import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { event } from "../test/fixtures";
import { ActivityPanel } from "./ActivityPanel";

describe("ActivityPanel", () => {
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
