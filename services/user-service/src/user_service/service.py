from typing import Annotated

from fastapi import Depends
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from user_service.database import get_session, log_database_error
from user_service.models import UserModel
from user_service.repository import UserRepository
from user_service.schemas import User, UserCreate


class EmailAlreadyExistsError(Exception):
    """Raised when a user with the same email already exists."""


class UserNotFoundError(Exception):
    """Raised when a user cannot be found by ID."""


class DatabaseOperationError(Exception):
    """Raised when a database operation cannot be completed."""


class UserService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = UserRepository(session)

    def create_user(self, user_data: UserCreate) -> User:
        normalized_email = str(user_data.email).casefold()

        try:
            if self._repository.get_by_email(normalized_email) is not None:
                raise EmailAlreadyExistsError

            user = self._repository.add(
                UserModel(name=user_data.name, email=normalized_email)
            )
            self._session.commit()
        except EmailAlreadyExistsError:
            self._session.rollback()
            raise
        except IntegrityError as error:
            self._session.rollback()
            raise EmailAlreadyExistsError from error
        except SQLAlchemyError as error:
            self._session.rollback()
            log_database_error(error, "create_user")
            raise DatabaseOperationError from error

        return User.model_validate(user)

    def get_user(self, user_id: int) -> User:
        try:
            user = self._repository.get_by_id(user_id)
        except SQLAlchemyError as error:
            log_database_error(error, "get_user")
            raise DatabaseOperationError from error

        if user is None:
            raise UserNotFoundError

        return User.model_validate(user)


def get_user_service(
    session: Annotated[Session, Depends(get_session)],
) -> UserService:
    return UserService(session)
