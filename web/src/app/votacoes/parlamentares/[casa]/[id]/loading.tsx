import { Skeleton } from "@/components/skeleton";

export default function Loading() {
  return (
    <main className="mx-auto w-full max-w-5xl pt-8">
      <header className="pb-8">
        <div className="flex items-center gap-4">
          <Skeleton className="h-[72px] w-[72px] flex-none" style={{ borderRadius: "999px" }} />
          <Skeleton className="h-9 w-80" />
        </div>
        <Skeleton className="mt-3 h-4 w-72" />
        <Skeleton className="mt-4 h-7 w-56" style={{ borderRadius: "var(--r-md)" }} />
      </header>

      <section className="py-6">
        <Skeleton className="mb-4 h-3 w-24" />
        <div className="kpis">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="kpi">
              <Skeleton className="h-2.5 w-2/3" />
              <Skeleton className="mt-2 h-5 w-4/5" />
              <Skeleton className="mt-1.5 h-2.5 w-1/2" />
            </div>
          ))}
        </div>
      </section>

      <section className="py-6">
        <Skeleton className="mb-4 h-3 w-40" />
        <div className="table-wrap">
          <div className="p-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="mb-3 h-3.5 w-full last:mb-0" />
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
