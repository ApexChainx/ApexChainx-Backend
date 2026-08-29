/** ApexChain Frontend Test Suite — live-region a11y primitives */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Announcer, LiveRegion, LiveStatus } from "@/components/ui/live-region";

afterEach(() => cleanup());

describe("LiveStatus", () => {
  it("renders a status role with polite, atomic ARIA by default", () => {
    render(<LiveStatus status="Connected" />);

    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("aria-atomic", "true");
  });

  it("separates the visually-hidden label from the visible status text", () => {
    render(<LiveStatus status="Connected" label="Connection" />);

    const region = screen.getByRole("status");

    // The sr-only label and the visible status are distinct child nodes —
    // not concatenated into a single opaque string — so each can be styled
    // (or hidden) independently.
    const label = region.querySelector("span.sr-only");
    expect(label).not.toBeNull();
    expect(label).toHaveTextContent("Connection:");

    const visible = region.querySelector("span[aria-hidden='true']");
    expect(visible).not.toBeNull();
    expect(visible).toHaveTextContent("Connected");

    // The visible status text itself is hidden from AT — only the sr-only
    // label + the region's own text content reach the accessibility tree.
    expect(visible).toHaveAttribute("aria-hidden", "true");
  });

  it("defaults the label to 'Status' when none is provided", () => {
    render(<LiveStatus status="Degraded" />);

    expect(screen.getByText("Status:")).toBeInTheDocument();
  });

  it("applies the provided className to the region", () => {
    render(<LiveStatus status="Connected" className="custom-status" />);

    expect(screen.getByRole("status")).toHaveClass("custom-status");
  });
});

describe("LiveRegion", () => {
  it("renders a log role with polite, non-atomic ARIA by default", () => {
    render(
      <LiveRegion>
        <p>Outage OUT-001 resolved</p>
      </LiveRegion>,
    );

    const region = screen.getByRole("log");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("aria-atomic", "false");
    expect(region).toHaveTextContent("Outage OUT-001 resolved");
  });

  it("switches to assertive aria-live when assertive is opted in", () => {
    render(
      <LiveRegion assertive>
        <p>Payment failed</p>
      </LiveRegion>,
    );

    expect(screen.getByRole("log")).toHaveAttribute("aria-live", "assertive");
  });

  it("stays non-atomic regardless of the assertive flag", () => {
    render(
      <LiveRegion assertive>
        <p>Payment failed</p>
      </LiveRegion>,
    );

    // aria-atomic is hard-coded to "false" on LiveRegion — only aria-live
    // toggles with the assertive prop.
    expect(screen.getByRole("log")).toHaveAttribute("aria-atomic", "false");
  });

  it("applies the provided className to the region", () => {
    render(
      <LiveRegion className="custom-log">
        <p>Entry</p>
      </LiveRegion>,
    );

    expect(screen.getByRole("log")).toHaveClass("custom-log");
  });
});

describe("Announcer", () => {
  it("renders a log role with polite, atomic ARIA by default", () => {
    render(<Announcer message="Outage created" />);

    const region = screen.getByRole("log");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("aria-atomic", "true");
    expect(region).toHaveTextContent("Outage created");
  });

  it("switches to assertive aria-live when assertive is opted in", () => {
    render(<Announcer message="Session expired" assertive />);

    expect(screen.getByRole("log")).toHaveAttribute("aria-live", "assertive");
  });

  it("is visually hidden via the sr-only class", () => {
    render(<Announcer message="Outage created" />);

    expect(screen.getByRole("log")).toHaveClass("sr-only");
  });
});
