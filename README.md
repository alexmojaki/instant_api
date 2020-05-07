# instant_api

[![Build Status](https://travis-ci.org/alexmojaki/instant_api.svg?branch=master)](https://travis-ci.org/alexmojaki/instant_api) [![Coverage Status](https://coveralls.io/repos/github/alexmojaki/instant_api/badge.svg?branch=master)](https://coveralls.io/github/alexmojaki/instant_api?branch=master) [![Supports Python versions 3.7+](https://img.shields.io/pypi/pyversions/instant_api.svg)](https://pypi.python.org/pypi/instant_api)

Instantly create an HTTP API with automatic type conversions, JSON RPC, and a Swagger UI. Just add methods!

    pip install instant-api

Usage looks like this:

```python
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
        """Move a point by dx and dy."""
        return Point(p.x + dx, p.y + dy)

    def scale(self, p: Point, factor: int) -> Point:
        """Scale a point away from the origin by factor."""
        return Point(p.x * factor, p.y * factor)

if __name__ == '__main__':
    app.run()
```

Visit http://127.0.0.1:5000/apidocs/ for a complete Swagger GUI to try out the API interactively:

![Swagger overview](images/swagger_overview.png)

The API implements the standard [JSON-RPC](https://www.jsonrpc.org/) protocol, making it easy to use libraries in existing languages to communicate with minimal boilerplate.

If you need a Python client, I highly recommend the companion library [instant_client](https://github.com/alexmojaki/instant_client). Basic usage looks like:

```python
from server import Methods, Point  # the classes we defined above
from instant_client import InstantClient

# The type hint is a lie, but your linter/IDE doesn't know that!
methods: Methods = InstantClient("http://127.0.0.1:5000/api/", Methods()).methods

assert methods.scale(Point(1, 2), factor=3) == Point(3, 6)
```

That looks a lot like it just called `Methods.scale()` directly, which is the point (no pun intended), but under the hood it did in fact send an HTTP request to the server.

If a library doesn't suit your needs, or if you're wondering what the protocol looks like, it's very simple. Here's a the same call done 'manually':

```python
import requests

response = requests.post(
    'http://127.0.0.1:5000/api/',
    json={
        'id': 0, 
        'jsonrpc': '2.0', 
        'method': 'scale', 
        'params': {
            'p': {'x': 1, 'y': 2}, 
            'factor': 3,
        },
    },
)

assert response.json()['result'] == {'x': 3, 'y': 6}
```

`instant_api` and `instant_client` use [`datafunctions`](https://github.com/alexmojaki/datafunctions) under the hood (which in turn uses [`marshmallow`](https://marshmallow.readthedocs.io/)) to transparently handle conversion between JSON and Python classes on both ends. All this means you can focus on writing 'normal' Python and worry less about the communication details.
