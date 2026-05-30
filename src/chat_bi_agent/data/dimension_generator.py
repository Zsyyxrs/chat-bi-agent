"""Dimension table generators: dim_branch, dim_customer, dim_product, dim_account, dim_date."""

import random
from datetime import date, timedelta
from typing import Generator
from faker import Faker

fake = Faker("zh_CN")
fake_en = Faker("en_US")


class DimensionGenerator:
    """Generate dimension table rows with realistic banking distributions."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        random.seed(seed)
        Faker.seed(seed)

    # ============================================================
    # dim_branch
    # ============================================================

    def generate_branches(self, count: int = 50) -> Generator[dict, None, None]:
        """
        Generate branch dimension data.
        Hierarchy: HEAD -> PROVINCEs -> CITYs -> SUBBRANCHes
        """
        regions = ["华东", "华北", "华南", "华中", "西南", "西北", "东北"]
        provinces = {
            "华东": ["浙江", "江苏", "江西", "上海"],
            "华北": ["北京", "天津", "河北", "山西"],
            "华南": ["广东", "广西", "福建", "海南"],
            "华中": ["湖北", "湖南", "河南"],
            "西南": ["四川", "重庆", "贵州", "云南"],
            "西北": ["陕西", "甘肃", "新疆", "宁夏"],
            "东北": ["辽宁", "吉林", "黑龙江"],
        }
        cities = {
            "浙江": ["杭州", "宁波", "温州"],
            "江苏": ["南京", "苏州", "无锡"],
            "上海": ["浦东", "黄浦", "静安"],
            "北京": ["朝阳", "海淀", "东城"],
            "广东": ["广州", "深圳", "佛山"],
        }

        branch_counter = 0
        # HEAD
        yield {
            "branch_id": "BR_HEAD_00",
            "branch_name": "总行",
            "branch_level": "HEAD",
            "parent_branch_id": None,
            "region": None,
            "province": None,
            "city": None,
            "open_date": date(2010, 1, 1),
            "is_active": True,
        }
        branch_counter += 1
        head_id = "BR_HEAD_00"

        # PROVINCE level (one per region)
        province_ids = {}
        for region in regions:
            for province in provinces.get(region, []):
                province_id = f"BR_PROV_{branch_counter:04d}"
                province_ids[province] = province_id
                yield {
                    "branch_id": province_id,
                    "branch_name": f"{province}分行",
                    "branch_level": "PROVINCE",
                    "parent_branch_id": head_id,
                    "region": region,
                    "province": province,
                    "city": None,
                    "open_date": date(2015, 1, 1),
                    "is_active": True,
                }
                branch_counter += 1

        # CITY level
        city_ids = {}
        for province, city_list in cities.items():
            prov_id = province_ids.get(province)
            if not prov_id:
                continue
            for city in city_list:
                city_id = f"BR_CITY_{branch_counter:04d}"
                city_ids[city] = city_id
                yield {
                    "branch_id": city_id,
                    "branch_name": f"{city}分行",
                    "branch_level": "CITY",
                    "parent_branch_id": prov_id,
                    "region": None,
                    "province": province,
                    "city": city,
                    "open_date": date(2018, 1, 1),
                    "is_active": True,
                }
                branch_counter += 1

        # SUBBRANCH level
        subbranch_names = ["营业部", "营销中心", "小微部", "财富部"]
        for city, city_id in list(city_ids.items())[:min(10, len(city_ids))]:
            for i, sb_name in enumerate(subbranch_names):
                sb_id = f"BR_SUB_{branch_counter:04d}"
                yield {
                    "branch_id": sb_id,
                    "branch_name": f"{city}{sb_name}",
                    "branch_level": "SUBBRANCH",
                    "parent_branch_id": city_id,
                    "region": None,
                    "province": None,
                    "city": city,
                    "open_date": date(2020, 1, 1),
                    "is_active": True,
                }
                branch_counter += 1
                if branch_counter >= count:
                    return

    # ============================================================
    # dim_customer
    # ============================================================

    def generate_customers(
        self, branch_ids: list[str], count: int = 10000
    ) -> Generator[dict, None, None]:
        """Generate customer dimension data with realistic tiers."""
        tiers = ["HIGH_NET_WORTH", "AFFLUENT", "MASS", "BASIC"]
        risk_appetites = ["C1", "C2", "C3", "C4", "C5"]

        for i in range(count):
            cust_id = f"CUST_{i:06d}"
            tier_dist = random.choices(tiers, weights=[2, 8, 35, 55])[0]
            open_year = random.randint(2015, 2026)
            birth_date = fake.date_of_birth(minimum_age=20, maximum_age=70)
            age = (date.today() - birth_date).days // 365

            yield {
                "customer_id": cust_id,
                "customer_name": fake.name(),
                "id_no_masked": f"{fake_en.numerify('####')}****{fake_en.numerify('####')}",
                "gender": random.choice(["M", "F", "U"]),
                "birth_date": birth_date,
                "age": age,
                "customer_tier": tier_dist,
                "risk_appetite": random.choice(risk_appetites),
                "open_date": date(open_year, random.randint(1, 12), random.randint(1, 28)),
                "branch_id": random.choice(branch_ids),
                "customer_manager_id": f"MGR_{random.randint(1, 200):04d}",
                "aum": round(random.expovariate(1 / 100000), 2),
                "is_active": random.choices([True, False], weights=[85, 15])[0],
            }

    # ============================================================
    # dim_product
    # ============================================================

    def generate_products(self, count: int = 100) -> Generator[dict, None, None]:
        """Generate product dimension data."""
        categories = {
            "DEPOSIT": ["活期存款", "定期存款", "大额存单"],
            "LOAN": ["个人贷款", "房屋贷款", "小微贷款"],
            "WEALTH": ["短期理财", "长期理财", "保证收益"],
            "FUND": ["股票基金", "债券基金", "混合基金"],
            "INSURANCE": ["人寿保险", "财产保险", "健康保险"],
            "CARD": ["借记卡", "信用卡"],
        }

        risk_levels = ["R1", "R2", "R3", "R4", "R5"]
        prod_id = 0

        for category, subcats in categories.items():
            for subcat in subcats:
                for i in range(max(1, count // len(categories) // len(subcats))):
                    product_id = f"PROD_{category[0:3]}_{prod_id:04d}"
                    term_days = None
                    if category == "WEALTH":
                        term_days = random.choice([30, 90, 180, 360])
                    elif category == "DEPOSIT":
                        term_days = random.choice([None, 30, 90, 180, 360])

                    yield {
                        "product_id": product_id,
                        "product_name": f"{subcat}产品{i+1}",
                        "product_category": category,
                        "product_subcategory": subcat,
                        "risk_level": random.choice(risk_levels),
                        "term_days": term_days,
                        "expected_return_rate": round(random.uniform(0.01, 0.06), 4),
                        "min_amount": random.choice([100, 1000, 5000, 10000]),
                        "currency": "CNY",
                        "launch_date": date(2020, 1, 1),
                        "expire_date": None,
                        "is_active": random.choices([True, False], weights=[80, 20])[0],
                    }
                    prod_id += 1

    # ============================================================
    # dim_account
    # ============================================================

    def generate_accounts(
        self, customer_ids: list[str], product_ids: list[str], branch_ids: list[str], count: int = 20000
    ) -> Generator[dict, None, None]:
        """Generate account dimension data."""
        account_types = ["CURRENT", "SAVING", "LOAN", "CARD", "INVESTMENT"]
        statuses = ["ACTIVE", "FROZEN", "CLOSED", "DORMANT"]

        for i in range(count):
            account_id = f"622202{i:010d}"
            acct_type = random.choice(account_types)
            customer_id = random.choice(customer_ids)
            product_id = random.choice(product_ids) if acct_type != "CURRENT" else None
            open_year = random.randint(2015, 2026)

            status = random.choices(
                statuses, weights=[70, 10, 15, 5]
            )[0]

            yield {
                "account_id": account_id,
                "customer_id": customer_id,
                "account_type": acct_type,
                "account_subtype": f"{acct_type}_subtype_{random.randint(1, 5)}",
                "currency": "CNY",
                "product_id": product_id,
                "branch_id": random.choice(branch_ids),
                "open_date": date(open_year, random.randint(1, 12), random.randint(1, 28)),
                "close_date": None if status != "CLOSED" else date(open_year + 1, 1, 1),
                "status": status,
            }

    # ============================================================
    # dim_date
    # ============================================================

    def generate_dates(
        self, start_date: date = date(2025, 1, 1), end_date: date = date(2026, 12, 31)
    ) -> Generator[dict, None, None]:
        """Generate date dimension table."""
        # China holidays (simplified)
        holidays = {
            (1, 1): "元旦",
            (2, 15): "春节",
            (2, 16): "春节",
            (2, 17): "春节",
            (2, 18): "春节",
            (2, 19): "春节",
            (2, 20): "春节",
            (2, 21): "春节",
            (2, 22): "春节",
            (2, 23): "春节",
            (4, 4): "清明节",
            (5, 1): "劳动节",
            (5, 2): "劳动节",
            (5, 3): "劳动节",
            (6, 19): "端午节",
            (9, 25): "中秋节",
            (10, 1): "国庆节",
            (10, 2): "国庆节",
            (10, 3): "国庆节",
            (10, 4): "国庆节",
            (10, 5): "国庆节",
            (10, 6): "国庆节",
        }

        current = start_date
        while current <= end_date:
            date_id = int(current.strftime("%Y%m%d"))
            is_month_end = (current + timedelta(days=1)).month != current.month
            is_quarter_end = current.month in [3, 6, 9, 12] and is_month_end
            is_year_end = current.month == 12 and is_month_end

            holiday_name = holidays.get((current.month, current.day))
            is_holiday = holiday_name is not None or current.weekday() >= 5

            fiscal_quarter = f"{current.year}Q{(current.month - 1) // 3 + 1}"

            yield {
                "date_id": date_id,
                "full_date": current,
                "year": current.year,
                "quarter": (current.month - 1) // 3 + 1,
                "month": current.month,
                "day": current.day,
                "week_of_year": current.isocalendar()[1],
                "day_of_week": current.weekday() + 1,
                "is_weekend": current.weekday() >= 5,
                "is_holiday": is_holiday,
                "holiday_name": holiday_name,
                "is_month_end": is_month_end,
                "is_quarter_end": is_quarter_end,
                "is_year_end": is_year_end,
                "fiscal_quarter": fiscal_quarter,
            }

            current += timedelta(days=1)
