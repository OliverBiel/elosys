import Link from "next/link";
import type { Provenance } from "@/lib/queries";
import type { CamaraVotePresence, MemberRef } from "@/lib/votes-queries";
import { DEFINITIONS } from "@/lib/vote-meaning";
import { SourceZone } from "@/components/source-zone";
import { SourcesList } from "./sources-list";

function MemberList({ members }: { members: MemberRef[] }) {
  return (
    <ul className="mt-3 grid gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2 lg:grid-cols-3">
      {members.map((m) => (
        <li key={m.memberId}>
          <Link href={`/votacoes/parlamentares/camara/${m.memberId}`} className="hover:underline">
            {m.name ?? `código ${m.memberId}`}
          </Link>
          <span className="mono ml-1.5 text-[11px]" style={{ color: "var(--muted-2)" }}>
            {[m.lastParty, m.state].filter(Boolean).join("/") || "—"}
          </span>
          {m.personId != null ? (
            <Link href={`/politico/${m.personId}`} className="link-primary mono ml-1.5 text-[10.5px]">
              perfil
            </Link>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function Group({
  title, hint, members, children,
}: {
  /** [singular, plural] */
  title: [string, string];
  hint: string;
  members: MemberRef[];
  children?: React.ReactNode;
}) {
  return (
    <details className="card" style={{ padding: "14px 18px" }}>
      <summary className="cursor-pointer text-[14px]">
        <span className="mono mr-2" style={{ color: "var(--fg-1)" }}>{members.length.toLocaleString("pt-BR")}</span>
        {members.length === 1 ? title[0] : title[1]}
      </summary>
      <p className="mt-2 text-[12px] leading-relaxed" style={{ color: "var(--muted-2)" }}>{hint}</p>
      {members.length > 0 ? <MemberList members={members} /> : null}
      {children ? <div className="mt-3">{children}</div> : null}
    </details>
  );
}

/** Câmara only. Presence is per SESSION (eventosPresencaDeputados), not per vote. */
export function VotePresence({
  presence, provenance,
}: {
  presence: CamaraVotePresence;
  provenance: Provenance | null;
}) {
  const hasList = presence.eventId != null && presence.presenceListed > 0;
  return (
    <SourceZone provenance={provenance}>
      <div className="flex flex-col gap-3">
        {hasList ? (
          <p className="text-[13px]" style={{ color: "var(--muted)" }}>
            <span className="mono" style={{ color: "var(--fg-1)" }}>
              {presence.presenceListed.toLocaleString("pt-BR")}
            </span>{" "}
            deputados registraram presença na sessão. A presença é registrada uma vez por sessão, e
            uma sessão pode ter várias votações: estar na lista não quer dizer que a pessoa estava no
            plenário na hora desta votação.
          </p>
        ) : (
          <p className="text-[13px]" style={{ color: "var(--muted)" }}>
            A lista de presença desta sessão não está disponível. Sem ela, ninguém é apontado como
            ausente: quem estava em exercício e não votou fica como indeterminado.
          </p>
        )}
        {hasList ? (
          <Group
            title={[
              "presente na sessão que não votou nesta votação",
              "presentes na sessão que não votaram nesta votação",
            ]}
            hint="Registraram presença na sessão, mas não têm voto nesta votação. Não é falta."
            members={presence.sessionPresentNotVoted}
          />
        ) : null}
        {presence.termsAvailable ? (
          <>
            <Group
              title={["ausente", "ausentes"]}
              hint={
                "Em exercício do mandato na hora da votação, fora da lista de presença da sessão " +
                "e sem voto registrado."
              }
              members={presence.absent}
            >
              <SourcesList
                sources={presence.termSources}
                label="fontes dos períodos de exercício do mandato (ausentes e indeterminados)"
              />
            </Group>
            {presence.indeterminate.length > 0 ? (
              <Group
                title={[
                  presence.indeterminateLabel ?? "indeterminado",
                  presence.indeterminateLabel ?? "indeterminados",
                ]}
                hint={
                  presence.indeterminateReason === "no_presence_list"
                    ? "Não votaram e não dá para afirmar ausência: a sessão não tem lista de presença."
                    : "Não votaram e não dá para afirmar ausência: a situação do mandato mudou nesse " +
                      "dia (posse, licença, saída)."
                }
                members={presence.indeterminate}
              />
            ) : null}
          </>
        ) : (
          <p className="text-[12px]" style={{ color: "var(--muted-2)" }}>
            Ausências não avaliadas: os períodos de exercício do mandato ainda não foram coletados.
          </p>
        )}
        <p className="max-w-3xl text-[11.5px] leading-relaxed" style={{ color: "var(--muted-2)" }}>
          {DEFINITIONS.presenceCamara}
        </p>
      </div>
    </SourceZone>
  );
}
