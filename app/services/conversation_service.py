from dataclasses import dataclass

from app.core.parsing import parse_inteiro, parse_valor_br
from app.db.models import ConversationState, ConversationStep, ParticipantStatus, PixKeyType, Role
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.participant_repository import ParticipantRepository
from app.repositories.role_repository import RoleRepository
from app.services.pix import normalizar_chave
from app.services.role_service import RoleService

_NUM_PESSOAS_MINIMO = 2

_MENU_TIPOS_CHAVE: dict[str, PixKeyType] = {
    "1": PixKeyType.cpf,
    "cpf": PixKeyType.cpf,
    "2": PixKeyType.cnpj,
    "cnpj": PixKeyType.cnpj,
    "3": PixKeyType.email,
    "email": PixKeyType.email,
    "e-mail": PixKeyType.email,
    "4": PixKeyType.telefone,
    "telefone": PixKeyType.telefone,
    "5": PixKeyType.aleatoria,
    "aleatoria": PixKeyType.aleatoria,
    "aleatória": PixKeyType.aleatoria,
    "chave aleatoria": PixKeyType.aleatoria,
    "chave aleatória": PixKeyType.aleatoria,
}

_NOME_TIPO_CHAVE: dict[PixKeyType, str] = {
    PixKeyType.cpf: "CPF",
    PixKeyType.cnpj: "CNPJ",
    PixKeyType.email: "e-mail",
    PixKeyType.telefone: "telefone",
    PixKeyType.aleatoria: "chave aleatória",
}

_MENU_TIPO_CHAVE_TEXTO = "1) CPF\n2) CNPJ\n3) E-mail\n4) Telefone\n5) Chave aleatória"


@dataclass(frozen=True)
class RespostaBot:
    texto: str
    imagem: bytes | None = None


class ConversationService:
    """State machine da conversa: decide o próximo passo e interpreta a resposta bruta (seção 6.3/6.6/6.7)."""

    def __init__(
        self,
        conversation_repository: ConversationRepository,
        role_repository: RoleRepository,
        participant_repository: ParticipantRepository,
        role_service: RoleService,
    ):
        self._conversation_repository = conversation_repository
        self._role_repository = role_repository
        self._participant_repository = participant_repository
        self._role_service = role_service

    def processar_mensagem(self, phone: str, texto: str) -> RespostaBot:
        """Avança o passo pendente do telefone, ou inicia um novo rolê se não houver estado (seção 6.1, casos 3/4/7)."""
        estado = self._conversation_repository.obter_por_telefone(phone)
        if estado is None:
            return self._iniciar_criacao(phone)
        if estado.step == ConversationStep.aguardando_confirmacao_encerramento:
            return self._confirmar_ou_cancelar_encerramento(estado, texto)
        if estado.step == ConversationStep.aguardando_valor:
            return self._passo_valor(estado, texto)
        if estado.step == ConversationStep.aguardando_num_pessoas:
            return self._passo_num_pessoas(estado, texto)
        if estado.step == ConversationStep.aguardando_tipo_chave_pix:
            return self._passo_tipo_chave_pix(estado, texto)
        return self._passo_chave_pix(estado, texto)  # só sobra aguardando_chave_pix

    def _iniciar_criacao(self, phone: str) -> RespostaBot:
        self._conversation_repository.salvar(ConversationState(phone=phone, step=ConversationStep.aguardando_valor))
        self._conversation_repository.commit()
        return RespostaBot("Oi! Qual o valor total da conta?")

    def _passo_valor(self, estado: ConversationState, texto: str) -> RespostaBot:
        valor = parse_valor_br(texto)
        if valor is None or valor <= 0:
            return RespostaBot("Não entendi o valor. Manda só o número, tipo 150,50 ou 150.")
        estado.pending_total_value = valor
        estado.step = ConversationStep.aguardando_num_pessoas
        self._conversation_repository.salvar(estado)
        self._conversation_repository.commit()
        return RespostaBot("Quantas pessoas vão dividir?")

    def _passo_num_pessoas(self, estado: ConversationState, texto: str) -> RespostaBot:
        num_pessoas = parse_inteiro(texto)
        if num_pessoas is None or num_pessoas < _NUM_PESSOAS_MINIMO:
            return RespostaBot(f"Manda um número inteiro de pessoas (mínimo {_NUM_PESSOAS_MINIMO}).")
        estado.pending_num_people = num_pessoas
        estado.step = ConversationStep.aguardando_tipo_chave_pix
        self._conversation_repository.salvar(estado)
        self._conversation_repository.commit()
        return RespostaBot(f"Qual o tipo da sua chave PIX?\n{_MENU_TIPO_CHAVE_TEXTO}")

    def _passo_tipo_chave_pix(self, estado: ConversationState, texto: str) -> RespostaBot:
        tipo = _MENU_TIPOS_CHAVE.get(texto.strip().lower())
        if tipo is None:
            return RespostaBot(f"Não entendi. Responde com o número:\n{_MENU_TIPO_CHAVE_TEXTO}")
        estado.pending_pix_key_type = tipo
        estado.step = ConversationStep.aguardando_chave_pix
        self._conversation_repository.salvar(estado)
        self._conversation_repository.commit()
        return RespostaBot(f"Beleza! Agora me manda sua chave PIX ({_NOME_TIPO_CHAVE[tipo]}).")

    def _passo_chave_pix(self, estado: ConversationState, texto: str) -> RespostaBot:
        tipo = estado.pending_pix_key_type
        try:
            chave = normalizar_chave(texto.strip(), tipo)
        except ValueError:
            return RespostaBot(f"Essa chave não parece válida pro tipo {_NOME_TIPO_CHAVE[tipo]}. Tenta de novo.")

        criado = self._role_service.criar_role(
            total_value=estado.pending_total_value,
            num_people=estado.pending_num_people,
            organizer_phone=estado.phone,
            pix_key=chave,
            pix_key_type=tipo,
        )
        role = criado.role
        texto_resposta = (
            f"Rolê criado! Valor por pessoa: R$ {role.value_per_person:.2f}\n"
            f"Código do rolê: {role.role_code}\n\n"
            f"{criado.pix_payload.copia_e_cola}\n\n"
            f'Peça pra cada pessoa mandar o comprovante pra mim com o código {role.role_code} '
            "na legenda da foto.\n\n"
            'Eu não aviso quando alguém paga — me manda "quem pagou" sempre que quiser conferir '
            "o placar.\n\n"
            'Quando você mandar "encerrar", eu apago os dados desse rolê (comprovantes e status) '
            "pra sempre — só encerra depois de já ter conferido tudo que precisava."
        )
        return RespostaBot(texto_resposta, imagem=criado.pix_payload.qr_code_image)

    def _formatar_placar(self, role: Role) -> str:
        participantes = self._participant_repository.listar_por_role(role.id)
        confirmados = [p for p in participantes if p.status == ParticipantStatus.confirmado]
        pendentes = [p for p in participantes if p.status != ParticipantStatus.confirmado]
        linhas = [f"Placar do rolê {role.role_code}: {len(confirmados)}/{len(participantes)} confirmados."]
        if pendentes:
            linhas.append("Ainda faltam:")
            linhas.extend(f"- {p.phone}" for p in pendentes)
        return "\n".join(linhas)

    def consultar_status(self, organizer_phone: str) -> RespostaBot:
        """Placar do rolê mais recente do organizador (seção 6.1 caso 5 / seção 6.6)."""
        role = self._role_repository.obter_mais_recente_por_organizador(organizer_phone)
        if role is None:
            return RespostaBot("Você não tem nenhum rolê ativo no momento.")
        return RespostaBot(self._formatar_placar(role))

    def iniciar_encerramento(self, organizer_phone: str) -> RespostaBot:
        """Início do fluxo de encerramento (seção 6.1 caso 6 / seção 6.7, passo 1).

        Só é chamado pelo roteador quando o telefone não tem ConversationState
        pendente (caso 4 tem prioridade sobre o caso 6) — por isso cria o
        estado direto, sem checar se já existe um.
        """
        role = self._role_repository.obter_mais_recente_por_organizador(organizer_phone)
        if role is None:
            return RespostaBot("Você não tem nenhum rolê ativo pra encerrar.")
        self._conversation_repository.salvar(
            ConversationState(
                phone=organizer_phone,
                step=ConversationStep.aguardando_confirmacao_encerramento,
                pending_role_id=role.id,
            )
        )
        self._conversation_repository.commit()
        placar = self._formatar_placar(role)
        return RespostaBot(
            f'{placar}\n\nManda "encerrar" de novo pra confirmar — isso apaga os comprovantes e '
            "o status desse rolê pra sempre e não tem volta."
        )

    def _confirmar_ou_cancelar_encerramento(self, estado: ConversationState, texto: str) -> RespostaBot:
        if "encerrar" in texto.strip().lower():
            self._role_service.confirmar_encerramento(estado.pending_role_id)
            return RespostaBot("Rolê encerrado! Apaguei todos os dados desse rolê.")
        self._conversation_repository.apagar(estado)
        self._conversation_repository.commit()
        return RespostaBot("Encerramento cancelado, o rolê continua aberto do jeito que estava.")
