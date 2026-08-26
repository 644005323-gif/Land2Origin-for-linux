"""Complete pure-Python reader for validated Neware NDAX/NDC v14 archives.

An NDAX is a ZIP archive, not a flat spreadsheet.  Neware v14 keeps the
electrochemical information in separate binary streams:

* ``data.ndc``: every measured voltage/current point;
* ``data_runInfo.ndc``: timestamp, capacity, energy, and range anchors;
* ``data_step.ndc``: cycle and step boundaries;
* ``data_log.ndc``: vendor log/event values;
* ``data_es.ndc``: extension protobuf messages, retained byte-for-byte and
  decoded at the protobuf wire level.

This module parses and retains each stream independently.  It never invents a
per-point timestamp or capacity where the archive did not store one.  Plotting
helpers deliberately consume the recorded run-info anchors.
"""
from __future__ import annotations

import bisect
import math
import re
import statistics
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from xml.etree import ElementTree as ET

from .models import BatteryData, CycleData


_BLOCK_SIZE = 4096
_PAGE_DATA_OFFSET = 132
_NDC_VERSION = 14
_POINT_SIZE = 8
_RUN_INFO_SIZE = 55
_STEP_SIZE = 37
_LOG_V6_SIZE = 32
_LOG_V17_SIZE = 34
_CURRENT_EPS_A = 1e-5

_STEP_STATE = {1: "charge_cc", 2: "discharge", 3: "charge_cv", 4: "rest"}


@dataclass(frozen=True)
class NDAXMainPoint:
    """One original record from ``data.ndc``.  It has no stored time/capacity."""

    sequence: int
    voltage_V: float
    current_A: float


@dataclass(frozen=True)
class NDAXStep:
    """One original step boundary from ``data_step.ndc``."""

    cycle_id: int
    step_ids: Tuple[int, int, int, int, int]
    step_type: int
    first_total_time_s: float
    first_sequence: int

    @property
    def step_id(self) -> int:
        return self.step_ids[0]

    @property
    def state(self) -> str:
        return _STEP_STATE.get(self.step_type, f"vendor_step_{self.step_type}")


@dataclass(frozen=True)
class NDAXRunInfo:
    """One original run-info anchor, associated with a main-data sequence."""

    sequence: int
    step_elapsed_s: float
    absolute_epoch_s: float
    interval_s: float
    charge_capacity_kAh: float
    discharge_capacity_kAh: float
    charge_energy_kWh: float
    discharge_energy_kWh: float
    current_range_A: float
    work_type: int
    step_index: int
    total_capacity_kAh: float
    total_energy_kWh: float

    @property
    def charge_capacity_C(self) -> float:
        return self.charge_capacity_kAh * 3_600_000.0

    @property
    def discharge_capacity_C(self) -> float:
        return self.discharge_capacity_kAh * 3_600_000.0

    @property
    def charge_capacity_mAh(self) -> float:
        return self.charge_capacity_kAh * 1_000_000.0

    @property
    def discharge_capacity_mAh(self) -> float:
        return self.discharge_capacity_kAh * 1_000_000.0


@dataclass(frozen=True)
class NDAXLog:
    """One original log entry from ``data_log.ndc``.

    Neware v14 uses both the 32-byte v6 layout and the 34-byte v17 layout.
    The latter only adds a millisecond timestamp field.
    """

    main_sequence: int
    code: int
    absolute_epoch_s: float
    values: Tuple[float, float, float, float, float]


@dataclass(frozen=True)
class NDAXExtensionField:
    """One protobuf wire field in an ES message.

    ``value`` is an integer for varints, a little-endian raw byte sequence for
    fixed-width values, and the exact payload bytes for length-delimited
    values.  The vendor does not embed an ES schema in NDAX, so no semantic
    field names are inferred here.
    """

    number: int
    wire_type: int
    value: int | bytes


@dataclass(frozen=True)
class NDAXExtensionMessage:
    """One original protobuf message from an ES NDC page."""

    page_number: int
    payload: bytes
    fields: Tuple[NDAXExtensionField, ...]


@dataclass(frozen=True)
class NDAXCycleSummary:
    """Vendor-cycle summary derived from original step/run-info streams."""

    cycle_id: int
    start_sequence: int
    end_sequence: int
    charge_capacity_C: float
    discharge_capacity_C: float
    start_epoch_s: float
    end_epoch_s: float
    has_discharge: bool

    @property
    def charge_capacity_mAh(self) -> float:
        return self.charge_capacity_C / 3.6

    @property
    def discharge_capacity_mAh(self) -> float:
        return self.discharge_capacity_C / 3.6

    @property
    def coulombic_efficiency_percent(self) -> float:
        return (self.discharge_capacity_C / self.charge_capacity_C * 100.0
                if self.charge_capacity_C > 0 else math.nan)


@dataclass(frozen=True)
class NDAXAnchoredRecord:
    """A direct join of original main-point and run-info records.

    ``cycle_id`` and ``step`` are assigned from the original step boundaries.
    All numerical electrochemical fields are source values, not interpolations.
    """

    main: NDAXMainPoint
    run_info: NDAXRunInfo
    cycle_id: int
    step: NDAXStep

    @property
    def state(self) -> str:
        return self.step.state


@dataclass
class NDAXArchive:
    """Fully decoded NDC v14 archive with all source streams retained."""

    source: Path
    metadata: Dict[str, str]
    active_mass_g: float
    main_points: List[NDAXMainPoint]
    run_info: List[NDAXRunInfo]
    steps: List[NDAXStep]
    logs: List[NDAXLog] = field(default_factory=list)
    extension_payload: bytes = b""
    extension_messages: List[NDAXExtensionMessage] = field(default_factory=list)
    xml_documents: Dict[str, str] = field(default_factory=dict)
    member_sizes: Dict[str, int] = field(default_factory=dict)

    def anchored_records(self) -> List[NDAXAnchoredRecord]:
        """Join run-info anchors to exact main records and step boundaries."""
        step_sequences = [step.first_sequence for step in self.steps]
        joined: List[NDAXAnchoredRecord] = []
        for anchor in self.run_info:
            if not 1 <= anchor.sequence <= len(self.main_points):
                continue
            step_index = bisect.bisect_right(step_sequences, anchor.sequence) - 1
            if step_index < 0:
                continue
            joined.append(NDAXAnchoredRecord(
                main=self.main_points[anchor.sequence - 1],
                run_info=anchor,
                cycle_id=self.steps[step_index].cycle_id,
                step=self.steps[step_index],
            ))
        return joined

    def to_cycle_summary(self, complete_only: bool = False) -> List[NDAXCycleSummary]:
        """Summarize the original vendor cycle groups.

        Capacity counters may reset between CC and CV steps.  Therefore each
        step's recorded terminal capacity is accumulated, rather than using a
        difference between arbitrary anchors.  ``complete_only`` filters out
        cycle groups without a recorded discharge step; by default these are
        retained so an interrupted measurement remains visible.
        """
        anchors = self.anchored_records()
        summaries: List[NDAXCycleSummary] = []
        for step_index, first_step in enumerate(self.steps):
            if step_index and self.steps[step_index - 1].cycle_id == first_step.cycle_id:
                continue
            end_step_index = step_index + 1
            while (end_step_index < len(self.steps)
                   and self.steps[end_step_index].cycle_id == first_step.cycle_id):
                end_step_index += 1
            end_sequence = (self.steps[end_step_index].first_sequence - 1
                            if end_step_index < len(self.steps) else len(self.main_points))
            cycle_anchors = [record for record in anchors
                             if first_step.first_sequence <= record.main.sequence <= end_sequence]
            capacities: Dict[int, float] = {}
            for record in cycle_anchors:
                value_C = (record.run_info.charge_capacity_C
                           if record.state.startswith("charge")
                           else record.run_info.discharge_capacity_C if record.state == "discharge"
                           else 0.0)
                capacities[record.step.first_sequence] = max(
                    capacities.get(record.step.first_sequence, 0.0), value_C)
            steps_by_sequence = {step.first_sequence: step for step in self.steps}
            charge_C = sum(value for sequence, value in capacities.items()
                           if steps_by_sequence[sequence].state.startswith("charge"))
            discharge_C = sum(value for sequence, value in capacities.items()
                              if steps_by_sequence[sequence].state == "discharge")

            # BTSDA includes main records after the final run-info anchor when
            # a test stops between capacity snapshots.  Their uniform sampling
            # interval is stored in that final anchor, so integrate those
            # original current samples only for the uncovered tail.  This is a
            # derived summary value; the raw records stay unchanged above.
            if cycle_anchors:
                last_anchor = cycle_anchors[-1]
                if (last_anchor.main.sequence < end_sequence
                        and last_anchor.run_info.interval_s > 0
                        and last_anchor.step.state.startswith("charge")):
                    tail_C = sum(
                        point.current_A * last_anchor.run_info.interval_s
                        for point in self.main_points[last_anchor.main.sequence:end_sequence]
                    )
                    charge_C += max(0.0, tail_C)
                elif (last_anchor.main.sequence < end_sequence
                      and last_anchor.run_info.interval_s > 0
                      and last_anchor.step.state == "discharge"):
                    tail_C = sum(
                        -point.current_A * last_anchor.run_info.interval_s
                        for point in self.main_points[last_anchor.main.sequence:end_sequence]
                    )
                    discharge_C += max(0.0, tail_C)
            has_discharge = discharge_C > 0.0
            if cycle_anchors:
                start_epoch = cycle_anchors[0].run_info.absolute_epoch_s
                end_epoch = cycle_anchors[-1].run_info.absolute_epoch_s
            else:
                start_epoch = end_epoch = math.nan
            summary = NDAXCycleSummary(
                cycle_id=first_step.cycle_id,
                start_sequence=first_step.first_sequence,
                end_sequence=end_sequence,
                charge_capacity_C=charge_C,
                discharge_capacity_C=discharge_C,
                start_epoch_s=start_epoch,
                end_epoch_s=end_epoch,
                has_discharge=has_discharge,
            )
            if not complete_only or summary.has_discharge:
                summaries.append(summary)
        return summaries

    def to_battery_data(self, rate_label: str = "1C") -> BatteryData:
        """Extract charge/discharge curves using only recorded capacity anchors."""
        grouped: Dict[int, List[NDAXAnchoredRecord]] = {}
        for record in self.anchored_records():
            grouped.setdefault(record.cycle_id, []).append(record)

        cycles: List[CycleData] = []
        for _cycle_id, records in sorted(grouped.items()):
            charge_capacity: List[float] = []
            charge_voltage: List[float] = []
            discharge_capacity: List[float] = []
            discharge_voltage: List[float] = []
            charge_offset = 0.0
            previous_charge_raw: Optional[float] = None
            previous_charge_curve: Optional[float] = None
            for record in records:
                if abs(record.main.current_A) <= _CURRENT_EPS_A:
                    continue
                if record.state.startswith("charge"):
                    if discharge_capacity:
                        cycles.append(CycleData(
                            cycle_number=len(cycles) + 1,
                            rate_label=rate_label,
                            capacity=charge_capacity + discharge_capacity,
                            voltage=charge_voltage + discharge_voltage,
                            charge_capacity=charge_capacity,
                            charge_voltage=charge_voltage,
                            discharge_capacity=discharge_capacity,
                            discharge_voltage=discharge_voltage,
                        ))
                        charge_capacity, charge_voltage = [], []
                        discharge_capacity, discharge_voltage = [], []
                        charge_offset = 0.0
                        previous_charge_raw = None
                        previous_charge_curve = None
                    raw_capacity = record.run_info.charge_capacity_mAh / self.active_mass_g
                    # A CC -> CV transition can restart Neware's local charge
                    # counter at zero.  Keep the raw run-info record unchanged;
                    # this is only the continuous plotting coordinate.
                    if previous_charge_raw is not None:
                        reset_limit = max(0.05, abs(previous_charge_raw) * 0.2)
                        if raw_capacity < previous_charge_raw - reset_limit:
                            charge_offset = previous_charge_curve or 0.0
                    curve_capacity = raw_capacity + charge_offset
                    charge_capacity.append(curve_capacity)
                    charge_voltage.append(record.main.voltage_V)
                    previous_charge_raw = raw_capacity
                    previous_charge_curve = curve_capacity
                elif record.state == "discharge":
                    discharge_capacity.append(record.run_info.discharge_capacity_mAh / self.active_mass_g)
                    discharge_voltage.append(record.main.voltage_V)

            # A charge-only terminal sequence is present in raw data but is not
            # a complete drawable charge/discharge curve.
            if discharge_capacity:
                cycles.append(CycleData(
                    cycle_number=len(cycles) + 1,
                    rate_label=rate_label,
                    capacity=charge_capacity + discharge_capacity,
                    voltage=charge_voltage + discharge_voltage,
                    charge_capacity=charge_capacity,
                    charge_voltage=charge_voltage,
                    discharge_capacity=discharge_capacity,
                    discharge_voltage=discharge_voltage,
                ))

        # Retain the archive unchanged, but avoid presenting a demonstrably
        # interrupted terminal discharge as an ordinary plotted cycle.
        if len(cycles) >= 2:
            endpoints = [abs(cycle.capacity[-1]) for cycle in cycles if cycle.capacity]
            if endpoints[:-1] and endpoints[-1] < statistics.median(endpoints[:-1]) * 0.1:
                cycles.pop()
        if not cycles:
            raise ValueError(f"{self.source.name} 未识别到包含放电段的循环")
        return BatteryData(filename=str(self.source), cycles=cycles)


def _read_member(archive: zipfile.ZipFile, name: str, required: bool = True) -> bytes:
    try:
        return archive.read(name)
    except KeyError as exc:
        if required:
            raise ValueError(f"NDAX 缺少 {name}，不是可识别的测试数据包") from exc
        return b""


def _decode_xml(blob: bytes) -> ET.Element:
    if blob.startswith(b"\xff\xfe"):
        text = blob[2:].decode("utf-16-le")
    elif blob.startswith(b"\xfe\xff"):
        text = blob[2:].decode("utf-16-be")
    else:
        for encoding in ("gb2312", "utf-8"):
            try:
                text = blob.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("XML 编码无法识别")
    return ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1))


def _decode_xml_text(blob: bytes) -> str:
    """Decode an XML member without discarding any vendor-defined fields."""
    if blob.startswith(b"\xff\xfe"):
        return blob[2:].decode("utf-16-le")
    if blob.startswith(b"\xfe\xff"):
        return blob[2:].decode("utf-16-be")
    for encoding in ("gb2312", "utf-8"):
        try:
            return blob.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("XML 编码无法识别")


def _validate_ndc_v14(blob: bytes, name: str) -> None:
    if len(blob) < _BLOCK_SIZE:
        raise ValueError(f"{name} 长度不足，无法读取 NDC 头")
    _flag, version = struct.unpack_from("<HH", blob, 0)
    if version != _NDC_VERSION:
        raise ValueError(
            f"{name} 的 NDC 版本为 {version}；当前纯 Python 解析器只验证了 v{_NDC_VERSION}。"
            "请使用 Windows BTSDA 导出器处理该文件。"
        )


def _iter_pages(blob: bytes, record_size: int, name: str) -> Iterator[Tuple[int, int]]:
    for page_offset in range(_BLOCK_SIZE, len(blob) - _BLOCK_SIZE + 1, _BLOCK_SIZE):
        _flag, count = struct.unpack_from("<HH", blob, page_offset)
        if count == 0:
            continue
        end = page_offset + _PAGE_DATA_OFFSET + count * record_size
        if end > page_offset + _BLOCK_SIZE - 4:
            raise ValueError(f"{name} 的页面记录数超出边界，文件可能已损坏")
        yield page_offset, count


def _parse_metadata(step_xml: bytes, test_info_xml: bytes, source: Path) -> Tuple[Dict[str, str], float]:
    try:
        step_root = _decode_xml(step_xml)
        test_root = _decode_xml(test_info_xml)
        scq = step_root.find(".//Head_Info/SCQ")
        mass_g = float(scq.attrib["Value"]) / 1000.0 if scq is not None else math.nan
        test_info = test_root.find(".//TestInfo")
        metadata = dict(test_info.attrib) if test_info is not None else {}
        for element in step_root.findall(".//Head_Info/*"):
            if "Value" in element.attrib:
                metadata[f"step.{element.tag}"] = element.attrib["Value"]
    except (ET.ParseError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{source.name}: Step.xml/TestInfo.xml 元数据无法读取") from exc
    if not math.isfinite(mass_g) or mass_g <= 0:
        raise ValueError(f"{source.name}: Step.xml 的 SCQ 质量必须为正数")
    return metadata, mass_g


def _parse_main_points(blob: bytes) -> List[NDAXMainPoint]:
    points: List[NDAXMainPoint] = []
    for page_offset, count in _iter_pages(blob, _POINT_SIZE, "data.ndc"):
        for index in range(count):
            voltage, current_kA = struct.unpack_from("<ff", blob, page_offset + _PAGE_DATA_OFFSET + index * _POINT_SIZE)
            points.append(NDAXMainPoint(len(points) + 1, float(voltage), float(current_kA) * 1000.0))
    if not points:
        raise ValueError("data.ndc 未包含任何逐点电压/电流记录")
    return points


def _parse_steps(blob: bytes) -> List[NDAXStep]:
    steps: List[NDAXStep] = []
    for page_offset, count in _iter_pages(blob, _STEP_SIZE, "data_step.ndc"):
        for index in range(count):
            offset = page_offset + _PAGE_DATA_OFFSET + index * _STEP_SIZE
            steps.append(NDAXStep(
                cycle_id=struct.unpack_from("<I", blob, offset)[0],
                step_ids=struct.unpack_from("<IIIII", blob, offset + 4),
                step_type=blob[offset + 24],
                first_total_time_s=struct.unpack_from("<Q", blob, offset + 25)[0] / 1000.0,
                first_sequence=struct.unpack_from("<I", blob, offset + 33)[0],
            ))
    steps.sort(key=lambda step: step.first_sequence)
    if not steps:
        raise ValueError("data_step.ndc 未包含工步边界")
    return steps


def _parse_run_info(blob: bytes) -> List[NDAXRunInfo]:
    records: List[NDAXRunInfo] = []
    for page_offset, count in _iter_pages(blob, _RUN_INFO_SIZE, "data_runInfo.ndc"):
        for index in range(count):
            offset = page_offset + _PAGE_DATA_OFFSET + index * _RUN_INFO_SIZE
            sequence = struct.unpack_from("<I", blob, offset + 41)[0]
            if not sequence:
                continue
            absolute_seconds = struct.unpack_from("<I", blob, offset + 33)[0]
            absolute_milliseconds = struct.unpack_from("<H", blob, offset + 45)[0]
            records.append(NDAXRunInfo(
                sequence=sequence,
                step_elapsed_s=(struct.unpack_from("<I", blob, offset)[0] + (blob[offset + 4] << 32)) / 1000.0,
                absolute_epoch_s=absolute_seconds + absolute_milliseconds / 1000.0,
                charge_capacity_kAh=struct.unpack_from("<f", blob, offset + 5)[0],
                discharge_capacity_kAh=struct.unpack_from("<f", blob, offset + 9)[0],
                charge_energy_kWh=struct.unpack_from("<f", blob, offset + 13)[0],
                discharge_energy_kWh=struct.unpack_from("<f", blob, offset + 17)[0],
                current_range_A=struct.unpack_from("<f", blob, offset + 21)[0],
                work_type=struct.unpack_from("<I", blob, offset + 25)[0],
                interval_s=struct.unpack_from("<I", blob, offset + 29)[0] / 1000.0,
                step_index=struct.unpack_from("<I", blob, offset + 37)[0],
                total_capacity_kAh=struct.unpack_from("<f", blob, offset + 47)[0],
                total_energy_kWh=struct.unpack_from("<f", blob, offset + 51)[0],
            ))
    records.sort(key=lambda record: record.sequence)
    if not records:
        raise ValueError("data_runInfo.ndc 未包含运行记录")
    return records


def _log_record_size(blob: bytes) -> int:
    """Determine the v14 log layout from all populated NDC pages."""
    candidates = []
    for record_size in (_LOG_V17_SIZE, _LOG_V6_SIZE):
        try:
            list(_iter_pages(blob, record_size, "data_log.ndc"))
            candidates.append(record_size)
        except ValueError:
            pass
    if not candidates:
        raise ValueError("data_log.ndc 的记录布局不受当前 v14 解析器支持")
    # Prefer v17 only when both mathematically fit (small log pages can do so).
    return _LOG_V17_SIZE if _LOG_V17_SIZE in candidates else _LOG_V6_SIZE


def _parse_logs(blob: bytes) -> List[NDAXLog]:
    if not blob:
        return []
    _validate_ndc_v14(blob, "data_log.ndc")
    record_size = _log_record_size(blob)
    logs: List[NDAXLog] = []
    for page_offset, count in _iter_pages(blob, record_size, "data_log.ndc"):
        for index in range(count):
            offset = page_offset + _PAGE_DATA_OFFSET + index * record_size
            absolute_seconds = struct.unpack_from("<I", blob, offset + 8)[0]
            logs.append(NDAXLog(
                main_sequence=struct.unpack_from("<I", blob, offset)[0],
                code=struct.unpack_from("<I", blob, offset + 4)[0],
                absolute_epoch_s=(absolute_seconds + struct.unpack_from("<H", blob, offset + 32)[0] / 1000.0
                                  if record_size == _LOG_V17_SIZE else absolute_seconds),
                values=struct.unpack_from("<fffff", blob, offset + 12),
            ))
    return logs


def _read_varint(blob: bytes, offset: int) -> Tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(blob):
            raise ValueError("protobuf varint 在消息结尾处截断")
        byte = blob[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
    raise ValueError("protobuf varint 超过 10 字节")


def _parse_protobuf_fields(payload: bytes) -> Tuple[NDAXExtensionField, ...]:
    fields: List[NDAXExtensionField] = []
    offset = 0
    while offset < len(payload):
        key, offset = _read_varint(payload, offset)
        number, wire_type = key >> 3, key & 0x07
        if number == 0:
            raise ValueError("protobuf 字段号不能为 0")
        if wire_type == 0:
            value, offset = _read_varint(payload, offset)
        elif wire_type == 1:
            value, offset = payload[offset:offset + 8], offset + 8
        elif wire_type == 2:
            size, offset = _read_varint(payload, offset)
            value, offset = payload[offset:offset + size], offset + size
            if len(value) != size:
                raise ValueError("protobuf length-delimited 字段在消息结尾处截断")
        elif wire_type == 5:
            value, offset = payload[offset:offset + 4], offset + 4
        else:
            raise ValueError(f"不支持的 protobuf wire type {wire_type}")
        fields.append(NDAXExtensionField(number, wire_type, value))
    return tuple(fields)


def _parse_extension_messages(blob: bytes) -> List[NDAXExtensionMessage]:
    if not blob:
        return []
    _validate_ndc_v14(blob, "data_es.ndc")
    messages: List[NDAXExtensionMessage] = []
    for page_number, page_offset in enumerate(range(_BLOCK_SIZE, len(blob), _BLOCK_SIZE), start=1):
        if page_offset + _BLOCK_SIZE > len(blob):
            raise ValueError("data_es.ndc 包含不完整的 NDC 页")
        flag, payload_size = struct.unpack_from("<HH", blob, page_offset)
        if not payload_size:
            continue
        end = page_offset + _PAGE_DATA_OFFSET + payload_size
        if end > page_offset + _BLOCK_SIZE - 4:
            raise ValueError("data_es.ndc 的 ES 消息超出页面边界")
        payload = blob[page_offset + _PAGE_DATA_OFFSET:end]
        if flag != 17:
            raise ValueError(f"data_es.ndc 的第 {page_number} 页标记为 {flag}，预期为 17")
        messages.append(NDAXExtensionMessage(page_number, payload, _parse_protobuf_fields(payload)))
    return messages


def parse_ndax_v14_archive(file_path: str | Path) -> NDAXArchive:
    """Parse every documented stream in a Neware NDAX/NDC v14 archive."""
    source = Path(file_path)
    if source.suffix.lower() != ".ndax":
        raise ValueError(f"{source.name} 不是 .ndax 文件")
    try:
        with zipfile.ZipFile(source) as archive:
            member_sizes = {entry.filename: entry.file_size for entry in archive.infolist()}
            step_xml = _read_member(archive, "Step.xml")
            test_info_xml = _read_member(archive, "TestInfo.xml")
            data_blob = _read_member(archive, "data.ndc")
            run_info_blob = _read_member(archive, "data_runInfo.ndc")
            step_blob = _read_member(archive, "data_step.ndc")
            log_blob = _read_member(archive, "data_log.ndc", required=False)
            es_blob = _read_member(archive, "data_es.ndc", required=False)
            xml_documents = {
                entry.filename: _decode_xml_text(archive.read(entry.filename))
                for entry in archive.infolist()
                if entry.filename.lower().endswith(".xml")
            }
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{source.name} 不是有效的 NDAX ZIP 容器") from exc

    for name, blob in (("data.ndc", data_blob), ("data_runInfo.ndc", run_info_blob), ("data_step.ndc", step_blob)):
        _validate_ndc_v14(blob, name)
    metadata, mass_g = _parse_metadata(step_xml, test_info_xml, source)
    return NDAXArchive(
        source=source,
        metadata=metadata,
        active_mass_g=mass_g,
        main_points=_parse_main_points(data_blob),
        run_info=_parse_run_info(run_info_blob),
        steps=_parse_steps(step_blob),
        logs=_parse_logs(log_blob),
        extension_payload=es_blob,
        extension_messages=_parse_extension_messages(es_blob),
        xml_documents=xml_documents,
        member_sizes=member_sizes,
    )


def parse_ndax_v14(file_path: str | Path, rate_label: str = "1C") -> BatteryData:
    """Compatibility entry point for existing charge/discharge plotting code."""
    return parse_ndax_v14_archive(file_path).to_battery_data(rate_label)
