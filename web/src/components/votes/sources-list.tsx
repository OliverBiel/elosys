import type { Provenance } from "@/lib/queries";
import { SourceZone } from "@/components/source-zone";

/** Folded list of the files behind an aggregate, one SourceZone per file. */
export function SourcesList({
  sources, label = "fontes", open = false,
}: {
  sources: Provenance[];
  label?: string;
  open?: boolean;
}) {
  if (sources.length === 0) return null;
  return (
    <details className="text-[11px]" style={{ color: "var(--muted-2)" }} open={open}>
      <summary className="cursor-pointer select-none">
        {label} ({sources.length.toLocaleString("pt-BR")} {sources.length === 1 ? "arquivo" : "arquivos"})
      </summary>
      <ul className="mono mt-2 flex flex-col gap-1">
        {sources.map((p) => (
          <li key={`${p.url}|${p.sha256}|${p.parserName}`}>
            <SourceZone provenance={p} inline>
              <span>
                {p.sourceName} — <span className="break-all">{p.url}</span>
              </span>
            </SourceZone>
          </li>
        ))}
      </ul>
    </details>
  );
}
