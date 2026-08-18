"""
品种分类元数据管理器。

Responsibility:
    从 db/rules/product_classification/classification.csv 读取品种分类元数据并
    返回为 pandas DataFrame，供因子模块中性化、归因分析和品种组过滤使用。

Classification hierarchy:
    asset_class -> sector -> subsector -> supply_chain

Usage:
    >>> mgr = ProductClassificationManager("db/rules/product_classification")
    >>> df = mgr.get_classification()
    >>> df[df["asset_class"] == "ferrous_metal"]
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal
import pandas as pd


class ProductClassificationManager:
    """
    管理期货品种的分类元数据。

    Parameters
    ----------
    classification_dir : str
        classification.csv 所在目录的路径。
    """

    _COLUMNS = ["product", "exchange", "product_name", "asset_class", "sector", "subsector", "supply_chain"]

    def __init__(self, classification_dir: str):
        self._dir = Path(classification_dir)
        self._csv_path = self._dir / "classification.csv"
        self._df: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._df is None:
            if not self._csv_path.exists():
                raise FileNotFoundError(f"品种分类文件不存在: {self._csv_path}")
            self._df = pd.read_csv(self._csv_path, dtype=str)
        return self._df

    def get_classification(self) -> pd.DataFrame:
        """
        Returns
        -------
        pd.DataFrame
            列: product, exchange, product_name, asset_class, sector, subsector, supply_chain
        """
        return self._load().copy()

    def _get_product_universe(self) -> list[str]:
        """
        Returns
        -------
        list[str]
            已分类的全部品种代码列表。
        """
        return self._load()["product"].tolist()

    def get_products_by(
        self,
        asset_class: str | None = None,
        sector: str | None = None,
        subsector: str | None = None,
        supply_chain: str | None = None,
    ) -> list[str]:
        """
        按分类层级过滤品种。

        Parameters
        ----------
        asset_class : str, optional
        sector : str, optional
        subsector : str, optional
        supply_chain : str, optional
            匹配时使用子串包含匹配，因此 'iron_steel_chain;feed_chain' 中的
            'iron_steel_chain' 也能被匹配到。

        Returns
        -------
        list[str]
            符合条件的品种代码列表。
        """
        df = self._load()
        mask = pd.Series(True, index=df.index)
        if asset_class is not None:
            mask &= df["asset_class"] == asset_class
        if sector is not None:
            mask &= df["sector"] == sector
        if subsector is not None:
            mask &= df["subsector"] == subsector
        if supply_chain is not None:
            mask &= df["supply_chain"].str.contains(supply_chain, na=False)
        return df.loc[mask, "product"].tolist()

    def _get_dummy_matrix(self, level: Literal['asset_class', 'sector', 'subsector'] = "sector") -> pd.DataFrame:
        """
        返回指定分类层级的虚拟变量矩阵，用于中性化回归。

        Parameters
        ----------
        level : {'asset_class', 'sector', 'subsector'}, default 'sector'
            生成虚拟变量的分类层级。

        Returns
        -------
        pd.DataFrame
            index=product, columns=category_values, values=0/1
        """
        df = self._load()
        dummies = pd.get_dummies(df.set_index("product")[level], prefix=level)
        return dummies.astype(int)

    @property
    def asset_classes(self) -> list[str]:
        return sorted(self._load()["asset_class"].unique().tolist())

    @property
    def sectors(self) -> list[str]:
        return sorted(self._load()["sector"].unique().tolist())

    @property
    def supply_chains(self) -> list[str]:
        """
        Returns
        -------
        list[str]
            展开后的所有不重复产业链标签（已从分号分隔解包）。
        """
        chains: set[str] = set()
        for entry in self._load()["supply_chain"].dropna():
            for c in entry.split(";"):
                chains.add(c.strip())
        return sorted(chains)


if __name__ == "__main__":
    mgr = ProductClassificationManager(
        r"D:\量化策略\CTA策略\Multi-factor Trend Following\db\rules\product_classification"
    )
    df = mgr.get_classification()
    print(df.to_string())
    print(f"品种总数: {len(df)}")
    print(f"asset_classes: {mgr.asset_classes}")
    print(f"sectors: {mgr.sectors}")
    print(f"supply_chains: {mgr.supply_chains}")
    print()

    ferrous = mgr.get_products_by(asset_class="ferrous_metal")
    print(f"黑色金属品种 ({len(ferrous)}): {ferrous}")

    iron_chain = mgr.get_products_by(supply_chain="iron_steel_chain")
    print(f"黑色产业链品种 ({len(iron_chain)}): {iron_chain}")

    print()

    dummy = mgr.get_dummy_matrix(level="sector")
    print(f"品种组虚拟变量矩阵: {dummy.shape[0]} products x {dummy.shape[1]} sectors")
    print(dummy.head(10).to_string())
