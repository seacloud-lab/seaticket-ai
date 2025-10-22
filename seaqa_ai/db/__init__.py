# -*- coding: utf-8 -*-
import logging

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

Base = declarative_base()


def create_engine_from_uri(db_uri):
    kwargs = dict(pool_recycle=300, pool_pre_ping=True, echo=False, echo_pool=False)
    engine = create_engine(db_uri, **kwargs)

    return engine


def init_db_session_class(db_uri):
    """Configure session class for mysql according to the config file."""
    try:
        engine = create_engine_from_uri(db_uri)
    except Exception as e:
        logger.error("Init db session class error: %s" % e)
        raise RuntimeError("Init db session class error: %s" % e)

    session = sessionmaker(bind=engine)
    return session
