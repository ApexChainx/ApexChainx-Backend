/** ApexChain Frontend Test Suite */
/** ApexChain Network Operations Intelligence Platform */
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { DataTable, type ColumnDef } from "@/components/data-table";

interface Row {
  id: string;
  name: string;
}

const columns: ColumnDef<Row>[] = [
  { accessorKey: "id", header: "ID" },
  { accessorKey: "name", header: "Name" },
];

const rows: Row[] = Array.from({ length: 20 }, (_, i) => ({
  id: `r${i}`,
  name: `Row ${i}`,
}));

function bodyRows() {
  return Array.from(document.querySelectorAll("tbody tr")) as HTMLTableRowElement[];
}

function SelectablePaginated({ pageSize = 10 }: { pageSize?: number }) {
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize });
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});

  // DataTable pagination is manual/controlled: the parent passes the page
  // slice and a stable `getRowId`, mirroring how real clients feed it.
  const pageSlice = rows.slice(
    pagination.pageIndex * pagination.pageSize,
    pagination.pageIndex * pagination.pageSize + pagination.pageSize
  );

  return (
    <>
      <DataTable
        columns={columns}
        data={pageSlice}
        enableRowSelection
        getRowId={(row) => row.id}
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
        pagination={pagination}
        onPaginationChange={setPagination}
        pageCount={Math.ceil(rows.length / pageSize)}
        rowCount={rows.length}
      />
      <pre data-testid="selection">{JSON.stringify(rowSelection)}</pre>
    </>
  );
}

describe("DataTable row selection identity", () => {
  it("keeps a record selected across pagination with stable row ids", () => {
    render(<SelectablePaginated />);

    // Page 1 renders r0..r9.
    const pageOne = bodyRows();
    expect(pageOne).toHaveLength(10);
    expect(pageOne[0]!.textContent).toContain("r0");

    // Select the record at index 2 on page 1 (r2).
    fireEvent.click(pageOne[2]!);
    expect(JSON.parse(screen.getByTestId("selection").textContent!)).toEqual({ r2: true });

    // Move to page 2 (r10..r19).
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    const pageTwo = bodyRows();
    expect(pageTwo).toHaveLength(10);
    expect(pageTwo[0]!.textContent).toContain("r10");

    // Selection is keyed by the stable id `r2`, not by the row index, so the
    // page-2 row at index 2 (r12) must NOT be selected.
    expect(pageTwo[2]!).not.toHaveAttribute("data-state", "selected");
    expect(JSON.parse(screen.getByTestId("selection").textContent!)).toEqual({ r2: true });

    // Returning to page 1, the same record (r2 at index 2) is still selected.
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    const back = bodyRows();
    expect(back[2]!.textContent).toContain("r2");
    expect(back[2]!).toHaveAttribute("data-state", "selected");
  });

  it("throws when row selection is enabled without a stable getRowId", () => {
    // React also logs a render error to the console; silence it so the thrown
    // message below is the only signal under assertion.
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      expect(() =>
        render(<DataTable columns={columns} data={rows} enableRowSelection />)
      ).toThrow(/getRowId/);
    } finally {
      errorSpy.mockRestore();
    }
  });
});