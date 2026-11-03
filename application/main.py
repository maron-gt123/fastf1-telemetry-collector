import os
import json
import logging

import fastf1
import pandas as pd
import mysql.connector
import yaml


# ==========================================
# 設定
# ==========================================

CONFIG_PATH = "config/config.yaml"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


config = load_config()


# ==========================================
# ログ設定
# ==========================================

log_dir = config["app"].get("log_dir", "./logs")
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(log_dir, "collector.log"),
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


# ==========================================
# FastF1設定
# ==========================================

cache_dir = config["app"]["cache_dir"]
os.makedirs(cache_dir, exist_ok=True)

fastf1.Cache.enable_cache(cache_dir)


# ==========================================
# セッション定義
# ==========================================

SESSION_MAP = {
    "FP1": ["Practice 1"],
    "FP2": ["Practice 2"],
    "FP3": ["Practice 3"],
    "SQ": ["Sprint Qualifying", "Sprint Shootout"],
    "S": ["Sprint"],
    "Q": ["Qualifying"],
    "R": ["Race"],
}


# ==========================================
# 追加取得項目
# ==========================================

# 追加したいFastF1のカラムをここに登録する
# 基本カラム以外はextra_data(JSON)に格納する

EXTRA_LAP_FIELDS = [
    "DriverNumber",
    "Stint",
    "Compound",
    "TyreLife",
    "SpeedST",
    "Position",
    "IsAccurate",
    "TrackStatus",
    "Deleted",
]


# ==========================================
# MySQL接続
# ==========================================

def connect_database():

    db = config["database"]

    return mysql.connector.connect(
        host=db["host"],
        port=db["port"],
        user=db["user"],
        password=db["password"],
        database=db["name"],
    )


# ==========================================
# テーブル作成
# ==========================================

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lap_times (

    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    season SMALLINT NOT NULL,
    gp_name VARCHAR(100) NOT NULL,
    session VARCHAR(10) NOT NULL,

    driver VARCHAR(10) NOT NULL,
    lap_number SMALLINT NOT NULL,

    lap_time_seconds DECIMAL(10,3),
    session_time_seconds DECIMAL(12,3),
    lap_start_time_seconds DECIMAL(12,3),
    lap_start_date DATETIME(3),

    extra_data JSON,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY unique_lap (
        season,
        gp_name,
        session,
        driver,
        lap_number
    ),

    INDEX idx_season_gp (season, gp_name),
    INDEX idx_driver (driver)

)
"""


def initialize_database(conn):

    cursor = conn.cursor()

    cursor.execute(CREATE_TABLE_SQL)

    conn.commit()
    cursor.close()

    logger.info("Database initialized")


# ==========================================
# データ変換
# ==========================================

def to_seconds(value):

    if pd.isna(value):
        return None

    return round(value.total_seconds(), 3)


def to_datetime(value):

    if pd.isna(value):
        return None

    # MySQL DATETIMEにはタイムゾーンを保存しないためUTCに統一
    timestamp = pd.Timestamp(value)

    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)

    return timestamp.to_pydatetime()


def json_safe(value):

    if value is None or pd.isna(value):
        return None

    if hasattr(value, "item"):
        value = value.item()

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if isinstance(value, pd.Timedelta):
        return value.total_seconds()

    return value


# ==========================================
# グランプリ検索
# ==========================================

def get_event_schedule(season):

    logger.info(f"Loading event schedule: {season}")

    return fastf1.get_event_schedule(
        season,
        include_testing=False
    )


def find_gp(schedule, gp_name):

    events = schedule[
        schedule["EventName"].str.casefold()
        == gp_name.casefold()
    ]

    if events.empty:
        logger.warning(
            f"Grand Prix not found: {gp_name}"
        )
        return None

    return events.iloc[0]


# ==========================================
# セッション存在確認
# ==========================================

def get_available_sessions(event):

    available = {}

    for i in range(1, 6):

        column = f"Session{i}"

        session_name = event.get(column)

        if not isinstance(session_name, str):
            continue

        session_name = session_name.strip()

        if not session_name:
            continue

        for code, names in SESSION_MAP.items():

            if session_name.casefold() in [
                name.casefold() for name in names
            ]:

                available[code] = session_name
                break

    return available


# ==========================================
# 追加データ取得
# ==========================================

def collect_extra_data(row):

    result = {}

    for field in EXTRA_LAP_FIELDS:

        if field not in row.index:
            continue

        value = json_safe(row[field])

        if value is not None:
            result[field] = value

    return json.dumps(
        result,
        ensure_ascii=False
    )


# ==========================================
# DB保存
# ==========================================

INSERT_SQL = """
INSERT INTO lap_times (

    season,
    gp_name,
    session,
    driver,
    lap_number,

    lap_time_seconds,
    session_time_seconds,
    lap_start_time_seconds,
    lap_start_date,

    extra_data

)
VALUES (
    %s,%s,%s,%s,%s,
    %s,%s,%s,%s,%s
)

ON DUPLICATE KEY UPDATE

    lap_time_seconds = VALUES(lap_time_seconds),
    session_time_seconds = VALUES(session_time_seconds),
    lap_start_time_seconds = VALUES(lap_start_time_seconds),
    lap_start_date = VALUES(lap_start_date),
    extra_data = VALUES(extra_data)
"""


def save_laps(conn, df, season, gp, session_code):

    cursor = conn.cursor()

    records = []

    for _, row in df.iterrows():

        if pd.isna(row["Driver"]):
            continue

        if pd.isna(row["LapNumber"]):
            continue

        records.append((

            season,
            gp,
            session_code,
            str(row["Driver"]),
            int(row["LapNumber"]),

            to_seconds(row["LapTime"]),
            to_seconds(row["Time"]),
            to_seconds(row["LapStartTime"]),
            to_datetime(row["LapStartDate"]),

            collect_extra_data(row),

        ))

    if records:

        cursor.executemany(
            INSERT_SQL,
            records
        )

        conn.commit()

    cursor.close()

    logger.info(
        f"Saved {len(records)} laps: {gp} [{session_code}]"
    )


# ==========================================
# セッション取得
# ==========================================

def fetch_session(conn, season, event, session_code):

    gp_name = event["EventName"]

    available = get_available_sessions(event)

    # セッションが存在するか確認
    if session_code not in available:

        logger.info(
            f"Skip unavailable session: "
            f"{gp_name} [{session_code}]"
        )

        return

    logger.info(
        f"Loading: {season} {gp_name} [{session_code}]"
    )

    try:

        session = fastf1.get_session(
            season,
            gp_name,
            session_code
        )

        session.load(
            laps=True,
            telemetry=False,
            weather=False,
            messages=False
        )

        laps = session.laps

        if laps.empty:

            logger.warning(
                f"No lap data: {gp_name} [{session_code}]"
            )

            return

        # 必須項目
        required_fields = [
            "Driver",
            "LapNumber",
            "LapTime",
            "Time",
            "LapStartTime",
            "LapStartDate",
        ]

        # データ項目の存在確認
        missing = [
            field for field in required_fields
            if field not in laps.columns
        ]

        if missing:

            raise ValueError(
                f"Missing columns: {missing}"
            )

        # 必要な項目だけを抽出
        fields = required_fields + [
            field
            for field in EXTRA_LAP_FIELDS
            if field in laps.columns
        ]

        df = laps[fields].copy()

        save_laps(
            conn,
            df,
            season,
            gp_name,
            session_code
        )

    except Exception:

        logger.exception(
            f"Session error: {gp_name} [{session_code}]"
        )


# ==========================================
# メイン処理
# ==========================================

def main():

    season = config["f1"]["season"]
    gps = config["f1"]["gps"]
    sessions = config["f1"]["sessions"]

    conn = None

    try:

        # DB接続
        conn = connect_database()

        initialize_database(conn)

        # シーズンスケジュール取得
        schedule = get_event_schedule(season)

        # グランプリごとに処理
        for gp_name in gps:

            logger.info(
                f"Processing Grand Prix: {gp_name}"
            )

            event = find_gp(schedule, gp_name)

            if event is None:
                continue

            # セッションごとに処理
            for session_code in sessions:

                session_code = session_code.upper()

                if session_code not in SESSION_MAP:

                    logger.warning(
                        f"Unknown session code: {session_code}"
                    )

                    continue

                fetch_session(
                    conn,
                    season,
                    event,
                    session_code
                )

    except Exception:

        logger.exception("Fatal error")

    finally:

        if conn is not None and conn.is_connected():

            conn.close()

            logger.info("Database connection closed")


# ==========================================
# Entry Point
# ==========================================

if __name__ == "__main__":
    main()
