/** ApexChain Frontend Test Suite */
/** ApexChain Network Operations Intelligence Platform */
import { fireEvent, render, screen } from "@testing-library/react";
import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DataTable, useTableKeyboardNavigation, type ColumnDef } from "@/components/data-table";

interface Item {
  id: string;
  name: string;
  status: string;
}

const columns: ColumnDef<Item>[] = [
  { accessorKey: "id", header: "ID" },
  { accessorKey: "name", header: "Name" },
  { accessorKey: "status", header: "Status" },
];

const data: Item[] = [
  { id: "1", name: "Alpha", status: "open" },
  { id: "2", name: "Beta", status: "open" },
  { id: "3", name: "Gamma", status: "resolved" },
];

function ControlledTable({
  enableRowSelection = true,
}: {
  enableRowSelection?: boolean;
}) {
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  return (
    <DataTable
      columns={columns}
      data={data}
      enableRowSelection={enableRowSelection}
      rowSelection={rowSelection}
      onRowSelectionChange={setRowSelection}
      getRowId={(row) => row.id}
    />
  );
}

function getBodyRows() {
  return document.querySelectorAll("tbody tr");
}

describe("DataTable keyboard navigation", () => {
  it("renders rows with roving tabindex (first row tabbable)", () => {
    render(<ControlledTable />);
    const rows = getBodyRows();
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute("tabindex", "0");
    expect(rows[1]).toHaveAttribute("tabindex", "-1");
    expect(rows[2]).toHaveAttribute("tabindex", "-1");
  });

  /** Focus the first row so arrow navigation starts from a known index. */
  function focusFirstRow() {
    const rows = getBodyRows();
    fireEvent.focus(rows[0]!);
    return rows;
  }

  it("moves focus down with ArrowDown and up with ArrowUp", () => {
    render(<ControlledTable />);
    const rows = focusFirstRow();
    const tbody = document.querySelector("tbody")!;

    fireEvent.keyDown(tbody, { key: "ArrowDown" });
    expect(rows[1]!).toHaveAttribute("tabindex", "0");
    expect(rows[0]!).toHaveAttribute("tabindex", "-1");
    expect(rows[1]!).toHaveFocus();

    fireEvent.keyDown(tbody, { key: "ArrowDown" });
    expect(rows[2]!).toHaveAttribute("tabindex", "0");
    expect(rows[2]!).toHaveFocus();

    fireEvent.keyDown(tbody, { key: "ArrowUp" });
    expect(rows[1]!).toHaveAttribute("tabindex", "0");
    expect(rows[1]!).toHaveFocus();
  });

  it("clamps focus at the first and last row", () => {
    render(<ControlledTable />);
    const rows = focusFirstRow();
    const tbody = document.querySelector("tbody")!;

    // ArrowUp on first row stays on first row
    fireEvent.keyDown(tbody, { key: "ArrowUp" });
    expect(rows[0]!).toHaveAttribute("tabindex", "0");

    // Jump to last row then ArrowDown stays
    fireEvent.keyDown(tbody, { key: "End" });
    expect(rows[2]!).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(tbody, { key: "ArrowDown" });
    expect(rows[2]!).toHaveAttribute("tabindex", "0");
  });

  it("selects the focused row with Enter", () => {
    render(<ControlledTable />);
    const rows = focusFirstRow();
    const tbody = document.querySelector("tbody")!;

    fireEvent.keyDown(tbody, { key: "ArrowDown" });
    fireEvent.keyDown(tbody, { key: "Enter" });

    expect(rows[1]!).toHaveAttribute("data-state", "selected");
    expect(rows[1]!).toHaveAttribute("aria-selected", "true");
  });

  it("selects the focused row with Space", () => {
    render(<ControlledTable />);
    const rows = focusFirstRow();
    const tbody = document.querySelector("tbody")!;

    fireEvent.keyDown(tbody, { key: "ArrowDown" });
    fireEvent.keyDown(tbody, { key: " " });

    expect(rows[1]!).toHaveAttribute("data-state", "selected");
  });

  it("shows a visible focus ring on the focused row", () => {
    render(<ControlledTable />);
    const rows = focusFirstRow();
    const tbody = document.querySelector("tbody")!;

    fireEvent.keyDown(tbody, { key: "ArrowDown" });

    expect(rows[1]!.className).toContain("ring-2");
    expect(rows[1]!.className).toContain("ring-blue-500");
    expect(rows[0]!.className).not.toContain("ring-2");
  });

  it("exposes the focused row via aria-activedescendant", () => {
    render(<ControlledTable />);
    const rows = focusFirstRow();
    const tbody = document.querySelector("tbody")!;

    fireEvent.keyDown(tbody, { key: "ArrowDown" });

    const activeId = tbody.getAttribute("aria-activedescendant");
    expect(activeId).toBeTruthy();
    expect(rows[1]!.id).toBe(activeId);
  });

  it("does not navigate when rows are empty", () => {
    render(
      <DataTable columns={columns} data={[]} enableRowSelection />
    );
    const tbody = document.querySelector("tbody")!;
    fireEvent.keyDown(tbody, { key: "ArrowDown" });
    expect(screen.getByText("No data available")).toBeInTheDocument();
  });
});

describe("useTableKeyboardNavigation hook", () => {
  function HookHarness({ rowCount }: { rowCount: number }) {
    const {
      focusedIndex,
      setFocusedIndex,
      handleKeyDown,
      registerRow,
      getRowTabIndex,
    } = useTableKeyboardNavigation({
      rowCount,
      onSelect: vi.fn(),
    });
    return (
      <div
        data-testid="harness"
        onKeyDown={handleKeyDown}
        data-focused={focusedIndex ?? ""}
      >
        {Array.from({ length: rowCount }).map((_, i) => (
          <div
            key={i}
            ref={registerRow(i)}
            tabIndex={getRowTabIndex(i)}
            onFocus={() => setFocusedIndex(i)}
          >
            row {i}
          </div>
        ))}
      </div>
    );
  }

  it("updates focusedIndex within bounds", () => {
    render(<HookHarness rowCount={3} />);
    const harness = screen.getByTestId("harness");
    const rows = harness.querySelectorAll("[tabindex]");

    // Focus the first row so navigation starts from index 0
    fireEvent.focus(rows[0]!);

    fireEvent.keyDown(harness, { key: "ArrowDown" });
    expect(harness.getAttribute("data-focused")).toBe("1");

    fireEvent.keyDown(harness, { key: "ArrowDown" });
    expect(harness.getAttribute("data-focused")).toBe("2");

    fireEvent.keyDown(harness, { key: "ArrowDown" });
    expect(harness.getAttribute("data-focused")).toBe("2");

    fireEvent.keyDown(harness, { key: "ArrowUp" });
    expect(harness.getAttribute("data-focused")).toBe("1");
  });
});
