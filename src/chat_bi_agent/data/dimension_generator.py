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
        # 顺序与编号锚定到评估 YAML：BR_CITY_0000=杭州, BR_CITY_0002=南京, BR_CITY_0006=浦东(上海)
        cities = {
            "浙江": ["杭州", "宁波"],
            "江苏": ["南京", "苏州"],
            "河北": ["石家庄", "唐山"],
            "上海": ["浦东", "黄浦", "静安"],
            "北京": ["朝阳", "海淀", "东城"],
            "广东": ["广州", "深圳", "佛山"],
        }

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
        head_id = "BR_HEAD_00"
        emitted = 1

        # PROVINCE level (one per region) — counter 独立，从 0 开始
        prov_counter = 0
        province_ids = {}
        for region in regions:
            for province in provinces.get(region, []):
                province_id = f"BR_PROV_{prov_counter:04d}"
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
                prov_counter += 1
                emitted += 1

        # CITY level — counter 独立从 0；cities 字典顺序决定 BR_CITY_NNNN 编号
        city_counter = 0
        city_ids = {}
        for province, city_list in cities.items():
            prov_id = province_ids.get(province)
            if not prov_id:
                continue
            for city in city_list:
                city_id = f"BR_CITY_{city_counter:04d}"
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
                city_counter += 1
                emitted += 1

        # SUBBRANCH level — counter 独立从 0
        sub_counter = 0
        subbranch_names = ["营业部", "营销中心", "小微部", "财富部"]
        for city, city_id in list(city_ids.items())[: min(10, len(city_ids))]:
            for sb_name in subbranch_names:
                sb_id = f"BR_SUB_{sub_counter:04d}"
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
                sub_counter += 1
                emitted += 1
                if emitted >= count:
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

    # 产品风险等级按 (category, subcategory) 的业务合理范围。
    # DEPOSIT/LOAN/CARD = R1（存贷卡无投资风险）；
    # WEALTH/FUND/INSURANCE 按子类别波动。
    _PRODUCT_RISK_LEVEL_RULES: dict[tuple[str, str], list[str]] = {
        ("DEPOSIT", "活期存款"): ["R1"],
        ("DEPOSIT", "定期存款"): ["R1"],
        ("DEPOSIT", "大额存单"): ["R1"],
        ("LOAN", "个人贷款"): ["R1"],
        ("LOAN", "房屋贷款"): ["R1"],
        ("LOAN", "小微贷款"): ["R1"],
        ("WEALTH", "短期理财"): ["R2", "R3"],
        ("WEALTH", "长期理财"): ["R3", "R4"],
        ("WEALTH", "保证收益"): ["R2"],
        ("FUND", "股票基金"): ["R4", "R5"],
        ("FUND", "债券基金"): ["R2", "R3"],
        ("FUND", "混合基金"): ["R3", "R4"],
        ("INSURANCE", "人寿保险"): ["R2", "R3"],
        ("INSURANCE", "财产保险"): ["R1"],
        ("INSURANCE", "健康保险"): ["R1", "R2"],
        ("CARD", "借记卡"): ["R1"],
        ("CARD", "信用卡"): ["R1"],
    }

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

        prod_id = 0

        for category, subcats in categories.items():
            for subcat in subcats:
                risk_pool = self._PRODUCT_RISK_LEVEL_RULES.get((category, subcat), ["R1"])
                for i in range(max(1, count // len(categories) // len(subcats))):
                    product_id = f"PROD_{category[0:3]}_{prod_id:04d}"
                    term_days = None
                    if category == "WEALTH":
                        term_days = random.choice([30, 90, 180, 360])
                    elif category == "DEPOSIT":
                        term_days = random.choice([None, 30, 90, 180, 360])

                    yield {
                        "product_id": product_id,
                        "product_name": f"{subcat}产品{i + 1}",
                        "product_category": category,
                        "product_subcategory": subcat,
                        "risk_level": random.choice(risk_pool),
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

    # account_type → list of (product_category, allowed_subcategories) rules.
    # `allowed_subcategories=None` means any subcategory in that category is OK.
    # DEPOSIT 拆开：活期存款挂 CURRENT，定期/大额存单挂 SAVING。
    _ACCOUNT_TYPE_TO_PRODUCT_CATEGORIES: dict[str, list[tuple[str, set[str] | None]]] = {
        "CURRENT": [("DEPOSIT", {"活期存款"})],
        "SAVING": [("DEPOSIT", {"定期存款", "大额存单"})],
        "LOAN": [("LOAN", None)],
        "CARD": [("CARD", None)],
        "INVESTMENT": [("WEALTH", None), ("FUND", None), ("INSURANCE", None)],
    }

    # account_subtype 业务真实枚举（替代原 f"{type}_subtype_N" 占位）。
    _ACCOUNT_SUBTYPES: dict[str, list[str]] = {
        "CURRENT": ["个人活期", "对公活期", "代发工资", "二类户"],
        "SAVING": ["三个月定期", "半年定期", "一年定期", "三年定期", "大额存单"],
        "LOAN": ["消费贷", "房贷", "车贷", "经营贷", "信用贷"],
        "CARD": ["普卡", "金卡", "白金卡"],
        "INVESTMENT": ["公募基金", "私募基金", "银行理财", "保险产品"],
    }

    def generate_accounts(
        self,
        customers: list[dict],
        products: list[dict],
        count: int = 20000,
    ) -> Generator[dict, None, None]:
        """Generate account dimension data.

        约束：
        - account.product_id 的 (类别,子类别) 必须符合 account_type 的白名单
          （CURRENT↔活期存款 / SAVING↔定期·大额存单 / LOAN↔LOAN 等）
        - account.branch_id 沿用所属客户的开户分行
        - account.open_date 不早于客户 open_date
        """
        account_types = ["CURRENT", "SAVING", "LOAN", "CARD", "INVESTMENT"]
        statuses = ["ACTIVE", "FROZEN", "CLOSED", "DORMANT"]

        # 按 (category, subcategory) 分桶，按 account_type 预算候选池。
        products_by_cat_subcat: dict[tuple[str, str], list[str]] = {}
        for p in products:
            key = (p["product_category"], p["product_subcategory"])
            products_by_cat_subcat.setdefault(key, []).append(p["product_id"])

        products_for_type: dict[str, list[str]] = {}
        for atype, rules in self._ACCOUNT_TYPE_TO_PRODUCT_CATEGORIES.items():
            pool: list[str] = []
            for cat, allowed_subcats in rules:
                for (c, s), pids in products_by_cat_subcat.items():
                    if c == cat and (allowed_subcats is None or s in allowed_subcats):
                        pool.extend(pids)
            products_for_type[atype] = pool

        # 风险等级合规：客户 Cn 只能买 Rm，要求 m <= n。
        # 预算 (account_type, risk_appetite) → 合规候选池。
        product_risk_index = {p["product_id"]: p["risk_level"] for p in products}
        risk_filtered_pools: dict[tuple[str, str], list[str]] = {}
        for atype, pool in products_for_type.items():
            for appetite in ("C1", "C2", "C3", "C4", "C5"):
                max_r = int(appetite[1])
                risk_filtered_pools[(atype, appetite)] = [
                    pid for pid in pool if int(product_risk_index[pid][1]) <= max_r
                ]

        for i in range(count):
            account_id = f"622202{i:010d}"
            acct_type = random.choice(account_types)
            customer = random.choice(customers)
            appetite = customer["risk_appetite"]

            # 优先用风险合规池；为空时（如 C1 客户开 INVESTMENT 账户但无 R1 产品）
            # 回落到不带风险约束的 acct_type 完整池，保证账户类型一致性优先。
            compliant_pool = risk_filtered_pools.get((acct_type, appetite)) or []
            pool = compliant_pool or products_for_type.get(acct_type) or []
            product_id = random.choice(pool) if pool else None

            cust_open = customer["open_date"]
            open_year = random.randint(cust_open.year, 2026)
            if open_year == cust_open.year:
                open_month = random.randint(cust_open.month, 12)
                if open_month == cust_open.month:
                    open_day = random.randint(cust_open.day, 28)
                else:
                    open_day = random.randint(1, 28)
            else:
                open_month = random.randint(1, 12)
                open_day = random.randint(1, 28)
            open_date_val = date(open_year, open_month, open_day)

            status = random.choices(statuses, weights=[70, 10, 15, 5])[0]

            yield {
                "account_id": account_id,
                "customer_id": customer["customer_id"],
                "account_type": acct_type,
                "account_subtype": random.choice(
                    self._ACCOUNT_SUBTYPES.get(acct_type) or ["GENERAL"]
                ),
                "currency": "CNY",
                "product_id": product_id,
                "branch_id": customer["branch_id"],
                "open_date": open_date_val,
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
