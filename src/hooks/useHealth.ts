import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { onlineManager } from "@tanstack/react-query";

export type HealthStatus = "green" | "red";

export function useHealth() {
  const [status, setStatus] = useState<HealthStatus>("green");
  const [isOffline, setIsOffline] = useState(false);

  useEffect(() => {
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let checking = false;

    const scheduleCheck = () => {
      if (timeoutId || !navigator.onLine) return;
      timeoutId = setTimeout(() => {
        timeoutId = null;
        void checkHealth();
      }, 30000);
    };

    const checkHealth = async () => {
      if (!navigator.onLine || checking) return;
      checking = true;
      try {
        await api.get("/health", { timeout: 5000 });
        setStatus("green");
      } catch {
        // Backend reachability is separate from browser connectivity. Keep
        // React Query online when the browser still has network access.
        setStatus("red");
      } finally {
        checking = false;
        scheduleCheck();
      }
    };

    const updateOnlineStatus = () => {
      const browserOnline = navigator.onLine;
      setIsOffline(!browserOnline);
      onlineManager.setOnline(browserOnline);
      if (browserOnline) {
        void checkHealth();
      } else if (timeoutId) {
        clearTimeout(timeoutId);
        timeoutId = null;
      }
    };

    window.addEventListener("online", updateOnlineStatus);
    window.addEventListener("offline", updateOnlineStatus);
    updateOnlineStatus();

    return () => {
      window.removeEventListener("online", updateOnlineStatus);
      window.removeEventListener("offline", updateOnlineStatus);
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, []);

  return { status, isOffline };
}
