/** ApexChain Frontend Test Suite */
/** ApexChain Network Operations Intelligence Platform */
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BulkImportView from "@/components/bulk-import/bulk-import-view";

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

const mockBulkImport = vi.fn();
vi.mock("@/services/bulkImportService", () => ({
  bulkImportOutages: (...a: unknown[]) => mockBulkImport(...a),
}));

const validCsv = "service_id,start_time,end_time\ns1,2026-01-01,2026-01-02";
const file = (name: string, content: string) => new File([content], name, { type: "text/csv" });

describe("BulkImportView", () => {
  beforeEach(() => mockBulkImport.mockReset());

  it("renders upload area with disabled button", () => {
    render(<BulkImportView />);
    expect(screen.getByText("Bulk Outage Import")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload file/i })).toBeDisabled();
  });

  it("rejects unsupported file types", async () => {
    render(<BulkImportView />);
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "data.txt", { type: "text/plain" })] } });
    expect(await screen.findByText(/Invalid file type/)).toBeInTheDocument();
  });

  it("shows blocking errors for CSV missing required columns", async () => {
    render(<BulkImportView />);
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file("bad.csv", "name,value\nfoo,bar")] } });
    expect(await screen.findByText(/Missing required columns/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload file/i })).toBeDisabled();
  });

  it("warns about unrecognized columns but allows upload to proceed", async () => {
    render(<BulkImportView />);
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [file("with-extra.csv", "service_id,start_time,end_time,mystery_col\ns1,2026-01-01,2026-01-02,x")],
      },
    });
    expect(await screen.findByText(/Unrecognized column.*mystery_col/)).toBeInTheDocument();
    // Warning is non-blocking — upload stays enabled
    expect(screen.getByRole("button", { name: /upload file/i })).toBeEnabled();
  });

  it("recognizes optional known columns without warnings", async () => {
    render(<BulkImportView />);
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [file("with-optional.csv", "service_id,start_time,end_time,severity,description\ns1,2026-01-01,2026-01-02,high,disk full")],
      },
    });
    expect(await screen.findByText("with-optional.csv")).toBeInTheDocument();
    expect(screen.queryByText(/Unrecognized column/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload file/i })).toBeEnabled();
  });

  it("catches row errors beyond the preview window across the whole file", async () => {
    render(<BulkImportView />);
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    // 10 rows: only 5 are previewed, but row 10 is missing end_time
    const rows = ["service_id,start_time,end_time"];
    for (let i = 1; i <= 9; i++) rows.push(`s${i},2026-01-0${i},2026-01-0${i + 1}`);
    rows.push("s10,2026-01-10,");
    fireEvent.change(input, {
      target: { files: [file("deep-error.csv", rows.join("\n"))] },
    });
    expect(await screen.findByText(/Required field "end_time" is empty/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload file/i })).toBeDisabled();
  });

  it("shows success summary after valid upload", async () => {
    mockBulkImport.mockResolvedValue({ imported: 3, skipped: 1, errors: [] });
    render(<BulkImportView />);
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file("data.csv", validCsv)] } });
    await screen.findByText("data.csv");
    fireEvent.click(screen.getByRole("button", { name: /upload file/i }));
    expect(await screen.findByText("Import Summary")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows server validation errors in summary", async () => {
    mockBulkImport.mockResolvedValue({ imported: 0, skipped: 1, errors: [{ row: 2, message: "Invalid date" }] });
    render(<BulkImportView />);
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file("data.csv", validCsv)] } });
    await screen.findByText("data.csv");
    fireEvent.click(screen.getByRole("button", { name: /upload file/i }));
    expect(await screen.findByText("Invalid date")).toBeInTheDocument();
  });
});
