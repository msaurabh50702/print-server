"""Start the print server with the waitress production WSGI server."""
import os

from waitress import serve

from printserver import create_app

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Pi Print Server listening on http://{host}:{port}")
    serve(create_app(), host=host, port=port, threads=4)
