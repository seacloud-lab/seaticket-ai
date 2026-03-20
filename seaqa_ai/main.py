import logging
import signal

import config
from seaqa_ai.app.log import LogConfigurator
from seaqa_ai.app.seaqa_ai_app import SeaQAAIApp

logger = logging.getLogger(__name__)


def main():
    app_logger = LogConfigurator(config.LOG_LEVEL, config.LOG_FILE)

    if config.ENABLE_SYS_LOG:
        app_logger.add_syslog_handler()

    app = SeaQAAIApp(config)

    def _signal_handler(signum, frame):
        logger.info('Received signal %s, shutting down SeaQAAIApp.', signum)
        app.stop()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        app.serve_forever()
    finally:
        app.stop()


if __name__ == '__main__':
    main()
