from sqlalchemy import func, select

from app.models import Stock


def resolve_stock(db, identifier: str):
    value = identifier.strip()
    if not value:
        return None
    stock = db.scalar(select(Stock).where(Stock.symbol == value.upper()))
    if stock is not None:
        return stock
    stock = db.scalar(select(Stock).where(func.lower(Stock.company_name) == value.lower()))
    if stock is not None:
        return stock
    matches = db.scalars(
        select(Stock).where(Stock.company_name.ilike(f"%{value}%")).order_by(Stock.company_name).limit(2)
    ).all()
    return matches[0] if len(matches) == 1 else None
