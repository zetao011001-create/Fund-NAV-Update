"""Initialize the database schema and seed the 21 tracked funds."""

from __future__ import annotations

from src.database import DatabaseManager
from src.logger import log

PRESET_FUNDS: list[dict[str, str | None]] = [
    {"fund_name": "恒如全天候守心2号", "manager": "恒如", "strategy": "套利+增强", "category": "套利"},
    {"fund_name": "恒如全天候守心1号", "manager": "恒如", "strategy": "套利+增强", "category": "套利"},
    {"fund_name": "恒如守心6号", "manager": "恒如", "strategy": "套利+增强", "category": "套利"},
    {"fund_name": "恒如守正2号", "manager": "恒如", "strategy": "套利", "category": "套利"},
    {"fund_name": "恒如套利三号", "manager": "恒如", "strategy": "低波混合套利", "category": "套利"},
    {"fund_name": "衡盛55号B类", "manager": "平方和", "strategy": "1800中性+股指高频", "category": "量化"},
    {"fund_name": "衡盛31号", "manager": "平方和", "strategy": "1801中性+股指高频", "category": "量化"},
    {"fund_name": "光年中证1000指数增强3号", "manager": "太衍", "strategy": "1000指增", "category": "指增"},
    {"fund_name": "多空对冲2号", "manager": "磐松", "strategy": "量化多空", "category": "量化"},
    {"fund_name": "多策略2号", "manager": "杨湜", "strategy": "多策略", "category": "多策略"},
    {"fund_name": "多策略稳健1号", "manager": "杨湜", "strategy": "低波多策略", "category": "多策略"},
    {"fund_name": "明世伙伴胜杯12号", "manager": "杨湜", "strategy": "宏观多策略中波", "category": "多策略"},
    {"fund_name": "明世伙伴胜杯23号1期", "manager": "杨湜", "strategy": "宏观多策略中高波", "category": "多策略"},
    {"fund_name": "宽辅臻好精选1号", "manager": "宽辅", "strategy": None, "category": None},
    {"fund_name": "思贤专享中性2号", "manager": "宽辅", "strategy": None, "category": None},
    {"fund_name": "正仁春晓中性", "manager": "正仁", "strategy": None, "category": None},
    {"fund_name": "正仁多资产一号", "manager": "正仁", "strategy": "多资产", "category": "多资产"},
    {"fund_name": "正仁择时量选听涛二号", "manager": "正仁", "strategy": None, "category": None},
    {"fund_name": "正仁择时量化选股一期", "manager": "正仁", "strategy": None, "category": None},
    {"fund_name": "正仁股票择时一期", "manager": "正仁", "strategy": None, "category": None},
    {"fund_name": "梧桐1号", "manager": "思梵", "strategy": None, "category": None},
]


def main() -> None:
    db = DatabaseManager()
    db.init_db()
    for row in PRESET_FUNDS:
        fid = db.add_fund(
            fund_name=row["fund_name"],
            manager=row["manager"],
            strategy=row["strategy"],
            category=row["category"],
            is_tracking=True,
            is_auto_registered=False,
        )
        log.info(f"Upserted fund id={fid} name={row['fund_name']}")
    summary = db.get_status_summary()
    log.info(f"Init complete. Status={summary}")
    print("\n=== INIT SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
