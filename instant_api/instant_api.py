import functools
import inspect
import traceback
from dataclasses import dataclass

from datafunctions import datafunction, ArgumentError
from flasgger import SwaggerView, Swagger
from flask import request, Flask
from jsonrpc import Dispatcher, JSONRPCResponseManager
from jsonrpc.exceptions import JSONRPCDispatchException
from marshmallow import ValidationError
from marshmallow_dataclass import class_schema


def _make_schema(**extra_props):
    return {
        "properties": {
            "jsonrpc": {"type": "string", "enum": ["2.0"]},
            "id": {"type": "integer"},
            **extra_props,
        },
    }


GLOBAL_SUCCESS_SCHEMA = _make_schema(
    result={"type": "object"},
)

GLOBAL_PARAMS_SCHEMA = _make_schema(
    params={"type": "object"},
    method={"type": "string"},
)

ERROR_SCHEMA = _make_schema(
    error={
        "properties": {
            "message": {"type": "string"},
            "code": {"type": "integer"},
            "data": {"type": "object"},
        },
    },
)


class MySwagger(Swagger):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._instant_methods = []

    def get_apispecs(self, *args, **kwargs):
        result = super().get_apispecs(*args, **kwargs)
        for method in self._instant_methods:
            result["definitions"][f"{method}_body"]["properties"]["method"]["enum"] = [method]
            result["definitions"][f"{method}_body"]["properties"]["jsonrpc"]["enum"] = ["2.0"]
            result["definitions"][f"{method}_success"]["properties"]["jsonrpc"]["enum"] = ["2.0"]
        return result


class InstantAPI:
    def __init__(
            self,
            app: Flask,
            *,
            path: str = "/api/",
            swagger_kwargs: dict = None,
    ):
        self.app = app
        self.path = path.rstrip("/") + "/"
        self.dispatcher = Dispatcher()
        self.swagger = MySwagger(app, **(swagger_kwargs or {}))
        self._add_view(
            GLOBAL_PARAMS_SCHEMA,
            GLOBAL_SUCCESS_SCHEMA,
            self.path,
            type(self).__name__,
        )

    def is_authenticated(self):
        return True

    def handle_request(self):
        if not self.is_authenticated():
            return "Forbidden", 403
        result = JSONRPCResponseManager.handle(request.get_data(), self.dispatcher)
        if result is None:
            # Request was a notification, i.e. client doesn't need response
            return ""
        else:
            return result.data

    def call_method(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ArgumentError as e:
            e = e.__cause__
            if isinstance(e, ValidationError):
                data = e.messages
            else:
                data = None

            raise JSONRPCDispatchException(
                code=-32602,  # Invalid params
                message=format_exception(e),
                data=data,
            )

    def __call__(self, func_class_or_obj):
        if isinstance(func_class_or_obj, type):
            cls = func_class_or_obj
            self(cls())
            return cls

        self._decorate_function(func_class_or_obj)
        methods = func_class_or_obj
        for name, func in inspect.getmembers(methods):
            if not name.startswith("_"):
                self._decorate_function(func)

    def _decorate_function(self, func):
        try:
            inspect.signature(func)
        except Exception:
            return

        name = func.__name__
        self.swagger._instant_methods.append(name)
        func = datafunction()(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return self.call_method(func, *args, **kwargs)

        self.dispatcher.add_method(wrapper)

        class _Body:
            id: int
            method: str
            params: func.params_schemas.dataclass
            jsonrpc: str

        Body = class_schema(_Body)
        Body.__name__ = f"{name}_body"

        @dataclass
        class _Success:
            id: int
            jsonrpc: str

        class Success(class_schema(_Success)):
            result = func.return_schemas.schema_class._declared_fields["_return"]

        Success.__name__ = f"{name}_success"

        self._add_view(
            Body,
            Success,
            self.path + name,
            type(self).__name__ + "_" + name,
            ((func.__doc__ or "").strip().splitlines() or [""])[0],
        )

    def _add_view(self, body_schema, success_schema, path, view_name, doc=""):
        instant_api_self = self

        class MethodView(SwaggerView):
            parameters = [
                {
                    "name": "body",
                    "in": "body",
                    "schema": body_schema,
                    "required": True,
                }
            ]
            responses = {
                "Success": {"schema": success_schema},
                "Error": {"schema": ERROR_SCHEMA},
            }

            def post(self):
                return instant_api_self.handle_request()

        MethodView.post.__doc__ = doc

        self.app.add_url_rule(
            path,
            view_func=MethodView.as_view(view_name),
            methods=["POST"],
        )


def format_exception(e):
    return "".join(traceback.format_exception_only(type(e), e)).rstrip()
