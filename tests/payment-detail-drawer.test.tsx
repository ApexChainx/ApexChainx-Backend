/** ApexChain Network Operations Intelligence Platform */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PaymentDetailDrawer } from "@/components/payments/payment-detail-drawer";

vi.mock("@/services/paymentService", () => ({
  fetchPayment: vi.fn(),
  retryPayment: vi.fn(),
  reconcilePayment: vi.fn(),
}));

describe("PaymentDetailDrawer", () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when paymentId is null", () => {
    render(<PaymentDetailDrawer paymentId={null} onClose={mockOnClose} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("calls onClose when close button is clicked", async () => {
    const user = userEvent.setup();
    render(<PaymentDetailDrawer paymentId="123" onClose={mockOnClose} />);
    
    const closeButton = screen.getByLabelText("Close");
    await user.click(closeButton);
    
    expect(mockOnClose).toHaveBeenCalled();
  });

  it("calls onClose when escape key is pressed", () => {
    render(<PaymentDetailDrawer paymentId="123" onClose={mockOnClose} />);
    
    fireEvent.keyDown(document, { key: "Escape" });
    
    expect(mockOnClose).toHaveBeenCalled();
  });
});
