import type { Provenance } from "@/lib/queries";
import { DEFINITIONS, isMeaningCode } from "@/lib/vote-meaning";
import { SourceZone } from "@/components/source-zone";

/**
 * Plain-language meaning next to the official text, verbatim (ADs/votacoes.md §5).
 * The voted item is opening + description; camara_vote.last_filed_* is never shown (§7).
 */
export function VoteMeaningBox({
  meaning, meaningCode, kindBasis, description, openingDescription, provenance,
}: {
  meaning: string;
  meaningCode: string | null;
  kindBasis: string | null;
  description: string | null;
  openingDescription: string | null;
  provenance: Provenance;
}) {
  const known = isMeaningCode(meaningCode);
  return (
    <section className="grid gap-4 md:grid-cols-2">
      <div className="card">
        <div className="label">O que significava votar Sim</div>
        {/* Unknown code: `meaning` is already MEANING_UNAVAILABLE. */}
        <p
          className="mt-3 text-[15px] leading-relaxed"
          style={{ color: known ? "var(--fg-1)" : "var(--muted)" }}
        >
          {meaning}
        </p>
        <p className="mt-4 text-[11.5px] leading-relaxed" style={{ color: "var(--muted-2)" }}>
          {DEFINITIONS.meaning}
          {kindBasis ? (
            <span className="mono ml-1" title="regra que classificou esta votação">
              (regra: {kindBasis})
            </span>
          ) : null}
        </p>
      </div>

      <SourceZone provenance={provenance}>
        <div className="card h-full">
          <div className="label">Descrição oficial</div>
          {openingDescription ? (
            <p className="mt-3 text-[13px] leading-relaxed whitespace-pre-line" style={{ color: "var(--fg-2)" }}>
              <span className="mono mr-1.5 text-[10.5px]" style={{ color: "var(--muted-2)" }}>abertura:</span>
              {openingDescription}
            </p>
          ) : null}
          {description ? (
            <p className="mt-3 text-[13px] leading-relaxed whitespace-pre-line" style={{ color: "var(--fg-1)" }}>
              {openingDescription ? (
                <span className="mono mr-1.5 text-[10.5px]" style={{ color: "var(--muted-2)" }}>descrição:</span>
              ) : null}
              {description}
            </p>
          ) : (
            <p className="mt-3 text-[13px]" style={{ color: "var(--muted)" }}>
              A fonte não publicou descrição para esta votação.
            </p>
          )}
          <p className="mt-4 text-[11.5px]" style={{ color: "var(--muted-2)" }}>
            Texto copiado sem alteração da fonte oficial.
          </p>
        </div>
      </SourceZone>
    </section>
  );
}
