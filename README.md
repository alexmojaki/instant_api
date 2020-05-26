# instant_api

[![Build Status](https://travis-ci.org/alexmojaki/instant_api.svg?branch=master)](https://travis-ci.org/alexmojaki/instant_api) [![Coverage Status](https://coveralls.io/repos/github/alexmojaki/instant_api/badge.svg?branch=master)](https://coveralls.io/github/alexmojaki/instant_api?branch=master) [![Supports Python versions 3.7+](https://img.shields.io/pypi/pyversions/instant_api.svg)](https://pypi.python.org/pypi/instant_api)

Instantly create an HTTP API with automatic type conversions, JSON RPC, and a Swagger UI. All the boring stuff is done for you. Just add methods!

    pip install instant-api

Or to also install the corresponding Python client:

    pip install 'instant-api[client]'

Basic usage looks like the below. Just write some Python functions or methods and decorate them. Parameters and the return value need type annotations so that they can be converted to and from JSON for you. You can use dataclasses for complex values.

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

The API has two flavours. Firstly, the generic endpoint `/api/` implements the standard [JSON-RPC](https://www.jsonrpc.org/) protocol, making it easy to use libraries in existing languages to communicate with minimal boilerplate.

If you need a Python client, I highly recommend the companion library [instant_client](https://github.com/alexmojaki/instant_client). It handles data conversion on the client side and works well with developer tools. Basic usage looks like:

```python
from server import Methods, Point  # the classes we defined above
from instant_client import InstantClient

# The type hint is a lie, but your linter/IDE doesn't know that!
methods: Methods = InstantClient("http://127.0.0.1:5000/api/", Methods()).methods

assert methods.scale(Point(1, 2), factor=3) == Point(3, 6)
```

That looks a lot like it just called `Methods.scale()` directly, which is the point (no pun intended), but under the hood it did in fact send an HTTP request to the server.

You can also make requests directly to paths for each method, sending only the parameters object, which is a bit simpler than the full JSON-RPC protocol. Here's what such a call looks like:  

```python
import requests

response = requests.post(
    'http://127.0.0.1:5000/api/scale',
    json={
        'p': {'x': 1, 'y': 2}, 
        'factor': 3,
    },
)

assert response.json()['result'] == {'x': 3, 'y': 6}
```

The response will be a complete JSON-RPC response as if you had made a full JSON-RPC request. In particular it will either have a `result` or an `error` key.

`instant_api` and `instant_client` use [`datafunctions`](https://github.com/alexmojaki/datafunctions) under the hood (which in turn uses [`marshmallow`](https://marshmallow.readthedocs.io/)) to transparently handle conversion between JSON and Python classes on both ends. All this means you can focus on writing 'normal' Python and worry less about the communication details. The Swagger UI is provided by [Flasgger](https://github.com/flasgger/flasgger), and the protocol is handled by the [json-rpc](https://github.com/pavlov99/json-rpc) library.

Because other libraries do so much of the work, `instant_api` itself is a very small library, essentially contained in [one little file](https://github.com/alexmojaki/instant_api/blob/master/instant_api/instant_api.py). You can probably read the source code pretty easily and adapt it to your needs. 

## Configuration and other details

### Class parameters

The `InstantAPI` class requires a Flask app and has the following optional keyword-only parameters:

- `path` is a string (default `'/api/'`) which is the endpoint that will be added to the app for the JSON RPC. This is where requests will be POSTed. There will also be a path for each method based on the function name, e.g. `/api/scale` and `/api/translate`, but these all behave identically (in particular the body must still specify a `"method"` key) and are only there to make the Swagger UI usable.
- `swagger_kwargs` is a dictionary (default empty) of keyword arguments to pass to the [`flasgger.Swagger`](https://github.com/flasgger/flasgger#externally-loading-swagger-ui-and-jquery-jscss) constructor that is called with the app.

### Errors

The server will always (unless a request is not authenticated, see below) respond with HTTP code 200, even if there is an error in the RPC call. Instead the response body will contain an error code and other details. Complete information can be found in the [protocol spec](https://www.jsonrpc.org/specification#error_object) and the [json-rpc library docs](https://json-rpc.readthedocs.io/en/latest/exceptions.html). Below are the essentials.

If a method is given invalid parameters, the details of the error (either a `TypeError` or a marshmallow `ValidationError`) will be included in the response. The error code will be `-32602`. The response JSON looks like this:

```json
{
  "error": {
    "code": -32602,
    "data": {
      "p": {
        "y": [
          "Not a valid integer."
        ]
      }
    },
    "message": "marshmallow.exceptions.ValidationError: {'p': {'y': ['Not a valid integer.']}}"
  },
  "id": 0,
  "jsonrpc": "2.0"
}
```

If there's an error inside the method, the exception type and message will be in the response, e.g:

```json
{
  "error": {
    "code": -32000,
    "data": {
      "args": [
        "division by zero"
      ],
      "message": "division by zero",
      "type": "ZeroDivisionError"
    },
    "message": "Server error"
  },
  "id": 0,
  "jsonrpc": "2.0"
}
```

If you'd like to control the error response directly, raise a `JSONRPCDispatchException` in your method, e.g:

```python
from jsonrpc.exceptions import JSONRPCDispatchException
from instant_api import InstantAPI

@InstantAPI(app)
class Methods:
    def find_thing(self, thing_id: int) -> Thing:
        ...
        raise JSONRPCDispatchException(
            code=40404,
            message="Thing not found anywhere at all",
            data=["not here", "or here"],
        )
```

The response will then be:

```json
{
  "error": {
    "code": 40404,
    "data": [
      "not here",
      "or here"
    ],
    "message": "Thing not found anywhere at all"
  },
  "id": 0,
  "jsonrpc": "2.0"
}
```

### Attaching methods

Instances of `InstantAPI` can be called with functions, classes, or arbitrary objects to add methods to the API. For functions and classes, the `InstantAPI` can be used as a decorators to call it.

Decorating a single function adds it as an API method, as you'd expect. The function itself should not be a method of a class, since there is no way to provide the first argument `self`.

Calling `InstantAPI` with an object will search through all its attributes and add to the API all functions (including bound methods) whose name doesn't start with an underscore (`_`).

Decorating a class will construct an instance of the class without arguments and then call the resulting object as described above. This means it will add bound methods, so the `self` argument is ignored.

So given `api = InstantAPI(app)`, all of these are equivalent:

```python
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
```

If a function is missing a type annotation for any of its parameters or for the return value, an exception will be raised. If you don't want a method to be added to the API, prefix its name with an underscore, e.g. `def _foo(...)`.

If a function has a docstring, it's first line will be shown in the Swagger UI.

### Intercepting requests

To directly control how requests are handled, create a subclass of `InstantAPI` and override one of these methods:

- `handle_request(self, method)` is the entrypoint which converts a raw flask request to a response. If `method` is None, the request was made to the generic JSON-RPC path. Otherwise `method` is a string with the method name at the end of the request path.
- `call_method(self, func, *args, **kwargs)` calls the API method `func` with the given arguments. The arguments here are not yet deserialized according to the function type annotations.

Unless you're doing something very weird, remember to call the parent method with `super()` somewhere.

### Authentication

To require authentication for requests:

1. Create a subclass of `InstantAPI`.
2. Override the method `def is_authenticated(self):`.
3. Return a boolean: `True` if a user should have access, `False` if they should be denied.
4. Use an instance of your subclass to decorate methods.

Unauthenticated requests will receive a 403 response with a non-JSON body.
