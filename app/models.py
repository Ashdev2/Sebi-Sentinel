from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", "series", name="uq_daily_price_stock_date_series"),
        Index("ix_daily_prices_stock_date", "stock_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    series: Mapped[str] = mapped_column(String(10), default="EQ")

    previous_close: Mapped[float | None] = mapped_column(Float)
    open_price: Mapped[float | None] = mapped_column(Float)
    high_price: Mapped[float | None] = mapped_column(Float)
    low_price: Mapped[float | None] = mapped_column(Float)
    last_price: Mapped[float | None] = mapped_column(Float)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    vwap: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    turnover: Mapped[float | None] = mapped_column(Float)
    number_of_trades: Mapped[int | None] = mapped_column(BigInteger)
    delivery_quantity: Mapped[int | None] = mapped_column(BigInteger)
    delivery_percentage: Mapped[float | None] = mapped_column(Float)
    source_file: Mapped[str | None] = mapped_column(String(500))

    stock = relationship("Stock")


class DailyAnalysis(Base):
    __tablename__ = "daily_analysis"
    __table_args__ = (
        UniqueConstraint("daily_price_id", name="uq_analysis_daily_price"),
        Index("ix_daily_analysis_risk_score", "risk_score"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    daily_price_id: Mapped[int] = mapped_column(ForeignKey("daily_prices.id"), nullable=False)

    return_1d: Mapped[float | None] = mapped_column(Float)
    return_5d: Mapped[float | None] = mapped_column(Float)
    volume_ratio_20d: Mapped[float | None] = mapped_column(Float)
    volume_zscore_20d: Mapped[float | None] = mapped_column(Float)
    volatility_20d: Mapped[float | None] = mapped_column(Float)
    vwap_deviation_pct: Mapped[float | None] = mapped_column(Float)
    trades_ratio_20d: Mapped[float | None] = mapped_column(Float)
    delivery_ratio_20d: Mapped[float | None] = mapped_column(Float)

    anomaly_score: Mapped[float] = mapped_column(Float, default=0)
    rule_score: Mapped[float] = mapped_column(Float, default=0)
    case_score: Mapped[float] = mapped_column(Float, default=0)
    risk_score: Mapped[float] = mapped_column(Float, default=0)
    risk_level: Mapped[str] = mapped_column(String(30), default="VERY LOW")
    suspected_pattern: Mapped[str] = mapped_column(String(150), default="No Strong Manipulation Pattern")
    reasons: Mapped[list] = mapped_column(JSONB, default=list)


class RegulatoryCase(Base):
    __tablename__ = "regulatory_cases"
    __table_args__ = (
        Index("ix_regulatory_cases_stock_dates", "stock_id", "period_start", "period_end"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    regulator: Mapped[str] = mapped_column(String(50), default="SEBI")
    case_title: Mapped[str] = mapped_column(String(500))
    order_date: Mapped[date | None] = mapped_column(Date)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    violation: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(String(255))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True)


class GeneratedReport(Base):
    __tablename__ = "generated_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    risk_score: Mapped[float] = mapped_column(Float)
    file_path: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
