import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jsonrpcclient.client
import pytest
from flask import Flask
from instant_api import InstantAPI
from instant_client import InstantClient
from jsonrpcclient import Response
from jsonrpcclient.exceptions import ReceivedErrorResponseError
from littleutils import file_to_json

app = Flask(__name__)
folder = Path(__file__).parent


@dataclass
class Point:
    x: int
    y: int


@InstantAPI(app)
class Methods:
    def translate(self, p: Point, dx: int, dy: int) -> Point:
        """
        Move a point by dx and dy.
        Other stuff here doesn't go into swagger.
        """
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
        InstantClient(_TestJsonRpcClient(flask_client, "/api/translate"), Methods()).methods,
        Methods(),
    ]:
        assert methods.translate(Point(1, 2), 3, 4) == Point(4, 6)


def test_server_type_error():
    with pytest.raises(ReceivedErrorResponseError, match="TypeError: missing a required argument: 'dy'"):
        rpc_client.translate(1, 3)


def test_server_validation_error():
    with pytest.raises(
            ReceivedErrorResponseError,
            match=re.escape("marshmallow.exceptions.ValidationError: "
                            "{'p': {'_schema': ['Invalid input type.']}}")
    ):
        rpc_client.translate("asd", 3, 4)


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
