from sqlalchemy import select
from sqlalchemy.orm import Session

from user_service.models import UserModel


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, user: UserModel) -> UserModel:
        self._session.add(user)
        self._session.flush()
        self._session.refresh(user)
        return user

    def get_by_id(self, user_id: int) -> UserModel | None:
        return self._session.get(UserModel, user_id)

    def get_by_email(self, email: str) -> UserModel | None:
        statement = select(UserModel).where(UserModel.email == email)
        return self._session.scalar(statement)
