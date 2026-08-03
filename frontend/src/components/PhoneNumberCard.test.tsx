import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PhoneNumberCard } from "./PhoneNumberCard";

describe("PhoneNumberCard", () => {
  it("renders nothing when no phone number is configured", () => {
    const view = render(<PhoneNumberCard phoneNumber="" />);

    expect(view.container).toBeEmptyDOMElement();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("provides native call and one-click copy actions", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<PhoneNumberCard phoneNumber="+17075551234" />);

    expect(
      screen.getByRole("link", { name: "Call gate at (707) 555-1234" }),
    ).toHaveAttribute("href", "tel:+17075551234");

    fireEvent.click(screen.getByRole("button", { name: "Copy number" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("+17075551234"));
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Gate phone number copied.");
  });

  it("falls back to the synchronous copy command on LAN HTTP", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("Not allowed"));
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    render(<PhoneNumberCard phoneNumber="+17075551234" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy number" }));

    await waitFor(() => expect(execCommand).toHaveBeenCalledWith("copy"));
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });
});
