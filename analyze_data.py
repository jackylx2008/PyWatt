import os
import csv
import mysql.connector
import random
import yaml
from datetime import datetime, date
from typing import Dict, Any, List
from dotenv import load_dotenv

# 加载配置
load_dotenv()


def expand_env_values(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {key: expand_env_values(val) for key, val in value.items()}
    if isinstance(value, list):
        return [expand_env_values(item) for item in value]
    return value


def load_config(config_path: str) -> Dict[str, Any]:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}
    result = expand_env_values(config)
    if isinstance(result, dict):
        return result
    return {}


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
CONFIG = load_config(CONFIG_PATH)


def get_mysql_connection():
    mysql_config = CONFIG.get("mysql")
    if not mysql_config:
        print("未找到 MySQL 配置")
        return None

    try:
        conn = mysql.connector.connect(
            host=mysql_config.get("host", "localhost"),
            port=int(mysql_config.get("port", 3306)),
            user=mysql_config.get("user", "root"),
            password=mysql_config.get("password", ""),
            database=mysql_config.get("database", "pywatt"),
        )
        return conn
    except Exception as e:
        print(f"MySQL 连接失败: {e}")
        return None


def analyze():
    conn = get_mysql_connection()
    if not conn:
        return

    cursor = conn.cursor(dictionary=True)
    table_name = CONFIG.get("mysql", {}).get("table", "elec_meter_data")

    print(f"正在从表 `{table_name}` 中获取随机数据...")

    # 获取表中的所有列名 (排除非寄存器列)
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    raw_rows = cursor.fetchall() or []

    all_columns: List[str] = [
        str(row["Field"])
        for row in raw_rows
        if isinstance(row, dict) and "Field" in row
    ]
    register_columns = [col for col in all_columns if col.startswith("reg_")]

    if not register_columns:
        print("未在数据库中找到任何寄存器列 (reg_xxx)")
        conn.close()
        return

    # 随机取一行数据
    cursor.execute(f"SELECT * FROM `{table_name}` ORDER BY RAND() LIMIT 1")
    mysql_row = cursor.fetchone()

    if not isinstance(mysql_row, dict):
        print("数据库中没有数据。")
        conn.close()
        return

    # 提取随机时间、日期和寄存器
    full_time = mysql_row.get("create_time")
    if not isinstance(full_time, (datetime, date)):
        print(f"创建时间格式不正确: {type(full_time)}")
        conn.close()
        return

    date_str = full_time.strftime("%Y-%m-%d")
    time_str = full_time.strftime("%H:%M:%S")
    random_reg = str(random.choice(register_columns))
    mysql_val = mysql_row.get(random_reg)

    print("-" * 50)
    print("随机抽样结果:")
    print(f"日期: {date_str}")
    print(f"时间: {time_str}")
    print(f"寄存器: {random_reg}")
    print(f"MySQL 中的值: {mysql_val}")
    print("-" * 50)

    # 查找对应的 CSV 文件
    _raw_csv_path = CONFIG.get("csv_file_path") or "electric_meter_total.csv"
    if not os.path.isabs(_raw_csv_path):
        csv_base_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), _raw_csv_path
        )
    else:
        csv_base_path = _raw_csv_path

    name, ext = os.path.splitext(csv_base_path)
    # get_elec_meter.py 中用的格式是 YYYYMMDD
    daily_csv_suffix = full_time.strftime("%Y%m%d")
    csv_path = f"{name}_{daily_csv_suffix}{ext}"

    if not os.path.exists(csv_path):
        print(f"错误: 找不到对应的 CSV 文件: {csv_path}")
        conn.close()
        return

    # 在 CSV 中查找对应的数据
    print(f"正在分析 CSV 文件: {os.path.basename(csv_path)}")
    csv_val = None
    found = False
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # CSV 中的 create_time 格式为 %Y-%m-%d %H:%M:%S
            if row["create_time"] == full_time.strftime("%Y-%m-%d %H:%M:%S"):
                csv_val = row.get(random_reg)
                found = True
                break

    if found:
        print(f"CSV 中的值: {csv_val}")
        # 比较
        try:
            m_val = float(str(mysql_val)) if mysql_val is not None else None
            c_val = float(str(csv_val)) if csv_val else None

            if m_val == c_val:
                print("结论: [成功] 数据一致")
            elif (
                m_val is not None and c_val is not None and abs(m_val - c_val) < 0.0001
            ):
                print("结论: [成功] 数据基本一致 (微小浮点误差)")
            else:
                print("结论: [失败] 数据不匹配！")
        except ValueError:
            print("结论: [错误] 无法解析数值进行比较")
    else:
        print(f"未在 CSV 中找到时间点 {full_time.strftime('%Y-%m-%d %H:%M:%S')} 的记录")

    conn.close()


if __name__ == "__main__":
    analyze()
