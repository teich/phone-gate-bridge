import { useEffect, useState } from "react";

import { prettyNumber } from "../format";
import { Icon } from "./Icon";

type CopyStatus = "idle" | "copied" | "error";

export function PhoneNumberCard({ phoneNumber }: { phoneNumber: string }) {
  const [copyStatus, setCopyStatus] = useState<CopyStatus>("idle");
  const canonicalPhoneNumber = phoneNumber.trim();
  const displayNumber = prettyNumber(canonicalPhoneNumber);

  useEffect(() => {
    setCopyStatus("idle");
  }, [phoneNumber]);

  const copyNumber = async () => {
    try {
      await copyText(canonicalPhoneNumber);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("error");
    }
  };

  if (!canonicalPhoneNumber) return null;

  return (
    <section className="phone-card" aria-labelledby="gate-phone-label">
      <span className="phone-card-icon" aria-hidden="true">
        <Icon name="phone" />
      </span>
      <div className="phone-card-body">
        <p className="phone-card-label" id="gate-phone-label">
          Call the gate
        </p>
        <a
          className="phone-card-number"
          href={`tel:${canonicalPhoneNumber}`}
          aria-label={`Call gate at ${displayNumber}`}
        >
          {displayNumber}
        </a>
        <p className="phone-card-hint">Call from an authorized phone to open the gate.</p>
      </div>
      <button
        className="phone-copy-button"
        type="button"
        data-status={copyStatus}
        onClick={() => void copyNumber()}
      >
        {copyStatus === "copied"
          ? "Copied"
          : copyStatus === "error"
            ? "Select number to copy"
            : "Copy number"}
      </button>
      <span className="visually-hidden" role="status" aria-live="polite">
        {copyStatus === "copied"
          ? "Gate phone number copied."
          : copyStatus === "error"
            ? "Automatic copy was unavailable. Select the phone number to copy it."
            : ""}
      </span>
    </section>
  );
}

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText !== undefined) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // LAN-hosted dashboards may not receive Clipboard API permission. Fall
      // through to the browser's synchronous copy command in that case.
    }
  }

  const input = document.createElement("input");
  input.value = value;
  input.readOnly = true;
  input.className = "phone-copy-fallback";
  document.body.append(input);
  input.select();
  input.setSelectionRange(0, value.length);
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("Copy command was unavailable");
}
