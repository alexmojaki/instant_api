import logging
import functools
import inspect
import json
import traceback
from dataclasses import dataclass
from textwrap import dedent
from typing import Dict, Any, Optional

from datafunctions import datafunction, ArgumentError
from flasgger import SwaggerView, Swagger
from flask import request, Flask
from jsonrpc import Dispatcher, JSONRPCResponseManager
from jsonrpc.exceptions import JSONRPCDispatchException
from marshmallow import ValidationError
from marshmallow_dataclass import class_schema

log = logging.getLogger("instant_api")


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


@dataclass
class InstantError(Exception):
    """
    Raise an instance of InstantError in your method to return an error response
    containing the given code, message, and data in the body.
    The http_code field will be the HTTP status code, *only if
    the method is called through the method path instead of JSON-RPC*.
    """
    code: int
    message: str
    data: Any = None
    http_code: int = 500


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
            e.g. `/api/scale` and `/api/translate`, see
            https://github.com/alexmojaki/instant_api#using-method-paths-instead-of-json-rpc
        - `swagger_kwargs` is a dictionary of keyword arguments
            to pass to the `flasgger.Swagger` constructor that is called with the app.
            For example, you can customise the Swagger UI by passing a dictionary to `config`
            (https://github.com/flasgger/flasgger#customize-default-configurations)::

                api = InstantAPI(app, swagger_kwargs={
                    "config": {"specs_route": "/my_apidocs/", ...}
                })
        """
        self.app = app
        self.path = path.rstrip("/") + "/"
        self.dispatcher = Dispatcher()
        self.swagger = Swagger(app, **(swagger_kwargs or {}))
        self._add_view(
            {"tags": ["JSON-RPC"]},
            GLOBAL_PARAMS_SCHEMA,
            GLOBAL_SUCCESS_SCHEMA,
            self.path,
            type(self).__name__,
            "Generic JSON RPC endpoint",
            method=None,
        )

    def is_authenticated(self):
        """
        Override and return False for certain requests to deny any access to the API.
        """
        return True

    def handle_request(self, method: Optional[str]):
        """
        Entrypoint which converts a raw flask request to a response.

        If `method` is None, the request was made to the generic JSON-RPC path.
        Otherwise `method` is a string with the method name at the end of the request path.
        """
        if not self.is_authenticated():
            return "Forbidden", 403

        # Forward the request to the correct method
        # Ultimately this calls call_method
        request_data = request.get_data(as_text=True)
        if method is not None:
            request_data = (
                '{'
                '   "id": null,'
                '   "jsonrpc": "2.0",'
                f'  "method": {json.dumps(method)},'
                f'  "params": {request_data}'
                '}'
            )
        result = JSONRPCResponseManager.handle(request_data, self.dispatcher)

        if result is None:
            # Request was a notification, i.e. client doesn't need response
            return "", 200
        else:
            http_code = 200
            if result.error:
                data = result.error.get("data")
                # See the InstantError handler at the end of call_method
                if isinstance(data, dict) and "__instant_http_code" in data:
                    http_code = data["__instant_http_code"]
                    result.error["data"] = data["data"]
                else:
                    if result.error.get("code") in [
                        -32700,  # JSON parse error
                        -32600,  # Invalid JSON structure
                        -32602,  # Invalid params
                    ]:
                        http_code = 400  # Bad request
                    else:
                        http_code = 500  # Internal server error

            # JSON RPC must always return 200
            if method is None:
                http_code = 200

            return result.data, http_code

    def call_method(self, func, *args, **kwargs):
        """
        Calls the API method `func` with the given arguments.
        The arguments here are not yet deserialized according to the function type annotations.
        """
        try:
            try:
                result = func(*args, **kwargs)
                log.info(f"Successfully called method {func.__name__}")
                return result
            except InstantError:
                raise
            except ArgumentError as e:
                e = e.__cause__
                if isinstance(e, ValidationError):
                    data = e.messages
                else:
                    data = None

                raise InstantError(
                    code=-32602,  # Invalid params
                    message=format_exception(e),
                    data=data,
                    http_code=400,
                )
            except JSONRPCDispatchException as e:
                raise InstantError(
                    code=e.error.code,
                    message=e.error.message,
                    data=e.error.data,
                )
            except Exception:
                message = f"Unhandled error in method {func.__name__}"
                log.exception(message)
                raise InstantError(
                    code=-32000,
                    message=message,
                )
        except InstantError as e:
            raise JSONRPCDispatchException(
                code=e.code,
                message=e.message,
                # Mash the http_code in here to be extracted later in handle_request
                # There's no easy way to get this info through the json-rpc
                # library up to the final response
                data=dict(
                    __instant_http_code=e.http_code,
                    data=e.data,
                ),
            )

    def __call__(self, func_class_or_obj: Any = None, *, swagger_view_attrs: dict = None):
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

        For each function, a `flasgger.SwaggerView` will be created.
        (see https://github.com/flasgger/flasgger#using-marshmallow-schemas)
        You can customise the view by passing a dictionary class attributes
        in the argument `swagger_view_attrs`
        For example::

            @api(swagger_view_attrs={"tags": ["Stuff"]})
            def foo(...)

        This will put `foo` in the `Stuff` section of the Swagger UI.

        If a function has a docstring, its first line will be the "summary"
        in the OpenAPI spec of the method path, visible in the overview in the Swagger UI.
        The remaining lines will become the "description",
        visible when the path is expanded in the UI.
        """

        if func_class_or_obj is None:
            # Decorator with arguments
            return functools.partial(self, swagger_view_attrs=swagger_view_attrs)

        if isinstance(func_class_or_obj, type):
            cls = func_class_or_obj
            self(cls(), swagger_view_attrs=swagger_view_attrs)
            return cls

        # noinspection PyTypeChecker
        self._decorate_function(func_class_or_obj, swagger_view_attrs)
        methods = func_class_or_obj
        for name, func in inspect.getmembers(methods):
            if not name.startswith("_"):
                self._decorate_function(func, swagger_view_attrs)

        return func_class_or_obj

    def _decorate_function(self, func, swagger_view_attrs):
        try:
            inspect.signature(func)
        except Exception:
            return

        name = func.__name__
        func: datafunction = datafunction(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return self.call_method(func, *args, **kwargs)

        self.dispatcher.add_method(wrapper)

        @dataclass
        class _Success:
            id: int
            jsonrpc: str

        class Success(class_schema(_Success)):
            result = func.return_schemas.schema_class._declared_fields["_return"]

        Success.__name__ = f"{name}_success"

        self._add_view(
            swagger_view_attrs or {},
            func.params_schemas.schema_class,
            Success,
            self.path + name,
            type(self).__name__ + "_" + name,
            func.__doc__,
            method=name,
        )

    def _add_view(
            self,
            swagger_view_attrs: dict,
            body_schema,
            success_schema,
            path: str,
            view_name: str,
            doc: str,
            method: Optional[str],
    ):
        instant_api_self = self

        class MethodView(SwaggerView):
            summary, _, description = dedent(doc or "").strip().partition("\n")
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
            tags = ["Methods"]

            locals().update(swagger_view_attrs)

            def post(self):
                return instant_api_self.handle_request(method)

        self.app.add_url_rule(
            path,
            view_func=MethodView.as_view(view_name),
            methods=["POST"],
        )


def format_exception(e: BaseException):
    return "".join(traceback.format_exception_only(type(e), e)).rstrip()
