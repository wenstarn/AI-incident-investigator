from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from order_service.models import OrderModel, ProductModel


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_products(self, limit: int, offset: int) -> list[ProductModel]:
        statement = (
            select(ProductModel).order_by(ProductModel.id).limit(limit).offset(offset)
        )
        return list(self._session.scalars(statement))

    def get_product(self, product_id: int) -> ProductModel | None:
        return self._session.get(ProductModel, product_id)

    def decrement_stock(self, product_id: int, quantity: int) -> bool:
        statement = (
            update(ProductModel)
            .where(ProductModel.id == product_id, ProductModel.stock >= quantity)
            .values(stock=ProductModel.stock - quantity)
            .returning(ProductModel.id)
        )
        return self._session.scalar(statement) is not None

    def add_order(self, order: OrderModel) -> OrderModel:
        self._session.add(order)
        self._session.flush()
        self._session.refresh(order)
        return order

    def get_order(self, order_id: int) -> OrderModel | None:
        return self._session.get(OrderModel, order_id)

    def list_orders(
        self, user_id: int | None, sort_order: str, limit: int
    ) -> list[OrderModel]:
        statement: Select[tuple[OrderModel]] = select(OrderModel)
        if user_id is not None:
            statement = statement.where(OrderModel.user_id == user_id)
        ordering = (
            OrderModel.created_at.asc()
            if sort_order == "asc"
            else OrderModel.created_at.desc()
        )
        id_ordering = (
            OrderModel.id.asc() if sort_order == "asc" else OrderModel.id.desc()
        )
        statement = statement.order_by(ordering, id_ordering).limit(limit)
        return list(self._session.scalars(statement))
