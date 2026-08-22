"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

type CommandAction = {
  id: string;
  name: string;
  description?: string;
  shortcut?: string;
  run: () => void;
};

export default function CommandPalette() {
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const currentOutageId = useMemo(() => {
    const segments = pathname.split("/").filter(Boolean);
    if (segments[0] === "outages" && segments[1]) {
      return segments[1];
    }
    return undefined;
  }, [pathname]);

  const actions = useMemo<CommandAction[]>(() => {
    const list: CommandAction[] = [
      {
        id: "dashboard",
        name: "Open dashboard",
        description: "Go to the main overview",
        run: () => router.push("/"),
      },
      {
        id: "outages",
        name: "Open outages",
        description: "View the incident list",
        run: () => router.push("/outages"),
      },
      {
        id: "new-outage",
        name: "New outage",
        description: "Create a new incident",
        run: () => router.push("/outages/new"),
      },
      {
        id: "payments",
        name: "Open payments",
        description: "Review payment activity",
        run: () => router.push("/payments"),
      },
      {
        id: "bulk-import",
        name: "Open bulk import",
        description: "Import outage data in bulk",
        run: () => router.push("/bulk-import"),
      },
      {
        id: "settings",
        name: "Open settings",
        description: "Manage app preferences",
        run: () => router.push("/setting"),
      },
      {
        id: "retry-queue",
        name: "Open retry queue",
        description: "Review retried payments",
        run: () => router.push("/payments/retry-queue"),
      },
    ];

    if (currentOutageId) {
      list.unshift({
        id: "resolve-current",
        name: "Resolve current outage",
        description: `Resolve outage ${currentOutageId} from this page`,
        shortcut: "R",
        run: () => {
          window.dispatchEvent(new CustomEvent("command-palette:resolve-outage"));
        },
      });
    }

    return list;
  }, [currentOutageId, router]);

  const filteredActions = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return actions;

    return actions.filter((action) => {
      const haystack = `${action.name} ${action.description ?? ""}`.toLowerCase();
      return haystack.includes(normalized);
    });
  }, [actions, query]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    setSelectedIndex(0);
  }, [open, query]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen(true);
        return;
      }

      if (!open) return;

      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        setQuery("");
        return;
      }

      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelectedIndex((current) => (current + 1) % Math.max(filteredActions.length, 1));
        return;
      }

      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelectedIndex((current) =>
          current === 0 ? Math.max(filteredActions.length - 1, 0) : current - 1,
        );
        return;
      }

      if (event.key.toLowerCase() === "r" && !event.metaKey && !event.ctrlKey && !event.altKey && query.trim() === "") {
        // Ctrl+K → R: run the resolve action directly while the query is empty.
        const resolveAction = filteredActions.find((action) => action.id === "resolve-current");
        if (resolveAction) {
          event.preventDefault();
          setOpen(false);
          setQuery("");
          resolveAction.run();
        }
        return;
      }

      if (event.key === "Enter") {
        event.preventDefault();
        const action = filteredActions[selectedIndex];
        if (action) {
          setOpen(false);
          setQuery("");
          action.run();
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [filteredActions, open, selectedIndex]);

  const handleSelect = (action: CommandAction) => {
    setOpen(false);
    setQuery("");
    action.run();
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-4 right-4 z-50 rounded-full border border-slate-300 bg-white/90 px-4 py-2 text-sm font-medium text-slate-700 shadow-lg backdrop-blur hover:bg-white"
      >
        ⌘K
      </button>

      {open && (
        <div className="fixed inset-0 z-[60] flex items-start justify-center bg-slate-950/40 px-4 py-16">
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Command palette"
            className="w-full max-w-2xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900"
          >
            <div className="border-b border-slate-200 p-3 dark:border-slate-700">
              <input
                ref={inputRef}
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setSelectedIndex(0);
                }}
                placeholder="Type a command or search"
                className="w-full border-0 bg-transparent text-sm outline-none"
              />
            </div>

            <div className="max-h-80 overflow-y-auto p-2">
              {filteredActions.length === 0 ? (
                <div className="px-3 py-4 text-sm text-slate-500">No commands found.</div>
              ) : (
                filteredActions.map((action, index) => (
                  <button
                    key={action.id}
                    type="button"
                    onClick={() => handleSelect(action)}
                    className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm ${
                      index === selectedIndex
                        ? "bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-white"
                        : "text-slate-700 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
                    }`}
                  >
                    <span>
                      <span className="font-medium">{action.name}</span>
                      {action.description ? (
                        <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">{action.description}</span>
                      ) : null}
                    </span>
                    <span className="text-xs text-slate-400">
                      {action.shortcut ? (
                        <kbd className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[10px] uppercase text-slate-500 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {action.shortcut}
                        </kbd>
                      ) : (
                        "↵"
                      )}
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
