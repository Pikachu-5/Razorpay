import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmAction } from "./ConfirmAction";

function renderConfirm(overrides: Partial<Parameters<typeof ConfirmAction>[0]> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ConfirmAction
      title="Dispatch intervention batch"
      summary="Policy-approved actions execute immediately."
      facts={[{ label: "Batch size", value: "up to 10 opportunities" }]}
      confirmLabel="Contact customers"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...overrides}
    />,
  );
  return { onConfirm, onCancel };
}

describe("ConfirmAction", () => {
  it("does not act until the operator confirms", () => {
    const { onConfirm } = renderConfirm();
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Contact customers" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("shows the scope of the action before it runs", () => {
    renderConfirm({
      facts: [
        { label: "Batch size", value: "up to 10 opportunities" },
        { label: "Revenue at risk", value: "₹1,93,598" },
      ],
    });
    expect(screen.getByText("up to 10 opportunities")).toBeInTheDocument();
    expect(screen.getByText("₹1,93,598")).toBeInTheDocument();
  });

  it("distinguishes customer-affecting actions from analysis-only ones", () => {
    const { unmount } = render(
      <ConfirmAction
        title="t" summary="s" facts={[]} confirmLabel="Go" danger
        onConfirm={() => {}} onCancel={() => {}}
      />,
    );
    expect(screen.getByText("Affects customers")).toBeInTheDocument();
    unmount();

    renderConfirm({ safeNote: "Shadow mode is on. Nothing is sent." });
    expect(screen.getByText("Analysis only")).toBeInTheDocument();
    expect(screen.getByText("Shadow mode is on. Nothing is sent.")).toBeInTheDocument();
  });

  it("cancels on Escape and on the cancel button", () => {
    const { onCancel, onConfirm } = renderConfirm();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("is an alert dialog and focuses the confirm control", () => {
    renderConfirm();
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "Contact customers" })).toHaveFocus();
  });

  it("blocks double submission while the action is in flight", () => {
    const { onConfirm } = renderConfirm({ busy: true });
    const confirm = screen.getByRole("button", { name: "Working…" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
