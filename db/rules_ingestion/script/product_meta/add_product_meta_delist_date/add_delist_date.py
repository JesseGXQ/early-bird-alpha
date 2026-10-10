"""
为 TIER1 `StaticMeta.delist_date` 变量补写入库脚本。

背景
----
本地 TIER1 规则库中，郑商所有 6 个品种因为"更换代码"而退市，旧代码不再使用：
`WS`(郑麦旧->WH)、`WT`(普麦旧->PM)、`RO`(菜油旧->OI)、`ER`(早稻旧->RI)、
`ME`(甲醇旧->MA)、`TC`(动力煤旧->ZC)。
在 TIER1 YAML 中，可以通过 `name` 字段是否含 "(旧)" 字样来无歧义地识别它们。

字段口径（务必与 `StaticMeta.delist_date` 的 docstring 保持一致）
------------------------------------------------------------
`delist_date` = **该品种最后一个合约的最后交易日**（`InstrumentRegistry.last_trade_date`）。
不是交易所公告的挂牌终止日，也不是最后一个合约的最后交割日（`end_deliv_date`）。
仍在市的品种不写这个键，`ProductMetaManager` 读取时归一为 `None`。

数据来源
--------
直接读取 TIER2 合约注册库 `db/rules/instrument_registry/*.parquet`（全市场 parquet 汇总），
按品种聚合出"最后一个合约"。

之所以不调用 `InstrumentRegistryManager`：
1. 它是饿汉式 eager load，一次性把全市场合约读进内存，而本任务只需要一次轻量聚合；
2. 它只提供单合约查询 API（`get_instrument_registry(contract)`），
   无法直接表达"按品种取交割年月最大的合约"这一聚合语义。

脚本行为
--------
默认 `--dry-run`：只做推导、交叉校验并打印结果，不落盘。
加 `--apply` 才真正改写 `db/rules/product_meta/*.yaml`。

YAML 改写采用**文本行插入**而非 `yaml.safe_load` + `safe_dump` 往返，
因为现有 YAML 里带有 `# -------------------- static Meta --------------------`
这类分节注释，round-trip 会把它们全部丢失。

用法
----
    python db/rules_ingestion/script/product_meta/add_product_meta_delist_date/add_delist_date.py
    python db/rules_ingestion/script/product_meta/add_product_meta_delist_date/add_delist_date.py --apply
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[5]
PRODUCT_META_DIR = ROOT / "db" / "rules" / "product_meta"
INSTRUMENT_REGISTRY_DIR = ROOT / "db" / "rules" / "instrument_registry"
OUT_ROOT = (
    ROOT
    / "db"
    / "rules_ingestion"
    / "data"
    / "derived"
    / "20261009"
    / "product_meta_delist_date"
)

# TIER1 YAML 中识别"因更换代码而退市"的品种的标志：name 字段含 "(旧)"。
# 刻意不用"最后一个合约的最后交易日是否已过期"这类时间启发式——
# 例如 LR(晚稻) 是仍在市的活跃品种，只是注册表里暂时没有更新到 2026 年之后的合约，
# 用时间启发式会把它误判成退市品种。
DELISTED_NAME_MARKER = "(旧)"

# 独立的第二信息源（人工交叉核对用），用于对自动推导结果做断言，防止单一数据源出错。
EXPECTED_LAST_CONTRACT: dict[str, str] = {
    "WS": "WS1305",
    "WT": "WT1211",
    "RO": "RO1305",
    "ER": "ER1305",
    "ME": "ME1505",
    "TC": "TC1604",
}

LIST_DATE_RE = re.compile(r"^list_date\s*:")
DELIST_DATE_RE = re.compile(r"^delist_date\s*:")


@dataclass(frozen=True)
class DelistCandidate:
    """单个退市品种的推导结果。"""

    product: str
    exchange: str
    name: str
    successor: str
    yaml_last_contract_deliv_yyyymm: str
    last_instrument: str
    last_trade_date: date
    end_deliv_date: date | None


def load_registry(registry_dir: Path) -> pd.DataFrame:
    """读取 TIER2 合约注册库的全市场 parquet 汇总。"""
    paths = sorted(registry_dir.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"在 {registry_dir} 下未找到任何合约注册 parquet 文件")
    df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    df["last_trade_date"] = pd.to_datetime(df["last_trade_date"])
    df["product"] = df["product"].astype(str).str.upper()
    df["instrument"] = df["instrument"].astype(str).str.upper()
    return df


def find_delisted_products(meta_dir: Path) -> dict[str, dict[str, Any]]:
    """扫描 TIER1 YAML，返回 name 含 "(旧)" 的品种及其原始内容。"""
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(meta_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if DELISTED_NAME_MARKER in str(raw.get("name", "")):
            found[path.stem.upper()] = raw
    return found


def resolve_last_contract(group: pd.DataFrame, product: str) -> tuple[str, date, date | None]:
    """
    取该品种的最后一个合约，返回 (合约代码, 最后交易日, 最后交割日)。

    用两种口径独立推导并要求一致（交叉校验一），以排除单一排序键带来的偏差：
      A. 按 (delivery_year, delivery_month) 取最大 —— "最后一个合约" 的字面含义；
      B. 按 last_trade_date 取最大 —— "最后一个仍有最后交易日的合约"。
    两者不一致时抛错，转人工复核。
    """
    if group.empty:
        raise ValueError(f"品种 {product} 在 TIER2 合约注册库中没有任何合约记录")

    by_delivery = group.sort_values(["delivery_year", "delivery_month"]).iloc[-1]
    by_last_trade = group.sort_values("last_trade_date").iloc[-1]

    if by_delivery["instrument"] != by_last_trade["instrument"]:
        raise ValueError(
            f"品种 {product} 的最后一个合约无法确定："
            f"按交割年月取到 {by_delivery['instrument']}，"
            f"按最后交易日取到 {by_last_trade['instrument']}。请人工复核。"
        )

    end_deliv = by_delivery["end_deliv_date"]
    return (
        str(by_delivery["instrument"]),
        by_delivery["last_trade_date"].date(),
        None if pd.isna(end_deliv) else pd.to_datetime(end_deliv).date(),
    )


def build_candidates(
    registry_df: pd.DataFrame, delisted: dict[str, dict[str, Any]]
) -> list[DelistCandidate]:
    """对每个退市品种推导 delist_date，并做三重交叉校验。"""
    candidates: list[DelistCandidate] = []
    for product, raw in sorted(delisted.items()):
        group = registry_df[registry_df["product"] == product]
        instrument, last_trade_date, end_deliv_date = resolve_last_contract(group, product)

        # YAML 中已记录的"最后一个合约交割年月"，稍后用于交叉校验 3。
        yaml_yyyymm = str(raw.get("last_contract_deliv_YYYYMM", "")).replace("-", "")
        actual_yyyymm = _yyyymm_of(group, instrument)

        # 交叉校验 2：与独立信息源（人工核对的最后合约代码）比对。
        expected_instrument = EXPECTED_LAST_CONTRACT.get(product)
        if expected_instrument is not None and expected_instrument != instrument:
            raise ValueError(
                f"品种 {product} 的最后合约与人工核对结果不一致："
                f"推导为 {instrument}，核对值为 {expected_instrument}。"
            )

        # 交叉校验 3：与 YAML 中 last_contract_deliv_YYYYMM 记录的交割年月一致。
        if yaml_yyyymm and actual_yyyymm != yaml_yyyymm:
            raise ValueError(
                f"品种 {product} 的最后合约交割年月与 YAML 中 last_contract_deliv_YYYYMM 不一致："
                f"推导为 {actual_yyyymm}，YAML 为 {yaml_yyyymm}。"
            )

        candidates.append(
            DelistCandidate(
                product=product,
                exchange=str(raw.get("exchange", "")),
                name=str(raw.get("name", "")),
                successor=str(raw.get("successor", "")),
                yaml_last_contract_deliv_yyyymm=yaml_yyyymm,
                last_instrument=instrument,
                last_trade_date=last_trade_date,
                end_deliv_date=end_deliv_date,
            )
        )
    return candidates


def _yyyymm_of(group: pd.DataFrame, instrument: str) -> str:
    """返回指定合约的 YYYYMM 交割年月。"""
    row = group[group["instrument"] == instrument].iloc[0]
    return f"{int(row['delivery_year'])}{int(row['delivery_month']):02d}"


def patch_yaml_text(text: str, delist_date: date) -> tuple[str, str]:
    """
    在 TIER1 YAML 的 static meta 段落里插入（或更新）delist_date 行。

    插入锚点是 static meta 段落内的 `list_date:` 行——它与 delist_date 同属品种生命周期字段，
    紧挨着放最自然；同时该行必定位于第一个分节注释之前，不会误插到别处。

    幂等：若已存在 delist_date 行则原地替换。

    Returns
    -------
    (新文本, 动作描述)
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)

    for i, line in enumerate(lines):
        if DELIST_DATE_RE.match(line):
            lines[i] = f"delist_date: {delist_date.isoformat()}"
            return newline.join(lines), "updated"

    for i, line in enumerate(lines):
        if LIST_DATE_RE.match(line):
            lines.insert(i + 1, f"delist_date: {delist_date.isoformat()}")
            return newline.join(lines), "inserted"

    raise ValueError("未在 YAML 中找到 list_date 行，无法确定 delist_date 的插入位置")


def write_report(candidates: list[DelistCandidate], actions: dict[str, str]) -> pd.DataFrame:
    """输出审计 CSV 与审计报告，便于追溯推导过程。"""
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    audit = pd.DataFrame(
        [
            {
                "product": c.product,
                "exchange": c.exchange,
                "name": c.name,
                "successor": c.successor,
                "yaml_last_contract_deliv_YYYYMM": c.yaml_last_contract_deliv_yyyymm,
                "last_instrument": c.last_instrument,
                "last_trade_date": c.last_trade_date.isoformat(),
                "end_deliv_date": c.end_deliv_date.isoformat() if c.end_deliv_date else "",
                "delist_date": c.last_trade_date.isoformat(),
                "yaml_action": actions.get(c.product, ""),
                "source": "db/rules/instrument_registry/*.parquet",
            }
            for c in candidates
        ]
    )
    audit.to_csv(OUT_ROOT / "delist_date_audit.csv", index=False, encoding="utf-8-sig")

    lines = [
        "# StaticMeta.delist_date 补写入库审计",
        "",
        "- 脚本: `db/rules_ingestion/script/product_meta/add_product_meta_delist_date/add_delist_date.py`",
        "- 数据来源: `db/rules/instrument_registry/*.parquet`（TIER2 合约注册库）",
        "- 目标字段: TIER1 YAML 顶层 `delist_date` → `StaticMeta.delist_date`",
        "- 口径: 品种更换代码导致的退市，取该品种**最后一个合约的最后交易日**",
        "",
        "## 口径说明",
        "",
        "- 退市品种的识别方式：TIER1 YAML 中 `name` 字段包含 `(旧)` 字样。",
        "  刻意不使用「最后合约的最后交易日已过期」这类时间启发式——"
        "例如 `LR`(晚稻) 是仍在市的活跃品种，只是注册表暂无 2026 年之后的合约，用时间启发式会误判。",
        "- `delist_date` 取 `last_trade_date`，**不取** `end_deliv_date`（最后交割日）。",
        "- 仍在市的品种不写 `delist_date` 键，`ProductMetaManager` 读取时归一为 `None`。",
        "",
        "## 交叉校验",
        "",
        "- 校验一：按 (delivery_year, delivery_month) 与按 last_trade_date 两种口径独立推导最后合约，要求一致。",
        "- 校验二：推导出的最后合约与人工交叉核对值一致（见下表 expected 列）。",
        "- 校验三：推导出的交割年月与 YAML 中已有的 `last_contract_deliv_YYYYMM` 一致。",
        "",
        "## 推导结果",
        "",
        "| 品种 | 名称 | 后继代码 | 最后合约 | 最后交易日 (delist_date) | 最后交割日 | 人工核对值 | YAML 动作 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in candidates:
        expected = EXPECTED_LAST_CONTRACT.get(c.product, "-")
        action = actions.get(c.product, "-")
        lines.append(
            f"| {c.product} | {c.name} | {c.successor} | {c.last_instrument} | "
            f"{c.last_trade_date.isoformat()} | "
            f"{c.end_deliv_date.isoformat() if c.end_deliv_date else '-'} | {expected} | {action} |"
        )

    lines.extend(["", "## 输出文件", "", "- `delist_date_audit.csv`", ""])
    (OUT_ROOT / "audit_report.md").write_text("\n".join(lines), encoding="utf-8")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正改写 db/rules/product_meta/*.yaml；缺省时只做 dry-run 校验与报告输出",
    )
    args = parser.parse_args()

    registry_df = load_registry(INSTRUMENT_REGISTRY_DIR)
    delisted = find_delisted_products(PRODUCT_META_DIR)

    # 与人工核对的品种清单对齐，防止规则或数据变动导致漏识别 / 多识别。
    if set(delisted) != set(EXPECTED_LAST_CONTRACT):
        raise ValueError(
            f"识别到的退市品种 {sorted(delisted)} 与人工核对清单 "
            f"{sorted(EXPECTED_LAST_CONTRACT)} 不一致，请人工复核。"
        )

    candidates = build_candidates(registry_df, delisted)

    actions: dict[str, str] = {}
    for candidate in candidates:
        path = PRODUCT_META_DIR / f"{candidate.product}.yaml"
        original = path.read_bytes().decode("utf-8")
        patched, action = patch_yaml_text(original, candidate.last_trade_date)

        # 防御性校验：patch 后的文本必须仍能被 yaml 解析，且 delist_date 落到了正确值。
        parsed = yaml.safe_load(patched)
        if str(parsed.get("delist_date")) != candidate.last_trade_date.isoformat():
            raise ValueError(f"{path} 写入后的 delist_date 校验失败：{parsed.get('delist_date')}")

        actions[candidate.product] = action
        if args.apply:
            path.write_bytes(patched.encode("utf-8"))

    audit = write_report(candidates, actions)

    print(f"识别到的退市品种: {sorted(delisted)}")
    print(audit[["product", "last_instrument", "last_trade_date", "end_deliv_date", "yaml_action"]].to_string(index=False))
    print(f"模式: {'APPLY（已写入 db/rules/product_meta）' if args.apply else 'DRY-RUN（未落盘，加 --apply 生效）'}")
    print(f"审计输出: {OUT_ROOT}")


if __name__ == "__main__":
    main()