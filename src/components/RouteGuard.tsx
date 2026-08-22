"use client";
/** ApexChain Network Operations Intelligence Platform */

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { useSession } from "@/hooks/useSession";
import { ADMIN_ROUTES } from "@/components/Navigation";

// Routes that do not require authentication
const PUBLIC_PATHS = ["/login", "/register"];

export default function RouteGuard({ children }: { children: React.ReactNode }) {
  const { state, user } = useSession();
  const pathname = usePathname() ?? "";
  const router = useRouter();

  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));
  const isAdminRoute = ADMIN_ROUTES.some((p) => pathname.startsWith(p));
  const isAdmin = user?.role === "admin";

  useEffect(() => {
    if (state === "unauthenticated" && !isPublic) {
      router.replace("/login");
    }
  }, [state, isPublic, router]);

  // Loading — don't flash protected content
  if (state === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-400">
        Loading…
      </div>
    );
  }

  // Unauthenticated on a protected route — redirect in progress, render nothing
  if (state === "unauthenticated" && !isPublic) {
    return null;
  }

  // Authenticated but not admin on an admin-only route
  if (state === "authenticated" && isAdminRoute && !isAdmin) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 text-center">
        <p className="text-lg font-semibold text-slate-800">Access denied</p>
        <p className="text-sm text-slate-500">
          This page is restricted to administrators. Contact your admin if you need access.
        </p>
        <a href="/" className="mt-2 text-sm text-blue-600 underline">
          Return to dashboard
        </a>
      </div>
    );
  }

  return <>{children}</>;
}
