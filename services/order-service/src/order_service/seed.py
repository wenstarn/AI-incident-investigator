from decimal import Decimal

from sqlalchemy import select

from order_service.database import get_session_factory
from order_service.models import ProductModel

PRODUCTS = [
    ("Laptop", Decimal("1299.00"), 10),
    ("Smartphone", Decimal("799.00"), 20),
    ("Headphones", Decimal("149.90"), 30),
    ("Keyboard", Decimal("89.50"), 25),
    ("Mouse", Decimal("49.90"), 40),
    ("Monitor", Decimal("349.00"), 15),
    ("Webcam", Decimal("99.00"), 18),
    ("USB-C Hub", Decimal("69.90"), 35),
    ("External SSD", Decimal("119.00"), 22),
    ("Laptop Stand", Decimal("59.00"), 28),
]


def seed_products() -> int:
    with get_session_factory()() as session:
        existing_names = set(session.scalars(select(ProductModel.name)))
        new_products = [
            ProductModel(name=name, price=price, stock=stock)
            for name, price, stock in PRODUCTS
            if name not in existing_names
        ]
        session.add_all(new_products)
        session.commit()
        return len(new_products)


def main() -> None:
    created = seed_products()
    print(f"Created {created} products")


if __name__ == "__main__":
    main()
