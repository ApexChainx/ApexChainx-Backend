import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CommandPalette from "@/components/CommandPalette";

const mockPush = vi.fn();
let mockPathname = "/outages/OUT-001";

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: mockPush }),
}));

describe("CommandPalette", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockPathname = "/outages/OUT-001";
  });

  it("opens with keyboard shortcut and executes a matching action", () => {
    render(<CommandPalette />);

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(screen.getByRole("dialog", { name: /command palette/i })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/type a command or search/i)).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/type a command or search/i), {
      target: { value: "payments" },
    });

    fireEvent.keyDown(window, { key: "Enter" });

    expect(mockPush).toHaveBeenCalledWith("/payments");
  });

  it("shows the resolve action for the current outage detail page", () => {
    render(<CommandPalette />);

    fireEvent.keyDown(window, { key: "k", metaKey: true });

    expect(screen.getByText(/resolve current outage/i)).toBeInTheDocument();
    expect(screen.getByText(/resolve outage OUT-001 from this page/i)).toBeInTheDocument();
  });

  it("shows the resolve action when typing 'resolve'", () => {
    render(<CommandPalette />);

    fireEvent.keyDown(window, { key: "k", metaKey: true });
    fireEvent.change(screen.getByPlaceholderText(/type a command or search/i), {
      target: { value: "resolve" },
    });

    expect(screen.getByText(/resolve current outage/i)).toBeInTheDocument();
  });

  it("dispatches the resolve event with the R shortcut while the query is empty", () => {
    const onResolve = vi.fn();
    window.addEventListener("command-palette:resolve-outage", onResolve);

    render(<CommandPalette />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    fireEvent.keyDown(window, { key: "r" });

    expect(onResolve).toHaveBeenCalledTimes(1);

    window.removeEventListener("command-palette:resolve-outage", onResolve);
  });

  it("does not run the resolve action from the R key once a query is typed", () => {
    const onResolve = vi.fn();
    window.addEventListener("command-palette:resolve-outage", onResolve);

    render(<CommandPalette />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    fireEvent.change(screen.getByPlaceholderText(/type a command or search/i), {
      target: { value: "r" },
    });
    fireEvent.keyDown(window, { key: "r" });

    expect(onResolve).not.toHaveBeenCalled();

    window.removeEventListener("command-palette:resolve-outage", onResolve);
  });

  it("hides the resolve action when not on an outage detail page", () => {
    mockPathname = "/outages";

    render(<CommandPalette />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });

    expect(screen.queryByText(/resolve current outage/i)).not.toBeInTheDocument();
  });
});
