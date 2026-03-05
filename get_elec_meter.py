from pymodbus.client import ModbusTcpClient
import struct
import csv
import os
import logging
import mysql.connector
import time
import msvcrt  # Windows 下用于监听键盘输入
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
FETCH_INTERVAL = int(os.getenv("FETCH_INTERVAL", "60"))


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

# CSV 文件配置
_raw_csv_path = CONFIG.get("csv_file_path") or "electric_meter_total.csv"
if not os.path.isabs(_raw_csv_path):
    # 如果是相对路径，相对于脚本所在目录
    CSV_BASE_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), _raw_csv_path
    )
else:
    CSV_BASE_PATH = _raw_csv_path


def get_daily_csv_path():
    """根据当前日期生成 CSV 文件路径，并确保目录存在"""
    name, ext = os.path.splitext(CSV_BASE_PATH)
    date_str = datetime.now().strftime("%Y%m%d")
    path = f"{name}_{date_str}{ext}"
    # 确保保存目录存在
    dir_name = os.path.dirname(os.path.abspath(path))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    return path


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
# 数据库存储工具函数
# --------------------------
def save_to_mysql(data_row, headers, mysql_config):
    """
    将数据保存到 MySQL 数据库
    """
    conn = None
    cursor = None
    try:
        # 0. 提取参数并处理端口
        db_host = mysql_config.get("host")
        db_port = mysql_config.get("port")
        db_user = mysql_config.get("user")
        db_pwd = mysql_config.get("password")
        db_name = mysql_config.get("database")
        db_table = mysql_config.get("table", "elec_meter_data")

        # 处理可能的环境变量占位符
        if str(db_port).startswith("${"):
            db_port = 3306

        # 1. 建立连接 (设置连接超时，增加稳定性)
        conn = mysql.connector.connect(
            host=db_host,
            port=int(db_port or 3306),
            user=db_user,
            password=db_pwd,
            connect_timeout=10,
        )
        cursor = conn.cursor()

        # 2. 创建数据库（如果不存在）
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
        cursor.execute(f"USE `{db_name}`")

        # 3. 动态构建表结构（如果不存在）
        columns_def = [
            "id INT AUTO_INCREMENT PRIMARY KEY",
            "create_time DATETIME",
            "total_kwh DECIMAL(16, 4)",
        ]
        for header in headers[2:]:
            columns_def.append(f"`{header}` DECIMAL(16, 4)")

        create_table_sql = (
            f"CREATE TABLE IF NOT EXISTS `{db_table}` ({', '.join(columns_def)})"
        )
        cursor.execute(create_table_sql)

        # 4. 插入数据
        columns = ["create_time", "total_kwh"] + headers[2:]
        placeholders = ["%s"] * len(columns)
        column_names = ", ".join([f"`{c}`" for c in columns])
        values_placeholders = ", ".join(placeholders)
        insert_sql = (
            f"INSERT INTO `{db_table}` ({column_names}) "
            f"VALUES ({values_placeholders})"
        )

        # 处理空值为 None (以便存入数据库为 NULL)
        processed_row = []
        for val in data_row:
            if val == "":
                processed_row.append(None)
            else:
                processed_row.append(val)

        cursor.execute(insert_sql, processed_row)
        conn.commit()
        logger.info(f"数据已成功保存到 MySQL 数据库: {db_table}")

    except mysql.connector.Error as e:
        logger.error(f"MySQL 存储失败: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# --------------------------
# 数据采集任务
# --------------------------
def fetch_job():
    # 每次任务独立创建连接，增加鲁棒性
    client = ModbusTcpClient(HOST, port=PORT, timeout=5)

    try:
        # 连接设备
        if not client.connect():
            logger.error(f"无法连接到 Modbus 设备 {HOST}:{PORT}")
            return

        # 读取保持寄存器
        response = client.read_holding_registers(
            address=START_ADDRESS, count=NUM_REGISTERS, slave=SLAVE_ID
        )

        if response.isError():
            logger.error(f"Modbus 读取错误: {response}")
            return

        if len(response.registers) != NUM_REGISTERS:
            logger.error(
                f"寄存器数量不匹配: 期望 {NUM_REGISTERS}, 实际 {len(response.registers)}"
            )
            return

        registers = response.registers
        logger.debug(f"原始寄存器值: {registers}")

        # 解析浮点数
        float_values = []
        for i in range(0, NUM_REGISTERS, 2):
            try:
                value = parse_float(registers, i, BYTE_ORDER, REGISTER_ORDER)
                float_values.append(value)
            except ValueError as e:
                logger.warning(f"跳过无效数据（寄存器 {i}）: {e}")
                float_values.append(None)

        valid_values = [v for v in float_values if v is not None]
        sum_total = sum(valid_values)

        # --------------------------
        # 数据持久化
        # --------------------------
        headers = ["create_time", "total_kwh"]
        for idx in range(len(float_values)):
            reg_start_device = 40001 + 2 * idx
            reg_end_device = reg_start_device + 1
            headers.append(f"reg_{reg_start_device}_{reg_end_device}")

        row_data = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            f"{sum_total:.4f}",
        ]
        for val in float_values:
            row_data.append(f"{val:.4f}" if val is not None else "")

        # 1. 写入 CSV
        current_csv = get_daily_csv_path()
        file_exists = os.path.exists(current_csv)
        try:
            with open(current_csv, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(headers)
                writer.writerow(row_data)
            logger.info(f"数据已同步到 CSV: {os.path.basename(current_csv)}")
        except Exception as e:
            logger.error(f"CSV 写入失败: {e}")

        # 2. 写入 MySQL
        mysql_config = CONFIG.get("mysql")
        if mysql_config and mysql_config.get("host"):
            save_to_mysql(row_data, headers, mysql_config)

    except Exception as e:
        logger.error(f"采集任务执行异常: {e}", exc_info=True)
    finally:
        try:
            client.close()
        except Exception:
            pass


# --------------------------
# 主循环控制
# --------------------------
def main():
    logger.info("=" * 40)
    logger.info("PyWatt 电表采集程序启动 (长期运行模式)")
    logger.info(f"设备地址: {HOST}:{PORT}")
    logger.info(f"采集频率: 每 {FETCH_INTERVAL} 秒一次")
    logger.info("温馨提示: 在终端按 'q' 或 'Q' 键可安全退出程序")
    logger.info("=" * 40)

    try:
        while True:
            start_time = time.time()
            fetch_job()

            # 计算剩余休眠时间，并在休眠期间监听键盘
            elapsed = time.time() - start_time
            sleep_time = max(0.1, FETCH_INTERVAL - elapsed)

            # 分段休眠并检测按键，提高响应速度
            check_interval = 0.5
            slept = 0
            while slept < sleep_time:
                # 检查是否有按键按下
                if msvcrt.kbhit():
                    key = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                    if key == "q":
                        logger.info("检测到退出按键 'q'，正在停止程序...")
                        return

                time.sleep(min(check_interval, sleep_time - slept))
                slept += check_interval

    except KeyboardInterrupt:
        logger.info("程序由用户通过 Ctrl+C 手动停止")
    except Exception as e:
        logger.critical(f"程序遭遇致命错误并退出: {e}", exc_info=True)


if __name__ == "__main__":
    main()
