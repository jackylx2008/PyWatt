from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
import struct
import csv
import os
import logging
from datetime import datetime  # 用于记录时间戳
from dotenv import load_dotenv
from logging_config import setup_logger
import yaml


# --------------------------
# 配置参数（根据设备文档调整）
# --------------------------
load_dotenv()

HOST = os.getenv("HOST", "localhost")
PORT = int(os.getenv("PORT", "502"))
SLAVE_ID = int(os.getenv("SLAVE_ID", "1"))
START_ADDRESS = int(os.getenv("START_ADDRESS", "0"))
NUM_REGISTERS = int(os.getenv("NUM_REGISTERS", "10"))
BYTE_ORDER = os.getenv("BYTE_ORDER", "big")
REGISTER_ORDER = os.getenv("REGISTER_ORDER", "low_first")


def load_config(config_path: str) -> dict:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}
    result = expand_env_values(config)
    return result if isinstance(result, dict) else {}


def expand_env_values(value):
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {key: expand_env_values(val) for key, val in value.items()}
    if isinstance(value, list):
        return [expand_env_values(item) for item in value]
    return value


def resolve_log_level(level_name: str) -> int:
    if isinstance(level_name, int):
        return level_name
    if level_name is None:
        return logging.INFO
    level = logging._nameToLevel.get(str(level_name).upper())
    return level if isinstance(level, int) else logging.INFO


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
CONFIG = load_config(CONFIG_PATH)

# CSV 文件配置（替代数据库）
CSV_FILE = CONFIG.get("csv_file_path") or "electric_meter_total.csv"

# --------------------------
# 日志配置
# --------------------------
logger = setup_logger(log_level=resolve_log_level(CONFIG.get("log_level") or "INFO"))


# --------------------------
# 解析浮点数工具函数（关键优化点）
# --------------------------
def parse_float(registers, index, byte_order, register_order):
    """
    从寄存器列表中解析IEEE 754浮点数
    :param registers: 寄存器列表（按Modbus地址顺序排列）
    :param index: 起始寄存器索引（每2寄存器解析1浮点数）
    :param byte_order: 字节序 ('big' 或 'little')
    :param register_order: 寄存器顺序 ('high_first' 或 'low_first')
    :return: 解析后的浮点数
    """
    if index + 1 >= len(registers):
        raise ValueError("寄存器索引超出范围，无法解析浮点数")

    reg_low = registers[index]  # 低地址寄存器（如40001）
    reg_high = registers[index + 1]  # 高地址寄存器（如40002）

    # 根据寄存器顺序组合字节流
    if register_order == "high_first":
        # 高地址寄存器在前（如40002的字节在前）
        bytes_data = reg_high.to_bytes(2, byteorder=byte_order) + reg_low.to_bytes(
            2, byteorder=byte_order
        )
    else:
        # 低地址寄存器在前（如40001的字节在前）
        bytes_data = reg_low.to_bytes(2, byteorder=byte_order) + reg_high.to_bytes(
            2, byteorder=byte_order
        )

    # 解析为浮点数
    try:
        format_char = ">" if byte_order == "big" else "<"
        return struct.unpack(f"{format_char}f", bytes_data)[0]
    except struct.error as e:
        logger.error(f"浮点数解析失败，字节流: {bytes_data.hex()}")
        raise ValueError("浮点数解析错误，请检查字节序和寄存器顺序") from e


# --------------------------
# 主程序
# --------------------------
def main():
    client = ModbusTcpClient(HOST, port=PORT)

    try:
        # 连接设备
        if not client.connect():
            raise ConnectionError(f"无法连接到设备 {HOST}:{PORT}")
        logger.info("设备连接成功")

        # 读取保持寄存器
        response = client.read_holding_registers(
            address=START_ADDRESS, count=NUM_REGISTERS, slave=SLAVE_ID
        )

        if response.isError():
            raise ModbusException(f"Modbus错误: {response}")
        if len(response.registers) != NUM_REGISTERS:
            raise ValueError(
                f"期望读取 {NUM_REGISTERS} 个寄存器，实际返回 {len(response.registers)} 个"
            )

        registers = response.registers
        logger.debug(f"原始寄存器值: {registers}")

        # 解析浮点数
        float_values = []
        for i in range(0, NUM_REGISTERS, 2):
            try:
                value = parse_float(registers, i, BYTE_ORDER, REGISTER_ORDER)
                float_values.append(value)
            except ValueError as e:
                logger.warning(f"跳过无效数据（寄存器{i} - {i + 1}）: {e}")
                float_values.append(None)

        # 输出结果（显示设备实际地址，如40001-40002）
        print("\n解析结果:")
        for idx, val in enumerate(float_values):
            reg_start_device = 40001 + 2 * idx  # 设备地址起始（如40001）
            reg_end_device = reg_start_device + 1  # 设备地址结束（如40002）
            if val is not None:
                print(f"寄存器 {reg_start_device}-{reg_end_device}: {val:.4f}")
            else:
                print(f"寄存器 {reg_start_device}-{reg_end_device}: <解析失败>")

        valid_values = [v for v in float_values if v is not None]  # 过滤掉解析失败的值
        sum_total = sum(valid_values)
        total_count = len(float_values)
        valid_count = len(valid_values)

        print(f"\n警告: 有 {total_count - valid_count} 个数值解析失败")
        print(f"有效数值总和 ({valid_count}个): {sum_total:.4f}")

        # 将结果写入 CSV 文件（追加模式），包含每个寄存器对应的电量值
        file_exists = os.path.exists(CSV_FILE)
        try:
            with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)

                # 构建表头：时间、总电量 + 每个寄存器对
                if not file_exists:
                    header = ["create_time", "total_kwh"]
                    for idx in range(len(float_values)):
                        reg_start_device = 40001 + 2 * idx
                        reg_end_device = reg_start_device + 1
                        header.append(f"reg_{reg_start_device}_{reg_end_device}")
                    writer.writerow(header)

                # 构建数据行
                row = [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    f"{sum_total:.4f}",
                ]
                for val in float_values:
                    if val is not None:
                        row.append(f"{val:.4f}")
                    else:
                        row.append("")  # 解析失败留空

                writer.writerow(row)
            logger.info(f"数据已写入CSV文件: {CSV_FILE}")
        except Exception as e:
            logger.error(f"写入CSV文件失败: {e}")

    except Exception as e:
        logger.error(f"未处理的异常: {e}", exc_info=True)
    finally:
        # 关闭 Modbus 连接
        try:
            client.close()
        except Exception as e:
            logger.warning(f"关闭Modbus连接时出错: {e}")

        logger.info("连接已关闭")


if __name__ == "__main__":
    main()
