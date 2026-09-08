# bot-rolezin — CLAUDE.md

Este arquivo é a fonte de verdade do projeto. O Claude Code deve seguir
exatamente o que está definido aqui. Não decidir arquitetura, nomes,
bibliotecas ou fluxo por conta própria — se algo não estiver especificado
neste documento, PARAR e perguntar antes de implementar, em vez de
assumir/inventar.

## 1. Objetivo do projeto

Bot de WhatsApp que ajuda a organizar a divisão de conta de um rolê em
grupo. O organizador fala com o bot no privado, informa valor total e
número de pessoas (passo a passo, guiado pelo bot), recebe um QR
Code/PIX copia-e-cola + um **código curto do rolê** pra colar no grupo
manualmente. Cada participante manda o comprovante de pagamento pro bot
no privado **incluindo esse código na legenda da foto**. O bot faz OCR
no comprovante, associa ao rolê certo pelo código e contabiliza quem
pagou. O organizador consulta o placar quando quiser e **só ele**
decide quando encerrar — encerrar apaga os dados daquele rolê do banco.

## 2. Restrições inegociáveis (não implementar nada disso)

- **Não custodiar dinheiro.** O bot nunca recebe, guarda nem repassa
  valores. Ele só gera o payload PIX (chave do próprio organizador) e lê
  comprovantes. Nenhuma integração com PSP (Asaas, EFI, Mercado Pago etc).
- **Não tentar ler mensagens de grupo comum do WhatsApp.** A API oficial
  (Cloud API) não permite isso — só suporta 1:1 e uma Groups API própria
  limitada a 8 participantes por convite. Todo o fluxo é 1:1 entre o
  usuário (organizador ou participante) e o bot.
- **Não fingir certeza sobre pagamento.** O OCR é um indício, não uma
  prova bancária. O status "pago" no sistema deve deixar claro que é
  "comprovante recebido e conferido por OCR", não "confirmado pelo banco".
- **O bot nunca inicia uma conversa.** Toda interação começa com uma
  mensagem do usuário (organizador ou participante) — o bot só responde
  dentro da janela de 24h da WhatsApp Cloud API. Mensagem
  *business-initiated* fora dessa janela exige template aprovado pela
  Meta e é cobrada. Não implementar lembrete ou notificação proativa
  (ex: avisar sozinho que "fulano pagou" sem o organizador ter
  perguntado antes).
- **Não encerrar rolê automaticamente.** Só o organizador, por comando
  explícito, decide quando o rolê acaba — mesmo que todo mundo já
  tenha confirmado pagamento. Isso é inegociável porque encerrar apaga
  dados (seção 6.7); fazer isso sozinho, sem o organizador pedir, seria
  destrutivo.

## 3. Stack (fixa — não trocar nem sugerir alternativa sem pedir)

- Python 3.12
- FastAPI (API/webhook)
- SQLAlchemy 2.0 (ORM, estilo declarative + typed)
- Alembic (migrations — toda mudança de schema precisa de migration, nunca
  alterar tabela na mão)
- Pydantic v2 (schemas de entrada/saída, separados dos modelos ORM)
- PostgreSQL (banco)
- httpx (client HTTP assíncrono, usado pra chamar a WhatsApp Cloud API)
- pytesseract + Pillow (OCR do comprovante — idioma `por` do Tesseract
  precisa estar instalado na imagem Docker)
- qrcode (geração da imagem do QR Code a partir do payload BR Code)
- pytest + pytest-asyncio (testes)
- Docker + docker-compose (ambiente local: app + Postgres)

Não adicionar nenhuma outra dependência sem perguntar antes. Em
particular: nada de parser de linguagem natural / LLM externo pra
interpretar as respostas do usuário — o parsing é determinístico (ver
seção 6.3). Nada de soft-delete/auditoria — encerrar é `DELETE` de
verdade (ver seção 6.7).

## 4. Estrutura de pastas (seguir exatamente)

```
bot-rolezin/
  app/
    api/
      webhook.py             # único endpoint público: POST /webhook (WhatsApp)
    core/
      config.py              # variáveis de ambiente (pydantic-settings)
      logging.py
      parsing.py             # parse de valor em formato BR ("150,50" / "150.50" / "150")
      exceptions.py           # exceptions de domínio
    db/
      base.py                 # engine, session factory
      models.py                # Role, Participant, ConversationState (ver seção 5)
    schemas/
      role.py                  # Pydantic: criação/leitura de Role
      participant.py            # Pydantic: participante e status
    services/
      pix.py                    # geração do payload/QR PIX (BR Code)
      ocr.py                     # extração de valor/data do comprovante
      whatsapp_client.py         # envio de mensagem/imagem via Cloud API
      role_service.py            # regra de negócio pura: criar rolê, calcular
                                  # valor/pessoa, registrar pagamento, encerrar
                                  # (com apagamento em cascata)
      conversation_service.py    # state machine da conversa (ver seção 6):
                                  # decide próxima pergunta, interpreta resposta,
                                  # aciona role_service quando os dados estão completos
      interfaces.py               # Protocols: WhatsAppClient, OcrExtractor, PixGenerator
    repositories/
      role_repository.py
      participant_repository.py
      conversation_repository.py
  alembic/
  tests/
    test_pix.py
    test_ocr.py
    test_role_service.py
    test_conversation_service.py
  docker-compose.yml
  Dockerfile
  .env.example
  requirements.txt
```

Regra de camadas: `api/` só recebe requisição, faz o roteamento descrito
na seção 6.1 e chama `services/`. `services/` contém a regra de negócio
e chama `repositories/`. `repositories/` é o único lugar que fala com o
banco via SQLAlchemy. Nunca colocar lógica de negócio dentro do router
nem query direta fora do repository.

## 5. Modelo de dados (schema fixo — não improvisar campo novo sem avisar)

**Role** (uma sessão de rolê)
- `id` (UUID, PK)
- `organizer_phone` (str, indexado)
- `total_value` (Decimal)
- `num_people` (int)
- `value_per_person` (Decimal, calculado — ver 6.4 pra regra de arredondamento)
- `role_code` (str(4), indexado — ver 6.2 pra geração; unicidade
  garantida entre os rolês que existem no banco no momento — como
  encerrar apaga a linha, basta checar contra os rolês atualmente
  existentes, não precisa filtrar por status)
- `pix_key` (str)
- `pix_key_type` (enum: cpf, cnpj, email, telefone, aleatoria)
- `created_at` (datetime)

Sem campo `status`: a existência da linha já significa "aberto". Não
existe estado "encerrado" persistido — encerrar é apagar (seção 6.7).

**Participant**
- `id` (UUID, PK)
- `role_id` (FK -> Role, `ON DELETE CASCADE` — apagar o Role remove os
  participantes junto, automaticamente pelo banco)
- `phone` (str)
- `status` (enum: pendente, comprovante_enviado, confirmado)
- `receipt_image_url` (str, nullable)
- `extracted_value` (Decimal, nullable — valor lido pelo OCR)
- `confirmed_at` (datetime, nullable)
- Constraint única: (`role_id`, `phone`) — reenvio de comprovante pelo
  mesmo participante no mesmo rolê **atualiza** a linha existente, não
  cria uma nova.

**ConversationState** (estado da conversa passo-a-passo, por telefone)
- `phone` (str, PK)
- `step` (enum: aguardando_valor, aguardando_num_pessoas,
  aguardando_confirmacao_encerramento)
- `pending_total_value` (Decimal, nullable — guarda o valor já
  informado enquanto aguarda o nº de pessoas)
- `pending_role_id` (UUID, nullable, FK -> Role — guarda qual rolê está
  prestes a ser apagado enquanto aguarda confirmação de encerramento)
- `updated_at` (datetime)

Não existe schema Pydantic pra `ConversationState` — é estado interno,
nunca exposto pela API.

## 6. Fluxo (não alterar a ordem/regras sem avisar)

### 6.1 Roteamento no webhook (ordem de prioridade — checar nessa ordem)

Toda mensagem recebida em `POST /webhook` passa por esta ordem de
verificação até a primeira que casar:

1. **Mensagem com imagem anexada E legenda contém um código de rolê
   válido** (regex do formato da seção 6.2) → fluxo de participante:
   `role_service.registrar_pagamento`.
2. **Mensagem com imagem anexada SEM código válido na legenda** → bot
   responde pedindo pra reenviar a foto com o código do rolê na legenda
   (não processa OCR sem saber o rolê).
3. **Telefone tem `ConversationState.step ==
   aguardando_confirmacao_encerramento`** → ver seção 6.7 (confirma ou
   cancela o encerramento, dependendo do conteúdo da mensagem).
4. **Telefone tem uma `ConversationState` pendente** (`aguardando_valor`
   ou `aguardando_num_pessoas`) → `conversation_service` interpreta a
   mensagem como resposta e avança o passo (seção 6.3).
5. **Mensagem de texto contém uma palavra-chave de comando reconhecida**
   (case-insensitive): `"pago"`, `"status"`, `"quem pagou"` → bot busca
   o rolê mais recente com aquele `organizer_phone` e responde o
   placar atual (seção 6.6).
6. **Mensagem de texto contém `"encerrar"`** (e nenhum dos casos acima
   casou) → inicia o fluxo de confirmação de encerramento (seção 6.7).
7. **Nenhum dos casos acima** (primeira mensagem, sem estado pendente,
   sem comando reconhecido) → início do fluxo de criação de rolê:
   `conversation_service` cria uma `ConversationState(step=aguardando_valor)`
   e o bot pergunta o valor total.

### 6.2 Código do rolê

- Formato: 4 caracteres maiúsculos, alfabeto `A-Z` + `2-9` **excluindo**
  `I`, `O`, `0`, `1` (evita confusão visual ao digitar/ler a legenda).
- Gerado aleatoriamente na criação do rolê. Antes de inserir, o service
  verifica se já existe um `Role` com esse código; se existir (colisão
  rara, já que rolês encerrados não ficam no banco), gera outro (retry).
- Extração da legenda: regex busca um token isolado de 4 caracteres
  desse alfabeto, case-insensitive, normalizado pra maiúsculo antes de
  comparar.
- Um código que não corresponde a nenhum `Role` existente (porque nunca
  existiu ou porque o rolê já foi encerrado/apagado) é tratado como
  inválido — cai no caso 2 da seção 6.1.

### 6.3 Fluxo de criação do rolê (passo a passo)

1. Organizador manda **qualquer mensagem de texto** pro bot (sem estado
   pendente, sem comando reconhecido) → bot cria `ConversationState`
   e pergunta o valor total da conta.
2. Organizador responde com o valor. `app/core/parsing.py` interpreta
   formato BR (aceita vírgula ou ponto como separador decimal, ex:
   `"150,50"`, `"150.50"`, `"150"`). Se não conseguir parsear ou o
   valor for `<= 0`, bot responde com erro e **permanece no mesmo
   passo** (não avança, não perde o estado). Se ok, salva em
   `pending_total_value`, avança `step = aguardando_num_pessoas` e
   pergunta o número de pessoas.
3. Organizador responde com o número de pessoas. Deve ser um inteiro
   `>= 2`. Erro de parsing ou valor inválido → mesma regra do passo 2
   (repergunta, mantém estado). Se ok, chama
   `role_service.criar_role(total_value, num_people, organizer_phone)`.
4. `role_service.criar_role`: calcula `value_per_person` (seção 6.4),
   gera `role_code` (seção 6.2), chama `pix.py` pra gerar payload BR
   Code + imagem do QR, cria o `Role`, **apaga** a `ConversationState`
   daquele telefone.
5. Bot devolve pro organizador, em uma única mensagem:
   - Valor por pessoa + imagem do QR + copia-e-cola PIX + o código do
     rolê + instrução ("peça pra cada pessoa mandar o comprovante pra
     mim com o código `XXXX` na legenda da foto").
   - **Aviso 1**: "Eu não aviso quando alguém paga — me manda 'quem
     pagou' sempre que quiser conferir o placar."
   - **Aviso 2**: "Quando você mandar 'encerrar', eu apago os dados
     desse rolê (comprovantes e status) pra sempre — só encerra depois
     de já ter conferido tudo que precisava."

### 6.4 Cálculo do valor por pessoa

- `value_per_person = round(total_value / num_people, 2)` usando
  arredondamento `ROUND_HALF_UP` (padrão financeiro BR).
- A soma dos valores individuais pode não bater centavo a centavo com
  `total_value` por causa do arredondamento — aceitável pro MVP, não é
  motivo pra lógica de ajuste automático de centavo.

### 6.5 Fluxo de recebimento de comprovante

1. Participante manda foto com legenda contendo o código do rolê
   (roteamento seção 6.1, caso 1).
2. `ocr.py` extrai valor e data da imagem. Se não conseguir extrair
   nenhum valor, `extracted_value = None`.
3. `role_service.registrar_pagamento(role_id, phone, image, extracted_value)`:
   - Cria o `Participant` se não existir `(role_id, phone)`, ou
     atualiza o existente (reenvio sobrescreve).
   - Se `extracted_value` for `None` → `status = comprovante_enviado`
     (fica pendente de conferência manual pelo organizador).
   - Se `abs(extracted_value - role.value_per_person) <= Decimal("0.01")`
     → `status = confirmado`, seta `confirmed_at`.
   - Caso contrário (valor não bate) → `status = comprovante_enviado`
     (indício de valor errado, organizador confere no placar).
4. Bot responde ao participante confirmando o recebimento (com o status
   atribuído, deixando claro que é "conferido por OCR", nunca "confirmado
   pelo banco"). **Não** há encerramento automático nem aviso ao
   organizador nesse passo — ver restrição da seção 2.

### 6.6 Consulta de status ("quem já pagou")

- Roteamento seção 6.1, caso 5. Busca o `Role` mais recente com
  `organizer_phone = <telefone de quem perguntou>`.
- Se não achar nenhum → bot avisa que não há rolê ativo daquele
  organizador.
- Resposta: contagem de confirmados / total, lista de quem está
  `pendente` ou `comprovante_enviado` (pra o organizador cobrar
  manualmente no grupo, já que o bot não pode).

### 6.7 Encerramento (só manual, com confirmação, e apaga o banco)

1. Organizador manda `"encerrar"` sem confirmação pendente (roteamento
   seção 6.1, caso 6). Bot busca o `Role` mais recente com aquele
   `organizer_phone`.
   - Não achou nenhum → avisa que não há rolê ativo, não cria estado
     nenhum.
   - Achou → cria `ConversationState(step=aguardando_confirmacao_encerramento,
     pending_role_id=role.id)` e responde com **o placar final** (igual
     ao da seção 6.6) seguido do aviso: "Manda 'encerrar' de novo pra
     confirmar — isso apaga os comprovantes e o status desse rolê pra
     sempre e não tem volta."
2. Nova mensagem chega com `ConversationState.step ==
   aguardando_confirmacao_encerramento` (roteamento seção 6.1, caso 3):
   - Se a mensagem contém `"encerrar"` (case-insensitive) →
     `role_service.confirmar_encerramento(pending_role_id)`: apaga o
     `Role` (o `ON DELETE CASCADE` remove os `Participant` junto),
     apaga a `ConversationState`. Bot responde confirmando que o rolê
     foi encerrado e apagado.
   - Qualquer outra mensagem → **cancela**: apaga só a
     `ConversationState` (não mexe no `Role`), bot responde "encerramento
     cancelado, o rolê continua aberto do jeito que estava." A mensagem
     em si não é reprocessada como novo comando nesse mesmo turno.

## 7. Variáveis de ambiente (.env.example deve conter exatamente estas)

```
DATABASE_URL=
WHATSAPP_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=
```

## 8. Convenções de código

- snake_case em tudo (arquivos, funções, variáveis).
- Type hints obrigatórios em toda função pública.
- Schemas Pydantic nunca são reaproveitados como modelo ORM e vice-versa —
  sempre há uma função explícita de conversão.
- Toda função de `services/` tem docstring de uma linha explicando a
  regra de negócio.
- Erros de domínio (ex: rolê não encontrado, código inválido) são
  exceptions customizadas em `app/core/exceptions.py`, tratadas por um
  exception handler central no FastAPI — não usar `HTTPException` solta
  dentro de `services/`.
- Constantes de negócio (tolerância de conferência do OCR = `Decimal("0.01")`,
  alfabeto e tamanho do `role_code`, mínimo de `num_people = 2`) ficam
  centralizadas em `app/core/config.py` ou como constantes no topo do
  módulo que as usa — nunca "mágicas" espalhadas pelo código.
- Operação de apagar (`confirmar_encerramento`) roda numa única
  transação (apaga `Role`, cascade cuida do resto, commit) — nunca
  apagar em passos separados sem transação.

## 9. Boas práticas de design (obrigatório, não opcional)

- **Baixo acoplamento entre camadas.** `services/` nunca importa
  `SQLAlchemy` diretamente nem monta query — só fala com
  `repositories/` através de uma interface (`typing.Protocol` ou ABC).
  Isso permite trocar a implementação (ou mockar em teste) sem tocar na
  regra de negócio.
- **Inversão de dependência nos serviços externos.** `whatsapp_client`,
  `ocr` e `pix` devem ter uma interface abstrata (`Protocol`) definida em
  `services/interfaces.py`, com a implementação concreta separada. Os
  `services/` de negócio dependem da interface, não da implementação —
  assim dá pra testar `role_service`/`conversation_service` com um fake
  de OCR/WhatsApp, sem chamar Tesseract ou a Cloud API de verdade.
- **Injeção de dependência explícita**, via `Depends` do FastAPI —
  nunca instanciar repository/client dentro da função de serviço.
- **Responsabilidade única.** `conversation_service` só decide *qual é
  o próximo passo* e interpreta a resposta bruta do usuário (incluindo
  a confirmação de encerramento); `role_service` só contém regra de
  negócio pura (calcular, criar, registrar pagamento, apagar). Nenhum
  dos dois formata payload de webhook nem fala com o SQLAlchemy
  diretamente.
- **Sem dependência circular entre camadas.** O fluxo é sempre
  `api -> services -> repositories -> db`. Nunca o contrário, e nunca
  `repositories` chamando `services`.
- **Schemas isolam a API do domínio.** Os schemas Pydantic (`schemas/`)
  não devem vazar campo interno do modelo ORM que não faça sentido pra
  quem consome a API — são contratos, não espelho do banco.
- **Testes unitários de `services/` não podem precisar de banco real
  nem de rede real** — devem rodar 100% com fakes/mocks das interfaces
  acima. Testes que precisam de banco/rede de verdade são testes de
  integração e ficam separados (`tests/integration/`), não misturados
  com os unitários. Incluir teste explícito garantindo que
  `confirmar_encerramento` remove Role e Participants juntos, e que uma
  mensagem qualquer diferente de "encerrar" durante a confirmação
  cancela sem apagar nada.

## 10. Regras de trabalho para o Claude Code (eficiência de token)

- Este arquivo é o plano. Antes de codar uma feature, localizar a seção
  correspondente aqui — não reexplicar o projeto inteiro a cada tarefa.
- Fazer mudanças pequenas e incrementais: editar só o que muda, nunca
  reescrever um arquivo inteiro se um trecho resolve.
- Não introduzir abstração, camada ou padrão de projeto não listado
  neste documento (ex: nada de Repository genérico, nada de CQRS, nada
  de event bus, nada de fila/worker assíncrono pra OCR, nada de
  soft-delete) — o escopo é deliberadamente simples e síncrono.
- Não adicionar funcionalidade que não está no fluxo da seção 6 (e
  especialmente nada de notificação proativa ou encerramento automático
  — ver restrições da seção 2). Se parecer uma boa ideia, sugerir antes
  de implementar, não implementar direto.
- Ao terminar uma parte, rodar os testes relevantes daquela parte, não a
  suíte inteira, a menos que peçam explicitamente.
- Se uma instrução do usuário conflitar com este documento, avisar a
  contradição antes de agir — não silenciosamente ignorar o CLAUDE.md.

## 11. Git flow

Remote: `https://github.com/cm0420/BotRole.git`.

- **Trunk-based simplificado, sem GitFlow completo.** Não existe branch
  `develop`, `release` nem `hotfix` — projeto solo em MVP, essas
  branches só adicionariam cerimônia sem benefício. `main` é o único
  tronco e deve estar sempre no estado mais avançado que já passou nos
  testes relevantes.
- **Nunca commitar direto na `main`**, exceto o commit inicial de
  scaffolding (estrutura de pastas vazia, `.gitignore`, este arquivo).
  Toda feature/fix nasce em branch própria.
- **Nomenclatura de branch**, prefixo pelo tipo + slug curto em inglês,
  de preferência mapeando pra uma seção/serviço deste documento:
  `feat/<slug>` (ex: `feat/pix-service`, `feat/conversation-state-machine`),
  `fix/<slug>`, `chore/<slug>` (setup, deps, docker, ci), `docs/<slug>`,
  `test/<slug>`.
- **Um Pull Request por branch no GitHub**, mesmo sendo desenvolvimento
  solo — abrir o PR e rodar a skill `/code-review` nele antes de
  mergear, funcionando como self-review. Não mergear PR com teste
  quebrado.
- **Merge por squash** — a `main` recebe 1 commit por feature/fix; o
  histórico granular fica só na branch (que pode ser deletada depois do
  merge).
- **Commits em Conventional Commits, em inglês**, mesmo o resto da
  documentação estando em PT-BR: `feat:`, `fix:`, `test:`, `chore:`,
  `docs:`, `refactor:`. Mensagem curta, no imperativo, descrevendo o
  quê (ex: `feat: add PIX payload generation`).
- **Tags `vX.Y.0`** em marcos que fecham um pedaço coerente do fluxo da
  seção 6 (sugestão de roadmap, ajustar se a ordem real de implementação
  mudar):
  - `v0.1.0` — estrutura de pastas, `db/models.py`, Alembic configurado
    e primeira migration.
  - `v0.2.0` — `services/pix.py` (payload BR Code + QR).
  - `v0.3.0` — `services/ocr.py`.
  - `v0.4.0` — `services/role_service.py` completo (criar, registrar
    pagamento, encerrar com cascade).
  - `v0.5.0` — `services/conversation_service.py` (state machine da
    seção 6.3/6.7).
  - `v0.6.0` — `api/webhook.py` com o roteamento completo da seção 6.1
    — fecha o MVP.
- **Nunca dar push forçado (`--force`) na `main`** nem reescrever
  histórico já publicado no remote, sob nenhuma circunstância.
