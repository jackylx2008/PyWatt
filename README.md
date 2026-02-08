# PyWatt

PyWatt 是一个轻量级的 Python 工具，用于通过 Modbus TCP 协议从工业/家用电表采集数据。它支持 IEEE 754 浮点数解析，并将采集到的电量数据持久化存储到 CSV 文件中（可选同步到 MySQL）。

## 🌟 功能特性

- **Modbus TCP 通讯**: 稳定读取保持寄存器数据。
- **高精度解析**: 支持 32 位浮点数（IEEE 754）解析，可灵活配置寄存器顺序（高位在前或低位在前）。
- **环境驱动配置**: 支持通过 `.env` 文件和环境变量管理设备参数（IP、端口、从站 ID 等）。
- **自动化记录**: 采集结果自动附加时间戳并保存至 CSV 文件，按天滚动命名。
- **可选 MySQL 存储**: 配置数据库后自动建库建表并写入采集结果。
- **完善的日志系统**: 自动生成详细的操作日志，支持 `DEBUG` 到 `CRITICAL` 各个级别。

## 🛠️ 快速开始

### 1. 克隆项目
```bash
git clone https://github.com/jackylx2008/PyWatt.git
cd PyWatt
```

### 2. 环境准备
建议使用虚拟环境：
```bash
python -m venv .venv
# Windows
.\venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
```

安装依赖：
```bash
pip install -r requirements.txt
```

### 3. 参数配置
将目录下的 `.env.example`（如果有）复制为 `.env` 并根据实际设备修改参数：
```ini
HOST=192.168.1.100       # 设备 IP
PORT=502                 # Modbus 端口
SLAVE_ID=1               # 从站 ID
START_ADDRESS=0         # 起始地址
NUM_REGISTERS=10         # 读取的寄存器数量
BYTE_ORDER=big           # 字节序 (big/little)
REGISTER_ORDER=low_first # 寄存器顺序 (high_first/low_first)
FETCH_INTERVAL=60         # 采集间隔(秒)
```

`config.yaml` 中用于配置日志、CSV 路径以及 MySQL：
```yaml
log_level: INFO
csv_file_path: data/electric_meter_total.csv
mysql:
	host: ${MYSQL_HOST}
	port: ${MYSQL_PORT}
	user: ${MYSQL_USER}
	password: ${MYSQL_PASSWORD}
	database: elec_meter
	table: elec_meter_data
```

### 4. 运行
```bash
python get_elec_meter.py
```

## 📊 数据输出格式

数据将以 CSV 格式保存，表头结构如下：
- `create_time`: 数据采集时间。
- `total_kwh`: 所有采集数值的总和。
- `reg_XXXXX_XXXXX`: 各个寄存器对对应的解析数值。

## 🗄️ MySQL 表结构示例

程序会自动建库建表，字段会随寄存器数量变化。以下示例基于 `NUM_REGISTERS=10`（5 个浮点值）：
```sql
CREATE TABLE `elec_meter_data` (
	`id` INT AUTO_INCREMENT PRIMARY KEY,
	`create_time` DATETIME,
	`total_kwh` FLOAT,
	`reg_40001_40002` FLOAT,
	`reg_40003_40004` FLOAT,
	`reg_40005_40006` FLOAT,
	`reg_40007_40008` FLOAT,
	`reg_40009_40010` FLOAT
);
```

## 📂 项目结构
- `get_elec_meter.py`: 主运行脚本。
- `logging_config.py`: 日志模块封装。
- `config.yaml`: 动态配置文件。
- `logs/`: 运行日志存放目录。

## 📝 贡献与许可
欢迎提交 Issue 和 Pull Request。本项目采用 [LICENSE](LICENSE) 获取许可。
