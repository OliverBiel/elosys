import type { ParliamentarianSummary, Ratio } from "@/lib/votes-queries";
import {
  DEFINITIONS, indeterminateLabel, voteValueLabel, type House, type IndeterminateReason, type VoteValue,
} from "@/lib/vote-meaning";
import { Definition, governmentRows, lowN, pctText, ratioSub } from "./parliamentarian-shared";

const GRID: React.CSSProperties = {
  gridAutoFlow: "row",
  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
};

// Order of the breakdown chips: positions first, then presence states, as each house stores them.
// Câmara 'indeterminate' is split by reason below; Senado 'unknown' is outside the list.
const ORDER: Record<House, VoteValue[]> = {
  camara: [
    "yes", "no", "obstruction", "abstention", "secret", "presiding",
    "session_present_not_voted", "absent",
  ],
  senado: [
    "yes", "no", "obstruction", "abstention", "secret", "presiding",
    "present_not_voted", "absent_justified", "absent", "other",
  ],
};

const REASONS: IndeterminateReason[] = ["status_change", "no_presence_list"];

/** Votes in scope where the member is listed (Senado 'unknown' = not in a voters-only list). */
export function listedVotes(summary: ParliamentarianSummary): number {
  const { counts } = summary.presence;
  return Object.entries(counts).reduce((a, [k, n]) => (k === "unknown" ? a : a + (n ?? 0)), 0);
}

function breakdownLabel(value: VoteValue, house: House): string {
  if (value === "other") return "Outro código da fonte (ex.: NA, MERC)";
  if (value === "absent" && house === "senado") return "Ausente (NCom)";
  return voteValueLabel(value, house);
}

/** Presided at least 10% of the listed votes: likely the house president (votes only by exception). */
export const presidedOften = (presiding: number, listed: number) => presiding > 0 && presiding * 10 >= listed;

export const PRESIDED_NOTE =
  "Presidiu muitas sessões: o presidente da Casa, pelo regimento, em regra só vota em votação " +
  "secreta ou para desempatar, por isso o % fica baixo.";

/** Percentage greyed when it rests on fewer than MIN_N votes. */
function Pct({ r }: { r: Ratio }) {
  return <span style={{ color: lowN(r) ? "var(--muted-2)" : undefined }}>{pctText(r)}</span>;
}

export function ParliamentarianKpis({ house, summary }: { house: House; summary: ParliamentarianSummary }) {
  const { presence, fidelity } = summary;
  const total = listedVotes(summary);
  const unknown = presence.counts.unknown ?? 0;
  const notVoted = presence.counts.present_not_voted ?? 0;
  const where = house === "camara" ? "em que estava em exercício" : "em que aparece na lista oficial";

  return (
    <div className="flex flex-col gap-4">
      <div className="kpis" style={GRID}>
        <div className="kpi">
          <div className="kpi__label">votou em</div>
          <div className="kpi__value"><Pct r={presence.voted} /></div>
          <div className="kpi__sub">{ratioSub(presence.voted, `votações ${where}`)}</div>
          {presence.presiding > 0 ? (
            <div className="kpi__sub">
              presidiu a sessão em {presence.presiding.toLocaleString("pt-BR")} (fora da conta)
            </div>
          ) : null}
          {presidedOften(presence.presiding, total) ? (
            <div className="kpi__sub" style={{ color: "var(--fg-2)" }}>{PRESIDED_NOTE}</div>
          ) : null}
          <Definition text={DEFINITIONS.voted} />
        </div>
        {house === "camara" ? (
          <div className="kpi">
            <div className="kpi__label">presença na sessão (lista da Câmara)</div>
            {presence.available ? (
              <>
                <div className="kpi__value" style={{ fontSize: "0.85em" }}><Pct r={presence.rate} /></div>
                <div className="kpi__sub">{ratioSub(presence.rate, "votações")}</div>
                <div className="kpi__sub">dado complementar: estar na lista não é votar</div>
              </>
            ) : (
              <>
                <div className="kpi__value">—</div>
                <div className="kpi__sub">ausências não avaliadas (mandatos não coletados)</div>
              </>
            )}
            <Definition text={DEFINITIONS.presenceCamara} />
          </div>
        ) : (
          <div className="kpi">
            <div className="kpi__label">não registrou voto (lista do Senado)</div>
            <div className="kpi__value" style={{ fontSize: "0.85em" }}>{notVoted.toLocaleString("pt-BR")}</div>
            <div className="kpi__sub">
              de {total.toLocaleString("pt-BR")} votações em que aparece na lista
            </div>
            <Definition text={DEFINITIONS.presenceSenado} />
          </div>
        )}
        <div className="kpi">
          <div className="kpi__label">fidelidade ao partido</div>
          <div className="kpi__value"><Pct r={fidelity} /></div>
          <div className="kpi__sub">{ratioSub(fidelity, "votos")}</div>
          <Definition
            text={`${DEFINITIONS.fidelity} ${house === "camara" ? DEFINITIONS.camaraBench : DEFINITIONS.senadoOrientation}`}
          />
        </div>
        {governmentRows(summary.withGovernment).map((g) => (
          <div key={g.id} className="kpi">
            <div className="kpi__label">votou com o {g.label}</div>
            <div className="kpi__value">{g.ratio ? <Pct r={g.ratio} /> : "—"}</div>
            <div className="kpi__sub">
              {g.ratio ? ratioSub(g.ratio, "votos") : "não votou nesse governo neste recorte"}
            </div>
            <Definition text={DEFINITIONS.government} />
          </div>
        ))}
      </div>

      {total > 0 ? (
        <div>
          <div className="label mb-2">
            o que aparece nas {total.toLocaleString("pt-BR")} votações do recorte
          </div>
          <div className="flex flex-wrap gap-1.5">
            {ORDER[house].filter((v) => presence.counts[v]).map((v) => (
              <span key={v} className={`badge${v === "absent" ? " badge--red" : ""}`}>
                {breakdownLabel(v, house)}: {presence.counts[v]!.toLocaleString("pt-BR")}
              </span>
            ))}
            {house === "camara"
              ? REASONS.filter((r) => presence.indeterminateByReason[r] > 0).map((r) => (
                  <span key={r} className="badge">
                    {indeterminateLabel(r)}: {presence.indeterminateByReason[r].toLocaleString("pt-BR")}
                  </span>
                ))
              : null}
          </div>
          {house === "camara" && presence.counts.indeterminate ? (
            <p className="mt-2 max-w-2xl text-[11px] text-[var(--muted-2)]">
              Indeterminado: sem voto, e não dá para afirmar ausência porque a situação do mandato
              mudou naquele dia ou porque a sessão não tem lista de presença. Entra na conta de
              &quot;votou em&quot; (não votou), mas fica fora da presença na sessão.
            </p>
          ) : null}
          {house === "senado" && unknown > 0 ? (
            <p className="mt-2 max-w-2xl text-[11px] text-[var(--muted-2)]">
              <span className="badge mr-1.5">
                {voteValueLabel("unknown", house)}: {unknown.toLocaleString("pt-BR")}
              </span>
              votações que listam só quem votou e em que o senador não aparece (entre o primeiro e o
              último voto dele na legislatura). Nelas a situação dele é desconhecida, nunca ausente,
              e elas ficam fora de &quot;votou em&quot; e da contagem acima.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
