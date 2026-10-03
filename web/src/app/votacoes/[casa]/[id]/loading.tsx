import { Skeleton } from "@/components/skeleton";

export default function Loading() {
  return (
    <div className="flex flex-col gap-10">
      <header>
        <Skeleton className="h-3 w-32" />
        <Skeleton className="mt-4 h-3 w-80" />
        <Skeleton className="mt-3 h-8 w-72" />
        <Skeleton className="mt-4 h-4 w-full max-w-3xl" />
        <Skeleton className="mt-1.5 h-4 w-2/3 max-w-3xl" />
      </header>

      <section className="grid gap-4 md:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="card">
            <Skeleton className="h-3 w-48" />
            <Skeleton className="mt-4 h-4 w-full" />
            <Skeleton className="mt-1.5 h-4 w-5/6" />
            <Skeleton className="mt-1.5 h-4 w-2/3" />
          </div>
        ))}
      </section>

      <section>
        <Skeleton className="h-5 w-48" />
        <div className="kpis mt-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="kpi">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="mt-2 h-6 w-20" />
            </div>
          ))}
        </div>
      </section>

      {Array.from({ length: 2 }).map((_, s) => (
        <section key={s}>
          <Skeleton className="h-5 w-56" />
          <div className="table-wrap mt-4">
            <div className="p-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 border-b border-[var(--border-1)] py-3 last:border-0">
                  <Skeleton className="h-3.5 w-24" />
                  <Skeleton className="h-3.5 flex-1" />
                  <Skeleton className="h-3.5 w-16" />
                  <Skeleton className="h-3.5 w-16" />
                </div>
              ))}
            </div>
          </div>
        </section>
      ))}
    </div>
  );
}
