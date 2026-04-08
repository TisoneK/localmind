"""
LocalMind server entrypoint.
Run directly: python server.py
Or via uvicorn: uvicorn server:app
"""
import logging
import signal
import sys
import uvicorn
from api.app import create_app
from core.config import settings

logging.basicConfig(
    level=getattr(logging, settings.localmind_log_level.upper(), logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    datefmt='%H:%M:%S',
)

app = create_app()

def signal_handler(sig, frame):
    print(f"\n🛑 Received signal {sig}. Shutting down LocalMind gracefully...")
    sys.exit(0)

# Register signal handlers for clean shutdown
signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, signal_handler)  # Termination signal

if __name__ == '__main__':
    try:
        uvicorn.run(
            'server:app',
            host=settings.localmind_host,
            port=settings.localmind_port,
            reload=False,
            log_level=settings.localmind_log_level.lower(),
        )
    except KeyboardInterrupt:
        print("\n🛑 LocalMind stopped by user")
    except Exception as e:
        print(f"\n❌ Server error: {e}")
        sys.exit(1)
