import logging.config

from keep.api.core.db import push_logs_to_db


class WorkflowDBHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        # we want to push only workflow logs to the DB
        if hasattr(record, "workflow_execution_id") and record.workflow_execution_id:
            self.records.append(record)

    def push_logs_to_db(self):
        # Convert log records to a list of dictionaries
        log_entries, self.records = [record.__dict__ for record in self.records], []
        # Push log entries to the database
        push_logs_to_db(log_entries)


class WorkflowLoggerAdapter(logging.LoggerAdapter):
    def __init__(self, logger, tenant_id, workflow_id, workflow_execution_id):
        self.tenant_id = tenant_id
        self.workflow_id = workflow_id
        self.workflow_execution_id = workflow_execution_id
        super().__init__(logger, None)

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        extra["tenant_id"] = self.tenant_id
        extra["workflow_id"] = self.workflow_id
        extra["workflow_execution_id"] = self.workflow_execution_id
        kwargs["extra"] = extra
        return msg, kwargs

    def dump(self):
        self.logger.info("Dumping workflow logs")
        # TODO - this is a POC level code.
        # TODO - we should:
        # TODO - 1. find the right handler to push the logs to the DB
        # TODO - 2. find a better way to push the logs async (maybe another service)
        self.logger.parent.handlers[1].push_logs_to_db()
        self.logger.info("Workflow logs dumped")


CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": "%(asctime)s %(message)s %(levelname)s %(name)s %(filename)s %(otelTraceID)s %(otelSpanID)s %(otelServiceName)s",
            "class": "pythonjsonlogger.jsonlogger.JsonFormatter",
        }
    },
    "handlers": {
        "default": {
            "level": "DEBUG",
            "formatter": "json",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
        "context": {
            "level": "DEBUG",
            "formatter": "json",
            "class": "keep.api.logging.WorkflowDBHandler",
        },
    },
    "loggers": {
        "": {"handlers": ["default", "context"], "level": "INFO", "propagate": False}
    },
}


import inspect
import logging


class CustomizedUvicornLogger(logging.Logger):
    """This class overrides the default Uvicorn logger to add trace_id to the log record

    Args:
        logging (_type_): _description_
    """

    def makeRecord(
        self,
        name,
        level,
        fn,
        lno,
        msg,
        args,
        exc_info,
        func=None,
        extra=None,
        sinfo=None,
    ):
        if extra:
            trace_id = extra.pop("otelTraceID", None)
        else:
            trace_id = None
        rv = super().makeRecord(
            name, level, fn, lno, msg, args, exc_info, func, extra, sinfo
        )
        if trace_id:
            rv.__dict__["otelTraceID"] = trace_id
        return rv

    def _log(
        self,
        level,
        msg,
        args,
        exc_info=None,
        extra=None,
        stack_info=False,
        stacklevel=1,
    ):
        # Find trace_id from call stack
        frame = (
            inspect.currentframe().f_back
        )  # Go one level up to get the caller's frame
        while frame:
            if frame.f_code.co_name == "run_asgi":
                trace_id = (
                    frame.f_locals.get("self").scope.get("state", {}).get("trace_id", 0)
                )
                if trace_id:
                    if extra is None:
                        extra = {}
                    extra.update({"otelTraceID": trace_id})
                    break
            frame = frame.f_back

        # Call the original _log function to handle the logging with trace_id
        logging.Logger._log(
            self, level, msg, args, exc_info, extra, stack_info, stacklevel
        )


def setup():
    logging.config.dictConfig(CONFIG)
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_error_logger.__class__ = CustomizedUvicornLogger
