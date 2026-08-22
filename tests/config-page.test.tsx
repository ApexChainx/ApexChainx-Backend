/** ApexChain Frontend Test Suite */
/** ApexChain Network Operations Intelligence Platform */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SlaConfigPage from "@/app/config/page";

const mockGet = vi.fn();
const mockPut = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    put: (...args: unknown[]) => mockPut(...args),
  },
}));

describe("SLA config page", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPut.mockReset();
  });

  it("covers config load, validation, and save", async () => {
    mockGet.mockResolvedValue({
      data: {
        critical: {
          threshold_minutes: 30,
          penalty_per_minute: 10,
          reward_base: 150,
        },
        high: {
          threshold_minutes: 60,
          penalty_per_minute: 5,
          reward_base: 75,
        },
      },
    });
    mockPut.mockResolvedValue({
      data: {
        threshold_minutes: 45,
        penalty_per_minute: 10,
        reward_base: 200,
      },
    });

    render(<SlaConfigPage />);

    expect(await screen.findByText("Severity Service Level Agreements")).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("/sla/config");
    expect(screen.getByText("critical")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]!);

    const thresholdInput = screen.getByLabelText("Threshold (minutes)");
    const rewardInput = screen.getByLabelText("Reward Base");

    fireEvent.change(rewardInput, { target: { value: "-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(await screen.findByText("Values cannot be negative.")).toBeInTheDocument();
    expect(mockPut).not.toHaveBeenCalled();

    fireEvent.change(thresholdInput, { target: { value: "45" } });
    fireEvent.change(rewardInput, { target: { value: "200" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(mockPut).toHaveBeenCalledWith("/sla/config/critical", {
        threshold_minutes: 45,
        penalty_per_minute: 10,
        reward_base: 200,
      });
    });

    expect(await screen.findByText("45")).toBeInTheDocument();
    expect(screen.getByText("200")).toBeInTheDocument();
  });
});
