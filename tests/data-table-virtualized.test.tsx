/** ApexChain Network Operations Intelligence Platform */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ColumnDef } from "@tanstack/react-table";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DataTable } from "@/components/data-table";

interface Row {
  id: string;
  name: string;
  status: string;
  description: string;
}

const columns: ColumnDef<Row>[] = [
  { accessorKey: "id", header: "ID" },
  { accessorKey: "name", header: "Name" },
  { accessorKey: "status", header: "Status" },
  { accessorKey: "description", header: "Description" },
];

function makeData(count: number): Row[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `r${i}`,
    name: `Row ${i}`,
    status: i % 2 === 0 ? "open" : "resolved",
    description: `Description for row ${i}`,
  }));
}

/** The virtualized layout renders header + rows as CSS grids. */
function getHeaderGrid(container: HTMLElement): HTMLElement {
  const headerCell = container.querySelector('[role="columnheader"]');
  expect(headerCell).not.toBeNull();
  return headerCell!.parentElement as HTMLElement;
}

function getBodyRows(container: HTMLElement): HTMLElement[] {
  const header = getHeaderGrid(container);
  return Array.from(
    container.querySelectorAll('[role="row"]'),
  ).filter((el) => el !== header).map((el) => el as HTMLElement);
}

const EXPECTED_TEMPLATE = "repeat(4, minmax(0, 1fr))";

describe("DataTable virtualization column alignment", () => {
  beforeEach(() => {
    vi.restoreAllMocks();

    // jsdom reports zero sizes, which makes the virtualizer compute an empty
    // range (outerSize === 0). Give elements a viewport so rows render.
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
      configurable: true,
      get() {
        return 600;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
      configurable: true,
      get() {
        return 800;
      },
    });
  });

  it("shares one grid template between header and body rows (>100 rows)", async () => {
    const { container } = render(
      <DataTable
        columns={columns}
        data={makeData(150)}
        virtualized
        onDensityChange={() => {}}
      />,
    );

    const headerGrid = getHeaderGrid(container);
    expect(headerGrid.style.gridTemplateColumns).toBe(EXPECTED_TEMPLATE);

    // The virtualizer renders the first window asynchronously.
    await waitFor(() => {
      expect(getBodyRows(container).length).toBeGreaterThan(0);
    });

    for (const row of getBodyRows(container)) {
      expect(row.style.gridTemplateColumns).toBe(EXPECTED_TEMPLATE);
    }
  });

  it("keeps header and body aligned when density changes", async () => {
    const { container } = render(
      <DataTable
        columns={columns}
        data={makeData(150)}
        virtualized
        onDensityChange={() => {}}
      />,
    );

    await waitFor(() => {
      expect(getBodyRows(container).length).toBeGreaterThan(0);
    });

    // Default density
    const defaultHeader = getHeaderGrid(container);
    expect(defaultHeader.style.gridTemplateColumns).toBe(EXPECTED_TEMPLATE);
    for (const row of getBodyRows(container)) {
      expect(row.style.gridTemplateColumns).toBe(EXPECTED_TEMPLATE);
    }

    // Switch to compact density
    fireEvent.click(screen.getByRole("button", { name: "compact" }));

    const compactHeader = getHeaderGrid(container);
    expect(compactHeader.style.gridTemplateColumns).toBe(EXPECTED_TEMPLATE);
    for (const row of getBodyRows(container)) {
      expect(row.style.gridTemplateColumns).toBe(EXPECTED_TEMPLATE);
    }

    // Switch to comfortable density
    fireEvent.click(screen.getByRole("button", { name: "comfortable" }));

    const comfortableHeader = getHeaderGrid(container);
    expect(comfortableHeader.style.gridTemplateColumns).toBe(EXPECTED_TEMPLATE);
    for (const row of getBodyRows(container)) {
      expect(row.style.gridTemplateColumns).toBe(EXPECTED_TEMPLATE);
    }
  });

  it("renders header cells with the same count as body cells", async () => {
    const { container } = render(
      <DataTable
        columns={columns}
        data={makeData(150)}
        virtualized
        onDensityChange={() => {}}
      />,
    );

    await waitFor(() => {
      expect(getBodyRows(container).length).toBeGreaterThan(0);
    });

    const headerCells = container.querySelectorAll('[role="columnheader"]');
    expect(headerCells.length).toBe(columns.length);

    for (const row of getBodyRows(container)) {
      expect(row.querySelectorAll('[role="cell"]').length).toBe(columns.length);
    }
  });

  it("does not use per-cell fixed widths that could drift under density", async () => {
    const { container } = render(
      <DataTable
        columns={columns}
        data={makeData(150)}
        virtualized
        onDensityChange={() => {}}
      />,
    );

    await waitFor(() => {
      expect(getBodyRows(container).length).toBeGreaterThan(0);
    });

    const headerCells = container.querySelectorAll('[role="columnheader"]');
    for (const cell of headerCells) {
      expect((cell as HTMLElement).style.width).toBe("");
    }

    for (const row of getBodyRows(container)) {
      const cells = row.querySelectorAll('[role="cell"]');
      for (const cell of cells) {
        expect((cell as HTMLElement).style.width).toBe("");
      }
    }
  });
});
