"""
LocalMind server entrypoint.
Run directly: python server.py
Or via uvicorn: uvicorn server:app
"""
import logging
import uvicorn
from api.app import create_app
from core.config import settings

logging.basicConfig(
    level=getattr(logging, settings.localmind_log_level.upper(), logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    datefmt='%H:%M:%S',
)

app = create_app()

if __name__ == '__main__':
    uvicorn.run(
        'server:app',
        host=settings.localmind_host,
        port=settings.localmind_port,
        reload=False,
        log_level=settings.localmind_log_level.lower(),
    )
