import uuid


class DominioError(Exception):
    """Classe base das exceptions de domínio, tratadas por um handler central no FastAPI."""


class RoleNaoEncontradoError(DominioError):
    def __init__(self, role_id: uuid.UUID):
        super().__init__(f"rolê {role_id} não encontrado")
        self.role_id = role_id
