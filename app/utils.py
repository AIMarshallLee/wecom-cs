"""
企业微信客服系统 - 工具函数模块

提供配置加载、日志配置、SQLite 数据库封装和消息格式化等基础工具。
"""
import os
import logging
import sqlite3
from typing import Optional, List, Dict, Any, Union, Tuple

import yaml


def load_config(config_path: str) -> Dict[str, Any]:
    """加载 YAML 配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        配置字典

    Raises:
        FileNotFoundError: 配置文件不存在
        yaml.YAMLError: 配置文件格式错误
    """
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if config is None:
        config = {}
    return config


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """配置日志系统

    Args:
        log_level: 日志级别（DEBUG / INFO / WARNING / ERROR）

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger("wecom_cs")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # 避免重复添加 handler
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # 避免日志向上传播导致重复输出
    logger.propagate = False
    return logger


class SQLiteDB:
    """SQLite 数据库封装

    提供简单的数据库初始化、执行和查询接口。
    线程安全（使用 check_same_thread=False）。
    """

    # 默认数据库 schema（建表语句）
    DEFAULT_SCHEMA = """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        content TEXT,
        reply TEXT,
        mode TEXT,
        timestamp TEXT DEFAULT (datetime('now', 'localtime'))
    );

    CREATE TABLE IF NOT EXISTS stats (
        date TEXT PRIMARY KEY,
        total_messages INTEGER DEFAULT 0,
        auto_replies INTEGER DEFAULT 0,
        human_replies INTEGER DEFAULT 0
    );
    """

    def __init__(self, db_path: str):
        """初始化数据库连接

        Args:
            db_path: 数据库文件路径，使用 ':memory:' 为内存数据库
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        """获取数据库连接（懒加载）"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init_db(self, schema: Optional[str] = None) -> None:
        """初始化数据库表结构

        Args:
            schema: 自定义建表 SQL，为 None 时使用默认 schema
        """
        if schema is None:
            schema = self.DEFAULT_SCHEMA
        self.conn.executescript(schema)
        self.conn.commit()

    def execute(self, sql: str, params: Optional[Union[Tuple, List]] = None) -> int:
        """执行 SQL 语句（INSERT / UPDATE / DELETE）

        Args:
            sql: SQL 语句
            params: 参数元组或列表

        Returns:
            lastrowid（插入行的 ID）
        """
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        self.conn.commit()
        return cur.lastrowid

    def query(self, sql: str, params: Optional[Union[Tuple, List]] = None) -> List[Dict[str, Any]]:
        """查询数据库（SELECT）

        Args:
            sql: SQL 查询语句
            params: 参数元组或列表

        Returns:
            字典列表，每行一个字典
        """
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def format_message(content: str, max_length: int = 500) -> str:
    """消息格式化：去除多余空白、截断超长内容

    Args:
        content: 原始消息内容
        max_length: 最大长度，超出部分截断并添加省略号

    Returns:
        格式化后的消息字符串
    """
    if not content:
        return ""

    # 去除首尾空白
    content = content.strip()

    # 去除多余空行，保留有意义的换行
    lines = [line.strip() for line in content.split("\n") if line.strip()]
    content = "\n".join(lines)

    # 截断超长内容
    if len(content) > max_length:
        content = content[:max_length] + "..."

    return content
