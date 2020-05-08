import functools
import inspect
import traceback
from dataclasses import dataclass
from typing import Dict, Any

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

        # To make the Swagger UI more user friendly, specify the values of constants
        # for the method paths whose schemas are partially defined using marshmallow
        for method in self._instant_methods:
            result["definitions"][f"{method}_body"]["properties"]["method"]["enum"] = [method]
            result["definitions"][f"{method}_body"]["properties"]["jsonrpc"]["enum"] = ["2.0"]
            result["definitions"][f"{method}_success"]["properties"]["jsonrpc"]["enum"] = ["2.0"]

        return result


class InstantAPI:
    """
    Instantly create an HTTP API with automatic type conversions, JSON RPC, and a Swagger UI. Just add methods!

    Basic usage looks like this::

        from dataclasses import dataclass
        from flask import Flask
        from instant_api import InstantAPI

        app = Flask(__name__)

        @dataclass
        class Point:
            x: int
            y: int

        @InstantAPI(app)
        class Methods:
            def translate(self, p: Point, dx: int, dy: int) -> Point:
                return Point(p.x + dx, p.y + dy)

            def scale(self, p: Point, factor: int) -> Point:
                return Point(p.x * factor, p.y * factor)

        if __name__ == '__main__':
            app.run()

    See the README at https://github.com/alexmojaki/instant_api for more details.

    Instances are callable so that they can be used as a decorator to add methods.
    See the docstring for __call__.

    You can subclass this class and override the following methods to customise behaviour:
        - is_authenticated
        - handle_request
        - call_method
    """

    def __init__(
            self,
            app: Flask,
            *,
            path: str = "/api/",
            swagger_kwargs: Dict[str, Any] = None,
    ):
        """
        - `app` is a Flask app (https://flask.palletsprojects.com/en/1.1.x/)
            to which URL rules are added for the RPC.
        - `path` is the endpoint
            that will be added to the app for the JSON RPC.
            This is where requests will be POSTed.
            There will also be a path for each method based on the function name,
            e.g. `/api/scale` and `/api/translate`, but these all behave identically
            (in particular the body must still specify a `"method"` key)
            and are only there to make the Swagger UI usable.
        - `swagger_kwargs` is a dictionary of keyword arguments
            to pass to the `flasgger.Swagger`
            (https://github.com/flasgger/flasgger#externally-loading-swagger-ui-and-jquery-jscss)
            constructor that is called with the app.
        """
        self.app = app
        self.path = path.rstrip("/") + "/"
        self.dispatcher = Dispatcher()
        self.swagger = MySwagger(app, **(swagger_kwargs or {}))
        self._add_view(
            GLOBAL_PARAMS_SCHEMA,
            GLOBAL_SUCCESS_SCHEMA,
            self.path,
            type(self).__name__,
            "Generic JSON RPC endpoint",
        )

    def is_authenticated(self):
        """
        Override and return False for certain requests to deny any access to the API.
        """
        return True

    def handle_request(self):
        """
        Entrypoint which converts a raw flask request to a response.
        """
        if not self.is_authenticated():
            return "Forbidden", 403

        # Forward the request to the correct method
        # Ultimately this calls call_method
        result = JSONRPCResponseManager.handle(request.get_data(), self.dispatcher)

        if result is None:
            # Request was a notification, i.e. client doesn't need response
            return ""
        else:
            return result.data

    def call_method(self, func, *args, **kwargs):
        """
        Calls the API method `func` with the given arguments.
        The arguments here are not yet deserialized according to the function type annotations.
        """
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
        """
        Accepts any object, with special treatment for functions and classes,
        so this can be used as a decorator.

        Decorating a single function adds it as an API method.
        The function itself should not be a method of a class,
        since there is no way to provide the first argument `self`.

        Decorating a class will construct an instance of the class without arguments
        and then call the resulting object as described below.
        This means it will add bound methods, so the `self` argument is ignored.

        Passing an object will search through all its attributes
        and add to the API all functions (including bound methods)
        whose name doesn't start with an underscore (`_`).

        So given `api = InstantAPI(app)`, all of these are equivalent:

            @api
            def foo(bar: Bar) -> Spam:
                ...

            api(foo)

            @api
            class Methods:
                def foo(self, bar: Bar) -> Spam:
                    ...

            api(Methods)

            api(Methods())

        If a function is missing a type annotation for any of its parameters or for the return value,
        an exception will be raised.
        If you don't want a method to be added to the API,
        prefix its name with an underscore, e.g. `def _foo(...)`.

        If a function has a docstring, it's first line will be shown in the Swagger UI.
        """
        if isinstance(func_class_or_obj, type):
            cls = func_class_or_obj
            self(cls())
            return cls

        self._decorate_function(func_class_or_obj)
        methods = func_class_or_obj
        for name, func in inspect.getmembers(methods):
            if not name.startswith("_"):
                self._decorate_function(func)

        return func_class_or_obj

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

    def _add_view(self, body_schema, success_schema, path: str, view_name: str, doc: str):
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


def format_exception(e: BaseException):
    return "".join(traceback.format_exception_only(type(e), e)).rstrip()
