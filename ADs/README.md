# Decisões de Arquitetura (ADs)

Este diretório registra as decisões de arquitetura do Elosys. O princípio que
atravessa todas elas é **não repúdio da informação**: qualquer dado exibido tem
que ser rastreável até uma fonte pública e oficial, de forma que um terceiro
consiga refazer o caminho e chegar ao mesmo dado bruto.

Não repúdio aqui significa três garantias:

1. **Origem** — de qual órgão / URL / arquivo veio cada dado.
2. **Integridade** — o `manifest.json` commitado registra o hash de cada arquivo
   de entrada; `elosys verify` re-baixa e confere.
3. **Reprodutibilidade** — o banco é um artefato de build rewrite-only: dado o
   manifesto e o commit do código, qualquer um reconstrói o mesmo `.db`.

## Índice

| AD | Assunto | Status |
| --- | --- | --- |
| [banco.md](banco.md) | Banco de dados: SQLite (arquivo único), idioma e convenções de tipo | Aceita |
| [confiabilidade.md](confiabilidade.md) | Proveniência: cadeia `source → collection → parse → record` | Aceita |
| [politician.md](politician.md) | `politician_history`, `campaign_org` (CNPJ de campanha), `social_media` (redes declaradas), CPF mascarado de 2024 | Aceita |
| [imutabilidade.md](imutabilidade.md) | Build reprodutível (rewrite-only) e âncora no git | Aceita |
| [dados_derivados.md](dados_derivados.md) | Proveniência de correlações e regras de detecção; primeira regra: `disproportionate_expense` | Aceita |
| [identidade.md](identidade.md) | Tabela mestra `people` e resolução de identidade | Aceita |
| [votacoes.md](votacoes.md) | Votações nominais da Câmara e do Senado: identidade, exercício, orientação, significado do voto | Aceita |

## Formato

Cada AD tem: **Contexto**, **Decisão**, **Consequências** e **Pontos em aberto**.
Pontos em aberto marcados com `⚠️` precisam de discussão antes de virar código.

**Idioma:** o texto das ADs é em português; nomes de tabela, coluna e identificadores
de código são em inglês (ver [banco.md](banco.md) §2), para casar com o código em
`elosys/`.
