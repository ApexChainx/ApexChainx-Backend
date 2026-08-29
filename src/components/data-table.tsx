"use client";
/** ApexChain Network Operations Intelligence Platform */

import {
  type ColumnDef,
  type VisibilityState,
  type SortingState,
  type PaginationState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// ─── Types ───────────────────────────────────────────────────────────────────
export type TableDensity = "default" | "compact" | "comfortable";

interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  columnVisibility?: VisibilityState;
  onColumnVisibilityChange?: (visibility: VisibilityState) => void;
  density?: TableDensity;
  onDensityChange?: (density: TableDensity) => void;
  sorting?: SortingState;
  onSortingChange?: (sorting: SortingState) => void;
  pagination?: PaginationState;
  onPaginationChange?: (pagination: PaginationState) => void;
  pageCount?: number;
  rowCount?: number;
  loading?: boolean;
  emptyMessage?: string;
  className?: string;
  enableRowSelection?: boolean;
  rowSelection?: Record<string, boolean>;
  onRowSelectionChange?: (selection: Record<string, boolean>) => void;
  getRowId?: (row: TData, index: number) => string;

  /** When true, uses @tanstack/react-virtual to render only visible rows.
   *  Automatically activates when total rows exceed the threshold (100). */
  virtualized?: boolean;
}

// ─── Density Configuration ───────────────────────────────────────────────────
const DENSITY_CONFIG: Record<TableDensity, { padding: string; textSize: string; height: string }> = {
  compact: { padding: "py-1 px-2", textSize: "text-xs", height: "h-7" },
  default: { padding: "py-2 px-4", textSize: "text-sm", height: "h-9" },
  comfortable: { padding: "py-3 px-6", textSize: "text-sm", height: "h-11" },
};

const DENSITY_OPTIONS: TableDensity[] = ["compact", "default", "comfortable"];

// ─── Sort Icon Component ─────────────────────────────────────────────────────
function SortIcon({ direction }: { direction: "asc" | "desc" | false }) {
  if (!direction) {
    return (
      <svg className="h-3 w-3 text-slate-300 opacity-0 group-hover:opacity-50 transition-opacity" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
      </svg>
    );
  }
  
  return (
    <svg 
      className={`h-3 w-3 transition-transform ${direction === "asc" ? "" : "rotate-180"}`} 
      fill="none" 
      viewBox="0 0 24 24" 
      stroke="currentColor"
    >
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
    </svg>
  );
}

// ─── Loading Skeleton ────────────────────────────────────────────────────────
function TableSkeleton({ columns, density }: { columns: number; density: TableDensity }) {
  const config = DENSITY_CONFIG[density];
  
  return (
    <>
      {Array.from({ length: 5 }).map((_, rowIdx) => (
        <TableRow key={rowIdx} className="animate-pulse">
          {Array.from({ length: columns }).map((_, colIdx) => (
            <TableCell key={colIdx} className={config.padding}>
              <div className={`bg-slate-200 rounded ${config.textSize} ${config.height} w-3/4`} />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

// ─── Empty State ─────────────────────────────────────────────────────────────
function EmptyState({ colSpan, message }: { colSpan: number; message: string }) {
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="h-48 text-center">
        <div className="flex flex-col items-center justify-center gap-2 text-slate-400">
          <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <p className="text-sm font-medium">{message}</p>
        </div>
      </TableCell>
    </TableRow>
  );
}

// ─── Toolbar Components ──────────────────────────────────────────────────────
function DensityControl({ 
  density, 
  onChange 
}: { 
  density: TableDensity; 
  onChange: (d: TableDensity) => void 
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Density</span>
      <div className="flex items-center rounded-lg border border-slate-200 overflow-hidden">
        {DENSITY_OPTIONS.map((d) => (
          <button
            key={d}
            onClick={() => onChange(d)}
            className={`px-3 py-1 text-xs font-medium capitalize transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-inset ${
              density === d
                ? "bg-slate-800 text-white"
                : "bg-white text-slate-600 hover:bg-slate-50"
            }`}
            aria-pressed={density === d}
            title={`${d} density`}
          >
            {d === "default" ? "Normal" : d}
          </button>
        ))}
      </div>
    </div>
  );
}

function ColumnVisibilityControl<TData>({ 
  table 
}: { 
  table: ReturnType<typeof useReactTable<TData>> 
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  
  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const allColumns = table.getAllLeafColumns();
  const visibleCount = allColumns.filter((c) => c.getIsVisible()).length;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2" />
        </svg>
        Columns ({visibleCount}/{allColumns.length})
      </button>
      
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 w-56 rounded-lg border border-slate-200 bg-white p-2 shadow-lg animate-in fade-in zoom-in-95 duration-100">
          <div className="mb-2 flex items-center justify-between border-b border-slate-100 pb-2">
            <span className="text-xs font-medium text-slate-500">Toggle columns</span>
            <button
              onClick={() => allColumns.forEach((c) => c.toggleVisibility(true))}
              className="text-xs text-blue-600 hover:underline"
            >
              Show all
            </button>
          </div>
          <div className="max-h-64 space-y-1 overflow-y-auto">
            {allColumns.map((col) => {
              const header = col.columnDef.header;
              const label = typeof header === "string" ? header : col.id;
              const isVisible = col.getIsVisible();
              
              return (
                <label 
                  key={col.id} 
                  className="flex items-center gap-2 rounded px-2 py-1.5 text-sm text-slate-700 hover:bg-slate-50 cursor-pointer transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={isVisible}
                    onChange={col.getToggleVisibilityHandler()}
                    className="h-4 w-4 rounded border-slate-300 text-slate-800 focus:ring-slate-800"
                  />
                  <span className="flex-1 truncate">{label}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Keyboard Navigation ─────────────────────────────────────────────────────
interface UseTableKeyboardNavigationOptions {
  rowCount: number;
  enabled?: boolean;
  onSelect?: (index: number) => void;
  onFocusRow?: (index: number) => void;
}

/**
 * Roving-tabindex keyboard navigation for table rows (WCAG 2.1).
 *
 * Maintains `focusedIndex` state, moves focus with ArrowUp/ArrowDown/Home/End,
 * and triggers selection on Enter/Space. Only the focused row is tabbable
 * (roving tabindex), and the focused row gets a visible focus ring.
 */
export function useTableKeyboardNavigation({
  rowCount,
  enabled = true,
  onSelect,
  onFocusRow,
}: UseTableKeyboardNavigationOptions) {
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null);
  const rowRefs = useRef<Array<HTMLTableRowElement | null>>([]);
  const focusedIndexRef = useRef<number | null>(null);

  // Keep a ref in sync so the row ref callback can focus rows that mount
  // after navigation (e.g. virtualized rows scrolled into view).
  useEffect(() => {
    focusedIndexRef.current = focusedIndex;
  }, [focusedIndex]);

  // Clamp the focused index when the row count shrinks (pagination/filtering).
  useEffect(() => {
    setFocusedIndex((current) => {
      if (current == null || current < rowCount) return current;
      return rowCount > 0 ? rowCount - 1 : null;
    });
  }, [rowCount]);

  const focusRow = useCallback(
    (index: number) => {
      const el = rowRefs.current[index];
      if (el) {
        el.focus({ preventScroll: true });
      } else {
        // Row not mounted yet (virtualized) — ask the table to scroll it in.
        onFocusRow?.(index);
      }
    },
    [onFocusRow]
  );

  // Move real DOM focus whenever the focused index changes.
  useEffect(() => {
    if (focusedIndex == null) return;
    focusRow(focusedIndex);
  }, [focusedIndex, focusRow]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (!enabled || rowCount === 0) return;

      // Don't hijack keys when an interactive element inside a cell has focus.
      const target = event.target as HTMLElement;
      if (target.closest("button, input, select, textarea, a, [contenteditable='true']")) {
        return;
      }

      switch (event.key) {
        case "ArrowDown":
          event.preventDefault();
          setFocusedIndex((current) =>
            current == null ? 0 : Math.min(current + 1, rowCount - 1)
          );
          break;
        case "ArrowUp":
          event.preventDefault();
          setFocusedIndex((current) =>
            current == null ? 0 : Math.max(current - 1, 0)
          );
          break;
        case "Home":
          event.preventDefault();
          setFocusedIndex(0);
          break;
        case "End":
          event.preventDefault();
          setFocusedIndex(rowCount - 1);
          break;
        case "Enter":
        case " ":
          if (focusedIndex != null) {
            event.preventDefault();
            onSelect?.(focusedIndex);
          }
          break;
        default:
          break;
      }
    },
    [enabled, rowCount, focusedIndex, onSelect]
  );

  const registerRow = useCallback(
    (index: number) => (el: HTMLTableRowElement | null) => {
      rowRefs.current[index] = el;
      if (el && focusedIndexRef.current === index) {
        el.focus({ preventScroll: true });
      }
    },
    []
  );

  const getRowTabIndex = useCallback(
    (index: number) => {
      if (focusedIndex === index) return 0;
      if (focusedIndex == null && index === 0) return 0;
      return -1;
    },
    [focusedIndex]
  );

  return {
    focusedIndex,
    setFocusedIndex,
    handleKeyDown,
    registerRow,
    getRowTabIndex,
  };
}

// ─── Main Component ──────────────────────────────────────────────────────────
/* ─── Virtualization Constants ─────────────────────────────────────────── */
const VIRTUALIZATION_THRESHOLD = 100;
const OVERSCAN = 10;

const ROW_ESTIMATED_HEIGHT: Record<TableDensity, number> = {
  compact: 36,
  default: 48,
  comfortable: 60,
};

/* ─── Main Component ──────────────────────────────────────────────────── */
export function DataTable<TData, TValue>({
  columns,
  data,
  columnVisibility,
  onColumnVisibilityChange,
  density = "default",
  onDensityChange,
  sorting,
  onSortingChange,
  pagination,
  onPaginationChange,
  pageCount,
  rowCount,
  loading = false,
  emptyMessage = "No data available",
  className,
  enableRowSelection = false,
  rowSelection,
  onRowSelectionChange,
  getRowId,
  virtualized = false,
}: DataTableProps<TData, TValue>) {
  const config = DENSITY_CONFIG[density];
  const hasToolbar = onColumnVisibilityChange || onDensityChange;

  const tableContainerRef = useRef<HTMLDivElement>(null);

  /*
   * Row selection is keyed by row id. The index-based default row id is only
   * safe for static, non-paginated lists; across a pagination boundary it
   * silently selects a different record (row 3 of page 1 becomes row 3 of
   * page 2). Rather than ship that behavior, refuse to enable selection
   * without a caller-provided stable id.
   */
  if (
    enableRowSelection &&
    typeof getRowId !== "function" &&
    data.length > 0
  ) {
    throw new Error(
      "DataTable: `enableRowSelection` requires a stable `getRowId`. " +
        "Index-based row ids do not survive pagination or data changes, so " +
        "selection would target the wrong records. Provide " +
        "`getRowId={(row) => row.id}` (or an equivalent stable field) when " +
        "enabling row selection."
    );
  }

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    state: {
      columnVisibility: columnVisibility ?? {},
      sorting: sorting ?? [],
      pagination: pagination ?? { pageIndex: 0, pageSize: 10 },
      rowSelection: rowSelection ?? {},
    },
    onColumnVisibilityChange: (updater) => {
      if (!onColumnVisibilityChange) return;
      const next = typeof updater === "function" ? updater(columnVisibility ?? {}) : updater;
      onColumnVisibilityChange(next);
    },
    onSortingChange: (updater) => {
      if (!onSortingChange) return;
      const next = typeof updater === "function" ? updater(sorting ?? []) : updater;
      onSortingChange(next);
    },
    onPaginationChange: (updater) => {
      if (!onPaginationChange) return;
      const next = typeof updater === "function" ? updater(pagination ?? { pageIndex: 0, pageSize: 10 }) : updater;
      onPaginationChange(next);
    },
    onRowSelectionChange: (updater) => {
      if (!onRowSelectionChange) return;
      const next = typeof updater === "function" ? updater(rowSelection ?? {}) : updater;
      onRowSelectionChange(next);
    },
    getRowId: getRowId ?? ((row, index) => String(index)),
    manualPagination: !!onPaginationChange,
    manualSorting: !!onSortingChange,
    pageCount: pageCount ?? -1,
    rowCount: rowCount ?? data.length,
    enableRowSelection,
  });

  const headerGroups = table.getHeaderGroups();
  const rows = table.getRowModel().rows;
  const leafColumns = table.getAllLeafColumns();
  const visibleLeafColumns = leafColumns.filter((c) => c.getIsVisible());
  const colSpan = visibleLeafColumns.length || 1;

  /* ─── Virtualizer ───────────────────────────────────────────────── */
  const shouldVirtualize = !!(virtualized && rows.length > VIRTUALIZATION_THRESHOLD && !loading);

  /**
   * Shared column template for the virtualized layout. Using one template
   * for the header and every body row guarantees column alignment for any
   * density; minmax(0, 1fr) prevents horizontal overflow when density
   * changes the cell padding.
   */
  const gridTemplateColumns = `repeat(${colSpan}, minmax(0, 1fr))`;

  const rowVirtualizer = useVirtualizer({
    count: shouldVirtualize ? rows.length : 0,
    getScrollElement: () => {
      // Walk up from the table body to find the scrollable container.
      // The container is the outermost div of the table wrapper.
      return tableContainerRef.current;
    },
    estimateSize: () => ROW_ESTIMATED_HEIGHT[density] ?? 48,
    overscan: OVERSCAN,
    getItemKey: (index) => {
      const row = rows[index]!;
      return row.id ?? String(index);
    },
  });

  const virtualRows = shouldVirtualize ? rowVirtualizer.getVirtualItems() : [];
  const totalSize = shouldVirtualize ? rowVirtualizer.getTotalSize() : 0;

  /* ─── Keyboard Navigation ──────────────────────────────────────── */
  const keyboardEnabled = !loading && rows.length > 0;
  const rowIdPrefix = useId();

  const keyboardNavOptions: UseTableKeyboardNavigationOptions = {
    rowCount: rows.length,
    enabled: keyboardEnabled,
  };
  if (enableRowSelection) {
    keyboardNavOptions.onSelect = (index) => rows[index]?.toggleSelected();
  }
  if (shouldVirtualize) {
    keyboardNavOptions.onFocusRow = (index) => rowVirtualizer.scrollToIndex(index);
  }

  const {
    focusedIndex,
    setFocusedIndex,
    handleKeyDown,
    registerRow,
    getRowTabIndex,
  } = useTableKeyboardNavigation(keyboardNavOptions);

  const rowId = (index: number) => `${rowIdPrefix}-row-${index}`;
  const keyboardNavProps = {
    onKeyDown: handleKeyDown,
    "aria-activedescendant": focusedIndex != null ? rowId(focusedIndex) : undefined,
  } as const;

  return (
    <div className={`space-y-3 ${className ?? ""}`}>
      {/* Toolbar */}
      {hasToolbar && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/50 px-4 py-3">
          <div className="flex flex-wrap items-center gap-3">
            {onDensityChange && (
              <DensityControl density={density} onChange={onDensityChange} />
            )}
          </div>
          {onColumnVisibilityChange && (
            <ColumnVisibilityControl table={table} />
          )}
        </div>
      )}

      {/* Table */}
      <div 
        ref={tableContainerRef}
        className={`rounded-lg border border-slate-200 overflow-hidden ${
          shouldVirtualize ? "overflow-auto" : ""
        }`}
        style={shouldVirtualize ? { maxHeight: '65vh' } : undefined}
      >
        <div className="overflow-x-auto">
          {shouldVirtualize ? (
            /* ── Virtualized Table ──
             * Rendered as CSS grids that share one column template
             * (repeat(N, minmax(0, 1fr))), so the header and every body row
             * have identical column boundaries by construction — regardless
             * of density, padding or content size. minmax(0, 1fr) keeps the
             * columns inside the container so density switches never produce
             * an unexpected horizontal scrollbar. Rows are absolutely
             * positioned inside the relative body container for windowed
             * rendering.
             */
            <div role="table" aria-rowcount={rows.length}>
              {/* Sticky header — shares the exact grid template with body rows */}
              <div
                role="row"
                className="sticky top-0 z-10 grid bg-slate-50"
                style={{ gridTemplateColumns: gridTemplateColumns }}
              >
                {headerGroups.flatMap((hg) =>
                  hg.headers.map((h) => {
                    const canSort = h.column.getCanSort();
                    const sortDir = h.column.getIsSorted();

                    return (
                      <div
                        key={h.id}
                        role="columnheader"
                        className={`${config.padding} ${config.textSize} flex items-center gap-1.5 whitespace-nowrap overflow-hidden font-semibold text-slate-700 text-left ${
                          canSort ? "cursor-pointer select-none group" : ""
                        }`}
                        onClick={canSort ? h.column.getToggleSortingHandler() : undefined}
                        aria-sort={
                          sortDir === "asc" ? "ascending" :
                          sortDir === "desc" ? "descending" : "none"
                        }
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {canSort && <SortIcon direction={sortDir} />}
                      </div>
                    );
                  })
                )}
              </div>

              {/* Windowed body */}
              <div style={{ height: `${totalSize}px`, position: "relative" }}>
                {virtualRows.map((virtualRow) => {
                  const row = rows[virtualRow.index]!;
                  const isFocused = focusedIndex === virtualRow.index;
                  return (
                    <div
                      key={row.id}
                      role="row"
                      className={`absolute left-0 right-0 w-full grid overflow-hidden border-b border-slate-200 transition-colors ${
                        row.getIsSelected() ? "bg-blue-50" : "hover:bg-slate-50"
                      } ${enableRowSelection ? "cursor-pointer" : ""} ${
                        isFocused ? "ring-2 ring-inset ring-blue-500 bg-blue-50" : ""
                      }`}
                      style={{
                        gridTemplateColumns: gridTemplateColumns,
                        height: `${virtualRow.size}px`,
                        transform: `translateY(${virtualRow.start}px)`,
                      }}
                      onClick={enableRowSelection ? row.getToggleSelectedHandler() : undefined}
                      data-state={row.getIsSelected() ? "selected" : undefined}
                      aria-selected={enableRowSelection ? row.getIsSelected() : undefined}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <div
                          key={cell.id}
                          role="cell"
                          className={`${config.padding} ${config.textSize} align-middle`}
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            /* ── Normal Table ── */
            <Table>
              <TableHeader>
                {headerGroups.map((hg) => (
                  <TableRow key={hg.id} className="bg-slate-50 hover:bg-slate-50">
                    {hg.headers.map((h) => {
                      const canSort = h.column.getCanSort();
                      const sortDir = h.column.getIsSorted();
                      
                      return (
                        <TableHead 
                          key={h.id} 
                          className={`${config.padding} ${config.textSize} font-semibold text-slate-700 whitespace-nowrap ${
                            canSort ? "cursor-pointer select-none group" : ""
                          }`}
                          onClick={canSort ? h.column.getToggleSortingHandler() : undefined}
                          aria-sort={
                            sortDir === "asc" ? "ascending" : 
                            sortDir === "desc" ? "descending" : "none"
                          }
                        >
                          <div className="flex items-center gap-1.5">
                            {flexRender(h.column.columnDef.header, h.getContext())}
                            {canSort && <SortIcon direction={sortDir} />}
                          </div>
                        </TableHead>
                      );
                    })}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody
                onKeyDown={keyboardNavProps.onKeyDown}
                aria-activedescendant={keyboardNavProps["aria-activedescendant"]}
              >
                {loading ? (
                  <TableSkeleton columns={colSpan} density={density} />
                ) : rows.length === 0 ? (
                  <EmptyState colSpan={colSpan} message={emptyMessage} />
                ) : (
                  rows.map((row, index) => {
                    const isFocused = focusedIndex === index;
                    return (
                      <TableRow 
                        key={row.id} 
                        ref={registerRow(index)}
                        id={rowId(index)}
                        tabIndex={getRowTabIndex(index)}
                        onFocus={() => setFocusedIndex(index)}
                        className={`transition-colors focus:outline-none ${
                          row.getIsSelected() ? "bg-blue-50 hover:bg-blue-50" : "hover:bg-slate-50"
                        } ${enableRowSelection ? "cursor-pointer" : ""} ${
                          isFocused ? "ring-2 ring-inset ring-blue-500 bg-blue-50" : ""
                        }`}
                        onClick={enableRowSelection ? row.getToggleSelectedHandler() : undefined}
                        data-state={row.getIsSelected() ? "selected" : undefined}
                        aria-selected={enableRowSelection ? row.getIsSelected() : undefined}
                      >
                        {row.getVisibleCells().map((cell) => (
                          <TableCell key={cell.id} className={`${config.padding} ${config.textSize}`}>
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </TableCell>
                        ))}
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          )}
        </div>
      </div>

      {/* Pagination */}
      {onPaginationChange && pagination && !loading && rows.length > 0 && (
        <div className="flex items-center justify-between px-1">
          <div className="text-xs text-slate-500">
            Showing <span className="font-medium">{pagination.pageIndex * pagination.pageSize + 1}</span> to{" "}
            <span className="font-medium">
              {Math.min((pagination.pageIndex + 1) * pagination.pageSize, rowCount ?? data.length)}
            </span> of{" "}
            <span className="font-medium">{rowCount ?? data.length}</span> results
          </div>
          
          <div className="flex items-center gap-1">
            <button
              onClick={() => table.setPageIndex(0)}
              disabled={!table.getCanPreviousPage()}
              className="rounded p-1 text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label="First page"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
              </svg>
            </button>
            <button
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="rounded p-1 text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label="Previous page"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </button>
            
            <span className="text-xs text-slate-600 px-2">
              Page <span className="font-medium">{pagination.pageIndex + 1}</span> of{" "}
              <span className="font-medium">{table.getPageCount()}</span>
            </span>
            
            <button
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="rounded p-1 text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label="Next page"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </button>
            <button
              onClick={() => table.setPageIndex(table.getPageCount() - 1)}
              disabled={!table.getCanNextPage()}
              className="rounded p-1 text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
              aria-label="Last page"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
              </svg>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Re-exports ──────────────────────────────────────────────────────────────
export type { ColumnDef, VisibilityState, SortingState, PaginationState };