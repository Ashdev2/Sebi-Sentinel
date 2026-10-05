from datetime import date

from sqlalchemy import or_, select

from app.database import SessionLocal
from app.models import RegulatoryCase, Stock


def find_cases(symbol: str, start_date: date, end_date: date):
    db = SessionLocal()
    try:
        stock = db.scalar(select(Stock).where(Stock.symbol == symbol.upper()))
        if stock is None:
            return []

        cases = db.scalars(
            select(RegulatoryCase)
            .where(
                RegulatoryCase.stock_id == stock.id,
                or_(
                    RegulatoryCase.period_start.is_(None),
                    RegulatoryCase.period_end.is_(None),
                    (RegulatoryCase.period_start <= end_date) & (RegulatoryCase.period_end >= start_date),
                ),
            )
            .order_by(RegulatoryCase.order_date.desc().nullslast())
        ).all()

        return cases
    finally:
        db.close()
