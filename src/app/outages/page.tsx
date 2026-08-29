import { Suspense } from "react";
/** ApexChain Network Operations Intelligence Platform */

import { RouteLoadingState } from "@/components/ui/route-state";
import OutagesConnectedList from "./components/outages-connected-list";

export default function OutagesPage() {
  return (
    <Suspense
      fallback={
        <RouteLoadingState
          title="Loading outages"
          description="Gathering the latest incidents and preparing the outage table."
        />
      }
    >
      <OutagesConnectedList />
    </Suspense>
  );
}