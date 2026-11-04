import { ArrowClockwiseIcon, LockIcon, TrayIcon, WarningIcon, WifiSlashIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { ApiError } from "../../lib/api";
import { Button } from "./primitives";

export function EmptyState({ icon, title, body, action, compact }: { icon?: ReactNode; title: string; body?: ReactNode; action?: ReactNode; compact?: boolean }) {
  return (
    <div className={`flex flex-col items-center justify-center text-center ${compact ? "py-8" : "py-14"}`}>
      <div className="mb-3 text-faint">{icon ?? <TrayIcon size={30} weight="light" />}</div>
      <p className="text-sm font-semibold text-ink">{title}</p>
      {body && <p className="mt-1 max-w-sm text-[13px] text-muted">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const e = error instanceof ApiError ? error : null;
  const icon = e?.isNetwork ? <WifiSlashIcon size={30} weight="light" /> : e?.isForbidden ? <LockIcon size={30} weight="light" /> : <WarningIcon size={30} weight="light" />;
  const title = e?.isNetwork ? "Connection lost" : e?.isForbidden ? "You don't have access to this" : e?.status === 404 ? "Not found" : "Couldn't load this data";
  return (
    <div className={`flex flex-col items-center justify-center text-center ${compact ? "py-8" : "py-14"}`} role="alert">
      <div className="mb-3 text-bad">{icon}</div>
      <p className="text-sm font-semibold text-ink">{title}</p>
      <p className="mt-1 max-w-sm text-[13px] text-muted">{(error as Error)?.message ?? "Unknown error"}</p>
      {onRetry && !e?.isForbidden && e?.status !== 404 && (
        <Button className="mt-4" size="sm" icon={<ArrowClockwiseIcon size={14} />} onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

/** Renders the loading state, error with retry, empty state, or the content. */
export function QueryView<T>({
  query,
  loading,
  empty,
  isEmpty,
  children,
  compact,
}: {
  query: { data: T | undefined; isLoading: boolean; error: unknown; refetch: () => unknown };
  loading: ReactNode;
  empty?: ReactNode;
  isEmpty?: (d: T) => boolean;
  children: (d: T) => ReactNode;
  compact?: boolean;
}) {
  if (query.isLoading) return <>{loading}</>;
  if (query.error && query.data === undefined) return <ErrorState error={query.error} onRetry={() => query.refetch()} compact={compact} />;
  if (query.data === undefined) return <>{loading}</>;
  if (isEmpty && empty && isEmpty(query.data)) return <>{empty}</>;
  return <>{children(query.data)}</>;
}
