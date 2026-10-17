from collections.abc import Iterator
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from order_service.cache import ProductCache
from order_service.config import get_product_cache_ttl
from order_service.database import get_session, log_database_error
from order_service.effective_config import effective_redis_url
from order_service.models import OrderModel
from order_service.redis_client import create_redis_client, get_redis_client
from order_service.repository import OrderRepository
from order_service.schemas import Order, OrderCreate, Product, SortOrder
from order_service.user_client import UserServiceClient, get_user_service_client


class ProductNotFoundError(Exception):
    pass


class OrderNotFoundError(Exception):
    pass


class InsufficientStockError(Exception):
    pass


class DatabaseOperationError(Exception):
    pass


class OrderService:
    def __init__(
        self,
        session: Session,
        user_client: UserServiceClient,
        product_cache: ProductCache | None = None,
    ) -> None:
        self._session = session
        self._repository = OrderRepository(session)
        self._user_client = user_client
        self._product_cache = product_cache

    def list_products(self, limit: int, offset: int) -> list[Product]:
        try:
            products = self._repository.list_products(limit, offset)
        except SQLAlchemyError as error:
            log_database_error(error, "list_products")
            raise DatabaseOperationError from error
        return [Product.model_validate(product) for product in products]

    def get_product(self, product_id: int) -> Product:
        try:
            product = self._repository.get_product(product_id)
        except SQLAlchemyError as error:
            log_database_error(error, "get_product")
            raise DatabaseOperationError from error
        if product is None:
            raise ProductNotFoundError
        cached = self._product_cache.get(product_id) if self._product_cache else None
        if cached is None:
            if self._product_cache:
                self._product_cache.set(product.id, product.name, product.price)
            return Product.model_validate(product)
        return Product(
            id=product.id,
            name=cached.name,
            price=cached.price,
            stock=product.stock,
        )

    def create_order(self, order_data: OrderCreate) -> Order:
        self._user_client.ensure_user_exists(order_data.user_id)
        try:
            if not self._repository.decrement_stock(
                order_data.product_id, order_data.quantity
            ):
                if self._repository.get_product(order_data.product_id) is None:
                    raise ProductNotFoundError
                raise InsufficientStockError

            order = self._repository.add_order(
                OrderModel(
                    user_id=order_data.user_id,
                    product_id=order_data.product_id,
                    quantity=order_data.quantity,
                    status="created",
                )
            )
            self._session.commit()
        except (ProductNotFoundError, InsufficientStockError):
            self._session.rollback()
            raise
        except SQLAlchemyError as error:
            self._session.rollback()
            log_database_error(error, "create_order")
            raise DatabaseOperationError from error
        return Order.model_validate(order)

    def get_order(self, order_id: int) -> Order:
        try:
            order = self._repository.get_order(order_id)
        except SQLAlchemyError as error:
            log_database_error(error, "get_order")
            raise DatabaseOperationError from error
        if order is None:
            raise OrderNotFoundError
        return Order.model_validate(order)

    def list_orders(
        self, user_id: int | None, sort_order: SortOrder, limit: int
    ) -> list[Order]:
        try:
            orders = self._repository.list_orders(user_id, sort_order, limit)
        except SQLAlchemyError as error:
            log_database_error(error, "list_orders")
            raise DatabaseOperationError from error
        return [Order.model_validate(order) for order in orders]


def get_order_service(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    user_client: Annotated[UserServiceClient, Depends(get_user_service_client)],
) -> Iterator[OrderService]:
    fault = request.app.state.fault_registry.get_fault_by_type("redis_url_override")
    url = effective_redis_url(request)
    redis_client = create_redis_client(url) if fault else get_redis_client()
    try:
        target = "redis"
        if fault:
            parsed = urlsplit(url)
            target = f"{parsed.hostname}:{parsed.port}"
        cache = ProductCache(redis_client, get_product_cache_ttl(), target=target)
        yield OrderService(session, user_client, cache)
    finally:
        if fault:
            redis_client.close()
