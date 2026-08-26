"""数据模型定义"""
from dataclasses import dataclass, field
from typing import List
try:
    import pandas as pd
except ImportError:  # pragma: no cover - exercised on the minimal Pi image
    pd = None

# NDAX binary extraction only needs the dataclasses below. Keep pandas
# optional so the Raspberry Pi can run the parser in its existing GSAS Python
# environment; dataframe export reports a clear dependency error if absent.


@dataclass
class CycleData:
    """单个循环的数据"""
    cycle_number: int
    rate_label: str  # 如 "1*C", "0.2C"
    capacity: List[float]  # 比容量 mAh/g
    voltage: List[float]   # 电压 V
    # 有明确工步信息的来源（例如 NDAX）可直接提供两个相位，避免绘图层
    # 通过电压极值反推充放电切分点。旧 LAND/TXT 数据保持为空，继续兼容。
    charge_capacity: List[float] = field(default_factory=list)
    charge_voltage: List[float] = field(default_factory=list)
    discharge_capacity: List[float] = field(default_factory=list)
    discharge_voltage: List[float] = field(default_factory=list)

    @property
    def rate_numeric(self) -> float:
        """从倍率标签提取数值，如 '1*C' -> 1.0"""
        import re
        match = re.search(r'(\d+\.?\d*)', self.rate_label)
        return float(match.group(1)) if match else 0.0


@dataclass
class BatteryData:
    """一个文件的完整数据"""
    filename: str
    cycles: List[CycleData]

    def get_unique_rates(self) -> List[str]:
        """获取所有唯一的倍率标签（按出现顺序）"""
        seen = set()
        unique = []
        for cycle in self.cycles:
            if cycle.rate_label not in seen:
                seen.add(cycle.rate_label)
                unique.append(cycle.rate_label)
        return unique

    def to_dataframe(self):
        """转换为DataFrame"""
        if pd is None:
            raise RuntimeError("BatteryData.to_dataframe() 需要安装 pandas")
        rows = []
        for cycle in self.cycles:
            for cap, volt in zip(cycle.capacity, cycle.voltage):
                rows.append({
                    '比容量/mAh/g': cap,
                    '电压/V': volt,
                    '循环序号': cycle.cycle_number,
                    '倍率': cycle.rate_label
                })
        return pd.DataFrame(rows)


@dataclass
class CVData:
    """单个 CHI 循环伏安 (CV) 文件的数据（已切片到最后一个完整圈）"""
    filename: str
    scan_rate: float           # V/s，例如 0.001
    e_high: float              # V
    e_low: float               # V
    sample_interval: float     # V
    potential: List[float] = field(default_factory=list)  # V
    current: List[float] = field(default_factory=list)    # A（原始）

    def scan_rate_label(self, origin_rich: bool = False) -> str:
        """扫速 → 显示字符串

        origin_rich=True  → 用 Origin Rich Text：'1 mV s\\+(-1)'
        origin_rich=False → 用普通 ASCII (Matplotlib)：'1 mV/s'
        """
        r = self.scan_rate
        if r < 1.0:
            mv = r * 1000.0
            if mv >= 1.0 and abs(mv - round(mv)) < 1e-6:
                num = f"{int(round(mv))}"
                unit_prefix = "mV"
            else:
                num = f"{mv:g}"
                unit_prefix = "mV"
        else:
            num = f"{r:g}"
            unit_prefix = "V"

        if origin_rich:
            return f"{num} {unit_prefix} s\\+(-1)"
        return f"{num} {unit_prefix}/s"


@dataclass
class XRDSample:
    """单个 XRD 测量数据"""
    filename: str
    sample_name: str
    two_theta: List[float]
    intensity: List[float]


@dataclass
class PdfCard:
    """PDF 标准卡片（由 CIF 算出的理论 stick pattern）"""
    filename: str
    card_number: str           # 如 '04-019-7540'
    formula_label: str         # 如 'Na4Fe3(PO4)2(P2O7)'
    two_theta_peaks: List[float]
    intensities: List[float]   # 0-100 的 I/I0


@dataclass
class XRDDataset:
    """一张 XRD 图的完整数据：N 条样品曲线 + M 张 PDF 卡片"""
    samples: List[XRDSample] = field(default_factory=list)
    pdf_cards: List[PdfCard] = field(default_factory=list)


@dataclass
class FTIRSample:
    """单个 FTIR 测量数据"""
    filename: str
    sample_name: str
    wavenumber: List[float]      # cm⁻¹
    transmittance: List[float]   # %（或归一化 0-1，看仪器导出）


@dataclass
class FTIRDataset:
    """一张 FTIR 图的完整数据：N 条样品曲线（共享 X 轴）"""
    samples: List[FTIRSample] = field(default_factory=list)


@dataclass
class CycleSummaryData:
    """已计算好的循环汇总数据（不含原始充放电曲线）

    数据来源：3 列汇总 TXT/CSV，列名形如：
        循环序号, 放电比容量/mAh/g, 能量效率/%
    """
    filename: str
    cycle_numbers: List[int] = field(default_factory=list)
    capacities: List[float] = field(default_factory=list)       # mAh/g
    efficiencies: List[float] = field(default_factory=list)     # %（E_C / 能量效率）
    rate_label: str = ""        # 用户可填，例如 "5C"
