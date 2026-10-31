"use client";

import { useEffect, useState } from "react";
import { discoverBackend } from "@/lib/api";

/** Hold rendering until we know which backend to talk to (at most a few seconds). */
export function BackendGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let done = false;
    const finish = () => {
      if (!done) {
        done = true;
        setReady(true);
      }
    };
    const timer = setTimeout(finish, 6000);
    discoverBackend().finally(finish);
    return () => clearTimeout(timer);
  }, []);
  if (!ready)
    return (
      <div className="gate" role="status">
        <span className="spinner" aria-hidden /> Connecting to the OpsLoop backend…
      </div>
    );
  return <>{children}</>;
}
