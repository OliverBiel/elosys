import { Skeleton } from "@/components/skeleton";

export default function Loading() {
  return (
    <div className="flex flex-col gap-8">
      <section>
        <Skeleton className="h-7 w-96 max-w-full" />
        <Skeleton className="mt-4 h-4 w-full max-w-2xl" />
        <div className="mt-5 flex flex-wrap items-center gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-40" style={{ borderRadius: "var(--r-md)" }} />
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-32" style={{ borderRadius: "var(--r-md)" }} />
          ))}
        </div>
      </section>

      <div className="kpis">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="kpi">
            <Skeleton className="h-2.5 w-2/3" />
            <Skeleton className="mt-2 h-5 w-1/2" />
          </div>
        ))}
      </div>

      <section className="table-wrap">
        <div className="p-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 border-b border-[var(--border-1)] py-3 last:border-0">
              <Skeleton className="h-3.5 w-20" />
              <Skeleton className="h-3.5 flex-1" />
              <Skeleton className="h-3.5 w-24" />
              <Skeleton className="h-3.5 w-24" />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
