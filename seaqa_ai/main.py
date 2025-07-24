import config
from seaqa_ai.app.log import LogConfigurator
from seaqa_ai.app.seaqa_ai_app import SeaQAAIApp


def main():
    app_logger = LogConfigurator(config.LOG_LEVEL, config.LOG_FILE)

    if config.ENABLE_SYS_LOG:
        app_logger.add_syslog_handler()

    app = SeaQAAIApp(config)
    app.serve_forever()


if __name__ == '__main__':
    main()
