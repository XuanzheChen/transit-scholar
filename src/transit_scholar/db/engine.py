"""数据库连接与 Session 工厂。

这个模块可以理解为整个项目访问数据库的“入口”。

这里有三层概念需要分开：

1. SQLite 数据库文件
   真正的数据保存在磁盘文件里，默认路径是
   ``data/database/transit_scholar.db``。SQLite 是单文件数据库，所以
   “创建数据库”很多时候就是连接到这个文件；如果文件不存在，SQLite
   会在合适的时候创建它。

2. SQLAlchemy Engine
   ``Engine`` 不是表，也不是某一行数据。它更像“数据库连接工厂”：
   它知道数据库在哪里，也知道如何打开底层连接。通常一个进程里创建
   一个默认 engine，然后反复使用。

3. SQLAlchemy Session
   ``Session`` 是一次短暂的数据库工作单元。创建、查询、修改、提交、
   回滚 ``Paper``、``PaperFile`` 这类 ORM 对象，都是通过 session
   完成的。日常可以把“打开一个 session”理解成“开始一次小的数据库对话”。

默认 engine 从 ``config.settings`` 读取数据库路径。测试和 smoke 脚本可以
在导入本模块前设置 ``TRANSIT_SCHOLAR_DATA_DIR``，让同一套代码连接到隔离
临时数据库，而不是正式 ``data/`` 目录。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from transit_scholar.config import settings


def engine_for(database_url: str | None = None):
    """创建一个绑定到 ``database_url`` 的 engine。

    ``database_url`` 是 SQLAlchemy 使用的数据库连接字符串。本项目通常是：

    ``sqlite:///data/database/transit_scholar.db``

    返回的 engine 不代表某篇论文，也不代表某张表；它代表“如何连接到这个
    数据库”，后续再用它创建 session。
    """
    url = database_url or settings.database_url

    # SQLite 默认有线程保护：在哪个 Python 线程里创建的连接，就只能在哪个
    # 线程里使用。这个默认值对很多应用是合理的，但对测试夹具、小型本地工具
    # 不太方便，因为 SQLAlchemy 或测试代码可能会把同一个连接交给不同辅助逻辑。
    #
    # ``check_same_thread=False`` 只是关闭 SQLite 客户端的“同线程检查”。
    # 它并不意味着可以随便并发写数据库；写入一致性仍然依赖 SQLite 事务和
    # SQLAlchemy 的 session/connection 管理。对当前这个单用户本地项目来说，
    # 这样可以让测试和 smoke 更简单，是可以接受的。
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}

    # ``future=True`` 使用 SQLAlchemy 2.x 风格行为，让 engine、session 和 ORM
    # 调用保持在较新的 API 口径上。
    return create_engine(url, future=True, connect_args=connect_args)


# 默认 engine，绑定到当前配置里的数据库。大多数业务代码应该复用它，
# 不需要每次查询都重新创建 engine。
engine = engine_for()

# ``SessionLocal`` 是“session 工厂”，不是 session 本身。调用
# ``SessionLocal()`` 才会创建一个绑定到 ``engine`` 的短生命周期 session。
#
# autoflush=False：
#   SQLAlchemy 不会在每次查询前自动把 Python 对象的待保存变化同步到数据库。
#   如果代码需要在 commit 前拿到自动生成的 id，可以显式调用 ``session.flush()``。
#
# autocommit=False：
#   只有代码显式调用 ``session.commit()`` 时才提交。这样一组相关的行变化
#   可以保持原子性：要么一起成功提交，要么一起回滚。
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """提供一个带事务管理的 session 上下文。

    使用方式：

    ``with get_session() as session:``

    正常退出时提交修改；如果中途出现异常，就回滚事务，避免留下半写入数据。
    无论成功还是失败，最后都会关闭 session，释放数据库连接。
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
