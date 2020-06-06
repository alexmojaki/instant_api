import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jsonrpcclient.client
import pytest
from flask import Flask
from instant_client import InstantClient
from jsonrpc.exceptions import JSONRPCDispatchException
from jsonrpcclient import Response
from jsonrpcclient.exceptions import ReceivedErrorResponseError
from littleutils import file_to_json

from instant_api import InstantAPI, InstantError

app = Flask(__name__)
folder = Path(__file__).parent


@dataclass
class Point:
    x: int
    y: int


api = InstantAPI(app)


@api(swagger_view_attrs=dict(tags=["Point methods"]))
class Methods:
    def translate(self, p: Point, dx: int, dy: int) -> Point:
        """
        Move a point by dx and dy.
        Other stuff here that goes into the description.
        """
        if dy == -8:
            raise ValueError
        if dy == -9:
            raise InstantError(
                code=12345,
                message="This is an instant message",
                data={"foo": 123},
                http_code=401,
            )
        if dy == -10:
            raise JSONRPCDispatchException(
                code=45678,
                message="This is a JSON RPC message",
                data={"foo": 456},
            )
        return Point(p.x + dx, p.y + dy)


app.config['TESTING'] = True
flask_client = app.test_client()


class _TestJsonRpcClient(jsonrpcclient.client.Client):
    def __init__(self, test_client, endpoint):
        super().__init__()
        self.test_client = test_client
        self.endpoint = endpoint

    def send_message(
            self, request: str, response_expected: bool, **kwargs: Any
    ) -> Response:
        response = self.test_client.post(self.endpoint, data=request.encode())
        return Response(response.data.decode("utf8"), raw=response)


rpc_client = _TestJsonRpcClient(flask_client, "/api/")
client_methods = InstantClient(rpc_client, Methods()).methods


def test_simple():
    for methods in [
        client_methods,
        Methods(),
    ]:
        assert methods.translate(Point(1, 2), 3, 4) == Point(4, 6)


def flask_post(url, data):
    response = flask_client.post(url, data=json.dumps(data).encode())
    return response, json.loads(response.data.decode())


def test_method_path():
    response, data = flask_post("/api/translate", {"p": {"x": 1, "y": 2}, "dx": 3, "dy": 4})
    assert {
               "id": None,
               "jsonrpc": "2.0",
               "result": {"x": 4, "y": 6},
           } == data
    assert response.status_code == 200


def test_server_type_error():
    message = "TypeError: missing a required argument: 'dy'"
    with pytest.raises(ReceivedErrorResponseError, match=message):
        rpc_client.translate(1, 3)

    response, data = flask_post("/api/translate", {"p": 1, "dx": 3})
    assert data == {
        "error": {
            "code": -32602,
            "data": None,
            "message": message,
        },
        "id": None,
        "jsonrpc": "2.0",
    }
    assert response.status_code == 400


def test_server_validation_error():
    message = "marshmallow.exceptions.ValidationError: " \
              "{'p': {'_schema': ['Invalid input type.']}}"
    with pytest.raises(
            ReceivedErrorResponseError,
            match=re.escape(message)
    ):
        rpc_client.translate("asd", 3, 4)

    response, data = flask_post("/api/translate", {"p": "asd", "dx": 3, "dy": 4})
    assert data == {
        "error": {
            "code": -32602,
            "data": {"p": {"_schema": ["Invalid input type."]}},
            "message": message,
        },
        "id": None,
        "jsonrpc": "2.0",
    }
    assert response.status_code == 400


def test_instant_error():
    message = "This is an instant message"
    response, data = flask_post("/api/translate", {"p": {"x": 1, "y": 2}, "dx": 3, "dy": -9})
    assert data == {
        "error": {
            "code": 12345,
            "data": {"foo": 123},
            "message": message,
        },
        "id": None,
        "jsonrpc": "2.0",
    }
    assert response.status_code == 401


def test_jsonrpc_dispatch_exception():
    message = "This is a JSON RPC message"
    response, data = flask_post("/api/translate", {"p": {"x": 1, "y": 2}, "dx": 3, "dy": -10})
    assert data == {
        "error": {
            "code": 45678,
            "data": {"foo": 456},
            "message": message,
        },
        "id": None,
        "jsonrpc": "2.0",
    }
    assert response.status_code == 500


def test_unhandled_error():
    message = "Unhandled error in method translate"
    response, data = flask_post("/api/translate", {"p": {"x": 1, "y": 2}, "dx": 3, "dy": -8})
    assert data == {
        "error": {
            "code": -32000,
            "data": None,
            "message": message,
        },
        "id": None,
        "jsonrpc": "2.0",
    }
    assert response.status_code == 500


def test_invalid_json():
    def check(path, expected_status_code):
        response = flask_client.post(path, data="foo")
        assert response.status_code == expected_status_code
        assert json.loads(response.data.decode()) == {
            "error": {
                "code": -32700,
                "message": "Parse error",
            },
            "id": None,
            "jsonrpc": "2.0",
        }

    check("/api/", 200)
    check("/api/translate", 400)


def test_method_not_found():
    message = "Method not found"
    with pytest.raises(ReceivedErrorResponseError, match=message):
        rpc_client.do_thing(1, 3)


def test_auth_error():
    with patch.object(InstantAPI, "is_authenticated", lambda self: False):
        response = flask_client.post("/api/")
        assert response.status_code == 403
        assert response.data == b"Forbidden"


def test_notification():
    rpc_client.notify("translate", 234)


def test_apispec():
    response = flask_client.get("/apispec_1.json")
    print(response.data)
    assert response.json == file_to_json(folder / "apispec.json")
